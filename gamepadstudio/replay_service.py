"""
GamePad Studio · 4K 极清硬件加速即时回放录制引擎 (100% 纯内存零损耗环形缓冲架构)
1. 支持 AMD AMF / NVIDIA NVENC / Intel QSV / MediaFoundation 原生 GPU 硬件编码
2. 纯内存环形缓冲区 (0 磁盘写入，彻底杜绝任何 SSD 闪存寿命损耗与临时碎文件)
3. 毫秒级无损合并落盘 (Lossless Stream Copy Muxing，瞬间生成标准 MP4)
4. 多屏幕/游戏屏幕智能识别锁定 (自动规避软件自身屏幕，支持 7680x2160 双屏全景跨屏录制)
5. 游戏标题穿透识别、与截图服务同源的干净命名与图库归档
"""

import collections
import ctypes
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable, Deque, Dict, List, Optional, Tuple
import mss
from .replay_capture import create_replay_capture
from .display_info import match_monitors, enumerate_displays
from .replay_timing import TimestampedRGBWriter, TimestampedAVWriter, TransportStreamClock, mp4_duration_seconds

from .screenshot_service import (
    foreground_info,
    smart_foreground_info,
    sanitize_filename,
    _get_active_monitor_bbox,
    get_target_monitor_bbox,
    _ensure_dpi_awareness
)


def get_ffmpeg_path() -> Optional[str]:
    """获取可用 FFmpeg 可执行文件路径"""
    # 1. 优先检查项目自身 bin 目录
    proj_bin = Path(__file__).resolve().parents[1] / 'bin' / ('ffmpeg.exe' if sys.platform == 'win32' else 'ffmpeg')
    if proj_bin.is_file():
        return str(proj_bin)
    
    # 2. 检查系统 PATH
    which_ffmpeg = shutil.which('ffmpeg')
    if which_ffmpeg:
        return which_ffmpeg
        
    # 3. 检查本机常见安装路径
    candidates = [
        Path(r"D:\voxcpm\tools\ffmpeg.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "ffmpeg" / "bin" / "ffmpeg.exe",
    ] if sys.platform == 'win32' else [Path('/opt/homebrew/bin/ffmpeg'), Path('/usr/local/bin/ffmpeg')]
    for c in candidates:
        if c.is_file():
            return str(c)
    if sys.platform == 'darwin':
        try:
            # The project-local wheel supplies a standalone FFmpeg when the
            # user has no Homebrew installation; packaged builds prefer bin/.
            import imageio_ffmpeg
            bundled = Path(imageio_ffmpeg.get_ffmpeg_exe())
            if bundled.is_file() and os.access(bundled, os.X_OK):
                return str(bundled)
        except Exception:
            return None
    return None


def _subprocess_hidden_flags() -> dict:
    """Hide helper windows and suppress Windows' process-start busy cursor."""
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        si = subprocess.STARTUPINFO()
        si.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
        si.dwFlags |= getattr(subprocess, "STARTF_FORCEOFFFEEDBACK", 0x00000080)
        si.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kwargs["startupinfo"] = si
    return kwargs


_GLOBAL_JOB_OBJECTS = []


def _assign_process_to_job(proc):
    """通过 Windows 内核 Job Object (KILL_ON_JOB_CLOSE) 确保 Python 退出时 ffmpeg.exe 必然瞬间强杀，绝对杜绝 GPU 资源残留"""
    if sys.platform != 'win32' or not proc:
        return
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        job = kernel32.CreateJobObjectW(None, None)
        if job:
            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ('PerProcessUserTimeLimit', wintypes.LARGE_INTEGER),
                    ('PerJobUserTimeLimit', wintypes.LARGE_INTEGER),
                    ('LimitFlags', wintypes.DWORD),
                    ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t),
                    ('ActiveProcessLimit', wintypes.DWORD),
                    ('Affinity', ctypes.c_size_t),
                    ('PriorityClass', wintypes.DWORD),
                    ('SchedulingClass', wintypes.DWORD),
                ]
            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [('ReadOperationCount', ctypes.c_ulonglong), ('WriteOperationCount', ctypes.c_ulonglong),
                            ('OtherOperationCount', ctypes.c_ulonglong), ('ReadTransferCount', ctypes.c_ulonglong),
                            ('WriteTransferCount', ctypes.c_ulonglong), ('OtherTransferCount', ctypes.c_ulonglong)]
            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ('IoInfo', IO_COUNTERS),
                    ('ProcessMemoryLimit', ctypes.c_size_t),
                    ('JobMemoryLimit', ctypes.c_size_t),
                    ('PeakProcessMemoryLimit', ctypes.c_size_t),
                    ('PeakJobMemoryLimit', ctypes.c_size_t),
                ]
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
            if hasattr(proc, '_handle'):
                kernel32.AssignProcessToJobObject(job, proc._handle)
            _GLOBAL_JOB_OBJECTS.append(job)
    except Exception:
        pass


_CACHED_ENCODERS: Dict[str, str] = {}


def _encoder_runtime_options(encoder):
    if encoder in ('hevc_videotoolbox', 'h264_videotoolbox'):
        # A successful probe must prove that hardware works; VideoToolbox's
        # own software implementation must not masquerade as acceleration.
        return ['-allow_sw', '0', '-realtime', '1']
    return []


def detect_hardware_encoder(codec: str = "hevc") -> str:
    """
    智能探针：根据本机 GPU 型号与驱动，探测可用的高吞吐硬件加速编码器
    macOS 优先验证 VideoToolbox；其他平台保留现有 GPU 优先级与软件回退。
    """
    global _CACHED_ENCODERS
    if codec in _CACHED_ENCODERS:
        return _CACHED_ENCODERS[codec]

    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        fallback = "libx265" if codec == "hevc" else ("libaom-av1" if codec == "av1" else "libx264")
        _CACHED_ENCODERS[codec] = fallback
        return fallback

    candidates = []
    if codec == "hevc":
        candidates = ["hevc_amf", "hevc_nvenc", "hevc_qsv", "hevc_mf", "libx265"]
    elif codec == "av1":
        candidates = ["av1_amf", "av1_nvenc", "av1_qsv", "libaom-av1"]
    elif codec == "h264":
        candidates = ["h264_amf", "h264_nvenc", "h264_qsv", "h264_mf", "libx264"]
    else:
        candidates = ["hevc_amf", "hevc_nvenc", "libx265"]

    if sys.platform == 'darwin':
        candidates = {'hevc': ['hevc_videotoolbox', 'libx265'],
                      'h264': ['h264_videotoolbox', 'libx264'],
                      'av1': ['libaom-av1']}.get(codec, ['hevc_videotoolbox', 'libx265'])

    for enc in candidates:
        try:
            options = _encoder_runtime_options(enc)
            cmd = [
                ffmpeg,
                "-y",
                "-f", "lavfi",
                "-i", "testsrc=size=256x256:rate=30",
                "-t", "0.1",
                "-c:v", enc,
                *options,
                *(['-pix_fmt', 'yuv420p'] if options else []),
                "-f", "null", "NUL" if sys.platform == "win32" else "/dev/null"
            ]
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2, **_subprocess_hidden_flags())
            if res.returncode == 0:
                _CACHED_ENCODERS[codec] = enc
                return enc
        except Exception:
            continue

    fallback = "libx265" if codec == "hevc" else ("libaom-av1" if codec == "av1" else "libx264")
    _CACHED_ENCODERS[codec] = fallback
    return fallback


def calculate_estimated_ram_gb(minutes: int, bitrate_mbps: int = 50) -> float:
    """计算指定时长和码率下的回放缓冲区内存开销（GB）"""
    return round((minutes * 60 * bitrate_mbps * 1e6) / (8 * 1024 * 1024 * 1024), 2)


def _align_ts_stream(raw_bytes: bytes) -> bytes:
    """确保 TS 码流从合法的 188 字节 TS 数据包同步头 (0x47) 开始对齐"""
    length = len(raw_bytes)
    for i in range(min(length, 1880)):
        if raw_bytes[i] == 0x47:
            if i + 188 < length and raw_bytes[i + 188] == 0x47:
                return raw_bytes[i:]
    return raw_bytes


class ReplayBufferEngine:
    """
    4K 极清硬件加速环形回放录制引擎 (100% 纯内存缓冲区架构)
    - 屏幕帧流式喂入 GPU 硬件编码器 (AMF / NVENC / QSV)
    - 编码后的 MPEG-TS 码流通过标准输出实时截流并存入 Python RAM 环形队列
    - 运行期间磁盘写入量严格为 0，零磨损，退出或异常时无需清理任何临时碎片
    - 智能游戏屏侦测或双屏全景 (7680x2160) 跨屏录制
    - 调用 save_replay 时瞬间从内存流式直写 MP4 文件
    """
    def __init__(
        self,
        save_dir: Path,
        minutes: int = 5,
        codec: str = "hevc",
        bitrate_mbps: int = 50,
        fps: int = 30,
        capture_mode: str = "game",
        on_event: Optional[Callable[[str], None]] = None
    ):
        self.save_dir = Path(save_dir)
        self.minutes = max(1, min(10, int(minutes)))
        self.codec = codec if codec in ("hevc", "av1", "h264") else "hevc"
        self.bitrate_mbps = max(10, min(120, int(bitrate_mbps)))
        self.fps = max(15, min(60, int(fps)))
        self.capture_mode = capture_mode or "game"
        self.on_event = on_event

        self.is_pure_ram = True
        import tempfile
        self.cache_dir = Path(tempfile.gettempdir()) / "GamePadStudio_ReplayCache"

        self.running = False
        self._lock = threading.Lock()
        self._ram_lock = threading.Lock()
        self._ram_chunks: Deque[Tuple[float, bytes]] = collections.deque()
        self._ram_chunk_spans: Deque[Tuple[float, float]] = collections.deque()
        self._total_bytes: int = 0

        self._worker_thread: Optional[threading.Thread] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._capture_source = None
        self._last_save_time = 0.0
        self._current_width = 0
        self._current_height = 0
        self._last_error = ''
        self._encoder_errors = collections.deque(maxlen=8)
        self.capture_method = ''
        self.color_mode = ''
        self.audio_enabled = False
        self.audio_format = None
        self._captured_frames = 0
        self._capture_seconds = 0.0
        self.encoder = None
        import atexit
        atexit.register(self.stop)

    def log(self, text: str):
        if self.on_event:
            try:
                self.on_event(text)
            except Exception:
                pass

    def is_running(self) -> bool:
        with self._lock:
            return self.running and self._ffmpeg_proc is not None and self._ffmpeg_proc.poll() is None

    def _fail(self, reason):
        self._last_error = str(reason)
        self.log(self._last_error)

    def _panorama_error(self, capture=None):
        if self.capture_mode != 'all':
            return ''
        if capture is None:
            try:
                with create_replay_capture() as source:
                    return self._panorama_error(source)
            except Exception as exc:
                return f'无法读取显示器信息，请选择单个屏幕录制（{exc}）'
        displays = enumerate_displays()
        monitors = match_monitors(capture.monitors, displays)
        info = monitors[0] if monitors else {}
        geometry = lambda row: tuple(row.get(key) for key in ('left', 'top', 'width', 'height'))
        if {geometry(row) for row in displays} != {geometry(row) for row in capture.monitors[1:]}:
            return '显示器配置已变化或无法完整读取，请选择单个屏幕录制'
        if info.get('mixed_refresh_rate') is not False:
            return info.get('recording_reason') or '无法确认所有显示器的刷新率，请选择单个屏幕录制'
        return ''

    def start(self) -> bool:
        if sys.platform == 'darwin':
            from .macos_permissions import screen_capture_permission_status
            permission = screen_capture_permission_status()
            if not permission['granted']:
                self._fail(permission['reason'])
                return False
        if not self.running and (self._ffmpeg_proc is not None or self._worker_thread is not None
                                 or self._capture_source is not None):
            self.stop()
        with self._lock:
            if self.running:
                return True
            if self._worker_thread and self._worker_thread.is_alive():
                self._fail('上一段回放录制仍在结束，请稍后重试')
                return False

            self._last_error = ''
            reason = self._panorama_error()
            if reason:
                self._fail(f'启动回放失败：{reason}')
                return False

            ffmpeg = get_ffmpeg_path()
            if not ffmpeg:
                self._fail("启动回放引擎失败：未检测到可用 FFmpeg 组件")
                return False

            self.save_dir.mkdir(parents=True, exist_ok=True)
            # 清理历史可能残留在相册中的旧临时目录
            legacy_cache = self.save_dir / ".replay_cache"
            if legacy_cache.exists():
                shutil.rmtree(legacy_cache, ignore_errors=True)

            with self._ram_lock:
                self._ram_chunks.clear()
                self._ram_chunk_spans.clear()
                self._total_bytes = 0

            self._captured_frames = 0
            self._capture_seconds = 0.0
            self._encoder_errors.clear()

            self.running = True
            self._worker_thread = threading.Thread(target=self._capture_and_encode_loop, daemon=True, name="ReplayCaptureThread")
            self._worker_thread.start()
            mode_desc = "全部屏幕" if self.capture_mode == 'all' else ("智能游戏屏幕" if self.capture_mode in ('game', 'smart', 'monitor') else self.capture_mode)
            self.log(f"4K 极清回放引擎已启动（纯内存环形缓冲, 目标: {mode_desc}, 编码: {self.codec.upper()}, 回看: {self.minutes}分钟, 码率: {self.bitrate_mbps}Mbps）")
            return True

    def stop(self):
        with self._lock:
            if (not self.running and self._ffmpeg_proc is None and self._worker_thread is None
                    and self._capture_source is None):
                return
            self.running = False
            source = self._capture_source

        # Only cancellation is safe across threads: MSS owns native HDCs on
        # its capture thread, whose context manager remains responsible for
        # close(). HDR cancellation wakes a grab waiting for its first frame.
        cancel = getattr(source, 'cancel', None)
        if callable(cancel):
            try:
                cancel()
            except Exception as exc:
                self.log(f'取消屏幕采集时发生异常：{exc}')

        proc = self._ffmpeg_proc
        if proc:
            try:
                # A frame writer may be blocked on a full pipe. Termination
                # releases it before close() can wait for the buffered lock.
                proc.terminate()
                proc.wait(timeout=1.5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            try:
                if proc.stdin:
                    proc.stdin.close()
            except (OSError, ValueError):
                pass

        for attr in ('_worker_thread', '_reader_thread', '_stderr_thread'):
            thread = getattr(self, attr)
            if thread and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=1.5)
            if not thread or not thread.is_alive():
                setattr(self, attr, None)
        self._ffmpeg_proc = None
        if self._worker_thread is None:
            self._capture_source = None

        with self._ram_lock:
            self._ram_chunks.clear()
            self._ram_chunk_spans.clear()
            self._total_bytes = 0

        self.log("4K 极清回放引擎已停止（内存缓冲区已清空释放）")

    def _stdout_reader_loop(self, proc: subprocess.Popen):
        """后台独立读取 FFmpeg 硬件编码器标准输出流，实时推入纯内存环形缓冲队列"""
        max_ram_bytes = int((self.minutes * 60 * self.bitrate_mbps * 1e6) / 8 * 1.5)
        clock = TransportStreamClock()
        # Drain until EOF, including packets buffered after encoder exit.
        while True:
            try:
                chunk = proc.stdout.read1(65536) if hasattr(proc.stdout, 'read1') else proc.stdout.read(4096)
                if not chunk:
                    break
                start, end = clock.span(chunk)
                with self._ram_lock:
                    self._ram_chunks.append((end, chunk))
                    self._ram_chunk_spans.append((start, end))
                    self._total_bytes += len(chunk)

                    # Keep a short keyframe lead-in; retention follows media
                    # PTS, independent of stdout batching or pipe congestion.
                    cutoff = end - (self.minutes * 60 + 3)
                    while self._ram_chunks and (self._ram_chunks[0][0] < cutoff or self._total_bytes > max_ram_bytes):
                        _, old_data = self._ram_chunks.popleft()
                        self._ram_chunk_spans.popleft()
                        self._total_bytes -= len(old_data)
            except Exception:
                break

    def _stderr_reader_loop(self, proc):
        for line in iter(proc.stderr.readline, b''):
            text = line.decode('utf-8', errors='replace').strip()
            if text:
                self._encoder_errors.append(text)

    def _encoder_command(self, ffmpeg, encoder, source):
        fallback_filter = ('scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format=yuv420p,'
                           'setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709')
        video_filter = getattr(source, 'encoder_filter', fallback_filter)
        color_args = getattr(source, 'output_color_args', (
            '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709', '-color_range', 'tv'))
        audio_args = ['-map','0:v:0','-map','0:a:0','-c:a','aac','-b:a','192k'] if getattr(source,'audio_format',None) else []
        return [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
                '-f', 'matroska', '-i', 'pipe:0', *audio_args, '-vf', video_filter, '-c:v', encoder,
                *_encoder_runtime_options(encoder),
                '-b:v', f'{self.bitrate_mbps}M', '-pix_fmt', 'yuv420p', *color_args,
                '-fps_mode', 'passthrough', '-enc_time_base', '1:1000',
                '-g', str(self.fps * 2), '-force_key_frames', 'expr:gte(t,n_forced*2)',
                '-flush_packets', '1', '-f', 'mpegts', 'pipe:1']

    def _capture_and_encode_loop(self):
        """后台屏幕抓取与纯内存流式硬件编码主循环"""
        proc = None
        source = None
        try:
            _ensure_dpi_awareness()
            ffmpeg = get_ffmpeg_path()
            if not ffmpeg:
                raise RuntimeError('未检测到可用 FFmpeg 组件')
            encoder = detect_hardware_encoder(self.codec)
            self.encoder = encoder
            with create_replay_capture() as sct:
                source = sct
                with self._lock:
                    if not self.running:
                        return
                    self._capture_source = sct
                reason = self._panorama_error(sct)
                if reason:
                    raise RuntimeError(reason)
                explicit_monitor = re.fullmatch(r'monitor_(\d+)', self.capture_mode)
                if explicit_monitor:
                    index = int(explicit_monitor.group(1))
                    if index < 1 or index >= len(sct.monitors):
                        raise RuntimeError('所选显示器已断开，请重新选择录制屏幕')
                    bbox = sct.monitors[index]
                else:
                    bbox = get_target_monitor_bbox(sct, mode=self.capture_mode)
                w, h = int(bbox['width']) & ~1, int(bbox['height']) & ~1
                if w < 2 or h < 2:
                    raise RuntimeError('录制区域尺寸无效')
                crop_bbox = dict(bbox) if sys.platform == 'darwin' else dict(left=bbox['left'], top=bbox['top'], width=w, height=h)
                if hasattr(sct, 'configure'):
                    if sys.platform == 'darwin' and callable(getattr(sct, 'read_audio', None)):
                        sct.configure(crop_bbox, self.fps, include_system_audio=True)
                    else:
                        sct.configure(crop_bbox, self.fps)
                if sys.platform == 'darwin' and getattr(sct, 'frame_size', None):
                    w, h = sct.frame_size
                    if min(w, h) < 2 or w % 2 or h % 2:
                        raise RuntimeError('macOS 捕获物理像素尺寸无效')
                self._current_width, self._current_height = w, h
                if not self.running:
                    return
                self.capture_method = str(getattr(sct, 'capture_method', 'MSS'))
                self.color_mode = str(getattr(sct, 'color_mode', 'SDR'))
                self.audio_format = getattr(sct, 'audio_format', None)
                self.audio_enabled = bool(self.audio_format)

                # Raw RGB in Matroska carries capture PTS. Input -r would
                # overwrite them; passthrough also prevents CFR duplicates.
                cmd = self._encoder_command(ffmpeg, encoder, sct)
                with self._lock:
                    if not self.running:
                        return
                    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, **_subprocess_hidden_flags())
                    self._ffmpeg_proc = proc
                _assign_process_to_job(proc)
                for attr, target, name in (
                    ('_reader_thread', self._stdout_reader_loop, 'ReplayReaderThread'),
                    ('_stderr_thread', self._stderr_reader_loop, 'ReplayErrorThread')):
                    thread = threading.Thread(target=target, args=(proc,), daemon=True, name=name)
                    setattr(self, attr, thread)
                    thread.start()
                writer = (TimestampedAVWriter(proc.stdin, w, h, self.fps, *self.audio_format)
                          if self.audio_enabled else TimestampedRGBWriter(proc.stdin, w, h, self.fps))
                frame_interval = 1.0 / self.fps
                first_timestamp = last_timestamp = None
                last_display_check = time.monotonic()
                while self.running:
                    t_start = time.perf_counter()
                    if time.monotonic() - last_display_check >= 1.0:
                        reason = self._panorama_error(sct)
                        if reason:
                            raise RuntimeError(reason)
                        last_display_check = time.monotonic()
                    shot = sct.grab(crop_bbox)
                    timestamp = getattr(shot, 'timestamp', None)
                    if timestamp is None:
                        timestamp = time.monotonic()
                    if not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
                        raise RuntimeError('屏幕采集时间无效，请重新启动录制')
                    if not self.running:
                        break
                    if proc.poll() is not None:
                        raise RuntimeError('视频编码器已退出：' + '；'.join(self._encoder_errors))
                    if last_timestamp is not None and timestamp < last_timestamp:
                        raise RuntimeError('屏幕采集时间发生变化，请重新启动录制')
                    if timestamp != last_timestamp:
                        if first_timestamp is None:
                            first_timestamp = timestamp
                        self._capture_seconds = writer.write_frame(shot.rgb, timestamp - first_timestamp)
                        self._captured_frames += 1
                        last_timestamp = timestamp
                    if self.audio_enabled and first_timestamp is not None:
                        self._drain_audio(sct, writer, first_timestamp)
                    # Target FPS is an upper bound; slow capture keeps its
                    # real elapsed time instead of accelerating playback.
                    elapsed = time.perf_counter() - t_start
                    sleep_time = frame_interval - elapsed
                    if sleep_time > 0:
                        time.sleep(sleep_time)
        except Exception as exc:
            if self.running:
                self._fail(f'回放录制失败：{exc}')
        finally:
            with self._lock:
                self.running = False
                if self._capture_source is source:
                    self._capture_source = None
            if proc:
                try:
                    if proc.stdin:
                        proc.stdin.close()
                    proc.wait(timeout=2.0)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

    @staticmethod
    def _drain_audio(source, writer, first_timestamp):
        # Keep source PTS even when audio arrives after a newer video frame.
        # Bound each drain so malformed sources cannot hang capture shutdown.
        for _ in range(1024):
            frame = source.read_audio()
            if frame is None:
                return
            writer.write_audio(frame.pcm, frame.timestamp-first_timestamp,
                               sample_rate=frame.sample_rate, channels=frame.channels)
        raise RuntimeError('系统音频数据持续积压，请降低录制负载后重新启动')

    def save_replay(self, title: Optional[str] = None) -> Optional[str]:
        """
        瞬间将当前纯内存环形缓冲区中的数据流无损封装为 MP4 文件 (0.05秒无损流复制，0 磁盘磨损)
        """
        now = time.monotonic()
        if now - self._last_save_time < 2.0:
            return None
        self._last_save_time = now

        ffmpeg = get_ffmpeg_path()
        if not ffmpeg:
            return None

        # 从纯内存中安全复制当前有效数据流
        with self._ram_lock:
            if self._total_bytes < 5_000:
                self.log("回放内存缓冲区尚在初始化积蓄，请稍后再试")
                return None
            all_raw = b"".join(c[1] for c in self._ram_chunks)
            media_seconds = self._buffered_seconds_locked()

        valid_ts = _align_ts_stream(all_raw)
        if len(valid_ts) < 5_000:
            self.log("回放数据有效性校验未通过，请稍后再试")
            return None

        if not title:
            _, title = smart_foreground_info()
        clean_title = sanitize_filename(title)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_filename = f"DS_{clean_title}_{timestamp}_replay.mp4"
        out_path = self.save_dir / out_filename
        # Publish only a complete MP4. The gallery may refresh while FFmpeg
        # is still writing or after a failed mux; its hidden partial-file
        # filter keeps both cases out of the user's saved recordings.
        partial_path = out_path.with_name(f'.{out_path.stem}.partial.mp4')
        proc = None

        try:
            # Lead-in packets retained for the decoder do not extend the
            # requested window. Stream copy starts at the next keyframe.
            trim_seconds = max(0.0, media_seconds - self.minutes * 60)
            cmd_mux = [
                ffmpeg,
                "-y",
                "-f", "mpegts",
                "-i", "pipe:0",
                "-ss", f"{trim_seconds:.6f}",
                "-t", str(self.minutes * 60),
                "-c", "copy",
                "-avoid_negative_ts", "make_zero",
                "-movflags", "+faststart",
                str(partial_path)
            ]

            t0 = time.perf_counter()
            proc = subprocess.Popen(
                cmd_mux,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **_subprocess_hidden_flags()
            )
            proc.communicate(input=valid_ts, timeout=60)
            elapsed = time.perf_counter() - t0

            if proc.returncode == 0 and partial_path.is_file() and partial_path.stat().st_size > 1000:
                partial_path.replace(out_path)
                # 瞬间为画廊生成一帧高保真封面预览图 (供画廊秒级展示，零卡顿)
                thumb_jpg = out_path.with_suffix('.jpg')
                try:
                    cmd_thumb = [
                        ffmpeg, "-y",
                        "-ss", "00:00:00.100",
                        "-i", str(out_path),
                        "-vframes", "1",
                        "-q:v", "3",
                        str(thumb_jpg)
                    ]
                    thumbnail = subprocess.run(cmd_thumb, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                               timeout=10, **_subprocess_hidden_flags())
                    if thumbnail.returncode != 0:
                        thumb_jpg.unlink(missing_ok=True)
                except Exception:
                    try:
                        thumb_jpg.unlink(missing_ok=True)
                    except OSError:
                        pass

                # 保存元数据 sidecar
                sidecar_json = out_path.with_suffix('.json')
                duration = mp4_duration_seconds(out_path)
                if duration is None:
                    duration = min(media_seconds, self.minutes * 60)
                meta = {
                    "title": title,
                    "raw_title": title,
                    "created": datetime.now().isoformat(),
                    "duration_seconds": round(duration, 3),
                    "duration_minutes": round(duration / 60, 4),
                    "buffer_minutes": self.minutes,
                    "codec": self.codec,
                    "bitrate_mbps": self.bitrate_mbps,
                    "resolution": f"{self._current_width}x{self._current_height}",
                    "mode": self.capture_mode,
                    "capture_method": self.capture_method,
                    "color_mode": self.color_mode,
                    "audio": self.audio_enabled,
                    "audio_source": "system" if self.audio_enabled else None,
                    "microphone": False,
                    "favorite": False,
                    "is_video": True
                }
                try:
                    sidecar_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
                except Exception:
                    pass

                size_mb = out_path.stat().st_size / (1024 * 1024)
                self.log(f"🎬 极清回放录像已瞬间保存: {out_filename} ({size_mb:.1f}MB, 耗时 {elapsed:.2f}s, 零磁盘读写损耗)")
                return str(out_path)
            self.log('回放封装失败，未保存不完整的视频')
        except Exception as exc:
            if isinstance(exc, subprocess.TimeoutExpired) and proc is not None:
                proc.kill()
                proc.communicate()
            self.log(f"合并保存回放失败: {exc}")
        finally:
            partial_path.unlink(missing_ok=True)
        return None

    def _buffered_seconds_locked(self):
        if not self._ram_chunks:
            return 0.0
        if len(self._ram_chunk_spans) == len(self._ram_chunks):
            start = self._ram_chunk_spans[0][0]
            end = self._ram_chunk_spans[-1][1]
            return max(0.0, end - start) + 1.0 / self.fps
        # Preserve compatibility with consumers that seed legacy chunks.
        return max(0.0, self._ram_chunks[-1][0] - self._ram_chunks[0][0])

    def get_status(self) -> dict:
        """获取当前引擎实时状态、纯内存缓冲区占用与时长"""
        is_run = self.is_running()
        # Reading status must never spawn encoder probes. Probe only when
        # starting an actual recording, on the capture worker thread.
        encoder = self.encoder
        with self._ram_lock:
            total_mb = self._total_bytes / (1024 * 1024)
            chunks_count = len(self._ram_chunks)
            buffered_sec = min(self._buffered_seconds_locked(), self.minutes * 60)

        return {
            "enabled": is_run,
            "running": is_run,
            "minutes": self.minutes,
            "codec": self.codec,
            "encoder": encoder,
            "bitrate_mbps": self.bitrate_mbps,
            "fps": self.fps,
            "capture_mode": self.capture_mode,
            "capture_method": self.capture_method,
            "color_mode": self.color_mode,
            "audio": self.audio_enabled,
            "audio_source": "system" if self.audio_enabled else None,
            "audio_format": self.audio_format,
            "microphone": False,
            "last_error": self._last_error,
            "captured_fps": round((self._captured_frames - 1) / self._capture_seconds, 1) if self._capture_seconds > 0 else 0.0,
            "buffered_seconds": round(buffered_sec, 1),
            "chunks_count": chunks_count,
            "current_size_mb": round(total_mb, 1),
            "estimated_max_gb": calculate_estimated_ram_gb(self.minutes, self.bitrate_mbps),
            "is_pure_ram": True,
            "resolution": f"{self._current_width}x{self._current_height}" if self._current_width else "未启动"
        }

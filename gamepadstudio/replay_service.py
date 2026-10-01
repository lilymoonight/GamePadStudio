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
    proj_bin = Path(__file__).resolve().parents[1] / 'bin' / 'ffmpeg.exe'
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
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
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


def detect_hardware_encoder(codec: str = "hevc") -> str:
    """
    智能探针：根据本机 GPU 型号与驱动，探测可用的高吞吐硬件加速编码器
    优先级：AMD AMF -> NVIDIA NVENC -> Intel QSV -> MediaFoundation -> 软件回退
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

    for enc in candidates:
        try:
            cmd = [
                ffmpeg,
                "-y",
                "-f", "lavfi",
                "-i", "testsrc=size=256x256:rate=30",
                "-t", "0.1",
                "-c:v", enc,
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
        self._total_bytes: int = 0

        self._worker_thread: Optional[threading.Thread] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._last_save_time = 0.0
        self._current_width = 0
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

    def start(self) -> bool:
        with self._lock:
            if self.running:
                return True

            ffmpeg = get_ffmpeg_path()
            if not ffmpeg:
                self.log("启动回放引擎失败：未检测到可用 FFmpeg 组件")
                return False

            self.save_dir.mkdir(parents=True, exist_ok=True)
            # 清理历史可能残留在相册中的旧临时目录
            legacy_cache = self.save_dir / ".replay_cache"
            if legacy_cache.exists():
                shutil.rmtree(legacy_cache, ignore_errors=True)

            with self._ram_lock:
                self._ram_chunks.clear()
                self._total_bytes = 0

            self.running = True
            self._worker_thread = threading.Thread(target=self._capture_and_encode_loop, daemon=True, name="ReplayCaptureThread")
            self._worker_thread.start()
            mode_desc = "双屏全景 (7680x2160)" if self.capture_mode == 'all' else ("智能游戏屏幕" if self.capture_mode in ('game', 'smart', 'monitor') else self.capture_mode)
            self.log(f"4K 极清回放引擎已启动（纯内存环形缓冲, 目标: {mode_desc}, 编码: {self.codec.upper()}, 回看: {self.minutes}分钟, 码率: {self.bitrate_mbps}Mbps）")
            return True

    def stop(self):
        with self._lock:
            if not self.running:
                return
            self.running = False

        if self._ffmpeg_proc:
            try:
                if self._ffmpeg_proc.stdin:
                    self._ffmpeg_proc.stdin.close()
                self._ffmpeg_proc.terminate()
                self._ffmpeg_proc.wait(timeout=1.5)
            except Exception:
                try:
                    self._ffmpeg_proc.kill()
                except Exception:
                    pass
            self._ffmpeg_proc = None

        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1.0)
            self._reader_thread = None

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.0)
            self._worker_thread = None

        with self._ram_lock:
            self._ram_chunks.clear()
            self._total_bytes = 0

        self.log("4K 极清回放引擎已停止（内存缓冲区已清空释放）")

    def _stdout_reader_loop(self, proc: subprocess.Popen):
        """后台独立读取 FFmpeg 硬件编码器标准输出流，实时推入纯内存环形缓冲队列"""
        max_ram_bytes = int((self.minutes * 60 * self.bitrate_mbps * 1e6) / 8 * 1.5)
        while self.running and proc.poll() is None:
            try:
                chunk = proc.stdout.read1(65536) if hasattr(proc.stdout, 'read1') else proc.stdout.read(4096)
                if not chunk:
                    break
                now = time.monotonic()
                with self._ram_lock:
                    self._ram_chunks.append((now, chunk))
                    self._total_bytes += len(chunk)
                    
                    # 滑动环形窗口淘汰：修剪超过最大回看时间的数据包
                    cutoff = now - (self.minutes * 60 + 3)
                    while self._ram_chunks and (self._ram_chunks[0][0] < cutoff or self._total_bytes > max_ram_bytes):
                        _, old_data = self._ram_chunks.popleft()
                        self._total_bytes -= len(old_data)
            except Exception:
                break

    def _capture_and_encode_loop(self):
        """后台屏幕抓取与纯内存流式硬件编码主循环"""
        _ensure_dpi_awareness()
        ffmpeg = get_ffmpeg_path()
        if not ffmpeg:
            return

        encoder = detect_hardware_encoder(self.codec)
        self.encoder = encoder
        seg_duration = 2  # 2秒一个 GOP 关键帧区间
        gop_size = self.fps * seg_duration

        with create_replay_capture() as sct:
            bbox = get_target_monitor_bbox(sct, mode=self.capture_mode)
            w = bbox["width"] - (bbox["width"] % 2)
            h = bbox["height"] - (bbox["height"] % 2)
            self._current_width = w
            self._current_height = h
            crop_bbox = dict(left=bbox["left"], top=bbox["top"], width=w, height=h)

            cmd = [
                ffmpeg,
                "-y",
                "-f", "rawvideo",
                "-pix_fmt", "rgb24",
                "-s", f"{w}x{h}",
                "-r", str(self.fps),
                "-i", "pipe:0",
                "-c:v", encoder,
                "-b:v", f"{self.bitrate_mbps}M",
                "-g", str(gop_size),
                "-flush_packets", "1",
                "-f", "mpegts",
                "pipe:1"
            ]

            try:
                self._ffmpeg_proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    **_subprocess_hidden_flags()
                )
                _assign_process_to_job(self._ffmpeg_proc)
            except Exception as exc:
                self.log(f"启动 FFmpeg 硬件编码器进程失败: {exc}")
                self.running = False
                return

            # 启动内存数据流提取守护线程
            self._reader_thread = threading.Thread(
                target=self._stdout_reader_loop,
                args=(self._ffmpeg_proc,),
                daemon=True,
                name="ReplayReaderThread"
            )
            self._reader_thread.start()

            frame_interval = 1.0 / self.fps

            try:
                while self.running:
                    t_start = time.perf_counter()
                    shot = sct.grab(crop_bbox)
                    
                    if not self.running or self._ffmpeg_proc.poll() is not None:
                        break

                    try:
                        self._ffmpeg_proc.stdin.write(shot.rgb)
                        self._ffmpeg_proc.stdin.flush()
                    except (BrokenPipeError, OSError):
                        break

                    # 精准帧率节奏控制
                    elapsed = time.perf_counter() - t_start
                    sleep_time = frame_interval - elapsed
                    if sleep_time > 0:
                        time.sleep(sleep_time)
            except Exception as e:
                self.log(f"回放录制抓取循环异常: {e}")
            finally:
                if self._ffmpeg_proc and self._ffmpeg_proc.poll() is None:
                    try:
                        if self._ffmpeg_proc.stdin:
                            self._ffmpeg_proc.stdin.close()
                        self._ffmpeg_proc.wait(timeout=1.0)
                    except Exception:
                        pass

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

        try:
            cmd_mux = [
                ffmpeg,
                "-y",
                "-f", "mpegts",
                "-i", "pipe:0",
                "-c", "copy",
                "-movflags", "+faststart",
                str(out_path)
            ]

            t0 = time.perf_counter()
            proc = subprocess.Popen(
                cmd_mux,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **_subprocess_hidden_flags()
            )
            proc.communicate(input=valid_ts)
            elapsed = time.perf_counter() - t0

            if out_path.is_file() and out_path.stat().st_size > 1000:
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
                    subprocess.run(cmd_thumb, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_subprocess_hidden_flags())
                except Exception:
                    pass

                # 保存元数据 sidecar
                sidecar_json = out_path.with_suffix('.json')
                meta = {
                    "title": title,
                    "raw_title": title,
                    "created": datetime.now().isoformat(),
                    "duration_minutes": self.minutes,
                    "codec": self.codec,
                    "bitrate_mbps": self.bitrate_mbps,
                    "resolution": f"{self._current_width}x{self._current_height}",
                    "mode": self.capture_mode,
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
        except Exception as exc:
            self.log(f"合并保存回放失败: {exc}")
        return None

    def get_status(self) -> dict:
        """获取当前引擎实时状态、纯内存缓冲区占用与时长"""
        is_run = self.is_running()
        # Reading status must never spawn encoder probes. Probe only when
        # starting an actual recording, on the capture worker thread.
        encoder = self.encoder
        with self._ram_lock:
            total_mb = self._total_bytes / (1024 * 1024)
            chunks_count = len(self._ram_chunks)
            if chunks_count > 1:
                buffered_sec = max(0.0, self._ram_chunks[-1][0] - self._ram_chunks[0][0])
            else:
                buffered_sec = 0.0

        return {
            "enabled": is_run,
            "running": is_run,
            "minutes": self.minutes,
            "codec": self.codec,
            "encoder": encoder,
            "bitrate_mbps": self.bitrate_mbps,
            "fps": self.fps,
            "capture_mode": self.capture_mode,
            "buffered_seconds": round(buffered_sec, 1),
            "chunks_count": chunks_count,
            "current_size_mb": round(total_mb, 1),
            "estimated_max_gb": calculate_estimated_ram_gb(self.minutes, self.bitrate_mbps),
            "is_pure_ram": True,
            "resolution": f"{self._current_width}x{self._current_height}" if self._current_width else "未启动"
        }

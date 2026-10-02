"""Color-managed ScreenCaptureKit RGB capture in CG points and physical pixels.

The bundled helper validates floating HDR buffers and tone maps before 8-bit
conversion. Python never labels an 8-bit SDR grab as HDR. Capture is explicit;
constructing this adapter only reads display metadata and TCC preflight state.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import deque
import io
import json
import math
import subprocess
import threading
import time

from PIL import Image

from .display_info import aggregate_displays, capture_display_info
from .macos_display import display_metadata, helper_path


@dataclass(frozen=True)
class NativeFrame:
    rgb: bytes
    width: int
    height: int
    timestamp: float
    metadata: dict


@dataclass(frozen=True)
class AudioFrame:
    """System audio, interleaved signed-16 PCM; PTS is the first sample time."""
    pcm: bytes
    timestamp: float
    sample_rate: int = 48000
    channels: int = 2


def _read_exact(stream, count):
    data = bytearray()
    while len(data) < count:
        block = stream.read(count - len(data))
        if not block:
            raise RuntimeError('macOS 捕获没有返回完整图像')
        data.extend(block)
    return bytes(data)


def read_packet(stream):
    line = stream.readline(65537)
    if not line or len(line) > 65536 or not line.endswith(b'\n'):
        raise RuntimeError('macOS 捕获帧头无效')
    value = json.loads(line)
    if not isinstance(value, dict) or value.get('version') != 1:
        raise RuntimeError('macOS 捕获协议不兼容')
    timestamp = value.get('timestamp')
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
        raise RuntimeError('macOS 捕获帧时间无效')
    if value.get('type') == 'audio':
        count = value.get('bytes')
        if (value.get('sample_rate') != 48000 or value.get('channels') != 2
                or value.get('sample_rate') is True or value.get('channels') is True
                or value.get('audio_format') != 's16le' or value.get('audio_source') != 'system'
                or value.get('microphone') is not False
                or isinstance(count, bool) or not isinstance(count, int) or not 0 < count <= 4194304 or count % 4):
            raise RuntimeError('macOS 系统音频格式无效')
        return AudioFrame(_read_exact(stream, count), float(timestamp))
    if value.get('type', 'video') != 'video':
        raise RuntimeError('macOS 捕获包类型无效')
    width, height, count = (value.get(key) for key in ('width', 'height', 'bytes'))
    if (any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in (width, height, count))
            or count > 512 * 1024 * 1024 or count != width * height * 3):
        raise RuntimeError('macOS 捕获图像尺寸无效')
    if (value.get('pixel_format') != 'rgb24' or value.get('color_primaries') != 'bt709'
            or value.get('transfer') not in ('srgb', 'bt709') or value.get('color_managed') is not True):
        raise RuntimeError('macOS 捕获缺少已验证的颜色转换')
    if value.get('hdr_capture') and not (value.get('hdr_verified') is True and value.get('tone_mapped') is True
                                       and value.get('source_pixel_format') == 'rgba16f'
                                       and value.get('source_color_space') == 'extended-linear-srgb'):
        raise RuntimeError('HDR 浮点来源或色调映射未确认，已拒绝录制')
    return NativeFrame(_read_exact(stream, count), width, height, float(timestamp), value)


def read_frame(stream):
    value = read_packet(stream)
    if not isinstance(value, NativeFrame):
        raise RuntimeError('macOS 截图返回了非图像包')
    return value


def _rectangle(value):
    rect = {key: float(value[key]) for key in ('left', 'top', 'width', 'height')}
    if not all(math.isfinite(v) for v in rect.values()) or min(rect['width'], rect['height']) <= 0:
        raise ValueError('捕获范围必须是有效的 CG 全局坐标矩形')
    return rect


def monitors_from_displays(rows):
    if not rows:
        raise RuntimeError('没有读取到可用显示器')
    left = min(row['left'] for row in rows); top = min(row['top'] for row in rows)
    right = max(row['left'] + row['width'] for row in rows)
    bottom = max(row['top'] + row['height'] for row in rows)
    scale = max(float(row['scale']) for row in rows)
    union = dict(left=left, top=top, width=right-left, height=bottom-top, scale=scale,
                 pixel_width=round((right-left)*scale), pixel_height=round((bottom-top)*scale),
                 coordinate_space='cg-points', displays=rows, **aggregate_displays(rows))
    return [union, *[dict(row) for row in rows]]


def composite_frames(bbox, rows, images, *, even=False):
    """Render point-space display placement to one physical-pixel canvas.

    Mixed Retina scales use the largest capture scale; logical layout is
    preserved and lower-density displays are resized once to that density.
    Screen gaps stay black, exactly as a bounding desktop capture requires.
    """
    rect = _rectangle(bbox)
    scale = max(float(row['scale']) for row in rows)
    width, height = round(rect['width'] * scale), round(rect['height'] * scale)
    if even:
        width &= ~1; height &= ~1
    if min(width, height) < 1 or width * height > 67108864:
        raise RuntimeError('跨屏捕获物理像素尺寸超出支持范围')
    canvas = Image.new('RGB', (width, height), 'black')
    for row, image in zip(rows, images, strict=True):
        size = (round(row['width'] * scale), round(row['height'] * scale))
        if image.size != size:
            image = image.resize(size, Image.Resampling.LANCZOS)
        canvas.paste(image, (round((row['left']-rect['left'])*scale), round((row['top']-rect['top'])*scale)))
    return canvas


class _NativeSource:
    """Drain native streams continuously; keep only one latest RGB frame."""
    def __init__(self, executable, display_id, fps, transfer, *, system_audio=False, kind='display'):
        if kind not in ('display','window'):
            raise ValueError('Invalid native stream kind')
        self.condition = threading.Condition()
        self.closed = False
        self.cleanup_complete = False
        self.frame = None
        self.audio = deque()
        self.audio_bytes = 0
        self.system_audio = system_audio
        self.error = ''
        self.stderr = bytearray()
        command = [executable, 'stream', kind, str(display_id), transfer, str(fps)]
        if system_audio:
            command.append('system-audio')
        self.process = subprocess.Popen(command,
                                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.reader = threading.Thread(target=self._read, daemon=True, name='ScreenCaptureKitFrames')
        self.errors = threading.Thread(target=self._errors, daemon=True, name='ScreenCaptureKitErrors')
        self.errors.start(); self.reader.start()

    def _errors(self):
        try:
            for block in iter(lambda: self.process.stderr.read(1024), b''):
                self.stderr.extend(block)
                if len(self.stderr) > 8192:
                    del self.stderr[:-8192]
        except (OSError, ValueError):
            pass

    def _read(self):
        last = None
        last_audio = None
        try:
            while not self.closed:
                frame = read_packet(self.process.stdout)
                if isinstance(frame, AudioFrame):
                    if not self.system_audio:
                        raise RuntimeError('未请求系统音频但捕获流返回音频')
                    if last_audio is not None and frame.timestamp <= last_audio:
                        raise RuntimeError('ScreenCaptureKit 系统音频时间发生变化')
                    last_audio = frame.timestamp
                    with self.condition:
                        if self.audio_bytes + len(frame.pcm) > 48000 * 2 * 2 * 5:
                            raise RuntimeError('系统音频队列溢出，请降低录制负载后重试')
                        self.audio.append(frame)
                        self.audio_bytes += len(frame.pcm)
                    continue
                if last is not None and frame.timestamp < last:
                    raise RuntimeError('ScreenCaptureKit 原始帧时间发生变化，请重新录制')
                last = frame.timestamp
                with self.condition:
                    self.frame = frame
                    self.condition.notify_all()
        except (OSError, RuntimeError, ValueError) as exc:
            if not self.closed:
                self.errors.join(timeout=.2)
                with self.condition:
                    self.error = self.stderr.decode('utf-8', 'replace').strip() or str(exc)
                    self.condition.notify_all()

    def latest(self):
        with self.condition:
            deadline = time.monotonic() + 15
            while self.frame is None and not self.error and not self.closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError('ScreenCaptureKit 捕获启动超时')
                self.condition.wait(min(.1, remaining))
            if self.closed:
                raise RuntimeError('macOS 捕获已取消')
            if self.error:
                raise RuntimeError(self.error)
            return self.frame

    def read_audio(self):
        with self.condition:
            if self.error:
                raise RuntimeError(self.error)
            if not self.audio:
                return None
            frame = self.audio.popleft()
            self.audio_bytes -= len(frame.pcm)
            return frame

    def close(self):
        with self.condition:
            if self.cleanup_complete:
                return
            self.closed = True; self.condition.notify_all()
        errors = []
        if self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except (OSError, subprocess.SubprocessError) as exc:
                errors.append(exc)
                if self.process.poll() is None:
                    try:
                        self.process.kill(); self.process.wait(timeout=2)
                    except (OSError, subprocess.SubprocessError) as exc:
                        errors.append(exc)
        for thread in (self.reader, self.errors):
            try:
                thread.join(timeout=2)
            except RuntimeError as exc:
                errors.append(exc)
        for pipe, thread in ((self.process.stdout, self.reader), (self.process.stderr, self.errors)):
            if thread.is_alive():
                continue  # Closing a BufferedReader held by a blocked read could hang.
            try:
                pipe.close()
            except OSError as exc:
                errors.append(exc)
        self.cleanup_complete = (self.process.poll() is not None and self.process.stdout.closed
                                 and self.process.stderr.closed and not self.reader.is_alive() and not self.errors.is_alive())
        if not self.cleanup_complete:
            raise RuntimeError('macOS 捕获进程仍未完全回收，请再次停止') from (errors[0] if errors else None)


class MacCapture:
    def __init__(self, *, transfer='srgb', metadata=None, executable=None, runner=None,
                 source_factory=None, permission_check=None):
        if transfer not in ('srgb', 'bt709'):
            raise ValueError('Unsupported capture transfer')
        self.transfer = transfer
        self.executable = executable or helper_path()
        self.runner = runner or subprocess.run
        self._metadata = metadata
        value = metadata if metadata is not None else display_metadata(runner=self.runner, executable=self.executable)
        self.displays = value['displays']
        self.monitors = monitors_from_displays(self.displays)
        self.capabilities = value
        self.source_factory = source_factory or _NativeSource
        self.permission_check = permission_check
        self.sources = []
        self.source_lock = threading.Lock()
        self.cancelled = threading.Event()
        self.frame_size = None
        self.bbox = None
        self.rows = []
        self.last_check = 0
        self.last_key = None
        self.clock_base = None
        self.source_base = None
        self.last_timestamp = None
        self.capture_method = 'ScreenCaptureKit / Core Image'
        self.color_mode = ''
        self.window_id = None
        self.window_pid = None
        self.window_size = None
        self.source_frame_size = None
        self.audio_format = None
        self.audio_enabled = False
        self.audio_scope = None
        self.encoder_filter = ('scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format=yuv420p,'
                               'setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709')
        self.output_color_args = ('-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-color_range','tv')

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _permission(self):
        if self.permission_check:
            value = self.permission_check()
        else:
            from .macos_permissions import screen_capture_permission_status
            value = screen_capture_permission_status()
        if not value.get('granted'):
            raise RuntimeError(value.get('reason') or '请先授权 macOS 屏幕录制权限')
        if self.cancelled.is_set():
            raise RuntimeError('macOS 捕获已取消')

    @staticmethod
    def _signature(rows):
        return tuple(tuple(row.get(k) for k in ('display_id','left','top','width','height','pixel_width',
                                              'pixel_height','scale','refresh_hz','hdr_enabled')) for row in rows)

    def _validate_frame(self, frame, row=None):
        if frame.metadata.get('transfer') != self.transfer:
            raise RuntimeError('macOS 捕获传递函数与编码器不一致')
        if row and row.get('hdr_enabled') is True and not (
                frame.metadata.get('hdr_capture') is True and frame.metadata.get('hdr_verified') is True
                and frame.metadata.get('tone_mapped') is True):
            raise RuntimeError('HDR 显示器没有返回已验证的浮点捕获，已拒绝降级录制')
        if row and frame.metadata.get('display_id') != row['display_id']:
            raise RuntimeError('捕获流显示器身份不一致')
        if row and (frame.width, frame.height) != (row['pixel_width'], row['pixel_height']):
            raise RuntimeError('显示器物理像素尺寸已变化，请重新录制')
        return Image.frombytes('RGB', (frame.width, frame.height), frame.rgb)

    def _shot(self, kind, identity):
        self._permission()
        result = self.runner([self.executable, 'shot', kind, str(identity), self.transfer, '30'],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=False)
        if result.returncode:
            raise RuntimeError(result.stderr.decode('utf-8', 'replace').strip() or 'ScreenCaptureKit 截图失败')
        frame = read_frame(io.BytesIO(result.stdout))
        key = 'window_id' if kind == 'window' else 'display_id'
        if frame.metadata.get(key) != identity:
            raise RuntimeError('捕获目标身份不一致，已丢弃截图')
        row = next((r for r in self.displays if r['display_id'] == identity), None) if kind == 'display' else None
        return self._validate_frame(frame, row)

    def capture_display(self, display_id):
        if isinstance(display_id,bool) or not isinstance(display_id,int) or display_id <= 0:
            raise ValueError('Display ID must be a positive integer')
        if not any(row['display_id'] == display_id for row in self.displays):
            raise RuntimeError('所选显示器已断开')
        return self._shot('display', display_id)

    def capture_window(self, window_id):
        if isinstance(window_id, bool) or not isinstance(window_id, int) or window_id <= 0:
            raise ValueError('Window ID must be a positive integer')
        return self._shot('window', window_id)

    def capture_rect(self, bbox):
        rows = capture_display_info(bbox, self.displays)['displays']
        if not rows:
            raise RuntimeError('捕获矩形没有覆盖可用显示器')
        return composite_frames(bbox, rows, [self.capture_display(row['display_id']) for row in rows])

    def capture_all(self):
        return self.capture_rect(self.monitors[0])

    def configure(self, bbox, fps=30, *, include_system_audio=False):
        if self.sources:
            raise RuntimeError('捕获已启动，请重新创建捕获实例')
        self._permission()
        info = capture_display_info(bbox, self.displays)
        if not info['displays'] or not info['recording_enabled']:
            raise RuntimeError(info['recording_reason'] or '没有可用录制显示器')
        if info['hdr_enabled'] is None:
            raise RuntimeError('无法确认 macOS 显示器 EDR 状态')
        if info['hdr_enabled'] and not self.capabilities.get('hdr_capture_available'):
            raise RuntimeError('HDR 捕获需要 macOS 15 或更新版本及 Apple Silicon')
        if isinstance(fps, bool) or not 0 < float(fps) <= 240:
            raise ValueError('Invalid capture frame rate')
        self.bbox = _rectangle(bbox)
        self.rows = info['displays']
        self.signature = self._signature(self.rows)
        scale = max(float(row['scale']) for row in self.rows)
        self.frame_size = (round(self.bbox['width']*scale) & ~1, round(self.bbox['height']*scale) & ~1)
        if min(self.frame_size) < 2 or self.frame_size[0] * self.frame_size[1] > 67108864:
            raise RuntimeError('录制物理像素尺寸超出支持范围')
        self.color_mode = 'HDR → SDR BT.709' if info['hdr_enabled'] else 'SDR BT.709'
        self.audio_enabled = bool(include_system_audio)
        self.audio_format = (48000, 2) if self.audio_enabled else None
        self.audio_scope = 'system' if self.audio_enabled else None
        try:
            for index, row in enumerate(self.rows):
                kwargs = {'system_audio': True} if self.audio_enabled and index == 0 else {}
                source = self.source_factory(self.executable, row['display_id'], round(fps), self.transfer, **kwargs)
                with self.source_lock:
                    if self.cancelled.is_set():
                        source.close(); raise RuntimeError('macOS 捕获已取消')
                    self.sources.append(source)
            self.last_check = time.monotonic()
        except Exception:
            self.cancel(); raise

    @staticmethod
    def _window_dimensions(frame):
        value = frame.metadata.get('window_size_points')
        if not isinstance(value,dict):
            raise RuntimeError('窗口捕获缺少当前尺寸元数据')
        dimensions = tuple(value.get(key) for key in ('width','height'))
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v <= 0 for v in dimensions):
            raise RuntimeError('窗口尺寸元数据无效')
        return dimensions

    def configure_window(self, window_id, fps=30, *, include_system_audio=True, expected_pid=None):
        """Lock recording to one window; application audio follows its filter.

        The caller verifies process generation/path before and after capture;
        native per-frame checks additionally reject window ID/PID reuse and
        resizing. Frame size comes from the actual physical-pixel sample.
        """
        if self.sources:
            raise RuntimeError('捕获已启动，请重新创建捕获实例')
        if isinstance(window_id,bool) or not isinstance(window_id,int) or window_id <= 0:
            raise ValueError('Window ID must be a positive integer')
        if expected_pid is not None and (isinstance(expected_pid,bool) or not isinstance(expected_pid,int) or expected_pid <= 0):
            raise ValueError('Expected PID must be a positive integer')
        if isinstance(fps,bool) or not 0 < float(fps) <= 240:
            raise ValueError('Invalid capture frame rate')
        self._permission()
        self.window_id = window_id
        self.audio_enabled = bool(include_system_audio)
        self.audio_format = (48000,2) if self.audio_enabled else None
        self.audio_scope = 'application' if self.audio_enabled else None
        try:
            kwargs = {'kind':'window'}
            if self.audio_enabled:
                kwargs['system_audio'] = True
            source = self.source_factory(self.executable,window_id,round(float(fps)),self.transfer,**kwargs)
            with self.source_lock:
                if self.cancelled.is_set():
                    source.close(); raise RuntimeError('macOS 捕获已取消')
                self.sources.append(source)
            frame = source.latest()
            pid = frame.metadata.get('pid')
            if (frame.metadata.get('window_id') != window_id or isinstance(pid,bool) or not isinstance(pid,int)
                    or pid <= 0 or (expected_pid is not None and pid != expected_pid)):
                raise RuntimeError('窗口捕获进程身份不一致')
            self.window_pid = pid
            self.window_size = self._window_dimensions(frame)
            self.source_frame_size = (frame.width,frame.height)
            self.frame_size = (frame.width & ~1,frame.height & ~1)
            if min(self.frame_size) < 2 or self.frame_size[0]*self.frame_size[1] > 67108864:
                raise RuntimeError('窗口物理像素尺寸无效')
            self._validate_frame(frame)
            self.color_mode = 'HDR → SDR BT.709' if frame.metadata.get('hdr_verified') else 'SDR BT.709'
        except Exception:
            self.cancel(); raise

    def grab(self, bbox):
        if self.window_id is not None:
            frames = [self.sources[0].latest()]
            frame = frames[0]
            if (frame.metadata.get('window_id') != self.window_id or frame.metadata.get('pid') != self.window_pid):
                raise RuntimeError('录制窗口已退出或进程身份变化')
            if (frame.width,frame.height) != self.source_frame_size or self._window_dimensions(frame) != self.window_size:
                raise RuntimeError('录制窗口尺寸已变化，请重新录制')
            image = self._validate_frame(frame)
            rgb = frame.rgb if image.size == self.frame_size else image.crop((0,0,*self.frame_size)).tobytes()
        elif self.bbox is None:
            image = self.capture_rect(bbox)
            from .replay_capture import ReplayShot
            return ReplayShot(image.tobytes(), time.monotonic())
        else:
            if time.monotonic() - self.last_check >= 2 and self._metadata is None:
                value = display_metadata(runner=self.runner, executable=self.executable)
                rows = capture_display_info(self.bbox, value['displays'])['displays']
                if self._signature(rows) != self.signature:
                    raise RuntimeError('显示器布局、物理像素、刷新率或 HDR 状态已变化，请重新录制')
                self.last_check = time.monotonic()
            frames = [source.latest() for source in self.sources]
            images = [self._validate_frame(frame, row) for frame, row in zip(frames, self.rows, strict=True)]
            direct = (len(self.rows) == 1 and all(self.bbox[k] == self.rows[0][k] for k in ('left','top','width','height'))
                      and images[0].size == self.frame_size)
            rgb = frames[0].rgb if direct else composite_frames(self.bbox, self.rows, images, even=True).tobytes()
        key = tuple(frame.timestamp for frame in frames)
        source_time = max(key)
        if self.source_base is None:
            self.source_base, self.clock_base = source_time, time.monotonic()
        timestamp = self.clock_base + source_time - self.source_base
        if key == self.last_key:
            timestamp = time.monotonic()
        if self.last_timestamp is not None:
            timestamp = max(self.last_timestamp, timestamp)
        self.last_key, self.last_timestamp = key, timestamp
        from .replay_capture import ReplayShot
        return ReplayShot(rgb, timestamp)

    def read_audio(self):
        """Drain one PCM block, aligned to the same time origin as RGB frames.

        Blocks are retained until the first video timestamp is known; samples
        preceding that video are cropped. No callback arrival clock is used.
        """
        if not self.audio_enabled or self.source_base is None or not self.sources:
            return None
        while True:
            frame = self.sources[0].read_audio()
            if frame is None:
                return None
            if (frame.sample_rate, frame.channels) != self.audio_format:
                raise RuntimeError('系统音频采样格式发生变化，请重新录制')
            count = len(frame.pcm) // (frame.channels * 2)
            skip = min(count, max(0, math.ceil((self.source_base-frame.timestamp)*frame.sample_rate)))
            if skip == count:
                continue
            pts = frame.timestamp + skip / frame.sample_rate
            return AudioFrame(frame.pcm[skip*frame.channels*2:], self.clock_base + pts - self.source_base,
                              frame.sample_rate, frame.channels)

    def cancel(self):
        self.cancelled.set()
        with self.source_lock:
            sources = list(self.sources)
        errors = []
        for source in sources:
            try:
                source.close()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError('macOS 捕获组件未完全停止：' + str(errors[0])) from errors[0]

    def close(self):
        self.cancel()


def capture_display(display_id):
    with MacCapture() as capture:
        return capture.capture_display(display_id)


def capture_window(window_id):
    with MacCapture() as capture:
        return capture.capture_window(window_id)


def capture_all():
    with MacCapture() as capture:
        return capture.capture_all()

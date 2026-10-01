"""Screen capture used by the continuous replay buffer.

MSS 10 uses CAPTUREBLT on Windows to include additional layered windows.
At video frame rates that operation can make the physical cursor flicker:
https://github.com/BoboTiG/python-mss/issues/179
Keep the replay grabber on SRCCOPY, without changing one-shot screenshots or
mutating MSS module globals shared with other capture threads.
"""
import sys
from collections import deque
from dataclasses import dataclass
import subprocess
import threading
import time

import mss


class _ReplayGdi:
    def __init__(self, gdi):
        self._gdi = gdi

    def __getattr__(self, name):
        return getattr(self._gdi, name)

    def BitBlt(self, *args):
        return self._gdi.BitBlt(*args[:-1], args[-1] & ~0x40000000)


@dataclass(frozen=True)
class ReplayShot:
    rgb: bytes
    timestamp: float


class _HDRSource:
    """Drain FP16 capture continuously and retain only the newest source frame."""

    def __init__(self, plan, ffmpeg):
        from .replay_service import _subprocess_hidden_flags, _assign_process_to_job
        self.plan = plan
        self.condition = threading.Condition()
        self.closed = False
        self.latest = None
        self.timestamps = {}
        self.errors = deque(maxlen=6)
        self.failure = None
        self.converted = None
        self.first_source_time = None
        self.first_clock_time = None
        self.last_source_time = None
        self.last_delivered_time = None
        self.last_delivered_key = None
        self.proc = subprocess.Popen([ffmpeg, *plan.capture_args], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     **_subprocess_hidden_flags())
        _assign_process_to_job(self.proc)
        self.stats_thread = threading.Thread(target=self._read_stats, daemon=True, name='HDRCaptureTimestamps')
        self.frame_thread = threading.Thread(target=self._read_frames, daemon=True, name='HDRCaptureFrames')
        self.stats_thread.start()
        self.frame_thread.start()

    def _read_stats(self):
        from .hdr_capture import parse_capture_timestamp
        try:
            for line in self.proc.stderr:
                parsed = parse_capture_timestamp(line)
                with self.condition:
                    if parsed:
                        self.timestamps[parsed[0]] = parsed[1]
                    elif line.strip():
                        self.errors.append(line.decode('utf-8', 'replace').strip())
                    self.condition.notify_all()
                if self.closed:
                    break
        except (OSError, ValueError):
            pass
        finally:
            with self.condition:
                self.condition.notify_all()

    def _read_frames(self):
        index = 0
        try:
            while not self.closed:
                frame = bytearray()
                while len(frame) < self.plan.frame_bytes and not self.closed:
                    block = self.proc.stdout.read(self.plan.frame_bytes - len(frame))
                    if not block:
                        break
                    frame.extend(block)
                if len(frame) != self.plan.frame_bytes:
                    if not self.closed:
                        self.stats_thread.join(timeout=.5)
                        raise RuntimeError('HDR 捕获未返回完整浮点帧：' + ' · '.join(self.errors))
                    break
                with self.condition:
                    deadline = time.monotonic() + 3
                    while index not in self.timestamps and not self.closed:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise RuntimeError('HDR 捕获缺少原始帧时间戳，请更新 FFmpeg 组件')
                        self.condition.wait(min(.1, remaining))
                    if self.closed:
                        break
                    source_time = self.timestamps.pop(index)
                    if self.last_source_time is not None and source_time < self.last_source_time:
                        raise RuntimeError('HDR 捕获时间发生变化，请重新开启录制')
                    if self.first_source_time is None:
                        self.first_source_time = source_time
                        self.first_clock_time = time.monotonic()
                    timestamp = self.first_clock_time + source_time - self.first_source_time
                    self.last_source_time = source_time
                    # A repeated DXGI frame retains its source PTS. Reuse its
                    # conversion; presentation time is supplied by grab().
                    self.latest = (source_time, bytes(frame), timestamp)
                    self.condition.notify_all()
                index += 1
        except Exception as exc:
            with self.condition:
                self.failure = str(exc)
                self.condition.notify_all()

    def grab(self):
        with self.condition:
            deadline = time.monotonic() + 10
            while self.latest is None and not self.failure and not self.closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError('HDR 屏幕捕获启动超时，请选择其他屏幕重试')
                self.condition.wait(min(.1, remaining))
            if self.failure:
                raise RuntimeError(self.failure)
            if self.closed:
                raise RuntimeError('HDR 屏幕捕获已停止')
            key, frame, timestamp = self.latest
        if self.converted is None or self.converted[0] != key:
            self.converted = (key, self.plan.convert_frame(frame))
        if key == self.last_delivered_key:
            timestamp = time.monotonic()
        if self.last_delivered_time is not None:
            timestamp = max(timestamp, self.last_delivered_time)
        self.last_delivered_key, self.last_delivered_time = key, timestamp
        return ReplayShot(self.converted[1], timestamp)

    def close(self):
        with self.condition:
            if self.closed:
                return
            self.closed = True
            self.condition.notify_all()
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=1)
        for thread in (self.frame_thread, self.stats_thread):
            thread.join(timeout=1)
        self.proc.stdout.close()
        self.proc.stderr.close()


class ReplayCapture:
    """Use GDI for SDR; HDR requires untouched DXGI FP16 plus tone mapping."""

    capture_method = 'GDI'
    color_mode = 'SDR'
    encoder_filter = ('scale=in_range=pc:out_range=pc,format=gbrp,colorspace=ispace=gbr:iprimaries=bt709:'
                      'itrc=iec61966-2-1:irange=pc:space=bt709:primaries=bt709:'
                      'trc=bt709:range=tv:format=yuv420p')
    output_color_args = ('-color_primaries', 'bt709', '-color_trc', 'bt709',
                         '-colorspace', 'bt709', '-color_range', 'tv')

    def __init__(self, capture):
        self.capture = capture
        self.hdr_source = None
        self.bbox = None
        self.display_signature = None
        self.last_display_check = 0
        self.cancelled = threading.Event()
        self.source_lock = threading.Lock()

    def __getattr__(self, name):
        return getattr(self.capture, name)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    @staticmethod
    def _signature(info):
        return tuple((row.get('device_name'), row.get('left'), row.get('top'),
                      row.get('width'), row.get('height'), row.get('hdr_enabled'),
                      row.get('sdr_white_nits'), row.get('refresh_hz')) for row in info['displays'])

    @staticmethod
    def _display_info(bbox):
        from .display_info import enumerate_displays, capture_display_info
        from .hdr_capture import resolve_display_color
        displays = enumerate_displays()
        touched = capture_display_info(bbox, displays)['displays']
        completed = [resolve_display_color(row, bbox) if row.get('hdr_enabled') is None else row
                     for row in touched]
        return capture_display_info(bbox, completed)

    def configure(self, bbox, fps=30):
        from .hdr_capture import build_hdr_capture_plan
        if self.cancelled.is_set():
            raise RuntimeError('屏幕捕获已取消')
        info = self._display_info(bbox)
        if not info['recording_enabled']:
            raise RuntimeError(info['recording_reason'])
        if info['hdr_enabled'] is None:
            raise RuntimeError('无法确认录制屏幕的 HDR 状态，请检查显示设置后重试')
        self.bbox = dict(bbox)
        self.display_signature = self._signature(info)
        self.last_display_check = time.monotonic()
        if info['hdr_enabled']:
            if len(info['displays']) != 1:
                raise RuntimeError('HDR 跨屏录制暂不支持，请选择单个屏幕录制')
            from .replay_service import get_ffmpeg_path
            plan = build_hdr_capture_plan(bbox, info['displays'][0], fps)
            source = _HDRSource(plan, get_ffmpeg_path())
            with self.source_lock:
                cancelled = self.cancelled.is_set()
                if not cancelled:
                    self.hdr_source = source
            if cancelled:
                source.close()
                raise RuntimeError('屏幕捕获已取消')
            self.capture_method = 'DXGI FP16'
            self.color_mode = 'HDR → SDR BT.709'
            self.encoder_filter = plan.rgb24_encode_filter
            self.output_color_args = plan.output_color_args

    def grab(self, bbox):
        if self.bbox is not None and time.monotonic() - self.last_display_check >= 2:
            info = self._display_info(self.bbox)
            if self._signature(info) != self.display_signature:
                raise RuntimeError('显示器布局、刷新率或 HDR 状态已改变，请重新开启录制')
            self.last_display_check = time.monotonic()
        return self.hdr_source.grab() if self.hdr_source else self.capture.grab(bbox)

    def close(self):
        self.cancel()
        self.hdr_source = None
        self.capture.close()

    def cancel(self):
        # Stop can run on the UI thread. Only cancel the independent HDR
        # process here; MSS owns thread-local GDI handles and closes on exit.
        self.cancelled.set()
        with self.source_lock:
            source = self.hdr_source
        if source:
            source.close()


def create_replay_capture():
    capture = mss.mss()
    if sys.platform == 'win32':
        capture.gdi32 = _ReplayGdi(capture.gdi32)
        return ReplayCapture(capture)
    return capture

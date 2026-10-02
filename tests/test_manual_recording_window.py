"""Window-locked Mac recording with synthetic metadata, frames and FFmpeg input.

No desktop, microphone, controller or application window is accessed here.
"""
from dataclasses import replace
from types import SimpleNamespace
import subprocess

import pytest

from gamepadstudio import macos_capture, macos_permissions, manual_recording, screenshot_service
from gamepadstudio.macos_windows import WindowInfo


POINT_BOUNDS = dict(left=-100, top=20, width=8, height=8)
WINDOW = WindowInfo(4701, 9123, 'Synthetic Game', 'Synthetic Game',
                    '/Applications/Synthetic Game.app/Contents/MacOS/Game', 'Game',
                    'test.synthetic.game', (1000, 5), POINT_BOUNDS)
OTHER = replace(WINDOW, window_id=4702, pid=9124, title='Other Application', generation=(1001, 6),
                process_path='/Applications/Other.app/Contents/MacOS/Other')


class WindowBackend:
    def __init__(self):
        self.front = self.current = WINDOW
        self.smart_calls = self.foreground_calls = self.verify_calls = 0

    def foreground_window(self):
        self.foreground_calls += 1
        return self.front

    def smart_window(self):
        self.smart_calls += 1
        return self.front

    def verify(self, expected):
        self.verify_calls += 1
        if self.current is None or not expected.generation or not expected.process_path \
                or self.current.identity != expected.identity:
            raise RuntimeError('录制窗口已退出或进程身份变化')
        return self.current


class RouteSource:
    """Stop after configuration, before launching an encoder."""
    monitors = [dict(left=-100, top=0, width=24, height=16),
                dict(left=-100, top=0, width=8, height=8, display_id=11),
                dict(left=-92, top=0, width=16, height=16, display_id=12)]
    frame_size = (16, 16)
    capture_method = 'synthetic ScreenCaptureKit'
    color_mode = 'SDR BT.709'

    def __init__(self):
        self.recorder = None
        self.window_args = self.display_args = None
        self.cancelled = self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def configure_window(self, window_id, fps, **kwargs):
        self.window_args = (window_id, fps, kwargs)
        self.recorder._stop.set()

    def configure(self, bbox, fps, **kwargs):
        self.display_args = (dict(bbox), fps, kwargs)
        self.recorder._stop.set()

    def cancel(self):
        self.cancelled = True


@pytest.fixture
def route(monkeypatch, tmp_path):
    backend = WindowBackend()
    source = RouteSource()
    monitor_modes = []
    monkeypatch.setattr(manual_recording, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(manual_recording, 'get_ffmpeg_path', lambda: '/synthetic/unused/ffmpeg')
    monkeypatch.setattr(manual_recording, 'detect_hardware_encoder', lambda _: 'libx264')
    monkeypatch.setattr(manual_recording, 'create_replay_capture', lambda: source)
    monkeypatch.setattr(manual_recording, 'smart_foreground_info', lambda: (WINDOW.window_id, WINDOW.title))
    monkeypatch.setattr(screenshot_service, 'get_mac_window_backend', lambda: backend)
    monkeypatch.setattr(macos_permissions, 'screen_capture_permission_status', lambda: {'granted': True})

    def monitor_bbox(_source, mode):
        monitor_modes.append(mode)
        return source.monitors[0] if mode == 'all' else source.monitors[2]

    monkeypatch.setattr(manual_recording, 'get_target_monitor_bbox', monitor_bbox)

    def create(mode):
        recorder = manual_recording.ManualRecording(tmp_path, capture_mode=mode)
        source.recorder = recorder
        assert recorder.start()
        recorder._worker.join(timeout=5)
        assert not recorder._worker.is_alive()
        assert recorder.status()['phase'] == 'idle'
        assert source.closed and source.cancelled
        return recorder

    return create, source, backend, monitor_modes


def test_window_mode_locks_one_verified_window(route):
    create, source, backend, monitor_modes = route
    create('window')
    assert source.window_args == (WINDOW.window_id, 30,
                                  {'include_system_audio': True, 'expected_pid': WINDOW.pid})
    assert source.display_args is None
    assert backend.verify_calls == 2
    assert (backend.smart_calls, backend.foreground_calls) == (1, 0)
    assert monitor_modes == ['window']


@pytest.mark.parametrize('mode,expected', [
    ('game', RouteSource.monitors[2]),
    ('smart', RouteSource.monitors[2]),
    ('monitor', RouteSource.monitors[2]),
    ('monitor_1', RouteSource.monitors[1]),
    ('all', RouteSource.monitors[0]),
])
def test_display_modes_keep_display_capture(route, mode, expected):
    create, source, backend, monitor_modes = route
    if mode == 'game':
        backend.front = None  # A display recording does not require a target window.
    create(mode)
    assert source.window_args is None
    assert source.display_args == (expected, 30, {})
    assert backend.verify_calls == backend.smart_calls == backend.foreground_calls == 0
    assert monitor_modes == ([] if mode == 'monitor_1' else [mode])


def native_frame(*, window_id=WINDOW.window_id, pid=WINDOW.pid, pixels=(16, 16), points=(8, 8), timestamp=1.0):
    width, height = pixels
    return macos_capture.NativeFrame(bytes((42, 100, 180)) * (width * height), width, height, timestamp,
                                     dict(type='video', window_id=window_id, pid=pid,
                                          window_size_points={'width': points[0], 'height': points[1]},
                                          transfer='bt709', pixel_format='rgb24',
                                          color_primaries='bt709', color_managed=True,
                                          hdr_capture=False, hdr_verified=False))


class NativeWindowStream:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.closed = False

    def latest(self):
        return next(self.frames)

    def read_audio(self):
        return None

    def close(self):
        self.closed = True


def native_capture(frames):
    display = dict(left=-100, top=0, width=8, height=8, scale=2, pixel_width=16, pixel_height=16,
                   display_id=11, refresh_hz=60, hdr_enabled=False)
    stream = NativeWindowStream(frames)
    calls = []

    def factory(executable, identity, fps, transfer, **kwargs):
        calls.append((executable, identity, fps, transfer, kwargs))
        return stream

    capture = macos_capture.MacCapture(metadata={'displays': [display]}, executable='/synthetic/MacCapture',
                                       source_factory=factory, permission_check=lambda: {'granted': True},
                                       transfer='bt709')
    return capture, stream, calls


def test_native_window_capture_uses_exact_id_pid_and_physical_frame_size():
    capture, stream, calls = native_capture([native_frame(), native_frame(timestamp=1.1)])
    with capture:
        capture.configure_window(WINDOW.window_id, 30, expected_pid=WINDOW.pid)
        assert calls == [('/synthetic/MacCapture', WINDOW.window_id, 30, 'bt709',
                          {'kind': 'window', 'system_audio': True})]
        assert capture.frame_size == (16, 16)  # 8×8 CG points, 16×16 encoded pixels.
        assert capture.window_pid == WINDOW.pid and capture.audio_scope == 'application'
        assert capture.grab(POINT_BOUNDS).rgb == bytes((42, 100, 180)) * (16 * 16)
    assert stream.closed


@pytest.mark.parametrize('change', [
    {'window_id': OTHER.window_id}, {'pid': OTHER.pid},
    {'pixels': (18, 16)}, {'points': (9, 8)},
])
def test_native_window_capture_rejects_reuse_or_resize(change):
    capture, stream, _ = native_capture([native_frame(), native_frame(**change)])
    with capture:
        capture.configure_window(WINDOW.window_id, expected_pid=WINDOW.pid)
        with pytest.raises(RuntimeError, match='身份变化|尺寸已变化'):
            capture.grab(POINT_BOUNDS)
    assert stream.closed


@pytest.mark.parametrize('change', [
    {'window_id': OTHER.window_id}, {'pid': OTHER.pid},
])
def test_native_window_capture_rejects_wrong_initial_identity(change):
    capture, stream, _ = native_capture([native_frame(**change)])
    with pytest.raises(RuntimeError, match='身份不一致'):
        capture.configure_window(WINDOW.window_id, expected_pid=WINDOW.pid)
    assert stream.closed


class RecordingWindowSource(RouteSource):
    def __init__(self, backend, on_first=None):
        super().__init__()
        self.backend, self.on_first = backend, on_first
        self.stamps = iter((10.0, 10.05, 10.1))
        self.grabs = 0

    def configure_window(self, window_id, fps, **kwargs):
        self.window_args = (window_id, fps, kwargs)
        self.audio_enabled = False

    def grab(self, _bbox):
        try:
            stamp = next(self.stamps)
        except StopIteration:
            self.recorder._stop.set()
            return SimpleNamespace(rgb=b'', timestamp=10.2)
        self.grabs += 1
        if self.grabs == 1 and self.on_first:
            self.on_first(self.backend)
        return SimpleNamespace(rgb=bytes((42, 100, 180)) * (16 * 16), timestamp=stamp)


@pytest.fixture
def recording_window(monkeypatch, tmp_path):
    ffmpeg = manual_recording.get_ffmpeg_path()
    if not ffmpeg:
        pytest.skip('FFmpeg unavailable for synthetic encode')
    backend = WindowBackend()
    monkeypatch.setattr(manual_recording, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(manual_recording, 'detect_hardware_encoder', lambda _: 'libx264')
    monkeypatch.setattr(screenshot_service, 'get_mac_window_backend', lambda: backend)
    monkeypatch.setattr(manual_recording, 'get_target_monitor_bbox', lambda source, mode: source.monitors[1])
    monkeypatch.setattr(macos_permissions, 'screen_capture_permission_status', lambda: {'granted': True})
    sources = []

    def make_source(on_first=None):
        source = RecordingWindowSource(backend, on_first)
        sources.append(source)
        monkeypatch.setattr(manual_recording, 'create_replay_capture', lambda: source)
        recorder = manual_recording.ManualRecording(tmp_path, capture_mode='window', codec='h264')
        source.recorder = recorder
        return recorder, source, backend, ffmpeg

    yield make_source
    for source in sources:
        if source.recorder.status()['running']:
            source.recorder.stop()


def finish(recorder):
    assert recorder.start()
    recorder._worker.join(timeout=10)
    assert not recorder._worker.is_alive(), recorder.status()
    return recorder.status()


def test_recording_stays_on_original_window_after_focus_changes(recording_window):
    recorder, source, backend, ffmpeg = recording_window(lambda backend: setattr(backend, 'front', OTHER))
    status = finish(recorder)
    assert status['phase'] == 'idle' and status['frames'] == 3 and status['path']
    assert source.window_args[0] == WINDOW.window_id and source.window_args[2]['expected_pid'] == WINDOW.pid
    assert backend.smart_calls == 1 and backend.verify_calls >= 2
    decoded = subprocess.run([ffmpeg, '-v', 'error', '-i', status['path'], '-frames:v', '1',
                              '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'],
                             capture_output=True, timeout=10)
    assert decoded.returncode == 0 and len(decoded.stdout) == 16 * 16 * 3


@pytest.mark.parametrize('change', [
    {'pid': OTHER.pid}, {'generation': OTHER.generation},
    {'process_path': OTHER.process_path},
])
def test_recording_stops_without_saving_when_process_identity_changes(recording_window, monkeypatch, change):
    recorder, source, backend, _ = recording_window(
        lambda backend: setattr(backend, 'current', replace(WINDOW, **change)))
    clock = iter(0.6 * index for index in range(100))
    monkeypatch.setattr(manual_recording, 'time', SimpleNamespace(monotonic=lambda: next(clock)))
    status = finish(recorder)
    assert status['phase'] == 'failed' and status['path'] == ''
    assert '身份变化' in status['last_error'] and source.grabs == 1
    assert not list(recorder.save_dir.iterdir())

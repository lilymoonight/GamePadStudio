from pathlib import Path
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from gamepadstudio.replay_service import (
    calculate_estimated_ram_gb,
    detect_hardware_encoder,
    ReplayBufferEngine,
    get_ffmpeg_path
)
from gamepadstudio.studio_core import ConfigStore
from gamepadstudio import replay_service
from gamepadstudio.replay_timing import TimestampedRGBWriter

HOST_FFMPEG = get_ffmpeg_path()


@pytest.fixture(autouse=True)
def portable_encoder_policy(monkeypatch):
    # Exercise the common encoder/panorama policy with fake sources. Native
    # macOS consent and single-display limits have separate focused tests.
    import sys
    monkeypatch.setattr(replay_service, 'sys', SimpleNamespace(platform='linux'))


def test_calculate_estimated_ram_gb():
    assert calculate_estimated_ram_gb(5, 50) == 1.75 or calculate_estimated_ram_gb(5, 50) == 1.88 or 1.5 < calculate_estimated_ram_gb(5, 50) < 2.0
    assert calculate_estimated_ram_gb(10, 50) > calculate_estimated_ram_gb(5, 50)
    assert calculate_estimated_ram_gb(1, 35) > 0


def test_detect_hardware_encoder(monkeypatch):
    ffmpeg = HOST_FFMPEG
    if ffmpeg:
        monkeypatch.setattr(replay_service, 'get_ffmpeg_path', lambda: ffmpeg)
        hevc_enc = detect_hardware_encoder('hevc')
        assert hevc_enc in ('hevc_amf', 'hevc_nvenc', 'hevc_qsv', 'hevc_mf', 'libx265')
        av1_enc = detect_hardware_encoder('av1')
        assert av1_enc in ('av1_amf', 'av1_nvenc', 'av1_qsv', 'libaom-av1')


def test_replay_config_defaults_and_clamping(tmp_path):
    store = ConfigStore(tmp_path)
    assert 'replay_buffer_enabled' in store.data
    assert store.data['replay_buffer_enabled'] is False
    assert store.data['replay_buffer_minutes'] == 5
    assert store.data['replay_codec'] == 'hevc'

    # Test clamping
    store.data['replay_buffer_minutes'] = 999
    store.save()
    reloaded = ConfigStore(tmp_path)
    assert reloaded.data['replay_buffer_minutes'] == 10

    store.data['replay_buffer_minutes'] = 0
    store.save()
    reloaded = ConfigStore(tmp_path)
    assert reloaded.data['replay_buffer_minutes'] == 1


def test_replay_engine_lifecycle(tmp_path):
    engine = ReplayBufferEngine(save_dir=tmp_path, minutes=2, codec='hevc', bitrate_mbps=20, capture_mode='game')
    status = engine.get_status()
    assert status['enabled'] is False
    assert status['minutes'] == 2
    assert status['codec'] == 'hevc'
    assert status['capture_mode'] == 'game'
    assert status['estimated_max_gb'] > 0
    assert status['is_pure_ram'] is True
    assert engine.is_pure_ram is True
    # Replay cache must NOT pollute user's album save_dir with files
    assert engine.cache_dir != engine.save_dir
    assert not (tmp_path / ".replay_cache").exists()
    assert engine._total_bytes == 0


def test_align_ts_stream():
    from gamepadstudio.replay_service import _align_ts_stream
    # Garbage bytes before valid TS packet starting with 0x47
    fake_packet_1 = b'\x47' + b'\x01' * 187
    fake_packet_2 = b'\x47' + b'\x02' * 187
    stream = b'junk_bytes_header' + fake_packet_1 + fake_packet_2
    aligned = _align_ts_stream(stream)
    assert aligned.startswith(b'\x47')
    assert len(aligned) == 188 * 2


def test_replay_buffer_zero_disk_writes(tmp_path):
    engine = ReplayBufferEngine(save_dir=tmp_path, minutes=1, codec='hevc', bitrate_mbps=10)
    # Simulate feeding chunks into RAM buffer
    dummy_ts = b'\x47' + b'\x00' * 187
    for _ in range(2000):
        engine._ram_chunks.append((1000.0, dummy_ts))
        engine._total_bytes += len(dummy_ts)

    status = engine.get_status()
    assert status['chunks_count'] == 2000
    assert status['current_size_mb'] > 0
    assert status['is_pure_ram'] is True
    # Confirm zero files written to disk in tmp_path
    assert list(tmp_path.iterdir()) == []


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def read(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeCapture:
    monitors = [dict(left=0, top=0, width=4, height=2),
                dict(left=0, top=0, width=2, height=2),
                dict(left=2, top=0, width=2, height=2)]

    def __init__(self, clock, delay=.2, timestamps=None):
        self.clock, self.delay, self.timestamps = clock, delay, timestamps
        self.index = 0
        self.configure = Mock()
        self.capture_method, self.color_mode = 'fake', 'SDR'

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def grab(self, bbox):
        self.clock.now += self.delay
        frame = SimpleNamespace(rgb=b'\0' * (bbox['width'] * bbox['height'] * 3))
        if self.timestamps is not None:
            frame.timestamp = self.timestamps[self.index]
        self.index += 1
        return frame


def run_fake_capture(monkeypatch, engine, *, delay=.2, backpressure=.3, timestamps=None, frames=3):
    clock = FakeClock()
    capture = FakeCapture(clock, delay=delay, timestamps=timestamps)
    monkeypatch.setattr(replay_service.time, 'monotonic', clock.read)
    monkeypatch.setattr(replay_service.time, 'perf_counter', clock.read)
    monkeypatch.setattr(replay_service.time, 'sleep', clock.sleep)
    monkeypatch.setattr(replay_service, '_ensure_dpi_awareness', lambda: None)
    monkeypatch.setattr(replay_service, 'get_ffmpeg_path', lambda: 'fake-ffmpeg')
    monkeypatch.setattr(replay_service, 'detect_hardware_encoder', lambda codec: 'libx264')
    monkeypatch.setattr(replay_service, '_assign_process_to_job', lambda proc: None)
    monkeypatch.setattr(replay_service, 'create_replay_capture', lambda: capture)
    monkeypatch.setattr(replay_service, 'get_target_monitor_bbox', lambda sct, mode: sct.monitors[1])
    recorded_pts = []

    class SpyWriter(TimestampedRGBWriter):
        def write_frame(self, rgb, timestamp):
            result = super().write_frame(rgb, timestamp)
            recorded_pts.append(result)
            if len(recorded_pts) >= frames:
                engine.running = False
            return result

    class SlowPipe(io.BytesIO):
        def write(self, data):
            if len(data) == 12:  # The actual 2x2 RGB frame, not container headers.
                clock.now += backpressure
            return super().write(data)

        def close(self):
            self.saved = self.getvalue()
            super().close()

    pipe = SlowPipe()
    process = SimpleNamespace(stdin=pipe, stdout=io.BytesIO(), stderr=io.BytesIO(),
                              poll=lambda: None, wait=lambda timeout: 0, kill=lambda: None)
    popen = Mock(return_value=process)
    monkeypatch.setattr(replay_service.subprocess, 'Popen', popen)
    monkeypatch.setattr(replay_service, 'TimestampedRGBWriter', SpyWriter)
    engine.running = True
    engine._capture_and_encode_loop()
    for attr in ('_reader_thread', '_stderr_thread'):
        thread = getattr(engine, attr)
        if thread:
            thread.join(timeout=1)
    engine._ffmpeg_proc = None  # No real process needs the atexit hook.
    engine._worker_thread = None
    return recorded_pts, capture, popen


def test_loop_slow_mss_and_write_backpressure_keep_wallclock_time(tmp_path, monkeypatch):
    engine = ReplayBufferEngine(tmp_path, fps=30)
    pts, capture, popen = run_fake_capture(monkeypatch, engine)
    assert pts == [0, .5, 1.0]
    assert engine.get_status()['captured_fps'] == 2.0
    assert engine.get_status()['last_error'] == ''
    capture.configure.assert_called_once_with(capture.monitors[1], 30)
    command = popen.call_args.args[0]
    assert command[command.index('-f') + 1] == 'matroska'
    assert '-r' not in command
    assert command[command.index('-fps_mode') + 1] == 'passthrough'


def test_loop_native_timestamps_survive_pipe_latency_and_skip_duplicate_frames(tmp_path, monkeypatch):
    engine = ReplayBufferEngine(tmp_path, fps=30)
    pts, capture, _ = run_fake_capture(monkeypatch, engine, delay=.05, backpressure=.4,
                                       timestamps=[10, 10, 10.6, 11.1], frames=3)
    assert pts == [0, .6, 1.1]
    assert capture.index == 4
    assert engine._captured_frames == 3


def display_rows(rates=(60, 60)):
    return [dict(monitor, refresh_hz=rate, hdr_enabled=False, device_name=f'display{index}')
            for index, (monitor, rate) in enumerate(zip(FakeCapture.monitors[1:], rates))]


@pytest.mark.parametrize('rows', [[], display_rows((60, 59.94)), display_rows((60, None)), display_rows()[:1]])
def test_all_start_rejects_mixed_or_unverified_actual_monitors(tmp_path, monkeypatch, rows):
    events = []
    engine = ReplayBufferEngine(tmp_path, capture_mode='all', on_event=events.append)
    monkeypatch.setattr(replay_service, 'create_replay_capture', lambda: FakeCapture(FakeClock()))
    monkeypatch.setattr(replay_service, 'enumerate_displays', lambda: rows)
    popen = Mock(side_effect=AssertionError('An unsupported panorama spawned an encoder'))
    monkeypatch.setattr(replay_service.subprocess, 'Popen', popen)
    assert engine.start() is False
    assert engine.get_status()['running'] is False
    assert '选择单个屏幕' in engine.get_status()['last_error']
    assert events
    popen.assert_not_called()


def test_all_stops_when_display_refresh_rates_change(tmp_path, monkeypatch):
    engine = ReplayBufferEngine(tmp_path, capture_mode='all')
    display_probe = Mock(side_effect=[display_rows(), display_rows((60, 59.94))])
    monkeypatch.setattr(replay_service, 'enumerate_displays', display_probe)
    pts, _, _ = run_fake_capture(monkeypatch, engine, delay=.6, backpressure=0, frames=10)
    assert pts == [0, .6]
    assert '刷新率不同' in engine.get_status()['last_error']
    assert engine.get_status()['running'] is False


def test_explicit_missing_monitor_never_falls_back_to_another_screen(tmp_path, monkeypatch):
    engine = ReplayBufferEngine(tmp_path, capture_mode='monitor_3')
    pts, _, popen = run_fake_capture(monkeypatch, engine)
    assert pts == []
    assert '显示器已断开' in engine.get_status()['last_error']
    popen.assert_not_called()


def ts_frame_pts(seconds):
    pts = round(seconds * 90_000)
    stamp = bytes((0x21 | ((pts >> 30) & 7) << 1, (pts >> 22) & 255,
                   1 | ((pts >> 15) & 127) << 1, (pts >> 7) & 255, 1 | (pts & 127) << 1))
    pes = b'\0\0\1\xe0\0\0\x80\x80\x05' + stamp
    return b'\x47\x41\x00\x10' + pes + b'\xff' * (184 - len(pes))


def test_buffer_retention_follows_media_pts_not_delivery_time(tmp_path, monkeypatch):
    engine = ReplayBufferEngine(tmp_path, minutes=1)
    chunks = [ts_frame_pts(100 + index) for index in range(70)]
    source = SimpleNamespace(read1=Mock(side_effect=chunks + [b'']))
    # No wallclock query is needed to interpret encoded video duration.
    monkeypatch.setattr(replay_service.time, 'monotonic', Mock(side_effect=AssertionError('Arrival time used')))
    engine._stdout_reader_loop(SimpleNamespace(stdout=source))
    assert engine._ram_chunks[0][0] == 6
    assert engine._ram_chunks[-1][0] == 69
    assert engine.get_status()['buffered_seconds'] == 60
    assert engine._total_bytes == 64 * 188
    assert list(tmp_path.iterdir()) == []


def test_single_stdout_batch_reports_its_full_media_duration(tmp_path):
    engine = ReplayBufferEngine(tmp_path)
    source = SimpleNamespace(read1=Mock(side_effect=[ts_frame_pts(50) + ts_frame_pts(55), b'']))
    engine._stdout_reader_loop(SimpleNamespace(stdout=source))
    assert engine.get_status()['buffered_seconds'] == 5


def test_stop_terminates_encoder_before_closing_a_blocked_stdin(tmp_path):
    engine = ReplayBufferEngine(tmp_path)
    order = []
    engine.running = True
    engine._ffmpeg_proc = SimpleNamespace(
        stdin=SimpleNamespace(close=lambda: order.append('close')),
        terminate=lambda: order.append('terminate'), wait=lambda timeout: None,
    )
    engine.stop()
    assert order == ['terminate', 'close']


def test_save_short_replay_reports_actual_movie_duration_not_buffer_budget(tmp_path, monkeypatch):
    import random
    import subprocess
    from gamepadstudio.replay_timing import mp4_duration_seconds
    ffmpeg = HOST_FFMPEG
    if not ffmpeg:
        pytest.skip('FFmpeg unavailable')
    monkeypatch.setattr(replay_service, 'get_ffmpeg_path', lambda: ffmpeg)
    stream = io.BytesIO()
    writer = TimestampedRGBWriter(stream, 64, 64, 30)
    pixels = random.Random(7)
    for index in range(6):
        writer.write_frame(pixels.randbytes(64 * 64 * 3), index * .2)
    encoded = subprocess.run(
        [ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'matroska', '-i', 'pipe:0',
         '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
         '-fps_mode', 'passthrough', '-enc_time_base', '1:1000', '-f', 'mpegts', 'pipe:1'],
        input=stream.getvalue(), capture_output=True, timeout=15,
        **replay_service._subprocess_hidden_flags(),
    )
    assert encoded.returncode == 0, encoded.stderr.decode(errors='replace')
    engine = ReplayBufferEngine(tmp_path, minutes=5, codec='h264')
    engine._current_width = engine._current_height = 64
    engine._ram_chunks.append((1.0, encoded.stdout))
    engine._ram_chunk_spans.append((0.0, 1.0))
    engine._total_bytes = len(encoded.stdout)
    saved = engine.save_replay('time-test')
    assert saved is not None
    path = Path(saved)
    actual = mp4_duration_seconds(path)
    # MPEG-TS infers the final sample's duration from the observed 5 FPS;
    # the preceding capture PTS remain 0,.2,.4,.6,.8,1.0.
    assert actual == pytest.approx(1.2, abs=.002)
    metadata = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
    assert metadata['duration_seconds'] == pytest.approx(actual, abs=.001)
    assert metadata['duration_minutes'] == pytest.approx(actual / 60, abs=.0001)
    assert metadata['buffer_minutes'] == 5


def test_real_encoded_ring_exports_recent_minute_on_media_timeline(tmp_path, monkeypatch):
    import random
    import subprocess
    from gamepadstudio.replay_timing import mp4_duration_seconds
    ffmpeg = HOST_FFMPEG
    if not ffmpeg:
        pytest.skip('FFmpeg unavailable')
    monkeypatch.setattr(replay_service, 'get_ffmpeg_path', lambda: ffmpeg)
    engine = ReplayBufferEngine(tmp_path, minutes=1, codec='h264', bitrate_mbps=10)
    engine._current_width = engine._current_height = 64
    stream = io.BytesIO()
    writer = TimestampedRGBWriter(stream, 64, 64, 30)
    pixels = random.Random(11)
    # More than one minute of real PTS at five captured FPS, generated without
    # waiting. GOP boundaries must follow two elapsed seconds, not 60 frames.
    for index in range(330):
        writer.write_frame(pixels.randbytes(64 * 64 * 3), index * .2)
    encoded = subprocess.run(engine._encoder_command(ffmpeg, 'libx264', SimpleNamespace()),
                             input=stream.getvalue(), capture_output=True, timeout=20,
                             **replay_service._subprocess_hidden_flags())
    assert encoded.returncode == 0, encoded.stderr.decode(errors='replace')
    engine._stdout_reader_loop(SimpleNamespace(stdout=io.BytesIO(encoded.stdout)))
    assert engine.get_status()['buffered_seconds'] == 60
    assert engine._ram_chunks[0][0] > 0  # Old encoded media was actually evicted.
    output = engine.save_replay('minute-window')
    assert output is not None
    duration = mp4_duration_seconds(output)
    # Stream copy begins at a keyframe, with at most a two-second lead-in loss.
    assert 57.9 <= duration <= 60.2
    metadata = json.loads(Path(output).with_suffix('.json').read_text(encoding='utf-8'))
    assert metadata['duration_seconds'] == pytest.approx(duration, abs=.001)


@pytest.mark.parametrize('phase,cancellable', [('configure', True), ('grab', True),
                                              ('write', True), ('write', False)])
def test_stop_wakes_capture_waits_and_blocked_encoder_without_closing_native_source_on_caller(
        tmp_path, monkeypatch, phase, cancellable):
    import threading
    engine = ReplayBufferEngine(tmp_path)
    entered, released, terminated, stopped = (threading.Event() for _ in range(4))
    order, native_close_threads = [], []
    cancel_calls = []

    class Source:
        monitors = FakeCapture.monitors

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

        def close(self):
            native_close_threads.append(threading.get_ident())

        def wait(self):
            entered.set()
            assert released.wait(3), 'Capture cancellation did not wake the source'

        def configure(self, bbox, fps):
            if phase == 'configure':
                self.wait()

        def grab(self, bbox):
            if phase == 'grab':
                self.wait()
            return SimpleNamespace(rgb=b'\0' * 12)

    source = Source()
    if cancellable:
        def cancel():
            assert engine.running is False
            order.append('cancel')
            cancel_calls.append(True)
            released.set()
        source.cancel = cancel

    class Pipe:
        def write(self, data):
            if phase == 'write' and len(data) == 12:
                entered.set()
                assert terminated.wait(3), 'Encoder backpressure blocked stop'
                raise BrokenPipeError('terminated')
            return len(data)

        def flush(self):
            pass

        def close(self):
            order.append('stdin-close')
            if phase == 'write':
                assert terminated.is_set(), 'stdin closed before releasing a blocked writer'

    def terminate():
        order.append('terminate')
        terminated.set()

    proc = SimpleNamespace(stdin=Pipe(), stdout=io.BytesIO(), stderr=io.BytesIO(),
                           poll=lambda: 0 if terminated.is_set() else None,
                           wait=lambda timeout: 0, terminate=terminate, kill=terminate)
    monkeypatch.setattr(replay_service, '_ensure_dpi_awareness', lambda: None)
    monkeypatch.setattr(replay_service, 'get_ffmpeg_path', lambda: 'fake-ffmpeg')
    monkeypatch.setattr(replay_service, 'detect_hardware_encoder', lambda codec: 'libx264')
    monkeypatch.setattr(replay_service, '_assign_process_to_job', lambda proc: None)
    monkeypatch.setattr(replay_service, 'create_replay_capture', lambda: source)
    monkeypatch.setattr(replay_service, 'get_target_monitor_bbox', lambda sct, mode: sct.monitors[1])
    monkeypatch.setattr(replay_service.subprocess, 'Popen', Mock(return_value=proc))
    engine.running = True
    worker = threading.Thread(target=engine._capture_and_encode_loop, daemon=True)
    engine._worker_thread = worker

    def stop_recording():
        engine.stop()
        stopped.set()

    stopper = threading.Thread(target=stop_recording, daemon=True)
    try:
        worker.start()
        assert entered.wait(1), 'Worker never reached the tested wait'
        assert engine._capture_source is source
        stopper.start()
        assert stopped.wait(1), 'Stopping waited for capture/pipe timeouts'
        assert not worker.is_alive()
        assert engine._capture_source is None
        assert native_close_threads == [worker.ident]
        assert cancel_calls == ([True] if cancellable else [])
        if phase == 'write':
            assert order.index('terminate') < order.index('stdin-close')
        assert engine.get_status()['last_error'] == ''  # Cancellation is normal shutdown.
    finally:
        released.set()
        terminated.set()
        worker.join(timeout=3)
        if stopper.ident is not None:
            stopper.join(timeout=3)
        engine.stop()


def test_restart_refuses_to_overlap_a_worker_that_has_not_finished_stopping(tmp_path, monkeypatch):
    engine = ReplayBufferEngine(tmp_path)
    old_worker = Mock()
    old_worker.is_alive.return_value = True
    engine._worker_thread = old_worker
    new_thread = Mock(side_effect=AssertionError('A second capture session was started'))
    monkeypatch.setattr(replay_service.threading, 'Thread', new_thread)
    try:
        assert engine.start() is False
        old_worker.join.assert_called_once()
        assert engine._worker_thread is old_worker
        assert '仍在结束' in engine.get_status()['last_error']
        new_thread.assert_not_called()
    finally:
        engine._worker_thread = None

import subprocess
import json
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from gamepadstudio import manual_recording as module
from gamepadstudio.screenshot_service import list_captures
from gamepadstudio.replay_timing import mp4_duration_seconds

pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='Mac recording replacement')


class Source:
    monitors = [{'left':0,'top':0,'width':8,'height':8},
                {'left':0,'top':0,'width':8,'height':8}]
    frame_size = (16, 16)  # Retina pixels differ from logical capture bounds.
    capture_method = 'synthetic'
    color_mode = 'SDR BT.709'

    def __init__(self, recorder, stamps=(0, .4, 1.7, 3.5)):
        self.recorder = recorder
        self.stamps = iter(stamps)
        self.closed = self.cancelled = False

    def __enter__(self): return self
    def __exit__(self, *_): self.closed = True
    def configure(self, bbox, fps): self.bbox = bbox
    def cancel(self): self.cancelled = True
    def grab(self, bbox):
        try: stamp = next(self.stamps)
        except StopIteration:
            self.recorder._stop.set()
            stamp = 4
        return SimpleNamespace(rgb=bytes((30, 100, 200))*256, timestamp=stamp)


@pytest.fixture
def recording(monkeypatch, tmp_path):
    from gamepadstudio import macos_permissions
    monkeypatch.setattr(macos_permissions, 'screen_capture_permission_status', lambda: {'granted':True})
    monkeypatch.setattr(module, 'detect_hardware_encoder', lambda _: 'libx264')
    monkeypatch.setattr(module, 'smart_foreground_info', lambda: (99, 'Synthetic Window'))
    value = module.ManualRecording(tmp_path, capture_mode='monitor_1', codec='h264')
    source = Source(value)
    monkeypatch.setattr(module, 'create_replay_capture', lambda: source)
    return value, source


def finish(value):
    value._worker.join(timeout=10)
    assert not value._worker.is_alive()


def test_full_recording_has_physical_pixels_and_original_elapsed_time(recording):
    value, source = recording
    ffmpeg = module.get_ffmpeg_path()
    if not ffmpeg: pytest.skip('FFmpeg unavailable')
    assert value.start()
    finish(value)
    result = value.status()
    assert result['phase'] == 'idle' and result['frames'] == 4
    assert result['path'].endswith('.mp4') and result['last_error'] == ''
    assert 3.5 <= mp4_duration_seconds(result['path']) <= 3.7
    decoded = subprocess.run([ffmpeg,'-v','error','-i',result['path'],'-frames:v','1',
                              '-f','rawvideo','-pix_fmt','rgb24','pipe:1'], capture_output=True, timeout=10)
    assert decoded.returncode == 0 and len(decoded.stdout) == 16*16*3
    video = value.save_dir / result['path'].split('/')[-1]
    with video.with_suffix('.jpg').open('rb') as stream:
        assert stream.read(3) == b'\xff\xd8\xff'
    metadata = json.loads(video.with_suffix('.json').read_text(encoding='utf-8'))
    assert (metadata['width'], metadata['height']) == (16, 16)
    assert metadata['resolution'] == '16x16'
    assert metadata['mode'] == 'monitor_1'
    assert metadata['capture_method'] == 'synthetic'
    assert metadata['color_mode'] == 'SDR BT.709'
    assert metadata['duration_seconds'] == pytest.approx(mp4_duration_seconds(video), abs=.001)
    gallery = list_captures(value.save_dir)
    assert len(gallery) == 1
    assert (gallery[0]['width'], gallery[0]['height']) == (16, 16)
    assert gallery[0]['thumb_path'] == str(video.with_suffix('.jpg'))
    assert source.closed
    assert not list(value.save_dir.glob('*.partial.mp4'))
    assert not list(value.save_dir.glob('.*.partial.mp4'))


def test_permission_failure_never_starts_capture(recording, monkeypatch):
    value, source = recording
    from gamepadstudio import macos_permissions
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(macos_permissions, 'screen_capture_permission_status', lambda: {'granted':False,'reason':'denied'})
    assert not value.start()
    assert value.status()['last_error'] == 'denied'
    assert value._worker is None and not source.closed


def test_missing_encoder_returns_failure(recording, monkeypatch):
    value, _ = recording
    monkeypatch.setattr(module, 'get_ffmpeg_path', lambda: None)
    assert not value.start()
    assert value.status()['phase'] == 'failed'


def test_disconnected_monitor_does_not_create_file(recording):
    value, source = recording
    value.capture_mode = 'monitor_3'
    assert value.start()
    finish(value)
    assert value.status()['phase'] == 'failed'
    assert '断开' in value.status()['last_error']
    assert not list(value.save_dir.iterdir())
    assert source.closed


@pytest.mark.parametrize('stamps', [(1,0), (0,float('nan')), (0,float('inf'))])
def test_invalid_capture_timing_is_not_saved(recording, stamps):
    value, source = recording
    source.stamps = iter(stamps)
    assert value.start()
    finish(value)
    assert value.status()['phase'] == 'failed'
    assert value.status()['path'] == ''
    assert not list(value.save_dir.iterdir())


def test_dimension_change_rejects_file(recording, monkeypatch):
    value, source = recording
    monkeypatch.setattr(source, 'grab', lambda _: SimpleNamespace(rgb=b'wrong', timestamp=0))
    assert value.start()
    finish(value)
    assert value.status()['phase'] == 'failed'
    assert not list(value.save_dir.iterdir())


def test_stop_during_source_initialization_does_not_leave_output(recording, monkeypatch):
    value, source = recording
    started, release = threading.Event(), threading.Event()
    def configure(*_):
        started.set(); release.wait(3)
    monkeypatch.setattr(source, 'configure', configure)
    assert value.start() and started.wait(3)
    assert value.start()  # repeated start preserves the same worker.
    assert value.request_stop()
    assert value.status()['phase'] == 'stopping'
    release.set()
    finish(value)
    value._stopper.join(3)
    assert not value.status()['running']
    assert value.status()['frames'] == 0
    assert not list(value.save_dir.iterdir())
    assert source.closed and source.cancelled


def test_canceled_empty_recording_reports_no_saved_path(recording):
    value, source = recording
    source.stamps = iter(())
    completed = []
    value.on_saved = lambda path, error: completed.append((path,error))
    assert value.start()
    finish(value)
    assert value.status()['path'] == ''
    assert completed == [('', '')]
    assert not list(value.save_dir.iterdir())


def test_no_input_fixed_fps_or_replay_duration_truncation(recording):
    value, source = recording
    value.encoder = 'h264_videotoolbox'
    command = value._command(source, value.save_dir/'part.mp4')
    assert '-r' not in command and '-t' not in command and '-ss' not in command
    assert command[command.index('-allow_sw')+1] == '0'
    assert command[command.index('-fps_mode')+1] == 'passthrough'


def test_successful_recording_can_restart_without_overwriting(recording, monkeypatch):
    value, source = recording
    assert value.start()
    finish(value)
    first = value.status()['path']
    second_source = Source(value, stamps=(0,.1))
    monkeypatch.setattr(module, 'create_replay_capture', lambda: second_source)
    assert value.start()
    finish(value)
    assert first != value.status()['path']
    assert len(list(value.save_dir.glob('*.mp4'))) == 2


def test_cancel_failure_still_joins_worker_and_reports_failure(recording, monkeypatch):
    value, source = recording
    started, release = threading.Event(), threading.Event()
    completed = []
    value.on_saved = lambda path,error:completed.append((path,error))
    monkeypatch.setattr(source,'configure',lambda *_:(started.set(),release.wait(3)))
    def cancel():
        release.set()
        raise OSError('cancel refused')
    monkeypatch.setattr(source,'cancel',cancel)
    assert value.start() and started.wait(3)
    value.stop()
    assert not value._worker.is_alive() and source.closed
    assert value.status()['phase'] == 'failed'
    assert completed and 'cancel refused' in completed[0][1]


def test_stop_reports_failure_when_capture_worker_never_finishes(recording):
    value, source = recording

    class StuckWorker:
        def __init__(self):
            self.join_timeouts = []

        def join(self, timeout):
            self.join_timeouts.append(timeout)

        def is_alive(self):
            return True

    worker = StuckWorker()
    killed = []
    value._worker = worker
    value._source = source
    value._proc = SimpleNamespace(poll=lambda: None, kill=lambda: killed.append(True))
    value._phase = 'recording'

    with pytest.raises(TimeoutError, match='录像结束超时'):
        value.stop()

    assert source.cancelled and killed == [True]
    assert worker.join_timeouts == [6, 2]
    assert value._stop.is_set()
    assert value.status()['phase'] == 'stopping'
    assert value.status()['running']
    assert '录像结束超时' in value.status()['last_error']
    assert value.status()['path'] == ''

    notices = []
    value.on_event = notices.append
    assert value.request_stop()
    value._stopper.join(timeout=1)
    assert not value._stopper.is_alive()
    assert any('录像结束失败：录像结束超时' in notice for notice in notices)


def test_notice_callback_failure_does_not_corrupt_recording(recording):
    value, _ = recording
    def notice(_): raise RuntimeError('UI closed')
    value.on_event = notice
    assert value.start()
    finish(value)
    assert value.status()['path'] and value.status()['phase'] == 'idle'


def test_gallery_artifact_failure_does_not_discard_playable_video(recording, monkeypatch):
    value, _ = recording
    monkeypatch.setattr(value, '_preview_jpeg', lambda *_: (_ for _ in ()).throw(OSError('preview unavailable')))
    original_write = type(value.save_dir).write_text

    def fail_sidecar(path, *args, **kwargs):
        if path.suffix == '.json':
            raise OSError('sidecar unavailable')
        return original_write(path, *args, **kwargs)

    monkeypatch.setattr(type(value.save_dir), 'write_text', fail_sidecar)
    assert value.start()
    finish(value)
    result = value.status()
    assert result['phase'] == 'idle' and result['path'] and result['last_error'] == ''
    assert len(list(value.save_dir.glob('*.mp4'))) == 1


def test_rename_failure_never_claims_saved_file(recording,monkeypatch):
    value, _ = recording
    from pathlib import Path
    def refused(*_):
        raise OSError('rename refused')
    monkeypatch.setattr(Path,'replace',refused)
    assert value.start()
    finish(value)
    assert value.status()['phase'] == 'failed' and value.status()['path'] == ''
    assert 'rename refused' in value.status()['last_error']
    assert not list(value.save_dir.iterdir())

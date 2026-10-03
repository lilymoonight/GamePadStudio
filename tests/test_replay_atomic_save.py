"""A failed instant-replay export must not appear as a playable gallery item."""
from pathlib import Path
import subprocess

from gamepadstudio import replay_service, screenshot_service


def test_failed_replay_mux_never_publishes_or_leaves_partial_video(tmp_path, monkeypatch):
    packet = b'\x47' + b'\x00' * 187
    stream = packet * 30
    engine = replay_service.ReplayBufferEngine(tmp_path)
    engine._ram_chunks.append((0., stream))
    engine._total_bytes = len(stream)
    monkeypatch.setattr(replay_service, 'get_ffmpeg_path', lambda: '/fixture/ffmpeg')
    observed = []

    class FailedMux:
        returncode = 1

        def __init__(self, command, **_kwargs):
            output = Path(command[-1])
            assert output.name.startswith('.') and output.name.endswith('.partial.mp4')
            self.output = output

        def communicate(self, *, input, timeout):
            assert input == stream and timeout == 60
            self.output.write_bytes(b'bad incomplete mp4')
            observed.extend(screenshot_service.list_captures(tmp_path))
            return b'', b''

    monkeypatch.setattr(replay_service.subprocess, 'Popen', FailedMux)
    assert engine.save_replay('synthetic') is None
    assert observed == []
    assert not list(tmp_path.iterdir())


def test_timed_out_replay_mux_kills_encoder_and_removes_unplayable_output(tmp_path, monkeypatch):
    packet = b'\x47' + b'\x00' * 187
    stream = packet * 30
    engine = replay_service.ReplayBufferEngine(tmp_path)
    engine._ram_chunks.append((0., stream))
    engine._total_bytes = len(stream)
    monkeypatch.setattr(replay_service, 'get_ffmpeg_path', lambda: '/fixture/ffmpeg')
    state = {'killed': False}

    class HungMux:
        def __init__(self, command, **_kwargs):
            self.output = Path(command[-1])

        def communicate(self, *, input=None, timeout=None):
            if timeout == 60:
                self.output.write_bytes(b'bad incomplete mp4')
                raise subprocess.TimeoutExpired('ffmpeg', 60)
            assert state['killed'] and input is None and timeout is None
            return b'', b''

        def kill(self):
            state['killed'] = True

    monkeypatch.setattr(replay_service.subprocess, 'Popen', HungMux)
    assert engine.save_replay('synthetic') is None
    assert state['killed'] and not list(tmp_path.iterdir())

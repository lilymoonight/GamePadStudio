"""Consent failures must block capture without creating files or starting workers."""
from types import SimpleNamespace

import pytest

from gamepadstudio import macos_permissions as permissions
from gamepadstudio import replay_service, screenshot_service


@pytest.fixture
def native(monkeypatch):
    calls = []
    state = {'granted': False}
    def preflight():
        calls.append('preflight')
        return state['granted']
    def request():
        calls.append('request')
        state['granted'] = True
        return True
    monkeypatch.setattr(permissions, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(permissions, '_graphics', lambda: SimpleNamespace(
        CGPreflightScreenCaptureAccess=preflight, CGRequestScreenCaptureAccess=request))
    return calls, state


def test_preflight_never_requests_and_reflects_new_consent(native):
    calls, state = native
    denied = permissions.screen_capture_permission_status()
    assert not denied['granted'] and '屏幕录制' in denied['reason']
    assert calls == ['preflight']
    state['granted'] = True
    assert permissions.screen_capture_permission_status()['granted']
    assert calls == ['preflight', 'preflight']


def test_only_explicit_request_prompts(native):
    calls, _ = native
    assert permissions.request_screen_capture_permission()['granted']
    assert calls == ['request', 'preflight']


def test_denied_screenshot_does_not_open_capture_or_create_folder(native, monkeypatch, tmp_path):
    monkeypatch.setattr(screenshot_service, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(screenshot_service.mss, 'mss', lambda: pytest.fail('No pixel capture without consent'))
    destination = tmp_path / 'new-album'
    with pytest.raises(PermissionError, match='屏幕录制'):
        screenshot_service.take_screenshot(destination)
    assert not destination.exists()


def test_denied_replay_does_not_start_capture_or_encoding(native, monkeypatch, tmp_path):
    monkeypatch.setattr(replay_service, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(replay_service, 'get_ffmpeg_path', lambda: pytest.fail('No encoder without consent'))
    engine = replay_service.ReplayBufferEngine(tmp_path)
    assert not engine.start()
    assert not engine.running and engine._worker_thread is None
    assert '屏幕录制' in engine.get_status()['last_error']


def test_mac_panorama_accepts_only_verified_equal_refresh_displays(monkeypatch, tmp_path):
    monkeypatch.setattr(replay_service, 'sys', SimpleNamespace(platform='darwin'))
    monitors = [dict(left=0, top=0, width=200, height=100),
                dict(left=0, top=0, width=100, height=100),
                dict(left=100, top=0, width=100, height=100)]

    class Capture:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    capture = Capture()
    capture.monitors = monitors
    monkeypatch.setattr(replay_service, 'create_replay_capture', lambda: capture)
    rows = [dict(monitor, refresh_hz=60) for monitor in monitors[1:]]
    monkeypatch.setattr(replay_service, 'enumerate_displays', lambda: rows)
    engine = replay_service.ReplayBufferEngine(tmp_path, capture_mode='all')
    assert engine._panorama_error() == ''

    rows[1]['refresh_hz'] = 59.94
    assert '刷新率不同' in engine._panorama_error()

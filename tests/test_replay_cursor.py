"""Regression coverage for continuous capture cursor flicker and process churn."""
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gamepadstudio import replay_capture, replay_service


def test_replay_capture_preserves_cursor_without_changing_screenshots(monkeypatch):
    original_gdi = Mock()
    captures = []

    def new_capture():
        capture = SimpleNamespace(gdi32=original_gdi)
        captures.append(capture)
        return capture

    monkeypatch.setattr(replay_capture.sys, 'platform', 'win32')
    monkeypatch.setattr(replay_capture.mss, 'mss', new_capture)
    replay = replay_capture.create_replay_capture()
    screenshot = replay_capture.mss.mss()
    srccopy, captureblt = 0x00CC0020, 0x40000000
    coordinates = (1, 0, 0, 64, 64, 2, 0, 0)
    replay.gdi32.BitBlt(*coordinates, srccopy | captureblt)
    original_gdi.BitBlt.assert_called_with(*coordinates, srccopy)
    screenshot.gdi32.BitBlt(*coordinates, srccopy | captureblt)
    original_gdi.BitBlt.assert_called_with(*coordinates, srccopy | captureblt)
    replay.gdi32.DeleteObject(42)
    original_gdi.DeleteObject.assert_called_once_with(42)
    assert screenshot.gdi32 is original_gdi


def test_status_queries_never_probe_encoder(tmp_path, monkeypatch):
    probe = Mock(side_effect=AssertionError('Status started an FFmpeg process'))
    monkeypatch.setattr(replay_service, 'detect_hardware_encoder', probe)
    engine = replay_service.ReplayBufferEngine(tmp_path)
    for _ in range(10):
        assert engine.get_status()['encoder'] is None
    engine.encoder = 'hevc_amf'
    assert engine.get_status()['encoder'] == 'hevc_amf'
    probe.assert_not_called()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows process startup flags')
def test_ffmpeg_startup_suppresses_windows_and_busy_cursor():
    flags = replay_service._subprocess_hidden_flags()
    assert flags['creationflags'] & 0x08000000  # CREATE_NO_WINDOW
    assert flags['startupinfo'].dwFlags & 0x80  # STARTF_FORCEOFFFEEDBACK
    assert flags['startupinfo'].dwFlags & 1  # STARTF_USESHOWWINDOW
    assert flags['startupinfo'].wShowWindow == 0


def test_replay_hud_uses_agent_status_without_launching_ffmpeg(monkeypatch):
    from gamepadstudio.studio import Studio
    probe = Mock()
    monkeypatch.setattr(replay_service, 'detect_hardware_encoder', probe)
    label = Mock()
    window = SimpleNamespace(config={'replay_buffer_enabled': True}, remote=True,
                             client=SimpleNamespace(status={'replay': {'running': True, 'encoder': 'hevc_amf'}}),
                             replay_hud_label=label)
    Studio._update_replay_hud(window)
    assert 'hevc_amf' in label.setText.call_args.args[0]
    probe.assert_not_called()


def test_settings_reload_keeps_buffer_and_only_restarts_for_capture_changes(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from gamepadstudio.agent import Agent
    from gamepadstudio.studio_core import ConfigStore

    app = QApplication.instance() or QApplication([])
    engine = Mock()
    engine.get_status.return_value = {}
    engine.is_running.return_value = False
    monkeypatch.setattr('gamepadstudio.agent.ReplayBufferEngine', Mock(return_value=engine))
    monkeypatch.setattr('gamepadstudio.agent.LocalServer', Mock())
    store = ConfigStore(tmp_path)
    store.data['replay_buffer_enabled'] = True
    store.save()
    device = Mock()
    device.controller_isolation_status.return_value = {
        'active': False, 'restore_pending': False, 'reason': ''}
    agent = Agent(tmp_path, device, Mock())
    agent.timer.stop(); agent.scan_timer.stop(); agent.broadcast_timer.stop()
    try:
        engine.start.assert_called_once()
        engine.reset_mock()
        # An independent GUI config store writes a normal preference.
        store = ConfigStore(tmp_path)
        store.data['rumble'] = 0.42
        store.save()
        agent.handle({'command': 'reload'})
        engine.stop.assert_not_called()
        engine.start.assert_not_called()

        store.data['replay_bitrate_mbps'] = 35
        store.save()
        agent.handle({'command': 'reload'})
        engine.stop.assert_called_once()
        engine.start.assert_called_once()
        assert engine.bitrate_mbps == 35

        engine.reset_mock()
        store.data['replay_buffer_enabled'] = False
        store.save()
        agent.handle({'command': 'reload'})
        engine.stop.assert_called_once()
        engine.start.assert_not_called()
        agent.handle({'command': 'save_replay'})
        engine.start.assert_not_called()
        engine.save_replay.assert_not_called()

        engine.reset_mock()
        store.data['replay_buffer_enabled'] = True
        store.save()
        agent.handle({'command': 'reload'})
        engine.start.assert_called_once()
        engine.stop.assert_not_called()
    finally:
        agent.close()

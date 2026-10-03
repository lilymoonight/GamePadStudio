"""Mac screenshot entry uses a separate, safely owned native shortcut."""
import copy
import json
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from gamepadstudio import emergency_hotkey, macos_hotkey, screenshot_hotkey, studio_core
from gamepadstudio.studio_core import ConfigStore
from tests.test_macos_hotkey import FakeCarbon


def mac_settings(shortcut=screenshot_hotkey.DEFAULT_SHORTCUT, enabled=True):
    return {'enabled': enabled, 'shortcut': shortcut}


def test_mac_default_and_legacy_window_capture_migrate_without_changing_explicit_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(studio_core, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(screenshot_hotkey, 'sys', SimpleNamespace(platform='darwin'))
    store = ConfigStore(tmp_path)
    assert store.data['screenshot_hotkey'] == mac_settings()
    store.save()
    saved = json.loads(store.path.read_text(encoding='utf-8'))
    saved.pop('screenshot_hotkey')
    saved.pop('replay_capture_mode')
    saved['capture_mode'] = 'window'
    store.path.write_text(json.dumps(saved), encoding='utf-8')
    migrated = ConfigStore(tmp_path)
    assert migrated.data['screenshot_hotkey'] == mac_settings()
    assert migrated.data['capture_mode'] == 'window'
    assert migrated.data['replay_capture_mode'] == 'game'
    migrated.set_setting('screenshot_hotkey', mac_settings(enabled=False))
    migrated.save()
    assert ConfigStore(tmp_path).data['screenshot_hotkey'] == mac_settings(enabled=False)
    saved['replay_capture_mode'] = 'monitor_2'
    store.path.write_text(json.dumps(saved), encoding='utf-8')
    assert ConfigStore(tmp_path).data['replay_capture_mode'] == 'monitor_2'


def test_mac_custom_shortcut_survives_windows_config_roundtrip(tmp_path, monkeypatch):
    choice = mac_settings('Alt+Cmd+S')
    monkeypatch.setattr(studio_core, 'sys', SimpleNamespace(platform='darwin'))
    store = ConfigStore(tmp_path)
    store.set_setting('screenshot_hotkey', choice)
    store.save()
    monkeypatch.setattr(studio_core, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(screenshot_hotkey, 'sys', SimpleNamespace(platform='win32'))
    windows = ConfigStore(tmp_path)
    assert windows.data['screenshot_hotkey'] == choice
    windows.save()
    monkeypatch.setattr(studio_core, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(screenshot_hotkey, 'sys', SimpleNamespace(platform='darwin'))
    assert ConfigStore(tmp_path).data['screenshot_hotkey'] == choice


@pytest.mark.parametrize('shortcut', ['Cmd+Shift+3', 'Ctrl+Cmd+Shift+4', 'Alt+Cmd+Shift+5',
                                     'Cmd+Shift+6'])
def test_macos_system_capture_shortcuts_never_reserved(shortcut):
    with pytest.raises(ValueError, match='macOS 系统截图'):
        screenshot_hotkey.normalize_screenshot_hotkey_settings(mac_settings(shortcut), strict=True)


def test_disabled_custom_shortcut_is_canonical_and_bad_value_rejected():
    assert screenshot_hotkey.normalize_screenshot_hotkey_settings(
        mac_settings('command+alt+k', enabled=False), strict=True) == mac_settings('Alt+Cmd+K', False)
    with pytest.raises(ValueError):
        screenshot_hotkey.normalize_screenshot_hotkey_settings(mac_settings('Ctrl+S', False), strict=True)


def test_screenshot_and_emergency_cannot_share_an_enabled_shortcut(tmp_path, monkeypatch):
    monkeypatch.setattr(studio_core, 'sys', SimpleNamespace(platform='darwin'))
    store = ConfigStore(tmp_path)
    screenshot = copy.deepcopy(store.data['screenshot_hotkey'])
    with pytest.raises(ValueError, match='截图'):
        store.set_setting('emergency_hotkey', screenshot)
    store.set_setting('screenshot_hotkey', dict(screenshot, enabled=False))
    store.set_setting('emergency_hotkey', screenshot)
    with pytest.raises(ValueError, match='紧急暂停'):
        store.set_setting('screenshot_hotkey', screenshot)


@pytest.fixture
def native(monkeypatch):
    app = QApplication.instance() or QApplication([])
    library = FakeCarbon()
    monkeypatch.setattr(emergency_hotkey, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(screenshot_hotkey, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(macos_hotkey, '_CarbonNative', lambda: library)
    owners = []
    yield SimpleNamespace(app=app, library=library, owners=owners)
    library.unregister_code = library.remove_code = 0
    for owner in owners:
        owner.close()


def test_native_screenshot_and_emergency_dispatch_independently_and_release(native):
    called = []
    emergency = emergency_hotkey.EmergencyHotkey(lambda: called.append('pause'))
    screenshot = screenshot_hotkey.ScreenshotHotkey(lambda: called.append('capture'))
    native.owners.extend((emergency, screenshot))
    assert emergency.configure(mac_settings('Ctrl+Alt+F10'))['registered']
    assert screenshot.configure(mac_settings())['registered']
    assert len(native.library.hotkeys) == 2
    native.library.queue.append(native.library.event(screenshot))
    emergency._mac_hotkey._pump_events()
    assert called == ['capture']
    native.library.queue.append(native.library.event(emergency))
    screenshot._mac_hotkey._pump_events()
    assert called == ['capture', 'pause']
    screenshot.close()
    emergency.close()
    assert not native.library.hotkeys and not native.library.handlers


def test_native_registration_conflict_cleans_partial_handler_and_reports_error(native):
    screenshot = screenshot_hotkey.ScreenshotHotkey(lambda: None)
    native.owners.append(screenshot)
    native.library.register_code = macos_hotkey.HOTKEY_EXISTS
    status = screenshot.configure(mac_settings())
    assert not status['registered'] and '占用' in status['error']
    assert not native.library.hotkeys and not native.library.handlers
    native.library.register_code = 0
    assert screenshot.configure(mac_settings())['registered']
    screenshot.close()
    assert not native.library.hotkeys and not native.library.handlers


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS agent keyboard entry')
def test_agent_routes_mac_keyboard_screenshot_through_existing_capture_pipeline(tmp_path, monkeypatch):
    from gamepadstudio import agent as agent_module
    from tests.test_agent import ActionsStub, DeviceStub

    captures = []

    class FakeScreenshotHotkey:
        def __init__(self, callback, parent=None):
            self.callback = callback
            self.settings = None
            self.closed = False

        def configure(self, settings):
            self.settings = copy.deepcopy(settings)
            return self.status()

        def status(self):
            return {**self.settings, 'registered': self.settings['enabled'], 'error': ''}

        def close(self):
            self.closed = True

    class FakeServer:
        def __init__(self, root, handler):
            self.events = []

        def broadcast(self, event):
            self.events.append(event)

        def close(self):
            pass

    monkeypatch.setattr(agent_module, 'ScreenshotHotkey', FakeScreenshotHotkey)
    monkeypatch.setattr(agent_module, 'LocalServer', FakeServer)
    monkeypatch.setattr(agent_module.Agent, 'capture', lambda self: captures.append('capture'))
    agent = agent_module.Agent(tmp_path, DeviceStub(), ActionsStub())
    try:
        assert agent.status()['screenshot_hotkey']['registered']
        agent.screenshot_hotkey.callback()
        assert captures == ['capture']
        saved = ConfigStore(tmp_path)
        saved.set_setting('screenshot_hotkey', mac_settings(enabled=False))
        saved.save()
        agent.handle({'command': 'reload'})
        assert not agent.status()['screenshot_hotkey']['registered']
    finally:
        hotkey = agent.screenshot_hotkey
        agent.close()
        assert hotkey.closed

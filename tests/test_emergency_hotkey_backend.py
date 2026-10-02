"""Global keyboard escape owns registration once and can only pause output."""
import copy
import json
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from gamepadstudio.agent import Agent
from gamepadstudio.emergency_hotkey import DEFAULT_SHORTCUT, normalize_hotkey_settings
from gamepadstudio.mapping_engine import MappingRuntime
from gamepadstudio.profile_transfer import export_profile
from gamepadstudio.studio_core import ConfigStore, DEVICE_SETTING_KEYS, profile_scope
from tests.test_application_profile_backend import controller
from tests.test_unified_mapping import Actions, entry


class Registration:
    """No RegisterHotKey or native message hooks are installed by these tests."""
    def __init__(self, callback, parent=None):
        self.callback = callback
        self.calls = []
        self.conflict = False
        self.closed = 0
        self.value = {'enabled': False, 'shortcut': DEFAULT_SHORTCUT, 'registered': False, 'error': ''}

    def configure(self, settings):
        settings = normalize_hotkey_settings(settings, strict=True)
        self.calls.append(copy.deepcopy(settings))
        self.value = {**settings, 'registered': settings['enabled'] and not self.conflict,
                      'error': '快捷键已被其他程序占用' if settings['enabled'] and self.conflict else ''}
        return self.status()

    def status(self): return copy.deepcopy(self.value)

    def fire(self):
        if self.value['registered']:
            self.callback()

    def close(self):
        self.closed += 1
        self.value['registered'] = False


@pytest.fixture
def hotkey_backend(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    state = controller()
    store = ConfigStore(tmp_path)
    store.activate_controller(state)
    store.set_setting('emergency_hotkey', {'enabled': True, 'shortcut': DEFAULT_SHORTCUT})
    store.save()
    foreground = {'executable': r'C:\Windows\explorer.exe', 'pid': 888999, 'hwnd': 80}
    monkeypatch.setattr('gamepadstudio.agent.EmergencyHotkey', Registration)
    monkeypatch.setattr('gamepadstudio.agent.foreground_application', lambda: dict(foreground))
    monkeypatch.setattr(Agent, 'apply_device_cloaking', lambda self: None)
    monkeypatch.setattr(Agent, 'apply_gamebar_shield', lambda self: None)

    class Device:
        available = []
        def __init__(self): self.state = state
        def scan(self): pass
        def read(self): return self.state
        def close(self): pass

    device, actions = Device(), Actions()
    agent = Agent(tmp_path, device, actions)
    agent.timer.stop(); agent.scan_timer.stop(); agent.broadcast_timer.stop()
    agent.engine.close()
    agent.engine = MappingRuntime(actions, agent.dispatch, start_mouse=False)
    agent._user32 = SimpleNamespace(GetAsyncKeyState=lambda _: 0)
    agent._last_prtsc_down = False
    events = []
    monkeypatch.setattr(agent.server, 'broadcast', lambda event: events.append(copy.deepcopy(event)))
    agent.poll()
    name = agent.config['active_profile']
    agent.config['profiles'][name] = {
        '0': entry('hold', 'Ctrl+E'), '1': entry('hold', 'Ctrl+Q'),
        '2': entry('mouse_hold', 'left'), '3': entry('mouse_hold', 'left')}
    agent.store.save()
    yield SimpleNamespace(agent=agent, device=device, actions=actions, events=events,
                          foreground=foreground, app=app, hotkey=agent.emergency_hotkey)
    agent.close()


def hold_shared_outputs(context):
    context.device.state['buttons'] = [0, 1, 2, 3]
    context.agent.engine.update(context.device.state, context.agent.config, now=1., enabled=True)
    context.agent.engine.update(context.device.state, context.agent.config, now=1.2, enabled=True)
    assert context.actions.keys[17] == 2
    assert context.actions.keys[ord('E')] == context.actions.keys[ord('Q')] == 1
    assert context.actions.mouse == {'left'}


def test_default_disabled_global_setting_never_enters_device_or_portable_payload(tmp_path):
    store = ConfigStore(tmp_path)
    first, second = controller(), controller('pad:two', 2)
    assert store.data['emergency_hotkey'] == {'enabled': False, 'shortcut': DEFAULT_SHORTCUT}
    assert 'emergency_hotkey' not in DEVICE_SETTING_KEYS
    store.activate_controller(first)
    store.set_setting('emergency_hotkey', {'enabled': True, 'shortcut': 'alt+ctrl+f11'}, first)
    store.save()
    assert store.data['emergency_hotkey'] == {'enabled': True, 'shortcut': 'Ctrl+Alt+F11'}
    assert 'emergency_hotkey' not in store.data['device_settings'][profile_scope(first)]
    store.activate_controller(second)
    assert store.data['emergency_hotkey']['enabled']
    assert 'emergency_hotkey' not in store.settings_for(second)
    name = store.profiles_for(second, 'kbm')[0]
    assert 'emergency_hotkey' not in json.dumps(export_profile(store.data, second, name))
    assert ConfigStore(tmp_path).data['emergency_hotkey'] == {'enabled': True, 'shortcut': 'Ctrl+Alt+F11'}


@pytest.mark.parametrize('invalid', [
    None, [], {'enabled': 1, 'shortcut': DEFAULT_SHORTCUT},
    {'enabled': 'yes', 'shortcut': DEFAULT_SHORTCUT},
    {'enabled': True, 'shortcut': 'A'}, {'enabled': True, 'shortcut': 'Ctrl+Alt+F999'},
])
def test_strict_setting_rejects_invalid_hotkeys_without_mutation(tmp_path, invalid):
    store = ConfigStore(tmp_path)
    before = copy.deepcopy(store.data)
    with pytest.raises(ValueError):
        store.set_setting('emergency_hotkey', invalid)
    assert store.data == before


@pytest.mark.parametrize('invalid', [
    None, [], {'enabled': 1, 'shortcut': DEFAULT_SHORTCUT},
    {'enabled': 'yes', 'shortcut': DEFAULT_SHORTCUT},
    {'enabled': True, 'shortcut': 'A'}, {'enabled': True, 'shortcut': 'Ctrl+Alt+F999'},
])
def test_invalid_saved_hotkey_is_disabled_without_discarding_profiles(tmp_path, invalid):
    store = ConfigStore(tmp_path)
    state = controller()
    store.activate_controller(state)
    name = store.data['active_profile']
    store.data['profiles'][name]['0'] = entry('hold', 'F')
    store.data['emergency_hotkey'] = invalid
    store.save()
    loaded = ConfigStore(tmp_path)
    assert loaded.data['emergency_hotkey'] == {'enabled': False, 'shortcut': DEFAULT_SHORTCUT}
    assert loaded.data['profiles'][name]['0'] == entry('hold', 'F')
    assert not loaded.warning


def test_registration_initialized_once_and_status_is_authoritative(hotkey_backend):
    context = hotkey_backend
    assert context.hotkey.calls == [{'enabled': True, 'shortcut': DEFAULT_SHORTCUT}]
    expected = {'enabled': True, 'shortcut': DEFAULT_SHORTCUT, 'registered': True, 'error': ''}
    for _ in range(3):
        assert context.agent.status()['emergency_hotkey'] == expected
    assert len(context.hotkey.calls) == 1


def test_hotkey_releases_shared_keys_and_mouse_and_persists_pause(hotkey_backend):
    context = hotkey_backend
    hold_shared_outputs(context)
    context.hotkey.fire()
    assert not context.agent.enabled
    assert context.agent.config['mapping_enabled'] is False
    assert not any(context.actions.keys.values()) and not context.actions.mouse
    assert context.agent.engine.feedback()['outputs'] == []
    assert ConfigStore(context.agent.root).data['mapping_enabled'] is False
    assert ('mouse', 'left', False) in context.actions.calls
    assert any(event.get('type') == 'notice' and '紧急暂停' in event.get('message', '') for event in context.events)
    context.agent.poll()
    assert not any(context.actions.keys.values()) and not context.actions.mouse


@pytest.mark.parametrize('guard', ['paused', 'editing', 'preview'])
def test_emergency_pause_never_restores_output_during_any_guard(hotkey_backend, guard):
    context = hotkey_backend
    if guard == 'paused':
        context.agent.set_enabled(False)
    elif guard == 'editing':
        context.agent.suspended_until = float('inf')
    else:
        context.agent.preview_until = float('inf')
    before_profile = context.agent.config['active_profile']
    context.hotkey.fire()
    context.hotkey.fire()
    assert not context.agent.enabled
    assert context.agent.config['mapping_enabled'] is False
    assert context.agent.config['active_profile'] == before_profile
    assert not context.actions.calls
    assert ConfigStore(context.agent.root).data['mapping_enabled'] is False


def test_profile_and_device_switch_preserve_pause_and_single_global_registration(hotkey_backend):
    context = hotkey_backend
    first_scope = profile_scope(context.device.state)
    nikki = context.agent.store.profiles_for(context.device.state, 'kbm')[0]
    context.agent.handle({'command': 'mapping_change', 'device_scope': first_scope,
                          'change': {'op': 'select', 'profile': nikki}})
    context.hotkey.fire()
    context.device.state = controller('pad:two', 2)
    context.agent.poll()
    assert not context.agent.enabled
    assert context.agent.config['active_profile'] in context.agent.store.profiles_for(context.device.state)
    assert len(context.hotkey.calls) == 1
    context.hotkey.fire()
    assert not context.agent.enabled
    assert ConfigStore(context.agent.root).data['mapping_enabled'] is False


def test_automatic_application_changes_keep_emergency_pause_and_manual_baseline(hotkey_backend):
    context = hotkey_backend
    baseline = context.agent.config['active_profile']
    nikki = context.agent.store.profiles_for(context.device.state, 'kbm')[0]
    path = r'C:\Games\InfinityNikki.exe'
    context.agent.store.apply_mapping_change({'op': 'application_profiles', 'settings': {
        'enabled': True, 'rules': [{'executable': path, 'profile': nikki}]}}, context.device.state)
    context.foreground.update(executable=path, hwnd=90, pid=888998)
    context.agent.update_application_profile(force=True)
    assert context.agent.config['active_profile'] == nikki
    assert context.agent.application_profile['automatic']
    context.hotkey.fire()
    assert context.agent.config['active_profile'] == nikki
    assert context.agent.config['controller_profiles'][profile_scope(context.device.state)] == baseline
    context.foreground.update(executable=r'C:\Windows\explorer.exe', hwnd=91, pid=888997)
    context.agent.update_application_profile(force=True)
    assert context.agent.config['active_profile'] == baseline
    assert not context.agent.enabled
    assert ConfigStore(context.agent.root).data['mapping_enabled'] is False


def test_pause_is_preserved_across_actual_agent_restart_until_explicit_resume(hotkey_backend):
    context = hotkey_backend
    context.hotkey.fire()
    context.agent.close()
    restarted = Agent(context.agent.root, context.device, context.actions)
    restarted.timer.stop(); restarted.scan_timer.stop(); restarted.broadcast_timer.stop()
    try:
        assert not restarted.enabled
        assert restarted.status()['emergency_hotkey']['registered']
        restarted.emergency_hotkey.fire()
        assert not restarted.enabled
        restarted.handle({'command': 'resume'})
        assert restarted.enabled
        assert ConfigStore(context.agent.root).data['mapping_enabled'] is True
    finally:
        restarted.close()


def test_reload_exposes_registration_conflict_without_changing_mapping_state(hotkey_backend):
    context = hotkey_backend
    editor = ConfigStore(context.agent.root)
    editor.set_setting('emergency_hotkey', {'enabled': True, 'shortcut': 'Ctrl+Alt+F11'})
    editor.save()
    context.hotkey.conflict = True
    enabled = context.agent.enabled
    context.agent.handle({'command': 'reload'})
    status = context.agent.status()['emergency_hotkey']
    assert status == {'enabled': True, 'shortcut': 'Ctrl+Alt+F11',
                      'registered': False, 'error': '快捷键已被其他程序占用'}
    context.hotkey.fire()
    assert context.agent.enabled == enabled
    assert context.hotkey.calls[-1] == {'enabled': True, 'shortcut': 'Ctrl+Alt+F11'}


def test_disabled_reload_unregisters_and_close_runs_once(hotkey_backend):
    context = hotkey_backend
    editor = ConfigStore(context.agent.root)
    editor.set_setting('emergency_hotkey', {'enabled': False, 'shortcut': DEFAULT_SHORTCUT})
    editor.save()
    context.agent.handle({'command': 'reload'})
    assert context.agent.status()['emergency_hotkey']['registered'] is False
    assert context.agent.status()['emergency_hotkey']['enabled'] is False
    context.hotkey.fire()
    assert context.agent.enabled
    context.agent.close()
    context.agent.close()
    assert context.hotkey.closed == 1


def test_save_failure_cannot_block_pause_or_releasing_native_output(hotkey_backend, monkeypatch):
    context = hotkey_backend
    hold_shared_outputs(context)

    def fail_save(*args, **kwargs):
        raise OSError('Configuration disk is unavailable')

    monkeypatch.setattr(context.agent.store, 'save', fail_save)
    context.hotkey.fire()
    assert not context.agent.enabled
    assert context.agent.config['mapping_enabled'] is False
    assert not any(context.actions.keys.values()) and not context.actions.mouse
    assert any('保存或释放失败' in event.get('message', '') for event in context.events)
    context.agent.engine.update(context.device.state, context.agent.config, now=2., enabled=context.agent.enabled)
    assert not any(context.actions.keys.values()) and not context.actions.mouse


def test_engine_release_failure_still_attempts_native_release_and_persists_pause(hotkey_backend, monkeypatch):
    context = hotkey_backend
    hold_shared_outputs(context)
    original = context.agent.engine.reset

    def fail_reset(*args, **kwargs):
        raise OSError('Engine key-up failed')

    monkeypatch.setattr(context.agent.engine, 'reset', fail_reset)
    try:
        context.hotkey.fire()
        assert not context.agent.enabled
        assert context.agent.config['mapping_enabled'] is False
        assert not any(context.actions.keys.values()) and not context.actions.mouse
        assert ConfigStore(context.agent.root).data['mapping_enabled'] is False
    finally:
        monkeypatch.setattr(context.agent.engine, 'reset', original)


def test_notice_log_failure_cannot_prevent_paused_state_broadcast(hotkey_backend, monkeypatch):
    context = hotkey_backend
    hold_shared_outputs(context)

    def fail_log(_message):
        raise OSError('History disk is unavailable')

    monkeypatch.setattr(context.agent, 'log', fail_log)
    context.hotkey.fire()
    assert not context.agent.enabled
    assert not any(context.actions.keys.values()) and not context.actions.mouse
    assert any(event.get('type') == 'state' and event['enabled'] is False for event in context.events)


def test_close_unregister_failure_does_not_prevent_releasing_output(hotkey_backend, monkeypatch):
    context = hotkey_backend
    hold_shared_outputs(context)

    def fail_unregister():
        raise OSError('Native unregister failed')

    monkeypatch.setattr(context.hotkey, 'close', fail_unregister)
    context.agent.close()
    assert not any(context.actions.keys.values()) and not context.actions.mouse
    assert context.agent.closed

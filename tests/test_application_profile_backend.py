"""Device ownership and actual background output for application associations."""
import copy
import os
from types import SimpleNamespace

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import pytest
from PySide6.QtWidgets import QApplication

from gamepadstudio.agent import Agent
from gamepadstudio.studio_core import ConfigStore, profile_scope
from tests.test_unified_mapping import Actions, entry


GAME_A = r'C:\Games\Nikki\InfinityNikki.exe'
GAME_B = r'C:\Games\Other\Game.exe'
DESKTOP = r'C:\Windows\explorer.exe'


def controller(key='pad:one', instance=1):
    return dict(device_key=key, profile_key='xbox:fixture', instance_id=instance,
                family='xbox', name='Fixture controller', buttons=[], axes=[0.] * 6,
                available_buttons=list(range(15)), available_axes=list(range(6)),
                touch=[], touchpad=False, led=False, rumble=False, power=-1)


def foreground(path=DESKTOP, pid=100, hwnd=1000):
    return {'executable': path, 'pid': pid, 'hwnd': hwnd}


def profiles(store, state):
    store.activate_controller(state)
    own = store.profiles_for(state)
    return store.data['active_profile'], next(name for name in own if name != store.data['active_profile'])


def settings(profile, path=GAME_A, enabled=True):
    return {'enabled': enabled, 'rules': [{'executable': path, 'profile': profile}]}


def test_application_rules_are_scoped_and_do_not_select_a_profile(tmp_path):
    store = ConfigStore(tmp_path)
    one, two = controller(), controller('pad:two', 2)
    base, target = profiles(store, one)
    store.apply_mapping_change({'op': 'application_profiles', 'settings': settings(target)}, one)
    assert store.data['active_profile'] == base
    assert store.data['controller_profiles'][profile_scope(one)] == base
    assert store.application_settings(one) == settings(target)
    profiles(store, two)
    assert store.application_settings(two) == {'enabled': False, 'rules': []}
    loaded = ConfigStore(tmp_path)
    assert loaded.application_settings(one) == settings(target)
    assert loaded.application_settings(None) == {'enabled': False, 'rules': []}


def test_foreign_rules_and_disconnected_writes_are_rejected(tmp_path):
    store = ConfigStore(tmp_path)
    one, two = controller(), controller('pad:two', 2)
    _, target = profiles(store, one)
    profiles(store, two)
    before = copy.deepcopy(store.data)
    with pytest.raises(ValueError):
        store.apply_mapping_change({'op': 'application_profiles', 'settings': settings(target)}, two)
    assert store.data == before
    with pytest.raises(ValueError, match='请先连接'):
        store.apply_mapping_change({'op': 'application_profiles', 'settings': settings(target)}, None)
    assert store.data == before


@pytest.mark.parametrize('bad', [
    {'enabled': 'yes', 'rules': []},
    {'enabled': True, 'rules': 'bad'},
    {'enabled': True, 'rules': [{'executable': 'game.exe', 'profile': 'P'}]},
    {'enabled': True, 'rules': [{'executable': r'C:\Games\game.bat', 'profile': 'P'}]},
])
def test_invalid_rules_leave_saved_settings_untouched(tmp_path, bad):
    store = ConfigStore(tmp_path); state = controller()
    _, target = profiles(store, state)
    store.apply_mapping_change({'op': 'application_profiles', 'settings': settings(target)}, state)
    original = copy.deepcopy(store.data)
    with pytest.raises(ValueError):
        store.apply_mapping_change({'op': 'application_profiles', 'settings': bad}, state)
    assert store.data == original


def test_deleting_a_profile_removes_its_associations(tmp_path):
    store = ConfigStore(tmp_path); state = controller()
    base, target = profiles(store, state)
    store.apply_mapping_change({'op': 'application_profiles', 'settings': settings(target)}, state)
    store.apply_mapping_change({'op': 'delete', 'profile': target}, state)
    assert store.application_settings(state) == {'enabled': True, 'rules': []}
    loaded = ConfigStore(tmp_path)
    assert loaded.data['controller_profiles'][profile_scope(state)] == base
    assert loaded.application_settings(state)['rules'] == []


def test_stale_preferences_save_preserves_new_application_rules(tmp_path):
    original = ConfigStore(tmp_path); state = controller()
    _, target = profiles(original, state); original.save()
    stale, backend = ConfigStore(tmp_path), ConfigStore(tmp_path)
    backend.apply_mapping_change({'op': 'application_profiles', 'settings': settings(target)}, state)
    stale.set_setting('cooldown', 1.2); stale.save()
    loaded = ConfigStore(tmp_path)
    assert loaded.application_settings(state) == settings(target)
    assert loaded.data['cooldown'] == 1.2


def test_application_settings_ignore_corrupt_or_deleted_saved_rules(tmp_path):
    store = ConfigStore(tmp_path); state = controller()
    _, target = profiles(store, state)
    store.data['application_profiles'][profile_scope(state)] = {
        'enabled': True,
        'rules': [None, {'executable': 'relative.exe', 'profile': target},
                  {'executable': GAME_B, 'profile': 'deleted'},
                  {'executable': GAME_A, 'profile': target},
                  {'executable': GAME_A.upper(), 'profile': target}]}
    assert store.application_settings(state) == settings(target)


@pytest.fixture
def backend(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    class Device:
        state = controller()
        available = []
        def scan(self): pass
        def read(self): return self.state
        def close(self): pass
    device = Device(); actions = Actions(); fg = {'value': foreground()}
    monkeypatch.setattr('gamepadstudio.agent.foreground_application', lambda: fg['value'])
    monkeypatch.setattr(Agent, 'apply_device_cloaking', lambda self: None)
    agent = Agent(tmp_path, device, actions)
    agent.timer.stop(); agent.scan_timer.stop(); agent.broadcast_timer.stop()
    agent._user32 = SimpleNamespace(GetAsyncKeyState=lambda _: 0); agent._last_prtsc_down = False
    agent.poll()
    base, target = profiles(agent.store, device.state)
    agent.config['profiles'][base] = {'0': entry('hold', 'W')}
    agent.config['profiles'][target] = {'0': entry('hold', 'Q')}
    agent.store.save()
    agent.handle({'command': 'mapping_change', 'device_scope': profile_scope(device.state),
                  'change': {'op': 'application_profiles', 'settings': settings(target)}})
    agent.engine.update(device.state, agent.config, now=0.)
    try:
        yield agent, device, actions, fg, base, target
    finally:
        agent.close()


def test_background_switches_actual_profile_and_restores_manual_fallback(backend):
    agent, device, actions, fg, base, target = backend
    fg['value'] = foreground(GAME_A)
    assert agent.update_application_profile(now=1., force=True)
    assert agent.config['active_profile'] == target
    assert agent.config['controller_profiles'][profile_scope(device.state)] == base
    assert agent.status()['application_profile'] == {
        'automatic': True, 'executable': GAME_A, 'profile': target}
    agent.engine.update(device.state, agent.config, now=1.05)
    device.state['buttons'] = [0]; agent.engine.update(device.state, agent.config, now=1.1)
    assert actions.keys[ord('Q')] == 1
    fg['value'] = foreground(DESKTOP, pid=200, hwnd=2000)
    assert agent.update_application_profile(now=2., force=True)
    assert agent.config['active_profile'] == base
    assert not any(actions.keys.values())
    assert ConfigStore(agent.root).data['controller_profiles'][profile_scope(device.state)] == base
    assert not agent.status()['application_profile']['automatic']


def test_held_input_is_released_and_requires_a_fresh_press(backend):
    agent, device, actions, fg, base, target = backend
    device.state['buttons'] = [0]; agent.engine.update(device.state, agent.config, now=.1)
    assert actions.keys[ord('W')] == 1
    fg['value'] = foreground(GAME_A); agent.update_application_profile(now=1., force=True)
    assert not any(actions.keys.values())
    agent.engine.update(device.state, agent.config, now=1.1)
    assert not any(actions.keys.values())
    device.state['buttons'] = []; agent.engine.update(device.state, agent.config, now=1.2)
    device.state['buttons'] = [0]; agent.engine.update(device.state, agent.config, now=1.3)
    assert actions.keys[ord('Q')] == 1


def test_auto_switch_never_resumes_a_user_pause(backend):
    agent, device, actions, fg, base, target = backend
    agent.handle({'command': 'pause'})
    fg['value'] = foreground(GAME_A); agent.update_application_profile(now=1., force=True)
    assert agent.config['active_profile'] == target and not agent.enabled
    assert agent.config['mapping_enabled'] is False
    device.state['buttons'] = [0]; agent.poll()
    assert not any(actions.keys.values())
    assert ConfigStore(agent.root).data['mapping_enabled'] is False


@pytest.mark.parametrize('guard', ['editing', 'preview', 'studio'])
def test_editor_preview_and_studio_focus_freeze_automatic_switching(backend, monkeypatch, guard):
    agent, device, actions, fg, base, target = backend
    fg['value'] = foreground(GAME_A); agent.update_application_profile(now=1., force=True)
    fg['value'] = foreground(DESKTOP, pid=200, hwnd=2000)
    if guard == 'editing': agent.suspended_until = 10.
    elif guard == 'preview': agent.preview_until = 10.
    else: monkeypatch.setattr('gamepadstudio.agent.read_lock_pid', lambda _: 200)
    agent.update_application_profile(now=2., force=True)
    assert agent.config['active_profile'] == target
    if guard == 'editing': agent.suspended_until = 0.
    elif guard == 'preview': agent.preview_until = 0.
    else: monkeypatch.setattr('gamepadstudio.agent.read_lock_pid', lambda _: 0)
    agent.update_application_profile(now=3., force=True)
    assert agent.config['active_profile'] == base


def test_manual_selection_overrides_matching_app_until_foreground_changes(backend):
    agent, device, actions, fg, base, target = backend
    fg['value'] = foreground(GAME_A); agent.update_application_profile(now=1., force=True)
    result = agent.handle({'command': 'mapping_change',
                           'change': {'op': 'select', 'profile': base}})
    assert result['config']['active_profile'] == base
    assert not agent.status()['application_profile']['automatic']
    agent.update_application_profile(now=2., force=True)
    assert agent.config['active_profile'] == base
    fg['value'] = foreground(DESKTOP, pid=200, hwnd=2000)
    agent.update_application_profile(now=3., force=True)
    fg['value'] = foreground(GAME_A); agent.update_application_profile(now=4., force=True)
    assert agent.config['active_profile'] == target


def test_rule_disable_restores_baseline_even_during_editing(backend):
    agent, device, actions, fg, base, target = backend
    fg['value'] = foreground(GAME_A); agent.update_application_profile(now=1., force=True)
    agent.suspended_until = float('inf')
    result = agent.handle({'command': 'mapping_change',
                           'change': {'op': 'application_profiles', 'settings': settings(target, enabled=False)}})
    assert result['config']['active_profile'] == base
    assert agent.suspended_until == float('inf')
    assert not agent.status()['application_profile']['automatic']


def test_removing_active_rule_restores_baseline_during_editing(backend):
    agent, device, actions, fg, base, target = backend
    fg['value'] = foreground(GAME_A); agent.update_application_profile(now=1., force=True)
    agent.suspended_until = float('inf')
    result = agent.handle({'command': 'mapping_change',
                           'change': {'op': 'application_profiles',
                                      'settings': {'enabled': True, 'rules': []}}})
    assert result['config']['active_profile'] == base
    assert not agent.status()['application_profile']['automatic']
    assert agent.suspended_until == float('inf')


def test_deleting_active_automatic_profile_updates_status_during_editing(backend):
    agent, device, actions, fg, base, target = backend
    fg['value'] = foreground(GAME_A); agent.update_application_profile(now=1., force=True)
    agent.suspended_until = float('inf')
    result = agent.handle({'command': 'mapping_change',
                           'change': {'op': 'delete', 'profile': target}})
    assert result['config']['active_profile'] == base
    assert not agent.status()['application_profile']['automatic']
    assert agent.store.application_settings(device.state)['rules'] == []


def test_check_interval_and_unchanged_match_do_not_save_repeatedly(backend, monkeypatch):
    agent, device, actions, fg, base, target = backend
    fg['value'] = foreground(GAME_A); agent.update_application_profile(now=1., force=True)
    saved = []
    monkeypatch.setattr(agent.store, 'save', lambda *a, **kw: saved.append(True))
    readings = []
    monkeypatch.setattr('gamepadstudio.agent.foreground_application',
                        lambda: readings.append(True) or fg['value'])
    revision = agent.config['mapping_revision']
    agent.update_application_profile(now=1.1)
    assert not readings
    agent.update_application_profile(now=1.26)
    agent.update_application_profile(now=1.52)
    assert len(readings) == 2 and not saved
    assert agent.config['mapping_revision'] == revision


def test_disconnect_and_device_switch_do_not_borrow_application_rules(backend):
    agent, device, actions, fg, base, target = backend
    fg['value'] = foreground(GAME_A); agent.update_application_profile(now=1., force=True)
    device.state = controller('pad:two', 2); agent.poll()
    assert agent.config['profile_devices'][agent.config['active_profile']] == 'pad:two'
    assert not agent.status()['application_profile']['automatic']
    device.state = None; agent.poll()
    assert agent.status()['application_profile']['profile'] == agent.config['active_profile']
    assert agent.config['profile_devices'][agent.config['active_profile']] == 'offline:xinput'


def test_delayed_rule_editor_cannot_modify_a_newly_selected_controller(backend):
    agent, device, actions, fg, base, target = backend
    old_scope = profile_scope(device.state)
    device.state = controller('pad:two', 2); agent.poll()
    before = copy.deepcopy(agent.config)
    with pytest.raises(ValueError, match='输入设备已变化'):
        agent.handle({'command': 'mapping_change', 'device_scope': old_scope,
                      'change': {'op': 'application_profiles', 'settings': settings(target)}})
    assert agent.config == before

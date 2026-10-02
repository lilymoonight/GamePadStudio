"""Portable profiles are validated again by their authoritative device owner."""
import copy
import os
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from gamepadstudio.agent import Agent
from gamepadstudio.mapping_engine import input_sources
from gamepadstudio.profile_transfer import export_profile, preview_profile_import
from gamepadstudio.studio_core import ConfigStore, is_nikki_profile, profile_scope
from tests.test_unified_mapping import Actions, entry


def controller(key='pad:one', instance=1, family='xbox'):
    return dict(device_key=key, profile_key=f'{family}:fixture', instance_id=instance,
                family=family, name='Fixture controller', buttons=[], axes=[0.] * 6,
                available_buttons=list(range(15)), available_axes=list(range(6)),
                touch=[], touchpad=False, led=False, rumble=False, power=-1,
                is_gamecontroller=True)


def package(name='我的暖暖备份', mappings=None, options=None):
    return {'format': 'gamepadstudio-profile', 'version': 1,
            'source': {'family': 'dualsense', 'input_kind': 'sdl_gamecontroller'},
            'profile': {'name': name, 'mode': 'kbm',
                        'mappings': mappings if mappings is not None else {
                            '0': entry('hold', 'Space'), '0+9': entry('shortcut', 'Ctrl+1')},
                        'options': options if options is not None else {
                            'right_stick_mouse': True, 'mouse': {'sensitivity': 24., 'deadzone': .09},
                            'input': {'stick_press': .24, 'stick_release': .17}}}}


def command(value, state, **extra):
    return {'op': 'import_profile', 'package': value,
            'expected_inputs': preview_profile_import(value, state)['input_signature'], **extra}


def seeded_store(path, state=None):
    state = state or controller()
    store = ConfigStore(path)
    store.activate_controller(state)
    store.set_setting('touch_gestures_enabled', False, state)
    store.set_setting('led', '#123456', state)
    store.data['application_profiles'][profile_scope(state)] = {
        'enabled': True, 'rules': [{'executable': r'D:\Games\Game.exe',
                                   'profile': store.data['active_profile']}]}
    store.save()
    return store, state


def test_import_creates_one_device_owned_inactive_profile_and_preserves_preferences(tmp_path):
    store, state = seeded_store(tmp_path)
    before = copy.deepcopy(store.data)
    value = package()
    original = copy.deepcopy(value)
    result = store.apply_mapping_change(command(value, state), state)
    name = store.last_imported_profile
    assert name == value['profile']['name']
    assert set(result['profiles']) - set(before['profiles']) == {name}
    assert result['profiles'][name] == preview_profile_import(value, state)['profile']['mappings']
    assert result['profile_modes'][name] == 'kbm'
    assert result['profile_devices'][name] == profile_scope(state)
    assert result['profile_families'][name] == state['family']
    assert result['profile_options'][name] == preview_profile_import(value, state)['profile']['options']
    assert result['mapping_revision'] == before['mapping_revision'] + 1
    for field in ('active_profile', 'controller_profiles', 'device_settings', 'application_profiles',
                  'profile_sources', 'nikki_profile_layouts', 'nikki_touch_settings_initialized',
                  'save_dir', 'gamebar_shield_enabled', 'gamebar_shield_backup'):
        assert result[field] == before[field]
    assert not is_nikki_profile(result, name)
    assert value == original
    loaded = ConfigStore(tmp_path)
    assert loaded.data['profiles'][name] == result['profiles'][name]
    assert loaded.data['active_profile'] == before['active_profile']
    assert loaded.data['profile_sources'] == before['profile_sources']


@pytest.mark.parametrize('invert_y', [False, True])
def test_authoritative_import_preserves_invert_y_after_restart_and_reexport(tmp_path, invert_y):
    store, state = seeded_store(tmp_path)
    value = package()
    value['profile']['options']['mouse']['invert_y'] = invert_y
    store.apply_mapping_change(command(value, state), state)
    name = store.last_imported_profile
    loaded = ConfigStore(tmp_path)
    assert loaded.data['profile_options'][name]['mouse']['invert_y'] is invert_y
    exported = export_profile(loaded.data, state, name)
    assert exported['profile']['options']['mouse']['invert_y'] is invert_y


@pytest.mark.parametrize('value', [0, 1, 0., 1., 'true', 'false', '0', '1', None, [], {}])
def test_core_rejects_non_boolean_invert_y_without_saving_changes(tmp_path, value):
    store, state = seeded_store(tmp_path)
    name = store.profiles_for(state, mode='kbm')[0]
    before, disk = copy.deepcopy(store.data), store.path.read_bytes()
    with pytest.raises(ValueError, match='Y 轴反转'):
        store.apply_mapping_change({'op': 'options', 'profile': name,
                                    'options': {'mouse': {'invert_y': value}}}, state)
    assert store.data == before and store.path.read_bytes() == disk


def test_y_inversion_belongs_to_each_device_preset_and_survives_switching(tmp_path):
    store, first = seeded_store(tmp_path)
    first_name = store.profiles_for(first, mode='kbm')[0]
    mouse = dict(store.data['profile_options'][first_name]['mouse'], invert_y=True)
    store.apply_mapping_change({'op': 'options', 'profile': first_name,
                                'options': {'mouse': mouse}}, first)
    second = controller('pad:two', 2)
    store.activate_controller(second)
    second_name = store.profiles_for(second, mode='kbm')[0]
    assert second_name != first_name
    assert store.data['profile_options'][second_name]['mouse']['invert_y'] is False
    store.apply_mapping_change({'op': 'select', 'profile': second_name}, second)
    store.activate_controller(first)
    store.apply_mapping_change({'op': 'select', 'profile': first_name}, first)
    loaded = ConfigStore(tmp_path)
    assert loaded.data['profile_options'][first_name]['mouse']['invert_y'] is True
    assert loaded.data['profile_options'][second_name]['mouse']['invert_y'] is False
    assert loaded.data['active_profile'] == first_name


def test_reset_defaults_turns_y_inversion_off_without_changing_other_presets(tmp_path):
    store, state = seeded_store(tmp_path)
    name = store.profiles_for(state, mode='kbm')[0]
    store.apply_mapping_change({'op': 'options', 'profile': name,
                                'options': {'mouse': {'invert_y': True}}}, state)
    value = package()
    value['profile']['options']['mouse']['invert_y'] = True
    store.apply_mapping_change(command(value, state), state)
    imported = store.last_imported_profile
    store.apply_mapping_change({'op': 'reset', 'profile': name}, state)
    assert store.data['profile_options'][name]['mouse']['invert_y'] is False
    assert store.data['profile_options'][imported]['mouse']['invert_y'] is True
    store.apply_mapping_change({'op': 'reset', 'profile': imported}, state)
    assert store.data['profile_options'][imported].get('mouse', {}).get('invert_y', False) is False
    assert ConfigStore(tmp_path).data['profile_options'][name]['mouse']['invert_y'] is False


def test_backend_generates_readable_unique_names_without_overwriting_profiles(tmp_path):
    store, state = seeded_store(tmp_path)
    value = package()
    request = command(value, state)
    store.apply_mapping_change(request, state)
    original = copy.deepcopy(store.data['profiles'][value['profile']['name']])
    store.apply_mapping_change(request, state)
    assert store.last_imported_profile == '我的暖暖备份 · 导入 2'
    store.apply_mapping_change(request, state)
    assert store.last_imported_profile == '我的暖暖备份 · 导入 3'
    assert store.data['profiles'][value['profile']['name']] == original


def test_user_can_rename_the_import_and_duplicate_long_names_stay_within_limit(tmp_path):
    store, state = seeded_store(tmp_path)
    name = '暖' * 80
    request = command(package(), state, name='  ' + name + '  ')
    store.apply_mapping_change(request, state)
    assert store.last_imported_profile == name
    store.apply_mapping_change(request, state)
    assert len(store.last_imported_profile) == 80
    assert store.last_imported_profile.endswith(' · 导入 2')


@pytest.mark.parametrize('name', ['', ' ', None, [], 'X' * 81, 'line\nline', 'tab\tname', 'del\x7fname',
                                  '\ud800name', 'name\udfff'])
def test_invalid_requested_names_do_not_change_memory_or_disk(tmp_path, name):
    store, state = seeded_store(tmp_path)
    before, disk = copy.deepcopy(store.data), store.path.read_bytes()
    with pytest.raises(ValueError):
        store.apply_mapping_change(command(package(), state, name=name), state)
    assert store.data == before and store.path.read_bytes() == disk


def test_import_is_revalidated_and_capability_filter_is_not_trusted_from_preview(tmp_path):
    store, state = seeded_store(tmp_path)
    value = package(mappings={'0': entry('hold', 'Space'),
                              '20': entry('shortcut', 'P'),
                              'TP:swipe_up': entry('shortcut', 'U')})
    preview = preview_profile_import(value, state)
    assert len(preview['skipped']) == 2
    request = command(value, state)
    request['accepted'] = [{'trigger': '20', 'mapping': entry('hold', 'Q')}]
    store.apply_mapping_change(request, state)
    assert set(store.data['profiles'][store.last_imported_profile]) == {'0'}


@pytest.mark.parametrize('mutate', ['inputs', 'family', 'input_kind', 'model', 'missing'])
def test_stale_or_incomplete_preview_signature_is_rejected_atomically(tmp_path, mutate):
    store, state = seeded_store(tmp_path)
    request = command(package(), state)
    if mutate == 'missing':
        request.pop('expected_inputs')
    elif mutate == 'inputs':
        request['expected_inputs']['inputs'].append('20')
    else:
        request['expected_inputs'][mutate] = 'changed'
    before, disk = copy.deepcopy(store.data), store.path.read_bytes()
    with pytest.raises(ValueError, match='重新预览'):
        store.apply_mapping_change(request, state)
    assert store.data == before and store.path.read_bytes() == disk


@pytest.mark.parametrize('action', ['launch', 'gamepad_macro'])
def test_dangerous_package_actions_are_rejected_before_capability_filtering(tmp_path, action):
    store, state = seeded_store(tmp_path)
    value = package(mappings={'63': {'short': {'action': action, 'executable': 'calc.exe',
                                               'sequence': [{'button': '0'}]},
                                     'long': {'action': 'none'}}})
    before, disk = copy.deepcopy(store.data), store.path.read_bytes()
    with pytest.raises(ValueError):
        store.apply_mapping_change({'op': 'import_profile', 'package': value,
                                    'expected_inputs': {'inputs': input_sources(state)}}, state)
    assert store.data == before and store.path.read_bytes() == disk


@pytest.mark.parametrize('state', [None, {}, {'device_key': 'pad:one', 'family': 'xbox'}])
def test_import_requires_a_connected_device_snapshot(tmp_path, state):
    store, real = seeded_store(tmp_path)
    before = copy.deepcopy(store.data)
    with pytest.raises(ValueError, match='请先连接'):
        store.apply_mapping_change(command(package(), real), state)
    assert store.data == before


def test_failed_atomic_save_restores_the_store_and_keeps_the_existing_file(tmp_path, monkeypatch):
    store, state = seeded_store(tmp_path)
    before = copy.deepcopy(store.data)
    baseline = copy.deepcopy(store._baseline)
    disk = store.path.read_bytes()
    def failed_replace(*args):
        raise OSError('disk refused replacement')
    monkeypatch.setattr('gamepadstudio.studio_core.os.replace', failed_replace)
    with pytest.raises(OSError, match='replacement'):
        store.apply_mapping_change(command(package(), state), state)
    assert store.data == before and store._baseline == baseline
    assert store.path.read_bytes() == disk
    assert not hasattr(store, 'last_imported_profile')


def test_import_survives_a_stale_ui_saving_an_unrelated_setting(tmp_path):
    store, state = seeded_store(tmp_path)
    stale = ConfigStore(tmp_path)
    store.apply_mapping_change(command(package(), state), state)
    name = store.last_imported_profile
    stale.set_setting('cooldown', 1.2); stale.save()
    loaded = ConfigStore(tmp_path)
    assert loaded.data['profiles'][name] == store.data['profiles'][name]
    assert loaded.data['profile_devices'][name] == profile_scope(state)
    assert loaded.data['cooldown'] == 1.2


def test_stale_import_snapshot_does_not_overwrite_a_recent_preset(tmp_path):
    store, state = seeded_store(tmp_path)
    stale = ConfigStore(tmp_path)
    store.apply_mapping_change(command(package(), state), state)
    first = store.last_imported_profile
    imported = copy.deepcopy(store.data['profiles'][first])
    previous_revision = store.data['mapping_revision']
    value = package(mappings={'0': entry('hold', 'Q')})
    stale.apply_mapping_change(command(value, state), state)
    assert stale.last_imported_profile == '我的暖暖备份 · 导入 2'
    loaded = ConfigStore(tmp_path)
    assert loaded.data['profiles'][first] == imported
    assert loaded.data['profiles'][stale.last_imported_profile]['0']['short']['value'] == 'Q'
    assert loaded.data['mapping_revision'] == previous_revision + 1


@pytest.fixture
def backend(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    class Device:
        state = controller()
        available = []
        def scan(self): pass
        def read(self): return self.state
        def close(self): pass
    device, actions = Device(), Actions()
    monkeypatch.setattr('gamepadstudio.agent.foreground_application',
                        lambda: {'pid': 100, 'hwnd': 50, 'executable': r'C:\Windows\explorer.exe'})
    monkeypatch.setattr(Agent, 'apply_device_cloaking', lambda _: None)
    agent = Agent(tmp_path, device, actions)
    agent.timer.stop(); agent.scan_timer.stop(); agent.broadcast_timer.stop()
    agent._user32 = SimpleNamespace(GetAsyncKeyState=lambda _: 0)
    agent._last_prtsc_down = False
    agent.poll()
    active = agent.config['active_profile']
    agent.config['profiles'][active] = {'0': entry('hold', 'W')}
    agent.store.save()
    agent.engine.update(device.state, agent.config, now=0.)
    try:
        yield agent, device, actions
    finally:
        agent.close()


def request_import(agent, state, value=None, **extra):
    return {'command': 'mapping_change', 'device_scope': profile_scope(state),
            'instance_id': state['instance_id'],
            'change': command(value or package(), state), **extra}


def test_agent_import_releases_held_output_and_does_not_activate_or_override(backend, monkeypatch):
    agent, device, actions = backend
    active = agent.config['active_profile']
    baseline = copy.deepcopy(agent.config['controller_profiles'])
    status = copy.deepcopy(agent.application_profile)
    device.state['buttons'] = [0]
    agent.engine.update(device.state, agent.config, now=.1)
    assert actions.keys[ord('W')] == 1
    manual = []
    monkeypatch.setattr(agent.application_resolver, 'manual_selection', lambda *a, **kw: manual.append(True))
    result = agent.handle(request_import(agent, device.state))
    assert result['imported_profile'] in result['config']['profiles']
    assert result['config']['active_profile'] == active
    assert result['config']['controller_profiles'] == baseline
    assert not any(actions.keys.values()) and not manual
    assert agent.application_profile == status
    assert agent.status()['profile'] == active


@pytest.mark.parametrize('changed', ['scope', 'instance', 'missing_scope', 'missing_instance', 'boolean_instance'])
def test_agent_rejects_old_or_missing_device_confirmation_before_releasing_output(backend, monkeypatch, changed):
    agent, device, actions = backend
    message = request_import(agent, device.state)
    if changed == 'scope': message['device_scope'] = 'pad:another'
    elif changed == 'instance': message['instance_id'] += 1
    elif changed == 'missing_scope': message.pop('device_scope')
    elif changed == 'missing_instance': message.pop('instance_id')
    else: message['instance_id'] = True
    before = copy.deepcopy(agent.config)
    released = []
    monkeypatch.setattr(agent, 'release', lambda: released.append(True))
    with pytest.raises(ValueError, match='输入设备已变化'):
        agent.handle(message)
    assert agent.config == before and not released


def test_agent_rejects_reconnection_with_the_same_physical_scope(backend):
    agent, device, actions = backend
    message = request_import(agent, device.state)
    device.state = controller(instance=2)
    agent.poll()
    before = copy.deepcopy(agent.config)
    with pytest.raises(ValueError, match='输入设备已变化'):
        agent.handle(message)
    assert agent.config == before


def test_agent_import_does_not_resume_paused_mapping(backend):
    agent, device, actions = backend
    agent.handle({'command': 'pause'})
    result = agent.handle(request_import(agent, device.state))
    assert result['imported_profile'] in result['config']['profiles']
    assert result['config']['mapping_enabled'] is False and not agent.enabled


def test_agent_rejects_import_after_disconnection(backend):
    agent, device, actions = backend
    message = request_import(agent, device.state)
    device.state = None; agent.poll()
    with pytest.raises(ValueError):
        agent.handle(message)

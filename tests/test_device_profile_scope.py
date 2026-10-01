"""Physical controllers own independent mappings and hardware preferences."""
import copy
import json

import pytest

from gamepadstudio.kbm_mapper import NIKKI_PROFILE_NAME
from gamepadstudio.studio_core import (ConfigStore, DEVICE_SETTING_DEFAULTS,
                                      OFFLINE_PROFILE_SCOPE, device_config,
                                      is_nikki_profile, profile_scope)


def controller(serial, family='xbox', available=None):
    model = f'{family}:045e:0b13:driver'
    return {'family': family, 'profile_key': model, 'model_key': model,
            'device_key': f'{model}:serial:{serial}',
            'available_buttons': list(range(15)) if available is None else available}


def keyboard_profile(store, state):
    names = store.profiles_for(state, 'kbm')
    return next(name for name in names if is_nikki_profile(store.data, name))


def test_scope_uses_physical_identity_and_offline_xinput():
    assert profile_scope(None) == OFFLINE_PROFILE_SCOPE
    assert profile_scope({'device_key': 'physical', 'profile_key': 'model', 'family': 'xbox'}) == 'physical'
    assert profile_scope({'profile_key': 'model', 'family': 'xbox'}) == 'model'
    assert profile_scope({'family': 'xbox'}) == 'xbox'


def test_disconnected_preview_hides_saved_playstation_profiles(tmp_path):
    store = ConfigStore(tmp_path)
    ps = controller('ps', 'dualsense', list(range(21)))
    store.activate_controller(ps)
    ps_names = set(store.profiles_for(ps))
    store.activate_controller(None)
    offline = store.profiles_for(None)
    assert NIKKI_PROFILE_NAME in offline and 'XInput · 默认' in offline
    assert not ps_names.intersection(offline)
    assert store.data['active_profile'] in offline
    assert set(store.data['profiles']) >= ps_names | set(offline)


def test_identical_models_never_share_keyboard_edits_or_operation_feel(tmp_path):
    store = ConfigStore(tmp_path)
    first, second = controller('one'), controller('two')
    store.activate_controller(first)
    first_name = keyboard_profile(store, first)
    store.apply_mapping_change({'op': 'binding', 'profile': first_name, 'trigger': '0',
                                'mapping': {'short': {'action': 'hold', 'value': 'Enter'}}}, first)
    store.apply_mapping_change({'op': 'options', 'profile': first_name,
                                'options': {'mouse': {'sensitivity': 42}, 'input': {'stick_press': .40}}}, first)
    store.activate_controller(second)
    second_name = keyboard_profile(store, second)
    assert first_name != second_name
    assert store.data['profiles'][second_name]['0']['short']['value'] == 'Space'
    assert store.data['profile_options'][second_name]['mouse']['sensitivity'] == 24
    assert store.data['profile_options'][second_name]['input']['stick_press'] == .24
    assert store.data['profiles'][NIKKI_PROFILE_NAME]['0']['short']['value'] == 'Space'
    assert not set(store.profiles_for(first)).intersection(store.profiles_for(second))
    store.activate_controller(first)
    assert store.data['profiles'][first_name]['0']['short']['value'] == 'Enter'
    assert store.data['profile_options'][first_name]['mouse']['sensitivity'] == 42


@pytest.mark.parametrize('operation', [
    {'op': 'select'}, {'op': 'reset'}, {'op': 'delete'},
    {'op': 'options', 'options': {'mouse': {'sensitivity': 100}}},
    {'op': 'binding', 'trigger': '0', 'mapping': {'short': {'action': 'hold', 'value': 'Enter'}}},
    {'op': 'unbind', 'trigger': '0'},
])
def test_commands_cannot_write_another_physical_controllers_profile(tmp_path, operation):
    store = ConfigStore(tmp_path)
    first, second = controller('one'), controller('two')
    store.activate_controller(first)
    name = keyboard_profile(store, first)
    store.activate_controller(second)
    snapshot = copy.deepcopy(store.data)
    with pytest.raises(ValueError, match='不属于当前输入设备'):
        store.apply_mapping_change(dict(operation, profile=name), second)
    assert store.data == snapshot


def test_create_cannot_copy_another_controllers_profile(tmp_path):
    store = ConfigStore(tmp_path)
    first, second = controller('one'), controller('two')
    store.activate_controller(first)
    source = keyboard_profile(store, first)
    store.activate_controller(second)
    with pytest.raises(ValueError, match='不属于当前输入设备'):
        store.apply_mapping_change({'op': 'create', 'profile': 'stolen', 'source': source}, second)
    assert 'stolen' not in store.data['profiles']


def test_controller_remembers_all_its_profiles_and_selection_after_restart(tmp_path):
    store = ConfigStore(tmp_path)
    first, second = controller('one'), controller('two')
    store.activate_controller(first)
    native = store.data['active_profile']
    store.apply_mapping_change({'op': 'create', 'profile': 'Personal gamepad', 'source': native}, first)
    store.apply_mapping_change({'op': 'create', 'profile': 'Personal keyboard',
                                'source': keyboard_profile(store, first)}, first)
    store.activate_controller(second)
    store.save()
    loaded = ConfigStore(tmp_path)
    loaded.activate_controller(first)
    assert loaded.data['active_profile'] == 'Personal keyboard'
    assert {'Personal gamepad', 'Personal keyboard'} <= set(loaded.profiles_for(first))
    assert 'Personal keyboard' not in loaded.profiles_for(second)


def test_hardware_options_are_independent_and_application_settings_are_shared(tmp_path):
    store = ConfigStore(tmp_path)
    first, second = controller('one'), controller('two')
    store.activate_controller(first)
    store.set_setting('rumble', .85, first)
    store.set_setting('deadzone', .25, first)
    store.set_setting('led', '#abcabc', first)
    store.set_setting('replay_fps', 60, first)
    store.set_setting('close_to_tray', True, first)
    store.activate_controller(second)
    assert store.settings_for(second) == DEVICE_SETTING_DEFAULTS
    assert store.settings_for(first)['deadzone'] == .25
    assert device_config(store.data, first)['rumble'] == .85
    assert device_config(store.data, second)['rumble'] == .35
    assert device_config(store.data, None)['led'] == '#5686ff'
    assert store.data['replay_fps'] == 60 and store.data['close_to_tray']
    store.save()
    assert ConfigStore(tmp_path).settings_for(first)['led'] == '#abcabc'


def test_uninitialized_and_disconnected_devices_do_not_borrow_global_hardware_options():
    values = {'rumble': 1, 'deadzone': .4, 'touch_mouse': True,
              'device_settings': {'other': {'rumble': .9, 'deadzone': .3}}}
    assert device_config(values, controller('new'))['rumble'] == .35
    assert device_config(values, None)['deadzone'] == .10
    assert device_config(values, None)['touch_mouse'] is False


def test_old_shared_profile_is_backed_up_and_adopted_then_cloned(tmp_path):
    old = ConfigStore(tmp_path).data
    old.pop('device_profile_version')
    old.pop('profile_devices')
    old.pop('profile_sources')
    old['profiles']['Shared custom'] = {'0': {'short': {'action': 'hold', 'value': 'Enter'}}}
    old['profile_modes']['Shared custom'] = 'gamepad'
    old['profile_families']['Shared custom'] = 'xbox'
    old['profile_options']['Shared custom'] = {'mouse': {'sensitivity': 39}, 'input': {'stick_press': .41}}
    old['controller_profiles'] = {'xbox:first': 'Shared custom', 'xbox:second': 'Shared custom'}
    old['active_profile'] = 'Shared custom'
    old['rumble'], old['deadzone'] = .8, .23
    raw = json.dumps(old, ensure_ascii=False, indent=1).encode('utf-8')
    (tmp_path / 'studio.json').write_bytes(raw)
    store = ConfigStore(tmp_path)
    assert (tmp_path / 'studio.before-device-profiles-v1.json').read_bytes() == raw
    assert store.data['active_profile'] == 'Shared custom'
    first = {'family': 'xbox', 'profile_key': 'xbox:first', 'available_buttons': list(range(15))}
    second = {'family': 'xbox', 'profile_key': 'xbox:second', 'available_buttons': list(range(15))}
    store.activate_controller(first)
    assert store.data['active_profile'] == 'Shared custom'
    assert store.settings_for(first)['deadzone'] == .23
    store.activate_controller(second)
    clone = store.data['active_profile']
    assert clone != 'Shared custom'
    assert store.data['profiles'][clone] == old['profiles']['Shared custom']
    assert store.data['profile_options'][clone] == old['profile_options']['Shared custom']
    assert store.settings_for(second)['rumble'] == .35
    store.data['profiles'][clone]['0']['short']['value'] = 'Esc'
    assert store.data['profiles']['Shared custom']['0']['short']['value'] == 'Enter'


def test_model_selection_and_hardware_settings_are_adopted_by_one_physical_device(tmp_path):
    first, second = controller('one'), controller('two')
    old = ConfigStore(tmp_path).data
    old.pop('device_profile_version')
    old.pop('profile_devices')
    old['profiles']['Old Xbox'] = {'0': {'short': {'action': 'gamepad_button', 'value': '3'}}}
    old['profile_modes']['Old Xbox'] = 'gamepad'
    old['profile_families']['Old Xbox'] = 'xbox'
    old['active_profile'] = 'Old Xbox'
    old['controller_profiles'] = {first['model_key']: 'Old Xbox'}
    old['rumble'] = .75
    (tmp_path / 'studio.json').write_text(json.dumps(old), encoding='utf-8')
    store = ConfigStore(tmp_path)
    store.activate_controller(first)
    assert store.data['active_profile'] == 'Old Xbox'
    assert store.settings_for(first)['rumble'] == .75
    store.activate_controller(second)
    assert store.data['active_profile'] != 'Old Xbox'
    assert store.settings_for(second)['rumble'] == .35
    assert 'Old Xbox' not in store.profiles_for(second)


def test_concurrent_device_edits_and_preferences_merge_without_cross_contamination(tmp_path):
    first, second = controller('one'), controller('two')
    initial = ConfigStore(tmp_path)
    initial.activate_controller(first)
    initial.activate_controller(second)
    initial.save()
    one, two = ConfigStore(tmp_path), ConfigStore(tmp_path)
    one.activate_controller(first)
    two.activate_controller(second)
    first_name, second_name = keyboard_profile(one, first), keyboard_profile(two, second)
    one.apply_mapping_change({'op': 'binding', 'profile': first_name, 'trigger': '0',
                              'mapping': {'short': {'action': 'hold', 'value': 'Enter'}}}, first)
    one.set_setting('rumble', .9, first)
    one.save()
    two.apply_mapping_change({'op': 'binding', 'profile': second_name, 'trigger': '0',
                              'mapping': {'short': {'action': 'hold', 'value': 'Esc'}}}, second)
    two.set_setting('rumble', .2, second)
    two.save()
    loaded = ConfigStore(tmp_path)
    assert loaded.data['profiles'][first_name]['0']['short']['value'] == 'Enter'
    assert loaded.data['profiles'][second_name]['0']['short']['value'] == 'Esc'
    assert loaded.settings_for(first)['rumble'] == .9
    assert loaded.settings_for(second)['rumble'] == .2


def test_deleting_a_profile_only_updates_current_device_selection(tmp_path):
    store = ConfigStore(tmp_path)
    first, second = controller('one'), controller('two')
    store.activate_controller(first)
    first_default = store.data['active_profile']
    store.apply_mapping_change({'op': 'create', 'profile': 'Disposable', 'source': first_default}, first)
    store.activate_controller(second)
    second_active = store.data['active_profile']
    store.activate_controller(first)
    store.delete_profile('Disposable', first)
    assert store.data['controller_profiles'][profile_scope(second)] == second_active
    assert 'Disposable' not in store.data['profiles']

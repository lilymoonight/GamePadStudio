"""The researched preset must work as a whole, including upgrades and release."""
import json
import pytest

from gamepadstudio.kbm_mapper import NIKKI_PROFILE_NAME, NIKKI_LAYOUT_VERSION, infinity_nikki_defaults
from gamepadstudio.mapping_engine import MappingRuntime, validate_mappings
from gamepadstudio.studio_core import ConfigStore, NIKKI_PROFILE_OPTIONS, default_config
from tests.test_unified_mapping import Actions, frame as input_frame


def frame(*args, **kwargs):
    return dict(input_frame(*args, **kwargs), family='dualsense', device_key='dualsense:nikki-test')


def setup_runtime(tmp_path):
    store = ConfigStore(tmp_path)
    state = dict(family='dualsense', device_key='dualsense:nikki-test', available_buttons=list(range(21)))
    store.activate_controller(state)
    store.remember_profile(state, store.profiles_for(state, 'kbm')[0])
    actions, dispatches = Actions(), []
    runtime = MappingRuntime(actions, lambda binding, down: dispatches.append((binding, down)), start_mouse=False)
    return store, runtime, actions, dispatches


def test_researched_keys_are_accessible_without_using_mouse_triggers_as_modifiers():
    mappings = validate_mappings(infinity_nikki_defaults('dualsense'))
    keys = {binding['value'] for entry in mappings.values()
            for gesture in ('short', 'long')
            if (binding := entry[gesture]).get('action') in ('hold', 'shortcut')}
    assert {'W', 'A', 'S', 'D', 'Space', 'Shift', 'F', 'E', 'Q', 'R', 'G', 'T',
            'Tab', 'Ctrl', 'Alt', 'Esc', 'V', 'Z', 'X', 'M', 'P', 'F12', 'C', 'N',
            'B', 'L', 'U', 'Y', 'I', 'O', 'Enter', 'F10', 'K', 'J', 'H', 'CapsLock',
            *map(str, range(1, 9)), *(f'F{i}' for i in range(1, 8))} <= keys
    assert not any('LT' in trigger.split('+') or 'RT' in trigger.split('+')
                   for trigger in mappings if '+' in trigger)
    assert mappings['LS:outer']['short']['action'] == 'none'


def test_triggers_are_immediate_holds_and_ability_modifier_does_not_steal_them(tmp_path):
    store, runtime, actions, dispatches = setup_runtime(tmp_path)
    runtime.update(frame([9], [0, 0, 0, 0, .31, .31]), store.data, now=0)
    assert actions.mouse == {'left', 'right'}
    assert not dispatches
    runtime.update(frame([9], [0, 0, 0, 0, .23, .23]), store.data, now=.01)
    assert actions.mouse == {'left', 'right'}
    runtime.update(frame([9], [0, 0, 0, 0, .19, .19]), store.data, now=.02)
    assert not actions.mouse
    runtime.update(frame(), store.data, now=.05)
    assert not any(actions.keys.values())


def test_wheel_is_held_while_selecting_with_mouse_and_never_releases_a_digit(tmp_path):
    store, runtime, actions, _ = setup_runtime(tmp_path)
    runtime.update(frame([11]), store.data, now=0)
    runtime.update(frame([11]), store.data, now=.06)
    runtime.update(frame([11], [0, 0, .4, 0, 0, .31]), store.data, now=.20)
    assert ('key', 'Tab', True) in actions.calls
    assert actions.mouse == {'left'}
    runtime.update(frame(), store.data, now=.24)
    assert ('key', 'Tab', False) in actions.calls
    assert not any(actions.keys.values()) and not actions.mouse
    assert not any(call[:2] == ('key', '5') for call in actions.calls)


def test_short_ability_and_long_clothes_are_exclusive_with_no_jump_or_modifier_leak(tmp_path):
    store, runtime, actions, _ = setup_runtime(tmp_path)
    runtime.update(frame([9]), store.data, now=0)
    runtime.update(frame([9, 0]), store.data, now=.10)
    runtime.update(frame([9, 0]), store.data, now=.61)
    runtime.update(frame([0]), store.data, now=.62)
    runtime.update(frame(), store.data, now=.70)
    assert ('key', 'F1', True) in actions.calls
    assert not any(call[:2] in (('key', '1'), ('key', 'Space'), ('key', 'Tab')) for call in actions.calls)
    actions.calls.clear()
    runtime.update(frame([9]), store.data, now=1)
    runtime.update(frame([9, 2]), store.data, now=1.1)
    runtime.update(frame([9]), store.data, now=1.2)
    runtime.update(frame([9]), store.data, now=1.3)
    runtime.update(frame(), store.data, now=1.4)
    assert ('key', '2', True) in actions.calls
    assert not any(call[:2] in (('key', 'F2'), ('key', 'F')) for call in actions.calls)
    assert not any(actions.keys.values())


def test_light_walk_then_normal_run_then_manual_sprint_releases_ctrl(tmp_path):
    store, runtime, actions, _ = setup_runtime(tmp_path)
    runtime.update(frame(axes=[0, -.4, 0, 0, 0, 0]), store.data, now=0)
    assert ('key', 'W', True) in actions.calls and ('key', 'Ctrl', True) in actions.calls
    runtime.update(frame(axes=[0, -.8, 0, 0, 0, 0]), store.data, now=.1)
    assert actions.keys[17] == 0 and actions.keys[87] == 1
    runtime.update(frame(axes=[0, -1, 0, 0, 0, 0]), store.data, now=.2)
    assert ('key', 'Shift', True) not in actions.calls
    runtime.update(frame([1], [0, -1, 0, 0, 0, 0]), store.data, now=.3)
    runtime.update(frame([1], [0, -1, 0, 0, 0, 0]), store.data, now=.36)
    assert ('key', 'Shift', True) in actions.calls
    runtime.update(frame(), store.data, now=.4)
    assert not any(actions.keys.values())


def test_backed_up_upgrade_only_replaces_dedicated_preset_and_does_not_repeat(tmp_path):
    old = default_config(tmp_path)
    old.pop('nikki_layout_version')
    old['active_profile'] = '主机体验'
    old['profiles'][NIKKI_PROFILE_NAME] = {'RT': {'short': {'action': 'mouse_hold', 'value': 'middle'},
                                               'long': {'action': 'none'}}}
    old['profiles']['Personal keyboard'] = {'0': {'short': {'action': 'hold', 'value': 'Ctrl+S'},
                                                 'long': {'action': 'none'}}}
    old['profile_modes']['Personal keyboard'] = 'kbm'
    old['profile_options']['Personal keyboard'] = {'mouse': {'sensitivity': 9}}
    old['controller_profiles'] = {'xbox:personal': '主机体验'}
    raw = json.dumps(old, ensure_ascii=False, indent=1).encode('utf-8')
    (tmp_path / 'studio.json').write_bytes(raw)
    store = ConfigStore(tmp_path)
    backup = tmp_path / f'studio.before-nikki-layout-v{NIKKI_LAYOUT_VERSION}.json'
    assert backup.read_bytes() == raw
    assert store.data['nikki_layout_version'] == NIKKI_LAYOUT_VERSION
    assert store.data['profiles'][NIKKI_PROFILE_NAME] == infinity_nikki_defaults('dualsense', touch_inputs=[])
    assert store.data['profile_options'][NIKKI_PROFILE_NAME] == NIKKI_PROFILE_OPTIONS
    assert store.data['active_profile'] == old['active_profile']
    assert store.data['controller_profiles'] == old['controller_profiles']
    assert store.data['profiles']['Personal keyboard'] == old['profiles']['Personal keyboard']
    assert store.data['profile_options']['Personal keyboard'] == old['profile_options']['Personal keyboard']
    store.data['profiles'][NIKKI_PROFILE_NAME]['0']['short']['value'] = 'Enter'
    store.data['profile_options'][NIKKI_PROFILE_NAME]['mouse']['sensitivity'] = 32
    store.save()
    loaded = ConfigStore(tmp_path)
    assert loaded.data['profiles'][NIKKI_PROFILE_NAME]['0']['short']['value'] == 'Enter'
    assert loaded.data['profile_options'][NIKKI_PROFILE_NAME]['mouse']['sensitivity'] == 32
    assert backup.read_bytes() == raw


def test_stale_editor_cannot_restore_layout_before_upgrade(tmp_path):
    stale = ConfigStore(tmp_path)
    stale.data.pop('nikki_layout_version')
    stale.data['profiles'][NIKKI_PROFILE_NAME]['0']['short']['value'] = 'F12'
    stale.save(merge=False)
    ConfigStore(tmp_path)
    stale.data['profiles'][NIKKI_PROFILE_NAME]['0']['short']['value'] = 'Ctrl+S'
    stale.data['profile_options'][NIKKI_PROFILE_NAME]['mouse']['sensitivity'] = 2
    stale.data['rumble'] = .2
    stale.save()
    loaded = ConfigStore(tmp_path)
    assert loaded.data['profiles'][NIKKI_PROFILE_NAME] == infinity_nikki_defaults('dualsense', touch_inputs=[])
    assert loaded.data['profile_options'][NIKKI_PROFILE_NAME] == NIKKI_PROFILE_OPTIONS
    assert loaded.data['rumble'] == .2


def test_restricted_controller_does_not_get_unreachable_chords():
    mappings = infinity_nikki_defaults('generic', [0, 1, 2, 3, 6])
    assert not any('+' in trigger for trigger in mappings)
    assert all(str(button) in mappings for button in (0, 1, 2, 3, 6))


@pytest.mark.parametrize('remaining', [(9,), (0,)])
def test_short_chord_finishes_on_either_release_order_and_keeps_survivor_silent(tmp_path, remaining):
    store, runtime, actions, dispatches = setup_runtime(tmp_path)
    runtime.update(frame([9]), store.data, now=0)
    runtime.update(frame([9, 0]), store.data, now=.10)
    runtime.update(frame(remaining), store.data, now=.20)
    assert ('key', '1', True) in actions.calls
    runtime.update(frame(remaining), store.data, now=.40)
    runtime.update(frame(), store.data, now=.50)
    assert not any(actions.keys.values())
    assert all(call[1] == '1' for call in actions.calls)
    assert not dispatches

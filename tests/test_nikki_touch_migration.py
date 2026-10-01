"""Nikki upgrades preserve edited fields and use confirmed touch capabilities."""
import copy
import json

import pytest

from gamepadstudio.kbm_mapper import (
    NIKKI_LAYOUT_VERSION, NIKKI_PROFILE_NAME, NIKKI_TOUCH_SETTINGS,
    infinity_nikki_defaults,
)
from gamepadstudio.studio_core import (
    ConfigStore, DEVICE_SETTING_DEFAULTS, NIKKI_PROFILE_OPTIONS,
    default_config, profile_scope,
)
from gamepadstudio.touch_gestures import touch_sources
from tests.test_touch_runtime import ds5
from tests.test_unified_mapping import entry


def keyboard(store, state):
    return store.profiles_for(state, 'kbm')[0]


def write_v2(root, profiles=None):
    old = default_config(root)
    old['nikki_layout_version'] = 2
    old.pop('nikki_profile_layouts', None)
    old.pop('nikki_touch_settings_initialized', None)
    old['profiles'][NIKKI_PROFILE_NAME] = infinity_nikki_defaults(
        'dualsense', layout_version=2)
    for name, state in (profiles or {}).items():
        old['profiles'][name] = infinity_nikki_defaults(state['family'],
            state['available_buttons'], layout_version=2)
        old['profile_sources'][name] = NIKKI_PROFILE_NAME
        old['profile_devices'][name] = profile_scope(state)
        old['profile_families'][name] = state['family']
        old['profile_modes'][name] = 'kbm'
        old['profile_options'][name] = copy.deepcopy(NIKKI_PROFILE_OPTIONS)
        old['controller_profiles'][profile_scope(state)] = name
        old.setdefault('device_profile_initialized', []).append(profile_scope(state))
        old['device_settings'][profile_scope(state)] = copy.deepcopy(DEVICE_SETTING_DEFAULTS)
    return old


def persist(root, config):
    raw = json.dumps(config, ensure_ascii=False, indent=2).encode('utf-8')
    (root / 'studio.json').write_bytes(raw)
    return raw


def test_fresh_nikki_template_waits_for_confirmed_touch_capability(tmp_path):
    store = ConfigStore(tmp_path)
    assert not any(key.startswith('TP:') for key in store.data['profiles'][NIKKI_PROFILE_NAME])
    no_touch = ds5(touchpad=False, touchpad_count=0, touchpad_fingers=0)
    store.activate_controller(no_touch)
    name = keyboard(store, no_touch)
    assert not any(key.startswith('TP:') for key in store.data['profiles'][name])
    assert store.data['profiles'][name]['0+4']['short']['value'] == 'C'
    assert not store.settings_for(no_touch)['touch_gestures_enabled']
    store.activate_controller(ds5())
    assert set(touch_sources(ds5())) <= set(store.data['profiles'][name])
    assert store.data['profiles'][name]['0+4']['short'] == {'action': 'none'}
    assert all(store.settings_for(ds5())[key] == value
               for key, value in NIKKI_TOUCH_SETTINGS.items())


def test_v2_upgrades_only_current_device_and_still_default_fields(tmp_path):
    first, second = ds5(), ds5(unit=2)
    old = write_v2(tmp_path, {'First': first, 'Copy': first, 'Second': second})
    mapping = old['profiles']['First']
    mapping['0']['short']['value'] = 'Enter'
    mapping['0+4']['short'] = {'action': 'shortcut', 'value': 'J'}
    mapping['2+4']['long'] = {'action': 'shortcut', 'value': 'Ctrl+L'}
    mapping['2+4']['long_press'] = .85
    mapping['3+4']['short'] = {'action': 'none'}
    mapping['TP:swipe_up'] = entry('shortcut', 'Z')
    mapping['TP:tap'] = entry('none')
    old['profile_options']['First']['mouse']['sensitivity'] = 43
    old['device_settings'][profile_scope(first)].update(touch_mouse=True,
                                                       touch_gesture_sensitivity=.83)
    old['profiles'][NIKKI_PROFILE_NAME]['0']['short']['value'] = 'F12'
    raw = persist(tmp_path, old)
    store = ConfigStore(tmp_path)
    backup = tmp_path / 'studio.before-nikki-layout-v3.json'
    assert backup.read_bytes() == raw
    assert store.data['nikki_layout_version'] == NIKKI_LAYOUT_VERSION
    assert store.data['profiles'][NIKKI_PROFILE_NAME]['0']['short']['value'] == 'F12'
    assert store.data['profiles']['Second'] == old['profiles']['Second']
    store.activate_controller(first)
    updated = store.data['profiles']['First']
    assert updated['0'] == old['profiles']['First']['0']
    assert updated['0+4']['short']['value'] == 'J'
    assert updated['2+4']['short'] == {'action': 'none'}
    assert updated['2+4']['long']['value'] == 'Ctrl+L'
    assert updated['2+4']['long_press'] == .85
    assert updated['3+4']['short'] == {'action': 'none'}
    assert updated['TP:swipe_up'] == old['profiles']['First']['TP:swipe_up']
    assert updated['TP:tap'] == old['profiles']['First']['TP:tap']
    assert store.data['profile_options']['First'] == old['profile_options']['First']
    assert store.settings_for(first)['touch_mouse']
    assert store.settings_for(first)['touch_gesture_sensitivity'] == .83
    assert set(touch_sources(first)) <= set(store.data['profiles']['Copy'])
    assert store.data['profiles']['Second'] == old['profiles']['Second']
    assert store.settings_for(second) == DEVICE_SETTING_DEFAULTS
    store.save()
    assert ConfigStore(tmp_path).data['profiles']['First'] == updated
    assert backup.read_bytes() == raw


def test_later_capability_installation_does_not_restore_unbound_gestures_or_enable_again(tmp_path):
    store = ConfigStore(tmp_path)
    one = ds5(touchpad_fingers=1, touch_finger_counts=[1])
    store.activate_controller(one)
    name = keyboard(store, one)
    assert len([key for key in store.data['profiles'][name] if key.startswith('TP:')]) == 7
    store.apply_mapping_change({'op': 'unbind', 'profile': name, 'trigger': 'TP:tap'}, one)
    store.set_setting('touch_gestures_enabled', False, one)
    store.save()
    loaded = ConfigStore(tmp_path)
    loaded.activate_controller(ds5())
    assert loaded.data['profiles'][name]['TP:tap']['short'] == {'action': 'none'}
    assert 'TP:scroll_up' in loaded.data['profiles'][name]
    assert not loaded.settings_for(ds5())['touch_gestures_enabled']
    loaded.data['profiles'][name].pop('TP:swipe_left')
    loaded.save()
    loaded.activate_controller(ds5())
    assert 'TP:swipe_left' not in loaded.data['profiles'][name]
    assert not loaded.settings_for(ds5())['touch_gestures_enabled']


def test_saved_touch_action_enables_only_current_device_and_not_another_profile(tmp_path):
    store = ConfigStore(tmp_path)
    first, second = ds5(), ds5(unit=2)
    store.activate_controller(first)
    native = store.data['active_profile']
    store.activate_controller(second)
    store.set_setting('touch_gestures_enabled', False, second)
    other = copy.deepcopy(store.settings_for(second))
    store.activate_controller(first)
    store.set_setting('touch_gestures_enabled', False, first)
    store.apply_mapping_change({'op': 'binding', 'profile': native,
                               'trigger': 'TP:scroll_up', 'mapping': entry('shortcut', 'Ctrl+S')}, first)
    assert store.settings_for(first)['touch_gestures_enabled']
    assert store.settings_for(second) == other
    assert store.data['active_profile'] == native
    assert ConfigStore(tmp_path).settings_for(first)['touch_gestures_enabled']


@pytest.mark.parametrize('source', ['TP:tap', 'TP:scroll_up', '20'])
def test_binding_rejects_inputs_the_current_device_does_not_report(tmp_path, source):
    store = ConfigStore(tmp_path)
    state = ds5(touchpad=False, touchpad_count=0, touchpad_fingers=0,
                available_buttons=list(range(15)))
    store.activate_controller(state)
    before = copy.deepcopy(store.data)
    with pytest.raises(ValueError, match='当前手柄支持'):
        store.apply_mapping_change({'op': 'binding', 'profile': keyboard(store, state),
                                    'trigger': source, 'mapping': entry('shortcut', 'S')}, state)
    assert store.data == before


def test_offline_preview_cannot_save_a_touch_source(tmp_path):
    store = ConfigStore(tmp_path)
    store.activate_controller(None)
    before = copy.deepcopy(store.data)
    with pytest.raises(ValueError, match='当前手柄支持'):
        store.apply_mapping_change({'op': 'binding', 'profile': NIKKI_PROFILE_NAME,
                                    'trigger': 'TP:tap', 'mapping': entry('shortcut', 'S')}, None)
    assert store.data == before


def test_reset_uses_current_capabilities_and_restores_recommended_touch_settings(tmp_path):
    store = ConfigStore(tmp_path)
    state = ds5()
    store.activate_controller(state)
    name = keyboard(store, state)
    for key, value in {'touch_gestures_enabled': False, 'touch_mouse': True,
                       'touch_scroll': True, 'touch_gesture_sensitivity': .9}.items():
        store.set_setting(key, value, state)
    store.apply_mapping_change({'op': 'reset', 'profile': name}, state)
    assert store.data['profiles'][name] == infinity_nikki_defaults(
        state['family'], state['available_buttons'], touch_inputs=touch_sources(state))
    assert all(store.settings_for(state)[key] == value for key, value in NIKKI_TOUCH_SETTINGS.items())
    assert store.data['nikki_profile_layouts'][name]['version'] == 3


@pytest.mark.parametrize('initial_version', [2, 3])
def test_stale_editor_cannot_undo_clone_upgrade_or_touch_settings(tmp_path, initial_version):
    state = ds5()
    old = write_v2(tmp_path, {'First': state})
    old['nikki_layout_version'] = initial_version
    if initial_version == 3:
        old['nikki_profile_layouts'] = {'First': {'version': 3, 'touch_inputs': []}}
    persist(tmp_path, old)
    stale = ConfigStore(tmp_path)
    upgraded = ConfigStore(tmp_path)
    upgraded.activate_controller(state)
    upgraded.save()
    expected = copy.deepcopy(upgraded.data['profiles']['First'])
    stale.data['profiles']['First']['0']['short']['value'] = 'Ctrl+S'
    stale.data['profile_options']['First']['mouse']['sensitivity'] = 2
    stale.set_setting('touch_gestures_enabled', False, state)
    stale.set_setting('rumble', .9, state)
    stale.save()
    loaded = ConfigStore(tmp_path)
    assert loaded.data['profiles']['First'] == expected
    assert loaded.data['profile_options']['First'] == upgraded.data['profile_options']['First']
    assert loaded.settings_for(state)['touch_gestures_enabled']
    assert loaded.settings_for(state)['rumble'] == .9
    assert loaded.data['nikki_profile_layouts']['First'] == upgraded.data['nikki_profile_layouts']['First']


def test_duplicate_nikki_profile_keeps_custom_bindings_and_capability_markers(tmp_path):
    store = ConfigStore(tmp_path)
    state = ds5()
    store.activate_controller(state)
    source = keyboard(store, state)
    store.apply_mapping_change({'op': 'binding', 'profile': source,
                               'trigger': 'TP:tap', 'mapping': entry('shortcut', 'Ctrl+S')}, state)
    store.set_setting('touch_gestures_enabled', False, state)
    store.apply_mapping_change({'op': 'create', 'profile': 'Personal Nikki', 'source': source}, state)
    assert store.data['profiles']['Personal Nikki'] == store.data['profiles'][source]
    assert store.data['profile_sources']['Personal Nikki'] == NIKKI_PROFILE_NAME
    assert store.data['nikki_profile_layouts']['Personal Nikki'] == store.data['nikki_profile_layouts'][source]
    assert not store.settings_for(state)['touch_gestures_enabled']


@pytest.mark.parametrize('touch_binding', [entry('none'), entry('shortcut', 'Z')])
def test_existing_custom_touch_menu_action_keeps_its_physical_menu_fallback(tmp_path, touch_binding):
    state = ds5()
    old = write_v2(tmp_path, {'First': state})
    old['profiles']['First']['TP:swipe_left'] = touch_binding
    persist(tmp_path, old)
    store = ConfigStore(tmp_path)
    store.activate_controller(state)
    assert store.data['profiles']['First']['TP:swipe_left'] == touch_binding
    assert store.data['profiles']['First']['0+4']['short'] == {'action': 'shortcut', 'value': 'C'}
    # An unchanged installed default still replaces its matching fallback.
    assert store.data['profiles']['First']['2+4']['short'] == {'action': 'none'}


def test_explicitly_disabled_physical_menu_binding_stays_disabled_during_upgrade(tmp_path):
    state = ds5()
    old = write_v2(tmp_path, {'First': state})
    old['profiles']['First']['TP:swipe_left'] = entry('none')
    old['profiles']['First']['0+4']['short'] = {'action': 'none'}
    persist(tmp_path, old)
    store = ConfigStore(tmp_path)
    store.activate_controller(state)
    assert store.data['profiles']['First']['0+4']['short'] == {'action': 'none'}


def test_confirmed_but_removed_touch_source_keeps_default_physical_fallback(tmp_path):
    state = ds5()
    old = write_v2(tmp_path, {'First': state})
    old['nikki_layout_version'] = 3
    old['nikki_profile_layouts'] = {'First': {'version': 3, 'touch_inputs': ['TP:swipe_left']}}
    persist(tmp_path, old)
    store = ConfigStore(tmp_path)
    store.activate_controller(state)
    assert 'TP:swipe_left' not in store.data['profiles']['First']
    assert store.data['profiles']['First']['0+4']['short'] == {'action': 'shortcut', 'value': 'C'}

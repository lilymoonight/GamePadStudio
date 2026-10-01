"""Hardware capabilities constrain source mappings without deleting saved work."""
import os

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import pytest
from PySide6.QtWidgets import QApplication

from gamepadstudio.device import Device
from gamepadstudio.mapping_engine import (InputNormalizer, canonical_trigger, effective_mappings,
                                        input_sources, profile_family, trigger_label)
from gamepadstudio.mapping_ui import BindingDialog
from tests.mapping_fixtures import MappingOwner


def device_state(family='xbox', instance=1, buttons=None, axes=None):
    return {'family': family, 'device_key': f'{family}:unit:{instance}', 'instance_id': instance,
            'available_buttons': list(range(15)) if buttons is None else buttons,
            'available_axes': list(range(6)) if axes is None else axes,
            'buttons': [], 'axes': [0.] * 6}


def test_offline_sources_and_labels_are_standard_xinput():
    sources = set(input_sources())
    assert {str(index) for index in range(15)} <= sources
    assert not {str(index) for index in range(15, 64)} & sources
    assert {'LT', 'RT', 'LS:up', 'RS:up', 'LS:inner', 'LS:outer'} <= sources
    assert profile_family({'profile_families': {'old': 'dualsense'}, 'active_profile': 'old'}) == 'generic'
    assert trigger_label('0+9') == 'A + LB'
    assert profile_family({}, {'family': 'unrecognized'}) == 'generic'


def test_connected_sources_follow_buttons_and_individual_axes():
    state = device_state('dualsense', buttons=[0, 4, 20], axes=[0, 1, 5])
    assert set(input_sources(state)) == {'0', '4', '20', 'LS:left', 'LS:right', 'LS:up', 'LS:down',
                                        'LS:inner', 'LS:outer', 'RT'}
    assert not input_sources(device_state(buttons=[], axes=[]))
    empty = device_state(buttons=[], axes=[])
    empty['axes'] = [1., 1., 1., 1., 1., 1.]
    assert not InputNormalizer().update(empty, {'walk_press': .62, 'walk_release': .72})
    assert '15' not in input_sources({'family': 'dualsense'})
    assert '20' not in input_sources({'family': 'dualsense'})


def test_raw_buttons_hat_and_axes_are_not_fabricated_as_gamepad_triggers():
    state = {'is_gamecontroller': False, 'available_buttons': [0, 2, 45], 'num_axes': 2,
             'num_hats': 1, 'axes': [.1, .2]}
    sources = set(input_sources(state))
    assert {'0', '2', '45', '11', '12', '13', '14', 'LS:inner'} <= sources
    assert not {'LT', 'RT', 'RS:up', '20', '15'} & sources
    assert canonical_trigger('45+2') == '2+45'


def test_unavailable_saved_mappings_stay_saved_but_cannot_fire():
    mappings = {'0': {'short': {'action': 'hold', 'value': 'Space'}},
                '0+20': {'short': {'action': 'hold', 'value': 'Z'}},
                'LT': {'short': {'action': 'hold', 'value': 'Alt'}},
                '45': {'short': {'action': 'hold', 'value': 'N'}}}
    config = {'active_profile': 'test', 'profiles': {'test': mappings}}
    state = device_state(buttons=[0], axes=[0, 1])
    assert effective_mappings(config, state) == {'0': mappings['0']}
    assert config['profiles']['test'] == mappings
    state.update(buttons=[0, 20], axes=[0, 0, 0, 0, 1., 1.])
    assert InputNormalizer().update(state) == {'0'}


def test_other_device_profile_cannot_be_displayed_or_executed():
    config = {'active_profile': 'foreign', 'profiles': {'foreign': {'0': {'short': {'action': 'capture'}}}},
              'profile_devices': {'foreign': 'dualsense:unit:1'}}
    assert effective_mappings(config, device_state('xbox')) == {}
    assert effective_mappings(config) == {}
    assert effective_mappings(config, device_state('dualsense')) == config['profiles']['foreign']


def test_runtime_uses_current_device_threshold_and_touch_preferences():
    from tests.test_unified_mapping import entry, runtime
    engine, config, actions, _ = runtime({'0': entry('hold', 'Space')})
    actions.moves = []
    actions.move_mouse = lambda dx, dy: actions.moves.append((dx, dy))
    config['long_press'] = .20
    config['touch_mouse'] = True
    config['device_settings'] = {'dualsense:unit:1': {'long_press': .80, 'touch_mouse': True},
                                 'dualsense:unit:2': {'long_press': .45, 'touch_mouse': False}}
    first, second = device_state('dualsense', instance=1), device_state('dualsense', instance=2)
    for state in (first, second):
        state.update(controller_type=7, vendor=0x054c, product=0x0ce6, is_gamecontroller=True,
                     touchpad=True, touchpad_count=1, touch_finger_counts=[2], touchpad_fingers=2,
                     touch_fingers=[], touch_valid=True)
    # Start at neutral, as execution does before observing a new contact.
    engine.update(first, config, now=0)
    assert engine.engine.threshold == .80
    first['touch_fingers'] = [{'pad': 0, 'finger': 0, 'contact': 1, 'x': .2, 'y': .2}]
    engine.update(first, config, now=.1)
    first['touch_fingers'][0].update(x=.22, y=.22)
    engine.update(first, config, now=.2)
    assert actions.moves
    actions.moves.clear()
    engine.update(second, config, now=.3)
    assert engine.engine.threshold == .45
    second['touch_fingers'] = [{'pad': 0, 'finger': 0, 'contact': 1, 'x': .22, 'y': .22}]
    engine.update(second, config, now=.4)
    second['touch_fingers'][0].update(x=.24, y=.24)
    engine.update(second, config, now=.5)
    assert not actions.moves


def test_other_device_profile_cannot_borrow_mouse_or_response_options():
    from types import SimpleNamespace
    from tests.test_unified_mapping import entry, runtime
    engine, config, actions, _ = runtime({'LT': entry('mouse_hold', 'left')})
    config['profile_devices'] = {'test': 'dualsense:unit:1'}
    config['profile_options'] = {'test': {'right_stick_mouse': True,
                                         'input': {'trigger_press': .25, 'trigger_release': .15}}}
    stick_updates = []
    engine.mouse_thread = SimpleNamespace(configure=lambda options: None,
                                         update_stick=lambda *values, **kwargs: stick_updates.append(values),
                                         set_click_lock=lambda value: None)
    state = device_state()
    state['axes'] = [0, 0, 1., 1., .30, 0]
    engine.update(state, config, now=0)
    assert engine.normalizer.thresholds['trigger_press'] == .55
    assert not actions.mouse
    assert stick_updates[-1] == (0., 0.)


@pytest.mark.parametrize('mode, expected', [('gamepad', (0., 0.)), ('kbm', (1., .8))])
def test_native_gamepad_mode_does_not_enable_pointer_without_keyboard_option(mode, expected):
    from types import SimpleNamespace
    from tests.test_unified_mapping import entry, runtime
    engine, config, actions, _ = runtime({'0': entry('capture')})
    config['profile_modes'] = {'test': mode}
    stick_updates = []
    engine.mouse_thread = SimpleNamespace(configure=lambda options: None,
                                         update_stick=lambda *values, **kwargs: stick_updates.append(values),
                                         set_click_lock=lambda value: None)
    state = device_state()
    state['axes'] = [0, 0, 1., .8, 0, 0]
    engine.update(state, config, now=0)
    assert stick_updates[-1] == expected


@pytest.fixture
def owner(tmp_path):
    app = QApplication.instance() or QApplication([])
    view = MappingOwner(tmp_path)
    yield view
    view.close()


def test_binding_editor_offers_only_actual_sources(owner):
    owner.snapshot = device_state('dualsense', buttons=[0, 4, 20], axes=[0, 1])
    dialog = BindingDialog(owner, mode='kbm')
    dialog.timer.stop()
    try:
        offered = {dialog.inputs[0].itemData(index) for index in range(dialog.inputs[0].count())}
        assert offered == {''} | set(input_sources(owner.snapshot))
        assert '20' in offered and '15' not in offered and 'LT' not in offered
        assert dialog.family == 'dualsense'
    finally:
        dialog.close()


@pytest.mark.parametrize('replacement', [None, device_state('switch'), device_state(instance=2)])
def test_open_editor_stops_capture_and_save_after_device_change(owner, replacement):
    owner.snapshot = device_state()
    dialog = BindingDialog(owner, mode='kbm')
    dialog.timer.stop()
    try:
        dialog.start_capture()
        owner.snapshot = replacement
        dialog.poll()
        assert not dialog.capturing and not dialog.best
        assert not dialog.save_button.isEnabled()
        assert not dialog.capture_button.isEnabled()
        assert all(not combo.isEnabled() for combo in dialog.inputs)
        assert dialog.error.text()
        dialog.validate()
        assert dialog.result() == 0
    finally:
        dialog.close()


def test_device_identity_uses_hardware_identifier_or_distinct_session_units():
    first, second = Device.__new__(Device), Device.__new__(Device)
    first.session_id, second.session_id = 'first-run', 'second-run'
    assert first._device_key('model', 1) != first._device_key('model', 2)
    assert first._device_key('model', 1) != second._device_key('model', 1)
    assert first._device_key('model', 1, serial=b'unit-a') == second._device_key('model', 22, serial=b'unit-a')
    assert first._device_key('model', 1, serial=b'unit-a') != first._device_key('model', 1, serial=b'unit-b')
    assert first._device_key('model', 1, path=b'path-a') == second._device_key('model', 99, path=b'path-a')
    assert 'unit-a' not in first._device_key('model', 1, serial=b'unit-a')


def test_new_editor_chooses_supported_button_when_south_is_absent(owner):
    owner.snapshot = device_state(buttons=[3, 8], axes=[])
    dialog = BindingDialog(owner, new=True, mode='kbm')
    dialog.timer.stop()
    try:
        assert dialog.trigger() == '3'
    finally:
        dialog.close()

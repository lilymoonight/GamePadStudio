"""The menu gestures form a complete Nikki layout without stealing held inputs."""
import copy

import pytest

from gamepadstudio.kbm_mapper import (
    NIKKI_TOUCH_MAPPINGS, NIKKI_TOUCH_SCROLL_MAPPINGS, NIKKI_TOUCH_SETTINGS,
    infinity_nikki_defaults,
)
from gamepadstudio.mapping_engine import MappingRuntime, validate_mappings
from gamepadstudio.studio_core import NIKKI_PROFILE_OPTIONS
from gamepadstudio.touch_gestures import touch_sources
from tests.test_touch_runtime import TouchActions, ds5, finger


def configured_runtime():
    state = ds5()
    mappings = infinity_nikki_defaults(state['family'], state['available_buttons'],
                                       touch_inputs=touch_sources(state))
    actions, dispatched = TouchActions(), []
    runtime = MappingRuntime(actions, lambda binding, down: dispatched.append((binding, down)),
                             start_mouse=False)
    config = {'active_profile': 'Nikki', 'profiles': {'Nikki': mappings},
              'profile_modes': {'Nikki': 'kbm'},
              'profile_options': {'Nikki': copy.deepcopy(NIKKI_PROFILE_OPTIONS)},
              'profile_devices': {'Nikki': state['device_key']},
              'device_settings': {state['device_key']: copy.deepcopy(NIKKI_TOUCH_SETTINGS)}}
    runtime.update(state, config, now=0.)
    return runtime, config, actions, dispatched


@pytest.mark.parametrize('family', ['dualsense', 'dualshock4'])
def test_sony_template_has_ten_independent_touch_sources_and_keeps_held_controls(family):
    mappings = validate_mappings(infinity_nikki_defaults(family))
    assert {key for key in mappings if key.startswith('TP:')} == {
        *NIKKI_TOUCH_MAPPINGS, *NIKKI_TOUCH_SCROLL_MAPPINGS}
    for trigger, value in NIKKI_TOUCH_MAPPINGS.items():
        assert mappings[trigger] == {'short': {'action': 'shortcut', 'value': value},
                                     'long': {'action': 'none'}}
    for trigger, direction in NIKKI_TOUCH_SCROLL_MAPPINGS.items():
        assert mappings[trigger]['short'] == {'action': 'wheel', 'value': direction}
    assert mappings['LT']['short'] == {'action': 'mouse_hold', 'value': 'right'}
    assert mappings['RT']['short'] == {'action': 'mouse_hold', 'value': 'left'}
    assert mappings['20']['short'] == {'action': 'shortcut', 'value': 'Esc'}
    assert mappings['LS:inner']['short'] == {'action': 'hold', 'value': 'Ctrl'}
    assert not any('TP:' in key and '+' in key for key in mappings)


def test_v2_defaults_exactly_preserve_the_keyboard_only_chord_layout():
    previous = infinity_nikki_defaults('dualsense', layout_version=2)
    assert not any(key.startswith('TP:') for key in previous)
    assert infinity_nikki_defaults('dualsense', layout_version=2,
                                  touch_inputs=[*NIKKI_TOUCH_MAPPINGS, *NIKKI_TOUCH_SCROLL_MAPPINGS]) == previous
    upgraded = infinity_nikki_defaults('dualsense')
    restored = {key: copy.deepcopy(value) for key, value in upgraded.items()
                if not key.startswith('TP:')}
    for trigger in ('0+4', '2+4', '3+4'):
        restored[trigger]['short'] = copy.deepcopy(previous[trigger]['short'])
    assert restored == previous


@pytest.mark.parametrize('family', ['dualsense', 'dualshock4', 'xbox', 'switch', 'generic'])
def test_no_touch_coordinates_keeps_all_previous_menu_shortcuts(family):
    mappings = infinity_nikki_defaults(family, touch_inputs=[])
    assert not any(key.startswith('TP:') for key in mappings)
    assert mappings['0+4']['short']['value'] == 'C'
    assert mappings['2+4']['short']['value'] == 'B'
    assert mappings['3+4']['short']['value'] == 'U'


def test_single_contact_device_has_only_supported_gestures():
    state = ds5(touch_finger_counts=[1], touchpad_fingers=1)
    mappings = infinity_nikki_defaults('dualsense', state['available_buttons'],
                                       touch_inputs=touch_sources(state))
    assert {key for key in mappings if key.startswith('TP:')} == set(NIKKI_TOUCH_MAPPINGS) - {'TP:two_tap'}
    assert 'TP:scroll_up' not in mappings and 'TP:scroll_down' not in mappings


def test_generic_controller_can_use_its_actual_touch_capabilities():
    state = ds5(family='generic', available_buttons=list(range(15)))
    mappings = infinity_nikki_defaults('generic', state['available_buttons'],
                                       touch_inputs=touch_sources(state))
    assert set(NIKKI_TOUCH_MAPPINGS) | set(NIKKI_TOUCH_SCROLL_MAPPINGS) <= mappings.keys()
    assert '20' not in mappings


def test_each_available_menu_gesture_only_replaces_its_matching_shortcut():
    mappings = infinity_nikki_defaults('dualsense', touch_inputs=['TP:swipe_left'])
    assert mappings['0+4']['short'] == {'action': 'none'}
    assert mappings['0+4']['long'] == {'action': 'shortcut', 'value': 'N'}
    assert mappings['0+4']['long_press'] == .60
    assert mappings['2+4']['short']['value'] == 'B'
    assert mappings['3+4']['short']['value'] == 'U'


@pytest.mark.parametrize('trigger,value', list(NIKKI_TOUCH_MAPPINGS.items()))
def test_default_touch_actions_emit_one_real_key_and_release_it(trigger, value):
    runtime, config, actions, dispatched = configured_runtime()
    runtime.update(ds5(fingers=[finger()]), config, now=.10)
    if trigger == 'TP:tap':
        runtime.update(ds5(), config, now=.17)
        runtime.update(ds5(), config, now=.46)
    elif trigger == 'TP:double_tap':
        runtime.update(ds5(), config, now=.17)
        runtime.update(ds5(fingers=[finger(contact=2)]), config, now=.30)
        runtime.update(ds5(), config, now=.38)
    elif trigger == 'TP:hold':
        runtime.update(ds5(fingers=[finger()]), config, now=.66)
        runtime.update(ds5(fingers=[finger()]), config, now=.90)
        runtime.update(ds5(), config, now=.95)
    elif trigger == 'TP:two_tap':
        runtime.update(ds5(fingers=[finger(), finger(index=1)]), config, now=.12)
        runtime.update(ds5(), config, now=.20)
    else:
        direction = trigger.removeprefix('TP:swipe_')
        x, y = {'up': (.5, .2), 'down': (.5, .8),
                'left': (.2, .5), 'right': (.8, .5)}[direction]
        runtime.update(ds5(fingers=[finger(x=x, y=y)]), config, now=.25)
        runtime.update(ds5(), config, now=.30)
    runtime.update(ds5(), config, now=1.20)
    assert [call for call in actions.calls if call[0] == 'key' and call[2]] == [('key', value, True)]
    assert [event['trigger'] for event in runtime.feedback()['events']] == [trigger]
    assert not any(actions.keys.values()) and not actions.mouse
    assert not actions.moves and not dispatched


def test_primary_menu_chord_tap_is_silent_and_held_secondary_is_still_usable():
    runtime, config, actions, _ = configured_runtime()
    runtime.update(ds5(buttons=[4]), config, now=.10)
    runtime.update(ds5(buttons=[4, 0]), config, now=.20)
    runtime.update(ds5(buttons=[4]), config, now=.30)
    runtime.update(ds5(), config, now=.40)
    assert not actions.calls
    runtime.update(ds5(buttons=[4]), config, now=1.)
    runtime.update(ds5(buttons=[4, 0]), config, now=1.10)
    runtime.update(ds5(buttons=[4, 0]), config, now=1.71)
    runtime.update(ds5(), config, now=1.80)
    assert ('key', 'N', True) in actions.calls
    assert all(call[1] == 'N' for call in actions.calls)
    assert not any(actions.keys.values())


def test_swiping_does_not_interrupt_l2_r2_or_send_combat_keys():
    runtime, config, actions, _ = configured_runtime()
    runtime.update(ds5(axes=[0., 0., 0., 0., .5, .5]), config, now=.10)
    runtime.update(ds5(axes=[0., 0., 0., 0., .5, .5], fingers=[finger()]), config, now=.20)
    runtime.update(ds5(axes=[0., 0., 0., 0., .5, .5], fingers=[finger(x=.8)]), config, now=.30)
    runtime.update(ds5(axes=[0., 0., 0., 0., .5, .5]), config, now=.40)
    assert actions.mouse == {'left', 'right'}
    assert ('key', 'B', True) in actions.calls
    assert not any(call[0] == 'key' and call[1] in ('Space', 'Shift', 'E', 'Q', 'R') for call in actions.calls)
    runtime.update(ds5(), config, now=.60)
    assert not actions.mouse and not any(actions.keys.values())


def test_double_finger_scroll_uses_bindings_without_direct_scroll_enabled():
    runtime, config, actions, dispatched = configured_runtime()
    assert not config['device_settings'][ds5()['device_key']]['touch_scroll']
    runtime.update(ds5(fingers=[finger(y=.6), finger(y=.6, index=1)]), config, now=.10)
    runtime.update(ds5(fingers=[finger(y=.49), finger(y=.49, index=1)]), config, now=.20)
    runtime.update(ds5(), config, now=.30)
    runtime.update(ds5(), config, now=.70)
    assert actions.calls == [('wheel', 1), ('wheel', 1)]
    assert not any(actions.keys.values()) and not actions.moves and not dispatched
    assert [event['trigger'] for event in runtime.feedback()['events']] == ['TP:scroll_up', 'TP:scroll_up']

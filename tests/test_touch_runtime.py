"""Touchpad input reaches the authoritative runtime without OS input or sleeps."""
import copy

import pytest

from gamepadstudio.mapping_engine import MappingRuntime
from gamepadstudio.studio_core import ConfigStore, device_config, profile_scope
from tests.test_unified_mapping import Actions, entry


def ds5(unit=1, fingers=(), buttons=(), **changes):
    """Capabilities match the connected Sony 054c:0ce6 SDL controller."""
    state = dict(family='dualsense', controller_type=7, vendor=0x054c, product=0x0ce6,
                 is_gamecontroller=True, input_backend='', instance_id=unit,
                 profile_key='dualsense:054c:0ce6:fixture',
                 device_key=f'dualsense:054c:0ce6:fixture:serial:{unit}',
                 available_buttons=list(range(16)) + [20], available_axes=list(range(6)),
                 trigger_axis_bindings=[4, 5], analog_trigger_axes=[4, 5],
                 axes=[0.] * 6, buttons=list(buttons), led=True, rumble=True,
                 trigger_rumble=False, touchpad=True, touchpad_count=1,
                 touch_finger_counts=[2], touchpad_fingers=2, touch_valid=True,
                 touch_read_error=False, touch=[], touch_fingers=list(fingers))
    state.update(changes)
    return state


def finger(x=.5, y=.5, index=0, contact=1):
    return {'pad': 0, 'finger': index, 'contact': contact, 'x': x, 'y': y, 'pressure': 1.}


class TouchActions(Actions):
    def __init__(self):
        super().__init__()
        self.moves = []

    def move_mouse(self, dx, dy):
        self.moves.append((dx, dy))
        self.calls.append(('move', dx, dy))


def runtime(mappings, settings=None, preview=False):
    actions, dispatched = TouchActions(), []
    engine = MappingRuntime(actions, lambda binding, down: dispatched.append((binding, down)),
                            start_mouse=False)
    preferences = {'touch_gestures_enabled': True, **(settings or {})}
    config = {'active_profile': 'test', 'profiles': {'test': copy.deepcopy(mappings)},
              'profile_modes': {'test': 'kbm'},
              'profile_devices': {'test': profile_scope(ds5())},
              'device_settings': {profile_scope(ds5()): preferences}}
    # A finger already resting on the pad during startup must not become a gesture.
    engine.update(ds5(), config, now=0., preview=preview)
    return engine, config, actions, dispatched


def presses(actions, value):
    return [call for call in actions.calls if call == ('key', value, True)]


def test_single_tap_waits_for_double_window_and_releases_its_output():
    engine, config, actions, _ = runtime({'TP:tap': entry('hold', 'S')})
    engine.update(ds5(fingers=[finger()]), config, now=.10)
    engine.update(ds5(), config, now=.20)
    engine.update(ds5(), config, now=.47)
    assert not actions.calls
    engine.update(ds5(), config, now=.49)
    assert presses(actions, 'S') == [('key', 'S', True)]
    assert engine.feedback()['events'][-1]['trigger'] == 'TP:tap'
    engine.update(ds5(), config, now=.54)
    assert actions.keys[ord('S')] == 1
    engine.update(ds5(), config, now=.56)
    assert actions.keys[ord('S')] == 0 and not engine.feedback()['outputs']
    engine.update(ds5(), config, now=1.)
    assert len(presses(actions, 'S')) == 1


def test_double_tap_fires_once_without_a_delayed_single_tap():
    engine, config, actions, _ = runtime({'TP:tap': entry('shortcut', 'S'),
                                          'TP:double_tap': entry('shortcut', 'D')})
    engine.update(ds5(fingers=[finger()]), config, now=.10)
    engine.update(ds5(), config, now=.17)
    engine.update(ds5(fingers=[finger(x=.51, contact=2)]), config, now=.30)
    assert not actions.calls
    engine.update(ds5(), config, now=.38)
    assert presses(actions, 'D') == [('key', 'D', True)]
    assert not presses(actions, 'S')
    engine.update(ds5(), config, now=.44)
    engine.update(ds5(), config, now=.90)
    assert not any(actions.keys.values()) and not presses(actions, 'S')
    assert [event['trigger'] for event in engine.feedback()['events']] == ['TP:double_tap']


@pytest.mark.parametrize('direction,end,key', [
    ('up', (.5, .2), 'W'), ('down', (.5, .8), 'S'),
    ('left', (.2, .5), 'A'), ('right', (.8, .5), 'D'),
])
def test_swipe_is_directional_and_fires_only_after_lifting(direction, end, key):
    mappings = {f'TP:swipe_{name}': entry('hold', value)
                for name, value in [('up', 'W'), ('down', 'S'), ('left', 'A'), ('right', 'D')]}
    mappings['TP:tap'] = entry('shortcut', 'T')
    engine, config, actions, _ = runtime(mappings)
    engine.update(ds5(fingers=[finger()]), config, now=.10)
    engine.update(ds5(fingers=[finger(x=(.5 + end[0]) / 2, y=(.5 + end[1]) / 2)]),
                  config, now=.20)
    engine.update(ds5(fingers=[finger(x=end[0], y=end[1])]), config, now=.30)
    assert not actions.calls
    engine.update(ds5(), config, now=.40)
    assert presses(actions, key) == [('key', key, True)]
    assert engine.feedback()['events'][-1]['trigger'] == f'TP:swipe_{direction}'
    engine.update(ds5(), config, now=.48)
    engine.update(ds5(), config, now=1.)
    assert not any(actions.keys.values()) and not presses(actions, 'T')
    assert len(engine.feedback()['events']) == 1


def test_hold_is_a_single_bounded_pulse_even_while_finger_remains_down():
    engine, config, actions, _ = runtime({'TP:hold': entry('mouse_hold', 'left'),
                                          'TP:tap': entry('shortcut', 'T')})
    down = ds5(fingers=[finger()])
    engine.update(down, config, now=.10)
    engine.update(down, config, now=.64)
    assert not actions.calls
    engine.update(down, config, now=.66)
    assert actions.mouse == {'left'}
    engine.update(down, config, now=.74)
    assert not actions.mouse and not engine.feedback()['outputs']
    engine.update(down, config, now=2.)
    engine.update(ds5(), config, now=2.1)
    engine.update(ds5(), config, now=3.)
    assert actions.calls == [('mouse', 'left', True), ('mouse', 'left', False)]
    assert [event['trigger'] for event in engine.feedback()['events']] == ['TP:hold']


def test_two_finger_tap_handles_separate_down_and_up_without_single_tap():
    engine, config, actions, _ = runtime({'TP:two_tap': entry('shortcut', 'D'),
                                          'TP:tap': entry('shortcut', 'S')})
    first, second = finger(x=.3), finger(x=.6, index=1, contact=2)
    engine.update(ds5(fingers=[first]), config, now=.10)
    engine.update(ds5(fingers=[first, second]), config, now=.13)
    engine.update(ds5(fingers=[second]), config, now=.18)
    engine.update(ds5(), config, now=.20)
    assert presses(actions, 'D') == [('key', 'D', True)]
    engine.update(ds5(), config, now=.30)
    engine.update(ds5(), config, now=.80)
    assert not any(actions.keys.values()) and not presses(actions, 'S')
    assert [event['trigger'] for event in engine.feedback()['events']] == ['TP:two_tap']


def test_capacity_shrink_cancels_two_fingers_without_reinterpreting_the_survivor():
    engine, config, actions, _ = runtime({'TP:two_tap': entry('shortcut', 'D'),
                                          'TP:tap': entry('shortcut', 'S'),
                                          'TP:hold': entry('hold', 'W')})
    first, second = finger(x=.3), finger(x=.6, index=1, contact=2)
    engine.update(ds5(fingers=[first, second]), config, now=.1)
    one = {'touchpad_fingers': 1, 'touch_finger_counts': [1]}
    engine.update(ds5(fingers=[first], **one), config, now=.2)
    assert engine.feedback()['touch']['mode'] == 'cancelled'
    engine.update(ds5(fingers=[first], **one), config, now=.8)
    engine.update(ds5(**one), config, now=.9)
    assert not actions.calls
    engine.update(ds5(fingers=[finger(contact=3)], **one), config, now=1.)
    engine.update(ds5(**one), config, now=1.1)
    engine.update(ds5(**one), config, now=1.5)
    engine.update(ds5(**one), config, now=1.6)
    assert actions.calls == [('key', 'S', True), ('key', 'S', False)]


@pytest.mark.parametrize('motion,step', [(-.06, 1), (.06, -1)])
def test_two_finger_scroll_does_not_move_pointer_or_fire_swipe_mapping(motion, step):
    engine, config, actions, dispatched = runtime(
        {'TP:two_tap': entry('capture'), 'TP:swipe_up': entry('capture'),
         'TP:swipe_down': entry('capture')}, {'touch_scroll': True, 'touch_mouse': True})
    for time, y in [(.1, .5), (.2, .5 + motion), (.3, .5 + motion * 2)]:
        engine.update(ds5(fingers=[finger(x=.3, y=y),
                                   finger(x=.6, y=y, index=1, contact=2)]), config, now=time)
    assert [call for call in actions.calls if call[0] == 'wheel'] == [('wheel', step)] * 2
    assert not actions.moves and not dispatched
    engine.update(ds5(), config, now=.4)
    engine.update(ds5(), config, now=.8)
    assert not dispatched and not engine.feedback()['outputs']


def test_preview_displays_touch_events_without_keyboard_mouse_scroll_or_dispatch():
    engine, config, actions, dispatched = runtime(
        {'TP:tap': entry('hold', 'S'), 'TP:swipe_right': entry('capture')},
        {'touch_scroll': True, 'touch_mouse': True}, preview=True)
    engine.update(ds5(fingers=[finger()]), config, now=.1, preview=True)
    engine.update(ds5(), config, now=.2, preview=True)
    engine.update(ds5(), config, now=.5, preview=True)
    assert 'key:83' in engine.feedback()['outputs']
    engine.update(ds5(), config, now=.6, preview=True)
    engine.update(ds5(fingers=[finger()]), config, now=.7, preview=True)
    engine.update(ds5(fingers=[finger(x=.8)]), config, now=.8, preview=True)
    engine.update(ds5(), config, now=.9, preview=True)
    engine.update(ds5(fingers=[finger(x=.3), finger(x=.6, index=1, contact=2)]),
                  config, now=1., preview=True)
    engine.update(ds5(fingers=[finger(x=.3, y=.4), finger(x=.6, y=.4, index=1, contact=2)]),
                  config, now=1.1, preview=True)
    engine.update(ds5(), config, now=1.2, preview=True)
    assert not actions.calls and not dispatched
    assert {'TP:tap', 'TP:swipe_right'} <= {event['trigger'] for event in engine.feedback()['events']}
    assert any(event['gesture'] == 'scroll' for event in engine.feedback()['events'])
    engine.update(ds5(), config, now=1.3, preview=False)
    engine.update(ds5(fingers=[finger(contact=3)]), config, now=1.4)
    engine.update(ds5(), config, now=1.5)
    engine.update(ds5(), config, now=1.8)
    engine.update(ds5(), config, now=1.9)
    assert actions.calls == [('key', 'S', True), ('key', 'S', False)]


@pytest.mark.parametrize('phase', ['contact', 'pending'])
@pytest.mark.parametrize('interrupt', ['pause', 'disconnect', 'device', 'profile',
                                     'binding', 'settings', 'capability', 'read_failure', 'click'])
def test_interruption_cancels_old_touch_and_requires_new_contact(phase, interrupt):
    mappings = {'TP:tap': entry('shortcut', 'S'), 'TP:hold': entry('hold', 'W')}
    engine, config, actions, dispatched = runtime(mappings)
    config['profiles']['second'] = copy.deepcopy(mappings)
    config['profile_modes']['second'] = 'kbm'
    config['profile_devices']['second'] = profile_scope(ds5(unit=2))
    config['device_settings'][profile_scope(ds5(unit=2))] = {'touch_gestures_enabled': True}
    current = ds5(fingers=[finger()])
    engine.update(current, config, now=.1)
    if phase == 'pending':
        current = ds5()
        engine.update(current, config, now=.2)
    changed = copy.deepcopy(current)
    update_options = {}
    if interrupt == 'pause':
        update_options['enabled'] = False
    elif interrupt == 'disconnect':
        changed = None
    elif interrupt == 'device':
        changed = ds5(unit=2, fingers=current['touch_fingers'])
        config['active_profile'] = 'second'
    elif interrupt == 'profile':
        config['profiles']['other'] = copy.deepcopy(mappings)
        config['profile_modes']['other'] = 'kbm'
        config['profile_devices']['other'] = profile_scope(ds5())
        config['active_profile'] = 'other'
    elif interrupt == 'binding':
        config['profiles']['test']['TP:tap'] = entry('shortcut', 'D')
    elif interrupt == 'settings':
        config['device_settings'][profile_scope(ds5())]['touch_gesture_sensitivity'] = .8
    elif interrupt == 'capability':
        changed.update(touchpad=False, touchpad_count=0, touchpad_fingers=0, touch_finger_counts=[])
    elif interrupt == 'read_failure':
        changed['touch_valid'] = False
    elif interrupt == 'click':
        changed['buttons'] = [20]
    engine.update(changed, config, now=.25, **update_options)
    unit = 2 if interrupt == 'device' else 1
    # If the interrupted gesture was still down, it cannot be reused on resume.
    # A pending tap had already lifted, so keep neutral to check its cancellation.
    surviving = [finger()] if phase == 'contact' else []
    engine.update(ds5(unit=unit, fingers=surviving), config, now=.3)
    engine.update(ds5(unit=unit, fingers=surviving), config, now=.9)
    engine.update(ds5(unit=unit), config, now=1.)
    engine.update(ds5(unit=unit), config, now=1.4)
    assert not actions.calls and not dispatched and not engine.feedback()['outputs']
    engine.update(ds5(unit=unit, fingers=[finger(contact=3)]), config, now=1.5)
    engine.update(ds5(unit=unit), config, now=1.6)
    engine.update(ds5(unit=unit), config, now=1.9)
    engine.update(ds5(unit=unit), config, now=2.)
    expected = 'D' if interrupt == 'binding' else 'S'
    assert actions.calls == [('key', expected, True), ('key', expected, False)]


@pytest.mark.parametrize('interrupt', ['pause', 'disconnect', 'edit', 'device', 'preview'])
def test_active_touch_output_releases_immediately_on_interruption(interrupt):
    engine, config, actions, _ = runtime({'TP:hold': entry('mouse_hold', 'left')})
    current = ds5(fingers=[finger()])
    engine.update(current, config, now=.1)
    engine.update(current, config, now=.66)
    assert actions.mouse == {'left'}
    options = {}
    if interrupt == 'pause':
        options['enabled'] = False
    elif interrupt == 'disconnect':
        current = None
    elif interrupt == 'edit':
        config['profiles']['test']['TP:hold'] = entry('mouse_hold', 'right')
    elif interrupt == 'device':
        current = ds5(unit=2, fingers=[finger()])
    else:
        options['preview'] = True
    engine.update(current, config, now=.67, **options)
    assert not actions.mouse and not engine.feedback()['outputs']
    assert actions.calls == [('mouse', 'left', True), ('mouse', 'left', False)]


def test_mechanical_touchpad_button_is_independent_and_suppresses_touch_tap():
    engine, config, actions, _ = runtime({'20': entry('hold', 'B'),
                                          'TP:tap': entry('shortcut', 'T')})
    engine.update(ds5(fingers=[finger()]), config, now=.1)
    engine.update(ds5(fingers=[finger()], buttons=[20]), config, now=.15)
    assert presses(actions, 'B') == [('key', 'B', True)]
    engine.update(ds5(fingers=[finger()]), config, now=.20)
    engine.update(ds5(), config, now=.25)
    engine.update(ds5(), config, now=.8)
    assert actions.calls == [('key', 'B', True), ('key', 'B', False)]
    assert [event['trigger'] for event in engine.feedback()['events']] == ['20']


def test_legacy_touch_coordinates_never_enable_new_mouse_or_gestures():
    engine, config, actions, dispatched = runtime({'TP:tap': entry('capture')}, {'touch_mouse': True})
    engine.update(ds5(touch=[.2, .2]), config, now=.1)
    engine.update(ds5(touch=[.4, .4]), config, now=.2)
    engine.update(ds5(), config, now=.8)
    assert not actions.calls and not dispatched
    engine.update(ds5(fingers=[finger(x=.2, y=.2)]), config, now=1.)
    engine.update(ds5(fingers=[finger(x=.22, y=.22)]), config, now=1.1)
    assert len(actions.moves) == 1
    assert actions.moves[0] == pytest.approx((32., 18.))


def test_touch_preferences_persist_for_each_physical_controller(tmp_path):
    first, second = ds5(), ds5(unit=2)
    store = ConfigStore(tmp_path)
    store.activate_controller(first)
    for key, value in {'touch_gestures_enabled': True, 'touch_mouse': True,
                       'touch_scroll': True, 'touch_gesture_sensitivity': .8}.items():
        store.set_setting(key, value, first)
    profile = store.profiles_for(first, 'kbm')[0]
    store.apply_mapping_change({'op': 'binding', 'profile': profile, 'trigger': 'TP:tap',
                                'mapping': entry('shortcut', 'Ctrl+S')}, first)
    store.activate_controller(second)
    store.set_setting('touch_gestures_enabled', False, second)
    store.set_setting('touch_gesture_sensitivity', .2, second)
    store.save()
    loaded = ConfigStore(tmp_path)
    assert device_config(loaded.data, first)['touch_gestures_enabled'] is True
    assert device_config(loaded.data, first)['touch_scroll'] is True
    assert device_config(loaded.data, first)['touch_mouse'] is True
    assert device_config(loaded.data, first)['touch_gesture_sensitivity'] == .8
    assert device_config(loaded.data, second)['touch_gestures_enabled'] is False
    assert device_config(loaded.data, second)['touch_scroll'] is False
    assert device_config(loaded.data, second)['touch_mouse'] is False
    assert device_config(loaded.data, second)['touch_gesture_sensitivity'] == .2
    assert profile not in loaded.profiles_for(second)
    assert loaded.data['profiles'][profile]['TP:tap'] == entry('shortcut', 'Ctrl+S')

import math

import pytest

from gamepadstudio.touch_gestures import (
    SINGLE_TOUCH_INPUTS, TOUCH_INPUTS, TouchGestureRecognizer, normalize_touch_sensitivity,
    normalize_touch_settings, touch_sources,
)


def frame(*fingers, buttons=(), identity='pad:A', capacity=2, **values):
    state = {'family': 'dualsense', 'device_key': identity, 'instance_id': 1,
             'touchpad': True, 'touchpad_fingers': capacity, 'touch_valid': True,
             'buttons': list(buttons), 'touch_fingers': []}
    for finger in fingers:
        index, x, y, *generation = finger
        state['touch_fingers'].append({'pad': 0, 'finger': index, 'x': x, 'y': y,
                                      'pressure': 1, 'contact': generation[0] if generation else index + 1})
    state.update(values)
    return state


@pytest.mark.parametrize('value', [None, '0.8', True, float('nan'), float('inf'), 10 ** 400])
def test_invalid_sensitivity_uses_safe_default(value):
    assert normalize_touch_sensitivity(value) == .5


def test_sensitivity_clamps_and_changes_only_movement_thresholds():
    assert normalize_touch_sensitivity(-1) == 0
    assert normalize_touch_sensitivity(2) == 1
    assert normalize_touch_sensitivity(.7) == .7
    cautious = normalize_touch_settings({'touch_gesture_sensitivity': 0})
    sensitive = normalize_touch_settings({'touch_gesture_sensitivity': 1})
    assert all(math.isfinite(value) and value > 0 for value in sensitive.values())
    assert cautious['swipe_distance'] > sensitive['swipe_distance']
    assert cautious['scroll_step'] > sensitive['scroll_step']
    assert cautious['tap_max_s'] == sensitive['tap_max_s'] < sensitive['hold_s']
    assert sensitive['tap_distance'] < sensitive['swipe_distance']


@pytest.mark.parametrize(('state', 'expected'), [
    (None, []), ({'family': 'dualsense'}, []),
    ({'family': 'dualsense', 'touchpad': True}, list(TOUCH_INPUTS)),
    ({'family': 'dualshock4', 'touchpad': True}, list(TOUCH_INPUTS)),
    ({'family': 'generic', 'touchpad': True}, list(SINGLE_TOUCH_INPUTS)),
    ({'touchpad_count': 1, 'touch_finger_counts': [2]}, list(TOUCH_INPUTS)),
    ({'touchpad': True, 'touchpad_fingers': 1}, list(SINGLE_TOUCH_INPUTS)),
    ({'touchpad': True, 'touchpad_fingers': 0}, []),
    ({'touchpad': True, 'touchpad_fingers': float('nan')}, []),
    ({'touchpad': True, 'touchpad_fingers': 10 ** 400}, []),
    ({'touchpad': True, 'connected': False}, []),
    ({'touchpad': True, 'is_gamecontroller': False}, []),
])
def test_touch_sources_require_reported_capacity(state, expected):
    assert touch_sources(state) == expected


def test_single_tap_waits_for_double_tap_window_and_fires_once():
    recognizer = TouchGestureRecognizer()
    recognizer.update(frame((0, .5, .5)), 0)
    assert recognizer.update(frame(), .10)['events'] == []
    assert recognizer.update(frame(), .37)['events'] == []
    assert recognizer.update(frame(), .39)['events'] == ['TP:tap']
    assert recognizer.update(frame(), 1)['events'] == []


def test_second_tap_defers_pending_single_until_release():
    recognizer = TouchGestureRecognizer()
    recognizer.update(frame((0, .5, .5)), 0)
    recognizer.update(frame(), .08)
    recognizer.update(frame((0, .52, .51, 2)), .30)
    assert recognizer.update(frame((0, .52, .51, 2)), .39)['events'] == []
    assert recognizer.update(frame(), .45)['events'] == ['TP:double_tap']
    assert recognizer.update(frame(), 1)['events'] == []


def test_distant_taps_do_not_combine():
    recognizer = TouchGestureRecognizer()
    recognizer.update(frame((0, .2, .2)), 0)
    recognizer.update(frame(), .08)
    recognizer.update(frame((0, .8, .8, 2)), .15)
    assert recognizer.update(frame(), .23)['events'] == ['TP:tap']
    assert recognizer.update(frame(), .52)['events'] == ['TP:tap']


def test_hold_fires_while_touching_once_and_suppresses_release_tap():
    recognizer = TouchGestureRecognizer()
    recognizer.update(frame((0, .5, .5)), 0)
    assert recognizer.update(frame((0, .51, .5)), .56)['events'] == ['TP:hold']
    assert recognizer.update(frame((0, .51, .5)), .8)['events'] == []
    assert recognizer.update(frame(), .9)['events'] == []
    assert recognizer.update(frame(), 1.3)['events'] == []


@pytest.mark.parametrize(('end', 'event'), [
    ((.2, .5), 'TP:swipe_left'), ((.8, .5), 'TP:swipe_right'),
    ((.5, .2), 'TP:swipe_up'), ((.5, .8), 'TP:swipe_down'),
])
def test_directional_swipes_and_pointer_delta_coexist(end, event):
    recognizer = TouchGestureRecognizer()
    recognizer.update(frame((0, .5, .5)), 0)
    moved = recognizer.update(frame((0, *end)), .12)
    assert moved['pointer_delta'] == pytest.approx([end[0] - .5, end[1] - .5])
    assert moved['events'] == []
    assert recognizer.update(frame(), .2)['events'] == [event]
    assert recognizer.update(frame(), 1)['events'] == []


def test_diagonal_and_returning_swipes_are_rejected():
    recognizer = TouchGestureRecognizer()
    recognizer.update(frame((0, .5, .5)), 0)
    recognizer.update(frame((0, .8, .8)), .1)
    assert recognizer.update(frame(), .2)['events'] == []
    recognizer.update(frame((0, .5, .5, 2)), .5)
    recognizer.update(frame((0, .8, .5, 2)), .6)
    recognizer.update(frame((0, .5, .5, 2)), .7)
    assert recognizer.update(frame(), .74)['events'] == []
    assert recognizer.update(frame(), 1.2)['events'] == []


def test_two_finger_tap_tolerates_initial_join_and_staggered_release():
    recognizer = TouchGestureRecognizer()
    recognizer.update(frame((0, .4, .5)), 0)
    joined = recognizer.update(frame((0, .4, .5), (1, .6, .5)), .025)
    assert joined['mode'] == 'two'
    assert joined['pointer_delta'] == [0, 0]
    partial = recognizer.update(frame((1, .6, .5)), .10)
    assert partial['pointer_delta'] == [0, 0]
    assert partial['events'] == []
    assert recognizer.update(frame(), .14)['events'] == ['TP:two_tap']
    assert recognizer.update(frame(), .7)['events'] == []


def test_two_finger_scroll_accumulates_small_motion_without_pointer_or_tap():
    recognizer = TouchGestureRecognizer()
    recognizer.update(frame((0, .4, .5), (1, .6, .5)), 0)
    assert recognizer.update(frame((0, .4, .48), (1, .6, .48)), .1)['scroll_steps'] == 0
    moved = recognizer.update(frame((0, .4, .445), (1, .6, .445)), .2)
    assert moved['scroll_steps'] == 1
    assert moved['pointer_delta'] == [0, 0]
    assert recognizer.update(frame(), .24)['events'] == []
    assert recognizer.update(frame(), .8)['events'] == []


def test_two_finger_horizontal_motion_does_not_scroll():
    recognizer = TouchGestureRecognizer()
    recognizer.update(frame((0, .3, .5), (1, .5, .5)), 0)
    moved = recognizer.update(frame((0, .6, .51), (1, .8, .51)), .1)
    assert moved['scroll_steps'] == 0
    assert moved['pointer_delta'] == [0, 0]
    assert recognizer.update(frame(), .2)['events'] == []


@pytest.mark.parametrize('changed', ['late_join', 'slot', 'contact', 'click', 'device',
                                    'capacity', 'read', 'pause', 'settings'])
def test_cancellations_cannot_create_a_release_tap(changed):
    recognizer = TouchGestureRecognizer()
    recognizer.update(frame((0, .4, .5)), 0)
    state = frame((0, .4, .5))
    settings = None
    if changed == 'late_join':
        state = frame((0, .4, .5), (1, .6, .5))
    elif changed == 'slot':
        state = frame((1, .4, .5))
    elif changed == 'contact':
        state = frame((0, .4, .5, 99))
    elif changed == 'click':
        state['buttons'] = [20]
    elif changed == 'device':
        state['device_key'] = 'pad:B'
    elif changed == 'capacity':
        state['touchpad_fingers'] = 1
    elif changed == 'read':
        state = frame(touch_valid=False)
    elif changed == 'pause':
        recognizer.reset(block_until_release=True)
    elif changed == 'settings':
        settings = {'touch_gesture_sensitivity': .8}
    result = recognizer.update(state, .1, settings)
    assert result['events'] == []
    assert result['pointer_delta'] == [0, 0]
    neutral = frame(identity=state['device_key'], capacity=state['touchpad_fingers'])
    assert recognizer.update(neutral, .2, settings)['events'] == []
    assert recognizer.update(neutral, .8, settings)['events'] == []


def test_capacity_change_cancels_an_existing_two_finger_tap():
    recognizer = TouchGestureRecognizer()
    recognizer.update(frame((0, .4, .5), (1, .6, .5)), 0)
    assert recognizer.update(frame((0, .4, .5), capacity=1), .1)['mode'] == 'cancelled'
    assert recognizer.update(frame(capacity=1), .14)['events'] == []


@pytest.mark.parametrize('restore', [False, True])
def test_partial_release_timeout_or_restored_contact_cancels(restore):
    recognizer = TouchGestureRecognizer()
    recognizer.update(frame((0, .4, .5), (1, .6, .5)), 0)
    recognizer.update(frame((1, .6, .5)), .1)
    state = frame((0, .4, .5), (1, .6, .5)) if restore else frame((1, .6, .5))
    assert recognizer.update(state, .13 if restore else .17)['mode'] == 'cancelled'
    assert recognizer.update(frame(), .2)['events'] == []
    assert recognizer.update(frame(), .8)['events'] == []


def test_disconnect_cancels_a_pending_single_tap():
    recognizer = TouchGestureRecognizer()
    recognizer.update(frame((0, .5, .5)), 0)
    recognizer.update(frame(), .08)
    assert recognizer.update(None, .2)['events'] == []
    assert recognizer.update(frame(), .5)['events'] == []


@pytest.mark.parametrize('fingers', [None, {}, ['bad'],
    [{'pad': 0, 'finger': 0, 'x': float('nan'), 'y': .5}],
    [{'pad': 0, 'finger': 0, 'x': .5, 'y': float('inf')}],
])
def test_invalid_contacts_cancel_safely(fingers):
    recognizer = TouchGestureRecognizer()
    recognizer.update(frame((0, .5, .5)), 0)
    assert recognizer.update(frame(touch_fingers=fingers), .1)['mode'] == 'cancelled'
    assert recognizer.update(frame(), .2)['events'] == []


@pytest.mark.parametrize('now', [float('nan'), float('inf'), 'bad', True, 10 ** 400])
def test_invalid_clock_cancels_safely(now):
    assert TouchGestureRecognizer().update(frame((0, .5, .5)), now)['mode'] == 'cancelled'


@pytest.mark.parametrize(('first', 'second'), [
    ((.4, .4), (.6, .65)),  # asymmetric vertical pinch
    ((.35, .43), (.65, .43)),  # horizontal pinch with downward translation
    ((.4, .35), (.6, .55)),  # rotation with moving centroid
])
def test_pinch_and_rotation_never_scroll_even_if_centroid_moves(first, second):
    recognizer = TouchGestureRecognizer()
    recognizer.update(frame((0, .4, .5), (1, .6, .5)), 0)
    assert recognizer.update(frame((0, *first), (1, *second)), .1)['scroll_steps'] == 0
    # Further translation in the same contact session cannot revive scrolling.
    translated = frame((0, first[0], first[1] - .1), (1, second[0], second[1] - .1))
    assert recognizer.update(translated, .2)['scroll_steps'] == 0
    assert recognizer.update(frame(), .24)['events'] == []

"""Device-local touchpad gestures using SDL's normalized contact coordinates.

SDL finger numbers identify slots, not a person's fingers. Local generations
cancel older gestures when a release/reuse edge has actually been observed.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass


TOUCH_INPUTS = ('TP:tap', 'TP:double_tap', 'TP:hold', 'TP:swipe_up',
                'TP:swipe_down', 'TP:swipe_left', 'TP:swipe_right', 'TP:two_tap',
                'TP:scroll_up', 'TP:scroll_down')
SINGLE_TOUCH_INPUTS = TOUCH_INPUTS[:7]


def _finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (ValueError, OverflowError):
        return False


def normalize_touch_sensitivity(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return .5
    try:
        value = float(value)
    except (ValueError, OverflowError):
        return .5
    return max(0., min(1., value)) if math.isfinite(value) else .5


def normalize_touch_settings(settings=None):
    settings = settings if isinstance(settings, dict) else {}
    sensitivity = normalize_touch_sensitivity(settings.get('touch_gesture_sensitivity', .5))
    return {'tap_distance': .055 - .025 * sensitivity,
            'swipe_distance': .24 - .14 * sensitivity,
            'scroll_step': .070 - .040 * sensitivity,
            'tap_max_s': .25, 'double_tap_s': .28, 'hold_s': .55,
            'swipe_max_s': .75, 'swipe_dominance': 1.45,
            'double_tap_distance': .12, 'join_s': .05, 'release_s': .06}


def touch_sources(state=None):
    if (not isinstance(state, dict) or not state or state.get('connected') is False
            or state.get('is_gamecontroller') is False
            or not (state.get('touchpad') or state.get('touchpad_count', 0))):
        return []
    capacity = state.get('touchpad_fingers')
    counts = state.get('touch_finger_counts')
    if capacity is None and isinstance(counts, (list, tuple)):
        capacity = max((value for value in counts if type(value) is int and value >= 0), default=0)
    if capacity is None:
        capacity = 2 if state.get('family') in ('dualsense', 'dualshock4') else 1
    if not _finite_number(capacity) or capacity <= 0:
        return []
    return list(TOUCH_INPUTS if capacity >= 2 else SINGLE_TOUCH_INPUTS)


def _contacts(state):
    fingers = state.get('touch_fingers', [])
    if not isinstance(fingers, (tuple, list)):
        return None
    result = {}
    for item in fingers:
        if not isinstance(item, dict):
            return None
        pad, finger = item.get('pad'), item.get('finger')
        if type(pad) is not int or type(finger) is not int or pad < 0 or finger < 0:
            return None
        generation = item.get('contact', 0)
        if not isinstance(generation, (int, str)) or isinstance(generation, bool):
            return None
        position = item.get('x'), item.get('y')
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in position):
            return None
        try:
            position = tuple(float(value) for value in position)
        except (ValueError, OverflowError):
            return None
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in position):
            return None
        key = pad, finger, generation
        if key in result or any(existing[:2] == key[:2] for existing in result):
            return None
        result[key] = position
    return result


def _distance(first, second):
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _center(positions):
    return tuple(sum(point[axis] for point in positions.values()) / len(positions) for axis in (0, 1))


@dataclass
class _Session:
    started: float
    origin: dict
    last: dict
    mode: str
    max_distance: float = 0.
    hold_sent: bool = False
    scroll_used: bool = False
    scroll_cancelled: bool = False
    scroll_remainder: float = 0.
    release_started: float | None = None
    second_tap: bool = False


class TouchGestureRecognizer:
    """Recognize bounded actions; input/output enablement belongs to the runtime."""
    def __init__(self):
        self._session = None
        self._pending_tap = None
        self._blocked = False
        self._identity = None
        self._sources = None
        self._settings = None
        self._last_now = None

    def reset(self, block_until_release=True):
        self._session = None
        self._pending_tap = None
        self._blocked = bool(block_until_release)
        self._last_now = None

    def _cancel(self):
        self.reset(block_until_release=True)

    def _flush_tap(self, now, result):
        pending = self._pending_tap
        if pending is None:
            return
        session = self._session
        eligible_second = (session is not None and session.second_tap
                           and session.mode != 'two' and not session.hold_sent
                           and now - session.started <= self._settings['tap_max_s']
                           and session.max_distance <= self._settings['tap_distance'])
        if not eligible_second and now >= pending['deadline']:
            result['events'].append('TP:tap')
            self._pending_tap = None

    def _begin(self, positions, now):
        mode = 'joining' if len(positions) == 1 else 'two'
        self._session = _Session(now, dict(positions), dict(positions), mode)
        pending = self._pending_tap
        if pending is not None and len(positions) == 1:
            first = next(iter(positions))
            self._session.second_tap = (now <= pending['deadline'] and first[0] == pending['pad']
                                       and _distance(_center(positions), pending['center'])
                                       <= self._settings['double_tap_distance'])

    def _finish(self, now, result):
        session = self._session
        elapsed = now - session.started
        still = session.max_distance <= self._settings['tap_distance']
        if session.mode == 'two':
            if elapsed <= self._settings['tap_max_s'] and still and not session.scroll_used:
                result['events'].append('TP:two_tap')
        elif not session.hold_sent:
            if elapsed <= self._settings['tap_max_s'] and still:
                center = _center(session.last)
                if session.second_tap and self._pending_tap is not None:
                    result['events'].append('TP:double_tap')
                    self._pending_tap = None
                else:
                    if self._pending_tap is not None:
                        result['events'].append('TP:tap')
                    self._pending_tap = {'deadline': now + self._settings['double_tap_s'],
                                         'center': center, 'pad': next(iter(session.origin))[0]}
            elif elapsed <= self._settings['swipe_max_s']:
                start, end = _center(session.origin), _center(session.last)
                dx, dy = end[0] - start[0], end[1] - start[1]
                major, minor = max(abs(dx), abs(dy)), min(abs(dx), abs(dy))
                if major >= self._settings['swipe_distance'] and major >= minor * self._settings['swipe_dominance']:
                    direction = ('right' if dx > 0 else 'left') if abs(dx) > abs(dy) else ('down' if dy > 0 else 'up')
                    result['events'].append('TP:swipe_' + direction)
        self._session = None

    def update(self, state, now=None, settings=None):
        result = {'events': [], 'scroll_steps': 0, 'pointer_delta': [0., 0.],
                  'contacts': 0, 'mode': 'idle'}
        thresholds = normalize_touch_settings(settings)
        now = time.monotonic() if now is None else now
        if not _finite_number(now):
            self._cancel()
            result['mode'] = 'cancelled'
            return result
        sources = tuple(touch_sources(state))
        if not state or not sources:
            self._cancel()
            self._identity = None
            return result
        identity = (state.get('device_key') or state.get('profile_key') or state.get('family'), state.get('instance_id'))
        if ((self._identity is not None and identity != self._identity)
                or (self._settings is not None and thresholds != self._settings)
                or (self._sources is not None and sources != self._sources)
                or (self._last_now is not None and now < self._last_now)):
            self._cancel()
        self._identity, self._settings, self._last_now = identity, thresholds, now
        self._sources = sources
        positions = _contacts(state)
        result['contacts'] = len(positions) if positions is not None else 0
        if (positions is None or state.get('touch_valid') is False or state.get('touch_read_error')
                or any(str(button) == '20' for button in state.get('buttons', []))):
            self._cancel()
            result['mode'] = 'cancelled'
            return result
        if self._blocked:
            self._blocked = bool(positions)
            result['mode'] = 'cancelled' if self._blocked else 'idle'
            return result
        if (len(positions) > 2 or len({key[0] for key in positions}) > 1
                or (len(positions) == 2 and 'TP:two_tap' not in sources)):
            self._cancel()
            result['mode'] = 'cancelled'
            return result
        if not positions:
            self._flush_tap(now, result)
            if self._session is not None:
                if (self._session.release_started is not None
                        and now - self._session.release_started > thresholds['release_s']):
                    self._cancel()
                else:
                    self._finish(now, result)
            self._flush_tap(now, result)
            return result
        if self._session is None:
            self._flush_tap(now, result)
            self._begin(positions, now)
        session = self._session
        old_keys, new_keys = set(session.origin), set(positions)
        elapsed = now - session.started
        if new_keys != old_keys:
            if session.mode == 'joining' and elapsed <= thresholds['join_s'] and old_keys < new_keys:
                session.max_distance = max(session.max_distance,
                    max(_distance(positions[key], session.origin[key]) for key in old_keys))
                session.origin = dict(positions)
                session.last = dict(positions)
                session.mode = 'two'
                session.second_tap = False
            elif session.mode == 'two' and new_keys < old_keys and len(positions) == 1:
                if session.release_started is None:
                    session.release_started = now
                residual = next(iter(positions))
                if (now - session.release_started > thresholds['release_s']
                        or _distance(positions[residual], session.last[residual]) > thresholds['tap_distance']):
                    self._cancel()
                    result['mode'] = 'cancelled'
                    return result
                result['mode'] = 'two'
                self._flush_tap(now, result)
                return result
            else:
                self._cancel()
                result['mode'] = 'cancelled'
                return result
        elif session.release_started is not None:
            self._cancel()
            result['mode'] = 'cancelled'
            return result
        session.max_distance = max(session.max_distance,
                                   max(_distance(positions[key], session.origin[key]) for key in positions))
        if session.mode == 'joining' and elapsed >= thresholds['join_s']:
            session.mode = 'single'
        if session.mode == 'single':
            key = next(iter(positions))
            result['pointer_delta'] = [positions[key][axis] - session.last[key][axis] for axis in (0, 1)]
            if (not session.hold_sent and session.max_distance <= thresholds['tap_distance']
                    and elapsed >= thresholds['hold_s']):
                session.hold_sent = True
                result['events'].append('TP:hold')
        elif session.mode == 'two':
            old_center, new_center = _center(session.last), _center(positions)
            dx, dy = new_center[0] - old_center[0], new_center[1] - old_center[1]
            keys = tuple(positions)
            deltas = [tuple(positions[key][axis] - session.last[key][axis] for axis in (0, 1))
                      for key in keys]
            span = tuple(positions[keys[1]][axis] - positions[keys[0]][axis] for axis in (0, 1))
            original_span = tuple(session.origin[keys[1]][axis] - session.origin[keys[0]][axis]
                                  for axis in (0, 1))
            vertical = [delta[1] for delta in deltas]
            opposed = (vertical[0] * vertical[1] < 0 and min(map(abs, vertical)) > .003)
            # A changed contact span distinguishes pinch/rotation from a pair
            # translating together. Cancel scrolling for the rest of that touch.
            if opposed or _distance(span, original_span) > .04:
                session.scroll_cancelled = True
                session.scroll_remainder = 0.
            largest = max(map(abs, vertical))
            same_direction = (vertical[0] * vertical[1] >= 0
                              and min(map(abs, vertical)) >= largest * .35)
            coordinated = _distance(deltas[0], deltas[1]) <= max(.01, .4 * max(map(lambda p: math.hypot(*p), deltas)))
            if (not session.scroll_cancelled and same_direction and coordinated
                    and abs(dy) >= abs(dx) * 1.2):
                session.scroll_remainder -= dy
                steps = math.trunc(session.scroll_remainder / thresholds['scroll_step'])
                if steps:
                    result['scroll_steps'] = max(-8, min(8, steps))
                    session.scroll_remainder -= result['scroll_steps'] * thresholds['scroll_step']
                    session.scroll_used = True
        session.last = dict(positions)
        result['mode'] = 'two' if session.mode == 'two' else 'single'
        self._flush_tap(now, result)
        return result

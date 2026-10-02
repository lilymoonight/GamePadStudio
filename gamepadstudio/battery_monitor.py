"""Stable battery notices from reported controller state, without hardware I/O."""
from __future__ import annotations

from collections import OrderedDict
import math
import uuid

LOW_STABLE_SECONDS = 3.
CRITICAL_STABLE_SECONDS = 1.
RECOVERY_SECONDS = 10.
MAX_EPISODES = 128


def normalize_power(state):
    """SDL levels 0..4 only; unknown, booleans and invented percentages fail."""
    value = state.get('power') if isinstance(state, dict) else None
    return value if type(value) is int and 0 <= value <= 4 else None


def battery_reported(state):
    return normalize_power(state) in (0, 1, 2, 3)


def _name(state):
    value = state.get('name')
    return value if isinstance(value, str) and value.strip() else '手柄'


def _time(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except (ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


class BatteryMonitor:
    """One selected input's debounce, with bounded physical-device episodes."""
    def __init__(self):
        self._session = str(uuid.uuid4())
        self._sequence = 0
        self._episodes = OrderedDict()
        self._identity = None
        self._pending_level = None
        self._pending_since = None
        self._recovery_since = None
        self._last_now = None

    def _reset_pending(self):
        self._pending_level = self._pending_since = self._recovery_since = None

    def _episode(self, scope):
        if scope not in self._episodes:
            self._episodes[scope] = {'notified': None, 'id': None}
        self._episodes.move_to_end(scope)
        while len(self._episodes) > MAX_EPISODES:
            self._episodes.popitem(last=False)
        return self._episodes[scope]

    def sample(self, state, now, enabled=True):
        """Return a new confirmed notice once, independent of mapping pause."""
        from .studio_core import profile_scope
        now = _time(now)
        if now is None or enabled is not True or not isinstance(state, dict) or not state:
            self._reset_pending()
            self._identity = None
            self._last_now = now
            return None
        scope = profile_scope(state)
        identity = (scope, state.get('instance_id'))
        if identity != self._identity or (self._last_now is not None and now < self._last_now):
            self._reset_pending()
        self._identity, self._last_now = identity, now
        power = normalize_power(state)
        if power is None:
            self._reset_pending()
            return None
        episode = self._episode(scope)
        if power >= 2:
            self._pending_level = self._pending_since = None
            if self._recovery_since is None:
                self._recovery_since = now
            elif now >= self._recovery_since + RECOVERY_SECONDS:
                episode['notified'] = episode['id'] = None
            return None
        self._recovery_since = None
        if self._pending_level != power:
            self._pending_level, self._pending_since = power, now
        if episode['notified'] == 0 or (episode['notified'] == 1 and power == 1):
            return None
        threshold = CRITICAL_STABLE_SECONDS if power == 0 else LOW_STABLE_SECONDS
        if now < self._pending_since + threshold:
            return None
        self._sequence += 1
        event = {'type': 'battery', 'id': f'{self._session}:{self._sequence}',
                 'device_scope': scope, 'instance_id': state.get('instance_id'),
                 'level': power, 'name': _name(state)}
        episode['notified'], episode['id'] = power, event['id']
        return event

    def warning(self, state, enabled=True):
        """Describe a confirmed episode only while this device reports low."""
        from .studio_core import profile_scope
        power = normalize_power(state)
        if enabled is not True or power not in (0, 1):
            return None
        scope = profile_scope(state)
        episode = self._episodes.get(scope)
        if episode is None or episode['notified'] is None:
            return None
        if power == 0 and episode['notified'] != 0:
            return None
        return {'type': 'battery', 'id': episode['id'], 'device_scope': scope,
                'instance_id': state.get('instance_id'), 'level': power, 'name': _name(state)}

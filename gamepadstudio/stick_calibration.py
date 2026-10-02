"""Measure reported right-stick rest values for an optional software deadzone."""
from __future__ import annotations

import math


SETTLE_SECONDS = .75
MEASURE_SECONDS = 3.
MIN_SAMPLES = 60
MAX_SAMPLE_GAP = .15
MAX_SAMPLES = 4096
MAX_MOTION_RADIUS = .35
MAX_MOTION_DISTANCE = .06
MAX_OFFSET = .15
MAX_NOISE = .015
DEADZONE_MARGIN = .02
MIN_DEADZONE = .03
MAX_DEADZONE = .20
_EPSILON = 1e-9


def _number(value):
    if type(value) not in (int, float):
        return None
    try:
        result = float(value)
    except (OverflowError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _stick_values(state):
    if not isinstance(state, dict):
        return None
    axes = state.get('axes')
    available = state.get('available_axes')
    if not isinstance(axes, (list, tuple)) or len(axes) < 4:
        return None
    if not isinstance(available, (list, tuple, set, frozenset)):
        return None
    if not {2, 3} <= {axis for axis in available if type(axis) is int}:
        return None
    x, y = _number(axes[2]), _number(axes[3])
    if x is None or y is None or not (-1. <= x <= 1. and -1. <= y <= 1.):
        return None
    return x, y


def supports_right_stick(state):
    """Require actual reported axes and a concrete current connection instance."""
    return (isinstance(state, dict) and type(state.get('instance_id')) is int
            and state['instance_id'] >= 0 and _stick_values(state) is not None)


class StickCalibration:
    """A bounded, deterministic session; sample() must receive fresh snapshots.

    tick() detects stalled input without adding samples. The settle period allows
    the user to release the stick; movement is checked throughout measurement.
    A terminal result stays fixed until start(), including after disconnection.
    """
    def __init__(self):
        self._phase = 'idle'
        self._progress = 0.
        self._error = ''
        self._result = None
        self._points = []
        self._scope = None
        self._instance = None
        self._started = None
        self._measurement_started = None
        self._last_sample = None
        self._last_now = None

    @property
    def phase(self):
        return self._phase

    @property
    def progress(self):
        return self._progress

    @property
    def result(self):
        return dict(self._result) if self._result is not None else None

    @property
    def error(self):
        return self._error

    def status(self):
        return {'phase': self.phase, 'progress': self.progress, 'samples': len(self._points),
                'result': self.result, 'error': self.error}

    def _fail(self, error):
        self._phase, self._error, self._result = 'failed', error, None
        return self.status()

    def start(self, state, now):
        from .studio_core import profile_scope
        self.__init__()
        now = _number(now)
        if now is None:
            return self._fail('采样时间异常，请重新测量')
        if not supports_right_stick(state):
            return self._fail('当前设备没有可测量的右摇杆')
        self._scope, self._instance = profile_scope(state), state['instance_id']
        self._started = self._last_sample = self._last_now = now
        self._phase = 'settling'
        return self.status()

    def _advance_time(self, now):
        now = _number(now)
        if now is None or now < self._last_now:
            self._fail('采样时间异常，请重新测量')
            return None
        self._last_now = now
        if now > self._last_sample + MAX_SAMPLE_GAP + _EPSILON:
            self._fail('没有连续收到新数据，请重新测量')
            return None
        if self._phase == 'settling':
            elapsed = min(SETTLE_SECONDS, now - self._started)
            self._progress = elapsed / (SETTLE_SECONDS + MEASURE_SECONDS)
        else:
            elapsed = min(MEASURE_SECONDS, now - self._measurement_started)
            self._progress = (SETTLE_SECONDS + elapsed) / (SETTLE_SECONDS + MEASURE_SECONDS)
        return now

    def tick(self, now):
        if self._phase in ('settling', 'sampling'):
            self._advance_time(now)
        return self.status()

    def sample(self, state, now):
        from .studio_core import profile_scope
        if self._phase not in ('settling', 'sampling'):
            return self.status()
        now = self._advance_time(now)
        if now is None:
            return self.status()
        if not isinstance(state, dict) or not state:
            return self._fail('手柄已断开，请重新测量')
        if profile_scope(state) != self._scope or state.get('instance_id') != self._instance:
            return self._fail('测量设备已变化，请重新测量')
        if not supports_right_stick(state):
            return self._fail('右摇杆数据无效，请重新测量')
        if now <= self._last_sample:
            return self.status()
        self._last_sample = now
        if self._phase == 'settling':
            if now + _EPSILON < self._started + SETTLE_SECONDS:
                return self.status()
            self._phase = 'sampling'
            self._measurement_started = now
        point = _stick_values(state)
        if (math.hypot(*point) > MAX_MOTION_RADIUS + _EPSILON
                or self._points and math.dist(point, self._points[0]) > MAX_MOTION_DISTANCE + _EPSILON):
            return self._fail('检测到摇杆移动，请松手后重新测量')
        if len(self._points) >= MAX_SAMPLES:
            return self._fail('采样频率异常，请重新测量')
        self._points.append(point)
        if now + _EPSILON < self._measurement_started + MEASURE_SECONDS:
            return self.status()
        if len(self._points) < MIN_SAMPLES:
            return self._fail('有效样本不足，请重新测量')
        self._finish()
        return self.status()

    def _finish(self):
        count = len(self._points)
        x = math.fsum(point[0] for point in self._points) / count
        y = math.fsum(point[1] for point in self._points) / count
        offset = math.hypot(x, y)
        noise = math.sqrt(math.fsum((px - x) ** 2 + (py - y) ** 2
                                   for px, py in self._points) / count)
        peak = max(math.hypot(*point) for point in self._points)
        # Cover the whole measured envelope, add margin, then round upward to
        # the UI's integer percentage. Never silently clamp an unsafe result.
        recommendation = max(MIN_DEADZONE, math.ceil((peak + DEADZONE_MARGIN) * 100 - 1e-12) / 100)
        if offset > MAX_OFFSET + _EPSILON or recommendation > MAX_DEADZONE + _EPSILON:
            recommendation = None
            self._error = '静止偏移过大，暂不建议调整死区'
        elif noise > MAX_NOISE + _EPSILON:
            recommendation = None
            self._error = '摇杆波动较大，暂不建议调整死区'
        self._result = {'device_scope': self._scope, 'instance_id': self._instance,
                        'offset_x': x, 'offset_y': y, 'offset': offset,
                        'noise': noise, 'peak': peak, 'recommended_deadzone': recommendation}
        self._phase, self._progress = 'complete', 1.

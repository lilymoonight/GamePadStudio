"""Response curves shared by device outputs and mapped trigger inputs.

These transform numeric amplitudes, not physical trigger resistance or another
application's vibration commands.
"""
from __future__ import annotations

import math

LINEAR_POINTS = (0., .25, .5, .75, 1.)
CURVE_CHANNELS = {'trigger_curves': ('left', 'right'),
                  'rumble_curves': ('low', 'high'),
                  'trigger_rumble_curves': ('left', 'right')}


def _number(value, fallback):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return fallback
    return number if math.isfinite(number) else fallback


def normalize_curve(spec=None):
    """Return a serializable monotonic five-point curve with safe endpoints."""
    spec = spec if isinstance(spec, dict) else {}
    points = spec.get('points', LINEAR_POINTS)
    if not isinstance(points, (tuple, list)) or len(points) != 5:
        points = LINEAR_POINTS
    points = [max(0., min(1., _number(value, LINEAR_POINTS[index])))
              for index, value in enumerate(points)]
    points[0], points[-1] = 0., 1.
    for index in range(1, len(points)):
        points[index] = max(points[index - 1], points[index])
    deadzone = _number(spec.get('deadzone', 0.), 0.)
    saturation = _number(spec.get('saturation', 1.), 1.)
    if not 0. <= deadzone < saturation <= 1. or saturation - deadzone < .01:
        deadzone, saturation = 0., 1.
    return {'points': points, 'deadzone': deadzone, 'saturation': saturation}


def evaluate_curve(value, spec=None):
    """Apply travel limits then interpolate input knots at 0/25/50/75/100%."""
    curve = normalize_curve(spec)
    value = max(0., min(1., _number(value, 0.)))
    if value <= curve['deadzone']:
        return 0.
    if value >= curve['saturation']:
        return 1.
    position = (value - curve['deadzone']) / (curve['saturation'] - curve['deadzone']) * 4.
    index = min(3, int(position))
    fraction = position - index
    return curve['points'][index] * (1. - fraction) + curve['points'][index + 1] * fraction


def curve_preset(name='linear'):
    points = {'linear': LINEAR_POINTS,
              'sensitive': (0., .40, .68, .87, 1.),
              'precise': (0., .12, .32, .60, 1.)}.get(name, LINEAR_POINTS)
    return normalize_curve({'points': list(points)})


def normalize_curve_channels(key, settings=None):
    """Missing channels reset to linear, never retaining another device's curve."""
    settings = settings if isinstance(settings, dict) else {}
    specs = settings.get(key)
    specs = specs if isinstance(specs, dict) else {}
    return {channel: normalize_curve(specs.get(channel)) for channel in CURVE_CHANNELS[key]}


def curve_capabilities(state=None):
    """Require hardware evidence; SDL digital mappings alone are insufficient."""
    if not isinstance(state, dict) or not state or state.get('connected') is False:
        return {'trigger_axes': [], 'rumble': False, 'trigger_rumble': False}
    family = state.get('family', 'generic')
    raw = state.get('is_gamecontroller') is False
    xinput = not raw and state.get('input_backend') == 'xinput'
    explicit = state.get('analog_trigger_axes')
    if isinstance(explicit, (list, tuple, set)):
        candidates = {axis for axis in explicit if type(axis) is int and axis in (4, 5)}
    else:
        candidates = {4, 5} if not raw and (xinput or family in ('xbox', 'dualshock4', 'dualsense')) else set()
    if family == 'switch' and not xinput:
        candidates = set()
    axis_bindings = state.get('trigger_axis_bindings')
    if isinstance(axis_bindings, (list, tuple, set)):
        candidates.intersection_update(axis_bindings)
    available = state.get('available_axes')
    if isinstance(available, (list, tuple, set)):
        candidates.intersection_update(available)
    elif isinstance(state.get('axes'), (list, tuple)):
        candidates.intersection_update(range(len(state['axes'])))
    elif raw:
        count = _number(state.get('num_axes', 0), 0)
        candidates.intersection_update(range(max(0, min(6, int(count)))))
    return {'trigger_axes': sorted(candidates), 'rumble': state.get('rumble') is True,
            'trigger_rumble': not raw and state.get('trigger_rumble') is True}

"""Short, channel-specific previews that never persist a draft curve."""
from contextlib import ExitStack
import copy
import math
import threading

from .response_curves import curve_capabilities, normalize_curve
from .studio_core import profile_scope


def preview_response_curve(device, settings, state, kind, channel, curve,
                           strength=.55, haptic_engine=None):
    capabilities = curve_capabilities(state)
    channels = {'rumble': ('low', 'high'), 'trigger_rumble': ('left', 'right')}
    if kind not in channels or channel not in channels[kind]:
        raise ValueError('无效的震动测试通道')
    if not capabilities[kind]:
        return False
    if isinstance(strength, bool):
        raise ValueError('无效的震动强度')
    strength = float(strength)
    if not math.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError('震动强度应在 0 到 1 之间')
    setter = getattr(device, 'set_response_curves', None)
    output = getattr(device, 'rumble_ext' if kind == 'rumble' else 'rumble_triggers', None)
    if not callable(setter) or not callable(output):
        return False
    saved = copy.deepcopy(settings)
    draft = copy.deepcopy(settings)
    key = kind + '_curves'
    curves = draft.get(key)
    draft[key] = copy.deepcopy(curves) if isinstance(curves, dict) else {}
    draft[key][channel] = normalize_curve(curve)
    # Use the same lock order as the feedback worker. Device selection cannot
    # replace the handle between the identity check, preview and restoration.
    with ExitStack() as stack:
        for lock in (getattr(haptic_engine, '_state_lock', None), getattr(device, '_io_lock', None)):
            if lock is not None:
                stack.enter_context(lock)
        metadata = getattr(device, 'metadata', {})
        if isinstance(metadata, dict) and (metadata.get('device_key') or metadata.get('profile_key')):
            if profile_scope(metadata) != profile_scope(state):
                raise ValueError('输入设备已变化，请重新打开当前设备设置')
        instance = getattr(device, 'instance_id', None)
        if instance is not None and state.get('instance_id') is not None and instance != state['instance_id']:
            raise ValueError('输入设备已变化，请重新打开当前设备设置')
        if getattr(haptic_engine, '_closed', False):
            return False
        cancellation = getattr(haptic_engine, '_pattern_cancel', None)
        if cancellation is not None:
            cancellation.set()
            haptic_engine._pattern_cancel = threading.Event()
        # Cancel an ongoing software pulse before auditioning a single channel.
        stop = getattr(device, 'rumble_ext', None)
        if callable(stop):
            stop(0., 0., 0)
        stop_triggers = getattr(device, 'rumble_triggers', None)
        if capabilities['trigger_rumble'] and callable(stop_triggers):
            stop_triggers(0., 0., 0)
        try:
            setter(draft)
            return bool(output(strength if channel == channels[kind][0] else 0.,
                               strength if channel == channels[kind][1] else 0., 250))
        finally:
            setter(saved)

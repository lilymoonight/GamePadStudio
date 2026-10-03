"""Apply selected-controller isolation and persist only confirmed changes."""
import copy
import math

from .studio_core import device_config


def isolation_status(device):
    method = getattr(device, 'controller_isolation_status', None)
    if method:
        return method()
    return dict(supported=False, enabled=False, active=False, status='unavailable',
                restore_pending=False, reason='当前输入后端未提供原生手柄隔离')


def controller_neutral(state):
    if not state or state.get('buttons'):
        return False
    try:
        axes = list(state.get('axes', []))
        return (all(math.isfinite(float(value)) and abs(float(value)) < .2 for value in axes[:4])
                and all(math.isfinite(float(value)) and float(value) <= .2 for value in axes[4:6]))
    except (ValueError, TypeError):
        return False


def set_controller_isolation(store, device, state, enabled, release):
    if type(enabled) is not bool:
        raise ValueError('隔离状态必须为开启或关闭')
    if not state:
        raise ValueError('请先连接并选择原生手柄')
    if enabled and not controller_neutral(state):
        return dict(applied=False, enabled=False, message='请先松开手柄按键并让摇杆回中，再开启隔离',
                    isolation=isolation_status(device))
    if enabled and (not state or not state.get('vendor')):
        raise ValueError('请先连接并选择原生手柄')
    previous = isolation_status(device)
    method = getattr(device, 'set_controller_isolation', None)
    if not method:
        return dict(applied=False, enabled=False, message=previous['reason'], isolation=previous)
    release()
    result = method(enabled)
    applied = bool(result.get('active')) if enabled else not result.get('active') and not result.get('restore_pending') and result.get('status') == 'off'
    if not result.get('supported'):
        applied = False
    text = ('当前手柄的原始输入已隔离；游戏只接收本软件映射输出' if enabled else '当前手柄的原始输入已恢复') if applied else result.get('reason') or '无法确认手柄隔离状态'
    if applied:
        backup = copy.deepcopy(store.data)
        try:
            store.set_setting('device_cloaking_enabled', enabled, state)
            store.set_setting('mac_controller_isolation_requested', enabled, state)
            store.save()
        except Exception as exc:
            store.data.clear(); store.data.update(backup)
            restored = method(bool(previous.get('active')))
            return dict(applied=False, enabled=bool(restored.get('active')),
                        message=f'保存隔离设置失败：{exc}；'+(restored.get('reason') or '已恢复原先访问模式'),
                        isolation=restored)
    return dict(applied=applied, enabled=bool(result.get('active')), message=text, isolation=result)


def isolation_requested(config, state):
    settings = device_config(config, state)
    return settings.get('mac_controller_isolation_requested') is True and settings.get('device_cloaking_enabled') is True

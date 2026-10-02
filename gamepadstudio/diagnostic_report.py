"""Bounded, privacy-preserving support report for the local activity logs.

The source logs contain free-form notices and may include paths, device names,
and button activity. This module never copies a source row into its output.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile


REPORT_FORMAT = 'gamepadstudio-diagnostic'
REPORT_VERSION = 1
MAX_LOG_BYTES = 256 * 1024
MAX_LOG_LINES = 256
MAX_LINE_BYTES = 4096

_FAMILIES = {'dualsense', 'dualshock4', 'xbox', 'switch', 'generic'}
_PLATFORMS = {'macos', 'windows', 'linux', 'other'}
_EXACT_EVENTS = {
    '后台映射已启动': 'service_started',
    '手柄已连接': 'controller_connected',
    '手柄已断开': 'controller_disconnected',
    '映射已恢复': 'mapping_resumed',
    '映射已暂停': 'mapping_paused',
    '截图已保存': 'capture_saved',
    '振动已发送': 'rumble_sent',
    '振动不可用': 'rumble_unavailable',
    '灯条已更新': 'led_updated',
    '灯条不可用': 'led_unavailable',
    'Connected': 'controller_connected',
    'Gamepad disconnected': 'controller_disconnected',
}
_PREFIX_EVENTS = (
    ('紧急暂停：', 'emergency_paused'),
    ('映射已暂停：', 'mapping_paused'),
    ('连接失败：', 'connection_failed'),
    ('截图失败：', 'capture_failed'),
    ('回放已保存：', 'replay_saved'),
    ('回放保存失败：', 'replay_failed'),
    ('录屏触发失败：', 'recording_failed'),
    ('正在启动录像', 'recording_started'),
    ('正在结束并保存录像', 'recording_stopped'),
    ('当前预设有 ', 'unsupported_output'),
    ('手柄原始输入隔离', 'isolation_changed'),
    ('硬件独占隐身', 'isolation_changed'),
)
_EVENTS = frozenset(_EXACT_EVENTS.values()) | frozenset(
    category for _, category in _PREFIX_EVENTS
) | {'battery_low', 'battery_critical', 'other'}


def _category(row):
    if row.get('type') == 'battery':
        level = row.get('level')
        if type(level) is int and level in (0, 1):
            return 'battery_critical' if level == 0 else 'battery_low'
    message = row.get('message')
    if isinstance(message, str):
        if message in _EXACT_EVENTS:
            return _EXACT_EVENTS[message]
        for prefix, category in _PREFIX_EVENTS:
            if message.startswith(prefix):
                return category
    return 'other'


def _read_log(path):
    result = {'present': False, 'truncated': False, 'read_error': False,
              'sampled_lines': 0, 'malformed_lines': 0, 'counts': {}}
    try:
        with path.open('rb') as source:
            result['present'] = True
            size = source.seek(0, os.SEEK_END)
            offset = max(0, size - MAX_LOG_BYTES)
            source.seek(offset)
            data = source.read(MAX_LOG_BYTES)
    except FileNotFoundError:
        return result
    except OSError:
        result['read_error'] = True
        return result
    if offset:
        result['truncated'] = True
        # The first bytes may be part of a line or a UTF-8 character.
        data = data.partition(b'\n')[2]
    lines = data.splitlines()
    if len(lines) > MAX_LOG_LINES:
        result['truncated'] = True
        lines = lines[-MAX_LOG_LINES:]
    result['sampled_lines'] = len(lines)
    counts = {}
    for line in lines:
        if len(line) > MAX_LINE_BYTES:
            result['malformed_lines'] += 1
            continue
        try:
            row = json.loads(line.decode('utf-8'))
        except (UnicodeError, json.JSONDecodeError, RecursionError):
            result['malformed_lines'] += 1
            continue
        if not isinstance(row, dict):
            result['malformed_lines'] += 1
            continue
        category = _category(row)
        counts[category] = counts.get(category, 0) + 1
    result['counts'] = dict(sorted(counts.items()))
    return result


def _count_inputs(value, maximum):
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        return None
    if any(type(item) is not int or item < 0 or item >= maximum for item in value):
        return None
    return len(set(value))


def _device_summary(device):
    if not isinstance(device, dict):
        device = {}
    reported = device.get('connected')
    connected = (reported if type(reported) is bool else
                 type(device.get('instance_id')) is int and device['instance_id'] >= 0)
    family = device.get('family')
    family = family if isinstance(family, str) and family in _FAMILIES else 'unknown'
    power = device.get('power')
    power = power if type(power) is int and 0 <= power <= 4 else None
    return {'connected': connected, 'family': family, 'power_level': power,
            'button_count': _count_inputs(device.get('available_buttons'), 64),
            'axis_count': _count_inputs(device.get('available_axes'), 32)}


def _service_summary(status):
    if not isinstance(status, dict):
        return {'online': False, 'mapping_enabled': None, 'recording_active': None}
    enabled = status.get('enabled')
    recording = status.get('recording')
    running = recording.get('running') if isinstance(recording, dict) else None
    return {'online': True,
            'mapping_enabled': enabled if type(enabled) is bool else None,
            'recording_active': running if type(running) is bool else None}


def build_report(root, *, device=None, status=None) -> dict:
    """Summarize recent local events without retaining any source text."""
    root = Path(root)
    if device is None and isinstance(status, dict):
        device = status.get('device')
    platform = {'darwin': 'macos', 'win32': 'windows', 'linux': 'linux'}.get(sys.platform, 'other')
    return {
        'format': REPORT_FORMAT,
        'version': REPORT_VERSION,
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'platform': platform,
        'device': _device_summary(device),
        'service': _service_summary(status),
        'logs': {
            'ui': _read_log(root / 'events.jsonl'),
            'agent': _read_log(root / 'agent-events.jsonl'),
        },
    }


def _valid_report(report):
    """Prevent callers from adding arbitrary text before a user saves a report."""
    if not isinstance(report, dict) or set(report) != {
            'format', 'version', 'generated_at', 'platform', 'device', 'service', 'logs'}:
        return False
    if report['format'] != REPORT_FORMAT or type(report['version']) is not int or report['version'] != REPORT_VERSION:
        return False
    stamp = report['generated_at']
    if not isinstance(stamp, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00', stamp):
        return False
    try:
        datetime.fromisoformat(stamp)
    except ValueError:
        return False
    if not isinstance(report['platform'], str) or report['platform'] not in _PLATFORMS:
        return False
    device = report['device']
    if not isinstance(device, dict) or set(device) != {
            'connected', 'family', 'power_level', 'button_count', 'axis_count'}:
        return False
    if (type(device['connected']) is not bool or not isinstance(device['family'], str)
            or device['family'] not in (_FAMILIES | {'unknown'})):
        return False
    for key, maximum in (('power_level', 4), ('button_count', 64), ('axis_count', 32)):
        value = device[key]
        if value is not None and (type(value) is not int or not 0 <= value <= maximum):
            return False
    service = report['service']
    if not isinstance(service, dict) or set(service) != {'online', 'mapping_enabled', 'recording_active'}:
        return False
    if type(service['online']) is not bool or any(
            service[key] is not None and type(service[key]) is not bool
            for key in ('mapping_enabled', 'recording_active')):
        return False
    logs = report['logs']
    if not isinstance(logs, dict) or set(logs) != {'ui', 'agent'}:
        return False
    for summary in logs.values():
        if not isinstance(summary, dict) or set(summary) != {
                'present', 'truncated', 'read_error', 'sampled_lines', 'malformed_lines', 'counts'}:
            return False
        if any(type(summary[key]) is not bool for key in ('present', 'truncated', 'read_error')):
            return False
        sampled, malformed = summary['sampled_lines'], summary['malformed_lines']
        if (type(sampled) is not int or type(malformed) is not int or
                not 0 <= malformed <= sampled <= MAX_LOG_LINES):
            return False
        counts = summary['counts']
        if (not isinstance(counts, dict) or any(
                key not in _EVENTS or type(value) is not int or value <= 0
                for key, value in counts.items()) or
                sum(counts.values()) + malformed != sampled):
            return False
    return True


def save_report(path, report) -> None:
    """Validate and atomically save one report beside a temporary file."""
    if not _valid_report(report):
        raise ValueError('诊断报告格式错误')
    data = (json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False,
                       separators=(',', ':')) + '\n').encode('utf-8')
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + '.',
                                         suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

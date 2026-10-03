"""A support report must remain bounded and never copy private log payloads."""
import copy
import json

import pytest

from gamepadstudio import diagnostic_report as reports


def _write_rows(path, rows):
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')


def test_report_uses_only_fixed_categories_and_allowlisted_device_status(tmp_path):
    secret = 'PRIVATE_PATH_SERIAL_TOKEN_123'
    _write_rows(tmp_path / 'events.jsonl', [
        {'time': '2026-10-02T09:00:00', 'message': '手柄已连接', 'path': secret},
        {'time': secret, 'message': '截图失败：/Users/lily/' + secret},
        {'message': '按键输入: 0 / 1 ' + secret},
    ])
    _write_rows(tmp_path / 'agent-events.jsonl', [
        {'type': 'battery', 'level': 1, 'name': secret,
         'device_scope': secret, 'id': secret, 'instance_id': 123},
        {'message': '回放已保存：/Users/lily/' + secret},
    ])
    device = {'connected': True, 'family': 'dualsense', 'power': 1,
              'available_buttons': [0, 1, 1, 15], 'available_axes': [0, 1, 4],
              'name': secret, 'device_key': secret, 'serial': secret,
              'instance_id': 123, 'touch': {'events': [secret]}}
    status = {'device': device, 'enabled': True, 'recording': {'running': False, 'path': secret},
              'profile': secret, 'application_profile': {'executable': secret},
              'mapping': {'events': [{'trigger': secret}]}}

    report = reports.build_report(tmp_path, status=status)
    assert report['format'] == reports.REPORT_FORMAT
    assert report['version'] == reports.REPORT_VERSION
    assert report['device'] == {'connected': True, 'family': 'dualsense', 'power_level': 1,
                                'button_count': 3, 'axis_count': 3}
    assert report['service'] == {'online': True, 'mapping_enabled': True,
                                 'recording_active': False}
    assert report['logs']['ui']['counts'] == {
        'capture_failed': 1, 'controller_connected': 1, 'other': 1}
    assert report['logs']['agent']['counts'] == {'battery_low': 1, 'replay_saved': 1}
    assert secret not in json.dumps(report, ensure_ascii=False)
    assert '/Users/lily/' not in json.dumps(report)


def test_missing_logs_and_malformed_device_fields_are_safe(tmp_path):
    report = reports.build_report(tmp_path, device={
        'connected': 'yes', 'family': 'PRIVATE_SERIAL', 'power': True,
        'available_buttons': ['PRIVATE_BUTTON'], 'available_axes': list(range(33)),
    }, status={'enabled': 'yes', 'recording': {'running': 1}})
    assert report['device'] == {'connected': False, 'family': 'unknown', 'power_level': None,
                                'button_count': None, 'axis_count': None}
    assert report['service'] == {'online': True, 'mapping_enabled': None,
                                 'recording_active': None}
    for summary in report['logs'].values():
        assert summary == {'present': False, 'truncated': False, 'read_error': False,
                           'sampled_lines': 0, 'malformed_lines': 0, 'counts': {}}
    assert 'PRIVATE' not in json.dumps(report)


def test_large_log_only_samples_recent_lines_and_counts_malformed(tmp_path):
    log = tmp_path / 'agent-events.jsonl'
    with log.open('wb') as output:
        output.write(b'S' * (reports.MAX_LOG_BYTES + 100) + b'\n')
        output.write((b'{"message":"\xe6\x89\x8b\xe6\x9f\x84\xe5\xb7\xb2\xe8\xbf\x9e\xe6\x8e\xa5"}\n') * 300)
        output.write(b'{"message":"\xe6\x89\x8b\xe6\x9f\x84\xe5\xb7\xb2\xe8\xbf\x9e\xe6\x8e\xa5"}\n' * 252)
        output.write(b'\xff\n')
        output.write(b'{"message":"unknown secret"}\n')
        output.write(b'{"message":"' + b'P' * (reports.MAX_LINE_BYTES + 1) + b'"}\n')
    report = reports.build_report(tmp_path)
    sampled = report['logs']['agent']
    assert sampled['present'] is True
    assert sampled['truncated'] is True
    assert sampled['sampled_lines'] == reports.MAX_LOG_LINES
    assert sampled['malformed_lines'] == 2
    assert sampled['counts'] == {'controller_connected': 253, 'other': 1}
    assert 'secret' not in json.dumps(report)
    assert 'P' * 30 not in json.dumps(report)


def test_save_is_atomic_and_rejects_unexpected_payload(tmp_path, monkeypatch):
    report = reports.build_report(tmp_path)
    target = tmp_path / 'diagnostic.json'
    reports.save_report(target, report)
    assert json.loads(target.read_text(encoding='utf-8')) == report
    assert list(tmp_path.iterdir()) == [target]

    original = target.read_bytes()
    unsafe = copy.deepcopy(report)
    unsafe['raw_config'] = {'executable': '/Users/lily/private'}
    with pytest.raises(ValueError, match='诊断报告格式错误'):
        reports.save_report(target, unsafe)
    assert target.read_bytes() == original

    changed = copy.deepcopy(report)
    changed['service']['online'] = True
    monkeypatch.setattr(reports.os, 'replace', lambda *_: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError, match='disk full'):
        reports.save_report(target, changed)
    assert target.read_bytes() == original
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize('unsafe_value', [
    {'extra': 'private'}, ['private'], 'private', 1,
])
def test_save_never_serializes_untrusted_report_fields(tmp_path, unsafe_value):
    report = reports.build_report(tmp_path)
    report['platform'] = unsafe_value
    with pytest.raises(ValueError, match='诊断报告格式错误'):
        reports.save_report(tmp_path / 'diagnostic.json', report)
    assert list(tmp_path.iterdir()) == []

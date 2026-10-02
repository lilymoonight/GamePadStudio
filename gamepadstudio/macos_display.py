"""Native CG point geometry, physical pixels and EDR metadata, without capture."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


HELPER_NAME = 'gps-mac-capture'


def helper_path():
    candidates = [Path(__file__).resolve().parents[1] / 'bin' / HELPER_NAME]
    if getattr(sys, 'frozen', False):
        candidates.insert(0, Path(sys.executable).resolve().parent / HELPER_NAME)
        candidates.insert(0, Path(sys.executable).resolve().parents[1] / 'Resources' / 'bin' / HELPER_NAME)
        base = getattr(sys, '_MEIPASS', None)
        if base:
            candidates.insert(0, Path(base) / 'bin' / HELPER_NAME)
    for path in candidates:
        if path.is_file():
            return str(path)
    raise RuntimeError('macOS 原生捕获组件未安装，请重新构建或安装 Gamepad Studio')


def display_metadata(*, runner=None, executable=None):
    runner = runner or subprocess.run
    try:
        result = runner([executable or helper_path(), 'displays'], stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, timeout=8, check=False)
    except subprocess.SubprocessError as exc:
        raise RuntimeError('macOS 显示器元数据读取超时或失败') from exc
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', 'replace').strip())
    value = json.loads(result.stdout)
    if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('displays'), list):
        raise RuntimeError('macOS 显示器元数据无效')
    return value


def enumerate_displays():
    return display_metadata()['displays']


def capture_capabilities():
    try:
        value = display_metadata()
        return {'supported': bool(value.get('sck_available')), 'hdr_supported': bool(value.get('hdr_capture_available')),
                'screen_permission': bool(value.get('screen_permission')), 'reason': ''}
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        return {'supported': False, 'hdr_supported': False, 'screen_permission': False, 'reason': str(exc)}

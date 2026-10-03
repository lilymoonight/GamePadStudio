"""Installed, source-checkout and frozen app launches share one entry point."""
from pathlib import Path
import sys
from types import SimpleNamespace

from gamepadstudio import ipc


def test_installed_wheel_spawns_module_without_repository_main(monkeypatch, tmp_path):
    installed = tmp_path / 'site-packages' / 'gamepadstudio' / 'ipc.py'
    monkeypatch.setattr(ipc, '__file__', str(installed))
    monkeypatch.setattr(ipc, 'sys', SimpleNamespace(platform='darwin', executable=sys.executable))
    assert ipc._source_checkout_root() is None
    command = ipc.command_line(tmp_path, '--agent')
    assert command == [sys.executable, '-m', 'gamepadstudio', '--data-dir', str(tmp_path), '--agent']


def test_frozen_app_uses_its_bundle_executable_without_python_module(monkeypatch, tmp_path):
    executable = '/Applications/GamePadStudio.app/Contents/MacOS/GamePadStudio'
    monkeypatch.setattr(ipc, 'sys', SimpleNamespace(platform='darwin', executable=executable, frozen=True))
    assert ipc._source_checkout_root() is None
    assert ipc.command_line(tmp_path, '--agent') == [executable, '--data-dir', str(tmp_path), '--agent']


def test_windows_run_key_source_mode_keeps_absolute_launcher(monkeypatch, tmp_path):
    monkeypatch.setattr(ipc, 'sys', SimpleNamespace(platform='win32', executable=sys.executable))
    source_root = ipc._source_checkout_root()
    assert source_root is not None
    assert (source_root / 'main.py').is_file()
    command = ipc._windows_run_command_line(tmp_path)
    assert Path(command[1]) == source_root / 'main.py'
    assert command[2:] == ['--data-dir', str(tmp_path), '--agent']


def test_windows_run_key_installed_mode_uses_module(monkeypatch, tmp_path):
    installed = tmp_path / 'site-packages' / 'gamepadstudio' / 'ipc.py'
    monkeypatch.setattr(ipc, '__file__', str(installed))
    monkeypatch.setattr(ipc, 'sys', SimpleNamespace(platform='win32', executable=sys.executable))
    assert ipc._windows_run_command_line(tmp_path)[1:3] == ['-m', 'gamepadstudio']

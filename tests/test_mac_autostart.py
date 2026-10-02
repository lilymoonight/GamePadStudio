"""Login-job tests use private temporary files and an in-memory launchctl."""
import plistlib
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from gamepadstudio import ipc


pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='macOS LaunchAgent only')


@pytest.fixture
def login_job(monkeypatch, tmp_path):
    path = tmp_path / 'LaunchAgents' / 'com.gamepadstudio.agent.plist'
    monkeypatch.setattr(ipc, 'sys', SimpleNamespace(platform='darwin', executable=sys.executable))
    monkeypatch.setattr(ipc, '_launch_agent_path', lambda: path)
    job = SimpleNamespace(path=path, calls=[], loaded=False, failure=None)

    def run(command, **kwargs):
        assert command[0] == '/bin/launchctl'
        assert kwargs == dict(capture_output=True, text=True, timeout=10, check=False)
        job.calls.append(command)
        action = command[1]
        if action == job.failure:
            return subprocess.CompletedProcess(command, 5, '', 'simulated launchctl refusal')
        if action == 'print':
            return subprocess.CompletedProcess(command, 0 if job.loaded else 113, '', '')
        if action == 'bootstrap':
            job.loaded = True
        elif action == 'bootout':
            job.loaded = False
        return subprocess.CompletedProcess(command, 0, '', '')

    monkeypatch.setattr(ipc.subprocess, 'run', run)
    return job


def test_status_is_read_only_and_explicit_enable_registers_this_users_job(login_job, tmp_path):
    assert ipc.autostart_supported() and not ipc.autostart_enabled(tmp_path)
    assert login_job.calls == [] and not login_job.path.exists()
    ipc.set_autostart(tmp_path, True)
    document = plistlib.loads(login_job.path.read_bytes())
    assert document['ProgramArguments'] == ipc.command_line(tmp_path, '--agent')
    assert document['RunAtLoad'] and document['Label'] == ipc.MAC_AGENT_LABEL
    assert document['LimitLoadToSessionType'] == 'Aqua'
    assert document['ProcessType'] == 'Interactive'
    assert Path(document['WorkingDirectory']) == ipc._source_checkout_root()
    assert stat.S_IMODE(login_job.path.stat().st_mode) == 0o600
    domain = f'gui/{ipc.os.getuid()}'
    assert ['/bin/launchctl', 'bootstrap', domain, str(login_job.path)] in login_job.calls
    assert ipc.autostart_enabled() and ipc.autostart_enabled(tmp_path)
    assert not ipc.autostart_enabled(tmp_path / 'other-data')


def test_installed_package_login_job_uses_interpreter_directory(login_job, tmp_path, monkeypatch):
    installed = tmp_path / 'site-packages' / 'gamepadstudio' / 'ipc.py'
    monkeypatch.setattr(ipc, '__file__', str(installed))
    ipc.set_autostart(tmp_path, True)
    document = plistlib.loads(login_job.path.read_bytes())
    assert document['ProgramArguments'][1:3] == ['-m', 'gamepadstudio']
    assert document['WorkingDirectory'] == str(Path(sys.executable).parent)


def test_repeated_enable_preserves_live_backend_and_disable_removes_job(login_job, tmp_path):
    ipc.set_autostart(tmp_path, True)
    before = login_job.path.stat().st_mtime_ns
    ipc.set_autostart(tmp_path, True)
    assert login_job.path.stat().st_mtime_ns == before
    assert len([cmd for cmd in login_job.calls if cmd[1] == 'bootstrap']) == 1
    assert not any(cmd[1] == 'bootout' for cmd in login_job.calls)
    ipc.set_autostart(tmp_path, False)
    assert not login_job.path.exists() and not login_job.loaded
    assert not ipc.autostart_enabled()
    assert any(cmd[1] == 'bootout' for cmd in login_job.calls)


def test_failed_bootstrap_does_not_leave_a_future_login_change(login_job, tmp_path):
    login_job.failure = 'bootstrap'
    with pytest.raises(OSError, match='simulated launchctl refusal'):
        ipc.set_autostart(tmp_path, True)
    assert not login_job.path.exists()
    assert not ipc.autostart_enabled()


def test_failed_bootout_keeps_existing_login_configuration(login_job, tmp_path):
    ipc.set_autostart(tmp_path, True)
    before = login_job.path.read_bytes()
    login_job.failure = 'bootout'
    with pytest.raises(OSError):
        ipc.set_autostart(tmp_path, False)
    assert login_job.path.read_bytes() == before
    assert ipc.autostart_enabled(tmp_path) and login_job.loaded


def test_failed_edit_restores_previous_plist(login_job, tmp_path):
    ipc.set_autostart(tmp_path, True)
    before = login_job.path.read_bytes()
    login_job.failure = 'bootstrap'
    with pytest.raises(OSError):
        ipc.set_autostart(tmp_path / 'other-root', True)
    assert login_job.path.read_bytes() == before
    assert ipc.autostart_enabled(tmp_path)
    assert not ipc.autostart_enabled(tmp_path / 'other-root')


@pytest.mark.parametrize('payload', [b'not a plist', plistlib.dumps([]), plistlib.dumps({
    'Label': ipc.MAC_AGENT_LABEL, 'RunAtLoad': True, 'ProgramArguments': ['--agent', '--data-dir']})])
def test_invalid_login_job_is_not_reported_enabled(login_job, tmp_path, payload):
    login_job.path.parent.mkdir()
    login_job.path.write_bytes(payload)
    assert not ipc.autostart_enabled(tmp_path)

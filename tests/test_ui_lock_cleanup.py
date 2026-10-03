"""A second UI cannot take over a live owner's lock by PID alone."""
from types import SimpleNamespace

from gamepadstudio import ipc


def platform_lock(monkeypatch, tmp_path, platform, pid=12345):
    lock_path = tmp_path / 'studio.lock'
    lock_path.write_text(f'{pid}\nfixture\n')
    monkeypatch.setattr(ipc, 'sys', SimpleNamespace(platform=platform))
    monkeypatch.setattr(ipc, 'read_lock_pid', lambda _path: pid)
    return lock_path


def test_mac_unresponsive_ui_keeps_its_lock_and_is_not_force_killed(monkeypatch, tmp_path):
    lock_path = platform_lock(monkeypatch, tmp_path, 'darwin')
    monkeypatch.setattr(ipc, 'is_process_alive', lambda _pid: True)
    commands = []
    def no_reply(_root, command, **_kwargs):
        commands.append(command)
        return None
    monkeypatch.setattr(ipc, 'request', no_reply)
    monkeypatch.setattr(ipc, 'terminate_pid', lambda *_args, **_kwargs: 1 / 0)
    assert ipc.cleanup_stale_ui(tmp_path) is False
    assert lock_path.exists() and commands == ['status']


def test_mac_mismatched_ui_reply_cannot_reclaim_the_old_lock(monkeypatch, tmp_path):
    lock_path = platform_lock(monkeypatch, tmp_path, 'darwin')
    monkeypatch.setattr(ipc, 'is_process_alive', lambda _pid: True)
    commands = []
    def wrong_ui(_root, command, **_kwargs):
        commands.append(command)
        return {'ok': True, 'pid': 999}
    monkeypatch.setattr(ipc, 'request', wrong_ui)
    assert ipc.cleanup_stale_ui(tmp_path) is False
    assert lock_path.exists() and commands == ['status']


def test_mac_confirmed_graceful_exit_reclaims_only_stale_lock(monkeypatch, tmp_path):
    lock_path = platform_lock(monkeypatch, tmp_path, 'darwin')
    state = {'alive': True, 'stale_check': False, 'commands': []}
    monkeypatch.setattr(ipc, 'is_process_alive', lambda _pid: state['alive'])

    def respond(_root, command, **_kwargs):
        state['commands'].append(command)
        if command == 'exit':
            state['alive'] = False
        return {'ok': True, 'pid': 12345}

    class StaleLock:
        def __init__(self, path):
            assert path == str(lock_path)

        def setStaleLockTime(self, milliseconds):
            assert milliseconds == 0

        def removeStaleLockFile(self):
            assert not state['alive']
            state['stale_check'] = True
            lock_path.unlink()
            return True

    monkeypatch.setattr(ipc, 'request', respond)
    monkeypatch.setattr(ipc, 'QLockFile', StaleLock)
    monkeypatch.setattr(ipc, 'terminate_pid', lambda *_args, **_kwargs: 1 / 0)
    assert ipc.cleanup_stale_ui(tmp_path) is True
    assert state['stale_check'] and state['commands'] == ['status', 'exit'] and not lock_path.exists()


def test_mac_unconfirmed_exit_reply_preserves_lock(monkeypatch, tmp_path):
    lock_path = platform_lock(monkeypatch, tmp_path, 'darwin')
    monkeypatch.setattr(ipc, 'is_process_alive', lambda _pid: True)
    monkeypatch.setattr(ipc, 'request', lambda _root, command, **_kwargs:
                        {'ok': True, 'pid': 12345 if command == 'status' else 999})
    monkeypatch.setattr(ipc, 'terminate_pid', lambda *_args, **_kwargs: 1 / 0)
    assert ipc.cleanup_stale_ui(tmp_path) is False
    assert lock_path.exists()


def test_mac_new_owner_arriving_during_cleanup_prevents_takeover(monkeypatch, tmp_path):
    lock_path = platform_lock(monkeypatch, tmp_path, 'darwin')
    monkeypatch.setattr(ipc, 'is_process_alive', lambda _pid: False)

    class LiveLock:
        def __init__(self, _path):
            pass

        def setStaleLockTime(self, _milliseconds):
            pass

        def removeStaleLockFile(self):
            return False

    monkeypatch.setattr(ipc, 'QLockFile', LiveLock)
    assert ipc.cleanup_stale_ui(tmp_path) is False
    assert lock_path.exists()


def test_windows_previous_force_recovery_reports_success_only_after_exit(monkeypatch, tmp_path):
    lock_path = platform_lock(monkeypatch, tmp_path, 'win32')
    state = {'alive': True, 'terminated': False}
    monkeypatch.setattr(ipc, 'is_process_alive', lambda _pid: state['alive'])
    monkeypatch.setattr(ipc, 'request', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ipc.time, 'sleep', lambda _seconds: None)

    def terminate(*_args, **_kwargs):
        state['terminated'] = True
        state['alive'] = False
        return True

    monkeypatch.setattr(ipc, 'terminate_pid', terminate)
    assert ipc.cleanup_stale_ui(tmp_path) is True
    assert state['terminated'] and not lock_path.exists()


def test_windows_failed_force_recovery_preserves_live_lock(monkeypatch, tmp_path):
    lock_path = platform_lock(monkeypatch, tmp_path, 'win32')
    monkeypatch.setattr(ipc, 'is_process_alive', lambda _pid: True)
    monkeypatch.setattr(ipc, 'request', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ipc.time, 'sleep', lambda _seconds: None)
    monkeypatch.setattr(ipc, 'terminate_pid', lambda *_args, **_kwargs: False)
    assert ipc.cleanup_stale_ui(tmp_path) is False
    assert lock_path.exists()

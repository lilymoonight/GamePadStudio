"""An unresponsive UI owner must never be replaced without the lock."""
import sys
from types import SimpleNamespace

import pytest

from gamepadstudio import studio


@pytest.mark.parametrize('cleanup_safe', [False, True])
@pytest.mark.parametrize('show_reply', [None, {'ok': False, 'error': 'busy'}])
def test_unresponsive_ui_keeps_its_lock_and_prevents_second_window(tmp_path, monkeypatch, cleanup_safe, show_reply):
    lock_path = tmp_path / 'studio.lock'
    lock_path.write_text('existing owner', encoding='utf-8')
    attempts = []
    warnings = []
    cleanup_calls = []

    class BusyLock:
        def __init__(self, path):
            assert path == str(lock_path)

        def setStaleLockTime(self, _milliseconds):
            pass

        def tryLock(self, timeout):
            attempts.append(timeout)
            return False

    app = SimpleNamespace(setApplicationName=lambda *_: None,
                          setStyle=lambda *_: None,
                          setStyleSheet=lambda *_: None)
    monkeypatch.setattr(studio, 'QApplication', lambda _: app)
    monkeypatch.setattr(studio, 'QLockFile', BusyLock)
    monkeypatch.setattr(studio, 'request', lambda *_args, **_kwargs: show_reply)
    monkeypatch.setattr(studio, 'cleanup_stale_ui', lambda root: cleanup_calls.append(root) or cleanup_safe)
    monkeypatch.setattr(studio, 'QMessageBox', SimpleNamespace(warning=lambda *args: warnings.append(args)))
    monkeypatch.setattr(studio, 'Studio', lambda *_args, **_kwargs: pytest.fail('Second UI must not start'))
    monkeypatch.setattr(sys, 'argv', ['gamepadstudio', '--data-dir', str(tmp_path)])

    assert studio.run() == 1
    assert attempts == ([100, 400] if cleanup_safe else [100])
    assert cleanup_calls == [tmp_path]
    assert len(warnings) == 1
    assert lock_path.read_text(encoding='utf-8') == 'existing owner'

"""Native API failure tests: no keyboard or mouse input reaches Windows."""
import ctypes
from types import SimpleNamespace
import pytest
from gamepadstudio import actions as actions_module
from gamepadstudio.actions import WindowsActions


@pytest.fixture(autouse=True)
def isolated_windows_input(monkeypatch):
    native_ctypes = SimpleNamespace(**vars(ctypes))
    native_ctypes.get_last_error = lambda: 0
    monkeypatch.setattr(actions_module, 'C', native_ctypes)


class NativeInput:
    def __init__(self, returns=()):
        self.returns = list(returns)
        self.calls = []
        self.down = set()

    def MapVirtualKeyW(self, key, mode):
        return key

    def SendInput(self, count, events, size):
        result = self.returns.pop(0) if self.returns else count
        batch = [(events[i].u.ki.wVk, not bool(events[i].u.ki.dwFlags & 2)) for i in range(count)]
        self.calls.append(batch)
        for key, down in batch[:result]:
            self.down.add(key) if down else self.down.discard(key)
        return result


def actions(native):
    # Skip the real constructor entirely: no SendInput or desktop attachment.
    value = WindowsActions.__new__(WindowsActions)
    value.user32 = native
    value.held = {}
    value.held_mouse = set()
    return value


def test_partial_shortcut_rolls_back_modifier_before_propagating_error(monkeypatch):
    monkeypatch.setattr(actions_module.C, 'get_last_error', lambda: 0)
    native = NativeInput([1])  # Alt goes down but the following letter fails.
    output = actions(native)
    with pytest.raises(OSError):
        output.hold('Alt+G', True)
    assert not native.down
    assert not output.held
    assert native.calls == [[(18, True), (71, True)], [(71, False)], [(18, False)]]


def test_failed_access_denied_retry_is_not_reported_as_success(monkeypatch):
    monkeypatch.setattr(actions_module.C, 'get_last_error', lambda: 5)
    monkeypatch.setattr('gamepadstudio.actions.attach_to_default_desktop', lambda: None)
    native = NativeInput([0, 0])
    output = actions(native)
    with pytest.raises(OSError):
        output.hold('Alt', True)
    assert not output.held and not native.down
    assert native.calls == [[(18, True)], [(18, True)], [(18, False)]]


def test_one_refused_release_does_not_skip_other_keys_or_mouse(monkeypatch):
    monkeypatch.setattr(actions_module.C, 'get_last_error', lambda: 0)
    native = NativeInput([0])
    native.down = {18, 71}
    output = actions(native)
    output.held = {18: 1, 71: 1}
    output.held_mouse = {'left'}
    mouse = []
    def release_mouse(button, down):
        mouse.append((button, down)); output.held_mouse.discard(button)
    output.mouse_button = release_mouse
    with pytest.raises(OSError):
        output.release_all()
    assert output.held == {71: 1} and native.down == {71}
    assert mouse == [('left', False)]
    output.release_all()
    assert not output.held and not native.down


def test_shortcut_preserves_modifier_held_by_another_mapping(monkeypatch):
    monkeypatch.setattr('gamepadstudio.actions.time.sleep', lambda _: None)
    native = NativeInput(); output = actions(native)
    output.hold('Alt', True)
    output.shortcut('Alt+G')
    assert native.down == {18} and output.held == {18: 1}
    output.hold('Alt', False)
    assert not native.down


def test_repeated_key_aliases_do_not_leave_reference_count_stuck():
    native = NativeInput(); output = actions(native)
    output.hold('Ctrl+Control+S', True)
    output.hold('Ctrl+Control+S', False)
    assert not output.held and not native.down


def test_live_agent_is_never_force_stopped_for_duplicate_start(tmp_path, monkeypatch):
    from gamepadstudio import ipc
    lock = tmp_path / 'agent.lock'; lock.write_text('12345\n')
    monkeypatch.setattr(ipc, 'is_process_alive', lambda _: True)
    def forbidden(*a, **kw):
        pytest.fail('A live input owner must not be terminated')
    monkeypatch.setattr(ipc, 'request', forbidden)
    monkeypatch.setattr(ipc, 'terminate_pid', forbidden)
    assert ipc.cleanup_stale_agent(tmp_path) is False
    assert lock.exists()


def test_run_returns_without_replacing_healthy_agent(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from gamepadstudio import agent
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr('gamepadstudio.actions.attach_to_default_desktop', lambda: None)
    monkeypatch.setattr(agent, 'request', lambda *a, **kw: {'ok': True, 'pid': 12345})
    def forbidden(*a, **kw):
        pytest.fail('Must reuse existing agent')
    monkeypatch.setattr(agent, 'Agent', forbidden)
    assert agent.run(tmp_path) == 0

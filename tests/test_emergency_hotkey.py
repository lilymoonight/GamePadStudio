"""Native registration and message ownership without reserving real keys."""
import ctypes as C
from ctypes import wintypes as W
from types import SimpleNamespace
import threading

import pytest
from PySide6.QtCore import QByteArray, QCoreApplication, QEvent, QObject
from PySide6.QtWidgets import QApplication

from gamepadstudio import emergency_hotkey as hotkeys

_REAL_LOAD_USER32 = hotkeys._load_user32


class Function:
    def __init__(self, callback):
        self.callback = callback
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.callback(*args)


class FakeUser32:
    def __init__(self):
        self.conflict = False
        self.unregister_failure = False
        self.raise_register = False
        self.active = {}
        self.order = []
        self.RegisterHotKey = Function(self.register)
        self.UnregisterHotKey = Function(self.unregister)

    def register(self, hwnd, identifier, modifiers, vk):
        self.order.append(('register', identifier))
        if self.raise_register:
            raise OSError('unavailable')
        if self.conflict:
            C.set_last_error(1409)
            return 0
        self.active[identifier] = (modifiers, vk)
        return 1

    def unregister(self, hwnd, identifier):
        self.order.append(('unregister', identifier))
        if self.unregister_failure:
            return 0
        return self.active.pop(identifier, None) is not None


@pytest.fixture
def native(monkeypatch):
    app = QApplication.instance() or QApplication([])
    library, owners = FakeUser32(), []
    monkeypatch.setattr(hotkeys, '_load_user32', lambda: library)

    def create(callback=None, parent=None):
        owner = hotkeys.EmergencyHotkey(callback or (lambda: None), parent)
        owners.append(owner)
        return owner

    yield SimpleNamespace(app=app, library=library, create=create)
    for owner in owners:
        try:
            owner.close()
        except RuntimeError:
            pass  # A parent-destruction test already deleted the QObject.


def enabled(shortcut=hotkeys.DEFAULT_SHORTCUT):
    return {'enabled': True, 'shortcut': shortcut}


def send(owner, *, identifier=None, parameter=None, event_type=b'windows_dispatcher_MSG',
         message=hotkeys.WM_HOTKEY, hwnd=None):
    msg = hotkeys._MSG(hwnd=hwnd, message=message,
                       wParam=owner._hotkey_id if identifier is None else identifier,
                       lParam=owner._message_parameter if parameter is None else parameter)
    return owner._filter.nativeEventFilter(QByteArray(event_type), C.addressof(msg))


@pytest.mark.parametrize(('value', 'canonical', 'modifiers', 'vk'), [
    ('Ctrl+Alt+F10', 'Ctrl+Alt+F10', 3, 0x79),
    (' alt + CONTROL + f10 ', 'Ctrl+Alt+F10', 3, 0x79),
    ('Shift+Alt+x', 'Alt+Shift+X', 5, 0x58),
    ('Shift+Ctrl+Alt+0', 'Ctrl+Alt+Shift+0', 7, 0x30),
    ('Ctrl+Shift+Return', 'Ctrl+Shift+Enter', 6, 13),
    ('Ctrl+Shift+Delete', 'Ctrl+Shift+Del', 6, 46),
    ('Ctrl+Alt+F24', 'Ctrl+Alt+F24', 3, 0x87),
    ('Alt+Shift+PgDn', 'Alt+Shift+PgDown', 5, 34),
])
def test_single_shortcut_parser_has_canonical_modifier_order(value, canonical, modifiers, vk):
    assert hotkeys.parse_shortcut(value) == (canonical, modifiers, vk)


@pytest.mark.parametrize('value', [None, True, 1, '', [], 'Ctrl+F10', 'F10',
                                 'Ctrl+Ctrl+F10', 'Ctrl+Control+F10', 'Ctrl+Alt+Shift',
                                 'Ctrl+Alt+', 'Ctrl+Alt+F25', 'Ctrl+Alt+two',
                                 'Ctrl+Alt+F10,F11', 'Ctrl+Alt+A+B',
                                 'Ctrl+Alt+\nF10', 'Ctrl+Alt+\x00F10', 'a' * 65])
def test_parser_rejects_incomplete_repeated_multiple_or_non_key_input(value):
    with pytest.raises(ValueError):
        hotkeys.parse_shortcut(value)


@pytest.mark.parametrize('value', ['Win+Ctrl+F10', 'Meta+Alt+F10', 'Ctrl+Alt+PrintScreen',
                                 'Ctrl+Shift+PrtSc', 'Ctrl+Alt+F12', 'Alt+Shift+f12',
                                 'Ctrl+Alt+Delete', 'Alt+Ctrl+Del', 'Ctrl+Alt+Shift+Del'])
def test_reserved_os_and_debugger_shortcuts_cannot_be_registered(value):
    with pytest.raises(ValueError, match='保留'):
        hotkeys.parse_shortcut(value)


@pytest.mark.parametrize('value', [None, {}, [], {'enabled': True}, {'shortcut': 'Ctrl+Alt+F10'},
                                 {'enabled': 1, 'shortcut': 'Ctrl+Alt+F10'},
                                 {'enabled': False, 'shortcut': 'Ctrl+Alt+F12'},
                                 dict(enabled(), extra=True)])
def test_bad_saved_settings_default_to_disabled_and_strict_writes_reject(value):
    assert hotkeys.normalize_hotkey_settings(value) == {
        'enabled': False, 'shortcut': hotkeys.DEFAULT_SHORTCUT}
    with pytest.raises(ValueError):
        hotkeys.normalize_hotkey_settings(value, strict=True)


def test_valid_settings_normalize_to_fresh_dictionary():
    source = enabled('Alt+Ctrl+f10')
    normalized = hotkeys.normalize_hotkey_settings(source, strict=True)
    assert normalized == enabled()
    normalized['enabled'] = False
    assert source['enabled'] is True


def test_default_and_disabled_never_load_win32_or_install_filter(native, monkeypatch):
    def unexpected():
        raise AssertionError('disabled hotkey must not touch Windows')
    monkeypatch.setattr(hotkeys, '_load_user32', unexpected)
    owner = native.create()
    assert owner.status() == {'enabled': False, 'shortcut': hotkeys.DEFAULT_SHORTCUT,
                              'registered': False, 'error': ''}
    assert owner.configure(None) == owner.status()
    assert owner.configure({'enabled': False, 'shortcut': 'Ctrl+Shift+F9'})['registered'] is False
    assert not owner._filter_installed and owner._hotkey_id is None
    owner.close()
    assert not native.library.RegisterHotKey.calls
    assert not native.library.UnregisterHotKey.calls


def test_registers_null_thread_hotkey_with_no_repeat_and_does_not_repeat_reload(native):
    owner = native.create()
    status = owner.configure(enabled())
    assert status == dict(enabled(), registered=True, error='')
    assert native.library.RegisterHotKey.calls == [(None, owner._hotkey_id, 0x4003, 0x79)]
    assert owner._filter_installed is True
    status['registered'] = False
    assert owner.status()['registered'] is True
    assert owner.configure(enabled('alt+ctrl+f10'))['registered'] is True
    assert len(native.library.RegisterHotKey.calls) == 1
    assert not native.library.UnregisterHotKey.calls


def test_only_exact_registered_native_thread_message_runs_callback(native):
    calls = []
    owner = native.create(lambda: calls.append('pause'))
    owner.configure(enabled())
    assert send(owner) == (True, 0)
    assert calls == ['pause']
    assert send(owner, event_type=b'windows_generic_MSG') == (True, 0)
    assert calls == ['pause', 'pause']


@pytest.mark.parametrize('alteration', [dict(identifier=1), dict(parameter=0),
                                       dict(parameter=(0x78 << 16) | 3),
                                       dict(parameter=(0x79 << 16) | 2),
                                       dict(parameter=(0x79 << 16) | 0x4003),
                                       dict(parameter=(1 << 48) | (0x79 << 16) | 3),
                                       dict(event_type=b'xcb_generic_event_t'),
                                       dict(message=0x100), dict(hwnd=0x1234567887654321)])
def test_unrelated_or_forged_message_cannot_trigger_pause(native, alteration):
    calls = []
    owner = native.create(lambda: calls.append('pause'))
    owner.configure(enabled())
    assert send(owner, **alteration) == (False, 0)
    assert calls == []


def test_invalid_null_message_pointer_is_ignored(native):
    owner = native.create()
    owner.configure(enabled())
    assert owner._filter.nativeEventFilter(QByteArray(b'windows_dispatcher_MSG'), 0) == (False, 0)


def test_switch_unregisters_before_register_and_queued_old_message_is_ignored(native):
    calls = []
    owner = native.create(lambda: calls.append('pause'))
    owner.configure(enabled())
    old_id, old_parameter = owner._hotkey_id, owner._message_parameter
    owner.configure(enabled('Ctrl+Shift+F9'))
    new_id = owner._hotkey_id
    assert new_id != old_id
    assert native.library.order == [('register', old_id), ('unregister', old_id),
                                   ('register', new_id)]
    assert send(owner, identifier=old_id, parameter=old_parameter) == (False, 0)
    assert send(owner) == (True, 0)
    owner.configure(enabled())
    assert owner._hotkey_id not in (old_id, new_id)
    assert send(owner, identifier=old_id, parameter=old_parameter) == (False, 0)
    assert calls == ['pause']


def test_disabling_and_close_release_registration_and_remove_filter(native):
    calls = []
    owner = native.create(lambda: calls.append('pause'))
    owner.configure(enabled())
    old_id, old_parameter = owner._hotkey_id, owner._message_parameter
    owner.configure(dict(enabled(), enabled=False))
    assert native.library.active == {}
    assert owner._filter_installed is False
    assert owner.status() == dict(enabled(), enabled=False, registered=False, error='')
    assert send(owner, identifier=old_id, parameter=old_parameter) == (False, 0)
    owner.configure(enabled())
    owner.close()
    assert owner.status()['enabled'] is False and owner.status()['registered'] is False
    assert native.library.active == {} and not owner._filter_installed
    assert owner._quit_connected is False and owner._app is None and owner._callback is None
    owner.close()
    assert len(native.library.UnregisterHotKey.calls) == 2
    assert owner.configure(enabled())['registered'] is False
    assert len(native.library.RegisterHotKey.calls) == 2
    assert calls == []


def test_multiple_objects_do_not_consume_each_others_hotkey_messages(native):
    calls = []
    first = native.create(lambda: calls.append('first'))
    second = native.create(lambda: calls.append('second'))
    first.configure(enabled())
    second.configure(enabled('Ctrl+Shift+F9'))
    assert first._hotkey_id != second._hotkey_id
    assert send(second, identifier=first._hotkey_id, parameter=first._message_parameter) == (False, 0)
    assert send(first) == send(second) == (True, 0)
    assert calls == ['first', 'second']
    first.close()
    assert second._hotkey_id in native.library.active
    assert send(second) == (True, 0)


def test_registration_conflict_is_visible_and_same_reload_does_not_busy_retry(native):
    native.library.conflict = True
    owner = native.create()
    status = owner.configure(enabled())
    assert status['enabled'] is True and status['registered'] is False
    assert '占用' in status['error']
    assert not owner._filter_installed and owner._hotkey_id is None
    native.library.conflict = False
    owner.configure(enabled())
    assert len(native.library.RegisterHotKey.calls) == 1
    assert owner.status()['registered'] is False
    owner.configure(dict(enabled(), enabled=False))
    assert owner.configure(enabled())['registered'] is True


def test_dll_failure_does_not_claim_success_or_leave_installed_filter(native):
    native.library.raise_register = True
    owner = native.create()
    status = owner.configure(enabled())
    assert status['registered'] is False and '注册失败' in status['error']
    assert owner._filter_installed is False
    assert owner._hotkey_id is None and not native.library.active


def test_unregister_failure_blocks_new_registration_and_ignores_old_key(native):
    owner = native.create()
    owner.configure(enabled())
    old_id, old_parameter = owner._hotkey_id, owner._message_parameter
    native.library.unregister_failure = True
    status = owner.configure(enabled('Ctrl+Shift+F9'))
    assert status['registered'] is False and '未能释放' in status['error']
    assert len(native.library.RegisterHotKey.calls) == 1
    assert not owner._filter_installed
    assert send(owner, identifier=old_id, parameter=old_parameter) == (False, 0)
    native.library.unregister_failure = False
    owner.close()
    assert native.library.active == {}


def test_unavailable_platform_has_honest_disabled_registration(native, monkeypatch):
    monkeypatch.setattr(hotkeys.sys, 'platform', 'linux')
    status = native.create().configure(enabled())
    assert status['enabled'] is True and status['registered'] is False
    assert '不支持' in status['error'] and not native.library.RegisterHotKey.calls


def test_main_qt_thread_is_required_for_registration(native):
    owner = native.create()
    results = []
    worker = threading.Thread(target=lambda: results.append(owner.configure(enabled())))
    worker.start()
    worker.join(timeout=2.)
    assert not worker.is_alive()
    assert results[0]['registered'] is False and '主线程' in results[0]['error']
    assert not native.library.RegisterHotKey.calls
    assert owner.configure(enabled())['registered'] is True


def test_parent_destruction_releases_native_registration(native):
    parent = QObject()
    owner = native.create(parent=parent)
    owner.configure(enabled())
    parent.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert native.library.active == {}
    assert owner._filter_installed is False and owner._closed is True


def test_application_quit_releases_native_registration(native):
    owner = native.create()
    owner.configure(enabled())
    native.app.aboutToQuit.emit()
    assert native.library.active == {}
    assert owner.status()['enabled'] is False and owner.status()['registered'] is False


def test_callback_exception_cannot_escape_qt_virtual_callback(native):
    def failed_pause():
        raise OSError('save failed')
    owner = native.create(failed_pause)
    owner.configure(enabled())
    assert send(owner) == (True, 0)
    assert '未能完成' in owner.status()['error']
    assert owner.status()['registered'] is True


def test_win32_functions_have_pointer_safe_typed_abi(native, monkeypatch):
    monkeypatch.setattr(C, 'WinDLL', lambda *_, **__: native.library)
    user32 = _REAL_LOAD_USER32()
    assert user32.RegisterHotKey.argtypes == [W.HWND, W.INT, W.UINT, W.UINT]
    assert user32.RegisterHotKey.restype is W.BOOL
    assert user32.UnregisterHotKey.argtypes == [W.HWND, W.INT]
    assert user32.UnregisterHotKey.restype is W.BOOL
    assert hotkeys._MSG.wParam.size == C.sizeof(C.c_void_p)
    assert hotkeys._MSG.lParam.size == C.sizeof(C.c_void_p)
    assert hotkeys._MSG.message.size == hotkeys._MSG.time.size == 4

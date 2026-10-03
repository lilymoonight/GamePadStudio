"""Carbon ownership and Qt dispatch using fake native events only."""
import ctypes as C
from types import SimpleNamespace
import threading
import sys

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QTimer

from gamepadstudio import emergency_hotkey as hotkeys
from gamepadstudio import macos_hotkey as mac


class Function:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


class FakeCarbon:
    def __init__(self):
        self.handlers = {}
        self.hotkeys = {}
        self.events = {}
        self.queue = []
        self.order = []
        self.register_code = 0
        self.install_code = 0
        self.unregister_code = 0
        self.remove_code = 0
        self.receive_code = 0
        self.next_ref = 0x1234567800000001
        for name in ('GetApplicationEventTarget', 'InstallEventHandler', 'RemoveEventHandler',
                     'RegisterEventHotKey', 'UnregisterEventHotKey', 'GetEventClass', 'GetEventKind',
                     'GetEventParameter', 'ReceiveNextEvent', 'SendEventToEventTarget', 'ReleaseEvent'):
            setattr(self, name, Function(getattr(self, '_' + name)))

    @staticmethod
    def _write(pointer, value):
        C.cast(pointer, C.POINTER(C.c_void_p))[0] = value

    def _GetApplicationEventTarget(self):
        return 0x1234567887654321

    def _InstallEventHandler(self, target, callback, count, types, context, output):
        self.order.append(('install', target, count, [(types[i].eventClass, types[i].eventKind)
                                                    for i in range(count)]))
        if self.install_code:
            return self.install_code
        ref = self.next_ref
        self.next_ref += 1
        self.handlers[ref] = callback
        self._write(output, ref)
        return 0

    def _RemoveEventHandler(self, ref):
        self.order.append(('remove', ref.value))
        if self.remove_code:
            return self.remove_code
        del self.handlers[ref.value]
        return 0

    def _RegisterEventHotKey(self, keycode, modifiers, identifier, target, options, output):
        self.order.append(('register', keycode, modifiers, identifier.id, target, options))
        if self.register_code:
            return self.register_code
        ref = self.next_ref
        self.next_ref += 1
        self.hotkeys[ref] = (identifier.signature, identifier.id)
        self._write(output, ref)
        return 0

    def _UnregisterEventHotKey(self, ref):
        self.order.append(('unregister', ref.value))
        if self.unregister_code:
            return self.unregister_code
        del self.hotkeys[ref.value]
        return 0

    def event(self, owner, kind=mac.HOTKEY_PRESSED, **overrides):
        self.next_ref += 1
        ref = self.next_ref
        self.events[ref] = dict(event_class=mac.KEYBOARD_EVENT, kind=kind,
                               signature=mac.SIGNATURE, identifier=owner._mac_hotkey._identifier.id,
                               actual_type=mac.HOTKEY_ID_TYPE, size=8, code=0, **overrides)
        return ref

    def _GetEventClass(self, event):
        return self.events[event]['event_class']

    def _GetEventKind(self, event):
        return self.events[event]['kind']

    def _GetEventParameter(self, event, name, desired_type, actual_type, size, actual_size, output):
        assert name == mac.DIRECT_OBJECT and desired_type == mac.HOTKEY_ID_TYPE and size == 8
        data = self.events[event]
        C.cast(actual_type, C.POINTER(C.c_uint32))[0] = data['actual_type']
        C.cast(actual_size, C.POINTER(C.c_ulong))[0] = data['size']
        C.cast(output, C.POINTER(mac.EventHotKeyID))[0] = mac.EventHotKeyID(
            data['signature'], data['identifier'])
        return data['code']

    def _ReceiveNextEvent(self, count, types, timeout, pull, output):
        assert count == 2 and timeout == 0.0 and pull is True
        assert [(types[i].eventClass, types[i].eventKind) for i in range(count)] == [
            (mac.KEYBOARD_EVENT, mac.HOTKEY_PRESSED), (mac.KEYBOARD_EVENT, mac.HOTKEY_RELEASED)]
        if self.receive_code:
            return self.receive_code
        if not self.queue:
            return mac.TIMED_OUT
        self._write(output, self.queue.pop(0))
        return 0

    def _SendEventToEventTarget(self, event, target):
        for callback in tuple(self.handlers.values()):
            result = callback(None, event.value, None)
            if result != mac.NOT_HANDLED:
                return result
        return mac.NOT_HANDLED

    def _ReleaseEvent(self, event):
        self.order.append(('release', event.value))
        del self.events[event.value]


@pytest.fixture
def native(monkeypatch):
    app = QCoreApplication.instance() or QCoreApplication([])
    library, owners = FakeCarbon(), []
    monkeypatch.setattr(hotkeys, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(mac, '_CarbonNative', lambda: library)

    def create(callback=None, parent=None):
        owner = hotkeys.EmergencyHotkey(callback or (lambda: None), parent)
        owners.append(owner)
        return owner

    yield SimpleNamespace(app=app, library=library, create=create)
    library.unregister_code = library.remove_code = 0
    for owner in owners:
        owner.close()


def enabled(shortcut=hotkeys.DEFAULT_SHORTCUT):
    return {'enabled': True, 'shortcut': shortcut}


@pytest.mark.parametrize(('value', 'canonical', 'modifiers'), [
    ('Alt+Control+F10', 'Ctrl+Alt+F10', 3),
    ('command+ctrl+x', 'Ctrl+Cmd+X', 10),
    ('Win+Shift+F12', 'Shift+Cmd+F12', 12),
    ('meta+Alt+F9', 'Alt+Cmd+F9', 9),
    ('Cmd+Shift+Alt+Ctrl+F20', 'Ctrl+Alt+Shift+Cmd+F20', 15),
    ('Ctrl+Alt+Del', 'Ctrl+Alt+Del', 3),
])
def test_mac_parser_keeps_physical_control_separate_from_command(native, value, canonical, modifiers):
    result = hotkeys.parse_shortcut(value)
    assert result[:2] == (canonical, modifiers)
    assert hotkeys.normalize_hotkey_settings(enabled(value), strict=True) == enabled(canonical)


@pytest.mark.parametrize('value', ['Ctrl+F10', 'Cmd+F10', 'Cmd+Meta+F10',
                                 'Ctrl+Alt+F21', 'Ctrl+Alt+Ins', 'Ctrl+Alt+PrintScreen'])
def test_mac_parser_rejects_unsafe_duplicate_or_unavailable_keys(native, value):
    with pytest.raises(ValueError):
        hotkeys.parse_shortcut(value)


def test_disabled_never_initializes_carbon(native, monkeypatch):
    def unexpected(*_):
        raise AssertionError('Disabled hotkeys must not touch Carbon')
    monkeypatch.setattr(mac, '_CarbonNative', unexpected)
    owner = native.create()
    assert owner.configure(None)['registered'] is False
    assert owner.configure(dict(enabled(), enabled=False))['error'] == ''
    assert owner._mac_hotkey is None and not native.library.order


@pytest.mark.parametrize(('platform', 'supported'), [('win32', True), ('darwin', True), ('linux', False)])
def test_capability_check_never_registers_or_loads_native_library(native, monkeypatch, platform, supported):
    monkeypatch.setattr(hotkeys.sys, 'platform', platform)
    assert hotkeys.emergency_hotkey_supported() is supported
    assert not native.library.order


def test_registers_exclusively_with_native_modifiers_and_stable_qt_owner(native):
    owner = native.create()
    assert owner.configure(enabled('Cmd+Ctrl+F10')) == dict(enabled('Ctrl+Cmd+F10'),
                                                         registered=True, error='')
    backend = owner._mac_hotkey
    register = next(item for item in native.library.order if item[0] == 'register')
    assert register[1:3] == (109, (1 << 12) | (1 << 8))
    assert register[4:] == (0x1234567887654321, mac.EXCLUSIVE)
    assert backend._hotkey.value > 0xFFFFFFFF and backend._handler.value > 0xFFFFFFFF
    assert backend._timer.isActive() and not owner._filter_installed
    assert id(backend) in mac._LIVE_HANDLERS
    owner.configure(enabled('Ctrl+Command+F10'))
    assert len([item for item in native.library.order if item[0] == 'register']) == 1


def test_press_fires_once_until_release_and_errors_stay_inside_callback(native):
    calls = []
    owner = native.create(lambda: calls.append('pause'))
    owner.configure(enabled())
    backend = owner._mac_hotkey
    press = native.library.event(owner)
    release = native.library.event(owner, mac.HOTKEY_RELEASED)
    assert backend._callback(None, press, None) == 0
    assert backend._callback(None, press, None) == 0
    assert calls == ['pause']
    assert backend._callback(None, release, None) == 0
    assert backend._callback(None, press, None) == 0
    assert calls == ['pause', 'pause']


@pytest.mark.parametrize('alteration', [dict(event_class=1), dict(kind=1), dict(signature=1),
                                       dict(identifier=0), dict(actual_type=1), dict(size=4), dict(code=-1)])
def test_unrelated_or_malformed_native_event_cannot_pause(native, alteration):
    calls = []
    owner = native.create(lambda: calls.append('pause'))
    owner.configure(enabled())
    ref = native.library.event(owner)
    native.library.events[ref].update(alteration)
    assert owner._mac_hotkey._callback(None, ref, None) == mac.NOT_HANDLED
    assert calls == []


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS Carbon and Qt event integration')
def test_qcore_loop_pumps_registered_events_and_releases_them(native):
    calls = []
    owner = native.create(lambda: calls.append('pause'))
    owner.configure(enabled())
    event = native.library.event(owner)
    native.library.queue.append(event)
    loop = QEventLoop()
    QTimer.singleShot(35, loop.quit)
    loop.exec()
    assert calls == ['pause'] and not native.library.queue
    assert ('release', event) in native.library.order


def test_callback_exception_is_reported_without_dropping_registration(native):
    def failed():
        raise OSError('save failed')
    owner = native.create(failed)
    owner.configure(enabled())
    assert owner._mac_hotkey._callback(None, native.library.event(owner), None) == 0
    assert '未能完成' in owner.status()['error'] and owner.status()['registered'] is True


def test_switch_disable_and_close_remove_resources_in_order(native):
    owner = native.create()
    owner.configure(enabled())
    first = owner._mac_hotkey._identifier.id
    owner.configure(enabled('Ctrl+Shift+F9'))
    second = owner._mac_hotkey._identifier.id
    assert second != first
    assert [item[0] for item in native.library.order] == [
        'install', 'register', 'unregister', 'remove', 'install', 'register']
    owner.configure(dict(enabled('Ctrl+Shift+F9'), enabled=False))
    assert not native.library.hotkeys and not native.library.handlers
    assert not owner._mac_hotkey._timer.isActive()
    assert id(owner._mac_hotkey) not in mac._LIVE_HANDLERS
    owner.close()
    owner.close()
    assert owner._app is None and owner._callback is None and not owner._quit_connected


@pytest.mark.parametrize('code', [mac.HOTKEY_EXISTS, -50])
def test_registration_failure_cleans_handler_and_reports_conflict(native, code):
    native.library.register_code = code
    owner = native.create()
    status = owner.configure(enabled())
    assert status['registered'] is False
    assert ('占用' if code == mac.HOTKEY_EXISTS else '注册失败') in status['error']
    assert not native.library.handlers and not native.library.hotkeys
    assert not owner._mac_hotkey.has_resources and not owner._mac_hotkey._timer.isActive()
    owner.configure(enabled())
    assert len([item for item in native.library.order if item[0] == 'register']) == 1


def test_install_failure_never_registers_hotkey(native):
    native.library.install_code = -50
    owner = native.create()
    assert owner.configure(enabled())['registered'] is False
    assert [item[0] for item in native.library.order] == ['install']


@pytest.mark.parametrize('failed_step', ['unregister_code', 'remove_code'])
def test_failed_cleanup_retains_native_callback_and_close_retries(native, failed_step):
    calls = []
    owner = native.create(lambda: calls.append('pause'))
    owner.configure(enabled())
    backend = owner._mac_hotkey
    event = native.library.event(owner)
    setattr(native.library, failed_step, -50)
    owner.close()
    assert owner.status()['registered'] is False and '未能释放' in owner.status()['error']
    assert backend.has_resources and id(backend) in mac._LIVE_HANDLERS
    assert not backend._timer.isActive()
    assert backend._callback(None, event, None) == mac.NOT_HANDLED and calls == []
    setattr(native.library, failed_step, 0)
    owner.close()
    assert not backend.has_resources and id(backend) not in mac._LIVE_HANDLERS
    assert not native.library.handlers and not native.library.hotkeys


def test_native_receive_failure_stops_listener_and_reports_unregistered(native):
    owner = native.create()
    owner.configure(enabled())
    native.library.receive_code = -50
    owner._mac_hotkey._pump_events()
    assert not owner.status()['registered'] and '监听失败' in owner.status()['error']
    assert not owner._mac_hotkey._timer.isActive()
    assert owner._mac_hotkey.has_resources  # Cleanup remains owned until close.
    owner.close()
    assert not native.library.handlers and not native.library.hotkeys


def test_native_dispatch_failure_still_releases_received_event(native, monkeypatch):
    owner = native.create()
    owner.configure(enabled())
    event = native.library.event(owner)
    native.library.queue.append(event)
    monkeypatch.setattr(native.library, 'SendEventToEventTarget', lambda *_: -50)
    owner._mac_hotkey._pump_events()
    assert not owner.status()['registered'] and '监听失败' in owner.status()['error']
    assert ('release', event) in native.library.order


def test_parent_destruction_unregisters_before_deleting_timer(native):
    parent = QObject()
    owner = native.create(parent=parent)
    owner.configure(enabled())
    parent.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert owner._closed and not native.library.hotkeys and not native.library.handlers


def test_mac_registration_is_main_thread_only_and_quit_releases(native):
    owner = native.create()
    status = []
    worker = threading.Thread(target=lambda: status.append(owner.configure(enabled())))
    worker.start()
    worker.join(timeout=2)
    assert status and '主线程' in status[0]['error'] and not native.library.order
    owner.configure(enabled())
    native.app.aboutToQuit.emit()
    assert not native.library.handlers and not native.library.hotkeys


def test_carbon_binding_preserves_pointer_and_native_count_widths(monkeypatch):
    library = FakeCarbon()
    monkeypatch.setattr(mac, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(mac.C, 'CDLL', lambda *_: library)
    native = mac._CarbonNative()
    assert native.GetApplicationEventTarget.restype is C.c_void_p
    assert native.RegisterEventHotKey.argtypes == [
        C.c_uint32, C.c_uint32, mac.EventHotKeyID, C.c_void_p, C.c_uint32, C.POINTER(C.c_void_p)]
    assert native.InstallEventHandler.argtypes[2] is C.c_ulong
    assert native.ReceiveNextEvent.argtypes[0] is C.c_ulong
    assert native.ReceiveNextEvent.argtypes[3] is C.c_ubyte
    assert native.GetEventParameter.argtypes[4:6] == [C.c_ulong, C.POINTER(C.c_ulong)]
    assert native.RegisterEventHotKey.restype is C.c_int32
    assert native.ReleaseEvent.restype is None
    assert C.sizeof(mac.EventHotKeyID) == C.sizeof(mac.EventTypeSpec) == 8

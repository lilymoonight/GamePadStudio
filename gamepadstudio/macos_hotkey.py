"""Carbon global hotkeys, without an event tap or Input Monitoring prompt.

Only registered hotkey press/release events are read. A nonblocking Qt timer
dispatches these events when the backend uses QCoreApplication rather than
Cocoa's GUI event loop. All Carbon calls run on the Qt main thread.
"""
from __future__ import annotations

import ctypes as C
import itertools
import sys

from PySide6.QtCore import QObject, QTimer, Signal

from .macos_actions import VK_TO_MAC


KEYBOARD_EVENT = int.from_bytes(b'keyb', 'big')
HOTKEY_PRESSED, HOTKEY_RELEASED = 5, 6
DIRECT_OBJECT = int.from_bytes(b'----', 'big')
HOTKEY_ID_TYPE = int.from_bytes(b'hkid', 'big')
SIGNATURE = int.from_bytes(b'GPSH', 'big')
NOT_HANDLED, TIMED_OUT, HOTKEY_EXISTS = -9874, -9875, -9878
EXCLUSIVE = 1
_IDS = itertools.count(1)
# If native cleanup fails, retain the callback's executable ctypes trampoline
# until cleanup can be retried. Letting it be collected would leave Carbon
# holding a dangling function pointer, even when the hotkey is inactive.
_LIVE_HANDLERS = {}


class EventHotKeyID(C.Structure):
    _fields_ = [('signature', C.c_uint32), ('id', C.c_uint32)]


class EventTypeSpec(C.Structure):
    _fields_ = [('eventClass', C.c_uint32), ('eventKind', C.c_uint32)]


EventHandler = C.CFUNCTYPE(C.c_int32, C.c_void_p, C.c_void_p, C.c_void_p)


class HotkeyRegistrationError(OSError):
    def __init__(self, code):
        self.code = code
        super().__init__(f'Carbon hotkey registration failed ({code})')


class _CarbonNative:
    def __init__(self):
        if sys.platform != 'darwin':
            raise OSError('Carbon hotkeys require macOS')
        self.library = C.CDLL('/System/Library/Frameworks/Carbon.framework/Carbon')
        pointer = C.POINTER(C.c_void_p)
        # MacTypes.h defines ItemCount and ByteCount as unsigned long, which
        # is 64-bit on modern macOS. Event class/type and OSStatus stay 32-bit.
        signatures = {
            'GetApplicationEventTarget': ([], C.c_void_p),
            'InstallEventHandler': (
                [C.c_void_p, EventHandler, C.c_ulong, C.POINTER(EventTypeSpec), C.c_void_p, pointer], C.c_int32),
            'RemoveEventHandler': ([C.c_void_p], C.c_int32),
            'RegisterEventHotKey': (
                [C.c_uint32, C.c_uint32, EventHotKeyID, C.c_void_p, C.c_uint32, pointer], C.c_int32),
            'UnregisterEventHotKey': ([C.c_void_p], C.c_int32),
            'GetEventClass': ([C.c_void_p], C.c_uint32),
            'GetEventKind': ([C.c_void_p], C.c_uint32),
            'GetEventParameter': (
                [C.c_void_p, C.c_uint32, C.c_uint32, C.POINTER(C.c_uint32),
                 C.c_ulong, C.POINTER(C.c_ulong), C.c_void_p], C.c_int32),
            'ReceiveNextEvent': (
                [C.c_ulong, C.POINTER(EventTypeSpec), C.c_double, C.c_ubyte, pointer], C.c_int32),
            'SendEventToEventTarget': ([C.c_void_p, C.c_void_p], C.c_int32),
            'ReleaseEvent': ([C.c_void_p], None),
        }
        for name, (argtypes, restype) in signatures.items():
            function = getattr(self.library, name)
            function.argtypes, function.restype = argtypes, restype
            setattr(self, name, function)


def carbon_modifiers(modifiers):
    """Translate logical Alt/Ctrl/Shift/Cmd bits without Qt's Cocoa swap."""
    return sum(native for logical, native in ((1, 1 << 11), (2, 1 << 12),
                                             (4, 1 << 9), (8, 1 << 8))
               if modifiers & logical)


class MacHotkey(QObject):
    triggered = Signal()
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._native = _CarbonNative()
        self._hotkey = C.c_void_p()
        self._handler = C.c_void_p()
        self._target = None
        self._identifier = None
        self._active = False
        self._pressed = False
        self._types = (EventTypeSpec * 2)(EventTypeSpec(KEYBOARD_EVENT, HOTKEY_PRESSED),
                                        EventTypeSpec(KEYBOARD_EVENT, HOTKEY_RELEASED))
        self._callback = EventHandler(self._handle_event)
        self._timer = QTimer(self)
        self._timer.setInterval(10)
        self._timer.timeout.connect(self._pump_events)

    @property
    def has_resources(self):
        return bool(self._hotkey.value or self._handler.value)

    def register(self, vk, modifiers):
        if self.has_resources:
            raise OSError('Previous Carbon hotkey is still registered')
        if vk not in VK_TO_MAC or not 0 < modifiers < 16:
            raise ValueError('Unsupported macOS hotkey')
        identifier = next(_IDS)
        if identifier > 0xFFFFFFFF:
            raise OSError('Carbon hotkey identifiers exhausted')
        self._identifier = EventHotKeyID(SIGNATURE, identifier)
        self._target = self._native.GetApplicationEventTarget()
        if not self._target:
            raise OSError('Carbon application event target unavailable')
        code = self._native.InstallEventHandler(
            self._target, self._callback, len(self._types), self._types, None, C.byref(self._handler))
        if self._handler.value:
            _LIVE_HANDLERS[id(self)] = self
        if code or not self._handler.value:
            raise HotkeyRegistrationError(code or -1)
        code = self._native.RegisterEventHotKey(
            VK_TO_MAC[vk], carbon_modifiers(modifiers), self._identifier,
            self._target, EXCLUSIVE, C.byref(self._hotkey))
        if code or not self._hotkey.value:
            raise HotkeyRegistrationError(code or -1)
        self._pressed = False
        self._active = True
        self._timer.start()

    def unregister(self):
        self._active = False
        self._pressed = False
        self._timer.stop()
        try:
            if self._hotkey.value:
                if self._native.UnregisterEventHotKey(self._hotkey):
                    return False
                self._hotkey = C.c_void_p()
            if self._handler.value:
                if self._native.RemoveEventHandler(self._handler):
                    return False
                self._handler = C.c_void_p()
        except (OSError, AttributeError, TypeError, RuntimeError):
            return False
        _LIVE_HANDLERS.pop(id(self), None)
        self._identifier = None
        self._target = None
        return True

    def _fail(self):
        self._active = False
        self._timer.stop()
        self.failed.emit('紧急暂停快捷键监听失败，请使用界面上的暂停按钮并重新设置快捷键')

    def _handle_event(self, _next_handler, event, _context):
        # Never allow Python exceptions to escape the native callback.
        try:
            if not self._active or not event:
                return NOT_HANDLED
            kind = self._native.GetEventKind(event)
            if (self._native.GetEventClass(event) != KEYBOARD_EVENT
                    or kind not in (HOTKEY_PRESSED, HOTKEY_RELEASED)):
                return NOT_HANDLED
            identifier = EventHotKeyID()
            size = C.c_ulong()
            actual_type = C.c_uint32()
            code = self._native.GetEventParameter(
                event, DIRECT_OBJECT, HOTKEY_ID_TYPE, C.byref(actual_type),
                C.sizeof(identifier), C.byref(size), C.byref(identifier))
            if (code or size.value != C.sizeof(identifier) or actual_type.value != HOTKEY_ID_TYPE
                    or identifier.signature != SIGNATURE or identifier.id != self._identifier.id):
                return NOT_HANDLED
            if kind == HOTKEY_RELEASED:
                self._pressed = False
            elif not self._pressed:
                self._pressed = True
                self.triggered.emit()
            return 0
        except Exception:
            self._fail()
            return NOT_HANDLED

    def _pump_events(self):
        if not self._active:
            return
        try:
            # Process only registered hotkey notifications, leaving unrelated
            # Cocoa input in its queue. Bound work to keep the Qt loop responsive.
            for _ in range(16):
                if not self._active:
                    return
                event = C.c_void_p()
                code = self._native.ReceiveNextEvent(
                    len(self._types), self._types, 0.0, True, C.byref(event))
                if code == TIMED_OUT:
                    return
                if code or not event.value:
                    self._fail()
                    return
                try:
                    code = self._native.SendEventToEventTarget(event, self._target)
                finally:
                    self._native.ReleaseEvent(event)
                if code not in (0, NOT_HANDLED):
                    self._fail()
                    return
        except Exception:
            self._fail()

"""Public AppKit media events and IOKit modifier-lock APIs.

Loading bindings neither opens a device nor changes permissions. Returned
media events are retained CF objects; callers own their final CFRelease.
"""
import ctypes as C
import sys
import time


class NSPoint(C.Structure):
    _fields_ = [('x', C.c_double), ('y', C.c_double)]


class MediaEventNative:
    def __init__(self):
        if sys.platform != 'darwin':
            raise OSError('macOS 系统按键接口只能在 macOS 上初始化')
        self.appkit = C.CDLL('/System/Library/Frameworks/AppKit.framework/AppKit')
        self.objc = C.CDLL('/usr/lib/libobjc.A.dylib')
        self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.objc.objc_getClass.argtypes, self.objc.objc_getClass.restype = [C.c_char_p], C.c_void_p
        self.objc.sel_registerName.argtypes, self.objc.sel_registerName.restype = [C.c_char_p], C.c_void_p
        address = C.cast(self.objc.objc_msgSend, C.c_void_p).value
        self._object = C.CFUNCTYPE(C.c_void_p, C.c_void_p, C.c_void_p)(address)
        self._void = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p)(address)
        # NSEventType / modifier flags are NSUInteger; windowNumber and data
        # are NSInteger; subtype is short; NSPoint is two CGFloat doubles.
        self._other_event = C.CFUNCTYPE(
            C.c_void_p, C.c_void_p, C.c_void_p, C.c_ulong, NSPoint,
            C.c_ulong, C.c_double, C.c_long, C.c_void_p, C.c_short,
            C.c_long, C.c_long)(address)
        self.cf.CFRetain.argtypes, self.cf.CFRetain.restype = [C.c_void_p], C.c_void_p
        self.cf.CFRelease.argtypes, self.cf.CFRelease.restype = [C.c_void_p], None

    def _selector(self, name):
        return self.objc.sel_registerName(name)

    def create_event(self, key_type, down, flags=0):
        # NX_SYSDEFINED=14, NX_SUBTYPE_AUX_CONTROL_BUTTONS=8, with
        # NX_KEYDOWN/KEYUP (10/11) encoded in the second byte of data1.
        # No autorepeat: ref-counted ownership emits each transition once.
        pool_class = self.objc.objc_getClass(b'NSAutoreleasePool')
        pool = self._object(pool_class, self._selector(b'alloc'))
        pool = self._object(pool, self._selector(b'init'))
        try:
            data1 = (int(key_type) << 16) | ((10 if down else 11) << 8)
            event = self._other_event(
                self.objc.objc_getClass(b'NSEvent'),
                self._selector(b'otherEventWithType:location:modifierFlags:timestamp:windowNumber:context:subtype:data1:data2:'),
                14, NSPoint(0, 0), int(flags), time.monotonic(), 0, None, 8, data1, -1)
            quartz = self._object(event, self._selector(b'CGEvent')) if event else None
            if not quartz:
                raise OSError('macOS 无法创建多媒体按键事件')
            return self.cf.CFRetain(quartz)
        finally:
            if pool:
                self._void(pool, self._selector(b'drain'))


class ModifierLockNative:
    """Set the real CapsLock latch through a short-lived parameter connection."""
    def __init__(self):
        if sys.platform != 'darwin':
            raise OSError('macOS 锁定键接口只能在 macOS 上初始化')
        self.iokit = C.CDLL('/System/Library/Frameworks/IOKit.framework/IOKit')
        self.system = C.CDLL('/usr/lib/libSystem.B.dylib')
        self.task = C.c_uint32.in_dll(self.system, 'mach_task_self_').value
        signatures = {
            'IOServiceMatching': ([C.c_char_p], C.c_void_p),
            'IOServiceGetMatchingService': ([C.c_uint32, C.c_void_p], C.c_uint32),
            'IOServiceOpen': ([C.c_uint32, C.c_uint32, C.c_uint32, C.POINTER(C.c_uint32)], C.c_int32),
            'IOObjectRelease': ([C.c_uint32], C.c_int32),
            'IOServiceClose': ([C.c_uint32], C.c_int32),
            'IOHIDSetModifierLockState': ([C.c_uint32, C.c_int, C.c_bool], C.c_int32),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.iokit, name)
            function.argtypes, function.restype = arguments, result

    @staticmethod
    def _check(result):
        if result:
            raise OSError(f'macOS 无法切换 CapsLock（0x{result & 0xffffffff:08x}）')

    def set_caps_lock(self, enabled):
        matching = self.iokit.IOServiceMatching(b'IOHIDSystem')
        if not matching:
            raise OSError('macOS 无法定位锁定键服务')
        # IOServiceGetMatchingService consumes the matching dictionary.
        service = self.iokit.IOServiceGetMatchingService(0, matching)
        if not service:
            raise OSError('macOS 锁定键服务不可用')
        connection = C.c_uint32()
        try:
            # kIOHIDParamConnectType=1, never the privileged server client.
            result = self.iokit.IOServiceOpen(service, self.task, 1, C.byref(connection))
        finally:
            self.iokit.IOObjectRelease(service)
        try:
            self._check(result)
            if not connection.value:
                raise OSError('macOS 未提供锁定键连接')
            # kIOHIDCapsLockState=1. NumLock is deliberately not changed:
            # macOS Clear and Windows keypad-mode switching differ.
            self._check(self.iokit.IOHIDSetModifierLockState(connection.value, 1, bool(enabled)))
        finally:
            if connection.value:
                self.iokit.IOServiceClose(connection.value)

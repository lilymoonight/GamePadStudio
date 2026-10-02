"""Opt-in exclusive access to the HID object already used by pygame's SDL.

SDL 2.28.4's macOS HIDAPI paths contain its live IOHIDDeviceRef. This is an
explicitly versioned bridge, not a general parser for stored device paths.
The reference is obtained only from the attached, selected SDL joystick while
holding SDL's joystick lock. No HID manager, additional reader or permission
request is created here. All other SDL versions/backends fail closed.
"""
import ctypes as C
import re
import sys
from contextlib import contextmanager, nullcontext


SUPPORTED_SDL = (2, 28, 4)
NOT_OPEN = 0xE00002CD
PATH_PATTERN = re.compile(rb'(USB|Bluetooth)_([0-9a-f]{4})_([0-9a-f]{4})_(0x[0-9a-f]{6,16})')


class SDLVersion(C.Structure):
    _fields_ = [('major', C.c_uint8), ('minor', C.c_uint8), ('patch', C.c_uint8)]


class IsolationUnavailable(RuntimeError):
    pass


def _bind(library, name, arguments, result):
    function = getattr(library, name)
    # Python methods are useful fake native functions in cross-platform tests.
    if isinstance(function, C._CFuncPtr):
        function.argtypes, function.restype = arguments, result
    return function


class IsolationNative:
    """Public IOKit/CF bindings; construction does not enumerate or open HID."""
    def __init__(self):
        if sys.platform != 'darwin':
            raise IsolationUnavailable('手柄独占访问只适用于 macOS')
        self.iokit = C.CDLL('/System/Library/Frameworks/IOKit.framework/IOKit')
        self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        for name, args, result in (
            ('IOHIDDeviceGetTypeID', [], C.c_ulong),
            ('IOHIDDeviceGetProperty', [C.c_void_p, C.c_void_p], C.c_void_p),
            ('IOHIDDeviceConformsTo', [C.c_void_p, C.c_uint32, C.c_uint32], C.c_bool),
            ('IOHIDDeviceOpen', [C.c_void_p, C.c_uint32], C.c_int32),
            ('IOHIDDeviceClose', [C.c_void_p, C.c_uint32], C.c_int32),
        ):
            _bind(self.iokit, name, args, result)
        for name, args, result in (
            ('CFGetTypeID', [C.c_void_p], C.c_ulong),
            ('CFRetain', [C.c_void_p], C.c_void_p),
            ('CFRelease', [C.c_void_p], None),
            ('CFStringCreateWithCString', [C.c_void_p, C.c_char_p, C.c_uint32], C.c_void_p),
            ('CFNumberGetTypeID', [], C.c_ulong),
            ('CFNumberGetValue', [C.c_void_p, C.c_int, C.c_void_p], C.c_bool),
            ('CFBooleanGetTypeID', [], C.c_ulong),
            ('CFBooleanGetValue', [C.c_void_p], C.c_bool),
        ):
            _bind(self.cf, name, args, result)

    def retain(self, reference):
        if not self.cf.CFRetain(reference):
            raise IsolationUnavailable('SDL 原生手柄引用已失效')

    def release(self, reference):
        self.cf.CFRelease(reference)

    def _number(self, reference, name):
        key = self.cf.CFStringCreateWithCString(None, name.encode('ascii'), 0x08000100)
        if not key:
            raise IsolationUnavailable('无法读取原生手柄属性')
        try:
            value = self.iokit.IOHIDDeviceGetProperty(reference, key)
            if not value or self.cf.CFGetTypeID(value) != self.cf.CFNumberGetTypeID():
                raise IsolationUnavailable('原生手柄属性缺失：' + name)
            output = C.c_int32()
            if not self.cf.CFNumberGetValue(value, 3, C.byref(output)):
                raise IsolationUnavailable('原生手柄属性无效：' + name)
            return output.value
        finally:
            self.cf.CFRelease(key)

    def validate(self, reference, vendor, product):
        if self.cf.CFGetTypeID(reference) != self.iokit.IOHIDDeviceGetTypeID():
            raise IsolationUnavailable('SDL 路径不是已知 IOHID 手柄对象')
        key = self.cf.CFStringCreateWithCString(None, b'GCSyntheticDevice', 0x08000100)
        if not key:
            raise IsolationUnavailable('无法确认是否为原生物理手柄')
        try:
            synthetic = self.iokit.IOHIDDeviceGetProperty(reference, key)
            if synthetic:
                if (self.cf.CFGetTypeID(synthetic) != self.cf.CFBooleanGetTypeID() or
                        self.cf.CFBooleanGetValue(synthetic)):
                    raise IsolationUnavailable('兼容层虚拟 HID 无法保证隔离物理手柄，请选择物理手柄接口')
        finally:
            self.cf.CFRelease(key)
        if (self._number(reference, 'VendorID'), self._number(reference, 'ProductID')) != (vendor, product):
            raise IsolationUnavailable('SDL 与原生手柄身份不一致')
        if (self._number(reference, 'PrimaryUsagePage') != 1 or
                self._number(reference, 'PrimaryUsage') not in (4, 5)):
            raise IsolationUnavailable('仅支持原生 joystick/gamepad 手柄独占访问')
        # A composite keyboard/mouse must never be seized through this feature.
        if any(self.iokit.IOHIDDeviceConformsTo(reference, 1, usage) for usage in (1, 2, 6, 7)):
            raise IsolationUnavailable('拒绝独占包含键盘或鼠标接口的设备')

    def open(self, reference, options):
        return int(self.iokit.IOHIDDeviceOpen(reference, options)) & 0xFFFFFFFF

    def close(self, reference):
        return int(self.iokit.IOHIDDeviceClose(reference, 0)) & 0xFFFFFFFF


class ControllerIsolation:
    def __init__(self, device, native=None):
        self.device = device
        self.native = native
        self.enabled = False
        self.active = False
        self.reference = None
        self.instance_id = None
        self.joystick = None
        self.restore_pending = False
        self.state = 'off'
        self.reason = ''
        self.supported = True
        try:
            self._check_backend()
        except Exception as exc:
            self.supported = False
            self.state, self.reason = 'unavailable', str(exc)

    def _check_backend(self):
        lib = self.device.lib
        specifications = (
            ('SDL_GetVersion', [C.POINTER(SDLVersion)], None),
            ('SDL_LockJoysticks', [], None), ('SDL_UnlockJoysticks', [], None),
            ('SDL_JoystickPath', [C.c_void_p], C.c_char_p),
            ('SDL_JoystickPathForIndex', [C.c_int], C.c_char_p),
        )
        try:
            for name, args, result in specifications:
                _bind(lib, name, args, result)
        except AttributeError as exc:
            raise IsolationUnavailable('当前 SDL 未提供受支持的手柄路径/锁接口') from exc
        version = SDLVersion()
        lib.SDL_GetVersion(C.byref(version))
        if (version.major, version.minor, version.patch) != SUPPORTED_SDL:
            raise IsolationUnavailable('原生手柄隔离尚未验证当前 SDL 版本；需要 SDL 2.28.4')

    @contextmanager
    def _locked(self):
        with getattr(self.device, '_io_lock', None) or nullcontext():
            self.device.lib.SDL_LockJoysticks()
            try:
                yield
            finally:
                self.device.lib.SDL_UnlockJoysticks()

    def _identity(self):
        device, lib = self.device, self.device.lib
        if not device.handle:
            raise IsolationUnavailable('请先连接并选择手柄')
        joystick = device.handle if device.is_raw_joystick else lib.SDL_GameControllerGetJoystick(device.handle)
        if not joystick or not lib.SDL_JoystickGetAttached(joystick):
            raise IsolationUnavailable('所选手柄已断开')
        instance = lib.SDL_JoystickInstanceID(joystick)
        if instance < 0 or instance != device.instance_id:
            raise IsolationUnavailable('所选 SDL 手柄已变化')
        index = next((index for index in range(lib.SDL_NumJoysticks())
                      if lib.SDL_JoystickGetDeviceInstanceID(index) == instance), None)
        if index is None:
            raise IsolationUnavailable('所选手柄不在当前 SDL 设备列表中')
        # Read both native paths now. Cached metadata/config/IPC paths are never
        # used as a pointer source; the global lock prevents index reuse here.
        path = lib.SDL_JoystickPathForIndex(index)
        if not isinstance(path, bytes) or path != lib.SDL_JoystickPath(joystick):
            raise IsolationUnavailable('SDL 当前设备路径与已打开手柄不一致')
        match = PATH_PATTERN.fullmatch(path)
        if not match:
            raise IsolationUnavailable('当前手柄不是已验证的 SDL macOS HIDAPI 后端')
        vendor, product, reference = (int(match.group(2), 16), int(match.group(3), 16), int(match.group(4), 16))
        if (not reference or reference % C.sizeof(C.c_void_p) or
                reference >= (1 << (8 * C.sizeof(C.c_void_p))) or
                (vendor, product) != (lib.SDL_JoystickGetDeviceVendor(index), lib.SDL_JoystickGetDeviceProduct(index))):
            raise IsolationUnavailable('SDL 原生路径身份无效')
        return reference, joystick, instance, vendor, product

    def _still_attached(self):
        device, lib = self.device, self.device.lib
        if not device.handle or device.instance_id != self.instance_id:
            return False
        joystick = device.handle if device.is_raw_joystick else lib.SDL_GameControllerGetJoystick(device.handle)
        return bool(joystick == self.joystick and lib.SDL_JoystickGetAttached(joystick))

    def _forget(self):
        if self.reference is not None:
            self.native.release(self.reference)
        self.reference = self.joystick = self.instance_id = None
        self.active = self.restore_pending = False

    @staticmethod
    def _error(operation, result):
        return OSError(f'{operation}失败（IOKit 0x{result:08x}）')

    def _restore(self):
        if self.reference is None:
            return
        if not self._still_attached():
            # Old SDL explicitly avoids IOHIDDeviceClose after removal. Keep
            # that lifecycle rule; the unplugged hardware cannot stay seized.
            self._forget()
            return
        self.active = False
        self.restore_pending = True
        result = self.native.close(self.reference)
        if result not in (0, NOT_OPEN):
            raise self._error('释放手柄独占访问', result)
        result = self.native.open(self.reference, 0)
        if result:
            raise self._error('恢复手柄共享访问', result)
        self._forget()

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        if not self.supported:
            return self.status()
        try:
            with self._locked():
                if self.reference is not None and not self._still_attached():
                    self._forget()
                if not self.enabled:
                    self._restore()
                    self.state, self.reason = 'off', ''
                elif self.active:
                    return self._status()
                elif self.restore_pending:
                    raise IsolationUnavailable('共享访问尚未恢复，请先关闭隔离并重试')
                else:
                    self._acquire()
                    self.state, self.reason = 'active', ''
        except Exception as exc:
            if self.reference is not None and not self.enabled:
                self.active = False
                self.restore_pending = True
            self.state = 'error' if self.reference is not None else 'unavailable'
            self.reason = str(exc)
        return self._status()

    def _acquire(self):
        reference, joystick, instance, vendor, product = self._identity()
        native = self.native or IsolationNative()
        self.native = native
        native.retain(reference)
        try:
            native.validate(reference, vendor, product)
        except Exception:
            native.release(reference)
            raise
        self.reference, self.joystick, self.instance_id = reference, joystick, instance
        # Re-opening an already-open IOHID client ignores new seize flags.
        # Close the shared link first; existing callbacks/run loop are retained.
        self.restore_pending = True
        try:
            result = native.close(reference)
            if result not in (0, NOT_OPEN):
                raise self._error('切换手柄共享访问', result)
            result = native.open(reference, 1)
            if result:
                raise self._error('启用手柄独占访问', result)
            if not self._still_attached():
                self._forget()
                raise IsolationUnavailable('手柄在启用隔离期间断开')
            self.active, self.restore_pending = True, False
        except Exception as exc:
            try:
                # Even ExclusiveAccess failures can register an opened client.
                # Close that attempt before opening the same object shared.
                self._restore()
            except Exception as restoration:
                raise OSError(f'{exc}；{restoration}，请关闭隔离或重新连接手柄') from exc
            raise

    def _status(self):
        return {'supported': self.supported, 'enabled': self.enabled,
                'active': self.active, 'status': self.state, 'reason': self.reason,
                'restore_pending': self.restore_pending}

    def status(self):
        if self.reference is not None:
            try:
                with self._locked():
                    if not self._still_attached():
                        self._forget()
                        self.state, self.reason = 'unavailable', '所选手柄已断开'
            except Exception as exc:
                self.active = False
                self.restore_pending = True
                self.state, self.reason = 'error', str(exc)
        return self._status()

    def forget_closed_handle(self):
        """Called after SDL closes its owned handle, even if restoration failed."""
        failed = self.restore_pending
        self._forget()
        self.enabled = False
        self.state = 'error' if failed else 'off'

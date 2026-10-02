"""macOS native input output, with no PyObjC or permission prompt at startup.

Profiles keep their Windows virtual-key values; only this boundary translates
them to macOS hardware keycodes. These are physical ANSI keyboard positions,
so characters follow the user's current keyboard layout, as on Windows.
"""
import ctypes as C
import sys
import time

from .actions import WindowsActions, parse_keys


# Carbon Events.h hardware keycodes. Unsupported Windows-only keys are absent,
# rather than being mapped to a key with different behavior (Insert -> Help).
VK_TO_MAC = {
    8: 51, 9: 48, 13: 36, 16: 56, 17: 59, 18: 58, 20: 57, 27: 53,
    32: 49, 33: 116, 34: 121, 35: 119, 36: 115,
    37: 123, 38: 126, 39: 124, 40: 125, 46: 117, 91: 55, 93: 110,
    0x6A: 67, 0x6B: 69, 0x6D: 78, 0x6E: 65, 0x6F: 75,
    0xBA: 41, 0xBB: 24, 0xBC: 43, 0xBD: 27, 0xBE: 47, 0xBF: 44,
    0xC0: 50, 0xDB: 33, 0xDC: 42, 0xDD: 30, 0xDE: 39,
}
VK_TO_MAC.update(dict(zip(range(48, 58), (29, 18, 19, 20, 21, 23, 22, 26, 28, 25))))
VK_TO_MAC.update(dict(zip(range(65, 91), (
    0, 11, 8, 2, 14, 3, 5, 4, 34, 38, 40, 37, 46,
    45, 31, 35, 12, 15, 1, 17, 32, 9, 13, 7, 16, 6,
))))
VK_TO_MAC.update(dict(zip(range(0x60, 0x6A), (82, 83, 84, 85, 86, 87, 88, 89, 91, 92))))
VK_TO_MAC.update(dict(zip(range(112, 132), (
    122, 120, 99, 118, 96, 97, 98, 100, 101, 109,
    103, 111, 105, 107, 113, 106, 64, 79, 80, 90,
))))

MODIFIER_FLAGS = {16: 1 << 17, 17: 1 << 18, 18: 1 << 19, 91: 1 << 20}
CAPS_LOCK_FLAG = 1 << 16
# Public NX_KEYTYPE_* values from IOKit/hidsystem/ev_keymap.h.
MEDIA_KEYS = {0xAD: 7, 0xAE: 1, 0xAF: 0, 0xB3: 16}
SUPPORTED_KEYS = frozenset(VK_TO_MAC) | frozenset(MEDIA_KEYS)
UNSUPPORTED_KEY_REASONS = {
    19: 'Pause 需要目标应用提供自己的暂停快捷键；macOS 没有对应的通用硬件按键输出。',
    44: 'PrintScreen 需要绑定明确的截图动作；macOS 没有对应的通用硬件按键输出。',
    45: 'Insert 的插入／覆盖作用需使用目标应用提供的快捷键。',
    144: 'macOS 数字区的 Clear 与 Windows NumLock 的数字／导航切换作用不同。',
    145: 'ScrollLock 的滚动锁定作用需使用目标应用提供的快捷键。',
    **{key: 'F21–F24 在 AppKit 有逻辑函数字符，但没有公开的通用硬件键码输出；目标应用可能忽略函数字符。'
       for key in range(132, 136)},
}
PERMISSION_REASON = (
    'macOS 尚未授权键鼠映射。请在「系统设置 → 隐私与安全性 → 辅助功能」'
    '允许 Gamepad Studio；从源码运行时，请允许启动它的 Python、终端或 Codex，'
    '然后重新检查权限。'
)


class CGPoint(C.Structure):
    _fields_ = [('x', C.c_double), ('y', C.c_double)]


class CGSize(C.Structure):
    _fields_ = [('width', C.c_double), ('height', C.c_double)]


class CGRect(C.Structure):
    _fields_ = [('origin', CGPoint), ('size', CGSize)]


class _QuartzNative:
    """Typed public CoreGraphics APIs; constructing this never posts input."""
    def __init__(self):
        if sys.platform != 'darwin':
            raise OSError('macOS 输入后端只能在 macOS 上初始化')
        self.cg = C.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        signatures = {
            'CGPreflightPostEventAccess': ([], C.c_bool),
            'CGRequestPostEventAccess': ([], C.c_bool),
            'CGEventCreate': ([C.c_void_p], C.c_void_p),
            'CGEventCreateKeyboardEvent': ([C.c_void_p, C.c_uint16, C.c_bool], C.c_void_p),
            'CGEventCreateMouseEvent': ([C.c_void_p, C.c_uint32, CGPoint, C.c_uint32], C.c_void_p),
            # Fixed-arity version avoids a variadic ABI on Apple Silicon.
            'CGEventCreateScrollWheelEvent2': (
                [C.c_void_p, C.c_uint32, C.c_uint32, C.c_int32, C.c_int32, C.c_int32], C.c_void_p),
            'CGEventGetLocation': ([C.c_void_p], CGPoint),
            'CGEventSetFlags': ([C.c_void_p, C.c_uint64], None),
            'CGEventSetIntegerValueField': ([C.c_void_p, C.c_uint32, C.c_int64], None),
            'CGEventSourceFlagsState': ([C.c_int32], C.c_uint64),
            'CGEventPost': ([C.c_uint32, C.c_void_p], None),
            'CGGetDisplaysWithPoint': (
                [CGPoint, C.c_uint32, C.POINTER(C.c_uint32), C.POINTER(C.c_uint32)], C.c_int32),
            'CGDisplayBounds': ([C.c_uint32], CGRect),
            'CGWarpMouseCursorPosition': ([CGPoint], C.c_int32),
        }
        for name, (argtypes, restype) in signatures.items():
            function = getattr(self.cg, name)
            function.argtypes, function.restype = argtypes, restype
            setattr(self, name, function)
        self.CFRelease = self.cf.CFRelease
        self.CFRelease.argtypes, self.CFRelease.restype = [C.c_void_p], None
        self._media = None
        self._locks = None

    def create_media_event(self, key_type, down):
        if self._media is None:
            from .macos_system_keys import MediaEventNative
            self._media = MediaEventNative()
        return self._media.create_event(key_type, down)

    def set_caps_lock_state(self, enabled):
        if self._locks is None:
            from .macos_system_keys import ModifierLockNative
            self._locks = ModifierLockNative()
        self._locks.set_caps_lock(enabled)

    def cursor_position(self):
        event = self.CGEventCreate(None)
        if not event:
            raise OSError('macOS 无法读取鼠标位置')
        try:
            return self.CGEventGetLocation(event)
        finally:
            self.CFRelease(event)

    def display_bounds_at(self, point):
        display = C.c_uint32()
        count = C.c_uint32()
        error = self.CGGetDisplaysWithPoint(point, 1, C.byref(display), C.byref(count))
        return self.CGDisplayBounds(display.value) if not error and count.value else None


class _MacWorkspace:
    """Read the foreground executable using NSWorkspace without AppleScript."""
    def __init__(self):
        # Loading AppKit registers the classes before objc_getClass is used.
        self.appkit = C.CDLL('/System/Library/Frameworks/AppKit.framework/AppKit')
        self.objc = C.CDLL('/usr/lib/libobjc.A.dylib')
        self.objc.objc_getClass.argtypes, self.objc.objc_getClass.restype = [C.c_char_p], C.c_void_p
        self.objc.sel_registerName.argtypes, self.objc.sel_registerName.restype = [C.c_char_p], C.c_void_p
        address = C.cast(self.objc.objc_msgSend, C.c_void_p).value
        self._send_object = C.CFUNCTYPE(C.c_void_p, C.c_void_p, C.c_void_p)(address)
        self._send_text = C.CFUNCTYPE(C.c_char_p, C.c_void_p, C.c_void_p)(address)
        self._send_void = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p)(address)

    def _message(self, obj, selector):
        return self._send_object(obj, self.objc.sel_registerName(selector)) if obj else None

    def foreground_process_name(self):
        pool = self._message(self.objc.objc_getClass(b'NSAutoreleasePool'), b'alloc')
        pool = self._message(pool, b'init')
        try:
            workspace = self._message(self.objc.objc_getClass(b'NSWorkspace'), b'sharedWorkspace')
            app = self._message(workspace, b'frontmostApplication')
            url = self._message(app, b'executableURL')
            name = self._message(url, b'lastPathComponent') or self._message(app, b'localizedName')
            raw = self._send_text(name, self.objc.sel_registerName(b'UTF8String')) if name else None
            return raw.decode('utf-8', errors='replace').lower() if raw else ''
        finally:
            if pool:
                self._send_void(pool, self.objc.sel_registerName(b'drain'))


def _permission_status(native):
    granted = bool(native.CGPreflightPostEventAccess())
    return {'supported': True, 'granted': granted, 'reason': '' if granted else PERMISSION_REASON}


def input_permission_status():
    return _permission_status(_QuartzNative())


def request_input_permission():
    native = _QuartzNative()
    native.CGRequestPostEventAccess()
    return _permission_status(native)


class MacActions(WindowsActions):
    """Share input ownership and cleanup behavior, replace every Windows API."""
    def __init__(self, native=None, workspace=None, window_backend=None):
        self.held = {}
        self.held_mouse = set()
        self.held_gamepad_buttons = set()
        self.held_turbo = {}
        self._posted_keys = set()
        self.native = native if native is not None else _QuartzNative()
        self._workspace = workspace
        self._window_backend = window_backend

    def input_permission_status(self):
        return _permission_status(self.native)

    def request_input_permission(self):
        self.native.CGRequestPostEventAccess()
        return self.input_permission_status()

    @staticmethod
    def supports_key(value):
        try:
            keys = parse_keys(value)
        except ValueError:
            return False
        return bool(keys) and all(key in SUPPORTED_KEYS for key in keys)

    @staticmethod
    def key_capability(value):
        """Explain unavailable keys without replacing them with another key."""
        try:
            keys = parse_keys(value)
        except ValueError as exc:
            return {'supported': False, 'reason': str(exc)}
        missing = [key for key in keys if key not in SUPPORTED_KEYS]
        if not keys or missing:
            reason = (UNSUPPORTED_KEY_REASONS.get(missing[0], '此按键暂不支持 macOS 输出。')
                      if missing else '没有可输出的按键。')
            if isinstance(value, str) and any(part.strip().casefold() in ('calc', 'calculator')
                                              for part in value.split('+')):
                reason = '当前配置将 Calc 与 F24 存为同一键值，尚不能区分计算器启动与 F24 输出。'
            return {'supported': False, 'reason': reason}
        return {'supported': True, 'reason': ''}

    def _require_permission(self):
        if not self.native.CGPreflightPostEventAccess():
            raise PermissionError(PERMISSION_REASON)

    def _hold_keys(self, keys, down):
        keys = list(keys)
        # Validate the complete chord before recording ownership or posting its
        # first modifier, so an unsupported final key cannot stick Ctrl down.
        if any(key not in SUPPORTED_KEYS for key in keys):
            key = next(key for key in keys if key not in SUPPORTED_KEYS)
            raise ValueError('macOS: ' + UNSUPPORTED_KEY_REASONS.get(key, '此按键暂不支持输出。'))
        if down and keys:
            self._require_permission()
        super()._hold_keys(keys, down)

    def _flags(self, key=None, down=None):
        posted = self._posted_keys.copy()
        owned = posted | ({key} if key is not None else set())
        if key is not None:
            posted.add(key) if down else posted.discard(key)
        flags = int(self.native.CGEventSourceFlagsState(1))
        # Preserve physical modifiers while replacing the modifiers owned by
        # this backend with the state after this particular transition.
        for code in owned:
            flags &= ~MODIFIER_FLAGS.get(code, 0)
        for code in posted:
            flags |= MODIFIER_FLAGS.get(code, 0)
        return flags

    def _post(self, event, key=None, down=None, caps_state=None):
        if not event:
            raise OSError('macOS 无法创建键鼠输入事件')
        try:
            flags = self._flags(key, down)
            if caps_state is not None:
                flags = (flags | CAPS_LOCK_FLAG) if caps_state else (flags & ~CAPS_LOCK_FLAG)
            self.native.CGEventSetFlags(event, flags)
            self.native.CGEventPost(0, event)  # kCGHIDEventTap
        finally:
            self.native.CFRelease(event)

    def _send(self, keys, down):
        if not keys:
            return
        self._require_permission()
        events = []
        try:
            # Create the complete batch before posting its first modifier.
            # This avoids partial presses if Quartz cannot allocate a later
            # event. Ownership cleanup still handles any actual post failure.
            for key in keys:
                event = (self.native.create_media_event(MEDIA_KEYS[key], down) if key in MEDIA_KEYS
                         else self.native.CGEventCreateKeyboardEvent(None, VK_TO_MAC[key], down))
                if not event:
                    raise OSError('macOS 无法创建按键输入事件')
                events.append((key, event))
            for index, (key, event) in enumerate(events):
                caps_state = None
                if key == 20 and down:
                    # Toggle only a fresh down transition. Release, pause and
                    # failed-output cleanup release the key without undoing
                    # the persistent latch, matching a physical lock key.
                    caps_state = not bool(int(self.native.CGEventSourceFlagsState(1)) & CAPS_LOCK_FLAG)
                    self.native.set_caps_lock_state(caps_state)
                events[index] = (key, None)  # _post always releases this event.
                self._post(event, key, down, caps_state)
                self._posted_keys.add(key) if down else self._posted_keys.discard(key)
        finally:
            for _, event in events:
                if event:
                    self.native.CFRelease(event)

    def mouse_button(self, button='left', down=True):
        if button not in ('left', 'right', 'middle'):
            raise ValueError(f'暂不支持鼠标按键 {button}')
        self._require_permission()
        position = self.native.cursor_position()
        number, pressed, released = {'left': (0, 1, 2), 'right': (1, 3, 4), 'middle': (2, 25, 26)}[button]
        # Ownership precedes posting; failed releases remain tracked for retry.
        if down:
            self.held_mouse.add(button)
        event = self.native.CGEventCreateMouseEvent(None, pressed if down else released, position, number)
        self._post(event)
        if not down:
            self.held_mouse.discard(button)

    def move_mouse(self, dx, dy):
        dx, dy = int(dx), int(dy)
        if not dx and not dy:
            return
        self._require_permission()
        current = self.native.cursor_position()
        destination = CGPoint(current.x + dx, current.y + dy)
        event_type, button = 5, 0  # moved
        for name, drag_type, number in (('left', 6, 0), ('right', 7, 1), ('middle', 27, 2)):
            if name in self.held_mouse:
                event_type, button = drag_type, number
                break
        event = self.native.CGEventCreateMouseEvent(None, event_type, destination, button)
        if not event:
            raise OSError('macOS 无法创建鼠标移动事件')
        try:
            self.native.CGEventSetIntegerValueField(event, 4, dx)  # kCGMouseEventDeltaX
            self.native.CGEventSetIntegerValueField(event, 5, dy)  # kCGMouseEventDeltaY
        except Exception:
            self.native.CFRelease(event)
            raise
        self._post(event)

    def scroll(self, steps):
        steps = int(steps)
        if not steps:
            return
        self._require_permission()
        event = self.native.CGEventCreateScrollWheelEvent2(None, 1, 1, steps, 0, 0)  # line units
        self._post(event)

    def media(self, action):
        # Same action names and ownership/release semantics as Windows.
        super().media(action)

    def get_foreground_process_name(self):
        now = time.monotonic()
        if now - getattr(self, '_cached_pname_time', -1.) < .5:
            return self._cached_pname
        try:
            if self._workspace is None:
                self._workspace = _MacWorkspace()
            name = self._workspace.foreground_process_name()
        except Exception:
            name = ''
        self._cached_pname, self._cached_pname_time = name, now
        return name

    def is_nikki_game_focused(self):
        name = self.get_foreground_process_name().casefold()
        if any(marker in name for marker in ('starter', 'launcher', 'install', 'update', 'gamepadstudio')):
            return False
        if any(marker in name for marker in ('infinitynikki', 'x6game', 'nikki', 'genshin')):
            return True
        if 'game' not in name:
            return False
        # A generic process name is weaker evidence than a known game. Only
        # recenter when that executable owns the actual foreground window,
        # excluding this app and system windows through the shared selector.
        try:
            if self._window_backend is None:
                from .macos_windows import MacWindowBackend
                self._window_backend = MacWindowBackend()
            window = self._window_backend.foreground_window()
            if window is None or self._window_backend.excluded(window):
                return False
            bounds = window.bounds_points
            return (window.process_name.casefold() == name.casefold()
                    and bounds['width'] >= 640 and bounds['height'] >= 480)
        except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError):
            return False

    def guard_cursor_edge(self, margin=35, only_if_game=True):
        now = time.monotonic()
        if now - getattr(self, '_last_guard_check', -1.) < .08:
            return
        self._last_guard_check = now
        if only_if_game and not self.is_nikki_game_focused():
            return
        self._require_permission()
        point = self.native.cursor_position()
        bounds = self.native.display_bounds_at(point)
        if bounds is None or bounds.size.width <= 2 * margin or bounds.size.height <= 2 * margin:
            return
        x, y, width, height = bounds.origin.x, bounds.origin.y, bounds.size.width, bounds.size.height
        if (point.x <= x + margin or point.x >= x + width - margin
                or point.y <= y + margin or point.y >= y + height - margin):
            if self.native.CGWarpMouseCursorPosition(CGPoint(x + width / 2, y + height / 2)):
                raise OSError('macOS 拒绝了鼠标回中')

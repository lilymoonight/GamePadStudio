"""An opt-in Windows thread hotkey for stopping controller output."""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import itertools
import sys
import weakref

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, QThread


DEFAULT_SHORTCUT = 'Ctrl+Alt+F10'
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 1, 2, 4, 0x4000
WM_HOTKEY = 0x0312
_IDS = itertools.count(0x6000)
_MODIFIERS = {'ctrl': ('Ctrl', MOD_CONTROL), 'control': ('Ctrl', MOD_CONTROL),
              'alt': ('Alt', MOD_ALT), 'shift': ('Shift', MOD_SHIFT)}
_KEYS = {'enter': ('Enter', 13), 'return': ('Enter', 13),
         'esc': ('Esc', 27), 'escape': ('Esc', 27), 'space': ('Space', 32),
         'tab': ('Tab', 9), 'backspace': ('Backspace', 8),
         'delete': ('Del', 46), 'del': ('Del', 46), 'insert': ('Ins', 45), 'ins': ('Ins', 45),
         'home': ('Home', 36), 'end': ('End', 35),
         'pageup': ('PgUp', 33), 'pgup': ('PgUp', 33),
         'pagedown': ('PgDown', 34), 'pgdown': ('PgDown', 34), 'pgdn': ('PgDown', 34),
         'up': ('Up', 38), 'down': ('Down', 40), 'left': ('Left', 37), 'right': ('Right', 39)}
_KEYS.update({chr(number).casefold(): (chr(number), number)
              for number in (*range(48, 58), *range(65, 91))})
_KEYS.update({f'f{number}': (f'F{number}', 111 + number) for number in range(1, 25)})
_RESERVED = {'win', 'windows', 'meta', 'cmd', 'super', 'f12', 'print',
             'printscreen', 'prtsc', 'prtscn', 'snapshot'}
_FORMAT_ERROR = '请使用至少两个 Ctrl、Alt、Shift 修饰键加一个普通按键'


def parse_shortcut(shortcut):
    """Return a canonical single shortcut, Win32 modifiers and virtual key."""
    if not isinstance(shortcut, str) or not shortcut or len(shortcut) > 64:
        raise ValueError(_FORMAT_ERROR)
    if any(ord(char) < 32 for char in shortcut):
        raise ValueError(_FORMAT_ERROR)
    parts = [part.strip().casefold() for part in shortcut.split('+')]
    if any(part in _RESERVED for part in parts):
        raise ValueError('该快捷键由 Windows 或调试器保留，请选择其他按键')
    if len(parts) not in (3, 4) or not all(parts) or parts[-1] not in _KEYS:
        raise ValueError(_FORMAT_ERROR)
    modifiers, names = 0, set()
    for part in parts[:-1]:
        if part not in _MODIFIERS:
            raise ValueError(_FORMAT_ERROR)
        name, modifier = _MODIFIERS[part]
        if name in names:
            raise ValueError('修饰键不能重复')
        modifiers |= modifier
        names.add(name)
    if len(names) < 2:
        raise ValueError(_FORMAT_ERROR)
    name, vk = _KEYS[parts[-1]]
    if vk == 46 and modifiers & (MOD_ALT | MOD_CONTROL) == MOD_ALT | MOD_CONTROL:
        raise ValueError('Ctrl+Alt+Del 由 Windows 保留，请选择其他按键')
    canonical = '+'.join([item for item in ('Ctrl', 'Alt', 'Shift') if item in names] + [name])
    return canonical, modifiers, vk


def normalize_hotkey_settings(value, strict=False):
    defaults = {'enabled': False, 'shortcut': DEFAULT_SHORTCUT}
    try:
        if not isinstance(value, dict) or set(value) != {'enabled', 'shortcut'}:
            raise ValueError('紧急暂停快捷键设置格式错误')
        if type(value['enabled']) is not bool:
            raise ValueError('紧急暂停快捷键只能设为开启或关闭')
        canonical, _, _ = parse_shortcut(value['shortcut'])
        return {'enabled': value['enabled'], 'shortcut': canonical}
    except ValueError:
        if strict:
            raise
        return defaults


class _POINT(C.Structure):
    _fields_ = [('x', C.c_int32), ('y', C.c_int32)]


class _MSG(C.Structure):
    # Fixed DWORD/LONG widths plus pointer-sized WPARAM/LPARAM are correct on
    # both Windows ABIs; do not use c_long for a pointer-sized message field.
    _fields_ = [('hwnd', C.c_void_p), ('message', C.c_uint32),
                ('wParam', C.c_size_t), ('lParam', C.c_ssize_t),
                ('time', C.c_uint32), ('pt', _POINT), ('lPrivate', C.c_uint32)]


def _load_user32():
    if sys.platform != 'win32':
        return None
    user32 = C.WinDLL('user32', use_last_error=True)
    user32.RegisterHotKey.argtypes = [W.HWND, W.INT, W.UINT, W.UINT]
    user32.RegisterHotKey.restype = W.BOOL
    user32.UnregisterHotKey.argtypes = [W.HWND, W.INT]
    user32.UnregisterHotKey.restype = W.BOOL
    return user32


class _NativeFilter(QAbstractNativeEventFilter):
    def __init__(self, owner):
        super().__init__()
        self._owner = weakref.ref(owner)

    def nativeEventFilter(self, event_type, message):
        owner = self._owner()
        if owner is None or owner._closed or not owner._registered:
            return False, 0
        if bytes(event_type) not in (b'windows_dispatcher_MSG', b'windows_generic_MSG'):
            return False, 0
        try:
            address = int(message)
            if not address:
                return False, 0
            msg = _MSG.from_address(address)
            if (msg.hwnd or msg.message != WM_HOTKEY or msg.wParam != owner._hotkey_id
                    or msg.lParam != owner._message_parameter):
                return False, 0
        except (TypeError, ValueError, OverflowError):
            return False, 0
        try:
            owner._callback()
        except Exception:
            # Do not let an exception escape a Qt native virtual callback.
            owner._error = '紧急暂停未能完成，请使用界面上的暂停按钮'
        return True, 0


class EmergencyHotkey(QObject):
    """Own one native hotkey, registered and dispatched on the Qt main thread."""
    def __init__(self, callback, parent=None):
        super().__init__(parent)
        if not callable(callback):
            raise TypeError('EmergencyHotkey callback must be callable')
        self._callback = callback
        self._settings = normalize_hotkey_settings(None)
        self._configured = None
        self._registered = False
        self._hotkey_id = None
        self._message_parameter = None
        self._user32 = None
        self._app = None
        self._quit_connected = False
        self._filter = _NativeFilter(self)
        self._filter_installed = False
        self._closed = False
        self._error = ''
        if parent is not None:
            parent.destroyed.connect(self.close)

    def status(self):
        return dict(self._settings, registered=self._registered, error=self._error)

    def _remove_filter(self):
        if self._filter_installed:
            self._app.removeNativeEventFilter(self._filter)
            self._filter_installed = False

    def _unregister(self):
        self._registered = False
        self._message_parameter = None
        self._remove_filter()
        if self._hotkey_id is None:
            return True
        try:
            success = self._user32.UnregisterHotKey(None, self._hotkey_id)
        except (OSError, AttributeError, TypeError):
            success = False
        if success:
            self._hotkey_id = None
            return True
        self._error = '旧快捷键未能释放，请重启软件后再试'
        return False

    def configure(self, settings):
        settings = normalize_hotkey_settings(settings)
        if self._closed:
            self._settings = dict(settings, enabled=False)
            self._error = '紧急暂停快捷键已关闭'
            return self.status()
        app = QCoreApplication.instance()
        if app is not None and QThread.currentThread() != app.thread():
            self._error = '请在软件主线程设置紧急暂停快捷键'
            return self.status()
        if settings == self._configured:
            return self.status()
        self._settings = settings
        self._configured = dict(settings)
        self._error = ''
        if not self._unregister():
            return self.status()
        if not settings['enabled']:
            return self.status()
        if sys.platform != 'win32':
            self._error = '当前系统不支持全局紧急暂停快捷键'
            return self.status()
        if app is None:
            self._error = '软件主线程尚未就绪，无法注册快捷键'
            return self.status()
        if self._app is None:
            self._app = app
            app.aboutToQuit.connect(self.close)
            self._quit_connected = True
        try:
            if self._user32 is None:
                self._user32 = _load_user32()
            _, modifiers, vk = parse_shortcut(settings['shortcut'])
            hotkey_id = next(_IDS)
            if hotkey_id > 0xBFFF:
                self._error = '快捷键注册次数过多，请重启软件后再试'
                return self.status()
            C.set_last_error(0)
            success = self._user32.RegisterHotKey(None, hotkey_id, modifiers | MOD_NOREPEAT, vk)
            if not success:
                code = C.get_last_error()
                self._error = ('该快捷键已被其他软件占用，请更换快捷键'
                               if code == 1409 else '快捷键注册失败，请更换快捷键后再试')
                return self.status()
            self._hotkey_id = hotkey_id
            self._message_parameter = modifiers | (vk << 16)
            self._registered = True
            app.installNativeEventFilter(self._filter)
            self._filter_installed = True
        except (OSError, AttributeError, TypeError, RuntimeError):
            self._error = '快捷键注册失败，请更换快捷键后再试'
            self._unregister()
        return self.status()

    def close(self, *_):
        if self._closed and self._hotkey_id is None:
            return
        if self._app is not None and QThread.currentThread() != self._app.thread():
            raise RuntimeError('EmergencyHotkey must close on the Qt main thread')
        self._closed = True
        self._settings['enabled'] = False
        if self._unregister():
            if self._quit_connected:
                try:
                    self._app.aboutToQuit.disconnect(self.close)
                except (TypeError, RuntimeError):
                    pass
                self._quit_connected = False
            self._app = None
            self._callback = None

    def __del__(self):
        try:
            self.close()
        except (AttributeError, RuntimeError):
            pass

import ctypes as C
from ctypes import wintypes as W
import shlex
import subprocess
import sys
import time


def attach_to_default_desktop():
    if sys.platform == 'win32':
        try:
            user32 = C.WinDLL('user32', use_last_error=True)
            hdesk = user32.OpenDesktopW('Default', 0, False, 0x01FF)
            if hdesk:
                user32.SetThreadDesktop(hdesk)
        except Exception:
            pass


KEYS = {'Ctrl': 0x11, 'Control': 0x11, 'Alt': 0x12, 'Shift': 0x10,
        'Meta': 0x5b, 'Win': 0x5b, 'Cmd': 0x5b, 'Command': 0x5b,
        'Enter': 13, 'Return': 13, 'Esc': 27,
        'Escape': 27, 'Space': 32, 'Tab': 9, 'Backspace': 8, 'Delete': 46,
        'Up': 38, 'Down': 40, 'Left': 37, 'Right': 39, 'Home': 36, 'End': 35,
        'PgUp': 33, 'PgDown': 34, 'PgDn': 34, 'RShift': 16, 'RCtrl': 17, 'RAlt': 18, 'Menu': 93, 'Insert': 45, 'Print': 44, 'Pause': 19,
        'PrtSc': 44, 'PrtScn': 44, 'PrintScreen': 44, 'Snapshot': 44,
        'ScrLk': 145, 'ScrollLock': 145, 'Caps': 20, 'CapsLock': 20, 'NumLock': 144}
KEYS.update({f'F{i}': 111+i for i in range(1, 25)})
KEYS.update({chr(i): i for i in range(48, 91)})

# Numpad (0-9, Operators, Enter)
for i in range(10):
    KEYS[f'Num{i}'] = 0x60 + i
    KEYS[f'Numpad{i}'] = 0x60 + i
KEYS.update({
    'Num/': 0x6F, 'NumDivide': 0x6F, 'Divide': 0x6F,
    'Num*': 0x6A, 'NumMultiply': 0x6A, 'Multiply': 0x6A,
    'Num-': 0x6D, 'NumSubtract': 0x6D, 'Subtract': 0x6D,
    'Num+': 0x6B, 'NumAdd': 0x6B, 'Add': 0x6B,
    'Num.': 0x6E, 'NumDecimal': 0x6E, 'Decimal': 0x6E,
    'NumEnter': 13,
    # 108-Key Multimedia & Utility
    'Mute': 0xAD, 'VolumeMute': 0xAD,
    'Vol-': 0xAE, 'VolumeDown': 0xAE, 'VolDown': 0xAE,
    'Vol+': 0xAF, 'VolumeUp': 0xAF, 'VolUp': 0xAF,
    'Calc': 0x87, 'Calculator': 0x87,
    # Punctuation & Symbols
    '`': 0xC0, '~': 0xC0,
    '-': 0xBD, '_': 0xBD,
    '=': 0xBB, '+': 0xBB,
    '[': 0xDB, '{': 0xDB,
    ']': 0xDD, '}': 0xDD,
    '\\': 0xDC, '|': 0xDC,
    ';': 0xBA, ':': 0xBA,
    "'": 0xDE, '"': 0xDE,
    ',': 0xBC, '<': 0xBC,
    '.': 0xBE, '>': 0xBE,
    '/': 0xBF, '?': 0xBF,
})


def parse_keys(text):
    text = text.strip()
    if not text:
        return []
    if text == '+':
        return [KEYS['+']]
    if text in KEYS:
        return [KEYS[text]]

    # 针对包含 '+' 的按键名称（如 Num+, Vol+, + 自身）进行安全分词
    if text.endswith('++'):
        parts = text[:-2].split('+') + ['+']
    elif '+Num+' in text:
        prefix = text.split('+Num+')[0]
        parts = prefix.split('+') + ['Num+']
    elif '+Vol+' in text:
        prefix = text.split('+Vol+')[0]
        parts = prefix.split('+') + ['Vol+']
    else:
        parts = text.split('+')

    keys = []
    for part in parts:
        part = part.strip()
        key = KEYS.get(part, KEYS.get(part.upper(), KEYS.get(part.title())))
        if key is None:
            raise ValueError(f'暂不支持按键 {part}，请使用字母、数字、功能键或导航键')
        keys.append(key)
    return keys


class KEYBDINPUT(C.Structure):
    _fields_ = [('wVk', W.WORD), ('wScan', W.WORD), ('dwFlags', W.DWORD), ('time', W.DWORD), ('dwExtraInfo', C.c_size_t)]


class MOUSEINPUT(C.Structure):
    _fields_ = [('dx', W.LONG), ('dy', W.LONG), ('mouseData', W.DWORD), ('dwFlags', W.DWORD), ('time', W.DWORD), ('dwExtraInfo', C.c_size_t)]


class POINT(C.Structure):
    _fields_ = [('x', W.LONG), ('y', W.LONG)]


class RECT(C.Structure):
    _fields_ = [('left', W.LONG), ('top', W.LONG), ('right', W.LONG), ('bottom', W.LONG)]


class UNION(C.Union):
    _fields_ = [('ki', KEYBDINPUT), ('mi', MOUSEINPUT)]


class INPUT(C.Structure):
    _fields_ = [('type', W.DWORD), ('u', UNION)]


class WindowsActions:
    def __init__(self):
        attach_to_default_desktop()
        self.held = {}
        self.held_mouse = set()
        self.held_gamepad_buttons = set()
        self.held_turbo = {}
        self.user32 = C.WinDLL('user32', use_last_error=True)
        self.user32.SendInput.argtypes = [W.UINT, C.POINTER(INPUT), C.c_int]
        self.user32.SendInput.restype = W.UINT

    def gamepad_button(self, button, down):
        if down:
            self.held_gamepad_buttons.add(button)
        else:
            self.held_gamepad_buttons.discard(button)

    def gamepad_chord(self, buttons, down):
        parts = buttons.split('+') if isinstance(buttons, str) else list(buttons)
        for b in parts:
            self.gamepad_button(b.strip(), down)

    def gamepad_turbo(self, button, down, rate_hz=15):
        if down:
            self.held_turbo[button] = rate_hz
        else:
            self.held_turbo.pop(button, None)

    def _send(self, keys, down):
        if not keys:
            return
        extended = {33, 34, 35, 36, 37, 38, 39, 40, 45, 46, 91, 0x6F, 0xAD, 0xAE, 0xAF, 0x87}
        events = []
        for k in keys:
            scan = self.user32.MapVirtualKeyW(k, 0)
            flag = (0x0008 if scan else 0) | (0 if down else 0x0002) | (0x0001 if k in extended else 0)
            events.append(INPUT(1, UNION(ki=KEYBDINPUT(k, scan, flag, 0, 0))))
        event_array = (INPUT * len(events))(*events)
        sent = self.user32.SendInput(len(event_array), event_array, C.sizeof(INPUT))
        if sent != len(event_array):
            err = C.get_last_error()
            if sent == 0 and err == 5:
                attach_to_default_desktop()
                sent = self.user32.SendInput(len(event_array), event_array, C.sizeof(INPUT))
            if sent != len(event_array):
                raise OSError('Windows 拒绝了按键输入；目标应用可能以管理员身份运行')

    def shortcut(self, value):
        try:
            self.hold(value, True)
            time.sleep(0.065)
        finally:
            self.hold(value, False)

    def hold(self, value, down):
        self._hold_keys(parse_keys(value), down)

    def _hold_keys(self, keys, down):
        keys = list(dict.fromkeys(keys))
        if down:
            fresh = [key for key in keys if self.held.get(key, 0) == 0]
            # Record ownership BEFORE SendInput. A partial batch can already
            # have pressed a modifier even though the API reports failure.
            for key in keys:
                self.held[key] = self.held.get(key, 0) + 1
            try:
                self._send(fresh, True)
            except Exception:
                try:
                    self.release_all()
                except Exception:
                    pass  # Failed releases remain tracked for the next retry.
                raise
        else:
            releases = [key for key in reversed(keys) if self.held.get(key) == 1]
            self._send(releases, False)
            for key in keys:
                if key in self.held:
                    self.held[key] -= 1
                    if not self.held[key]:
                        del self.held[key]

    def media(self, action):
        code = {'volume_mute': 0xad, 'volume_up': 0xaf, 'volume_down': 0xae, 'media': 0xb3}[action]
        try:
            self._hold_keys([code], True)
        finally:
            self._hold_keys([code], False)

    def move_mouse(self, dx, dy):
        # 0x0001 (MOUSEEVENTF_MOVE) 相对坐标位移注入
        idx, idy = int(dx), int(dy)
        if idx == 0 and idy == 0:
            return
        # 优先使用 SendInput (符合现代 Windows 规范与 DirectInput/RawInput 游戏视角采集)
        sent = False
        try:
            event = INPUT(0, UNION(mi=MOUSEINPUT(idx, idy, 0, 0x0001, 0, 0)))
            sent = (self.user32.SendInput(1, C.byref(event), C.sizeof(INPUT)) == 1)
        except Exception:
            sent = False
        # 若 SendInput 失败（如权限受阻或特定老游戏引擎），底层硬件级回退至 mouse_event
        if not sent:
            try:
                self.user32.mouse_event(0x0001, idx, idy, 0, 0)
            except Exception:
                pass

    def get_foreground_process_name(self) -> str:
        """获取当前 Windows 交互前台活动窗口的进程映像名称（带 500ms 缓存，杜绝高频 OpenProcess 冲击内核与反作弊）"""
        now = time.monotonic()
        if hasattr(self, '_cached_pname_time') and (now - self._cached_pname_time < 0.5):
            return getattr(self, '_cached_pname', '')

        pname = ''
        try:
            hwnd = self.user32.GetForegroundWindow()
            if hwnd:
                pid = W.DWORD()
                self.user32.GetWindowThreadProcessId(hwnd, C.byref(pid))
                if pid.value:
                    hproc = C.windll.kernel32.OpenProcess(0x1000, False, pid.value)
                    if hproc:
                        try:
                            buf = C.create_unicode_buffer(260)
                            size = W.DWORD(260)
                            if C.windll.kernel32.QueryFullProcessImageNameW(hproc, 0, buf, C.byref(size)):
                                from pathlib import Path
                                pname = Path(buf.value).name.lower()
                        finally:
                            C.windll.kernel32.CloseHandle(hproc)
        except Exception:
            pass

        self._cached_pname = pname
        self._cached_pname_time = now
        return pname

    def is_nikki_game_focused(self) -> bool:
        """
        判断当前前台活动窗口是否为 3D 动作游戏本体 (InfinityNikki / X6Game / Genshin 等)
        - 采用双重感知：先检查窗口类名与标题 (即便被反作弊阻止 OpenProcess 也能 100% 准确感知)，再校验进程名
        - 自动精准排除启动器 (xstarter.exe / Qt 启动器)，保障启动器与桌面原生点击自由度
        """
        try:
            hwnd = self.user32.GetForegroundWindow()
            if hwnd:
                cbuf = C.create_unicode_buffer(260)
                tbuf = C.create_unicode_buffer(260)
                self.user32.GetClassNameW(hwnd, cbuf, 260)
                self.user32.GetWindowTextW(hwnd, tbuf, 260)
                cname = cbuf.value or ''
                title = tbuf.value or ''

                # 如果是 Qt 启动器窗口，明确判定为非游戏本体
                if 'Qt' in cname or 'launcher' in title.lower() or '启动器' in title:
                    return False

                # 虚幻引擎原生窗口，且非编辑器
                if cname == 'UnrealWindow' and 'editor' not in title.lower():
                    return True

                # 标题明确匹配目标游戏本体
                if any(k in title.lower() for k in ('infinitynikki', 'x6game', '无限暖暖', 'nikki', 'infinity')):
                    return True
        except Exception:
            pass

        # 2. 回退校验进程映像名称
        pname = self.get_foreground_process_name()
        if not pname:
            return False
        # 排除各类启动器程序
        if any(kw in pname for kw in ('starter', 'launcher', 'install', 'update')):
            return False
        return any(kw in pname for kw in ('infinitynikki', 'x6game', 'nikki', 'genshin', 'game'))


    def guard_cursor_edge(self, margin=35, only_if_game=True):
        """
        防止虚拟鼠标光标在 3D 游戏视角旋转中触碰 Windows 屏幕边界被系统裁切停滞。
        - 仅在 3D 游戏处于前台时进行边缘守护回中；
        - 在桌面或启动器 (xstarter.exe) 中保留完整物理光标自由度，绝不打扰用户点击边缘按钮。
        """
        now = time.monotonic()
        if hasattr(self, '_last_guard_check') and (now - self._last_guard_check < 0.08):
            return
        self._last_guard_check = now

        if only_if_game and not self.is_nikki_game_focused():
            return
        pt = POINT()
        if not self.user32.GetCursorPos(C.byref(pt)):
            return
        vx = self.user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
        vy = self.user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
        vw = self.user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
        vh = self.user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
        
        if pt.x <= vx + margin or pt.x >= vx + vw - margin or pt.y <= vy + margin or pt.y >= vy + vh - margin:
            hwnd = self.user32.GetForegroundWindow()
            rect = RECT()
            if hwnd and self.user32.GetWindowRect(hwnd, C.byref(rect)) and (rect.right - rect.left > 200) and (rect.bottom - rect.top > 200):
                cx = (rect.left + rect.right) // 2
                cy = (rect.top + rect.bottom) // 2
            else:
                cx = self.user32.GetSystemMetrics(0) // 2
                cy = self.user32.GetSystemMetrics(1) // 2
            self.user32.SetCursorPos(cx, cy)

    def mouse_button(self, button='left', down=True):
        flags = {
            'left': (0x0002 if down else 0x0004),
            'right': (0x0008 if down else 0x0010),
            'middle': (0x0020 if down else 0x0040),
        }.get(button, 0x0002 if down else 0x0004)
        if down:
            self.held_mouse.add(button)
        event = INPUT(0, UNION(mi=MOUSEINPUT(0, 0, 0, flags, 0, 0)))
        if self.user32.SendInput(1, C.byref(event), C.sizeof(INPUT)) != 1:
            err = C.get_last_error()
            if err == 5:
                attach_to_default_desktop()
                if self.user32.SendInput(1, C.byref(event), C.sizeof(INPUT)) == 1:
                    if not down:self.held_mouse.discard(button)
                    return
            self.user32.mouse_event(flags, 0, 0, 0, 0)
        if not down:self.held_mouse.discard(button)

    def release_all(self):
        errors = []
        # One refused key-up must not prevent other keys or mouse buttons from
        # being released. Keep failed keys so later cleanup can retry them.
        for key in list(reversed(self.held)):
            try:
                self._send([key], False)
                self.held.pop(key, None)
            except Exception as exc:
                errors.append(exc)
        for b in list(self.held_mouse):
            try:
                self.mouse_button(b, False)
            except Exception as exc:
                errors.append(exc)
        if hasattr(self, 'held_gamepad_buttons'):
            self.held_gamepad_buttons.clear()
        if hasattr(self, 'held_turbo'):
            self.held_turbo.clear()
        if errors:
            raise OSError('部分键鼠按键未能释放；映射保持暂停，可再次尝试释放') from errors[0]

    def scroll(self, steps):
        event = INPUT(0, UNION(mi=MOUSEINPUT(0, 0, (int(steps) * 120) & 0xffffffff, 0x0800, 0, 0)))
        if self.user32.SendInput(1, C.byref(event), C.sizeof(INPUT)) != 1:
            raise OSError('Windows 拒绝了滚轮输入')


def create_actions():
    """Select the native output backend without importing other platforms."""
    if sys.platform == 'win32':
        return WindowsActions()
    if sys.platform == 'darwin':
        from .macos_actions import MacActions
        return MacActions()
    raise OSError(f'暂不支持此操作系统的键鼠映射：{sys.platform}')


def input_permission_status():
    if sys.platform == 'darwin':
        from .macos_actions import input_permission_status as status
        return status()
    return {'supported': sys.platform == 'win32',
            'granted': sys.platform == 'win32', 'reason': ''}


def request_input_permission():
    if sys.platform == 'darwin':
        from .macos_actions import request_input_permission as request
        return request()
    return input_permission_status()


def supports_key(value):
    try:
        keys = parse_keys(value)
    except ValueError:
        return False
    if sys.platform == 'darwin':
        from .macos_actions import SUPPORTED_KEYS
        return bool(keys) and all(key in SUPPORTED_KEYS for key in keys)
    return sys.platform == 'win32' and bool(keys)


def launch_command(executable, arguments=''):
    if not executable.strip():
        raise ValueError('请先选择可执行文件')
    if sys.platform == 'win32':
        args = [part.strip('"') for part in shlex.split(arguments, posix=False)]
    else:
        args = shlex.split(arguments, posix=True)
    if sys.platform == 'darwin':
        from pathlib import Path
        application = Path(executable)
        if application.suffix.lower() == '.app' and application.is_dir():
            return subprocess.Popen(['/usr/bin/open', '-a', str(application), '--args', *args], shell=False)
    return subprocess.Popen([executable, *args], shell=False)

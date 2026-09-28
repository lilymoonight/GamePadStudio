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
        'Meta': 0x5b, 'Win': 0x5b, 'Enter': 13, 'Return': 13, 'Esc': 27,
        'Escape': 27, 'Space': 32, 'Tab': 9, 'Backspace': 8, 'Delete': 46,
        'Up': 38, 'Down': 40, 'Left': 37, 'Right': 39, 'Home': 36, 'End': 35,
        'PgUp': 33, 'PgDown': 34, 'Insert': 45, 'Print': 44, 'Pause': 19}
KEYS.update({f'F{i}': 111+i for i in range(1, 25)})
KEYS.update({chr(i): i for i in range(48, 91)})


def parse_keys(text):
    keys = []
    for part in text.split('+'):
        part = part.strip()
        key = KEYS.get(part, KEYS.get(part.upper()))
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
        self.user32 = C.WinDLL('user32', use_last_error=True)
        self.user32.SendInput.argtypes = [W.UINT, C.POINTER(INPUT), C.c_int]
        self.user32.SendInput.restype = W.UINT

    def _send(self, keys, down):
        if not keys:
            return
        extended = {33, 34, 35, 36, 37, 38, 39, 40, 45, 46, 91}
        events = []
        for k in keys:
            scan = self.user32.MapVirtualKeyW(k, 0)
            flag = 0x0008 | (0 if down else 0x0002) | (0x0001 if k in extended else 0)
            events.append(INPUT(1, UNION(ki=KEYBDINPUT(k, scan, flag, 0, 0))))
        event_array = (INPUT * len(events))(*events)
        if self.user32.SendInput(len(event_array), event_array, C.sizeof(INPUT)) != len(event_array):
            err = C.get_last_error()
            if err == 5:
                attach_to_default_desktop()
                self.user32.SendInput(len(event_array), event_array, C.sizeof(INPUT))
            else:
                raise OSError('Windows 拒绝了按键输入；目标应用可能以管理员身份运行')

    def shortcut(self, value):
        keys = parse_keys(value)
        try:
            self._send(keys, True)
            time.sleep(0.065)
        finally:
            self._send(list(reversed(keys)), False)

    def hold(self, value, down):
        keys = parse_keys(value)
        if down:
            fresh = [key for key in keys if self.held.get(key, 0) == 0]
            self._send(fresh, True)
            for key in keys:
                self.held[key] = self.held.get(key, 0) + 1
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
            self._send([code], True)
        finally:
            self._send([code], False)

    def move_mouse(self, dx, dy):
        # 0x0001 (MOUSEEVENTF_MOVE) | 0x2000 (MOUSEEVENTF_MOVE_NOCOALESCE)
        # 禁止 Windows 消息队列合并/丢弃高频相对位移事件，保障 300Hz 物理视角帧帧即时投递
        event = INPUT(0, UNION(mi=MOUSEINPUT(int(dx), int(dy), 0, 0x0001 | 0x2000, 0, 0)))
        self.user32.SendInput(1, C.byref(event), C.sizeof(INPUT))

    def get_foreground_process_name(self) -> str:
        """获取当前 Windows 交互前台活动窗口的进程映像名称（小写，如 xstarter.exe / infinitynikki.exe）"""
        try:
            hwnd = self.user32.GetForegroundWindow()
            if not hwnd:
                return ''
            pid = W.DWORD()
            self.user32.GetWindowThreadProcessId(hwnd, C.byref(pid))
            if not pid.value:
                return ''
            # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            hproc = C.windll.kernel32.OpenProcess(0x1000, False, pid.value)
            if not hproc:
                return ''
            try:
                buf = C.create_unicode_buffer(260)
                size = W.DWORD(260)
                if C.windll.kernel32.QueryFullProcessImageNameW(hproc, 0, buf, C.byref(size)):
                    from pathlib import Path
                    return Path(buf.value).name.lower()
            finally:
                C.windll.kernel32.CloseHandle(hproc)
        except Exception:
            pass
        return ''

    def is_nikki_game_focused(self) -> bool:
        """
        判断当前前台活动窗口是否为 3D 动作游戏本体
        自动精准排除启动器 (xstarter.exe / launcher.exe)，保障启动器与桌面原生点击自由度
        """
        pname = self.get_foreground_process_name()
        if not pname:
            return False
        # 排除各类启动器程序
        if any(kw in pname for kw in ('starter', 'launcher', 'install', 'update')):
            return False
        return any(kw in pname for kw in ('infinitynikki', 'x6game', 'nikki'))

    def guard_cursor_edge(self, margin=35, only_if_game=True):
        """
        防止虚拟鼠标光标在 3D 游戏视角旋转中触碰 Windows 屏幕边界被系统裁切停滞。
        - 仅在 3D 游戏处于前台时进行边缘守护回中；
        - 在桌面或启动器 (xstarter.exe) 中保留完整物理光标自由度，绝不打扰用户点击边缘按钮。
        """
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
        else:
            self.held_mouse.discard(button)
        event = INPUT(0, UNION(mi=MOUSEINPUT(0, 0, 0, flags, 0, 0)))
        self.user32.SendInput(1, C.byref(event), C.sizeof(INPUT))

    def release_all(self):
        self._send(list(reversed(self.held)), False)
        self.held.clear()
        for b in list(self.held_mouse):
            self.mouse_button(b, False)
        self.held_mouse.clear()


def launch_command(executable, arguments=''):
    if not executable.strip():
        raise ValueError('请先选择可执行文件')
    args = [part.strip('"') for part in shlex.split(arguments, posix=False)]
    return subprocess.Popen([executable, *args], shell=False)

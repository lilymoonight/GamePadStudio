"""Public macOS window metadata, with injectable native calls.

Window and display bounds use Core Graphics global points (top-left origin).
Physical pixel dimensions belong to the capture result, never this geometry.
No AX access, application activation, consent prompt or image capture is used.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes as C
from dataclasses import dataclass
import math
import os
from pathlib import Path
import plistlib
import sys
import threading


STUDIO_BUNDLE_ID = 'io.github.lilymoonight.GamePadStudio'
_SYSTEM_BUNDLES = {
    'com.apple.finder', 'com.apple.dock', 'com.apple.WindowManager',
    'com.apple.controlcenter', 'com.apple.systemuiserver', 'com.apple.Spotlight',
    'com.apple.loginwindow', 'com.apple.TextInputMenuAgent', 'com.apple.Terminal',
}
_SYSTEM_NAMES = {'finder', 'dock', 'window server', 'windowmanager', 'controlcenter',
                 'systemuiserver', 'spotlight', 'loginwindow', 'terminal'}
_excluded_pids = frozenset()
_exclusions_lock = threading.Lock()


def configure_window_exclusions(pids):
    """Register this instance's UI/backend PIDs, including source checkouts."""
    global _excluded_pids
    with _exclusions_lock:
        _excluded_pids = frozenset(pid for pid in pids if isinstance(pid, int) and pid > 0)


def _bounds(value):
    if not isinstance(value, dict):
        return None
    try:
        result = {name: float(value[key]) for name, key in
                  (('left', 'X'), ('top', 'Y'), ('width', 'Width'), ('height', 'Height'))}
        if not all(math.isfinite(item) for item in result.values()):
            return None
        return result if result['width'] > 0 and result['height'] > 0 else None
    except (KeyError, TypeError, ValueError):
        return None


def monitor_for_bounds(bounds, monitors):
    """Use largest overlap, then nearest display, retaining capture metadata."""
    def score(monitor):
        left, top = float(monitor['left']), float(monitor['top'])
        right, bottom = left + float(monitor['width']), top + float(monitor['height'])
        x, y = bounds['left'], bounds['top']
        far_x, far_y = x + bounds['width'], y + bounds['height']
        overlap = max(0.0, min(right, far_x) - max(left, x)) * max(0.0, min(bottom, far_y) - max(top, y))
        dx = max(left - far_x, x - right, 0.0)
        dy = max(top - far_y, y - bottom, 0.0)
        return overlap, -(dx * dx + dy * dy)

    candidates = [monitor for monitor in monitors
                  if monitor.get('width', 0) > 0 and monitor.get('height', 0) > 0]
    return dict(max(candidates, key=score)) if candidates else None


@dataclass(frozen=True)
class WindowInfo:
    window_id: int
    pid: int
    title: str
    owner: str
    process_path: str
    process_name: str
    bundle_id: str
    generation: object
    bounds_points: dict

    @property
    def identity(self):
        return self.window_id, self.pid, self.generation, self.process_path


class CGPoint(C.Structure):
    _fields_ = [('x', C.c_double), ('y', C.c_double)]


class CGSize(C.Structure):
    _fields_ = [('width', C.c_double), ('height', C.c_double)]


class CGRect(C.Structure):
    _fields_ = [('origin', CGPoint), ('size', CGSize)]


class ProcBSDInfo(C.Structure):
    # SDK sys/proc_info.h: MAXCOMLEN is 16, pid_t/uid_t/gid_t are 32 bits.
    _fields_ = [(name, C.c_uint32) for name in (
        'pbi_flags', 'pbi_status', 'pbi_xstatus', 'pbi_pid', 'pbi_ppid', 'pbi_uid',
        'pbi_gid', 'pbi_ruid', 'pbi_rgid', 'pbi_svuid', 'pbi_svgid', 'rfu_1')]
    _fields_ += [('pbi_comm', C.c_char * 16), ('pbi_name', C.c_char * 32)]
    _fields_ += [(name, C.c_uint32) for name in ('pbi_nfiles', 'pbi_pgid', 'pbi_pjobc', 'e_tdev', 'e_tpgid')]
    _fields_ += [('pbi_nice', C.c_int32), ('pbi_start_tvsec', C.c_uint64), ('pbi_start_tvusec', C.c_uint64)]


class MacWindowNative:
    """Core Graphics lists plus process metadata; pointer-safe Apple Silicon ABI."""
    def __init__(self):
        if sys.platform != 'darwin':
            raise RuntimeError('macOS window detection is unavailable on this platform')
        self.cg = C.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.appkit = C.CDLL('/System/Library/Frameworks/AppKit.framework/AppKit')
        self.objc = C.CDLL('/usr/lib/libobjc.A.dylib')
        self.libproc = C.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        signatures = (
            (self.cg, 'CGWindowListCopyWindowInfo', [C.c_uint32, C.c_uint32], C.c_void_p),
            (self.cg, 'CGGetActiveDisplayList', [C.c_uint32, C.POINTER(C.c_uint32), C.POINTER(C.c_uint32)], C.c_int32),
            (self.cg, 'CGMainDisplayID', [], C.c_uint32),
            (self.cg, 'CGDisplayBounds', [C.c_uint32], CGRect),
            (self.cf, 'CFPropertyListCreateData', [C.c_void_p, C.c_void_p, C.c_long, C.c_ulong, C.POINTER(C.c_void_p)], C.c_void_p),
            (self.cf, 'CFDataGetLength', [C.c_void_p], C.c_long),
            (self.cf, 'CFDataGetBytePtr', [C.c_void_p], C.c_void_p),
            (self.cf, 'CFRelease', [C.c_void_p], None),
            (self.libproc, 'proc_pidpath', [C.c_int32, C.c_void_p, C.c_uint32], C.c_int32),
            (self.libproc, 'proc_pidinfo', [C.c_int32, C.c_int32, C.c_uint64, C.c_void_p, C.c_int32], C.c_int32),
        )
        for library, name, args, result in signatures:
            function = getattr(library, name)
            function.argtypes, function.restype = args, result
        self.objc.objc_getClass.argtypes, self.objc.objc_getClass.restype = [C.c_char_p], C.c_void_p
        self.objc.sel_registerName.argtypes, self.objc.sel_registerName.restype = [C.c_char_p], C.c_void_p
        address = C.cast(self.objc.objc_msgSend, C.c_void_p).value
        self._object = C.CFUNCTYPE(C.c_void_p, C.c_void_p, C.c_void_p)(address)
        self._object_pid = C.CFUNCTYPE(C.c_void_p, C.c_void_p, C.c_void_p, C.c_int32)(address)
        self._integer = C.CFUNCTYPE(C.c_int32, C.c_void_p, C.c_void_p)(address)
        self._text = C.CFUNCTYPE(C.c_char_p, C.c_void_p, C.c_void_p)(address)
        self._void = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p)(address)

    def _selector(self, name):
        return self.objc.sel_registerName(name.encode('ascii'))

    def _message(self, obj, name):
        return self._object(obj, self._selector(name)) if obj else None

    def _string(self, obj):
        raw = self._text(obj, self._selector('UTF8String')) if obj else None
        return raw.decode('utf-8') if raw else ''

    @contextmanager
    def _pool(self):
        pool = self._message(self.objc.objc_getClass(b'NSAutoreleasePool'), 'alloc')
        pool = self._message(pool, 'init')
        try:
            yield
        finally:
            if pool:
                self._void(pool, self._selector('drain'))

    def process(self, pid):
        """Read executable and kernel start time, including non-bundle games."""
        result = dict(pid=pid, executable='', bundle_id='', name='', generation=None)
        bsd = ProcBSDInfo()
        if self.libproc.proc_pidinfo(pid, 3, 0, C.byref(bsd), C.sizeof(bsd)) == C.sizeof(bsd) and bsd.pbi_pid == pid:
            result['generation'] = (int(bsd.pbi_start_tvsec), int(bsd.pbi_start_tvusec))
        path = C.create_string_buffer(4096)
        if self.libproc.proc_pidpath(pid, path, len(path)) > 0:
            result['executable'] = os.fsdecode(path.value)
        with self._pool():
            app = self._object_pid(self.objc.objc_getClass(b'NSRunningApplication'),
                                   self._selector('runningApplicationWithProcessIdentifier:'), pid)
            if app:
                result['bundle_id'] = self._string(self._message(app, 'bundleIdentifier'))
                result['name'] = self._string(self._message(app, 'localizedName'))
        return result

    def frontmost(self):
        with self._pool():
            workspace = self._message(self.objc.objc_getClass(b'NSWorkspace'), 'sharedWorkspace')
            app = self._message(workspace, 'frontmostApplication')
            pid = self._integer(app, self._selector('processIdentifier')) if app else 0
        return self.process(pid) if pid > 0 else None

    def windows(self, window_id=None):
        # Query just the known ID during verification; discovery uses the
        # on-screen list excluding desktop elements, in front-to-back order.
        options, relative = (8, int(window_id)) if window_id is not None else (1 | 16, 0)
        value = self.cg.CGWindowListCopyWindowInfo(options, relative)
        if not value:
            raise RuntimeError('当前后台进程不在可用的 macOS 图形登录会话中')
        data, error = None, C.c_void_p()
        try:
            data = self.cf.CFPropertyListCreateData(None, value, 200, 0, C.byref(error))
            if not data:
                raise RuntimeError('无法解析 macOS 窗口列表')
            size = self.cf.CFDataGetLength(data)
            if not 0 <= size <= 64 * 1024 * 1024:
                raise RuntimeError('macOS 窗口列表尺寸无效')
            rows = plistlib.loads(C.string_at(self.cf.CFDataGetBytePtr(data), size))
            if not isinstance(rows, list):
                raise RuntimeError('macOS 窗口列表格式无效')
            return rows
        finally:
            if error.value:
                self.cf.CFRelease(error)
            if data:
                self.cf.CFRelease(data)
            self.cf.CFRelease(value)

    def displays(self):
        count = C.c_uint32()
        if self.cg.CGGetActiveDisplayList(0, None, C.byref(count)) != 0 or not 0 < count.value <= 128:
            raise RuntimeError('无法读取 macOS 显示器列表')
        ids = (C.c_uint32 * count.value)()
        if self.cg.CGGetActiveDisplayList(count.value, ids, C.byref(count)) != 0:
            raise RuntimeError('macOS 显示器连接状态已变化')
        main = self.cg.CGMainDisplayID()
        result = []
        for display in sorted(ids[:count.value], key=lambda identifier: identifier != main):
            rect = self.cg.CGDisplayBounds(display)
            result.append(dict(left=rect.origin.x, top=rect.origin.y,
                               width=rect.size.width, height=rect.size.height, display_id=int(display)))
        return result


class MacWindowBackend:
    def __init__(self, native=None, excluded_pids=None):
        self._native = native
        self.excluded_pids = frozenset(excluded_pids or ())

    @property
    def native(self):
        if self._native is None:
            self._native = MacWindowNative()
        return self._native

    def _window_rows(self, window_id=None):
        processes, result = {}, []
        rows = self.native.windows() if window_id is None else self.native.windows(window_id)
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                window_id, pid = int(row.get('kCGWindowNumber', 0)), int(row.get('kCGWindowOwnerPID', 0))
                bounds = _bounds(row.get('kCGWindowBounds'))
                if (not window_id or pid <= 0 or not bounds or row.get('kCGWindowLayer', 0) != 0
                        or row.get('kCGWindowIsOnscreen', True) is False or float(row.get('kCGWindowAlpha', 1)) <= 0):
                    continue
                if pid not in processes:
                    processes[pid] = self.native.process(pid)
                app = processes[pid]
                path = app.get('executable', '')
                owner = row.get('kCGWindowOwnerName') or app.get('name', '')
                title = row.get('kCGWindowName') or owner or (Path(path).name if path else '应用窗口')
                result.append(WindowInfo(window_id, pid, str(title), str(owner), path,
                                         Path(path).name if path else str(owner), app.get('bundle_id', ''),
                                         app.get('generation'), bounds))
            except (TypeError, ValueError, KeyError, OverflowError):
                continue
        return result

    def excluded(self, window):
        with _exclusions_lock:
            protected = _excluded_pids
        if window.pid in protected | self.excluded_pids | {os.getpid()}:
            return True
        if window.bundle_id == STUDIO_BUNDLE_ID or window.bundle_id in _SYSTEM_BUNDLES:
            return True
        if window.owner.casefold() in _SYSTEM_NAMES or window.process_name.casefold() in _SYSTEM_NAMES:
            return True
        return bool(getattr(sys, 'frozen', False) and window.process_path == sys.executable)

    def foreground_window(self):
        before = self.native.frontmost()
        if not before:
            return None
        candidates = self._window_rows()
        after = self.native.frontmost()
        key = lambda app: (app.get('pid'), app.get('generation')) if app else None
        if key(before) != key(after):
            return None
        return next((row for row in candidates if (row.pid, row.generation, row.process_path) == (
            before['pid'], before.get('generation'), before.get('executable', ''))), None)

    def smart_window(self):
        foreground = self.foreground_window()
        if foreground is not None and not self.excluded(foreground):
            return foreground
        candidates = [row for row in self._window_rows() if not self.excluded(row)
                      and row.bounds_points['width'] >= 640 and row.bounds_points['height'] >= 480]
        return max(candidates, key=lambda row: row.bounds_points['width'] * row.bounds_points['height'], default=None)

    def window(self, window_id):
        return next((row for row in self._window_rows(window_id) if row.window_id == window_id), None)

    def verify(self, expected):
        if not isinstance(expected, WindowInfo):
            raise RuntimeError('目标窗口已退出或进程身份变化，请重新截图')
        current = self.window(expected.window_id)
        if (current is None or expected.generation is None or not expected.process_path
                or current.identity != expected.identity):
            raise RuntimeError('目标窗口已退出或进程身份变化，请重新截图')
        return current

    def monitor(self, window, monitors=None):
        return monitor_for_bounds(window.bounds_points, self.native.displays() if monitors is None else monitors)

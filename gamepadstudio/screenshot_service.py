"""Capture physical pixels, including negative multi-monitor coordinates."""
import ctypes
from ctypes import wintypes
from datetime import datetime
import json
from pathlib import Path
import re
import struct
import sys
import uuid
import zlib
from functools import lru_cache
import mss
import mss.tools


def sanitize_filename(name: str) -> str:
    """清理窗口标题中不能作为 Windows 文件名的非法字符"""
    clean = re.sub(r'[\\/*?:"<>|\s]+', '_', (name or '').strip()).strip('_')
    return clean[:36] if clean else 'Desktop'


def embed_png_metadata(png_path: Path, metadata: dict):
    """将元数据以标准 tEXt chunk 原生嵌入 PNG 文件中（零伴生文件，开箱自包含）"""
    try:
        content = png_path.read_bytes()
        key = b'GamePadStudio'
        val = json.dumps(metadata, ensure_ascii=False).encode('utf-8')
        chunk_data = key + b'\x00' + val
        chunk_type = b'tEXt'
        crc = struct.pack('>I', zlib.crc32(chunk_type + chunk_data) & 0xffffffff)
        chunk = struct.pack('>I', len(chunk_data)) + chunk_type + chunk_data + crc
        iend_pos = content.rfind(b'IEND') - 4
        if iend_pos > 0:
            png_path.write_bytes(content[:iend_pos] + chunk + content[iend_pos:])
    except Exception:
        pass


def extract_png_metadata(png_path: Path) -> dict:
    """从 PNG 头部高效读取嵌入的 tEXt chunk（无需解码像素，毫秒级响应）"""
    try:
        with open(png_path, 'rb') as f:
            sig = f.read(8)
            if sig != b'\x89PNG\r\n\x1a\n':
                return {}
            while True:
                lb = f.read(4)
                if len(lb) < 4:
                    break
                length = struct.unpack('>I', lb)[0]
                chunk_type = f.read(4)
                if chunk_type == b'tEXt':
                    chunk_data = f.read(length)
                    f.read(4)  # crc
                    key, _, val = chunk_data.partition(b'\x00')
                    if key == b'GamePadStudio':
                        return json.loads(val.decode('utf-8', 'replace'))
                elif chunk_type == b'IEND':
                    break
                else:
                    f.seek(length + 4, 1)
    except Exception:
        pass
    return {}


def update_png_metadata(png_path: Path, metadata: dict):
    """更新 PNG 内嵌的元数据 chunk（支持动态收藏/修改）"""
    try:
        content = bytearray(png_path.read_bytes())
        pos = 8
        while pos < len(content):
            lb = content[pos:pos+4]
            if len(lb) < 4:
                break
            length = struct.unpack('>I', lb)[0]
            chunk_type = content[pos+4:pos+8]
            if chunk_type == b'tEXt':
                chunk_data = content[pos+8:pos+8+length]
                if chunk_data.startswith(b'GamePadStudio\x00'):
                    del content[pos:pos+12+length]
                    continue
            elif chunk_type == b'IEND':
                break
            pos += 12 + length

        key = b'GamePadStudio'
        val = json.dumps(metadata, ensure_ascii=False).encode('utf-8')
        chunk_data = key + b'\x00' + val
        chunk_type = b'tEXt'
        crc = struct.pack('>I', zlib.crc32(chunk_type + chunk_data) & 0xffffffff)
        chunk = struct.pack('>I', len(chunk_data)) + chunk_type + chunk_data + crc
        iend_pos = content.rfind(b'IEND') - 4
        if iend_pos > 0:
            new_content = content[:iend_pos] + chunk + content[iend_pos:]
            png_path.write_bytes(new_content)
    except Exception:
        pass


def _ensure_dpi_awareness():
    """启用 Windows Per-Monitor V2 DPI 识别，确保像素坐标与物理屏幕 1:1 绝对对齐"""
    if sys.platform == 'win32':
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


@lru_cache(maxsize=1)
def get_mac_window_backend():
    from .macos_windows import MacWindowBackend
    return MacWindowBackend()


def configure_window_exclusions(pids):
    """Exclude this instance's GUI/backend, including separate source processes."""
    from .macos_windows import configure_window_exclusions as configure
    configure(pids)


def create_mac_capture():
    from .macos_capture import MacCapture
    return MacCapture()


def get_window_process_name(hwnd) -> str:
    """安全获取指定窗口所属进程名"""
    if hwnd and sys.platform == 'darwin':
        window = get_mac_window_backend().window(hwnd)
        return window.process_name if window else ''
    if not hwnd or sys.platform != 'win32':
        return ''
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    hproc = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)
    if not hproc:
        return ''
    buf = ctypes.create_unicode_buffer(1024)
    size = wintypes.DWORD(1024)
    res = ''
    if ctypes.windll.kernel32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(size)):
        import os
        res = os.path.basename(buf.value)
    ctypes.windll.kernel32.CloseHandle(hproc)
    return res


def is_software_or_system_window(hwnd, title: str, pname: str) -> bool:
    """判断是否为 GamePad Studio 本身、桌面管理器或后台无感系统组件"""
    p = (pname or '').lower()
    t = (title or '').lower()
    if p in ('python.exe', 'pythonw.exe') or 'gamepad' in t or 'dualsense' in t:
        return True
    if p in ('explorer.exe', 'taskmgr.exe', 'textinputhost.exe', 'shellexperiencehost.exe', 'searchhost.exe', 'cmd.exe', 'conhost.exe'):
        return True
    if t in ('program manager', 'dummylayeredwnd', ''):
        return True
    return False


def smart_foreground_info():
    """
    智能前台识别：若前台窗口是游戏则直接返回；若当前前台是 GamePad Studio 或桌面，
    则自动穿透扫描 Default 桌面找到真正运行的大型游戏/全屏应用窗口
    """
    if sys.platform == 'darwin':
        window = get_mac_window_backend().smart_window()
        return (window.window_id, window.title) if window else (None, '桌面')
    _ensure_dpi_awareness()
    fg_hwnd, fg_title = foreground_info()
    fg_pname = get_window_process_name(fg_hwnd)

    if fg_hwnd and not is_software_or_system_window(fg_hwnd, fg_title, fg_pname):
        return fg_hwnd, fg_title
    if fg_title and not fg_hwnd and not is_software_or_system_window(None, fg_title, ""):
        return None, fg_title

    if sys.platform != 'win32':
        return None, fg_title or '桌面'

    u = ctypes.windll.user32
    # 扫描 Default 桌面活跃顶层窗口
    hdesk = u.OpenDesktopW('Default', 0, False, 0x0100)
    candidates = []
    if hdesk:
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd, lparam):
            if not u.IsWindowVisible(hwnd):
                return True
            rect = wintypes.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(rect))
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w < 640 or h < 480:
                return True
            t_buf = ctypes.create_unicode_buffer(512)
            u.GetWindowTextW(hwnd, t_buf, 512)
            title = t_buf.value
            pname = get_window_process_name(hwnd)
            if not is_software_or_system_window(hwnd, title, pname):
                candidates.append((hwnd, title, pname, w * h))
            return True
        u.EnumDesktopWindows(hdesk, WNDENUMPROC(cb), 0)
        u.CloseDesktop(hdesk)

    if candidates:
        # 按窗口物理面积降序，优先锁定全屏/大型游戏
        candidates.sort(key=lambda x: x[3], reverse=True)
        return candidates[0][0], candidates[0][1]

    return fg_hwnd, fg_title or '桌面'


def foreground_info():
    if sys.platform == 'darwin':
        window = get_mac_window_backend().foreground_window()
        return (window.window_id, window.title) if window else (None, '桌面')
    _ensure_dpi_awareness()
    if sys.platform != 'win32':
        return None, '桌面'
    u = ctypes.windll.user32
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    hwnd = u.GetForegroundWindow()
    title = ctypes.create_unicode_buffer(512)
    u.GetWindowTextW(hwnd, title, 512)
    return hwnd, title.value or '桌面'


def _get_active_monitor_bbox(monitors=None):
    if sys.platform == 'darwin':
        backend = get_mac_window_backend()
        window = backend.smart_window() or backend.foreground_window()
        return backend.monitor(window, monitors) if window else None
    _ensure_dpi_awareness()
    if sys.platform != 'win32':
        return None
    u = ctypes.windll.user32
    hwnd, _ = smart_foreground_info()
    if not hwnd:
        hwnd = u.GetForegroundWindow()
    u.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    u.MonitorFromWindow.restype = wintypes.HANDLE

    class MONITORINFO(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT), ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]

    u.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    if not u.GetMonitorInfoW(u.MonitorFromWindow(hwnd, 2), ctypes.byref(info)):
        return None
    r = info.rcMonitor
    return dict(left=r.left, top=r.top, width=r.right-r.left, height=r.bottom-r.top)


def get_target_monitor_bbox(sct, mode: str = 'game') -> dict:
    """
    智能屏幕与目标范围解析：
    - 'all': 全屏跨屏全景 (例如双 4K 拼接 7680x2160)
    - 'monitor_1': 物理显示器 1
    - 'monitor_2': 物理显示器 2
    - 'game' / 'smart' / 'monitor': 智能侦测游戏所在屏幕，彻底规避录制 GamePad Studio 自身窗口
    """
    _ensure_dpi_awareness()
    if mode == 'all':
        return sct.monitors[0]
    if sys.platform == 'darwin':
        selected = re.fullmatch(r'monitor_(\d+)', mode)
        if selected:
            index = int(selected.group(1))
            if index < 1 or index >= len(sct.monitors):
                raise RuntimeError('所选显示器已断开，请重新选择截图屏幕')
            return sct.monitors[index]
        active = _get_active_monitor_bbox(sct.monitors[1:])
        return active or (sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0])
    elif mode == 'monitor_1':
        return sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
    elif mode == 'monitor_2':
        return sct.monitors[2] if len(sct.monitors) > 2 else (sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0])

    active = _get_active_monitor_bbox()
    if active:
        return active
    return sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]


def _take_macos_screenshot(save_dir, mode):
    backend = get_mac_window_backend()
    window = backend.smart_window()
    title = window.title if window else '桌面'
    now = datetime.now()
    folder = Path(save_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'DS_{sanitize_filename(title)}_{now:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:4]}.png'
    details = {}
    with create_mac_capture() as capture:
        if mode == 'window':
            if window is None:
                raise RuntimeError('未找到可截图的游戏或应用窗口，请切换到目标窗口')
            current = backend.verify(window)
            image = capture.capture_window(current.window_id)
            backend.verify(current)
            details = dict(window_id=current.window_id, pid=current.pid,
                           process_path=current.process_path, bounds_points=current.bounds_points)
        elif mode == 'all':
            image = capture.capture_all()
        else:
            selected = re.fullmatch(r'monitor_(\d+)', mode)
            if selected:
                index = int(selected.group(1))
                if index < 1 or index >= len(capture.monitors):
                    raise RuntimeError('所选显示器已断开，请重新选择截图屏幕')
                monitor = capture.monitors[index]
            else:
                monitor = backend.monitor(window, capture.monitors[1:]) if window else None
                monitor = monitor or (capture.monitors[1] if len(capture.monitors) > 1 else None)
            if not monitor or not monitor.get('display_id'):
                raise RuntimeError('未找到可截图的 macOS 显示器')
            image = capture.capture_display(monitor['display_id'])
            details = dict(display_id=monitor['display_id'], bounds_points={
                key: monitor[key] for key in ('left', 'top', 'width', 'height')})
        if image.width < 1 or image.height < 1:
            raise RuntimeError('macOS 捕获未返回有效图像')
        image.save(path, format='PNG')
    metadata = dict(title=title, created=now.isoformat(), width=image.width, height=image.height,
                    mode=mode, favorite=False, capture_method='ScreenCaptureKit', **details)
    embed_png_metadata(path, metadata)
    return str(path)


def take_screenshot(save_dir, toast_duration_ms=0, mode='game'):
    if sys.platform == 'darwin':
        from .macos_permissions import screen_capture_permission_status
        permission = screen_capture_permission_status()
        if not permission['granted']:
            raise PermissionError(permission['reason'])
        return _take_macos_screenshot(save_dir, mode)
    folder = Path(save_dir)
    folder.mkdir(parents=True, exist_ok=True)
    _ensure_dpi_awareness()
    hwnd, title = smart_foreground_info()
    clean_title = sanitize_filename(title)
    now = datetime.now()
    path = folder / f'DS_{clean_title}_{now:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:4]}.png'
    with mss.mss() as capture:
        if mode == 'window' and hwnd and sys.platform == 'win32':
            rect = wintypes.RECT()
            if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                desktop = capture.monitors[0]
                left = max(rect.left, desktop['left'])
                top = max(rect.top, desktop['top'])
                right = min(rect.right, desktop['left'] + desktop['width'])
                bottom = min(rect.bottom, desktop['top'] + desktop['height'])
                if right > left and bottom > top:
                    bbox = dict(left=left, top=top, width=right - left, height=bottom - top)
                else:
                    bbox = get_target_monitor_bbox(capture, mode)
            else:
                bbox = get_target_monitor_bbox(capture, mode)
        else:
            bbox = get_target_monitor_bbox(capture, mode)

        shot = capture.grab(bbox)
        mss.tools.to_png(shot.rgb, shot.size, output=str(path))
    metadata = dict(title=title, created=now.isoformat(), width=shot.width, height=shot.height,
                    mode=mode, favorite=False)
    # 原生嵌入 PNG tEXt 数据块，彻底消除伴生 .json 文件
    embed_png_metadata(path, metadata)
    return str(path)


def list_captures(folder):
    rows = []
    folder_path = Path(folder)
    if not folder_path.exists():
        return rows

    candidates = list(folder_path.glob('*.png')) + list(folder_path.glob('*.mp4'))
    for path in sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True):
        if path.suffix.lower() == '.mp4':
            sidecar = path.with_suffix('.json')
            data = {}
            if sidecar.exists():
                try:
                    data = json.loads(sidecar.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    data = {}
            thumb = path.with_suffix('.jpg')
            raw_title = data.get('title') or path.stem.replace("DS_", "").replace("_replay", "")
            size_mb = round(path.stat().st_size / (1024 * 1024), 1)
            rows.append({
                **data,
                'path': str(path),
                'thumb_path': str(thumb) if thumb.is_file() else str(path),
                'title': f"🎬 {raw_title}",
                'raw_title': raw_title,
                'created': datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                'favorite': bool(data.get('favorite')),
                'is_video': True,
                'size_mb': size_mb,
                'width': 3840,
                'height': 2160,
            })
        else:
            data = extract_png_metadata(path)
            if not data:
                sidecar = path.with_suffix('.json')
                try:
                    data = json.loads(sidecar.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    data = {}
            rows.append({
                **data,
                'path': str(path),
                'thumb_path': str(path),
                'title': data.get('title', path.stem),
                'favorite': bool(data.get('favorite')),
                'is_video': False
            })
    return rows


def set_favorite(path, favorite):
    p = Path(path)
    if p.suffix.lower() == '.mp4':
        sidecar = p.with_suffix('.json')
        data = {}
        if sidecar.exists():
            try:
                data = json.loads(sidecar.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                data = {}
        data['favorite'] = favorite
        sidecar.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        return

    meta = extract_png_metadata(p)
    if meta:
        meta['favorite'] = favorite
        update_png_metadata(p, meta)
    sidecar = p.with_suffix('.json')
    if sidecar.exists() or not meta:
        try:
            data = json.loads(sidecar.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
        data['favorite'] = favorite
        sidecar.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def delete_capture(path):
    p = Path(path)
    sidecar = p.with_suffix('.json')
    thumb = p.with_suffix('.jpg')
    deleted = False
    try:
        if p.exists():
            p.unlink()
            deleted = True
    except OSError:
        pass
    try:
        if sidecar.exists():
            sidecar.unlink()
    except OSError:
        pass
    try:
        if thumb.exists():
            thumb.unlink()
    except OSError:
        pass
    return deleted

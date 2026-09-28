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


def foreground_info():
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


def _get_active_monitor_bbox():
    if sys.platform != 'win32':
        return None
    u = ctypes.windll.user32
    hwnd, _ = foreground_info()
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


def take_screenshot(save_dir, toast_duration_ms=0, mode='monitor'):
    folder = Path(save_dir)
    folder.mkdir(parents=True, exist_ok=True)
    hwnd, title = foreground_info()
    clean_title = sanitize_filename(title)
    now = datetime.now()
    path = folder / f'DS_{clean_title}_{now:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:4]}.png'
    with mss.mss() as capture:
        bbox = _get_active_monitor_bbox() or capture.monitors[1]
        if mode == 'all':
            bbox = capture.monitors[0]
        elif mode == 'window' and hwnd:
            rect = wintypes.RECT()
            if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                desktop = capture.monitors[0]
                left, top = max(rect.left, desktop['left']), max(rect.top, desktop['top'])
                right = min(rect.right, desktop['left']+desktop['width'])
                bottom = min(rect.bottom, desktop['top']+desktop['height'])
                if right > left and bottom > top:
                    bbox = dict(left=left, top=top, width=right-left, height=bottom-top)
        shot = capture.grab(bbox)
        mss.tools.to_png(shot.rgb, shot.size, output=str(path))
    metadata = dict(title=title, created=now.isoformat(), width=shot.width, height=shot.height,
                    mode=mode, favorite=False)
    # 原生嵌入 PNG tEXt 数据块，彻底消除伴生 .json 文件
    embed_png_metadata(path, metadata)
    return str(path)


def list_captures(folder):
    rows = []
    for path in sorted(Path(folder).glob('*.png'), key=lambda p: p.stat().st_mtime, reverse=True):
        data = extract_png_metadata(path)
        if not data:
            sidecar = path.with_suffix('.json')
            try:
                data = json.loads(sidecar.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                data = {}
        rows.append({**data, 'path': str(path), 'title': data.get('title', path.stem), 'favorite': bool(data.get('favorite'))})
    return rows


def set_favorite(path, favorite):
    p = Path(path)
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
    return deleted


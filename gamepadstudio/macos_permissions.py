"""Read/request screen capture consent without prompting during startup."""
import ctypes
from functools import lru_cache
import sys


@lru_cache(maxsize=1)
def _graphics():
    library = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
    for name in ('CGPreflightScreenCaptureAccess', 'CGRequestScreenCaptureAccess'):
        function = getattr(library, name)
        function.argtypes = []
        function.restype = ctypes.c_bool
    return library


def screen_capture_permission_status():
    if sys.platform != 'darwin':
        return {'supported': False, 'granted': True, 'reason': ''}
    try:
        granted = bool(_graphics().CGPreflightScreenCaptureAccess())
        reason = '' if granted else '请在系统设置 → 隐私与安全性 → 屏幕录制中允许 GamePad Studio（源码运行时为 Python 或启动它的终端），然后重新启动软件'
    except (OSError, AttributeError) as exc:
        granted = False
        reason = '无法检查 macOS 屏幕录制权限：' + str(exc)
    return {'supported': True, 'granted': granted, 'reason': reason}


def request_screen_capture_permission():
    """Call only after the user explicitly presses the permission button."""
    if sys.platform == 'darwin':
        _graphics().CGRequestScreenCaptureAccess()
    return screen_capture_permission_status()

"""Windows driver health checks, one-click installer helpers, and conflict remediation."""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

VIGEMBUS_LATEST_API = 'https://api.github.com/repos/nefarius/ViGEmBus/releases/latest'
VIGEMBUS_FALLBACK_EXE_URL = (
    'https://github.com/nefarius/ViGEmBus/releases/download/v1.22.0/ViGEmBus_1.22.0_x64_x86_arm64.exe'
)
VIGEMBUS_RELEASE_PAGE = 'https://github.com/nefarius/ViGEmBus/releases/latest'

HIDHIDE_LATEST_API = 'https://api.github.com/repos/nefarius/HidHide/releases/latest'
HIDHIDE_FALLBACK_EXE_URL = (
    'https://github.com/nefarius/HidHide/releases/download/v1.5.230.0/HidHide_1.5.230_x64.exe'
)
HIDHIDE_RELEASE_PAGE = 'https://github.com/nefarius/HidHide/releases/latest'

_TH32CS_SNAPPROCESS = 0x00000002


class _PROCESSENTRY32W(C.Structure):
    _fields_ = [
        ('dwSize', W.DWORD),
        ('cntUsage', W.DWORD),
        ('th32ProcessID', W.DWORD),
        ('th32DefaultHeapID', C.c_size_t),
        ('th32ModuleID', W.DWORD),
        ('cntThreads', W.DWORD),
        ('th32ParentProcessID', W.DWORD),
        ('pcPriClassBase', C.c_long),
        ('dwFlags', W.DWORD),
        ('szExeFile', C.c_wchar * 260),
    ]


def list_running_exe_names() -> set[str]:
    """Return lower-cased executable basenames of currently running processes on Windows."""
    if sys.platform != 'win32':
        return set()
    names: set[str] = set()
    try:
        kernel32 = C.WinDLL('kernel32', use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = W.HANDLE
        kernel32.Process32FirstW.argtypes = [W.HANDLE, C.POINTER(_PROCESSENTRY32W)]
        kernel32.Process32FirstW.restype = W.BOOL
        kernel32.Process32NextW.argtypes = [W.HANDLE, C.POINTER(_PROCESSENTRY32W)]
        kernel32.Process32NextW.restype = W.BOOL
        kernel32.CloseHandle.argtypes = [W.HANDLE]
        kernel32.CloseHandle.restype = W.BOOL

        snap = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if not snap or snap in (-1, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
            return names
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = C.sizeof(_PROCESSENTRY32W)
            ok = kernel32.Process32FirstW(snap, C.byref(entry))
            while ok:
                exe_name = str(entry.szExeFile or '').strip().lower()
                if exe_name:
                    names.add(exe_name)
                ok = kernel32.Process32NextW(snap, C.byref(entry))
        finally:
            kernel32.CloseHandle(snap)
    except Exception:
        pass
    return names


def is_hidhide_gui_running() -> bool:
    """True when Nefarius HidHide Configuration Client GUI (HidHideClient.exe) is open.

    HidHideClient.exe holds an exclusive control handle on \\\\.\\HidHide while open,
    preventing all other applications (including HidHideCLI.exe) from updating rules.
    """
    return 'hidhideclient.exe' in list_running_exe_names()


def find_steam_whitelist_conflicts(whitelist: List[str]) -> List[str]:
    """Identify Steam executables inside HidHide's application whitelist.

    If steam.exe is whitelisted in HidHide while virtual Xbox 360 remapping is active,
    Steam Input sees BOTH the physical controller and the virtual controller, causing
    double button presses or unswapped inputs.
    """
    conflicts: List[str] = []
    steam_basenames = {'steam.exe', 'steamwebhelper.exe', 'gameoverlayui.exe'}
    for item in whitelist or ():
        base = os.path.basename(str(item).replace('/', '\\')).lower()
        if base in steam_basenames:
            conflicts.append(item)
    return conflicts


def remove_steam_from_hidhide_whitelist(client=None) -> Tuple[bool, str]:
    """Remove Steam executables from HidHide's whitelist while preserving other apps."""
    if sys.platform != 'win32':
        return False, '仅支持 Windows'
    from .hidhide import HidHideClient, resolve_hidhide_cli_path, _run_hidhide_cli
    hh = client if client is not None else HidHideClient()
    if not hh.is_driver_installed():
        return True, ''
    whitelist = hh.get_whitelist()
    conflicts = find_steam_whitelist_conflicts(whitelist)
    if not conflicts:
        return True, '未发现 Steam 白名单冲突'
    conflict_set = {c.casefold() for c in conflicts}
    cleaned = [item for item in whitelist if item.casefold() not in conflict_set]
    if hh.set_whitelist(cleaned):
        return True, '已从 HidHide 白名单移除 Steam，避免双重手柄输入'
    cli_path = resolve_hidhide_cli_path()
    if cli_path:
        all_ok = True
        for item in conflicts:
            if _run_hidhide_cli(cli_path, ['--app-unreg', item]) != 0:
                all_ok = False
        if all_ok:
            return True, '已从 HidHide 白名单移除 Steam，避免双重手柄输入'
    return False, '未能自动移除 Steam 白名单项，请先关闭 HidHide 官方配置窗口或以管理员身份运行工作台'


def resolve_latest_driver_installer_url(kind: str, timeout: float = 4.0) -> Tuple[str, str]:
    """Resolve (download_url, filename) for 'vigembus' or 'hidhide'."""
    if kind == 'vigembus':
        api_url = VIGEMBUS_LATEST_API
        fallback_url = VIGEMBUS_FALLBACK_EXE_URL
    elif kind == 'hidhide':
        api_url = HIDHIDE_LATEST_API
        fallback_url = HIDHIDE_FALLBACK_EXE_URL
    else:
        raise ValueError(f'Unknown driver kind: {kind}')

    try:
        req = urllib.request.Request(
            api_url,
            headers={'User-Agent': 'GamePadStudio-Updater', 'Accept': 'application/vnd.github+json'},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode('utf-8', 'replace'))
        for asset in payload.get('assets') or ():
            dl = str(asset.get('browser_download_url') or '').strip()
            name = str(asset.get('name') or '').strip()
            if dl.lower().endswith(('.exe', '.msi')) and name:
                return dl, name
    except Exception:
        pass
    return fallback_url, fallback_url.rsplit('/', 1)[-1]


def download_and_launch_driver_installer(
    kind: str,
    target_dir: Optional[Path] = None,
    opener: Optional[Callable[[str], None]] = None,
    timeout: float = 25.0,
) -> Dict[str, Any]:
    """Download official signed Nefarius driver installer (.exe) and launch it.

    Falls back to winget (for HidHide) or returns release_url if offline.
    """
    release_url = VIGEMBUS_RELEASE_PAGE if kind == 'vigembus' else HIDHIDE_RELEASE_PAGE
    if sys.platform != 'win32':
        return {'ok': False, 'launched': False, 'release_url': release_url, 'message': '仅支持 Windows'}

    dl_url, filename = resolve_latest_driver_installer_url(kind)
    dest_root = Path(target_dir) if target_dir is not None else (Path(tempfile.gettempdir()) / 'GamePadStudio-Drivers')
    dest_root.mkdir(parents=True, exist_ok=True)
    dest_path = dest_root / filename
    part_path = dest_root / (filename + '.part')

    downloaded = False
    if dest_path.is_file() and dest_path.stat().st_size >= 65536:
        try:
            with dest_path.open('rb') as f:
                if f.read(2) == b'MZ':
                    downloaded = True
        except OSError:
            downloaded = False

    if not downloaded:
        try:
            req = urllib.request.Request(dl_url, headers={'User-Agent': 'GamePadStudio-Updater'})
            with urllib.request.urlopen(req, timeout=timeout) as resp, part_path.open('wb') as out:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out.write(chunk)
            if part_path.stat().st_size >= 65536:
                with part_path.open('rb') as f:
                    header = f.read(2)
                if header == b'MZ' or filename.lower().endswith('.msi'):
                    part_path.replace(dest_path)
                    downloaded = True
            if not downloaded and part_path.exists():
                part_path.unlink(missing_ok=True)
        except Exception:
            try:
                part_path.unlink(missing_ok=True)
            except OSError:
                pass

    launch_fn = opener or os.startfile
    if downloaded and dest_path.is_file():
        try:
            launch_fn(str(dest_path))
            label = 'ViGEmBus 虚拟手柄驱动' if kind == 'vigembus' else 'HidHide 原始输入屏蔽驱动'
            return {
                'ok': True,
                'launched': True,
                'installer_path': str(dest_path),
                'release_url': release_url,
                'message': f'已下载并启动 {label} 官方安装向导，请按提示完成安装',
            }
        except OSError as exc:
            return {
                'ok': False,
                'launched': False,
                'installer_path': str(dest_path),
                'release_url': release_url,
                'message': f'安装程序启动被取消或失败：{exc}',
            }

    # Network fallback for HidHide via Microsoft winget CDN
    if kind == 'hidhide':
        winget_exe = shutil.which('winget') or shutil.which('winget.exe')
        if winget_exe:
            try:
                subprocess.Popen([
                    'cmd.exe', '/c', 'start', '""',
                    winget_exe, 'install', '--id', 'Nefarius.HidHide', '-e',
                    '--accept-package-agreements', '--accept-source-agreements',
                ])
                return {
                    'ok': True,
                    'launched': True,
                    'method': 'winget',
                    'release_url': release_url,
                    'message': '已调起 Windows 程序包管理器 (winget) 安装 HidHide 驱动，请按窗口提示完成授权',
                }
            except Exception:
                pass

    return {
        'ok': False,
        'launched': False,
        'fallback_browser': True,
        'release_url': release_url,
        'message': '直连下载超时，已在浏览器打开官方驱动下载页，请下载安装最新 .exe 驱动包',
    }

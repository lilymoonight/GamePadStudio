"""Backed-up Windows Game Bar and GameDVR preferences.

These settings disable Windows' controller overlay/capture responses; they do
not consume physical HID events or repair missing protocol handlers. The input
backend and the mapping dispatcher handle controller buttons separately.
"""

import sys
import os
import subprocess
from pathlib import Path
import subprocess
from pathlib import Path
from typing import Tuple, Dict, Any, Optional

if sys.platform == 'win32':
    import winreg
    import ctypes
else:
    winreg = None
    ctypes = None


# 涉及拦截的核心注册表项配置表: (根键, 子项路径, 键名, 屏蔽值, 恢复默认值)
TARGET_REG_ITEMS = [
    # 1. 禁用手柄按键 (Nexus / Share 键) 唤起 Game Bar / ms-gamebar 协议
    (
        "HKCU",
        r"Software\Microsoft\GameBar",
        "UseNexusForGameBarEnabled",
        0,  # 屏蔽时设为 0
        1   # 恢复时设为 1
    ),
    (
        "HKCU",
        r"Software\Microsoft\GameBar",
        "AutoNexusEnable",
        0,
        1
    ),
    # 2. 禁用 Windows 应用程序截图与后台游戏片段录制 (消除截屏横幅)
    (
        "HKCU",
        r"Software\Microsoft\Windows\CurrentVersion\GameDVR",
        "AppCaptureEnabled",
        0,
        1
    ),
    (
        "HKCU",
        r"Software\Microsoft\Windows\CurrentVersion\GameDVR",
        "HistoricalCaptureEnabled",
        0,
        1
    ),
    # 3. 禁用系统级 GameDVR 配置
    (
        "HKCU",
        r"System\GameConfigStore",
        "GameDVR_Enabled",
        0,
        1
    ),
    # 4. 可选：禁用 Xbox 游戏覆层通知横幅
    (
        "HKCU",
        r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings\Microsoft.XboxGamingOverlay_8wekyb3d8bbwe!App",
        "Enabled",
        0,
        1
    ),
]


def _broadcast_setting_change():
    """向系统广播 WM_SETTINGCHANGE 消息，提示系统输入与 GameBar 守护进程立即刷新配置"""
    if sys.platform != 'win32' or not ctypes:
        return
    try:
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002
        res = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            "Software\\Microsoft\\GameBar",
            SMTO_ABORTIFHUNG,
            500,
            ctypes.byref(res)
        )
    except Exception:
        pass


def _refresh_gamebar_processes() -> bool:
    """Close only this session's verified Game Bar hosts to clear cached settings.

    Called once when enabling the shield, never from polling or status reads.
    Leaves Xbox, gaming services and the controller driver running.
    """
    from .replay_service import _subprocess_hidden_flags
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    script = r'''
$ErrorActionPreference = 'Stop'
$session = (Get-Process -Id $PID).SessionId
$barHosts = Get-Process -Name GameBar,GameBarFTServer -ErrorAction SilentlyContinue
foreach ($barHost in $barHosts) {
    if ($barHost.SessionId -ne $session) { continue }
    if (-not $barHost.Path) { exit 2 }
    if ($barHost.Path -match '\\WindowsApps\\Microsoft\.XboxGamingOverlay_[^\\]+\\GameBar(FTServer)?\.exe$') {
        Stop-Process -Id $barHost.Id -ErrorAction Stop
    }
}
exit 0
'''
    try:
        result = subprocess.run([str(powershell), '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
                                **_subprocess_hidden_flags())
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _refresh_gamebar_processes() -> bool:
    """Close only this session's verified Game Bar hosts to clear cached settings.

    Called once when enabling the shield, never from polling or status reads.
    Leaves Xbox, gaming services and the controller driver running.
    """
    from .replay_service import _subprocess_hidden_flags
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    script = r'''
$ErrorActionPreference = 'Stop'
$session = (Get-Process -Id $PID).SessionId
$barHosts = Get-Process -Name GameBar,GameBarFTServer -ErrorAction SilentlyContinue
foreach ($barHost in $barHosts) {
    if ($barHost.SessionId -ne $session) { continue }
    if (-not $barHost.Path) { exit 2 }
    if ($barHost.Path -match '\\WindowsApps\\Microsoft\.XboxGamingOverlay_[^\\]+\\GameBar(FTServer)?\.exe$') {
        Stop-Process -Id $barHost.Id -ErrorAction Stop
    }
}
exit 0
'''
    try:
        result = subprocess.run([str(powershell), '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
                                **_subprocess_hidden_flags())
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _read_reg_dword(root_str: str, subkey: str, name: str) -> Optional[int]:
    """读取指定注册表项的 DWORD 值；若不存在或读取失败返回 None"""
    if sys.platform != 'win32' or not winreg:
        return None
    root_key = winreg.HKEY_CURRENT_USER if root_str == "HKCU" else winreg.HKEY_LOCAL_MACHINE
    try:
        with winreg.OpenKey(root_key, subkey, 0, winreg.KEY_READ) as k:
            val, _ = winreg.QueryValueEx(k, name)
            return int(val)
    except Exception:
        return None


def _write_reg_dword(root_str: str, subkey: str, name: str, val: int) -> bool:
    """写入指定注册表项的 DWORD 值（若子项不存在则自动创建）"""
    if sys.platform != 'win32' or not winreg:
        return False
    root_key = winreg.HKEY_CURRENT_USER if root_str == "HKCU" else winreg.HKEY_LOCAL_MACHINE
    try:
        with winreg.CreateKeyEx(root_key, subkey, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, int(val))
            return True
    except Exception:
        return False


def _delete_reg_value(root_str: str, subkey: str, name: str) -> bool:
    """删除指定的注册表值（用于完全还原初次安装前未配置的状态）"""
    if sys.platform != 'win32' or not winreg:
        return False
    root_key = winreg.HKEY_CURRENT_USER if root_str == "HKCU" else winreg.HKEY_LOCAL_MACHINE
    try:
        with winreg.OpenKey(root_key, subkey, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
            return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def is_gamebar_shield_active() -> bool:
    """
    检查当前系统是否处于'屏蔽截图弹窗与 Game Bar'状态
    判定规则：UseNexusForGameBarEnabled、AppCaptureEnabled、GameDVR_Enabled 均为 0
    """
    if sys.platform != 'win32':
        return False
    
    # 核心三项必须全为 0
    core_items = [
        ("HKCU", r"Software\Microsoft\GameBar", "UseNexusForGameBarEnabled"),
        ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled"),
        ("HKCU", r"System\GameConfigStore", "GameDVR_Enabled"),
    ]
    for root_str, subkey, name in core_items:
        val = _read_reg_dword(root_str, subkey, name)
        if val != 0:
            return False
    return True


def enable_gamebar_shield(existing_backup: Optional[Dict[str, Any]] = None) -> Tuple[bool, str, Dict[str, Any]]:
    """
    开启系统截图与 Game Bar 弹窗屏蔽：
    1. 自动备份修改前的注册表原始值（若已有备份则沿用）
    2. 将劫持按键与录屏相关的 DWORD 值置 0
    3. 广播设置变更让系统即时生效
    返回: (成功状态, 描述文本, 备份字典)
    """
    if sys.platform != 'win32':
        return False, "当前平台不是 Windows 系统", {}

    backup = dict(existing_backup) if existing_backup else {}

    # 1. 采集未记录过的项作为备份
    for root_str, subkey, name, _, _ in TARGET_REG_ITEMS:
        item_key = f"{root_str}:{subkey}:{name}"
        if item_key not in backup:
            current_val = _read_reg_dword(root_str, subkey, name)
            backup[item_key] = current_val

    # 2. 写入屏蔽值
    fail_count = 0
    for root_str, subkey, name, block_val, _ in TARGET_REG_ITEMS:
        ok = _write_reg_dword(root_str, subkey, name, block_val)
        if not ok:
            # 部分项如可选通知子键若不存在失败可容忍，但核心项失败需记录
            if name in ("UseNexusForGameBarEnabled", "AppCaptureEnabled", "GameDVR_Enabled"):
                fail_count += 1

    _broadcast_setting_change()

    if fail_count > 0 or not is_gamebar_shield_active():
        return False, f"写入注册表失败 ({fail_count} 项未成功)，请检查权限", backup
    if not _refresh_gamebar_processes():
        return False, "系统设置已保存，但游戏栏未能关闭；请关闭游戏栏后重试", backup
    if not _refresh_gamebar_processes():
        return False, "系统设置已保存，但游戏栏未能关闭；请关闭游戏栏后重试", backup
    return True, "已关闭 Windows 手柄游戏栏和系统游戏录制响应", backup


def disable_gamebar_shield(backup: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """
    关闭屏蔽，恢复 Windows 原生响应：
    1. 若提供了原始备份字典，严格原样恢复各键值（未设定的键执行安全删除）
    2. 若无备份，写入标准 Windows 官方默认值 (1)
    3. 广播设置变更
    返回: (成功状态, 描述文本)
    """
    if sys.platform != 'win32':
        return False, "当前平台不是 Windows 系统"

    fail_count = 0
    for root_str, subkey, name, _, default_val in TARGET_REG_ITEMS:
        item_key = f"{root_str}:{subkey}:{name}"
        if backup is not None and item_key not in backup:
            continue  # Never alter settings absent from this backup.
        target_val = backup[item_key] if backup is not None else default_val
        if target_val is None:
            ok = _delete_reg_value(root_str, subkey, name)
        else:
            ok = _write_reg_dword(root_str, subkey, name, int(target_val))
        if not ok or _read_reg_dword(root_str, subkey, name) != target_val:
            fail_count += 1

    _broadcast_setting_change()
    if fail_count:
        return False, f"未能恢复 {fail_count} 项系统设置；原始备份已保留"
    return True, "已恢复 Windows 截图键与 Game Bar 默认响应"


def set_gamebar_shield(store, enabled):
    """Persist a recoverable backup before changing any Windows settings."""
    backup = store.data.get('gamebar_shield_backup') or None
    if enabled:
        backup = dict(backup or {})
        for root, subkey, name, _, _ in TARGET_REG_ITEMS:
            key = f'{root}:{subkey}:{name}'
            if key not in backup:
                backup[key] = _read_reg_dword(root, subkey, name)
        store.data['gamebar_shield_backup'] = backup
        store.save()
        ok, message, backup = enable_gamebar_shield(backup)
        store.data['gamebar_shield_backup'] = backup
        store.data['gamebar_shield_enabled'] = ok
    else:
        ok, message = disable_gamebar_shield(backup)
        if ok:
            store.data['gamebar_shield_enabled'] = False
            store.data['gamebar_shield_backup'] = {}
    store.save()
    return ok, message


def get_gamebar_shield_summary() -> Dict[str, Any]:
    """获取当前注册表屏蔽状态的诊断汇总"""
    active = is_gamebar_shield_active()
    details = {}
    if sys.platform == 'win32':
        for root_str, subkey, name, _, _ in TARGET_REG_ITEMS:
            val = _read_reg_dword(root_str, subkey, name)
            details[f"{name}"] = val
    return {
        "active": active,
        "details": details
    }

"""
HidHide Windows Kernel Filter Driver Integration (硬件独占屏蔽与设备隐身服务)
1. 基于微软 WHQL 官方认证的 HidHide.sys 上层过滤驱动
2. 通过 Win32 DeviceIoControl 与 \\\\.\\HidHide 直接通信
3. 维护白名单（允许访问手柄的应用程序，如本程序）与黑名单（需对系统屏蔽的物理设备 Instance ID）
4. 彻底杜绝双重输入 (Double Input) 与游戏原生键位冲突，实现纯净虚拟键鼠接管
"""

import sys
import os
if sys.platform == 'win32':
    import winreg
else:
    winreg = None
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

from .i18n import tr

# HidHide 官方 IOCTL 控制码
IOCTL_GET_WHITELIST = 0x80016000
IOCTL_SET_WHITELIST = 0x80016004
IOCTL_GET_BLACKLIST = 0x80016008
IOCTL_SET_BLACKLIST = 0x8001600C
IOCTL_GET_ACTIVE    = 0x80016010
IOCTL_SET_ACTIVE    = 0x80016014

# 官方发布与下载页
HIDHIDE_RELEASE_URL = "https://github.com/nefarius/HidHide/releases/latest"

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
INVALID_HANDLE_VALUE = -1


def encode_multi_sz(strings: List[str]) -> bytes:
    """将字符串列表编码为 Windows MULTI_SZ 双空结尾 Unicode 宽字节流"""
    if not strings:
        return b'\x00\x00\x00\x00'
    return b''.join(s.encode('utf-16le') + b'\x00\x00' for s in strings) + b'\x00\x00'


def decode_multi_sz(data: bytes) -> List[str]:
    """从 MULTI_SZ 宽字节流解析出字符串列表"""
    try:
        text = data.decode('utf-16le', errors='ignore')
        return [part.strip() for part in text.split('\x00') if part.strip()]
    except Exception:
        return []


def dos_to_nt_path(dos_path: str) -> str:
    """将 Win32 DOS 路径 (例 D:\\...) 转换为 Windows 内核驱动所需的 NT 路径 (例 \\Device\\HarddiskVolume4\\...)"""
    if sys.platform != 'win32' or not dos_path:
        return dos_path
    try:
        drive = dos_path[:2]
        if len(drive) == 2 and drive[1] == ':':
            buf = (C.c_wchar * 1024)()
            res = C.windll.kernel32.QueryDosDeviceW(drive, buf, 1024)
            if res > 0:
                return buf.value + dos_path[2:]
    except Exception:
        pass
    return dos_path


def _get_present_hid_instance_ids() -> set[str]:
    """Query SetupAPI on Windows for currently connected (DIGCF_PRESENT) HID Device Instance IDs."""
    if sys.platform != 'win32':
        return set()
    present: set[str] = set()
    try:
        setupapi = C.WinDLL('setupapi', use_last_error=True)
        DIGCF_PRESENT = 0x00000002
        DIGCF_ALLCLASSES = 0x00000004

        class SP_DEVINFO_DATA(C.Structure):
            _fields_ = [
                ('cbSize', W.DWORD),
                ('ClassGuid', C.c_byte * 16),
                ('DevInst', W.DWORD),
                ('Reserved', C.c_void_p),
            ]

        setupapi.SetupDiGetClassDevsW.argtypes = [C.c_void_p, W.LPCWSTR, W.HWND, W.DWORD]
        setupapi.SetupDiGetClassDevsW.restype = C.c_void_p
        setupapi.SetupDiEnumDeviceInfo.argtypes = [C.c_void_p, W.DWORD, C.POINTER(SP_DEVINFO_DATA)]
        setupapi.SetupDiEnumDeviceInfo.restype = W.BOOL
        setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [
            C.c_void_p, C.POINTER(SP_DEVINFO_DATA), W.LPWSTR, W.DWORD, C.POINTER(W.DWORD)
        ]
        setupapi.SetupDiGetDeviceInstanceIdW.restype = W.BOOL
        setupapi.SetupDiDestroyDeviceInfoList.argtypes = [C.c_void_p]
        setupapi.SetupDiDestroyDeviceInfoList.restype = W.BOOL

        for enum_name in ('HID', 'BTHLEDevice', 'USB'):
            hdev = setupapi.SetupDiGetClassDevsW(None, enum_name, None, DIGCF_PRESENT | DIGCF_ALLCLASSES)
            if not hdev or hdev in (-1, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
                continue
            try:
                idx = 0
                info = SP_DEVINFO_DATA()
                info.cbSize = C.sizeof(SP_DEVINFO_DATA)
                buf = C.create_unicode_buffer(512)
                req = W.DWORD(0)
                while setupapi.SetupDiEnumDeviceInfo(hdev, idx, C.byref(info)):
                    idx += 1
                    if setupapi.SetupDiGetDeviceInstanceIdW(hdev, C.byref(info), buf, 512, C.byref(req)):
                        val = buf.value.strip().upper()
                        if val:
                            present.add(val)
            finally:
                setupapi.SetupDiDestroyDeviceInfoList(hdev)
    except Exception:
        return set()
    return present


_SYSTEM_CONTAINER_IDS = {'', '{00000000-0000-0000-ffff-ffffffffffff}'}
_NON_GAMEPAD_SERVICES = {'kbdhid', 'mouhid'}
_HIDHIDE_USB_XINPUT_CLASS_GUIDS = {
    '{d61ca365-5af4-4486-998b-9db4734c6ca3}',  # XnaComposite (USB XInput / xusb22)
    '{05f5cfe2-4733-4950-a6bb-07aad01a3a84}',  # XboxComposite (Xbox One / Series USB GIP)
}
_NON_GAMEPAD_DESC_KEYWORDS = (
    'keyboard',
    'mouse',
    'headset',
    'consumer control',
    'hid_device_system_consumer',
    'hid_device_headset',
    'wireless radio controls',
)


def _inspect_hid_registry_node(instance_path: str) -> Tuple[bool, str]:
    """Return (is_non_gamepad, peripheral_container_id) from Enum\\<BUS>\\<HW>\\<INST>."""
    if sys.platform != 'win32' or winreg is None or not instance_path:
        return False, ''
    parts = [p for p in str(instance_path).split('\\') if p]
    if len(parts) < 3:
        return False, ''
    bus, hw_name, inst_id = parts[0].upper(), parts[1], parts[2]
    if bus not in ('HID', 'USB', 'BTHENUM', 'BTHLE', 'BTHLEDEVICE'):
        return False, ''
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            rf"SYSTEM\CurrentControlSet\Enum\{bus}\{hw_name}\{inst_id}",
        ) as inst_key:
            is_non_gamepad = False
            if bus == 'HID':
                svc = ''
                desc = ''
                try:
                    svc = str(winreg.QueryValueEx(inst_key, 'Service')[0] or '').strip().lower()
                except OSError:
                    pass
                try:
                    desc = str(winreg.QueryValueEx(inst_key, 'DeviceDesc')[0] or '').strip().lower()
                except OSError:
                    pass
                if svc in _NON_GAMEPAD_SERVICES or any(k in desc for k in _NON_GAMEPAD_DESC_KEYWORDS):
                    is_non_gamepad = True
            cid = ''
            try:
                cid = str(winreg.QueryValueEx(inst_key, 'ContainerID')[0] or '').strip().lower()
            except OSError:
                pass
            if cid in _SYSTEM_CONTAINER_IDS:
                cid = ''
            return is_non_gamepad, cid
    except Exception:
        return False, ''


def find_hid_instances(
    vendor: Optional[int] = None,
    product: Optional[int] = None,
    include_virtual: bool = False,
) -> List[str]:
    """
    根据 VID 与 PID，从 Windows 注册表枚举当前系统的 HID 设备实例路径 (Device Instance Path)
    例如：'HID\\VID_054C&PID_0CE6&REV_0100\\7&1A2B3C4D&0&0000'
    兼容普通 USB HID、蓝牙 HID、以及 Windows Bluetooth LE 格式 (如 BTHLE 实例与合成本地 XInput 实例)。
    自动排除复合设备上的键盘/鼠标/耳机/多媒体消费类子接口，仅保留游戏控制器 HID 实例。
    """
    if sys.platform != 'win32' or winreg is None or (vendor is None and product is None):
        return []

    v_hex = f"{vendor:04X}".upper() if vendor is not None else ""
    p_hex = f"{product:04X}".upper() if product is not None else ""
    is_xbox_synthetic = (vendor == 0x045E and (product in (0x02FD, 0x028E, 0x02FF) or product is None))
    xbox_pids = {"0B13", "0B12", "02E0", "02EA", "02D1", "02DD", "02E3", "0B00", "0B05", "028E", "02FF"}
    known_mice = {"0932", "082C", "07A5"}

    found = []
    ig_fallback = []
    node_containers: Dict[str, str] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\HID") as root:
            n_subkeys = winreg.QueryInfoKey(root)[0]
            for i in range(n_subkeys):
                hw_name = winreg.EnumKey(root, i)
                u = hw_name.upper()
                is_ig_xinput = is_xbox_synthetic and ("&IG_00" in u or "&IG_01" in u)
                matched = True
                if v_hex:
                    if not (f"VID_{v_hex}" in u or f"VID&{v_hex}" in u or f"VID&02{v_hex}" in u or f"VID_{vendor:X}" in u):
                        matched = False
                if matched:
                    if is_xbox_synthetic:
                        if any(f"PID_{p}" in u or f"PID&{p}" in u for p in known_mice):
                            matched = False
                        elif not any(f"PID_{p}" in u or f"PID&{p}" in u for p in xbox_pids):
                            matched = False
                    elif p_hex:
                        if not (f"PID_{p_hex}" in u or f"PID&{p_hex}" in u or f"PID&02{p_hex}" in u or f"PID_{product:X}" in u):
                            matched = False
                if not matched and not is_ig_xinput:
                    continue
                try:
                    with winreg.OpenKey(root, hw_name) as hw_key:
                        n_inst = winreg.QueryInfoKey(hw_key)[0]
                        for j in range(n_inst):
                            inst_id = winreg.EnumKey(hw_key, j)
                            full_path = f"HID\\{hw_name}\\{inst_id}"
                            is_non_gamepad, cid = _inspect_hid_registry_node(full_path)
                            if is_non_gamepad:
                                continue
                            if cid:
                                node_containers[full_path.upper()] = cid
                            if matched:
                                if full_path not in found:
                                    found.append(full_path)
                            elif is_ig_xinput and full_path not in ig_fallback:
                                ig_fallback.append(full_path)
                except Exception:
                    pass
    except Exception:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\USB") as usb_root:
            n_usb = winreg.QueryInfoKey(usb_root)[0]
            for i in range(n_usb):
                hw_name = winreg.EnumKey(usb_root, i)
                u = hw_name.upper()
                matched = True
                if v_hex:
                    if not (f"VID_{v_hex}" in u or f"VID&{v_hex}" in u or f"VID&02{v_hex}" in u or f"VID_{vendor:X}" in u):
                        matched = False
                if matched:
                    if is_xbox_synthetic:
                        if any(f"PID_{p}" in u or f"PID&{p}" in u for p in known_mice):
                            matched = False
                        elif not any(f"PID_{p}" in u or f"PID&{p}" in u for p in xbox_pids):
                            matched = False
                    elif p_hex:
                        if not (f"PID_{p_hex}" in u or f"PID&{p_hex}" in u or f"PID&02{p_hex}" in u or f"PID_{product:X}" in u):
                            matched = False
                if not matched:
                    continue
                try:
                    with winreg.OpenKey(usb_root, hw_name) as hw_key:
                        n_inst = winreg.QueryInfoKey(hw_key)[0]
                        for j in range(n_inst):
                            inst_id = winreg.EnumKey(hw_key, j)
                            try:
                                with winreg.OpenKey(hw_key, inst_id) as inst_key:
                                    cls_guid = ''
                                    svc = ''
                                    cid = ''
                                    try:
                                        cls_guid = str(winreg.QueryValueEx(inst_key, 'ClassGUID')[0] or '').strip().lower()
                                    except OSError:
                                        pass
                                    try:
                                        svc = str(winreg.QueryValueEx(inst_key, 'Service')[0] or '').strip().lower()
                                    except OSError:
                                        pass
                                    if svc == 'usbccgp' or cls_guid not in _HIDHIDE_USB_XINPUT_CLASS_GUIDS:
                                        continue
                                    try:
                                        cid = str(winreg.QueryValueEx(inst_key, 'ContainerID')[0] or '').strip().lower()
                                    except OSError:
                                        pass
                                    if cid in _SYSTEM_CONTAINER_IDS:
                                        cid = ''
                            except OSError:
                                continue
                            full_path = f"USB\\{hw_name}\\{inst_id}"
                            if cid:
                                node_containers[full_path.upper()] = cid
                            if full_path not in found:
                                found.append(full_path)
                except Exception:
                    pass
    except Exception:
        pass
    present_ids = _get_present_hid_instance_ids()
    if present_ids:
        present_inst_ids = {
            item.split('\\')[2].upper()
            for item in present_ids
            if len(item.split('\\')) >= 3
        }
        present_containers = {
            node_containers[item.upper()]
            for item in (*found, *ig_fallback)
            if item.upper() in node_containers
            and (
                item.upper() in present_ids
                or (len(item.split('\\')) >= 3 and item.split('\\')[2].upper() in present_inst_ids)
            )
        }

        def _is_present_or_sibling(item: str) -> bool:
            up = item.upper()
            if up in present_ids:
                return True
            if len(item.split('\\')) >= 3 and item.split('\\')[2].upper() in present_inst_ids:
                return True
            cid = node_containers.get(up, '')
            return bool(cid and cid in present_containers)

        present_found = [item for item in found if _is_present_or_sibling(item)]
        if present_found:
            found = present_found
        present_ig = [item for item in ig_fallback if _is_present_or_sibling(item)]
        if present_ig:
            ig_fallback = present_ig
    if not include_virtual:
        try:
            from .virtual_xinput import _ACTIVE_VIRTUAL_PADS, _VIRTUAL_HID_INSTANCES, is_vigembus_virtual_hid_path
            found = [
                item for item in found
                if item.upper() not in _VIRTUAL_HID_INSTANCES and not is_vigembus_virtual_hid_path(item)
            ]
            ig_fallback = [
                item for item in ig_fallback
                if item.upper() not in _VIRTUAL_HID_INSTANCES and not is_vigembus_virtual_hid_path(item)
            ]
            if is_xbox_synthetic and _ACTIVE_VIRTUAL_PADS:
                non_virtual_xbox = [
                    item for item in found
                    if "PID_028E" not in item.upper() and "PID&028E" not in item.upper()
                ]
                if non_virtual_xbox:
                    found = non_virtual_xbox
        except Exception:
            pass
    if not found and ig_fallback:
        found = ig_fallback
    return found


def _all_candidates_belong_to_single_device(candidates: List[str]) -> bool:
    if len(candidates) <= 1:
        return len(candidates) == 1
    cids = []
    for item in candidates:
        _, cid = _inspect_hid_registry_node(item)
        if cid:
            cids.append(cid)
    if len(cids) == len(candidates):
        return len(set(cids)) == 1
    hw_names = set()
    parent_prefixes = set()
    for item in candidates:
        parts = [p for p in item.split('\\') if p]
        if len(parts) < 3:
            return False
        hw_names.add(parts[1].upper())
        inst = parts[2].upper()
        if '&' in inst:
            parent_prefixes.add(inst.rsplit('&', 1)[0])
        else:
            parent_prefixes.add(inst)
    if len(parent_prefixes) == 1 and any('&' in item.split('\\')[2] for item in candidates if len(item.split('\\')) >= 3):
        return True
    if len(hw_names) == len(candidates) and len(hw_names) > 1:
        return True
    return False


def resolve_controller_vid_pid(state: Optional[Dict[str, Any]]) -> Tuple[Optional[int], Optional[int]]:
    """Resolve (vendor, product) from a controller state dict, with path and XInput fallbacks."""
    if not isinstance(state, dict) or not state:
        return None, None
    vendor = state.get('vendor')
    product = state.get('product')
    dev_path = state.get('device_path')
    if (not vendor or not product) and dev_path:
        import re
        m = re.search(r'VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})', str(dev_path), re.IGNORECASE)
        if m:
            vendor = int(vendor or int(m.group(1), 16))
            product = int(product or int(m.group(2), 16))
    if (not vendor or not product) and (state.get('family') or state.get('name') or state.get('device_key')):
        return int(vendor or 0x045E), int(product or 0x028E)
    return (int(vendor) if vendor else None), (int(product) if product else None)


def selected_device_instances(vendor, product, device_path=None):
    """Resolve only the selected physical HID, never all pads of its model."""
    if vendor is None or product is None:
        return []
    candidates = list(find_hid_instances(vendor, product))
    if device_path:
        import re
        m = re.search(r'VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})', str(device_path), re.IGNORECASE)
        if m:
            path_vid, path_pid = int(m.group(1), 16), int(m.group(2), 16)
            if (path_vid, path_pid) != (vendor, product):
                for extra in find_hid_instances(path_vid, path_pid):
                    if extra not in candidates:
                        candidates.append(extra)
        normalized = str(device_path).upper().replace('#', '\\')
        normalized = normalized.removeprefix('\\\\?\\')
        normalized = normalized.split('\\{', 1)[0]
        exact = [item for item in candidates if item.upper() == normalized]
        by_inst: List[str] = []
        by_parent: List[str] = []
        norm_parts = [p for p in normalized.split('\\') if p]
        if len(norm_parts) >= 3:
            norm_inst = norm_parts[2]
            by_inst = [
                item for item in candidates
                if len(item.split('\\')) >= 3 and item.split('\\')[2].upper() == norm_inst
            ]
            if '&' in norm_inst:
                parent_prefix = norm_inst.rsplit('&', 1)[0] + '&'
                by_parent = [
                    item for item in candidates
                    if len(item.split('\\')) >= 3 and item.split('\\')[2].upper().startswith(parent_prefix)
                ]
        target_cids = set()
        _, norm_cid = _inspect_hid_registry_node(normalized)
        if norm_cid:
            target_cids.add(norm_cid)
        for matched_item in (*exact, *by_inst, *by_parent):
            _, m_cid = _inspect_hid_registry_node(matched_item)
            if m_cid:
                target_cids.add(m_cid)
        by_container: List[str] = []
        if target_cids:
            for item in candidates:
                _, item_cid = _inspect_hid_registry_node(item)
                if item_cid and item_cid in target_cids:
                    by_container.append(item)
        if exact or by_inst or by_parent or by_container:
            combined: List[str] = []
            for group in (exact, by_inst, by_parent, by_container):
                for item in group:
                    if item not in combined:
                        combined.append(item)
            return combined
        is_xinput_synthetic = (vendor == 0x045E and product in (0x028E, 0x02FF, 0x02FD))
        if (not normalized.startswith(('HID\\', 'USB\\')) or is_xinput_synthetic) and _all_candidates_belong_to_single_device(candidates):
            return candidates
        return []
    # Legacy drivers can omit an interface path. A single match (or multiple interfaces of one pad) is unambiguous.
    return candidates if _all_candidates_belong_to_single_device(candidates) else []


def find_all_gamepad_instances() -> List[str]:
    """
    枚举系统内接入的所有手柄/游戏控制器 HID 实例路径（直接复用 device_quirks 统一厂商库）
    """
    from .device_quirks import KNOWN_GAMING_VENDOR_IDS
    found = []
    for vid in KNOWN_GAMING_VENDOR_IDS:
        instances = find_hid_instances(vendor=vid, product=None)
        for inst in instances:
            if inst not in found:
                found.append(inst)
    return found


def is_apple_bootcamp_environment() -> bool:
    """Return True on Windows when running on Apple Mac Bootcamp hardware."""
    if sys.platform != 'win32' or winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\BIOS") as bios:
            for field in ("SystemManufacturer", "BaseBoardManufacturer"):
                try:
                    value, _ = winreg.QueryValueEx(bios, field)
                    if "apple" in str(value or "").lower():
                        return True
                except Exception:
                    pass
    except Exception:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\KeyMagic"):
            return True
    except Exception:
        return False


def hidhide_usage_guidance(bootcamp: Optional[bool] = None) -> str:
    """Return user-facing guidance on HidHide optionality, re-plug requirement, and Bootcamp safety."""
    if bootcamp is None:
        bootcamp = is_apple_bootcamp_environment()
    lines = [
        tr("💡 提示：设备隐身为可选功能（非必装），不安装也可正常使用虚拟键鼠、陀螺仪、连发与截图等全部核心功能。"),
        tr("🔄 生效说明：开启隐身后，已运行的游戏仍持有旧设备连接；请重新拔插一次手柄（或重启游戏），并关闭 Steam 后台的手柄接管。"),
    ]
    if bootcamp:
        lines.append(tr("⚠️ Bootcamp 警告：检测到 Apple Mac 双系统环境，请勿安装或启用 HidHide，以免与 Apple 内置键盘/触控板复合驱动冲突！"))
    else:
        lines.append(tr("⚠️ Mac 双系统提示：若使用 Mac Bootcamp 双系统，请勿安装 HidHide，以免导致内置键盘/触控板失灵。"))
    return "\n".join(lines)


def _iter_registry_hidhide_dirs() -> List[str]:
    if sys.platform != 'win32' or winreg is None:
        return []
    found: List[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Nefarius Software Solutions e.U.\HidHide") as k:
            val, _ = winreg.QueryValueEx(k, "Path")
            if val:
                found.append(str(val).strip().strip('"'))
    except Exception:
        pass
    uninstall_roots = (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    )
    for root_key in uninstall_roots:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root_key) as parent:
                idx = 0
                while True:
                    try:
                        sub_name = winreg.EnumKey(parent, idx)
                    except OSError:
                        break
                    idx += 1
                    try:
                        with winreg.OpenKey(parent, sub_name) as sub:
                            disp, _ = winreg.QueryValueEx(sub, "DisplayName")
                            if "hidhide" not in str(disp).lower():
                                continue
                            for field in ("InstallLocation", "DisplayIcon"):
                                try:
                                    loc, _ = winreg.QueryValueEx(sub, field)
                                    loc_str = str(loc).strip().strip('"').split(",", 1)[0]
                                    if loc_str:
                                        if loc_str.lower().endswith(".exe"):
                                            loc_str = os.path.dirname(loc_str)
                                        found.append(loc_str)
                                except OSError:
                                    pass
                    except OSError:
                        continue
        except Exception:
            continue
    return found


def resolve_hidhide_cli_path() -> Optional[str]:
    """Dynamically locate HidHideCLI.exe across registry, ProgramFiles env vars, and PATH."""
    if sys.platform != 'win32':
        return None
    import shutil
    candidates: List[str] = []
    for base_dir in _iter_registry_hidhide_dirs():
        candidates.append(os.path.join(base_dir, "x64", "HidHideCLI.exe"))
        candidates.append(os.path.join(base_dir, "HidHideCLI.exe"))
    for env_key in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        env_val = os.environ.get(env_key, "").strip()
        if env_val:
            candidates.append(os.path.join(env_val, "Nefarius Software Solutions", "HidHide", "x64", "HidHideCLI.exe"))
    candidates.append(r"C:\Program Files\Nefarius Software Solutions\HidHide\x64\HidHideCLI.exe")
    which_cli = shutil.which("HidHideCLI.exe") or shutil.which("HidHideCLI")
    if which_cli:
        candidates.append(which_cli)

    seen = set()
    for cand in candidates:
        norm = os.path.normcase(os.path.normpath(cand))
        if norm in seen:
            continue
        seen.add(norm)
        if os.path.exists(cand):
            return cand
    return None


def _run_hidhide_cli(cli_path: str, args: List[str], timeout: float = 8.0) -> int:
    """Invoke HidHideCLI.exe safely with DEVNULL stdin so interactive REPL never hangs."""
    import subprocess
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0
    try:
        completed = subprocess.run(
            [cli_path, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            creationflags=flags,
        )
        return int(completed.returncode)
    except Exception:
        return -1


def _query_hidhide_cli_output(cli_path: str, args: List[str], timeout: float = 8.0) -> Tuple[int, str]:
    import subprocess
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0
    try:
        completed = subprocess.run(
            [cli_path, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            creationflags=flags,
        )
        out = (completed.stdout or b"").decode("utf-8", "replace")
        return int(completed.returncode), out
    except Exception:
        return -1, ""


def _parse_cli_dev_list(raw_output: str) -> List[str]:
    import re
    items: List[str] = []
    for m in re.finditer(r'"((?:HID|USB|BTHENUM)\\[^"]+)"', raw_output or "", flags=re.IGNORECASE):
        val = m.group(1).strip()
        if val and val not in items:
            items.append(val)
    return items


def should_auto_cloak_controller(
    config: Dict[str, Any],
    state: Optional[Dict[str, Any]],
    mapping_enabled: bool = True,
) -> bool:
    """Return True only when an active output layer substitutes for the physical pad.

    Prevents hiding a physical controller into a 'dead controller' state when:
    - mapping is paused (`mapping_enabled=False`),
    - active profile is an unmapped native `'gamepad'` profile (passthrough), or
    - active profile has `'gamepad'` remaps, but `ViGEmBus` is not installed yet.
    """
    if not state or not mapping_enabled:
        return False
    from .studio_core import device_config, profile_has_gamepad_remap, profile_mode
    settings = device_config(config, state)
    if not settings.get('device_cloaking_enabled', True):
        return False
    active_name = str((config or {}).get('active_profile') or '')
    mode = profile_mode(config, active_name)
    if mode == 'kbm':
        return True
    if not profile_has_gamepad_remap(config, state, profile_name=active_name or None):
        return False
    from .virtual_xinput import virtual_gamepad_backend_available
    return bool(virtual_gamepad_backend_available())


def _session_file(root: Optional[Path] = None) -> Path:
    if root is None:
        from .ipc import default_root
        root = default_root()
    return Path(root) / 'hidhide_session.json'


def record_hidhide_session(cloaked_devices, root: Optional[Path] = None) -> None:
    """Persist owned cloaked devices so orphan locks can be recovered after crashes."""
    if sys.platform != 'win32':
        return
    import json
    path = _session_file(root)
    rows = []
    for item in cloaked_devices or ():
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            vendor, product = int(item[0] or 0), int(item[1] or 0)
            dev_path = str(item[2]) if len(item) >= 3 and item[2] else None
            if vendor and product:
                rows.append([vendor, product, dev_path])
    try:
        if not rows:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {'pid': os.getpid(), 'devices': rows}
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    except OSError:
        pass


def clear_hidhide_session(root: Optional[Path] = None) -> None:
    if sys.platform != 'win32':
        return
    try:
        _session_file(root).unlink(missing_ok=True)
    except OSError:
        pass


def recover_orphan_hidhide_session(root: Optional[Path] = None, force_all: bool = False) -> Tuple[bool, str]:
    """Recover controllers left hidden after a crash, power loss, or forced kill.

    Never touches HidHide rules created by other apps unless `force_all=True` (uninstall).
    """
    if sys.platform != 'win32':
        return True, ''
    import json
    if root is None:
        from .ipc import default_root
        root = default_root()
    root = Path(root)
    if not force_all:
        try:
            from .ipc import is_endpoint_alive
            if is_endpoint_alive(root, 'agent') or is_endpoint_alive(root, 'ui'):
                return True, 'GamePad Studio 正在运行，保留当前隐身状态'
        except Exception:
            pass

    client = HidHideClient()
    if not client.is_driver_installed():
        clear_hidhide_session(root)
        return True, '未安装 HidHide 驱动'

    if force_all:
        ok, msg = client.uncloak_all_controllers()
        clear_hidhide_session(root)
        return ok, msg

    sfile = _session_file(root)
    if not sfile.exists():
        return True, '无异常残留的隐身会话'

    try:
        payload = json.loads(sfile.read_text(encoding='utf-8'))
        devices = payload.get('devices') if isinstance(payload, dict) else []
    except Exception:
        devices = []

    all_ok = True
    for entry in devices or ():
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            vendor, product = int(entry[0] or 0), int(entry[1] or 0)
            dev_path = entry[2] if len(entry) >= 3 else None
            if vendor and product:
                opts = {'device_path': dev_path} if dev_path else {}
                ok, _ = client.uncloak_controller(vendor, product, **opts)
                if not ok:
                    all_ok = False
    if not devices:
        ok, _ = client.uncloak_all_controllers()
        all_ok = all_ok and ok
    elif not client.get_blacklist():
        client.set_active(False)

    if all_ok:
        clear_hidhide_session(root)
        return True, '已自动恢复上次异常退出残留的手柄共享访问'
    return False, '部分残留隐身设备未能自动解锁，请关闭 HidHide 官方窗口后重试'


def ensure_current_app_input_access(client=None) -> Tuple[bool, str]:
    """Allow this input application before SDL enumerates an already hidden pad.

    Installation and inactive-driver checks are read-only. With active filtering,
    only the application's access entry can be added; hiding settings stay intact.
    """
    if sys.platform != 'win32':
        return True, ''
    try:
        client = client if client is not None else HidHideClient()
        if not client.is_driver_installed():
            return True, ''
        ok, active = client._send_ioctl(IOCTL_GET_ACTIVE, out_size=1)
        if not ok or len(active) != 1:
            cli_path = resolve_hidhide_cli_path()
            if cli_path:
                rc, state_out = _query_hidhide_cli_output(cli_path, ["--cloak-state"])
                if rc == 0 and "false" in state_out.lower():
                    return True, ''
                if os.path.isfile(sys.executable) and _run_hidhide_cli(cli_path, ["--app-reg", sys.executable]) == 0:
                    return True, ''
            try:
                from .driver_setup import is_hidhide_gui_running
                if is_hidhide_gui_running():
                    return False, '检测到官方 HidHide 配置窗口正在运行并独占驱动，请先关闭该窗口。'
            except Exception:
                pass
            return False, '无法读取手柄访问状态，已隐身的手柄可能不可见。'
        if not active[0]:
            return True, ''
        if not client.allow_current_input_app():
            cli_path = resolve_hidhide_cli_path()
            if cli_path and os.path.isfile(sys.executable) and _run_hidhide_cli(cli_path, ["--app-reg", sys.executable]) == 0:
                return True, ''
            try:
                from .driver_setup import is_hidhide_gui_running
                if is_hidhide_gui_running():
                    return False, '检测到官方 HidHide 配置窗口正在运行并独占驱动，请先关闭该窗口。'
            except Exception:
                pass
            return False, '无法登记当前程序的手柄访问权限，已隐身的手柄可能不可见。'
        return True, ''
    except Exception:
        return False, '无法登记当前程序的手柄访问权限，已隐身的手柄可能不可见。'


_INVALID_HANDLES = {None, 0, -1, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF}


class HidHideClient:
    """
    HidHide 驱动通信客户端
    负责查询驱动状态、配置应用白名单、设备黑名单与全局激活状态
    """
    def __init__(self):
        self._kernel32 = None
        self._cli_active: Optional[bool] = None
        self._cli_blacklist: List[str] = []
        if sys.platform == 'win32':
            try:
                self._kernel32 = C.WinDLL('kernel32', use_last_error=True)
                self._kernel32.CreateFileW.argtypes = [
                    W.LPCWSTR, W.DWORD, W.DWORD, W.LPVOID, W.DWORD, W.DWORD, W.HANDLE
                ]
                self._kernel32.CreateFileW.restype = W.HANDLE
                self._kernel32.DeviceIoControl.argtypes = [
                    W.HANDLE, W.DWORD, W.LPVOID, W.DWORD, W.LPVOID, W.DWORD,
                    C.POINTER(W.DWORD), W.LPVOID
                ]
                self._kernel32.DeviceIoControl.restype = W.BOOL
                self._kernel32.CloseHandle.argtypes = [W.HANDLE]
                self._kernel32.CloseHandle.restype = W.BOOL
            except Exception:
                self._kernel32 = None
        try:
            if self.is_driver_installed():
                self.sanitize_blacklist()
        except Exception:
            pass

    def is_driver_installed(self) -> bool:
        """检查系统是否安装了 HidHide 过滤驱动"""
        if sys.platform != 'win32':
            return False

        # 方式 1：检查服务注册表项
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\HidHide") as k:
                return True
        except Exception:
            pass

        # 方式 2：尝试打开设备句柄
        try:
            h = self._open_device(read_only=True)
            if h is not None and h not in _INVALID_HANDLES:
                self._close_device(h)
                return True
        except Exception:
            pass

        return False

    def _open_device(self, read_only: bool = False) -> Optional[int]:
        if not self._kernel32:
            return None
        access = GENERIC_READ if read_only else (GENERIC_READ | GENERIC_WRITE)
        try:
            h = self._kernel32.CreateFileW(
                r"\\.\HidHide",
                access,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                None,
                OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL,
                None
            )
        except Exception:
            return None
        if h in _INVALID_HANDLES:
            return None
        return h

    def _close_device(self, handle: int):
        if self._kernel32 and handle not in _INVALID_HANDLES:
            try:
                self._kernel32.CloseHandle(handle)
            except Exception:
                pass

    def _send_ioctl(self, ioctl_code: int, in_bytes: bytes = b'', out_size: int = 4096) -> Tuple[bool, bytes]:
        """向 \\\\.\\HidHide 发送 IOCTL 命令"""
        h = self._open_device(read_only=(ioctl_code in (IOCTL_GET_ACTIVE, IOCTL_GET_WHITELIST, IOCTL_GET_BLACKLIST)))
        if not h:
            return False, b''

        try:
            in_buf = None
            in_len = len(in_bytes)
            if in_len > 0:
                in_buf = (C.c_char * in_len).from_buffer_copy(in_bytes)

            # HidHide.sys requires querying exact output buffer size first for GET_WHITELIST / GET_BLACKLIST
            if ioctl_code in (IOCTL_GET_WHITELIST, IOCTL_GET_BLACKLIST) and in_len == 0 and out_size > 0:
                needed = W.DWORD(0)
                ok_size = self._kernel32.DeviceIoControl(
                    h, ioctl_code, None, 0, None, 0, C.byref(needed), None
                )
                if ok_size and needed.value > 0:
                    if needed.value <= 2:
                        return True, b'\x00\x00\x00\x00'
                    exact_buf = (C.c_char * needed.value)()
                    ret_bytes = W.DWORD(0)
                    ok_data = self._kernel32.DeviceIoControl(
                        h, ioctl_code, None, 0, exact_buf, needed.value, C.byref(ret_bytes), None
                    )
                    if ok_data:
                        raw = bytes(exact_buf[:ret_bytes.value])
                        if raw in (b'', b'\x00\x00'):
                            return True, b'\x00\x00\x00\x00'
                        return True, raw
                elif ok_size and needed.value == 0:
                    # Try standard buffer call in case a mock returned needed=0
                    pass

            out_buf = None
            if out_size > 0:
                out_buf = (C.c_char * out_size)()

            bytes_returned = W.DWORD(0)
            ok = self._kernel32.DeviceIoControl(
                h,
                ioctl_code,
                in_buf,
                in_len,
                out_buf,
                out_size,
                C.byref(bytes_returned),
                None
            )
            if ok:
                ret_data = bytes(out_buf[:bytes_returned.value]) if out_buf else b''
                if ioctl_code in (IOCTL_GET_WHITELIST, IOCTL_GET_BLACKLIST) and ret_data in (b'', b'\x00\x00'):
                    return True, b'\x00\x00\x00\x00'
                return True, ret_data
            return False, b''
        except Exception:
            return False, b''
        finally:
            self._close_device(h)

    def is_active(self) -> bool:
        """获取全局设备隐身功能是否处于激活状态"""
        ok, data = self._send_ioctl(IOCTL_GET_ACTIVE, out_size=1)
        if ok and len(data) >= 1:
            val = bool(data[0])
            self._cli_active = val
            return val
        if self._cli_active is not None:
            return self._cli_active
        return False

    def set_active(self, active: bool) -> bool:
        """开启或关闭全局设备隐身功能"""
        in_byte = b'\x01' if active else b'\x00'
        ok, _ = self._send_ioctl(IOCTL_SET_ACTIVE, in_bytes=in_byte, out_size=0)
        if ok:
            self._cli_active = bool(active)
        return ok

    def get_whitelist(self) -> List[str]:
        """获取当前允许直接穿透访问手柄的白名单程序绝对路径列表"""
        ok, data = self._send_ioctl(IOCTL_GET_WHITELIST, out_size=8192)
        if ok:
            return decode_multi_sz(data)
        return []

    def set_whitelist(self, paths: List[str]) -> bool:
        """设置允许穿透的白名单程序列表"""
        payload = encode_multi_sz(paths)
        ok, _ = self._send_ioctl(IOCTL_SET_WHITELIST, in_bytes=payload, out_size=0)
        return ok

    def _read_whitelist_checked(self) -> Tuple[bool, List[str]]:
        """Keep a failed or truncated read distinct from an empty access list."""
        ok, data = self._send_ioctl(IOCTL_GET_WHITELIST, out_size=8192)
        if not ok or len(data) < 4 or len(data) % 2 or not data.endswith(b'\x00\x00\x00\x00'):
            return False, []
        try:
            text = data.decode('utf-16le', errors='strict')
        except UnicodeError:
            return False, []
        return True, [item for item in text.split('\x00') if item]

    def _append_whitelist_paths(self, paths: List[str]) -> bool:
        ok, whitelist = self._read_whitelist_checked()
        if not ok:
            return False
        original = list(whitelist)
        for path in paths:
            normalized = os.path.normpath(path).casefold()
            if not any(os.path.normpath(existing).casefold() == normalized for existing in whitelist):
                whitelist.append(path)
        return self.set_whitelist(whitelist) if whitelist != original else True

    def allow_current_input_app(self) -> bool:
        """Register only this executable, preserving every existing access entry."""
        candidates = [sys.executable]
        nt_path = dos_to_nt_path(sys.executable)
        if nt_path and nt_path not in candidates:
            candidates.append(nt_path)
        return self._append_whitelist_paths(candidates)

    def _current_app_whitelist_candidates(self) -> Tuple[List[str], List[str]]:
        curr_exe = sys.executable
        base_exe = getattr(sys, '_base_executable', curr_exe)
        dos_paths: List[str] = []
        for exe in (curr_exe, base_exe):
            if exe and exe not in dos_paths:
                dos_paths.append(exe)
            p_obj = Path(exe)
            if 'python' in p_obj.name.lower():
                pyw = str(p_obj.with_name('pythonw.exe'))
                py = str(p_obj.with_name('python.exe'))
                for candidate in (pyw, py):
                    if os.path.isfile(candidate) and candidate not in dos_paths:
                        dos_paths.append(candidate)

        full_candidates: List[str] = []
        for p in dos_paths:
            if p not in full_candidates:
                full_candidates.append(p)
            nt_p = dos_to_nt_path(p)
            if nt_p and nt_p not in full_candidates:
                full_candidates.append(nt_p)
        return dos_paths, full_candidates

    def add_current_app_to_whitelist(self) -> bool:
        """确保当前运行的 Python 进程及其同伴程序 (python.exe, pythonw.exe 等，包含 venv 与真实基准解释器) 在白名单中 (同时加入 NT 设备路径与 DOS 路径)"""
        _, full_candidates = self._current_app_whitelist_candidates()
        return self._append_whitelist_paths(full_candidates)

    def get_blacklist(self) -> List[str]:
        """获取当前被屏蔽隐身的外设 Instance ID 列表"""
        ok, data = self._send_ioctl(IOCTL_GET_BLACKLIST, out_size=8192)
        if ok:
            items = decode_multi_sz(data)
            self._cli_blacklist = list(items)
            return items
        if self._cli_blacklist:
            return list(self._cli_blacklist)
        return []

    def set_blacklist(self, device_instance_ids: List[str]) -> bool:
        """设置被屏蔽隐身的外设列表"""
        payload = encode_multi_sz(device_instance_ids)
        ok, _ = self._send_ioctl(IOCTL_SET_BLACKLIST, in_bytes=payload, out_size=0)
        if ok:
            self._cli_blacklist = list(device_instance_ids)
        return ok

    def sanitize_blacklist(self) -> bool:
        return True

    def cloak_all_controllers(self) -> Tuple[bool, str]:
        """彻底对外部系统隐身并屏蔽所有接入的手柄设备（无论是 PS5、Xbox 还是 Switch Pro 等）"""
        return self.cloak_controller(None, None)

    def uncloak_all_controllers(self) -> Tuple[bool, str]:
        """解除所有手柄的隐身屏蔽，清空黑名单并关闭全局过滤驱动，恢复系统共享访问"""
        return self.uncloak_controller(None, None)

    def cloak_controller(self, vendor: Optional[int] = None, product: Optional[int] = None, device_path=None) -> Tuple[bool, str]:
        """
        对控制器执行一键独占隐身接管：
        1. 确保当前主程序在白名单中
        2. 查找特定控制器或系统内所有接入的手柄物理实例 ID
        3. 添加至黑名单并激活驱动
        若直接内核 IOCTL 因权限不足受阻，自动调用 HidHideCLI 申请 Windows 管理员提权完成配置
        """
        if sys.platform != 'win32':
            return False, "HidHide 仅支持 Windows，macOS 无法隐藏手柄原始输入"
        if not self.is_driver_installed():
            return False, "未检测到 HidHide 驱动，请先安装驱动"

        if vendor is not None or product is not None:
            instances = selected_device_instances(vendor, product, device_path)
        else:
            instances = find_all_gamepad_instances()

        if not instances:
            return False, "无法唯一匹配当前设备的 HID 节点，请重新连接后重试"

        # 尝试通过直接内核 IOCTL 通信
        direct_ok = False
        try:
            wl_ok = self.add_current_app_to_whitelist()
            if not wl_ok:
                ok_raw, raw_wl = self._send_ioctl(IOCTL_GET_WHITELIST, out_size=8192)
                if ok_raw and not decode_multi_sz(raw_wl):
                    _, full_candidates = self._current_app_whitelist_candidates()
                    wl_ok = self.set_whitelist(full_candidates)
            if wl_ok:
                blacklist = self.get_blacklist()
                for inst in instances:
                    norm_inst = inst.upper()
                    if not any(b.upper() == norm_inst for b in blacklist):
                        blacklist.append(inst)
                bl_ok = self.set_blacklist(blacklist)
                act_ok = self.set_active(True)
                if bl_ok and act_ok:
                    direct_ok = True
        except Exception:
            direct_ok = False

        if direct_ok:
            return True, f"已成功将 {len(instances)} 个硬件手柄节点屏蔽，对外部游戏完全隐身（请重新拔插一次手柄或重启游戏使其生效）"

        # 若直接通信未成功（非管理员或受系统权限限制），通过动态解析的 HidHideCLI 执行
        cli_path = resolve_hidhide_cli_path()
        if cli_path and os.path.exists(cli_path):
            dos_paths, _ = self._current_app_whitelist_candidates()
            existing_dos = [p for p in dos_paths if os.path.isfile(p)] or [sys.executable]
            try:
                import subprocess
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0
                # 1. 先尝试直接无弹窗调用 HidHideCLI（单次连接顺序执行，stdin=DEVNULL 防挂起）
                seq_args: List[str] = []
                for app_p in existing_dos:
                    seq_args.extend(["--app-reg", app_p])
                for inst in instances:
                    seq_args.extend(["--dev-hide", inst])
                seq_args.append("--cloak-on")
                if _run_hidhide_cli(cli_path, seq_args) == 0:
                    for inst in instances:
                        if not any(b.upper() == inst.upper() for b in self._cli_blacklist):
                            self._cli_blacklist.append(inst)
                    self._cli_active = True
                    return True, f"已成功将 {len(instances)} 个硬件手柄节点屏蔽，对外部游戏完全隐身（请重新拔插一次手柄或重启游戏使其生效）"

                # 2. 若官方 HidHide Client GUI 正在运行并独占句柄，优先明确告知用户关闭该窗口，避免徒劳弹出 UAC
                try:
                    from .driver_setup import is_hidhide_gui_running
                    if is_hidhide_gui_running():
                        return False, "写入隐身配置失败：检测到官方 HidHide Configuration Client 窗口正在运行并独占驱动句柄，请先关闭该窗口后再试"
                except Exception:
                    pass

                # 3. 若无提权调用失败，则通过 UAC 提权在单个 PowerShell 会话中顺序执行
                ps_cmds = [f'& "{cli_path}" --app-reg "{p}"' for p in existing_dos]
                ps_cmds.extend([f'& "{cli_path}" --dev-hide "{inst}"' for inst in instances])
                ps_cmds.append(f'& "{cli_path}" --cloak-on')
                joined_script = "; ".join(ps_cmds)
                escaped_script = joined_script.replace("'", "''")
                cmd = f"Start-Process powershell -ArgumentList '-NoProfile -WindowStyle Hidden -Command \"{escaped_script}\"' -Verb RunAs -Wait"
                completed = subprocess.run(
                    ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", cmd],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=30.0,
                    creationflags=flags,
                )
                if completed.returncode == 0:
                    for inst in instances:
                        if not any(b.upper() == inst.upper() for b in self._cli_blacklist):
                            self._cli_blacklist.append(inst)
                    self._cli_active = True
                    return True, f"已通过管理员权限成功将 {len(instances)} 个硬件手柄节点屏蔽，对外部游戏完全隐身（请重新拔插一次手柄或重启游戏使其生效）"
            except Exception as e:
                return False, f"管理员提权执行失败: {e}"

        try:
            from .driver_setup import is_hidhide_gui_running
            if is_hidhide_gui_running():
                return False, "写入隐身配置失败：检测到官方 HidHide Configuration Client 窗口正在运行并独占驱动句柄，请先关闭该窗口后再试"
        except Exception:
            pass
        return False, "写入隐身配置失败：若后台正打开着 HidHide 官方配置窗口 (HidHide Client)，请先将其关闭后再试，或以管理员身份运行工作台"

    def uncloak_controller(self, vendor: Optional[int] = None, product: Optional[int] = None, device_path=None) -> Tuple[bool, str]:
        """解除特定控制器的隐身屏蔽，恢复系统共享访问"""
        if sys.platform != 'win32':
            return False, "HidHide 仅支持 Windows，macOS 无法隐藏手柄原始输入"
        if not self.is_driver_installed():
            return False, "未检测到 HidHide 驱动"

        cli_path = resolve_hidhide_cli_path()
        targets = selected_device_instances(vendor, product, device_path) if vendor is not None or product is not None else None
        if targets == []:
            # 当设备已拔出/断开导致 find_hid_instances 为空时，从黑名单中基于路径或 VID/PID 找回匹配项
            candidates = find_hid_instances(vendor, product)
            if not candidates:
                blacklist = self.get_blacklist()
                if device_path:
                    normalized = str(device_path).upper().replace('#', '\\')
                    normalized = normalized.removeprefix('\\\\?\\').split('\\{', 1)[0]
                    _, norm_cid = _inspect_hid_registry_node(normalized)
                    matched_in_bl = []
                    for item in blacklist:
                        if item.upper() == normalized:
                            matched_in_bl.append(item)
                        elif norm_cid:
                            _, item_cid = _inspect_hid_registry_node(item)
                            if item_cid and item_cid == norm_cid:
                                matched_in_bl.append(item)
                    if matched_in_bl:
                        targets = matched_in_bl
                elif vendor is not None:
                    v_hex = f"{vendor:04X}".upper()
                    p_hex = f"{product:04X}".upper() if product is not None else ""
                    matched_in_bl = [
                        item for item in blacklist
                        if f"VID_{v_hex}" in item.upper() or f"VID&{v_hex}" in item.upper()
                    ]
                    if p_hex:
                        matched_in_bl = [
                            item for item in matched_in_bl
                            if f"PID_{p_hex}" in item.upper() or f"PID&{p_hex}" in item.upper()
                        ]
                    if len(matched_in_bl) == 1:
                        targets = matched_in_bl
                    elif len(matched_in_bl) == 0:
                        if not blacklist and self.is_active():
                            self.set_active(False)
                        return True, "当前手柄未处于屏蔽状态（已恢复共享访问）"

        if targets == []:
            return False, "无法唯一匹配当前设备的 HID 节点，请重新连接后重试"
        remaining = [item for item in self.get_blacklist() if targets is not None and item.upper() not in {target.upper() for target in targets}]

        # 尝试直接 IOCTL 通信
        direct_ok = False
        try:
            if not vendor and not product:
                if self.set_blacklist([]) and self.set_active(False):
                    direct_ok = True
            else:
                if self.set_blacklist(remaining) and self.set_active(bool(remaining)):
                    direct_ok = True
        except Exception:
            direct_ok = False

        if direct_ok:
            if not vendor and not product:
                return True, "已恢复所有手柄共享访问，驱动过滤已关闭"
            return True, "已解除控制器隐身，系统已恢复共享访问"

        # CLI 回退（同时清空黑名单项并更新激活状态）
        if cli_path and os.path.exists(cli_path):
            try:
                import subprocess
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0
                if not vendor and not product:
                    known_bl = list(self.get_blacklist())
                    rc_list, out_list = _query_hidhide_cli_output(cli_path, ["--dev-list"])
                    if rc_list == 0:
                        for parsed in _parse_cli_dev_list(out_list):
                            if not any(b.upper() == parsed.upper() for b in known_bl):
                                known_bl.append(parsed)
                    seq_args = []
                    for inst in known_bl:
                        seq_args.extend(["--dev-unhide", inst])
                    seq_args.append("--cloak-off")
                    if _run_hidhide_cli(cli_path, seq_args) == 0:
                        self._cli_blacklist.clear()
                        self._cli_active = False
                        return True, "已恢复所有手柄共享访问"
                    ps_cmds = [f'& "{cli_path}" --dev-unhide "{inst}"' for inst in known_bl]
                    ps_cmds.append(f'& "{cli_path}" --cloak-off')
                    escaped_script = "; ".join(ps_cmds).replace("'", "''")
                    cmd = f"Start-Process powershell -ArgumentList '-NoProfile -WindowStyle Hidden -Command \"{escaped_script}\"' -Verb RunAs -Wait"
                else:
                    instances = targets or []
                    cloak_flag = '--cloak-on' if remaining else '--cloak-off'
                    seq_args = []
                    for inst in instances:
                        seq_args.extend(["--dev-unhide", inst])
                    seq_args.append(cloak_flag)
                    if _run_hidhide_cli(cli_path, seq_args) == 0:
                        self._cli_blacklist = list(remaining)
                        self._cli_active = bool(remaining)
                        return True, "已解除当前控制器隐身"
                    ps_cmds = [f'& "{cli_path}" --dev-unhide "{inst}"' for inst in instances]
                    ps_cmds.append(f'& "{cli_path}" {cloak_flag}')
                    escaped_script = "; ".join(ps_cmds).replace("'", "''")
                    cmd = f"Start-Process powershell -ArgumentList '-NoProfile -WindowStyle Hidden -Command \"{escaped_script}\"' -Verb RunAs -Wait"
                try:
                    from .driver_setup import is_hidhide_gui_running
                    if is_hidhide_gui_running():
                        return False, "解除设备隐身失败：检测到官方 HidHide Configuration Client 正在运行并独占驱动句柄，请先关闭该窗口后再试"
                except Exception:
                    pass
                completed = subprocess.run(
                    ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", cmd],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=30.0,
                    creationflags=flags,
                )
                if completed.returncode == 0:
                    if not vendor and not product:
                        self._cli_blacklist.clear()
                        self._cli_active = False
                        return True, "已恢复所有手柄共享访问"
                    self._cli_blacklist = list(remaining)
                    self._cli_active = bool(remaining)
                    return True, "已解除当前控制器隐身"
            except Exception:
                pass

        try:
            from .driver_setup import is_hidhide_gui_running
            if is_hidhide_gui_running():
                return False, "解除设备隐身失败：检测到官方 HidHide Configuration Client 正在运行并独占驱动句柄，请先关闭该窗口后再试"
        except Exception:
            pass
        return False, "解除当前设备隐身失败，请检查驱动权限"


    def get_status_summary(self) -> Dict[str, Any]:
        """获取综合状态字典"""
        installed = self.is_driver_installed()
        active = self.is_active() if installed else False
        return {
            "installed": installed,
            "active": active,
            "whitelist_count": len(self.get_whitelist()) if installed else 0,
            "blacklist_count": len(self.get_blacklist()) if installed else 0,
            "release_url": HIDHIDE_RELEASE_URL,
        }

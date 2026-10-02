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


def find_hid_instances(vendor: Optional[int] = None, product: Optional[int] = None) -> List[str]:
    """
    根据 VID 与 PID，从 Windows 注册表枚举当前系统的 HID 设备实例路径 (Device Instance Path)
    例如：'HID\\VID_054C&PID_0CE6&REV_0100\\7&1A2B3C4D&0&0000'
    兼容普通 USB HID、蓝牙 HID、以及 Windows Bluetooth LE 格式 (如 BTHLE 实例与合成本地 XInput 实例)。
    """
    if sys.platform != 'win32' or winreg is None or (vendor is None and product is None):
        return []

    v_hex = f"{vendor:04X}".upper() if vendor is not None else ""
    p_hex = f"{product:04X}".upper() if product is not None else ""
    is_xbox_synthetic = (vendor == 0x045E and (product in (0x02FD, 0x028E, 0x02FF) or product is None))
    xbox_pids = {"0B13", "0B12", "02E0", "02EA", "02D1", "02DD", "02E3", "0B00", "0B05", "028E", "02FF"}
    known_mice = {"0932", "082C", "07A5"}

    found = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\HID") as root:
            n_subkeys = winreg.QueryInfoKey(root)[0]
            for i in range(n_subkeys):
                hw_name = winreg.EnumKey(root, i)
                u = hw_name.upper()
                if v_hex:
                    if not (f"VID_{v_hex}" in u or f"VID&{v_hex}" in u or f"VID&02{v_hex}" in u or f"VID_{vendor:X}" in u):
                        continue
                if is_xbox_synthetic:
                    if any(f"PID_{p}" in u or f"PID&{p}" in u for p in known_mice):
                        continue
                    if not any(f"PID_{p}" in u or f"PID&{p}" in u for p in xbox_pids):
                        continue
                elif p_hex:
                    if not (f"PID_{p_hex}" in u or f"PID&{p_hex}" in u or f"PID&02{p_hex}" in u or f"PID_{product:X}" in u):
                        continue
                try:
                    with winreg.OpenKey(root, hw_name) as hw_key:
                        n_inst = winreg.QueryInfoKey(hw_key)[0]
                        for j in range(n_inst):
                            inst_id = winreg.EnumKey(hw_key, j)
                            full_path = f"HID\\{hw_name}\\{inst_id}"
                            if full_path not in found:
                                found.append(full_path)
                except Exception:
                    pass
    except Exception:
        pass
    return found


def selected_device_instances(vendor, product, device_path=None):
    """Resolve only the selected physical HID, never all pads of its model."""
    if vendor is None or product is None:
        return []
    candidates = find_hid_instances(vendor, product)
    if device_path:
        normalized = str(device_path).upper().replace('#', '\\')
        normalized = normalized.removeprefix('\\\\?\\')
        normalized = normalized.split('\\{', 1)[0]
        exact = [item for item in candidates if item.upper() == normalized]
        return exact
    # Legacy drivers can omit an interface path. A single match is unambiguous.
    return candidates if len(candidates) == 1 else []


def find_all_gamepad_instances() -> List[str]:
    """
    枚举系统内接入的所有手柄/游戏控制器 HID 实例路径（涵盖 PS5/PS4、Xbox 全系、Switch Pro、第三方通用 HID 手柄）
    """
    found = []
    gaming_vids = (0x054C, 0x045E, 0x057E, 0x20D6, 0x0E6F, 0x1532, 0x0738, 0x2563, 0x2DC8)
    for vid in gaming_vids:
        instances = find_hid_instances(vendor=vid, product=None)
        for inst in instances:
            if inst not in found:
                found.append(inst)
    return found


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
            return False, '无法读取手柄访问状态，已隐身的手柄可能不可见。'
        if not active[0]:
            return True, ''
        if not client.allow_current_input_app():
            return False, '无法登记当前程序的手柄访问权限，已隐身的手柄可能不可见。'
        return True, ''
    except Exception:
        return False, '无法登记当前程序的手柄访问权限，已隐身的手柄可能不可见。'


class HidHideClient:
    """
    HidHide 驱动通信客户端
    负责查询驱动状态、配置应用白名单、设备黑名单与全局激活状态
    """
    def __init__(self):
        self._kernel32 = None
        if sys.platform == 'win32':
            try:
                self._kernel32 = C.WinDLL('kernel32', use_last_error=True)
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
        h = self._open_device(read_only=True)
        if h is not None and h != INVALID_HANDLE_VALUE:
            self._close_device(h)
            return True

        return False

    def _open_device(self, read_only: bool = False) -> Optional[int]:
        if not self._kernel32:
            return None
        access = GENERIC_READ if read_only else (GENERIC_READ | GENERIC_WRITE)
        h = self._kernel32.CreateFileW(
            r"\\.\HidHide",
            access,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL,
            None
        )
        if h == INVALID_HANDLE_VALUE or h is None:
            return None
        return h

    def _close_device(self, handle: int):
        if self._kernel32 and handle and handle != INVALID_HANDLE_VALUE:
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
                return True, ret_data
            return False, b''
        finally:
            self._close_device(h)

    def is_active(self) -> bool:
        """获取全局设备隐身功能是否处于激活状态"""
        ok, data = self._send_ioctl(IOCTL_GET_ACTIVE, out_size=1)
        if ok and len(data) >= 1:
            return bool(data[0])
        return False

    def set_active(self, active: bool) -> bool:
        """开启或关闭全局设备隐身功能"""
        in_byte = b'\x01' if active else b'\x00'
        ok, _ = self._send_ioctl(IOCTL_SET_ACTIVE, in_bytes=in_byte, out_size=0)
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

    def add_current_app_to_whitelist(self) -> bool:
        """确保当前运行的 Python 进程及其同伴程序 (python.exe, pythonw.exe 等，包含 venv 与真实基准解释器) 在白名单中 (同时加入 NT 设备路径与 DOS 路径)"""
        curr_exe = sys.executable
        base_exe = getattr(sys, '_base_executable', curr_exe)
        paths_to_add = [curr_exe, base_exe]
        for exe in (curr_exe, base_exe):
            p_obj = Path(exe)
            if 'python' in p_obj.name.lower():
                pyw = str(p_obj.with_name('pythonw.exe'))
                py = str(p_obj.with_name('python.exe'))
                for candidate in (pyw, py):
                    if os.path.isfile(candidate) and candidate not in paths_to_add:
                        paths_to_add.append(candidate)

        # 转换为完整候选列表（DOS + NT 路径）
        full_candidates = []
        for p in paths_to_add:
            full_candidates.append(p)
            nt_p = dos_to_nt_path(p)
            if nt_p and nt_p not in full_candidates:
                full_candidates.append(nt_p)

        return self._append_whitelist_paths(full_candidates)

    def get_blacklist(self) -> List[str]:
        """获取当前被屏蔽隐身的外设 Instance ID 列表"""
        ok, data = self._send_ioctl(IOCTL_GET_BLACKLIST, out_size=8192)
        if ok:
            return decode_multi_sz(data)
        return []

    def set_blacklist(self, device_instance_ids: List[str]) -> bool:
        """设置被屏蔽隐身的外设列表"""
        payload = encode_multi_sz(device_instance_ids)
        ok, _ = self._send_ioctl(IOCTL_SET_BLACKLIST, in_bytes=payload, out_size=0)
        return ok

    def sanitize_blacklist(self) -> bool:
        return True

    def cloak_all_controllers(self) -> Tuple[bool, str]:
        """彻底对外部系统隐身并屏蔽所有接入的手柄设备（无论是 PS5、Xbox 还是 Switch Pro 等）"""
        return self.cloak_controller(None, None)

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
            blacklist = self.get_blacklist()
            for inst in instances:
                norm_inst = inst.upper()
                if not any(b.upper() == norm_inst for b in blacklist):
                    blacklist.append(inst)
            bl_ok = self.set_blacklist(blacklist)
            act_ok = self.set_active(True)
            if wl_ok and bl_ok and act_ok:
                direct_ok = True
        except Exception:
            direct_ok = False

        if direct_ok:
            return True, f"已成功将 {len(instances)} 个硬件手柄节点屏蔽，对外部游戏完全隐身"

        # 若直接通信未成功（非管理员或受系统权限限制），通过 HidHideCLI 进行 UAC 提权代理
        cli_path = r"C:\Program Files\Nefarius Software Solutions\HidHide\x64\HidHideCLI.exe"
        if os.path.exists(cli_path):
            hide_args = " ".join([f'--dev-hide "{inst}"' for inst in instances])
            pyw = sys.executable.replace("python.exe", "pythonw.exe")
            app_args = f'--app-reg "{sys.executable}" --app-reg "{pyw}"'
            cmd = f'Start-Process "{cli_path}" -ArgumentList \'{app_args} {hide_args} --cloak-on\' -Verb RunAs -Wait'
            try:
                import subprocess
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0
                ret = subprocess.call(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", cmd], creationflags=flags)
                if ret == 0:
                    return True, f"已通过管理员权限成功将 {len(instances)} 个硬件手柄节点屏蔽，对外部游戏完全隐身"
            except Exception as e:
                return False, f"管理员提权执行失败: {e}"

        return False, "写入黑名单失败，请检查驱动权限或以管理员身份运行工作台"

    def uncloak_controller(self, vendor: Optional[int] = None, product: Optional[int] = None, device_path=None) -> Tuple[bool, str]:
        """解除特定控制器的隐身屏蔽，恢复系统共享访问"""
        if sys.platform != 'win32':
            return False, "HidHide 仅支持 Windows，macOS 无法隐藏手柄原始输入"
        if not self.is_driver_installed():
            return False, "未检测到 HidHide 驱动"

        cli_path = r"C:\Program Files\Nefarius Software Solutions\HidHide\x64\HidHideCLI.exe"
        targets = selected_device_instances(vendor, product, device_path) if vendor is not None or product is not None else None
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
            return True, "已解除控制器隐身，系统已恢复共享访问"

        # 提权回退
        if os.path.exists(cli_path):
            if not vendor and not product:
                cmd = f'Start-Process "{cli_path}" -ArgumentList \'--cloak-off\' -Verb RunAs -Wait'
            else:
                instances = targets
                unhide_args = " ".join([f'--dev-unhide "{inst}"' for inst in instances]) if instances else ""
                cloak_flag = '--cloak-on' if remaining else '--cloak-off'
                cmd = f'Start-Process "{cli_path}" -ArgumentList \'{unhide_args} {cloak_flag}\' -Verb RunAs -Wait'
            try:
                import subprocess
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0
                result = subprocess.call(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", cmd], creationflags=flags)
                if result == 0:
                    return True, "已解除当前控制器隐身"
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

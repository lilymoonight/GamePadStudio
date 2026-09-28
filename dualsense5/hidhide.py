"""
HidHide Windows Kernel Filter Driver Integration (硬件独占屏蔽与设备隐身服务)
1. 基于微软 WHQL 官方认证的 HidHide.sys 上层过滤驱动
2. 通过 Win32 DeviceIoControl 与 \\\\.\\HidHide 直接通信
3. 维护白名单（允许访问手柄的应用程序，如本程序）与黑名单（需对系统屏蔽的物理设备 Instance ID）
4. 彻底杜绝双重输入 (Double Input) 与游戏原生键位冲突，实现纯净虚拟键鼠接管
"""

import sys
import os
import winreg
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


def find_hid_instances(vendor: Optional[int] = None, product: Optional[int] = None) -> List[str]:
    """
    根据 VID 与 PID，从 Windows 注册表枚举当前系统的 HID 设备实例路径 (Device Instance Path)
    例如：'HID\\VID_054C&PID_0CE6&REV_0100\\7&1A2B3C4D&0&0000'
    """
    results = []
    target_pattern = ""
    if vendor is not None and product is not None:
        target_pattern = f"VID_{vendor:04X}&PID_{product:04X}".upper()
    elif vendor is not None:
        target_pattern = f"VID_{vendor:04X}".upper()

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\HID") as root:
            n_subkeys = winreg.QueryInfoKey(root)[0]
            for i in range(n_subkeys):
                hw_name = winreg.EnumKey(root, i)
                if target_pattern and target_pattern not in hw_name.upper():
                    continue
                try:
                    with winreg.OpenKey(root, hw_name) as hw_key:
                        n_inst = winreg.QueryInfoKey(hw_key)[0]
                        for j in range(n_inst):
                            inst_id = winreg.EnumKey(hw_key, j)
                            full_path = f"HID\\{hw_name}\\{inst_id}"
                            results.append(full_path)
                except Exception:
                    pass
    except Exception:
        pass
    return results


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

    def add_current_app_to_whitelist(self) -> bool:
        """确保当前运行的 Python 进程或打包的可执行文件在白名单中"""
        curr_exe = sys.executable
        whitelist = self.get_whitelist()
        norm_curr = os.path.normpath(curr_exe).lower()
        already = any(os.path.normpath(p).lower() == norm_curr for p in whitelist)
        if not already:
            whitelist.append(curr_exe)
            return self.set_whitelist(whitelist)
        return True

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

    def cloak_controller(self, vendor: Optional[int], product: Optional[int]) -> Tuple[bool, str]:
        """
        对特定控制器执行一键独占隐身接管：
        1. 确保当前主程序在白名单中
        2. 查找该控制器的物理实例 ID
        3. 添加至黑名单并激活驱动
        """
        if not self.is_driver_installed():
            return False, "未检测到 HidHide 驱动，请先安装驱动"

        # 1. 确保自身在白名单
        if not self.add_current_app_to_whitelist():
            return False, "写入白名单失败，可能需要以管理员身份运行"

        # 2. 匹配硬件 ID
        instances = find_hid_instances(vendor, product)
        if not instances:
            return False, f"未在系统中找到 VID:{vendor:04X} PID:{product:04X} 对应的 HID 实例"

        blacklist = self.get_blacklist()
        changed = False
        for inst in instances:
            norm_inst = inst.upper()
            if not any(b.upper() == norm_inst for b in blacklist):
                blacklist.append(inst)
                changed = True

        if changed:
            if not self.set_blacklist(blacklist):
                return False, "写入黑名单失败，请检查驱动权限"

        # 3. 激活全局隐身
        self.set_active(True)
        return True, f"已成功将 {len(instances)} 个硬件节点屏蔽，仅向本程序独占暴露"

    def uncloak_controller(self, vendor: Optional[int], product: Optional[int]) -> Tuple[bool, str]:
        """解除特定控制器的隐身屏蔽，恢复系统共享访问"""
        if not self.is_driver_installed():
            return False, "未检测到 HidHide 驱动"

        instances = find_hid_instances(vendor, product)
        if not instances:
            return True, "无匹配硬件实例"

        blacklist = self.get_blacklist()
        norm_targets = {inst.upper() for inst in instances}
        new_blacklist = [b for b in blacklist if b.upper() not in norm_targets]

        if len(new_blacklist) != len(blacklist):
            self.set_blacklist(new_blacklist)

        return True, "已解除控制器隐身，系统已恢复共享访问"

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

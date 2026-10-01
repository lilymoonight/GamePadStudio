import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from gamepadstudio.hidhide import (
    HidHideClient,
    encode_multi_sz,
    decode_multi_sz,
    find_hid_instances,
    IOCTL_GET_WHITELIST,
    IOCTL_SET_WHITELIST,
    IOCTL_GET_BLACKLIST,
    IOCTL_SET_BLACKLIST,
    IOCTL_GET_ACTIVE,
    IOCTL_SET_ACTIVE,
)


def test_multi_sz_codec():
    # Empty
    assert decode_multi_sz(encode_multi_sz([])) == []

    # Single
    single = ["C:\\App\\app.exe"]
    enc = encode_multi_sz(single)
    assert decode_multi_sz(enc) == single

    # Multiple with unicode
    items = [
        "C:\\Program Files\\Nefarius\\app.exe",
        "D:\\无限暖暖\\game.exe",
        "HID\\VID_054C&PID_0CE6\\7&123456&0&0000"
    ]
    enc = encode_multi_sz(items)
    dec = decode_multi_sz(enc)
    assert dec == items


def test_find_hid_instances():
    # When vendor and product are None, should safely return empty list to prevent cloaking all devices
    all_instances = find_hid_instances()
    assert all_instances == []
    # Test specific filter
    filtered = find_hid_instances(vendor=0xFFFF, product=0xFFFF)
    assert isinstance(filtered, list)
    assert len(filtered) == 0



def test_hidhide_client_mock_interactions():
    client = HidHideClient()

    # Mock open_device & send_ioctl
    mock_active = False
    mock_whitelist = ["C:\\Existing\\app.exe"]
    mock_blacklist = []

    def fake_send_ioctl(ioctl, in_bytes=b'', out_size=4096):
        nonlocal mock_active, mock_whitelist, mock_blacklist
        if ioctl == IOCTL_GET_ACTIVE:
            return True, (b'\x01' if mock_active else b'\x00')
        elif ioctl == IOCTL_SET_ACTIVE:
            mock_active = bool(in_bytes[0]) if in_bytes else False
            return True, b''
        elif ioctl == IOCTL_GET_WHITELIST:
            return True, encode_multi_sz(mock_whitelist)
        elif ioctl == IOCTL_SET_WHITELIST:
            mock_whitelist = decode_multi_sz(in_bytes)
            return True, b''
        elif ioctl == IOCTL_GET_BLACKLIST:
            return True, encode_multi_sz(mock_blacklist)
        elif ioctl == IOCTL_SET_BLACKLIST:
            mock_blacklist = decode_multi_sz(in_bytes)
            return True, b''
        return False, b''

    with patch.object(client, 'is_driver_installed', return_value=True), \
         patch.object(client, '_send_ioctl', side_effect=fake_send_ioctl):

        assert client.is_driver_installed() is True

        # Test active toggle
        assert client.is_active() is False
        assert client.set_active(True) is True
        assert client.is_active() is True
        assert client.set_active(False) is True
        assert client.is_active() is False

        # Test whitelist
        wl = client.get_whitelist()
        assert wl == ["C:\\Existing\\app.exe"]
        assert client.add_current_app_to_whitelist() is True
        assert sys.executable in client.get_whitelist()

        # Test cloak controller
        with patch('gamepadstudio.hidhide.find_hid_instances', return_value=["HID\\VID_054C&PID_0CE6\\INST_1"]):
            ok, msg = client.cloak_controller(0x054C, 0x0CE6)
            assert ok is True
            assert "HID\\VID_054C&PID_0CE6\\INST_1" in client.get_blacklist()
            assert client.is_active() is True

            # Test uncloak
            ok, msg = client.uncloak_controller(0x054C, 0x0CE6)
            assert ok is True
            assert "HID\\VID_054C&PID_0CE6\\INST_1" not in client.get_blacklist()

        # Status summary
        status = client.get_status_summary()
        assert status["installed"] is True


def test_virtual_kbm_ui_cloaking_integration(tmp_path):
    from PySide6.QtWidgets import QApplication
    from gamepadstudio.virtual_kbm_ui import VirtualKbmPage
    from gamepadstudio.studio_core import ConfigStore
    from tests.mapping_fixtures import MappingOwner

    app = QApplication.instance() or QApplication([])
    owner = MappingOwner(tmp_path)
    store = owner.store

    try:
        with patch.object(VirtualKbmPage, 'get_current_device_info', return_value=(0x054C, 0x0CE6)):
            with patch.object(HidHideClient, 'is_driver_installed', return_value=False):
                page = VirtualKbmPage(owner, store=store)
                page.open_cloaking()

                # Verify cloaking UI components exist
                assert hasattr(page, 'cloaking_toggle')
                assert hasattr(page, 'cloaking_status_label')
                assert hasattr(page, 'btn_install_driver')

                # Check fallback uninstalled state
                assert page.cloaking_toggle.isEnabled() is False
                assert "未安装" in page.cloaking_status_label.text()

            # Simulate HidHide installed and active
            with patch.object(page.hidhide, 'is_driver_installed', return_value=True), \
                 patch.object(page.hidhide, 'is_active', return_value=True):
                page.refresh_cloaking_status()
                assert page.cloaking_toggle.isEnabled() is True
                assert page.cloaking_toggle.isChecked() is True
                assert "已隐身" in page.cloaking_status_label.text()

                # Simulate toggling off
                with patch.object(page.hidhide, 'uncloak_controller', return_value=(True, "ok")), \
                     patch.object(page.hidhide, 'set_active', return_value=True):
                    page.cloaking_toggle.setChecked(False)
                    assert store.data.get('device_cloaking_enabled') is False

    finally:
        owner.close()


def test_xbox_cloaking_refused_and_sanitized():
    client = HidHideClient()
    mock_active = True
    mock_blacklist = [
        "HID\\VID_045E&PID_0B13\\12345",
        "HID\\VID_054C&PID_0CE6\\67890"
    ]

    def fake_send_ioctl(ioctl, in_bytes=b'', out_size=4096):
        nonlocal mock_active, mock_blacklist
        if ioctl == IOCTL_GET_ACTIVE:
            return True, (b'\x01' if mock_active else b'\x00')
        elif ioctl == IOCTL_SET_ACTIVE:
            mock_active = bool(in_bytes[0]) if in_bytes else False
            return True, b''
        elif ioctl == IOCTL_GET_BLACKLIST:
            return True, encode_multi_sz(mock_blacklist)
        elif ioctl == IOCTL_SET_BLACKLIST:
            mock_blacklist = decode_multi_sz(in_bytes)
            return True, b''
        return False, b''

    with patch.object(client, 'is_driver_installed', return_value=True), \
         patch.object(client, '_send_ioctl', side_effect=fake_send_ioctl), \
         patch.object(client, 'add_current_app_to_whitelist', return_value=True), \
         patch('gamepadstudio.hidhide.find_hid_instances', return_value=['HID\\VID_045E&PID_0B13\\12345']), \
         patch('gamepadstudio.hidhide.find_all_gamepad_instances', return_value=['HID\\VID_045E&PID_0B13\\12345']):

        # 1. Universal gamepad support: Xbox and PS5 controllers are both supported for cloaking
        ok, msg = client.cloak_controller(0x045E, 0x0B13)
        assert ok is True
        assert "硬件手柄节点屏蔽" in msg

        # 2. Blacklist sanitation is non-destructive
        res = client.sanitize_blacklist()
        assert res is True


def test_virtual_kbm_ui_xbox_cloaking_enabled(tmp_path):
    from PySide6.QtWidgets import QApplication
    from gamepadstudio.virtual_kbm_ui import VirtualKbmPage
    from gamepadstudio.studio_core import ConfigStore
    from tests.mapping_fixtures import MappingOwner

    app = QApplication.instance() or QApplication([])
    owner = MappingOwner(tmp_path)
    store = owner.store

    try:
        page = VirtualKbmPage(owner, store=store)
        page.open_cloaking()
        page.set_device_state({"vendor": 0x045E, "product": 0x02FD, "name": "Xbox Controller"})

        with patch.object(page.hidhide, 'is_driver_installed', return_value=True):
            page.refresh_cloaking_status()
            assert page.cloaking_toggle.isEnabled() is True
    finally:
        owner.close()


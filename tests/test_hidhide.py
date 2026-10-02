import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
import pytest
from gamepadstudio import hidhide

from gamepadstudio.hidhide import (
    HidHideClient,
    ensure_current_app_input_access,
    encode_multi_sz,
    decode_multi_sz,
    find_hid_instances,
    selected_device_instances,
    IOCTL_GET_WHITELIST,
    IOCTL_SET_WHITELIST,
    IOCTL_GET_BLACKLIST,
    IOCTL_SET_BLACKLIST,
    IOCTL_GET_ACTIVE,
    IOCTL_SET_ACTIVE,
)


@pytest.fixture(autouse=True)
def block_native_hidhide_access(monkeypatch):
    """These tests never inspect or change the host's real driver state."""
    import gamepadstudio.hidhide as module
    # Exercise Windows policy with local stand-ins; never change Python's host
    # platform or allow a fixture to load/query the real driver.
    windows_sys = SimpleNamespace(**vars(sys))
    windows_sys.platform = 'win32'
    monkeypatch.setattr(module, 'sys', windows_sys)
    monkeypatch.setattr(HidHideClient, 'is_driver_installed', lambda self: False)
    monkeypatch.setattr(HidHideClient, '_send_ioctl', lambda self, *args, **kwargs: (False, b''))

    def no_registry(*args, **kwargs):
        raise OSError('Registry access is isolated in tests')

    monkeypatch.setattr(module, 'winreg', SimpleNamespace(
        OpenKey=no_registry, HKEY_LOCAL_MACHINE='HKLM'))
    native_ctypes = SimpleNamespace(**vars(module.C))
    native_ctypes.WinDLL = no_registry
    native_ctypes.windll = SimpleNamespace(kernel32=SimpleNamespace(QueryDosDeviceW=no_registry))
    monkeypatch.setattr(module, 'C', native_ctypes)
    from gamepadstudio import virtual_kbm_ui
    monkeypatch.setattr(virtual_kbm_ui, 'WINDOWS_FEATURES', True)
    original_exists = module.os.path.exists
    cli = r'C:\Program Files\Nefarius Software Solutions\HidHide\x64\HidHideCLI.exe'
    monkeypatch.setattr(module.os.path, 'exists', lambda path: False if str(path) == cli else original_exists(path))


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


def bootstrap_driver(monkeypatch, active=True, whitelist=()):
    client = HidHideClient()
    state = {'whitelist': list(whitelist), 'writes': [], 'reads': []}
    monkeypatch.setattr(client, 'is_driver_installed', lambda: True)

    def send_ioctl(code, in_bytes=b'', out_size=4096):
        if code == IOCTL_GET_ACTIVE:
            state['reads'].append(code)
            return True, bytes([active])
        if code == IOCTL_GET_WHITELIST:
            state['reads'].append(code)
            return True, encode_multi_sz(state['whitelist'])
        assert code == IOCTL_SET_WHITELIST, 'Bootstrap may only append application access'
        state['writes'].append(code)
        state['whitelist'] = decode_multi_sz(in_bytes)
        return True, b''

    monkeypatch.setattr(client, '_send_ioctl', send_ioctl)
    return client, state


def test_bootstrap_active_hidhide_preserves_entries_and_adds_only_current_executable(monkeypatch):
    current = r'D:\GamePadStudio\GamePadStudio.exe'
    nt_current = r'\Device\HarddiskVolume5\GamePadStudio\GamePadStudio.exe'
    existing = [r'\Device\HarddiskVolume4\Other App\app.exe', r'C:\Existing\tool.exe']
    client, state = bootstrap_driver(monkeypatch, whitelist=existing)
    monkeypatch.setattr(hidhide.sys, 'executable', current)
    monkeypatch.setattr(hidhide.sys, '_base_executable', r'C:\Python\python.exe')
    monkeypatch.setattr('gamepadstudio.hidhide.dos_to_nt_path', lambda path: nt_current)
    ok, message = ensure_current_app_input_access(client)
    assert ok and not message
    assert state['whitelist'] == existing + [current, nt_current]
    assert state['writes'] == [IOCTL_SET_WHITELIST]


def test_bootstrap_inactive_hidhide_only_reads_activity(monkeypatch):
    client, state = bootstrap_driver(monkeypatch, active=False)
    assert ensure_current_app_input_access(client) == (True, '')
    assert state['reads'] == [IOCTL_GET_ACTIVE]
    assert not state['writes']


def test_bootstrap_without_driver_does_not_query_or_write_it(monkeypatch):
    client = HidHideClient()
    monkeypatch.setattr(client, '_send_ioctl', lambda *args, **kwargs: pytest.fail('No driver access expected'))
    assert ensure_current_app_input_access(client) == (True, '')


def test_bootstrap_existing_access_is_idempotent_and_preserves_other_entries(monkeypatch):
    current = r'D:\GamePadStudio\GamePadStudio.exe'
    existing = [r'C:\Other\app.exe', current.upper()]
    client, state = bootstrap_driver(monkeypatch, whitelist=existing)
    monkeypatch.setattr(hidhide.sys, 'executable', current)
    monkeypatch.setattr('gamepadstudio.hidhide.dos_to_nt_path', lambda path: path)
    assert ensure_current_app_input_access(client) == (True, '')
    assert state['whitelist'] == existing and not state['writes']


@pytest.mark.parametrize('code', [IOCTL_GET_ACTIVE, IOCTL_GET_WHITELIST])
def test_bootstrap_read_failure_never_replaces_access_list(monkeypatch, code):
    client, state = bootstrap_driver(monkeypatch, whitelist=[r'C:\Other\app.exe'])
    original = client._send_ioctl
    monkeypatch.setattr(client, '_send_ioctl',
                        lambda query, **kwargs: (False, b'') if query == code else original(query, **kwargs))
    ok, warning = ensure_current_app_input_access(client)
    assert not ok and warning
    assert state['whitelist'] == [r'C:\Other\app.exe'] and not state['writes']


@pytest.mark.parametrize('payload', [b'', b'\x00', b'\x00\x00', b'\x61\x00',
                                    b'\x00\xd8\x00\x00\x00\x00'])
def test_bootstrap_invalid_or_truncated_list_never_writes(monkeypatch, payload):
    client, state = bootstrap_driver(monkeypatch)
    original = client._send_ioctl
    monkeypatch.setattr(client, '_send_ioctl',
                        lambda query, **kwargs: (True, payload) if query == IOCTL_GET_WHITELIST else original(query, **kwargs))
    ok, warning = ensure_current_app_input_access(client)
    assert not ok and warning and not state['writes']


def test_regular_app_registration_also_refuses_failed_whitelist_read(monkeypatch):
    client = HidHideClient()
    writes = []
    monkeypatch.setattr(client, '_send_ioctl', lambda *args, **kwargs: (False, b''))
    monkeypatch.setattr(client, 'set_whitelist', lambda value: writes.append(value) or True)
    assert client.add_current_app_to_whitelist() is False
    assert not writes


def test_bootstrap_write_failure_returns_warning_without_changing_hiding(monkeypatch):
    client, state = bootstrap_driver(monkeypatch, whitelist=[r'C:\Other\app.exe'])
    monkeypatch.setattr(client, 'set_whitelist', lambda value: False)
    ok, warning = ensure_current_app_input_access(client)
    assert not ok and warning and not state['writes']


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


def test_virtual_kbm_ui_cloaking_integration(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from gamepadstudio.virtual_kbm_ui import VirtualKbmPage
    from gamepadstudio.studio_core import ConfigStore
    from tests.mapping_fixtures import MappingOwner

    app = QApplication.instance() or QApplication([])
    owner = MappingOwner(tmp_path)
    store = owner.store
    instance = r'HID\VID_054C&PID_0CE6\INST_1'
    owner.snapshot = dict(device_key='ps:first', family='dualsense', vendor=0x054C, product=0x0CE6)

    def setting(key, value):
        store.set_setting(key, value, owner.snapshot)
        store.save()

    monkeypatch.setattr(owner, 'setting', setting)
    hidden = [instance]

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
                 patch.object(page.hidhide, 'is_active', return_value=True), \
                 patch.object(page.hidhide, 'get_blacklist', side_effect=lambda: hidden[:]), \
                 patch('gamepadstudio.hidhide.find_hid_instances', return_value=[instance]):
                page.refresh_cloaking_status()
                assert page.cloaking_toggle.isEnabled() is True
                assert page.cloaking_toggle.isChecked() is True
                assert "已隐身" in page.cloaking_status_label.text()

                # Simulate toggling off
                def uncloak(vendor, product):
                    hidden.clear()
                    return True, 'ok'

                with patch.object(page.hidhide, 'uncloak_controller', side_effect=uncloak):
                    page.cloaking_toggle.setChecked(False)
                    assert store.settings_for(owner.snapshot)['device_cloaking_enabled'] is False
                    assert not page.cloaking_toggle.isChecked()

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
        owner.snapshot = dict(device_key='xbox:first', family='xbox', vendor=0x045E,
                              product=0x02FD, name='Xbox Controller')
        page.set_device_state(owner.snapshot)

        with patch.object(page.hidhide, 'is_driver_installed', return_value=True), \
             patch('gamepadstudio.hidhide.find_hid_instances', return_value=[r'HID\VID_045E&PID_02FD\FIRST']):
            page.refresh_cloaking_status()
            assert page.cloaking_toggle.isEnabled() is True
    finally:
        owner.close()


FIRST_PAD = r'HID\VID_054C&PID_0CE6\FIRST'
SECOND_PAD = r'HID\VID_054C&PID_0CE6\SECOND'
FIRST_PATH = r'\\?\hid#vid_054c&pid_0ce6#first#{4d1e55b2-f16f-11cf-88cb-001111000030}'
SECOND_PATH = r'\\?\hid#vid_054c&pid_0ce6#second#{4d1e55b2-f16f-11cf-88cb-001111000030}'


def isolated_driver(monkeypatch, blacklist=()):
    """Keep the real filtering code, but put all driver data in memory."""
    client = HidHideClient()
    hardware = {'blacklist': list(blacklist), 'active': bool(blacklist), 'writes': []}

    def set_blacklist(values):
        hardware['blacklist'] = list(values)
        hardware['writes'].append(('blacklist', list(values)))
        return True

    def set_active(value):
        hardware['active'] = bool(value)
        hardware['writes'].append(('active', bool(value)))
        return True

    monkeypatch.setattr(client, 'is_driver_installed', lambda: True)
    monkeypatch.setattr(client, 'get_blacklist', lambda: hardware['blacklist'][:])
    monkeypatch.setattr(client, 'set_blacklist', set_blacklist)
    monkeypatch.setattr(client, 'is_active', lambda: hardware['active'])
    monkeypatch.setattr(client, 'set_active', set_active)
    monkeypatch.setattr(client, 'add_current_app_to_whitelist', lambda: True)
    monkeypatch.setattr('gamepadstudio.hidhide.find_hid_instances', lambda *args: [FIRST_PAD, SECOND_PAD])
    monkeypatch.setattr('gamepadstudio.hidhide.find_all_gamepad_instances',
                        lambda: pytest.fail('A selected-device operation must not enumerate other controllers'))
    return client, hardware


def test_selected_instances_normalize_physical_path_and_reject_ambiguous_models(monkeypatch):
    monkeypatch.setattr('gamepadstudio.hidhide.find_hid_instances', lambda *args: [FIRST_PAD, SECOND_PAD])
    assert selected_device_instances(0x054C, 0x0CE6, FIRST_PATH) == [FIRST_PAD]
    assert selected_device_instances(0x054C, 0x0CE6, SECOND_PATH) == [SECOND_PAD]
    assert selected_device_instances(0x054C, 0x0CE6) == []
    assert selected_device_instances(0x054C, 0x0CE6, FIRST_PATH.replace('first', 'unknown')) == []
    assert selected_device_instances(None, None, FIRST_PATH) == []
    monkeypatch.setattr('gamepadstudio.hidhide.find_hid_instances', lambda *args: [FIRST_PAD])
    assert selected_device_instances(0x054C, 0x0CE6) == [FIRST_PAD]


def test_cloak_only_selected_physical_pad_from_two_identical_models(monkeypatch):
    client, hardware = isolated_driver(monkeypatch)
    ok, message = client.cloak_controller(0x054C, 0x0CE6, device_path=FIRST_PATH)
    assert ok and hardware['blacklist'] == [FIRST_PAD]
    assert hardware['active']
    assert SECOND_PAD not in hardware['blacklist']


def test_uncloak_selected_pad_preserves_other_hidden_pad_and_driver_activity(monkeypatch):
    client, hardware = isolated_driver(monkeypatch, [FIRST_PAD, SECOND_PAD])
    ok, message = client.uncloak_controller(0x054C, 0x0CE6, device_path=FIRST_PATH)
    assert ok and hardware['blacklist'] == [SECOND_PAD]
    assert hardware['active']
    ok, message = client.uncloak_controller(0x054C, 0x0CE6, device_path=SECOND_PATH)
    assert ok and hardware['blacklist'] == []
    assert not hardware['active']


@pytest.mark.parametrize('path', [None, FIRST_PATH.replace('first', 'unknown')])
@pytest.mark.parametrize('method', ['cloak_controller', 'uncloak_controller'])
def test_unmatched_or_ambiguous_device_cannot_change_driver_state(monkeypatch, path, method):
    client, hardware = isolated_driver(monkeypatch, [SECOND_PAD])
    ok, message = getattr(client, method)(0x054C, 0x0CE6, device_path=path)
    assert not ok and '唯一匹配' in message
    assert hardware['blacklist'] == [SECOND_PAD] and hardware['active']
    assert hardware['writes'] == []


def test_cloaking_ui_shows_selected_pad_status_instead_of_global_active(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from gamepadstudio.virtual_kbm_ui import VirtualKbmPage
    from tests.mapping_fixtures import MappingOwner
    app = QApplication.instance() or QApplication([])
    owner = MappingOwner(tmp_path)
    first = dict(device_key='ps:first', family='dualsense', vendor=0x054C,
                 product=0x0CE6, device_path=FIRST_PATH)
    second = {**first, 'device_key': 'ps:second', 'device_path': SECOND_PATH}
    owner.snapshot = first
    monkeypatch.setattr('gamepadstudio.hidhide.find_hid_instances', lambda *args: [FIRST_PAD, SECOND_PAD])
    monkeypatch.setattr(HidHideClient, 'is_driver_installed', lambda self: True)
    monkeypatch.setattr(HidHideClient, 'is_active', lambda self: True)
    monkeypatch.setattr(HidHideClient, 'get_blacklist', lambda self: [FIRST_PAD])
    try:
        page = VirtualKbmPage(owner, store=owner.store)
        page.open_cloaking()
        assert page.cloaking_toggle.isChecked()
        assert '已隐身' in page.cloaking_status_label.text()
        owner.snapshot = second
        page.refresh_cloaking_status()
        assert not page.cloaking_toggle.isChecked()
        assert '原始输入可见' in page.cloaking_status_label.text()
    finally:
        owner.close()


def test_cloaking_ui_without_device_disables_control_and_ignores_toggle(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from gamepadstudio.virtual_kbm_ui import VirtualKbmPage
    from tests.mapping_fixtures import MappingOwner
    app = QApplication.instance() or QApplication([])
    owner = MappingOwner(tmp_path)
    monkeypatch.setattr(HidHideClient, 'is_driver_installed', lambda self: True)
    cloak = MagicMock()
    uncloak = MagicMock()
    monkeypatch.setattr(HidHideClient, 'cloak_controller', cloak)
    monkeypatch.setattr(HidHideClient, 'uncloak_controller', uncloak)
    try:
        page = VirtualKbmPage(owner, store=owner.store)
        page.open_cloaking()
        assert not page.cloaking_toggle.isEnabled()
        assert '未连接设备' in page.cloaking_status_label.text()
        page.cloaking_toggle.setChecked(True)
        assert not page.cloaking_toggle.isChecked()
        cloak.assert_not_called()
        uncloak.assert_not_called()
        assert owner.store.settings_for(None)['device_cloaking_enabled']
    finally:
        owner.close()

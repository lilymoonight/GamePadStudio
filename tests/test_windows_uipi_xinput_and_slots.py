"""Tests for Windows UIPI kernel XInput fallback, XInput Slot 0 reservation/reclamation,
and universal USB XnaComposite/XboxComposite HidHide device selection.
"""
from __future__ import annotations

import ctypes as C
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gamepadstudio.application_profiles import uipi_elevation_warning
from gamepadstudio.device import Device, _XInputStateExStruct
from gamepadstudio.hidhide import find_hid_instances, selected_device_instances
from gamepadstudio.mapping_engine import MappingRuntime
from gamepadstudio.studio_core import ConfigStore
from gamepadstudio.virtual_xinput import (
    VIGEM_ERROR_NONE,
    VirtualXInputPad,
    _ACTIVE_VIRTUAL_PADS,
    _ACTIVE_VIRTUAL_XINPUT_SLOTS,
    _VIGEM_HID_PATH_CACHE,
    active_virtual_xinput_slots,
    compose_xusb_report,
    is_vigembus_virtual_hid_path,
)


class _FakeViGEmLib:
    """In-memory ViGEmClient mock assigning lowest free XInput user index via notification."""

    def __init__(self, occupied_slots: set[int] | None = None):
        self.occupied: set[int] = set(occupied_slots or ())
        self._next_handle = 100
        self._target_slots: dict[int, int] = {}
        self._callbacks: dict[int, object] = {}
        self.register_calls = 0
        self.last_reports: dict[int, tuple[int, int, int]] = {}

    def vigem_alloc(self):
        self._next_handle += 1
        return self._next_handle

    def vigem_free(self, _client):
        return None

    def vigem_connect(self, _client):
        return VIGEM_ERROR_NONE

    def vigem_disconnect(self, _client):
        return None

    def vigem_target_x360_alloc(self):
        self._next_handle += 1
        return self._next_handle

    def vigem_target_free(self, _target):
        return None

    def vigem_target_add(self, _client, target):
        for idx in range(4):
            if idx not in self.occupied:
                self.occupied.add(idx)
                self._target_slots[target] = idx
                return VIGEM_ERROR_NONE
        return 0xE0000001

    def vigem_target_remove(self, _client, target):
        slot = self._target_slots.pop(target, None)
        if slot is not None:
            self.occupied.discard(slot)
        self._callbacks.pop(target, None)
        return VIGEM_ERROR_NONE

    def vigem_target_x360_register_notification(self, client, target, cb, userdata):
        self.register_calls += 1
        if target in self._callbacks:
            # Real ViGEmClient returns VIGEM_ERROR_CALLBACK_ALREADY_REGISTERED (0xE0000010)
            return 0xE0000010
        self._callbacks[target] = cb
        slot = self._target_slots.get(target, 0)
        cb(client, target, 0, 0, slot, userdata)
        return VIGEM_ERROR_NONE

    def vigem_target_x360_unregister_notification(self, target):
        self._callbacks.pop(target, None)

    def vigem_target_x360_update(self, _client, target, report):
        self.last_reports[target] = (
            int(report.wButtons),
            int(report.bLeftTrigger),
            int(report.bRightTrigger),
        )
        return VIGEM_ERROR_NONE

    def send_rumble(self, target, large: int, small: int):
        cb = self._callbacks.get(target)
        slot = self._target_slots.get(target, 0)
        if cb is not None:
            cb(1, target, large, small, slot, None)


def test_virtual_xinput_tracks_user_index_and_reclaims_slot_zero():
    # Physical controller initially occupies Slot 0 -> VirtualXInputPad gets Slot 1
    fake_lib = _FakeViGEmLib(occupied_slots={0})
    pad = VirtualXInputPad(lib=fake_lib)
    try:
        assert pad.connect() is True
        assert pad.user_index == 1
        assert 1 in active_virtual_xinput_slots()
        assert fake_lib.register_calls == 1

        # Registering rumble callback AFTER connect() reuses existing notification without double-registering
        rumbles: list[tuple[float, float]] = []
        assert pad.register_rumble_callback(lambda l_m, s_m: rumbles.append((l_m, s_m))) is True
        assert fake_lib.register_calls == 1
        fake_lib.send_rumble(pad._target, 255, 128)
        assert len(rumbles) == 1
        assert rumbles[0][0] == pytest.approx(1.0)
        assert rumbles[0][1] == pytest.approx(128 / 255.0)

        # Physical controller disconnects (Slot 0 becomes free) -> reclaim_slot_zero moves VirtualXInputPad 1 -> 0
        fake_lib.occupied.discard(0)
        assert pad.reclaim_slot_zero() is True
        assert pad.user_index == 0
        assert active_virtual_xinput_slots() == {0}

        # Calling reclaim_slot_zero when already on Slot 0 does not reconnect
        prev_target = pad._target
        assert pad.reclaim_slot_zero() is True
        assert pad._target == prev_target
    finally:
        pad.close()
        assert pad.user_index is None
        assert active_virtual_xinput_slots() == set()


def test_xinput_kernel_fallback_bypasses_uipi_and_excludes_virtual_slot():
    dev = Device.__new__(Device)
    dev.metadata = {
        'family': 'xbox',
        'vendor': 0x045E,
        'product': 0x0B13,
        'name': 'Xbox Series X Controller',
        'device_path': r'\\?\HID#{00001812-0000-1000-8000-00805f9b34fb}&Dev&VID_045e&PID_0b13&IG_00#9&24d70bae&0&0000',
        'profile_key': 'xbox:045e:0b13:0300509d5e040000130b000020057200',
    }
    dev._matched_xinput_slot = None
    dev._uipi_cache_until = 0.0
    dev._uipi_cached_blocked = False

    # Slot 0 is our VirtualXInputPad emitting B (0x2000); Slot 1 is physical Xbox holding A (0x1000) + Guide (0x0400)
    def fake_xinput_get_state(slot: int, state_ptr):
        st = C.cast(state_ptr, C.POINTER(_XInputStateExStruct)).contents
        if slot == 0:
            st.dwPacketNumber = 10
            st.Gamepad.wButtons = 0x2000  # Virtual B
            return 0
        if slot == 1:
            st.dwPacketNumber = 42
            st.Gamepad.wButtons = 0x1000 | 0x0400 | 0x0001  # Physical A + Guide + D-Pad Up
            st.Gamepad.bLeftTrigger = 0
            st.Gamepad.bRightTrigger = 255
            st.Gamepad.sThumbLX = -32768
            st.Gamepad.sThumbLY = 32767   # XInput UP (+32767) -> SDL UP (-1.0)
            st.Gamepad.sThumbRX = 0
            st.Gamepad.sThumbRY = -32768  # XInput DOWN (-32768) -> SDL DOWN (+1.0)
            return 0
        return 1167  # ERROR_DEVICE_NOT_CONNECTED

    res = dev._read_xinput_kernel_fallback(
        sdl_buttons=[],
        xinput_fn=fake_xinput_get_state,
        virtual_slots={0},
    )
    assert res is not None
    btns, axes = res
    assert btns == [0, 5, 11]  # A (0), Guide (5), D-Pad Up (11) — Virtual B (1) strictly excluded!
    assert axes[0] == pytest.approx(-1.0)
    assert axes[1] == pytest.approx(-1.0)
    assert axes[3] == pytest.approx(1.0)
    assert axes[4] == pytest.approx(0.0)
    assert axes[5] == pytest.approx(1.0)

    # Round-trip axis check through compose_xusb_report preserves exact stick orientation
    _, snap = compose_xusb_report(['1'], axes)
    assert snap['wButtons'] == 0x2000
    assert snap['sThumbLX'] == -32767
    assert snap['sThumbLY'] == 32767
    assert snap['sThumbRY'] == -32767
    assert snap['bRightTrigger'] == 255


def test_end_to_end_ab_swap_under_uipi_elevated_game_block(tmp_path):
    """Simulate tonight's exact Infinity Nikki scenario:
    - Physical Xbox BLE controller (045E:0B13) with profile 'AexchangeB' (0->1, 1->0)
    - Elevated game focused -> SDL RAWINPUT returns empty [] buttons
    - Kernel XInput fallback reads physical A (0) on Slot 1 while VirtualXInputPad owns Slot 0
    - MappingRuntime emits swapped B (0x2000) on VirtualXInputPad without feedback loop!
    """
    store = ConfigStore(tmp_path)
    xbox_state = {
        'instance_id': 1,
        'device_key': 'xbox:045e:0b13:test',
        'profile_key': 'xbox:045e:0b13:0300509d5e040000130b000020057200',
        'family': 'xbox',
        'vendor': 0x045E,
        'product': 0x0B13,
        'name': 'Xbox Series X Controller',
        'device_path': r'\\?\HID#{00001812-0000-1000-8000-00805f9b34fb}&Dev&VID_045e&PID_0b13&IG_00#9&24d70bae&0&0000',
        'available_buttons': list(range(16)),
        'available_axes': list(range(6)),
        'buttons': [],
        'axes': [0.0] * 6,
    }
    store.activate_controller(xbox_state)
    base_prof = store.data['active_profile']
    store.apply_mapping_change({'op': 'create', 'profile': 'AexchangeB', 'mode': 'gamepad', 'source': base_prof}, xbox_state)
    store.apply_mapping_change({'op': 'swap_ab_xy', 'profile': 'AexchangeB', 'pairs': [['0', '1']]}, xbox_state)

    fake_lib = _FakeViGEmLib(occupied_slots=set())
    vpad = VirtualXInputPad(lib=fake_lib)
    assert vpad.connect() is True
    assert vpad.user_index == 0

    class _HeadlessActions:
        def __init__(self, pad):
            self._virtual_pad = pad
            self.held_gamepad_buttons = set()

        def gamepad_button(self, button, down):
            if down:
                self.held_gamepad_buttons.add(str(button))
            else:
                self.held_gamepad_buttons.discard(str(button))

        def sync_virtual_gamepad(self, buttons=(), axes=()):
            vpad.emit_frame(buttons=buttons, axes=axes)

        def release_all(self):
            self.held_gamepad_buttons.clear()
            vpad.reset()

    dev = Device.__new__(Device)
    dev.lib = C.CDLL('kernel32') if sys.platform == 'win32' else SimpleNamespace()
    dev.metadata = dict(xbox_state)
    dev._matched_xinput_slot = None

    physical_wbuttons = 0x1000  # User presses physical A in Infinity Nikki

    def fake_xinput(slot: int, state_ptr):
        st = C.cast(state_ptr, C.POINTER(_XInputStateExStruct)).contents
        if slot == 0:
            # Slot 0 is VirtualXInputPad!
            st.Gamepad.wButtons = vpad.last_report['wButtons']
            return 0
        if slot == 1:
            st.Gamepad.wButtons = physical_wbuttons
            return 0
        return 1167

    actions = _HeadlessActions(vpad)
    runtime = MappingRuntime(actions, lambda *_: None, start_mouse=False)
    try:
        with patch('gamepadstudio.device.sys', SimpleNamespace(platform='win32')), \
             patch.object(dev, '_is_uipi_blocking_rawinput', return_value=True), \
             patch('gamepadstudio.device._get_xinput_state_fn', return_value=fake_xinput):
            # Frame 1: SDL RAWINPUT is deafened ([]), kernel XInput rescues physical A (0) -> VirtualXInputPad emits B (0x2000)!
            btns, axes = dev._apply_windows_xinput_uipi_fallback([], [0.0] * 6)
            assert btns == [0]
            frame_state = dict(xbox_state, buttons=btns, axes=axes)
            runtime.update(frame_state, store.data, enabled=True, now=1.0)
            assert vpad.last_report['wButtons'] == 0x2000  # B emitted!

            # Frame 2: VirtualXInputPad now holds 0x2000 on Slot 0, while physical A is still held on Slot 1.
            # Ensure Slot 0 is ignored and no feedback loop turns B back into A!
            btns2, _ = dev._apply_windows_xinput_uipi_fallback([], [0.0] * 6)
            assert btns2 == [0]
            runtime.update(dict(xbox_state, buttons=btns2, axes=axes), store.data, enabled=True, now=1.016)
            assert vpad.last_report['wButtons'] == 0x2000

            # Frame 3: User releases A and presses physical B (0x2000) -> VirtualXInputPad emits A (0x1000)!
            physical_wbuttons = 0x2000
            btns3, _ = dev._apply_windows_xinput_uipi_fallback([], [0.0] * 6)
            assert btns3 == [1]
            runtime.update(dict(xbox_state, buttons=btns3, axes=axes), store.data, enabled=True, now=1.032)
            assert vpad.last_report['wButtons'] == 0x1000
    finally:
        runtime.reset()
        vpad.close()


def test_dualsense_does_not_enable_xinput_fallback():
    dev = Device.__new__(Device)
    dev.metadata = {
        'family': 'dualsense',
        'vendor': 0x054C,
        'product': 0x0CE6,
        'name': 'DualSense Wireless Controller',
        'device_path': r'\\?\HID#VID_054C&PID_0CE6#7&12345&0&0000',
        'profile_key': 'dualsense:054c:0ce6:030000004c050000e60c000000016800',
    }
    assert dev._is_xinput_capable_controller() is False


def test_hidhide_includes_usb_xnacomposite_and_excludes_usbccgp_and_vigembus(monkeypatch):
    """Verify wired USB XInput controllers cloak BOTH HIDClass and USB XnaComposite/XboxComposite nodes,
    while skipping composite hubs (usbccgp), other units, and ViGEmBus virtual USB X360 pads."""
    tree = {
        r'SYSTEM\CurrentControlSet\Enum\HID': {
            'VID_3537&PID_100E&IG_00': {
                '8&UNIT1_HID&0&0000': {
                    'Service': 'HidUsb',
                    'DeviceDesc': 'HID-compliant game controller',
                    'ContainerID': '{11111111-2222-3333-4444-555555555555}',
                },
                '8&UNIT2_HID&0&0000': {
                    'Service': 'HidUsb',
                    'DeviceDesc': 'HID-compliant game controller',
                    'ContainerID': '{aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee}',
                },
            },
            'VID_3537&PID_100E&MI_01&Col02': {
                '8&UNIT1_AUDIO&0&0001': {
                    'Service': 'HidUsb',
                    'DeviceDesc': 'HID-compliant headset',
                    'ContainerID': '{11111111-2222-3333-4444-555555555555}',
                },
            },
        },
        r'SYSTEM\CurrentControlSet\Enum\USB': {
            'VID_3537&PID_100E': {
                'SERIAL_UNIT1': {
                    'Service': 'usbccgp',
                    'ClassGUID': '{36fc9e60-c465-11cf-8056-444553540000}',
                    'ContainerID': '{11111111-2222-3333-4444-555555555555}',
                },
            },
            'VID_3537&PID_100E&MI_00': {
                '7&UNIT1_XUSB&0&0000': {
                    'Service': 'xusb22',
                    'ClassGUID': '{d61ca365-5af4-4486-998b-9db4734c6ca3}',  # XnaComposite
                    'ContainerID': '{11111111-2222-3333-4444-555555555555}',
                },
                '7&UNIT2_XUSB&0&0000': {
                    'Service': 'xusb22',
                    'ClassGUID': '{d61ca365-5af4-4486-998b-9db4734c6ca3}',
                    'ContainerID': '{aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee}',
                },
            },
            'VID_045E&PID_028E': {
                'VIGEM_01': {
                    'Service': 'xusb22',
                    'ClassGUID': '{d61ca365-5af4-4486-998b-9db4734c6ca3}',
                    'DeviceDesc': 'Virtual Gamepad Emulation Bus',
                    'FriendlyName': 'Xbox 360 Controller for Windows (ViGEm)',
                    'ContainerID': '{99999999-9999-9999-9999-999999999999}',
                },
            },
        },
    }

    class _FakeKey:
        def __init__(self, node):
            self.node = node

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    class _FakeWinReg:
        HKEY_LOCAL_MACHINE = 1
        KEY_READ = 1

        @staticmethod
        def OpenKey(_root, subkey, *_args):
            if isinstance(_root, _FakeKey):
                if subkey in _root.node and isinstance(_root.node[subkey], dict):
                    return _FakeKey(_root.node[subkey])
                raise OSError(subkey)
            parts = [p for p in str(subkey).split('\\') if p]
            # Match top-level Enum\HID or Enum\USB or nested paths
            for prefix_len in (4, 3):
                prefix = '\\'.join(parts[:prefix_len])
                if prefix in tree:
                    cur = tree[prefix]
                    for rem in parts[prefix_len:]:
                        found_k = next((k for k in cur if k.upper() == rem.upper()), None)
                        if found_k is None or not isinstance(cur[found_k], dict):
                            raise OSError(subkey)
                        cur = cur[found_k]
                    return _FakeKey(cur)
            raise OSError(subkey)

        @staticmethod
        def QueryInfoKey(key):
            subkeys = [k for k, v in key.node.items() if isinstance(v, dict)]
            return (len(subkeys), 0, 0)

        @staticmethod
        def EnumKey(key, index):
            subkeys = [k for k, v in key.node.items() if isinstance(v, dict)]
            return subkeys[index]

        @staticmethod
        def QueryValueEx(key, value_name):
            for k, v in key.node.items():
                if not isinstance(v, dict) and k.lower() == value_name.lower():
                    return (v, 1)
            raise OSError(value_name)

    _VIGEM_HID_PATH_CACHE.clear()
    monkeypatch.setattr('gamepadstudio.hidhide.sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr('gamepadstudio.hidhide.winreg', _FakeWinReg)
    monkeypatch.setattr('gamepadstudio.hidhide._get_present_hid_instance_ids', lambda: set())
    monkeypatch.setitem(sys.modules, 'winreg', _FakeWinReg)

    # 1. ViGEmBus USB X360 virtual node is detected when include_virtual=True and excluded when include_virtual=False
    assert is_vigembus_virtual_hid_path(r'USB\VID_045E&PID_028E\VIGEM_01') is True
    assert r'USB\VID_045E&PID_028E\VIGEM_01' in find_hid_instances(0x045E, 0x028E, include_virtual=True)
    assert r'USB\VID_045E&PID_028E\VIGEM_01' not in find_hid_instances(0x045E, 0x028E, include_virtual=False)

    # 2. Selecting Unit 1 returns BOTH Unit 1's HID node AND Unit 1's USB XnaComposite node,
    #    while excluding usbccgp parent, headset node, and Unit 2!
    sdl_path_unit1 = r'\\?\hid#vid_3537&pid_100e&ig_00#8&unit1_hid&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}'
    selected = selected_device_instances(0x3537, 0x100E, device_path=sdl_path_unit1)
    assert selected == [
        r'HID\VID_3537&PID_100E&IG_00\8&UNIT1_HID&0&0000',
        r'USB\VID_3537&PID_100E&MI_00\7&UNIT1_XUSB&0&0000',
    ]


def test_uipi_warning_suppressed_for_gamepad_mode_and_active_for_kbm():
    with patch('gamepadstudio.application_profiles.sys', SimpleNamespace(platform='win32')), patch(
        'gamepadstudio.application_profiles.is_process_elevated',
        side_effect=lambda pid=None: False if pid is None else True,
    ):
        fg = {'pid': 24100, 'executable': r'D:\InfinityNikki\X6Game-Win64-Shipping.exe'}
        assert uipi_elevation_warning(fg, mode='gamepad') == ''
        kbm_msg = uipi_elevation_warning(fg, mode='kbm')
        assert 'UIPI' in kbm_msg
        assert 'X6Game-Win64-Shipping.exe' in kbm_msg


def test_agent_holds_virtual_gamepad_and_reclaims_slot_zero_on_disconnect(tmp_path):
    from PySide6.QtCore import QCoreApplication
    from gamepadstudio.agent import Agent

    _app = QCoreApplication.instance() or QCoreApplication([])
    store = ConfigStore(tmp_path)
    xbox_state = {
        'instance_id': 7,
        'device_key': 'xbox:045e:0b13:slot_test',
        'profile_key': 'xbox:045e:0b13:0300509d5e040000130b000020057200',
        'family': 'xbox',
        'vendor': 0x045E,
        'product': 0x0B13,
        'name': 'Xbox Series X Controller',
        'available_buttons': list(range(16)),
        'available_axes': list(range(6)),
        'buttons': [],
        'axes': [0.0] * 6,
        'power': 3,
    }
    store.activate_controller(xbox_state)
    base_prof = store.data['active_profile']
    store.apply_mapping_change({'op': 'create', 'profile': 'AexchangeB', 'mode': 'gamepad', 'source': base_prof}, xbox_state)
    store.apply_mapping_change({'op': 'swap_ab_xy', 'profile': 'AexchangeB', 'pairs': [['0', '1']]}, xbox_state)
    # Simulate offline state at startup (active_profile is 'XInput · 默认', while controller_profiles remembers 'AexchangeB')
    store.activate_controller(None)
    store.save()

    fake_lib = _FakeViGEmLib(occupied_slots={0})
    vpad = VirtualXInputPad(lib=fake_lib)
    assert vpad.connect() is True
    assert vpad.user_index == 1

    class _DummyDevice:
        available = []
        preferred_key = ''
        current = dict(xbox_state)

        def scan(self):
            return []

        def read(self):
            return self.current

        def set_response_curves(self, *_):
            pass

        def rumble(self, *_):
            return False

        def led(self, *_):
            return False

        def close(self):
            pass

    class _DummyActions:
        def __init__(self):
            self.virtual_pad = vpad
            self.disconnect_calls = 0

        def _ensure_virtual_pad(self):
            return self.virtual_pad

        def sync_virtual_gamepad(self, buttons=(), axes=()):
            self.virtual_pad.emit_frame(buttons=buttons, axes=axes)

        def disconnect_virtual_gamepad(self):
            self.disconnect_calls += 1
            self.virtual_pad.close()

        def release_all(self):
            pass

    dev = _DummyDevice()
    act = _DummyActions()
    with patch('gamepadstudio.virtual_xinput.virtual_gamepad_backend_available', return_value=True), \
         patch('gamepadstudio.agent.Agent.apply_device_cloaking', lambda self: None), \
         patch('gamepadstudio.agent.Agent.apply_gamebar_shield', lambda self: None):
        agent = Agent(tmp_path, device=dev, actions=act)
        try:
            agent.timer.stop()
            agent.scan_timer.stop()
            agent.broadcast_timer.stop()
            agent._user32 = SimpleNamespace(GetAsyncKeyState=lambda _key: 0)
            agent._last_prtsc_down = False
            with patch('gamepadstudio.agent.sys', SimpleNamespace(platform='win32')):
                # Offline remembered profile check works even before first poll
                assert agent._should_hold_virtual_gamepad() is True

                # Poll 1: controller connects (occupying Slot 0, so VirtualXInputPad is on Slot 1)
                agent.poll()
                assert agent.status()['virtual_gamepad'] == {'connected': True, 'user_index': 1}

                # Poll 2: controller disconnects (releasing Slot 0) -> Agent keeps VirtualXInputPad and reclaims Slot 0!
                dev.current = None
                fake_lib.occupied.discard(0)
                agent.poll()
                assert act.disconnect_calls == 0
                assert vpad.is_connected is True
                assert vpad.user_index == 0
                assert agent.status()['virtual_gamepad'] == {'connected': True, 'user_index': 0}
        finally:
            agent.close()
            vpad.close()

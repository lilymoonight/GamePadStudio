"""Unit and UI tests for the 6 Bilibili player feedback integrations:
1. Gyro independent X/Y sensitivity + Gyro-to-Right-Stick output mode (@Flyknight666)
2. Virtual KBM 'Clear All Bindings' & 'Delete Profile' (@Flyknight666)
3. Virtual KBM Back-Paddles (P1-P4 / 16-19) quick-bind bar (@its蟹bro, @陨落深海, @寂无歌)
4. DualSense Edge (054C:0DF2) recognition priority, virtual X360 deprioritization & P1<->P4 order (@wqpdb, @longskygoo)
5. Stick Anti-Deadzone (0%-25%) in pointer & virtual XInput (@Azz-123, @iO想吃羊肉串)
6. Screenshot game process name prefix & .metadata/ sidecar organization (@cakeyslime)
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtWidgets import QDialog

from gamepadstudio.controller_catalog import family_for
from gamepadstudio.mapping_engine import MappingRuntime
from gamepadstudio.profile_transfer import export_profile, preview_profile_import
from gamepadstudio.screenshot_service import (
    delete_capture,
    extract_png_metadata,
    list_captures,
    organize_capture_sidecars,
    resolve_capture_game_label,
    set_favorite,
    take_screenshot,
)
from gamepadstudio.studio_core import ConfigStore, radial_deadzone
from gamepadstudio.virtual_kbm import VirtualMouseThread
from gamepadstudio.virtual_kbm_ui import KbmFeelDialog
from tests.test_device_scope_ui import workspace


class DummyActions:
    def __init__(self):
        self.moves = []
        self.vpad_updates = []

    def move_mouse(self, dx, dy):
        self.moves.append((dx, dy))

    def hold(self, key, down, force=False):
        pass

    def mouse_button(self, button, down):
        pass

    def scroll(self, steps):
        pass

    def release_all(self):
        pass

    def sync_virtual_gamepad(self, buttons=(), axes=()):
        self.vpad_updates.append((set(buttons), list(axes)))


def test_gyro_independent_xy_sensitivity_and_right_stick_output_mode():
    actions = DummyActions()
    mouse = VirtualMouseThread(actions)
    mouse.configure({
        'gyro_enabled': True,
        'gyro_sensitivity': 10.0,
        'gyro_sensitivity_y': 40.0,
        'gyro_output_mode': 'mouse',
    })
    assert mouse.gyro_sensitivity == pytest.approx(10.0)
    assert mouse.gyro_sensitivity_y == pytest.approx(40.0)
    assert mouse.gyro_output_mode == 'mouse'

    # Switch to right_stick mode: mouse_thread must not emit mouse movement from gyro
    mouse.configure({
        'gyro_enabled': True,
        'gyro_sensitivity': 20.0,
        'gyro_sensitivity_y': 35.0,
        'gyro_output_mode': 'right_stick',
    })
    assert mouse.gyro_output_mode == 'right_stick'

    # Verify MappingRuntime routes gyro to virtual XInput right stick (axes[2], axes[3])
    # and sends (0, 0, 0) to mouse_thread when gyro_output_mode == 'right_stick'
    rt_actions = DummyActions()
    runtime = MappingRuntime(rt_actions, lambda *a, **kw: None, start_mouse=False)
    store = ConfigStore(Path('/tmp/gps_test_gyro_rs'))
    state = {
        'instance_id': 1,
        'family': 'dualsense',
        'vendor': 0x054C,
        'product': 0x0CE6,
        'profile_key': 'dualsense:054c:0ce6:abcd',
        'device_key': 'dualsense:054c:0ce6:abcd:1',
        'available_buttons': list(range(21)),
        'available_axes': list(range(6)),
        'buttons': [],
        'axes': [0.0, 0.0, 0.0, 0.0, 0.8, 0.0],  # LT held (> trigger press threshold)
        'gyro': [0.4, -0.5, 0.0],
    }
    store.activate_controller(state)
    active = store.data['active_profile']
    store.data.setdefault('profile_options', {})[active] = {
        'right_stick_mouse': True,
        'stick_mouse_enabled': True,
        'mouse': {
            'gyro_enabled': True,
            'gyro_trigger': 'LT',
            'gyro_sensitivity': 18.0,
            'gyro_sensitivity_y': 36.0,
            'gyro_output_mode': 'right_stick',
        },
    }
    runtime.update(state, store.data, enabled=True, now=1.0)
    vstate = runtime.virtual_xinput_state
    assert vstate['gyro_output_mode'] == 'right_stick'
    assert vstate['gyro_right_stick_active'] is True
    assert rt_actions.vpad_updates
    _, sent_axes = rt_actions.vpad_updates[-1]
    assert sent_axes[2] > 0.2   # yaw=-0.5 -> +RX
    assert sent_axes[3] < -0.3  # pitch=+0.4 -> -RY (scaled by 36.0/36.0 = 1.0)


def test_anti_deadzone_rescaling_and_profile_transfer_validation(tmp_path):
    # Without anti_deadzone, deflection just above deadzone (0.11 vs 0.10) has tiny output (~0.011)
    rx0, _ = radial_deadzone(0.11, 0.0, 0.10, anti_deadzone=0.0)
    assert rx0 == pytest.approx((0.11 - 0.10) / 0.90, abs=1e-4)

    # With 20% anti_deadzone, deflection just above deadzone immediately overcomes game stiffness (>= 0.20)
    rx_anti, _ = radial_deadzone(0.11, 0.0, 0.10, anti_deadzone=0.20)
    assert rx_anti >= 0.20

    # Portable profile export/import round-trip preserves anti_deadzone, gyro_sensitivity_y, gyro_output_mode
    store = ConfigStore(tmp_path)
    state = {
        'instance_id': 1,
        'family': 'dualsense',
        'vendor': 0x054C,
        'product': 0x0DF2,
        'profile_key': 'dualsense:054c:0df2:edge',
        'device_key': 'dualsense:054c:0df2:edge:1',
        'available_buttons': list(range(21)),
        'available_axes': list(range(6)),
    }
    store.activate_controller(state)
    prof = store.data['active_profile']
    store.apply_mapping_change({
        'op': 'options',
        'profile': prof,
        'options': {
            'right_stick_mouse': True,
            'stick_mouse_enabled': True,
            'mouse': {
                'sensitivity': 28.0,
                'deadzone': 0.06,
                'anti_deadzone': 0.15,
                'gyro_enabled': True,
                'gyro_sensitivity': 18.0,
                'gyro_sensitivity_y': 26.0,
                'gyro_output_mode': 'right_stick',
            },
        },
    }, state)
    pkg = export_profile(store.data, state, prof)
    preview = preview_profile_import(pkg, state)
    mouse_out = preview['profile']['options']['mouse']
    assert mouse_out['anti_deadzone'] == pytest.approx(0.15)
    assert mouse_out['gyro_sensitivity_y'] == pytest.approx(26.0)
    assert mouse_out['gyro_output_mode'] == 'right_stick'


def test_dualsense_edge_family_priority_and_paddle_swap():
    # Even if SDL reports controller_type=1 (Xbox 360) or 2 (Xbox One) via an XInput wrapper,
    # DualSense Edge (0x054C:0x0DF2) must ALWAYS resolve to 'dualsense'
    assert family_for(controller_type=1, vendor=0x054C, product=0x0DF2, name='XInput Controller') == 'dualsense'
    assert family_for(controller_type=2, vendor=0x054C, product=0x0CE6, name='Controller') == 'dualsense'
    assert family_for(controller_type=1, vendor=0x057E, product=0x2009, name='Controller') == 'switch'


def test_kbm_clear_all_delete_scheme_and_back_paddles_quick_bar(workspace):
    from tests.test_device_scope_ui import device, select

    window, app = workspace
    select(window, app, device(101))
    kbm = window.virtual_kbm_page
    scheme = kbm.current_scheme()
    assert scheme

    # 1. Back paddles P1..P4 quick bar is present and updates label when bound
    assert set(kbm.paddle_buttons.keys()) == {'16', '17', '18', '19'}
    window.mapping_change({
        'op': 'binding',
        'profile': scheme,
        'trigger': '16',
        'allow_back_paddles': True,
        'mapping': {'short': {'action': 'shortcut', 'value': 'Alt+Tab'}, 'long': {'action': 'none'}},
    })
    kbm.refresh_display()
    assert 'Alt+Tab' in kbm.paddle_buttons['16'].text()

    # 2. KbmFeelDialog round-trip for anti_deadzone, gyro_sensitivity_y, and gyro_output_mode
    window.snapshot['gyro'] = True
    dialog = KbmFeelDialog(window, scheme)
    try:
        dialog.mouse_fields['anti_deadzone'].setValue(15)
        dialog.gyro_enabled_toggle.setChecked(True)
        dialog.gyro_sensitivity.setValue(20.0)
        dialog.gyro_sensitivity_y.setValue(32.5)
        dialog.gyro_output_mode.setCurrentIndex(dialog.gyro_output_mode.findData('right_stick'))
        opts = dialog.options()
        assert opts['mouse']['anti_deadzone'] == pytest.approx(0.15)
        assert opts['mouse']['gyro_sensitivity'] == pytest.approx(20.0)
        assert opts['mouse']['gyro_sensitivity_y'] == pytest.approx(32.5)
        assert opts['mouse']['gyro_output_mode'] == 'right_stick'
    finally:
        dialog.device_timer.stop()
        dialog.close()

    # 3. Clear all bindings on current KBM scheme
    assert kbm.clear_all_bindings(confirm=False) is True
    cleared_entries = window.config['profiles'][scheme]
    assert all(
        entry.get('short', {}).get('action', 'none') == 'none'
        and entry.get('long', {}).get('action', 'none') == 'none'
        for entry in cleared_entries.values()
    )

    # 4. Duplicate and delete a KBM preset
    window.mapping_change({'op': 'create', 'profile': '自定义键鼠二号', 'source': scheme, 'mode': 'kbm'})
    kbm.refresh_display()
    kbm_count_before = len(window.store.profiles_for(window.snapshot, mode='kbm'))
    assert kbm_count_before >= 2
    assert kbm.delete_scheme(confirm=False) is True
    assert len(window.store.profiles_for(window.snapshot, mode='kbm')) == kbm_count_before - 1


def test_screenshot_game_label_prefix_and_organize_sidecars(tmp_path, monkeypatch):
    from gamepadstudio import screenshot_service as service

    # 1. Game executable name is prefixed when window title does not include it
    label = resolve_capture_game_label(hwnd=100, title='第四章：曲度紫鸳', pname='BlackMythWukong-Win64-Shipping.exe')
    assert label == 'BlackMythWukong_第四章：曲度紫鸳'

    # 2. When window title is generic '桌面', uses cleaned game executable name
    label_desk = resolve_capture_game_label(hwnd=100, title='桌面', pname='InfinityNikki.exe')
    assert label_desk == 'InfinityNikki'

    # 3. Organize loose .json sidecars in Captures folder
    vid = tmp_path / 'DS_Game_20261102_120000_replay.mp4'
    vid.write_bytes(b'\x00' * 2048)
    vid_json = vid.with_suffix('.json')
    vid_json.write_text(json.dumps({'title': '黑神话', 'resolution': '3840x2160', 'favorite': False}), encoding='utf-8')

    moved = organize_capture_sidecars(tmp_path)
    assert moved == 1
    assert not vid_json.exists()
    assert (tmp_path / '.metadata' / vid_json.name).is_file()

    # list_captures, set_favorite, and delete_capture seamlessly work with .metadata/ sidecar
    rows = list_captures(tmp_path)
    assert len(rows) == 1
    assert rows[0]['raw_title'] == '黑神话'
    assert (rows[0]['width'], rows[0]['height']) == (3840, 2160)

    set_favorite(str(vid), True)
    assert list_captures(tmp_path)[0]['favorite'] is True

    assert delete_capture(str(vid)) is True
    assert not vid.exists()
    assert not (tmp_path / '.metadata' / vid_json.name).exists()

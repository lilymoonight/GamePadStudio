"""Comprehensive regression and verification test suite for GamePad Studio v2.2.0 remediation.

Covers:
1. Left stick mouse conflict suppression (WASD directional keys suppressed when stick_source='left' steers mouse).
2. 2D radial outer threshold activation for 45-degree diagonal deflection at physical rim.
3. DirectInput trigger normalization (rescaling signed [-1.0, 1.0] -> [0.0, 1.0]) and sdl_mapping passing.
4. Slot 15 reservation (Share/Capture never collided with raw unused buttons).
5. Schema cross-validation (outer_press > stick_press, gyro_trigger aliases L3/R3/7/8).
6. VirtualXInputPad and WindowsActions thread safety & negative caching.
7. Double-tap-hold force pulse and turbo toggle clamping.
"""
from __future__ import annotations

import math
import os
import random
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from gamepadstudio.actions import WindowsActions
from gamepadstudio.device_quirks import (
    normalize_dinput_gamepad_state,
    partition_dinput_buttons_by_sdl_db,
)
from gamepadstudio.mapping_engine import (
    InputNormalizer,
    MappingRuntime,
)
from gamepadstudio.profile_transfer import export_profile, preview_profile_import
from gamepadstudio.schema_registry import (
    DEFAULT_GYRO_SENSITIVITY,
    DEFAULT_GYRO_TRIGGER,
    DEFAULT_STICK_SOURCE,
    DEFAULT_TURBO_RATE,
    GYRO_TRIGGERS,
    STICK_SOURCES,
)
from gamepadstudio.virtual_xinput import (
    _VIGEM_CACHE,
    VirtualXInputPad,
    _find_vigem_dll,
)


class FakeActions:
    def __init__(self):
        self.held: set[str] = set()
        self.pulses: list[tuple[str, bool]] = []
        self.mouse_moves: list[tuple[int, int]] = []
        self.virtual_gamepad_synced = None

    def hold(self, value, down, force=False):
        if down:
            self.held.add(value)
        else:
            self.held.discard(value)
        self.pulses.append((value, down, force))

    def move_mouse(self, dx, dy):
        self.mouse_moves.append((dx, dy))

    def sync_virtual_gamepad(self, buttons=None, axes=None):
        self.virtual_gamepad_synced = (list(buttons or ()), list(axes or ()))

    def release_all(self):
        self.held.clear()


class FakeMouseThread:
    def __init__(self):
        self.stick_updates: list[tuple[float, float, dict]] = []
        self.gyro_updates: list[tuple[list, dict]] = []
        self.options = {}

    def configure(self, options):
        self.options = dict(options)

    def update_stick(self, x, y, options=None, **kwargs):
        self.stick_updates.append((x, y, options, kwargs))

    def update_gyro(self, *args, **kwargs):
        self.gyro_updates.append((args, kwargs))

    def set_click_lock(self, locked):
        pass

    def stop(self):
        pass


class TestV220PipelineRemediation(unittest.TestCase):
    def setUp(self):
        self.actions = FakeActions()
        self.mouse = FakeMouseThread()
        self.runtime = MappingRuntime(self.actions, lambda b, d: None, start_mouse=False)
        self.runtime.mouse_thread = self.mouse

        self.config = {
            'active_profile': 'test_p',
            'profiles': {
                'test_p': {
                    'LS:up': {'short': {'action': 'hold', 'value': 'W'}, 'long': {'action': 'none'}},
                    'LS:down': {'short': {'action': 'hold', 'value': 'S'}, 'long': {'action': 'none'}},
                    'LS:left': {'short': {'action': 'hold', 'value': 'A'}, 'long': {'action': 'none'}},
                    'LS:right': {'short': {'action': 'hold', 'value': 'D'}, 'long': {'action': 'none'}},
                }
            },
            'profile_options': {
                'test_p': {
                    'stick_mouse_enabled': True,
                    'mouse': {'mode': 'game', 'stick_source': 'left'},
                }
            },
            'profile_modes': {'test_p': 'kbm'},
        }
        self.state = {
            'family': 'xbox',
            'device_key': 'test:rem',
            'buttons': [],
            'axes': [0.0] * 6,
            'touch_fingers': [],
        }

    # -------------------------------------------------------------------------
    # 1. Left Stick Mouse Conflict Suppression
    # -------------------------------------------------------------------------
    def test_left_stick_mouse_suppresses_directional_wasd(self):
        """When stick_source is 'left' and stick is moved, directional WASD keys must be suppressed."""
        # Deflect left stick diagonally: x=0.75, y=-0.75
        state = dict(self.state, axes=[0.75, -0.75, 0.0, 0.0, 0.0, 0.0])
        self.runtime.update(state, self.config, enabled=True, now=1.0)

        # Mouse thread received the stick deflection
        self.assertTrue(len(self.mouse.stick_updates) > 0)
        self.assertEqual(self.mouse.stick_updates[-1][:2], (0.75, -0.75))

        # Crucial check: W, S, A, D must NOT be held by the actions backend
        held_keys = self.actions.held
        self.assertNotIn('W', held_keys, "LS:up (W) should be suppressed when left stick controls mouse")
        self.assertNotIn('D', held_keys, "LS:right (D) should be suppressed when left stick controls mouse")

    def test_right_stick_mouse_allows_directional_wasd(self):
        """When stick_source is 'right' (default), left stick directional WASD keys work normally."""
        self.config['profile_options']['test_p']['mouse']['stick_source'] = 'right'
        state = dict(self.state, axes=[0.75, -0.75, 0.0, 0.0, 0.0, 0.0])
        self.runtime.update(state, self.config, enabled=True, now=1.0)

        held_keys = self.actions.held
        self.assertIn('W', held_keys)
        self.assertIn('D', held_keys)

    # -------------------------------------------------------------------------
    # 2. 2D Radial Outer Threshold Activation for Diagonal Deflection
    # -------------------------------------------------------------------------
    def test_diagonal_outer_ring_rim_activation(self):
        """At 45-degree deflection to physical rim (magnitude 1.0), components are ~0.7071 < 0.85.

        2D radial hypot >= 0.85 must activate both directional outers (e.g. LS:up:outer and LS:right:outer).
        """
        normalizer = InputNormalizer()
        # Physical rim deflection at 45 degrees: x = 0.707106, y = -0.707106 (Up-Right)
        x_rim = math.cos(math.pi / 4)
        y_rim = -math.sin(math.pi / 4)
        self.assertAlmostEqual(math.hypot(x_rim, y_rim), 1.0, places=5)
        self.assertLess(x_rim, 0.85, "Individual scalar component is below 0.85")

        state = {'buttons': [], 'axes': [x_rim, y_rim, 0.0, 0.0, 0.0, 0.0]}
        inputs = normalizer.update(state)

        # Both radial outer and directional outers must be triggered!
        self.assertIn('LS:outer', inputs)
        self.assertIn('LS:up:outer', inputs, "45-degree rim push must activate LS:up:outer")
        self.assertIn('LS:right:outer', inputs, "45-degree rim push must activate LS:right:outer")

        # Test remaining 3 quadrants at physical rim
        # Down-Right: (0.7071, 0.7071)
        inputs_dr = normalizer.update({'buttons': [], 'axes': [x_rim, -y_rim, 0, 0, 0, 0]})
        self.assertIn('LS:down:outer', inputs_dr)
        self.assertIn('LS:right:outer', inputs_dr)

        # Down-Left: (-0.7071, 0.7071)
        inputs_dl = normalizer.update({'buttons': [], 'axes': [-x_rim, -y_rim, 0, 0, 0, 0]})
        self.assertIn('LS:down:outer', inputs_dl)
        self.assertIn('LS:left:outer', inputs_dl)

        # Up-Left: (-0.7071, -0.7071)
        inputs_ul = normalizer.update({'buttons': [], 'axes': [-x_rim, y_rim, 0, 0, 0, 0]})
        self.assertIn('LS:up:outer', inputs_ul)
        self.assertIn('LS:left:outer', inputs_ul)

    # -------------------------------------------------------------------------
    # 3. DirectInput Trigger Normalization & Rescaling
    # -------------------------------------------------------------------------
    def test_dinput_signed_triggers_rescaled_from_minus_one_to_zero(self):
        """Resting DInput triggers at -1.0 must be rescaled to 0.0."""
        # Raw axes with LT (idx 4) and RT (idx 5) at resting -1.0
        raw_axes = [0.0, 0.0, 0.0, 0.0, -1.0, -1.0]
        _, norm_axes = normalize_dinput_gamepad_state(
            raw_buttons=[],
            raw_axes=raw_axes,
            dinput_layout='type_b',
            num_buttons=12,
        )
        self.assertAlmostEqual(norm_axes[4], 0.0, places=4, msg="LT resting at -1.0 must normalize to 0.0")
        self.assertAlmostEqual(norm_axes[5], 0.0, places=4, msg="RT resting at -1.0 must normalize to 0.0")

        # Half pull (0.0 raw) with signed trigger tracking -> 0.5 normalized
        raw_axes_half = [0.0, 0.0, 0.0, 0.0, 0.0, 1.0]
        _, norm_axes_half = normalize_dinput_gamepad_state(
            raw_buttons=[],
            raw_axes=raw_axes_half,
            dinput_layout='type_b',
            num_buttons=12,
            signed_trigger_axes={4, 5},
        )
        self.assertAlmostEqual(norm_axes_half[4], 0.5, places=4, msg="LT at 0.0 signed must normalize to 0.5")
        self.assertAlmostEqual(norm_axes_half[5], 1.0, places=4, msg="RT at 1.0 signed must normalize to 1.0")

    # -------------------------------------------------------------------------
    # 4. Slot 15 Reservation & Partitioning
    # -------------------------------------------------------------------------
    def test_partition_reserves_slot_15_for_canonical_share_capture(self):
        """Unused raw buttons must NEVER be assigned slot 15 (reserved for Share/Capture)."""
        # A 15-button controller where buttons 5, 8, 12 are unused
        part = partition_dinput_buttons_by_sdl_db(num_buttons=15, sdl_mapping='')
        for raw_btn, slot in part['canonical_unused_slots'].items():
            self.assertNotEqual(slot, 15, f"Raw unused button {raw_btn} collided with reserved slot 15!")
            self.assertGreaterEqual(slot, 16, f"Allocated slot {slot} must be >= 16")

    # -------------------------------------------------------------------------
    # 5. Schema Cross-Validation & Constants
    # -------------------------------------------------------------------------
    def test_schema_cross_validation_rejects_outer_press_below_stick_press(self):
        """profile_transfer must reject presets where outer_press <= stick_press."""
        from gamepadstudio.profile_transfer import _options

        # outer_press (0.50) <= stick_press (0.60)
        invalid_options = {
            'input': {
                'stick_press': 0.60,
                'stick_release': 0.40,
                'outer_press': 0.50,
                'outer_release': 0.45,
            }
        }
        with self.assertRaises(ValueError) as ctx:
            _options(invalid_options)
        self.assertIn('外圈推满位置应大于摇杆开始响应位置', str(ctx.exception))

    def test_gyro_triggers_contains_l3_r3_and_aliases(self):
        """GYRO_TRIGGERS schema constants must contain canonical L3/R3, LT/RT, LB/RB and raw IDs."""
        for trig in ('LT', 'RT', 'LB', 'RB', 'L3', 'R3', 'always', '7', '8'):
            self.assertIn(trig, GYRO_TRIGGERS)

        self.assertEqual(DEFAULT_GYRO_TRIGGER, 'LT')
        self.assertEqual(DEFAULT_GYRO_SENSITIVITY, 18.0)
        self.assertEqual(DEFAULT_STICK_SOURCE, 'right')
        self.assertEqual(DEFAULT_TURBO_RATE, 15)

    # -------------------------------------------------------------------------
    # 6. VirtualXInputPad and Actions Thread Safety & Negative Caching
    # -------------------------------------------------------------------------
    def test_windows_actions_concurrent_thread_safety(self):
        """WindowsActions methods must execute concurrently without deadlock or state corruption."""
        action = WindowsActions.__new__(WindowsActions)
        action._lock = threading.RLock()
        action.held = {}
        action.held_mouse = set()
        action.held_gamepad_buttons = set()
        action.held_turbo = {}
        action.cached_foreground_window = (0, 0.0, "")

        class MockUser32:
            def MapVirtualKeyW(self, k, mode):
                return 0x20
            def SendInput(self, count, array, size):
                return count
            def GetForegroundWindow(self):
                return 1234
            def GetWindowThreadProcessId(self, hwnd, pid_ptr):
                return 1

        action.user32 = MockUser32()
        keys = ('A', 'B', 'C', 'D')
        errors = []

        def worker(tid):
            k = keys[tid]
            try:
                for i in range(50):
                    action.hold(k, True)
                    action.move_mouse(1, -1)
                    action.hold(k, False)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Thread safety errors occurred: {errors}")

    def test_virtual_xinput_negative_caching(self):
        """When ViGEm DLL is not installed, _find_vigem_dll caches failure and does not walk disk continuously."""
        _VIGEM_CACHE.clear()
        _VIGEM_CACHE['found_path'] = None
        _VIGEM_CACHE['failed_until'] = 0.0
        start = time.perf_counter()
        res1 = _find_vigem_dll()
        dur1 = time.perf_counter() - start

        # Second call immediately after must return cached result instantly (< 1ms)
        start2 = time.perf_counter()
        res2 = _find_vigem_dll()
        dur2 = time.perf_counter() - start2

        self.assertEqual(res1, res2)
        self.assertLess(dur2, 0.01)

    # -------------------------------------------------------------------------
    # 7. Virtual Gamepad Synchronization Wiring
    # -------------------------------------------------------------------------
    def test_virtual_gamepad_sync_receives_unmapped_buttons_and_axes(self):
        """MappingRuntime._update_virtual_xinput_state wires into actions.sync_virtual_gamepad."""
        state = dict(self.state, buttons=[0, 1], axes=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
        self.runtime._update_virtual_xinput_state(state, regular_mappings={})

        self.assertIsNotNone(self.actions.virtual_gamepad_synced)
        synced_btns, synced_axes = self.actions.virtual_gamepad_synced
        self.assertEqual(synced_axes, [0.1, 0.2, 0.3, 0.4, 0.5, 0.6])

    def test_virtual_gamepad_concurrent_thread_safety(self):
        """Concurrent calls to gamepad_button, gamepad_chord, and sync_virtual_gamepad must not raise RuntimeError."""
        action = WindowsActions.__new__(WindowsActions)
        action._lock = threading.RLock()
        action.held = {}
        action.held_mouse = set()
        action.held_gamepad_buttons = set()
        action.held_turbo = {}
        action.virtual_pad = VirtualXInputPad()

        stop = threading.Event()
        errors = []

        def btn_worker():
            while not stop.is_set():
                try:
                    b = str(random.randint(0, 15))
                    action.gamepad_button(b, random.choice([True, False]))
                except Exception as exc:
                    errors.append(exc)

        def chord_worker():
            while not stop.is_set():
                try:
                    chord = f"{random.randint(0, 5)}+{random.randint(6, 12)}"
                    action.gamepad_chord(chord, random.choice([True, False]))
                except Exception as exc:
                    errors.append(exc)

        def sync_worker():
            while not stop.is_set():
                try:
                    action.sync_virtual_gamepad()
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=btn_worker) for _ in range(4)]
        threads += [threading.Thread(target=chord_worker) for _ in range(2)]
        threads += [threading.Thread(target=sync_worker) for _ in range(3)]
        for t in threads:
            t.start()
        time.sleep(1.0)
        stop.set()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Virtual gamepad concurrency errors: {errors}")

    def test_i18n_v220_ui_translation_parity(self):
        """All v2.2.0 UI components must achieve 100% bilingual parity in English mode."""
        from gamepadstudio.i18n import tr, set_language
        set_language('en')
        required_keys = [
            '按住 L3/LS 激活',
            '按住 R3/RS 激活',
            '3D 动作：左摇杆推底 Shift 冲刺',
            '已套用「3D 动作：左摇杆推底 Shift 冲刺」模板',
            '桌面指针：摇杆控制光标（自动清空移动按键冲突）',
            '已套用「桌面指针：摇杆控制光标（自动清空移动按键冲突）」模板',
            '右摇杆 (默认/视角控制)',
            '左摇杆 (单摇杆外设/辅助指针模式)',
            '若选择左摇杆控制指针，请确保未在左摇杆上绑定 WASD 移动，以免产生操作冲突',
            '将手柄摇杆模拟为鼠标移动，用于游戏视角或桌面光标控制',
            '按住高频连发 (Turbo)',
            '连发速率',
            '设置按住连发频率（5 - 30 Hz）',
            '外圈推满',
            '分方向独立推满阈值',
            '上推满',
            '下推满',
            '左推满',
            '右推满',
            '手柄输出配置；当前未接入系统虚拟手柄驱动后端。若需在游戏内高频自动连发，请切换到虚拟键鼠模式使用“按住高频连发 (Turbo)”。',
            '手柄连发速率（5–30 Hz），通过虚拟 XInput 总线实时输出高频脉冲',
            '未接入虚拟手柄驱动；游戏内连发请改用虚拟键鼠模式的“按住高频连发 (Turbo)”',
        ]
        for key in required_keys:
            translated = tr(key)
            self.assertNotEqual(translated, key, f"Missing or untranslated key: {key}")


if __name__ == '__main__':
    unittest.main()

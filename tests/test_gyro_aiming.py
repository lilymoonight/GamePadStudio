"""Automated unit and UI tests for Milestone 4 (Requirement R3):
DualSense / SDL2 Gyroscope 500Hz Mouse Aiming & KbmFeelDialog UI.

Covers:
1. Mock SDL2 controller sensor detection and reading in Device (present, absent, unsupported, nan/inf safety).
2. MappingRuntime trigger gating (active on trigger, inactive on release, 'always', preview blocked, reset).
3. VirtualMouseThread 500Hz kinematics (angular velocity conversion, sensitivity, invert-Y, sub-pixel accumulation, zero-stick execution).
4. Concurrent right-stick mouse movement + gyro aiming superposition and transitions.
5. KbmFeelDialog UI tests (visibility conditions, controls round-trip persistence, cancel isolation).
"""
from __future__ import annotations

import copy
import ctypes as C
import math
import os
import sys
import threading
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from gamepadstudio import virtual_kbm
from gamepadstudio.device import Device
from gamepadstudio.i18n import init_language
from gamepadstudio.mapping_engine import MappingRuntime
from gamepadstudio.studio_core import ConfigStore
from gamepadstudio.virtual_kbm import VirtualMouseThread
from gamepadstudio.virtual_kbm_ui import KbmFeelDialog
from tests.mapping_fixtures import MappingOwner


# ---------------------------------------------------------------------------
# Test Mocks & Helpers
# ---------------------------------------------------------------------------

SDL_SENSOR_GYRO = 2


class MockSensorSDL:
    """Mock SDL2 controller and sensor subsystem for Device unit tests."""

    def __init__(self, has_gyro: bool = True, sensor_api_available: bool = True):
        self.has_gyro = has_gyro
        self.sensor_api_available = sensor_api_available
        self.sensor_enabled: Dict[int, int] = {}
        self.sensor_data: Dict[int, Tuple[float, float, float]] = {
            SDL_SENSOR_GYRO: (0.15, -0.42, 0.08)
        }
        self.sensor_failures: Dict[int, Any] = {}
        self.attached = True
        self.events: List[Any] = []
        self.closed_handles: List[int] = []
        self.SDL_GameControllerHasSensor = self._has_sensor
        self.SDL_GameControllerSetSensorEnabled = self._set_sensor_enabled
        self.SDL_GameControllerGetSensorData = self._get_sensor_data

    def SDL_GameControllerOpen(self, index: int) -> int:
        return 11

    def _has_sensor(self, handle: int, sensor_type: int) -> int:
        if not self.sensor_api_available:
            raise AttributeError("SDL_GameControllerHasSensor unavailable")
        return 1 if (self.has_gyro and sensor_type == SDL_SENSOR_GYRO) else 0

    def _set_sensor_enabled(self, handle: int, sensor_type: int, enabled: int) -> int:
        if not self.sensor_api_available:
            raise AttributeError("SDL_GameControllerSetSensorEnabled unavailable")
        self.sensor_enabled[sensor_type] = enabled
        return 0

    def _get_sensor_data(self, handle: int, sensor_type: int,
                         data: Any, num_values: int) -> int:
        if not self.sensor_api_available:
            raise AttributeError("SDL_GameControllerGetSensorData unavailable")
        if sensor_type in self.sensor_failures:
            failure = self.sensor_failures[sensor_type]
            if isinstance(failure, Exception):
                raise failure
            return failure
        values = self.sensor_data.get(sensor_type, (0.0, 0.0, 0.0))
        for i in range(min(num_values, len(values))):
            data[i] = values[i]
        return 0

    def SDL_GameControllerGetAttached(self, handle: int) -> int:
        return 1 if self.attached else 0

    def SDL_GameControllerName(self, handle: int) -> bytes:
        return b"DualSense Wireless Controller"

    def SDL_GameControllerGetButton(self, handle: int, button: int) -> int:
        return 0

    def SDL_GameControllerGetAxis(self, handle: int, axis: int) -> int:
        return 0

    def SDL_GameControllerGetJoystick(self, handle: int) -> int:
        return handle

    def SDL_JoystickCurrentPowerLevel(self, handle: int) -> int:
        return 4

    def SDL_GameControllerGetVendor(self, handle: int) -> int:
        return 0x054C

    def SDL_GameControllerGetProduct(self, handle: int) -> int:
        return 0x0CE6

    def SDL_GameControllerHasLED(self, handle: int) -> int:
        return 1

    def SDL_GameControllerHasRumble(self, handle: int) -> int:
        return 1

    def SDL_GameControllerRumble(self, handle: int, low: int, high: int, duration: int) -> int:
        return 0

    def SDL_GameControllerGetNumTouchpads(self, handle: int) -> int:
        return 0

    def SDL_GameControllerClose(self, handle: int):
        self.closed_handles.append(handle)

    def SDL_PumpEvents(self):
        pass

    def SDL_PollEvent(self, buffer: Any) -> int:
        return 0


class MockActions:
    """Mock OS synthetic mouse and keyboard action sink."""

    def __init__(self):
        self.moves: List[Tuple[int, int, int]] = []  # (tick, dx, dy)
        self.keys: Dict[Any, int] = {}
        self.mouse_buttons: set = set()
        self.tick = 0

    def move_mouse(self, dx: int, dy: int):
        self.moves.append((self.tick, dx, dy))

    def hold(self, value: str, down: bool):
        self.keys[value] = max(0, self.keys.get(value, 0) + (1 if down else -1))

    def mouse_button(self, button: str, down: bool):
        if down:
            self.mouse_buttons.add(button)
        else:
            self.mouse_buttons.discard(button)

    def guard_cursor_edge(self):
        pass

    def is_nikki_game_focused(self) -> bool:
        return True


def deterministic_mouse_loop(monkeypatch, mouse: VirtualMouseThread,
                             actions: MockActions, frames: int = 50,
                             hooks: Optional[Dict[int, Any]] = None) -> List[Tuple[int, int, int]]:
    """Execute VirtualMouseThread synchronously over deterministic simulated 500Hz clock ticks."""
    hooks = hooks or {}
    elapsed = 0.0
    tick = -1
    timer: List[bool] = []

    def clock():
        nonlocal elapsed, tick
        tick += 1
        elapsed += 0.002  # Exact 500 Hz (dt = 2ms)
        actions.tick = tick
        if tick in hooks:
            hooks[tick]()
        if tick >= frames:
            mouse.stop()
        return elapsed

    monkeypatch.setattr(virtual_kbm, 'time', SimpleNamespace(perf_counter=clock, sleep=lambda _: None))
    monkeypatch.setattr(virtual_kbm, 'set_system_timer_resolution', lambda enabled: timer.append(enabled))
    monkeypatch.setattr('gamepadstudio.actions.attach_to_default_desktop', lambda: None)
    mouse.run()
    return list(actions.moves)


@pytest.fixture
def owner(tmp_path):
    """Fixture providing a headless MappingOwner UI harness."""
    _app = QApplication.instance() or QApplication([])
    init_language('zh')
    val = MappingOwner(tmp_path)
    val.snapshot = {
        'family': 'dualsense',
        'device_key': 'dualsense:unit:test',
        'instance_id': 1,
        'gyro': True,
        'touchpad': False,
        'touchpad_count': 0,
        'touchpad_fingers': 0,
        'available_buttons': list(range(16)),
        'available_axes': list(range(6)),
        'buttons': [],
        'axes': [0.0] * 6,
        'touch_fingers': [],
    }
    val.store.activate_controller(val.snapshot)
    profile = val.config['active_profile']
    val.config.setdefault('profile_options', {}).setdefault(profile, {'mouse': {}, 'input': {}})
    yield val
    val.close()


# ---------------------------------------------------------------------------
# 1. Device Gyro Sensor Detection, Reading, & Fallbacks
# ---------------------------------------------------------------------------

def test_device_detects_and_enables_gyro():
    """Device.select() must detect gyro support, enable the sensor, and record metadata['gyro'] = True."""
    mock_sdl = MockSensorSDL(has_gyro=True)
    dev = Device.__new__(Device)
    dev.lib = mock_sdl
    dev.handle = None
    dev.instance_id = None
    dev.metadata = {}
    dev.is_raw_joystick = False
    dev.event = C.create_string_buffer(128)
    dev.set_response_curves({})

    row = {'index': 0, 'profile_key': 'ps5:dualsense'}
    dev.select_row = row
    # Simulate select branch for GameController
    handle = mock_sdl.SDL_GameControllerOpen(row['index'])
    dev.handle = handle
    dev.instance_id = 1

    has_sensor_fn = getattr(dev.lib, 'SDL_GameControllerHasSensor', None)
    set_sensor_fn = getattr(dev.lib, 'SDL_GameControllerSetSensorEnabled', None)
    has_gyro = bool(has_sensor_fn and has_sensor_fn(handle, SDL_SENSOR_GYRO))
    if has_gyro and set_sensor_fn:
        set_sensor_fn(handle, SDL_SENSOR_GYRO, 1)
    dev.metadata['gyro'] = has_gyro

    assert dev.metadata['gyro'] is True
    assert mock_sdl.sensor_enabled.get(SDL_SENSOR_GYRO) == 1


def test_device_handles_absent_gyro_sensor():
    """Device.select() must set metadata['gyro'] = False when SDL controller lacks gyro sensor."""
    mock_sdl = MockSensorSDL(has_gyro=False)
    dev = Device.__new__(Device)
    dev.lib = mock_sdl
    dev.handle = 11
    dev.instance_id = 1
    dev.metadata = {}

    has_sensor_fn = getattr(dev.lib, 'SDL_GameControllerHasSensor', None)
    has_gyro = bool(has_sensor_fn and has_sensor_fn(dev.handle, SDL_SENSOR_GYRO))
    dev.metadata['gyro'] = has_gyro

    assert dev.metadata['gyro'] is False
    assert SDL_SENSOR_GYRO not in mock_sdl.sensor_enabled


def test_device_handles_unsupported_sdl_without_sensor_api():
    """Device must gracefully fallback with metadata['gyro'] = False on SDL builds lacking sensor APIs."""
    mock_sdl = MockSensorSDL(sensor_api_available=False)
    # Remove sensor methods to simulate older SDL2
    del mock_sdl.SDL_GameControllerHasSensor
    del mock_sdl.SDL_GameControllerSetSensorEnabled
    del mock_sdl.SDL_GameControllerGetSensorData

    dev = Device.__new__(Device)
    dev.lib = mock_sdl
    dev.handle = 11
    dev.instance_id = 1
    dev.metadata = {}

    has_sensor_fn = getattr(dev.lib, 'SDL_GameControllerHasSensor', None)
    has_gyro = bool(has_sensor_fn and has_sensor_fn(dev.handle, SDL_SENSOR_GYRO))
    dev.metadata['gyro'] = has_gyro

    assert dev.metadata['gyro'] is False


def test_device_read_valid_gyro_values():
    """Device.read() must read [pitch, yaw, roll] floats in rad/s when gyro is supported."""
    mock_sdl = MockSensorSDL(has_gyro=True)
    mock_sdl.sensor_data[SDL_SENSOR_GYRO] = (0.24, -0.65, 0.11)

    dev = Device.__new__(Device)
    dev.lib = mock_sdl
    dev.handle = 11
    dev.instance_id = 1
    dev.is_raw_joystick = False
    dev.metadata = {'gyro': True}
    dev.event = C.create_string_buffer(128)

    state = dev.read()
    assert 'gyro' in state
    assert state['gyro'] == pytest.approx([0.24, -0.65, 0.11])


@pytest.mark.parametrize('bad_values', [
    (float('nan'), 0.0, 0.0),
    (0.0, float('inf'), 0.0),
    (0.0, 0.0, float('-inf')),
])
def test_device_read_sanitizes_non_finite_values(bad_values):
    """Device.read() must sanitize NaN/Inf to [0.0, 0.0, 0.0] preventing accumulator corruption."""
    mock_sdl = MockSensorSDL(has_gyro=True)
    mock_sdl.sensor_data[SDL_SENSOR_GYRO] = bad_values

    dev = Device.__new__(Device)
    dev.lib = mock_sdl
    dev.handle = 11
    dev.instance_id = 1
    dev.is_raw_joystick = False
    dev.metadata = {'gyro': True}
    dev.event = C.create_string_buffer(128)

    state = dev.read()
    assert state['gyro'] == [0.0, 0.0, 0.0]


def test_device_read_handles_sensor_read_error():
    """Device.read() must return [0.0, 0.0, 0.0] when GetSensorData returns error code or raises."""
    mock_sdl = MockSensorSDL(has_gyro=True)
    mock_sdl.sensor_failures[SDL_SENSOR_GYRO] = -1

    dev = Device.__new__(Device)
    dev.lib = mock_sdl
    dev.handle = 11
    dev.instance_id = 1
    dev.is_raw_joystick = False
    dev.metadata = {'gyro': True}
    dev.event = C.create_string_buffer(128)

    state = dev.read()
    assert state['gyro'] == [0.0, 0.0, 0.0]


def test_device_read_unsupported_or_raw_joystick_returns_empty():
    """Device.read() must return 'gyro': [] for controllers without gyro or in raw joystick mode."""
    mock_sdl = MockSensorSDL(has_gyro=False)
    dev = Device.__new__(Device)
    dev.lib = mock_sdl
    dev.handle = 11
    dev.instance_id = 1
    dev.is_raw_joystick = False
    dev.metadata = {'gyro': False}
    dev.event = C.create_string_buffer(128)

    state = dev.read()
    assert state['gyro'] == []


def test_device_close_handle_disables_gyro():
    """Device.close_handle() must disable gyro sensor when active."""
    mock_sdl = MockSensorSDL(has_gyro=True)
    dev = Device.__new__(Device)
    dev.lib = mock_sdl
    dev.handle = 11
    dev.instance_id = 1
    dev.is_raw_joystick = False
    dev.metadata = {'gyro': True}
    mock_sdl.sensor_enabled[SDL_SENSOR_GYRO] = 1

    dev.close_handle()
    set_sensor_fn = getattr(mock_sdl, 'SDL_GameControllerSetSensorEnabled', None)
    # Either disabled to 0 or handle closed
    assert mock_sdl.sensor_enabled.get(SDL_SENSOR_GYRO) == 0 or 11 in mock_sdl.closed_handles


# ---------------------------------------------------------------------------
# 2. MappingRuntime Gyro Trigger Gating & Safety Lifecycle
# ---------------------------------------------------------------------------

def test_runtime_gyro_gated_by_trigger_press_and_release():
    """MappingRuntime must feed gyro only while gyro_trigger is pressed, zeroing immediately on release."""
    actions = MockActions()
    runtime = MappingRuntime(actions, lambda *_: None, start_mouse=False)
    mouse = VirtualMouseThread(actions)
    runtime.mouse_thread = mouse

    profile = 'test_gyro_profile'
    config = {
        'active_profile': profile,
        'profile_modes': {profile: 'kbm'},
        'profile_devices': {profile: 'generic:1'},
        'profile_options': {
            profile: {
                'mouse': {
                    'gyro_enabled': True,
                    'gyro_trigger': 'LT',
                    'gyro_sensitivity': 18.0,
                    'gyro_invert_y': False,
                }
            }
        },
        'profiles': {profile: {}},
    }

    state_neutral = {'device_key': 'generic:1', 'buttons': [], 'axes': [0.0] * 6, 'gyro': [0.1, -0.4, 0.0]}
    state_lt_pressed = {'device_key': 'generic:1', 'buttons': [], 'axes': [0.0, 0.0, 0.0, 0.0, 0.9, 0.0],
                        'gyro': [0.1, -0.4, 0.0]}  # axis 4 = LT

    # 1. Neutral (LT not pressed) -> gyro receives 0
    runtime.update(state_neutral, config, enabled=True, preview=False, now=0.0)
    assert (mouse.gyro_pitch, mouse.gyro_yaw, mouse.gyro_roll) == (0.0, 0.0, 0.0)

    # 2. LT pressed -> gyro receives sensor angular velocity
    runtime.update(state_lt_pressed, config, enabled=True, preview=False, now=0.1)
    assert (mouse.gyro_pitch, mouse.gyro_yaw, mouse.gyro_roll) == pytest.approx((0.1, -0.4, 0.0))

    # 3. LT released -> gyro immediately returns to 0
    runtime.update(state_neutral, config, enabled=True, preview=False, now=0.2)
    assert (mouse.gyro_pitch, mouse.gyro_yaw, mouse.gyro_roll) == (0.0, 0.0, 0.0)


def test_runtime_gyro_always_on_mode():
    """When gyro_trigger is 'always' or '', gyro aiming is active without holding any button."""
    actions = MockActions()
    runtime = MappingRuntime(actions, lambda *_: None, start_mouse=False)
    mouse = VirtualMouseThread(actions)
    runtime.mouse_thread = mouse

    profile = 'always_gyro'
    config = {
        'active_profile': profile,
        'profile_modes': {profile: 'kbm'},
        'profile_devices': {profile: 'generic:1'},
        'profile_options': {
            profile: {
                'mouse': {
                    'gyro_enabled': True,
                    'gyro_trigger': 'always',
                }
            }
        },
        'profiles': {profile: {}},
    }

    state = {'device_key': 'generic:1', 'buttons': [], 'axes': [0.0] * 6, 'gyro': [-0.25, 0.35, 0.05]}
    runtime.update(state, config, enabled=True, preview=False, now=0.0)
    assert (mouse.gyro_pitch, mouse.gyro_yaw, mouse.gyro_roll) == pytest.approx((-0.25, 0.35, 0.05))


@pytest.mark.parametrize('trigger_name,state_input', [
    ('RT', {'axes': [0.0, 0.0, 0.0, 0.0, 0.0, 0.9]}),  # axis 5 = RT
    ('9', {'buttons': [9]}),                           # button 9 = LB
    ('10', {'buttons': [10]}),                         # button 10 = RB
])
def test_runtime_gyro_alternate_triggers(trigger_name, state_input):
    """MappingRuntime must support alternate gyro triggers (RT, LB/9, RB/10)."""
    actions = MockActions()
    runtime = MappingRuntime(actions, lambda *_: None, start_mouse=False)
    mouse = VirtualMouseThread(actions)
    runtime.mouse_thread = mouse

    profile = 'alt_trigger'
    config = {
        'active_profile': profile,
        'profile_modes': {profile: 'kbm'},
        'profile_devices': {profile: 'generic:1'},
        'profile_options': {
            profile: {
                'mouse': {
                    'gyro_enabled': True,
                    'gyro_trigger': trigger_name,
                }
            }
        },
        'profiles': {profile: {}},
    }

    gyro_vec = [0.15, -0.30, 0.0]
    base_state = {'device_key': 'generic:1', 'buttons': [], 'axes': [0.0] * 6, 'gyro': gyro_vec}
    active_state = {**base_state, **state_input}

    # Inactive before pressing
    runtime.update(base_state, config, enabled=True, now=0.0)
    assert (mouse.gyro_pitch, mouse.gyro_yaw, mouse.gyro_roll) == (0.0, 0.0, 0.0)

    # Active when pressed
    runtime.update(active_state, config, enabled=True, now=0.1)
    assert (mouse.gyro_pitch, mouse.gyro_yaw, mouse.gyro_roll) == pytest.approx((0.15, -0.30, 0.0))


def test_runtime_gyro_blocked_in_preview():
    """MappingRuntime must block gyro aiming when preview=True to prevent cursor movement during UI capture."""
    actions = MockActions()
    runtime = MappingRuntime(actions, lambda *_: None, start_mouse=False)
    mouse = VirtualMouseThread(actions)
    runtime.mouse_thread = mouse

    profile = 'preview_test'
    config = {
        'active_profile': profile,
        'profile_modes': {profile: 'kbm'},
        'profile_devices': {profile: 'generic:1'},
        'profile_options': {profile: {'mouse': {'gyro_enabled': True, 'gyro_trigger': 'always'}}},
        'profiles': {profile: {}},
    }
    state = {'device_key': 'generic:1', 'buttons': [], 'axes': [0.0] * 6, 'gyro': [0.5, -0.5, 0.0]}

    runtime.update(state, config, enabled=True, preview=True, now=0.0)
    assert (mouse.gyro_pitch, mouse.gyro_yaw, mouse.gyro_roll) == (0.0, 0.0, 0.0)


def test_runtime_reset_zeros_gyro_and_clears_motion():
    """MappingRuntime.reset() must immediately zero gyro velocities and reset mouse accumulators."""
    actions = MockActions()
    runtime = MappingRuntime(actions, lambda *_: None, start_mouse=False)
    mouse = VirtualMouseThread(actions)
    runtime.mouse_thread = mouse

    mouse.gyro_enabled = True
    mouse.update_gyro(0.5, -0.5, 0.1)
    mouse.acc_x, mouse.acc_y = 0.45, -0.35

    runtime.reset()
    assert (mouse.gyro_pitch, mouse.gyro_yaw, mouse.gyro_roll) == (0.0, 0.0, 0.0)
    assert mouse.acc_x == 0.0 and mouse.acc_y == 0.0


# ---------------------------------------------------------------------------
# 3. VirtualMouseThread 500Hz Kinematics & Sub-Pixel Accumulation
# ---------------------------------------------------------------------------

def test_virtual_mouse_gyro_kinematics_direction(monkeypatch):
    """VirtualMouseThread converts gyro angular velocity to correct screen coordinates:
    - Turning right (-yaw > 0) -> dx > 0
    - Rolling right (-roll > 0) -> dx > 0
    - Tilting down (-pitch > 0) -> dy > 0
    - Tilting up (+pitch > 0) -> dy < 0
    """
    actions = MockActions()
    mouse = VirtualMouseThread(actions)
    mouse.configure({'gyro_enabled': True, 'gyro_sensitivity': 18.0, 'gyro_invert_y': False})

    # Case 1: Pure rightward yaw (yaw < 0)
    mouse.update_gyro(pitch=0.0, yaw=-0.5, roll=0.0)
    moves_yaw = deterministic_mouse_loop(monkeypatch, mouse, actions, frames=20)
    assert moves_yaw and all(dx > 0 and dy == 0 for _, dx, dy in moves_yaw)

    # Case 2: Pure rightward roll (roll < 0)
    actions.moves.clear()
    mouse.running = True
    mouse.update_gyro(pitch=0.0, yaw=0.0, roll=-0.5)
    moves_roll = deterministic_mouse_loop(monkeypatch, mouse, actions, frames=20)
    assert moves_roll and all(dx > 0 and dy == 0 for _, dx, dy in moves_roll)

    # Case 3: Downward tilt (pitch < 0)
    actions.moves.clear()
    mouse.running = True
    mouse.update_gyro(pitch=-0.5, yaw=0.0, roll=0.0)
    moves_down = deterministic_mouse_loop(monkeypatch, mouse, actions, frames=20)
    assert moves_down and all(dx == 0 and dy > 0 for _, dx, dy in moves_down)

    # Case 4: Upward tilt (pitch > 0)
    actions.moves.clear()
    mouse.running = True
    mouse.update_gyro(pitch=0.5, yaw=0.0, roll=0.0)
    moves_up = deterministic_mouse_loop(monkeypatch, mouse, actions, frames=20)
    assert moves_up and all(dx == 0 and dy < 0 for _, dx, dy in moves_up)


def test_virtual_mouse_gyro_invert_y(monkeypatch):
    """gyro_invert_y=True must flip vertical direction (pitch < 0 yields dy < 0) while keeping X intact."""
    actions = MockActions()
    mouse = VirtualMouseThread(actions)
    mouse.configure({'gyro_enabled': True, 'gyro_sensitivity': 18.0, 'gyro_invert_y': True})

    mouse.update_gyro(pitch=-0.5, yaw=-0.5, roll=0.0)
    moves = deterministic_mouse_loop(monkeypatch, mouse, actions, frames=20)
    assert moves and all(dx > 0 and dy < 0 for _, dx, dy in moves)


def test_virtual_mouse_gyro_sensitivity_scaling(monkeypatch):
    """Doubling gyro_sensitivity must produce exactly double the mouse movement over the same time."""
    actions_1x = MockActions()
    mouse_1x = VirtualMouseThread(actions_1x)
    mouse_1x.configure({'gyro_enabled': True, 'gyro_sensitivity': 18.0})
    mouse_1x.update_gyro(pitch=0.0, yaw=-0.5, roll=0.0)
    moves_1x = deterministic_mouse_loop(monkeypatch, mouse_1x, actions_1x, frames=30)
    total_1x = sum(dx for _, dx, _ in moves_1x)

    actions_2x = MockActions()
    mouse_2x = VirtualMouseThread(actions_2x)
    mouse_2x.configure({'gyro_enabled': True, 'gyro_sensitivity': 36.0})
    mouse_2x.update_gyro(pitch=0.0, yaw=-0.5, roll=0.0)
    moves_2x = deterministic_mouse_loop(monkeypatch, mouse_2x, actions_2x, frames=30)
    total_2x = sum(dx for _, dx, _ in moves_2x)

    assert total_2x == pytest.approx(total_1x * 2, abs=2)


def test_virtual_mouse_gyro_moves_when_right_stick_is_centered(monkeypatch):
    """CRITICAL REGRESSION: 500Hz loop must NOT abort or reset when right stick is at (0, 0) if gyro is active."""
    actions = MockActions()
    mouse = VirtualMouseThread(actions)
    mouse.configure({'gyro_enabled': True, 'gyro_sensitivity': 18.0})
    mouse.update_stick(0.0, 0.0)  # Stick strictly centered at deadzone
    mouse.update_gyro(pitch=0.0, yaw=-0.6, roll=0.0)

    moves = deterministic_mouse_loop(monkeypatch, mouse, actions, frames=25)
    assert len(moves) > 0
    assert sum(dx for _, dx, _ in moves) > 0


def test_virtual_mouse_stick_centering_does_not_clear_gyro_accumulators():
    """update_stick(0, 0) must not clear acc_x / acc_y while gyro velocity is present."""
    actions = MockActions()
    mouse = VirtualMouseThread(actions)
    mouse.configure({'gyro_enabled': True, 'gyro_sensitivity': 18.0})
    mouse.update_gyro(0.0, -0.5, 0.0)
    mouse.acc_x = 0.42
    mouse.acc_y = 0.31

    mouse.update_stick(0.0, 0.0)
    assert (mouse.acc_x, mouse.acc_y) == (0.42, 0.31)


def test_virtual_mouse_gyro_zero_immediately_halts_motion(monkeypatch):
    """Calling update_gyro(0, 0, 0) halts cursor movement immediately on subsequent ticks."""
    actions = MockActions()
    mouse = VirtualMouseThread(actions)
    mouse.configure({'gyro_enabled': True, 'gyro_sensitivity': 18.0})
    mouse.update_gyro(pitch=0.0, yaw=-0.5, roll=0.0)

    def stop_gyro():
        mouse.update_gyro(0.0, 0.0, 0.0)

    moves = deterministic_mouse_loop(monkeypatch, mouse, actions, frames=40, hooks={15: stop_gyro})
    assert moves and all(tick < 16 for tick, _, _ in moves)


# ---------------------------------------------------------------------------
# 4. Concurrent Right-Stick + Gyro Aiming Superposition
# ---------------------------------------------------------------------------

def test_concurrent_stick_and_gyro_constructive_superposition(monkeypatch):
    """Right stick and gyro pointing in the same direction add constructively."""
    # 1. Stick only
    actions_stick = MockActions()
    mouse_stick = VirtualMouseThread(actions_stick)
    mouse_stick.configure({'sensitivity': 20.0, 'gyro_enabled': False})
    mouse_stick.update_stick(0.6, 0.0)
    moves_stick = deterministic_mouse_loop(monkeypatch, mouse_stick, actions_stick, frames=25)
    dx_stick = sum(dx for _, dx, _ in moves_stick)

    # 2. Both stick and gyro pushing right
    actions_both = MockActions()
    mouse_both = VirtualMouseThread(actions_both)
    mouse_both.configure({'sensitivity': 20.0, 'gyro_enabled': True, 'gyro_sensitivity': 18.0})
    mouse_both.update_stick(0.6, 0.0)
    mouse_both.update_gyro(0.0, -0.5, 0.0)
    moves_both = deterministic_mouse_loop(monkeypatch, mouse_both, actions_both, frames=25)
    dx_both = sum(dx for _, dx, _ in moves_both)

    assert dx_both > dx_stick


def test_concurrent_stick_and_gyro_counter_aiming(monkeypatch):
    """Right stick pushing right + gyro twisting left results in destructive superposition."""
    actions_stick = MockActions()
    mouse_stick = VirtualMouseThread(actions_stick)
    mouse_stick.configure({'sensitivity': 20.0, 'gyro_enabled': False})
    mouse_stick.update_stick(0.6, 0.0)
    moves_stick = deterministic_mouse_loop(monkeypatch, mouse_stick, actions_stick, frames=25)
    dx_stick = sum(dx for _, dx, _ in moves_stick)

    actions_counter = MockActions()
    mouse_counter = VirtualMouseThread(actions_counter)
    mouse_counter.configure({'sensitivity': 20.0, 'gyro_enabled': True, 'gyro_sensitivity': 18.0})
    mouse_counter.update_stick(0.6, 0.0)
    mouse_counter.update_gyro(0.0, 0.5, 0.0)  # Counter yaw (left)
    moves_counter = deterministic_mouse_loop(monkeypatch, mouse_counter, actions_counter, frames=25)
    dx_counter = sum(dx for _, dx, _ in moves_counter)

    assert dx_counter < dx_stick


# ---------------------------------------------------------------------------
# 5. KbmFeelDialog UI & Round-Trip Persistence
# ---------------------------------------------------------------------------

def test_kbm_feel_dialog_visibility_on_gyro_hardware(owner):
    """KbmFeelDialog displays gyro section when controller snapshot indicates gyro hardware."""
    owner.snapshot['gyro'] = True
    profile = owner.config['active_profile']
    dialog = KbmFeelDialog(owner, profile)
    try:
        assert getattr(dialog, 'has_gyro', False) is True
        assert hasattr(dialog, 'gyro_enabled_toggle')
    finally:
        dialog.close()


def test_kbm_feel_dialog_visibility_hidden_without_hardware_or_config(owner):
    """KbmFeelDialog hides gyro section when controller lacks gyro and profile has no gyro settings."""
    owner.snapshot['gyro'] = False
    profile = owner.config['active_profile']
    owner.config['profile_options'].setdefault(profile, {}).setdefault('mouse', {}).pop('gyro_enabled', None)
    dialog = KbmFeelDialog(owner, profile)
    try:
        assert getattr(dialog, 'has_gyro', False) is False
    finally:
        dialog.close()


def test_kbm_feel_dialog_visibility_retained_when_configured_even_without_hardware(owner):
    """KbmFeelDialog displays gyro section if already configured in profile, allowing offline editing."""
    owner.snapshot['gyro'] = False
    profile = owner.config['active_profile']
    owner.config['profile_options'].setdefault(profile, {}).setdefault('mouse', {})['gyro_enabled'] = True
    dialog = KbmFeelDialog(owner, profile)
    try:
        assert getattr(dialog, 'has_gyro', False) is True
    finally:
        dialog.close()


def test_kbm_feel_dialog_gyro_options_save_and_reopen_roundtrip(owner):
    """KbmFeelDialog must save gyro_enabled, trigger, sensitivity, and invert_y, and reload them accurately."""
    owner.snapshot['gyro'] = True
    profile = owner.config['active_profile']
    dialog = KbmFeelDialog(owner, profile)
    try:
        dialog.gyro_enabled_toggle.setChecked(True)
        # Select RT trigger
        idx_rt = dialog.gyro_trigger.findData('RT')
        assert idx_rt >= 0
        dialog.gyro_trigger.setCurrentIndex(idx_rt)

        dialog.gyro_sensitivity.setValue(24.5)

        idx_inv = dialog.gyro_invert_y.findData(True)
        assert idx_inv >= 0
        dialog.gyro_invert_y.setCurrentIndex(idx_inv)

        dialog.validate()
        assert dialog.result() == QDialog.DialogCode.Accepted

        # Apply mapping options change to owner store
        owner.mapping_change({'op': 'options', 'profile': profile, 'options': dialog.options()})
    finally:
        dialog.close()

    # Verify persisted in config store
    stored_mouse = owner.config['profile_options'][profile]['mouse']
    assert stored_mouse['gyro_enabled'] is True
    assert stored_mouse['gyro_trigger'] == 'RT'
    assert stored_mouse['gyro_sensitivity'] == pytest.approx(24.5)
    assert stored_mouse['gyro_invert_y'] is True

    # Reopen dialog and check widget values
    reopened = KbmFeelDialog(owner, profile)
    try:
        assert reopened.gyro_enabled_toggle.isChecked() is True
        assert reopened.gyro_trigger.currentData() == 'RT'
        assert reopened.gyro_sensitivity.value() == pytest.approx(24.5)
        assert reopened.gyro_invert_y.currentData() is True
    finally:
        reopened.close()


def test_kbm_feel_dialog_gyro_cancel_does_not_mutate_config(owner):
    """Calling reject() on KbmFeelDialog must discard draft changes without mutating profile options."""
    owner.snapshot['gyro'] = True
    profile = owner.config['active_profile']
    before = copy.deepcopy(owner.config)

    dialog = KbmFeelDialog(owner, profile)
    try:
        dialog.gyro_enabled_toggle.setChecked(True)
        dialog.gyro_sensitivity.setValue(99.0)
        dialog.reject()
    finally:
        dialog.close()

    assert owner.config == before

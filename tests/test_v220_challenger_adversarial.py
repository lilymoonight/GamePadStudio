"""Adversarial stress-test suite for GamePad Studio v2.2.0 input pipeline.

Conducted by Challenger 1 (Input Pipeline Adversarial Verifier).
Stress-tests:
1. 360-degree continuous stick sweeps at sub-threshold, threshold, and outer rim radii.
2. 45-degree diagonal outer rim activation and radial projection boundaries.
3. High-frequency packet streams with stick_source == 'left' (100% zero WASD leakage).
4. double_tap_hold OS key pulse verification under overlapping directional holds and quick-release edge cases.
5. Turbo repeat state-machine under severe simulated frame time jitter and profile switches.
"""
from __future__ import annotations

import math
import random
import threading
import unittest

from gamepadstudio.actions import WindowsActions
from gamepadstudio.mapping_engine import (
    InputNormalizer,
    MappingRuntime,
)


class MockActionsWithRefcounts:
    """Rigorous actions mock that tracks hardware-level OS events, refcounts, and pulses."""
    def __init__(self):
        self.os_events: list[tuple[str, str, bool, bool]] = []  # (kind, key/button, down, force)
        self.held_keys: set[str] = set()
        self.key_refcounts: dict[str, int] = {}
        self.mouse_moves: list[tuple[int, int]] = []
        self.mouse_buttons: set[str] = set()
        self.scroll_events: list[int] = []
        self.virtual_gamepad_synced = None

    def hold(self, value: str, down: bool, force: bool = False):
        self.os_events.append(('key', value, down, force))
        if down:
            fresh = True if force else (self.key_refcounts.get(value, 0) == 0)
            self.key_refcounts[value] = self.key_refcounts.get(value, 0) + 1
            if fresh:
                self.held_keys.add(value)
        else:
            releases = True if force else (self.key_refcounts.get(value) == 1)
            if value in self.key_refcounts:
                self.key_refcounts[value] -= 1
                if self.key_refcounts[value] <= 0:
                    del self.key_refcounts[value]
            if releases and value not in self.key_refcounts:
                self.held_keys.discard(value)

    def mouse_button(self, button: str, down: bool):
        self.os_events.append(('mouse', button, down, False))
        if down:
            self.mouse_buttons.add(button)
        else:
            self.mouse_buttons.discard(button)

    def move_mouse(self, dx: int, dy: int):
        self.mouse_moves.append((dx, dy))

    def scroll(self, steps: int):
        self.scroll_events.append(steps)

    def sync_virtual_gamepad(self, buttons=None, axes=None):
        self.virtual_gamepad_synced = (list(buttons or ()), list(axes or ()))

    def release_all(self):
        self.held_keys.clear()
        self.key_refcounts.clear()
        self.mouse_buttons.clear()


class MockMouseThreadTracker:
    def __init__(self):
        self.stick_updates: list[tuple[float, float, bool]] = []
        self.gyro_updates = []
        self.options = {}

    def configure(self, opts):
        self.options = dict(opts)

    def update_stick(self, x: float, y: float, is_desktop: bool = False, **kwargs):
        self.stick_updates.append((x, y, is_desktop))

    def update_gyro(self, *args, **kwargs):
        self.gyro_updates.append((args, kwargs))

    def set_click_lock(self, locked: bool):
        pass

    def stop(self):
        pass


class TestInputPipelineAdversarial(unittest.TestCase):
    def setUp(self):
        self.normalizer = InputNormalizer()
        self.actions = MockActionsWithRefcounts()
        self.mouse_thread = MockMouseThreadTracker()
        self.runtime = MappingRuntime(self.actions, lambda b, d: None, start_mouse=False)
        self.runtime.mouse_thread = self.mouse_thread

    # =========================================================================
    # AREA 1: 360° Continuous Stick Sweeps & Outer Rim Threshold Verification
    # =========================================================================
    def test_360_degree_sweep_sub_threshold_strict_zero_outer(self):
        """At any radius below outer_press (0.85), NO outer ring trigger may ever activate across 360°."""
        sub_radii = [0.0, 0.25, 0.50, 0.70, 0.80, 0.84, 0.8499]
        angle_steps = 720  # 0.5-degree resolution

        for r in sub_radii:
            for i in range(angle_steps):
                theta = 2.0 * math.pi * i / angle_steps
                x = r * math.cos(theta)
                y = -r * math.sin(theta)  # Gamepad Y is inverted (negative is UP)
                state = {'buttons': [], 'axes': [x, y, 0.0, 0.0, 0.0, 0.0]}
                norm = InputNormalizer()
                active = norm.update(state)

                # Outer triggers must NEVER be active
                self.assertNotIn('LS:outer', active, f"LS:outer activated at sub-threshold radius {r} at theta={math.degrees(theta):.1f}°")
                for d in ('up', 'down', 'left', 'right'):
                    self.assertNotIn(f'LS:{d}:outer', active, f"LS:{d}:outer activated at sub-threshold radius {r} at theta={math.degrees(theta):.1f}°")

    def test_360_degree_sweep_outer_rim_continuity_and_no_dropouts(self):
        """At outer rim (radius 1.0), every angle must have LS:outer active, and at least one directional outer active without drops."""
        angle_steps = 720  # 0.5-degree steps
        outer_counts = []

        for i in range(angle_steps):
            theta = 2.0 * math.pi * i / angle_steps
            x = math.cos(theta)
            y = -math.sin(theta)
            state = {'buttons': [], 'axes': [x, y, 0.0, 0.0, 0.0, 0.0]}
            norm = InputNormalizer()
            active = norm.update(state)

            self.assertIn('LS:outer', active, f"LS:outer missing at rim radius 1.0 at theta={math.degrees(theta):.1f}°")
            dir_outers = [d for d in ('up', 'down', 'left', 'right') if f'LS:{d}:outer' in active]
            self.assertGreaterEqual(len(dir_outers), 1, f"Dropout detected! No directional outer active at theta={math.degrees(theta):.1f}°")
            self.assertLessEqual(len(dir_outers), 2, f"More than 2 directional outers active at theta={math.degrees(theta):.1f}°: {dir_outers}")
            outer_counts.append(len(dir_outers))

        # Check diagonal overlaps: 4 diagonal sectors must have exactly 2 outers active
        dual_outer_count = sum(1 for c in outer_counts if c == 2)
        single_outer_count = sum(1 for c in outer_counts if c == 1)
        self.assertGreater(dual_outer_count, 0, "No dual diagonal activations observed")
        self.assertGreater(single_outer_count, 0, "No single cardinal activations observed")

    def test_45_degree_diagonal_outer_rim_activation(self):
        """At 45° diagonal outer rim, individual components are 0.7071 < 0.85, but radial projection triggers both outers."""
        diagonals = [
            (45.0, {'LS:right:outer', 'LS:up:outer'}, 'Up-Right'),
            (135.0, {'LS:left:outer', 'LS:up:outer'}, 'Up-Left'),
            (225.0, {'LS:left:outer', 'LS:down:outer'}, 'Down-Left'),
            (315.0, {'LS:right:outer', 'LS:down:outer'}, 'Down-Right'),
        ]

        for deg, expected_outers, name in diagonals:
            rad = math.radians(deg)
            x = math.cos(rad)
            y = -math.sin(rad)
            # Verify individual component is strictly less than 0.85
            self.assertLess(abs(x), 0.85, f"Component x for {name} is unexpectedly >= 0.85")
            self.assertLess(abs(y), 0.85, f"Component y for {name} is unexpectedly >= 0.85")
            self.assertAlmostEqual(math.hypot(x, y), 1.0, places=5)

            state = {'buttons': [], 'axes': [x, y, 0.0, 0.0, 0.0, 0.0]}
            norm = InputNormalizer()
            active = norm.update(state)

            self.assertIn('LS:outer', active)
            for expected in expected_outers:
                self.assertIn(expected, active, f"Expected {expected} at {name} (deg={deg}), active={active}")

    def test_outer_rim_hysteresis_boundary_stability(self):
        """Oscillating across the 0.85 outer_press boundary must demonstrate hysteresis stability (release at default 0.75)."""
        norm = InputNormalizer()
        # Move up to 0.849: not active
        state_pre = {'buttons': [], 'axes': [0.0, -0.849, 0.0, 0.0, 0.0, 0.0]}
        active_pre = norm.update(state_pre)
        self.assertNotIn('LS:up:outer', active_pre)

        # Cross 0.851: becomes active
        state_cross = {'buttons': [], 'axes': [0.0, -0.851, 0.0, 0.0, 0.0, 0.0]}
        active_cross = norm.update(state_cross)
        self.assertIn('LS:up:outer', active_cross)

        # Oscillate between 0.849 and 0.760: MUST REMAIN ACTIVE due to outer_release (0.75)
        for val in [0.849, 0.820, 0.780, 0.760]:
            state_hold = {'buttons': [], 'axes': [0.0, -val, 0.0, 0.0, 0.0, 0.0]}
            active_hold = norm.update(state_hold)
            self.assertIn('LS:up:outer', active_hold, f"Premature drop of LS:up:outer at y=-{val} (hysteresis release is 0.75)")

        # Drop below 0.75 (0.740): MUST DEACTIVATE
        state_rel = {'buttons': [], 'axes': [0.0, -0.740, 0.0, 0.0, 0.0, 0.0]}
        active_rel = norm.update(state_rel)
        self.assertNotIn('LS:up:outer', active_rel, "LS:up:outer failed to release when dropping below outer_release threshold (0.75)")

    # =========================================================================
    # AREA 2: Zero WASD Leakage during stick_source == 'left' Mouse Steering
    # =========================================================================
    def test_zero_wasd_leakage_under_high_frequency_packet_stream(self):
        """Under a continuous high-frequency stream of 5,000 packets with stick_source='left', WASD must NEVER be emitted."""
        config = {
            'active_profile': 'kbm_profile',
            'profiles': {
                'kbm_profile': {
                    'LS:up': {'short': {'action': 'hold', 'value': 'W'}, 'long': {'action': 'none'}},
                    'LS:down': {'short': {'action': 'hold', 'value': 'S'}, 'long': {'action': 'none'}},
                    'LS:left': {'short': {'action': 'hold', 'value': 'A'}, 'long': {'action': 'none'}},
                    'LS:right': {'short': {'action': 'hold', 'value': 'D'}, 'long': {'action': 'none'}},
                }
            },
            'profile_options': {
                'kbm_profile': {
                    'stick_mouse_enabled': True,
                    'mouse': {'mode': 'game', 'stick_source': 'left'},
                }
            },
            'profile_modes': {'kbm_profile': 'kbm'},
        }

        rng = random.Random(42)
        total_packets = 5000
        for i in range(total_packets):
            # Generate vigorous left stick deflections across all 4 quadrants
            mag = rng.uniform(0.1, 1.0)
            angle = rng.uniform(0.0, 2.0 * math.pi)
            x = mag * math.cos(angle)
            y = mag * math.sin(angle)
            state = {
                'family': 'xbox',
                'device_key': 'test:adversarial',
                'buttons': [],
                'axes': [x, y, 0.0, 0.0, 0.0, 0.0],
                'touch_fingers': [],
            }
            self.runtime.update(state, config, enabled=True, now=1.0 + i * 0.005)

        # Assert zero WASD in held keys
        wasd = {'W', 'A', 'S', 'D'}
        self.assertTrue(wasd.isdisjoint(self.actions.held_keys), f"Leaked held WASD keys: {wasd.intersection(self.actions.held_keys)}")

        # Assert zero WASD events ever registered
        wasd_events = [ev for ev in self.actions.os_events if ev[0] == 'key' and ev[1] in wasd]
        self.assertEqual(len(wasd_events), 0, f"Leaked {len(wasd_events)} WASD hardware key events: {wasd_events[:10]}")

        # Assert mouse updates were received
        self.assertEqual(len(self.mouse_thread.stick_updates), total_packets)

    def test_rapid_stick_source_toggling_no_stuck_keys(self):
        """Rapidly switching stick_source between 'left' and 'right' while deflected must cleanly release WASD."""
        config = {
            'active_profile': 'p',
            'profiles': {
                'p': {
                    'LS:up': {'short': {'action': 'hold', 'value': 'W'}, 'long': {'action': 'none'}},
                    'LS:right': {'short': {'action': 'hold', 'value': 'D'}, 'long': {'action': 'none'}},
                }
            },
            'profile_options': {
                'p': {
                    'stick_mouse_enabled': True,
                    'mouse': {'mode': 'game', 'stick_source': 'right'},
                }
            },
            'profile_modes': {'p': 'kbm'},
        }

        state_deflected = {
            'family': 'xbox',
            'device_key': 'test:dev',
            'buttons': [],
            'axes': [0.8, -0.8, 0.0, 0.0, 0.0, 0.0],
            'touch_fingers': [],
        }
        state_center = dict(state_deflected, axes=[0.0] * 6)

        # Step 1: stick_source='right' -> WASD active
        self.runtime.update(state_deflected, config, enabled=True, now=1.0)
        self.assertIn('W', self.actions.held_keys)
        self.assertIn('D', self.actions.held_keys)

        # Step 2: switch stick_source='left' -> WASD must be suppressed immediately
        config['profile_options']['p']['mouse']['stick_source'] = 'left'
        self.runtime.update(state_deflected, config, enabled=True, now=1.02)
        self.assertNotIn('W', self.actions.held_keys, "W remained held after switching stick_source to 'left'")
        self.assertNotIn('D', self.actions.held_keys, "D remained held after switching stick_source to 'left'")

        # Step 3: return stick to center and switch back to 'right'
        config['profile_options']['p']['mouse']['stick_source'] = 'right'
        self.runtime.update(state_center, config, enabled=True, now=1.04)
        self.runtime.update(state_center, config, enabled=True, now=1.05)  # neutral polling frame

        # Step 4: re-deflect -> WASD restored cleanly
        self.runtime.update(state_deflected, config, enabled=True, now=1.06)
        self.assertIn('W', self.actions.held_keys)
        self.assertIn('D', self.actions.held_keys)

        # Step 5: release stick -> cleanly released
        self.runtime.update(state_center, config, enabled=True, now=1.08)
        self.assertNotIn('W', self.actions.held_keys)
        self.assertNotIn('D', self.actions.held_keys)

    # =========================================================================
    # AREA 3: double_tap_hold Pulse Verification with Overlapping Holds
    # =========================================================================
    def test_double_tap_hold_overlapping_directional_hold_produces_genuine_pulse(self):
        """When double_tap_hold targets 'W' while 'W' is already held by LS:up, OS must receive explicit KeyUp and KeyDown pulses."""
        config = {
            'active_profile': 'p',
            'profiles': {
                'p': {
                    'LS:up': {'short': {'action': 'hold', 'value': 'W'}, 'long': {'action': 'none'}},
                    '0': {'short': {'action': 'hold', 'mode': 'double_tap_hold', 'value': 'W'}, 'long': {'action': 'none'}},
                }
            },
            'profile_options': {'p': {}},
            'profile_modes': {'p': 'kbm'},
        }

        # Phase 1: Player moves stick forward (LS:up -> hold 'W')
        t = 1.0
        state = {'family': 'xbox', 'device_key': 'test:dev', 'buttons': [], 'axes': [0.0, -0.9, 0, 0, 0, 0], 'touch_fingers': []}
        self.runtime.update(state, config, enabled=True, now=t)
        self.assertIn('W', self.actions.held_keys)

        # Record events prior to double-tap
        events_start = list(self.actions.os_events)
        self.assertEqual(events_start[-1], ('key', 'W', True, False))

        # Phase 2: Player presses button 0 (trigger double-tap-hold on 'W')
        t += 0.01
        state_btn = dict(state, buttons=[0])
        self.runtime.update(state_btn, config, enabled=True, now=t)

        # Phase 3: Advance time to t_up (t + 0.045) -> force KeyUp pulse
        t += 0.05
        self.runtime.update(state_btn, config, enabled=True, now=t)
        events_after_up = [ev for ev in self.actions.os_events[len(events_start):] if ev[1] == 'W']
        # Must contain a forced KeyUp!
        forced_ups = [ev for ev in events_after_up if ev[2] is False and ev[3] is True]
        self.assertGreaterEqual(len(forced_ups), 1, f"Missing forced KeyUp pulse! Events: {events_after_up}")

        # Phase 4: Advance time to t_hold (t + 0.05) -> force KeyDown pulse
        t += 0.05
        self.runtime.update(state_btn, config, enabled=True, now=t)
        events_after_hold = [ev for ev in self.actions.os_events[len(events_start):] if ev[1] == 'W']
        forced_downs = [ev for ev in events_after_hold if ev[2] is True and ev[3] is True]
        self.assertGreaterEqual(len(forced_downs), 1, f"Missing forced KeyDown pulse! Events: {events_after_hold}")

        # Phase 5: Release button 0 while still moving forward
        t += 0.10
        state_rel_btn = dict(state, buttons=[])
        self.runtime.update(state_rel_btn, config, enabled=True, now=t)
        # 'W' must STILL be held because LS:up is still active!
        self.assertIn('W', self.actions.held_keys, "Directional hold 'W' prematurely dropped when double-tap released")

        # Phase 6: Release stick
        t += 0.05
        state_rel_stick = dict(state, buttons=[], axes=[0.0] * 6)
        self.runtime.update(state_rel_stick, config, enabled=True, now=t)
        # 'W' must now be cleanly released
        self.assertNotIn('W', self.actions.held_keys, "'W' stuck after releasing both inputs")

    def test_double_tap_hold_quick_release_completes_pulse_without_sticking(self):
        """Releasing double_tap_hold within 10ms (before step 1 or step 2) must complete the pulse and release cleanly."""
        config = {
            'active_profile': 'p',
            'profiles': {
                'p': {
                    '0': {'short': {'action': 'hold', 'mode': 'double_tap_hold', 'value': 'F'}, 'long': {'action': 'none'}},
                }
            },
            'profile_options': {'p': {}},
            'profile_modes': {'p': 'kbm'},
        }

        t = 10.0
        state_press = {'family': 'xbox', 'device_key': 'test:dev', 'buttons': [0], 'axes': [0.0] * 6, 'touch_fingers': []}
        self.runtime.update(state_press, config, enabled=True, now=t)
        self.assertIn('F', self.actions.held_keys)

        # Quick release 10ms later (well before t_up 45ms and t_hold 90ms)
        t += 0.010
        state_release = dict(state_press, buttons=[])
        self.runtime.update(state_release, config, enabled=True, now=t)

        # Step through time past t_up (45ms), t_hold (90ms), and t_release (+65ms)
        for dt in [0.040, 0.050, 0.070, 0.050]:
            t += dt
            self.runtime.update(state_release, config, enabled=True, now=t)

        # Verify key is completely released and binding removed
        self.assertNotIn('F', self.actions.held_keys, "Key 'F' stuck down after quick-release double-tap-hold")
        self.assertEqual(len(self.runtime.double_tap_bindings), 0, "Dangling double_tap_bindings remained")

    # =========================================================================
    # AREA 4: Turbo Repeat State Machine under Severe Frame Jitter & Switches
    # =========================================================================
    def test_turbo_repeat_under_severe_frame_jitter(self):
        """Simulating 1,000 ticks of severe frame jitter (dt randomly spanning 1ms to 120ms): no burst dumping or stuck keys."""
        config = {
            'active_profile': 'p',
            'profiles': {
                'p': {
                    '0': {'short': {'action': 'hold', 'mode': 'turbo', 'value': 'Space', 'rate_hz': 20}, 'long': {'action': 'none'}},
                }
            },
            'profile_options': {'p': {}},
            'profile_modes': {'p': 'kbm'},
        }

        rng = random.Random(1337)
        t = 50.0
        state = {'family': 'xbox', 'device_key': 'test:dev', 'buttons': [0], 'axes': [0.0] * 6, 'touch_fingers': []}

        # Run 1,000 ticks under extreme jitter
        for _ in range(1000):
            # Frame time varies between 1ms (super fast) and 120ms (severe drop)
            dt = rng.choice([0.001, 0.005, 0.016, 0.033, 0.080, 0.120])
            t += dt
            events_before = len(self.actions.os_events)
            self.runtime.update(state, config, enabled=True, now=t)
            events_after = len(self.actions.os_events)
            # Toggles per tick must NEVER exceed 1 (strictly clamped to eliminate burst aliasing)
            self.assertLessEqual(events_after - events_before, 1, f"Burst dumping detected: {events_after - events_before} toggles in one tick")

        # Now release the button
        t += 0.016
        state_released = dict(state, buttons=[])
        self.runtime.update(state_released, config, enabled=True, now=t)

        # Space must be released immediately!
        self.assertNotIn('Space', self.actions.held_keys, "Turbo Space key remained stuck after button release")
        self.assertEqual(len(self.runtime.turbo_bindings), 0)

    def test_turbo_profile_switch_cleanup(self):
        """Switching profile while turbo is actively held down must immediately release the key."""
        config = {
            'active_profile': 'turbo_prof',
            'profiles': {
                'turbo_prof': {
                    '0': {'short': {'action': 'hold', 'mode': 'turbo', 'value': 'J', 'rate_hz': 15}, 'long': {'action': 'none'}},
                },
                'idle_prof': {
                    '0': {'short': {'action': 'none'}, 'long': {'action': 'none'}},
                }
            },
            'profile_options': {'turbo_prof': {}, 'idle_prof': {}},
            'profile_modes': {'turbo_prof': 'kbm', 'idle_prof': 'kbm'},
        }

        t = 100.0
        state = {'family': 'xbox', 'device_key': 'test:dev', 'buttons': [0], 'axes': [0.0] * 6, 'touch_fingers': []}
        self.runtime.update(state, config, enabled=True, now=t)
        self.assertIn('J', self.actions.held_keys)

        # Switch active_profile to 'idle_prof'
        config['active_profile'] = 'idle_prof'
        t += 0.02
        self.runtime.update(state, config, enabled=True, now=t)

        # Key 'J' must be released
        self.assertNotIn('J', self.actions.held_keys, "Turbo key 'J' stuck after switching profile")

    # =========================================================================
    # AREA 5: Advanced Adversarial Edge Cases & Boundary Conditions
    # =========================================================================
    def test_outer_ring_custom_directional_thresholds(self):
        """Custom directional outer thresholds (e.g. outer_up_press=0.70) must correctly override defaults."""
        custom_settings = {
            'outer_press': 0.85,
            'outer_release': 0.75,
            'outer_up_press': 0.70,
            'outer_up_release': 0.65,
        }
        norm = InputNormalizer()
        # Deflect UP to 0.72: greater than outer_up_press (0.70) but less than generic outer_press (0.85)
        state = {'buttons': [], 'axes': [0.0, -0.72, 0.0, 0.0, 0.0, 0.0]}
        active = norm.update(state, custom_settings)

        self.assertIn('LS:up:outer', active, "Custom directional outer_up_press (0.70) failed to activate at y=-0.72")
        self.assertNotIn('LS:outer', active, "Generic LS:outer should not activate at 0.72 < 0.85")

    def test_double_tap_hold_triple_overlap_refcounts(self):
        """Three separate inputs holding key 'W' (directional LS:up, regular hold, double_tap_hold).
        Releasing inputs in arbitrary order must never leave 'W' stuck or drop it prematurely."""
        config = {
            'active_profile': 'p',
            'profiles': {
                'p': {
                    'LS:up': {'short': {'action': 'hold', 'value': 'W'}, 'long': {'action': 'none'}},
                    '0': {'short': {'action': 'hold', 'mode': 'double_tap_hold', 'value': 'W'}, 'long': {'action': 'none'}},
                    '1': {'short': {'action': 'hold', 'value': 'W'}, 'long': {'action': 'none'}},
                }
            },
            'profile_options': {'p': {}},
            'profile_modes': {'p': 'kbm'},
        }

        # Step 1: LS:up forward -> W held
        t = 1.0
        s1 = {'family': 'xbox', 'device_key': 'test:dev', 'buttons': [], 'axes': [0.0, -0.9, 0, 0, 0, 0], 'touch_fingers': []}
        self.runtime.update(s1, config, enabled=True, now=t)
        self.assertIn('W', self.actions.held_keys)

        # Step 2: button 1 held -> W refcount increments
        t += 0.01
        s2 = dict(s1, buttons=[1])
        self.runtime.update(s2, config, enabled=True, now=t)
        self.assertIn('W', self.actions.held_keys)

        # Step 3: button 0 double-tap pressed
        t += 0.01
        s3 = dict(s1, buttons=[0, 1])
        self.runtime.update(s3, config, enabled=True, now=t)

        # Step 4: Advance past double-tap pulses
        t += 0.05
        self.runtime.update(s3, config, enabled=True, now=t)
        t += 0.05
        self.runtime.update(s3, config, enabled=True, now=t)
        self.assertIn('W', self.actions.held_keys)

        # Step 5: Release LS:up (stick to center) -> W must STAY held by buttons 0 and 1
        t += 0.02
        s4 = {'family': 'xbox', 'device_key': 'test:dev', 'buttons': [0, 1], 'axes': [0.0] * 6, 'touch_fingers': []}
        self.runtime.update(s4, config, enabled=True, now=t)
        self.assertIn('W', self.actions.held_keys, "W dropped prematurely while buttons 0 and 1 are still held")

        # Step 6: Release button 0 -> W must STAY held by button 1
        t += 0.02
        s5 = dict(s4, buttons=[1])
        self.runtime.update(s5, config, enabled=True, now=t)
        self.assertIn('W', self.actions.held_keys, "W dropped prematurely while button 1 is still held")

        # Step 7: Release button 1 -> W must be cleanly released!
        t += 0.02
        s6 = dict(s4, buttons=[])
        self.runtime.update(s6, config, enabled=True, now=t)
        self.assertNotIn('W', self.actions.held_keys, "W remained stuck after releasing all inputs")

    def test_turbo_multi_frequency_cadence(self):
        """Turbo repeat rates (10Hz, 20Hz, 30Hz) must alternate cleanly with approximately 50% duty cycle."""
        rates = [10, 20, 30]
        for rate in rates:
            actions = MockActionsWithRefcounts()
            runtime = MappingRuntime(actions, lambda b, d: None, start_mouse=False)
            config = {
                'active_profile': 'p',
                'profiles': {
                    'p': {'0': {'short': {'action': 'hold', 'mode': 'turbo', 'value': 'X', 'rate_hz': rate}, 'long': {'action': 'none'}}}
                },
                'profile_options': {'p': {}},
                'profile_modes': {'p': 'kbm'},
            }
            state = {'family': 'xbox', 'device_key': 'test:dev', 'buttons': [0], 'axes': [0.0] * 6, 'touch_fingers': []}

            # Simulate 1.0 second at 120Hz polling (dt = 0.00833s)
            t = 200.0
            ticks = 120
            dt = 1.0 / ticks
            toggles = 0
            for _ in range(ticks):
                t += dt
                before = len(actions.os_events)
                runtime.update(state, config, enabled=True, now=t)
                toggles += (len(actions.os_events) - before)

            # In 1 second at `rate` Hz, total toggles (KeyDown + KeyUp) should be ~ 2 * rate
            expected_toggles = 2 * rate
            self.assertAlmostEqual(toggles, expected_toggles, delta=3,
                                   msg=f"Rate {rate}Hz yielded {toggles} toggles in 1s, expected ~{expected_toggles}")

            # Release
            runtime.update(dict(state, buttons=[]), config, enabled=True, now=t + dt)
            self.assertNotIn('X', actions.held_keys)

    def test_stick_source_left_preserves_unrelated_inputs(self):
        """When stick_source == 'left', only LS directional WASD is suppressed; outer ring (Shift) and buttons work."""
        config = {
            'active_profile': 'p',
            'profiles': {
                'p': {
                    'LS:up': {'short': {'action': 'hold', 'value': 'W'}, 'long': {'action': 'none'}},
                    'LS:outer': {'short': {'action': 'hold', 'value': 'Shift'}, 'long': {'action': 'none'}},
                    '0': {'short': {'action': 'hold', 'value': 'Space'}, 'long': {'action': 'none'}},
                }
            },
            'profile_options': {
                'p': {
                    'stick_mouse_enabled': True,
                    'mouse': {'mode': 'game', 'stick_source': 'left'},
                }
            },
            'profile_modes': {'p': 'kbm'},
        }

        # Push stick fully forward (0.0, -1.0) and press button 0
        state = {'family': 'xbox', 'device_key': 'test:dev', 'buttons': [0], 'axes': [0.0, -1.0, 0, 0, 0, 0], 'touch_fingers': []}
        self.runtime.update(state, config, enabled=True, now=1.0)

        # W must be suppressed
        self.assertNotIn('W', self.actions.held_keys, "WASD leaked during stick_source='left'")
        # But Shift (LS:outer) and Space (button 0) must be active!
        self.assertIn('Shift', self.actions.held_keys, "LS:outer (Shift) was improperly suppressed")
        self.assertIn('Space', self.actions.held_keys, "Button 0 (Space) was improperly suppressed")

    def test_input_normalizer_extreme_deflections_and_subnormals(self):
        """InputNormalizer must handle extreme axis values (+10.0, -5.0, 1e-15, 0.0) without crashing."""
        norm = InputNormalizer()
        extremes = [
            [10.0, 10.0, 0.0, 0.0, 0.0, 0.0],
            [-5.0, -5.0, 0.0, 0.0, 0.0, 0.0],
            [1e-15, -1e-15, 0.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        ]
        for axes in extremes:
            active = norm.update({'buttons': [], 'axes': axes})
            self.assertIsInstance(active, set)

    def test_mapping_runtime_reset_clean_teardown_all_active(self):
        """Calling reset() while turbo, double-tap, pulses, and held buttons are all simultaneously active must release all keys."""
        config = {
            'active_profile': 'p',
            'profiles': {
                'p': {
                    '0': {'short': {'action': 'hold', 'mode': 'turbo', 'value': 'Q', 'rate_hz': 15}, 'long': {'action': 'none'}},
                    '1': {'short': {'action': 'hold', 'mode': 'double_tap_hold', 'value': 'E'}, 'long': {'action': 'none'}},
                    '2': {'short': {'action': 'shortcut', 'value': 'R'}, 'long': {'action': 'none'}},
                    '3': {'short': {'action': 'hold', 'value': 'T'}, 'long': {'action': 'none'}},
                }
            },
            'profile_options': {'p': {}},
            'profile_modes': {'p': 'kbm'},
        }

        # Press all 4 buttons
        state = {'family': 'xbox', 'device_key': 'test:dev', 'buttons': [0, 1, 2, 3], 'axes': [0.0] * 6, 'touch_fingers': []}
        self.runtime.update(state, config, enabled=True, now=1.0)

        # Trigger reset
        self.runtime.reset()

        # All keys must be completely cleared and state machines empty
        self.assertEqual(len(self.actions.held_keys), 0, f"Stuck keys after reset(): {self.actions.held_keys}")
        self.assertEqual(len(self.runtime.turbo_bindings), 0)
        self.assertEqual(len(self.runtime.double_tap_bindings), 0)
        self.assertEqual(len(self.runtime.pulses), 0)


if __name__ == '__main__':
    unittest.main()


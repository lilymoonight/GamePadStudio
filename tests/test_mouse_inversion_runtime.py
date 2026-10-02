"""Run the real mouse loop against deterministic clocks and captured output."""
from types import SimpleNamespace
import threading

import pytest

from gamepadstudio import virtual_kbm
from gamepadstudio.mapping_engine import MappingRuntime


class Actions:
    def __init__(self):
        self.moves = []
        self.guards = []
        self.tick = 0

    def move_mouse(self, dx, dy):
        self.moves.append((self.tick, dx, dy))

    def guard_cursor_edge(self):
        self.guards.append(self.tick)


def run_loop(monkeypatch, mouse, actions, frames=50, hooks=None):
    hooks = hooks or {}
    elapsed = 0.
    tick = -1
    timer = []
    def clock():
        nonlocal elapsed, tick
        tick += 1; elapsed += .003; actions.tick = tick
        if tick in hooks:
            hooks[tick]()
        if tick > frames:
            mouse.stop()
        return elapsed
    monkeypatch.setattr(virtual_kbm, 'time', SimpleNamespace(perf_counter=clock, sleep=lambda _: None))
    monkeypatch.setattr(virtual_kbm, 'set_system_timer_resolution', lambda enabled: timer.append(enabled))
    monkeypatch.setattr('gamepadstudio.actions.attach_to_default_desktop', lambda: None)
    mouse.run()
    assert timer == [True, False]
    assert mouse.acc_x == mouse.acc_y == mouse.outer_hold_time == 0.
    return list(actions.moves)


def configured_mouse(invert=False, desktop=False, stick=(.6, .6)):
    actions = Actions(); mouse = virtual_kbm.VirtualMouseThread(actions)
    mouse.configure({'invert_y': invert, 'sensitivity': 20., 'deadzone': .06,
                     'y_ratio': .7, 'edge_boost': 1.})
    mouse.update_stick(*stick, is_desktop=desktop)
    return mouse, actions


@pytest.mark.parametrize('invert', [False, True])
@pytest.mark.parametrize('ry', [-.6, .6])
def test_game_camera_moves_in_the_actual_selected_y_direction(monkeypatch, invert, ry):
    mouse, actions = configured_mouse(invert=invert, stick=(.6, ry))
    moves = run_loop(monkeypatch, mouse, actions)
    assert moves and all(dx > 0 for _, dx, _ in moves)
    expected_sign = (-1 if invert else 1) * (1 if ry > 0 else -1)
    assert all(dy * expected_sign > 0 for _, _, dy in moves)
    assert actions.guards


def test_inversion_keeps_x_and_magnitude_identical(monkeypatch):
    ordinary, ordinary_actions = configured_mouse()
    normal = run_loop(monkeypatch, ordinary, ordinary_actions)
    inverted, inverted_actions = configured_mouse(invert=True)
    reversed_moves = run_loop(monkeypatch, inverted, inverted_actions)
    assert [(t, dx, -dy) for t, dx, dy in normal] == reversed_moves


@pytest.mark.parametrize('value', [None, False, 0, 1, 'true', 'false', [], {}])
def test_inversion_requires_literal_true_and_other_values_default_off(monkeypatch, value):
    mouse, actions = configured_mouse(invert=value)
    assert mouse.invert_y is False
    moves = run_loop(monkeypatch, mouse, actions)
    assert moves and all(dy > 0 for _, _, dy in moves)


def test_default_and_omitted_configuration_are_not_inverted(monkeypatch):
    actions = Actions(); mouse = virtual_kbm.VirtualMouseThread(actions)
    assert mouse.invert_y is False
    mouse.configure({'invert_y': True}); assert mouse.invert_y is True
    mouse.configure({}); assert mouse.invert_y is False
    mouse.update_stick(0., .8)
    moves = run_loop(monkeypatch, mouse, actions)
    assert moves and all(dx == 0 and dy > 0 for _, dx, dy in moves)


def test_desktop_motion_is_identical_with_or_without_game_inversion(monkeypatch):
    normal, normal_actions = configured_mouse(desktop=True)
    ordinary = run_loop(monkeypatch, normal, normal_actions)
    inverted, inverted_actions = configured_mouse(invert=True, desktop=True)
    assert run_loop(monkeypatch, inverted, inverted_actions) == ordinary
    assert normal_actions.guards == inverted_actions.guards == []


def test_inversion_change_discards_fractional_motion_before_next_frame(monkeypatch):
    mouse, actions = configured_mouse(stick=(0., .12))
    observed = []
    def invert():
        assert 0. < mouse.acc_y < 1.
        mouse.configure({'invert_y': True, 'sensitivity': 20., 'deadzone': .06,
                         'y_ratio': .7, 'edge_boost': 1.})
        observed.append((mouse.acc_x, mouse.acc_y, mouse.outer_hold_time))
    moves = run_loop(monkeypatch, mouse, actions, frames=100, hooks={12: invert})
    assert observed == [(0., 0., 0.)]
    assert moves and all(dy < 0 for tick, _, dy in moves if tick >= 12)


def test_switching_to_desktop_discards_inverted_fractional_remainder(monkeypatch):
    mouse, actions = configured_mouse(invert=True, stick=(0., .12))
    observed = []
    def desktop():
        assert -1. < mouse.acc_y < 0.
        mouse.update_stick(0., .12, is_desktop=True)
        observed.append((mouse.acc_x, mouse.acc_y))
    moves = run_loop(monkeypatch, mouse, actions, frames=100, hooks={12: desktop})
    assert observed == [(0., 0.)]
    assert moves and all(dy > 0 for tick, _, dy in moves if tick >= 12)


def test_neutral_input_stops_immediately_and_clears_all_carry(monkeypatch):
    mouse, actions = configured_mouse(invert=True)
    observed = []
    def neutral():
        mouse.update_stick(0., 0.)
        observed.append((mouse.acc_x, mouse.acc_y, mouse.outer_hold_time))
    moves = run_loop(monkeypatch, mouse, actions, hooks={12: neutral})
    assert moves and all(tick < 12 for tick, _, _ in moves)
    assert observed == [(0., 0., 0.)]


def test_stop_prevents_the_current_clock_tick_from_sending_old_motion(monkeypatch):
    mouse, actions = configured_mouse(invert=True)
    moves = run_loop(monkeypatch, mouse, actions, hooks={12: mouse.stop})
    assert moves and all(tick < 12 for tick, _, _ in moves)
    assert mouse.stick_x == mouse.stick_y == 0.


def test_configuration_changes_reset_boost_and_carry_but_noop_keeps_carry():
    mouse, _ = configured_mouse(invert=True)
    mouse.acc_x, mouse.acc_y, mouse.outer_hold_time = .3, -.4, .2
    mouse.configure({'invert_y': True, 'sensitivity': 20., 'deadzone': .06,
                     'y_ratio': .7, 'edge_boost': 1.})
    assert (mouse.acc_x, mouse.acc_y, mouse.outer_hold_time) == (.3, -.4, .2)
    mouse.configure({'invert_y': True, 'sensitivity': 25., 'deadzone': .06,
                     'y_ratio': .7, 'edge_boost': 1.})
    assert mouse.acc_x == mouse.acc_y == mouse.outer_hold_time == 0.


@pytest.mark.parametrize('desktop,stick,expected', [(False, .20, True), (True, .20, False),
                                                   (True, .45, True), (False, .04, False)])
def test_click_lock_and_deadzone_keep_their_existing_behavior(monkeypatch, desktop, stick, expected):
    mouse, actions = configured_mouse(invert=True, desktop=desktop, stick=(0., stick))
    mouse.set_click_lock(True)
    moves = run_loop(monkeypatch, mouse, actions)
    assert bool(moves) is expected
    if moves:
        assert all(dy > 0 if desktop else dy < 0 for _, _, dy in moves)


def test_mapping_runtime_reset_releases_mouse_motion_and_fractional_inversion(monkeypatch):
    actions = Actions(); mouse = virtual_kbm.VirtualMouseThread(actions)
    runtime = MappingRuntime(actions, lambda *_: None, start_mouse=False)
    runtime.mouse_thread = mouse
    mouse.configure({'invert_y': True}); mouse.update_stick(.6, .6)
    def paused():
        runtime.reset()
        assert mouse.stick_x == mouse.stick_y == 0.
        assert mouse.acc_x == mouse.acc_y == 0.
    moves = run_loop(monkeypatch, mouse, actions, hooks={12: paused})
    assert moves and all(tick < 12 for tick, _, _ in moves)
    assert not runtime.feedback()['outputs']


def test_actual_send_and_configuration_use_the_same_motion_lock(monkeypatch):
    entered, release, configured = threading.Event(), threading.Event(), threading.Event()
    class BlockingActions:
        def move_mouse(self, _dx, _dy):
            entered.set()
            assert release.wait(1.)
    mouse = virtual_kbm.VirtualMouseThread(BlockingActions())
    mouse.update_stick(.6, .6)
    ticks = 0
    def clock():
        nonlocal ticks
        ticks += 1
        if ticks >= 3:
            assert configured.wait(1.)
            mouse.stop()
        return ticks * .003
    monkeypatch.setattr(virtual_kbm, 'time', SimpleNamespace(perf_counter=clock, sleep=lambda _: None))
    monkeypatch.setattr(virtual_kbm, 'set_system_timer_resolution', lambda _: None)
    monkeypatch.setattr('gamepadstudio.actions.attach_to_default_desktop', lambda: None)
    def configure():
        mouse.configure({'invert_y': True}); configured.set()
    setting = threading.Thread(target=configure)
    mouse.start(); assert entered.wait(1.); setting.start()
    try:
        assert not configured.wait(.03)
        release.set(); assert configured.wait(1.)
    finally:
        release.set(); configured.set(); mouse.stop(); mouse.join(1.); setting.join(1.)
    assert not mouse.is_alive()
    assert mouse.invert_y is True

"""Mouse worker failures pause their owner; no test posts native input."""
from types import SimpleNamespace

import pytest

from gamepadstudio import virtual_kbm
from gamepadstudio.mapping_engine import MappingRuntime


class RefusedMouse:
    def __init__(self, refusal='move'):
        self.refusal = refusal
        self.attempts = []
        self.guards = 0
        self.releases = 0

    def move_mouse(self, dx, dy):
        self.attempts.append((dx, dy))
        if self.refusal == 'move':
            raise PermissionError('请授权辅助功能')

    def guard_cursor_edge(self):
        self.guards += 1
        if self.refusal == 'guard':
            raise OSError('鼠标回中被拒绝')

    def is_nikki_game_focused(self):
        return self.refusal == 'guard'

    def release_all(self):
        self.releases += 1


def run_worker(monkeypatch, mouse, frames=20, hooks=None):
    """Advance the real loop deterministically without starting a thread."""
    tick = -1
    def clock():
        nonlocal tick
        tick += 1
        if hooks and tick in hooks:
            hooks[tick]()
        if tick > frames:
            mouse.stop()
        return tick * .003
    monkeypatch.setattr(virtual_kbm, 'time', SimpleNamespace(perf_counter=clock, sleep=lambda _: None))
    monkeypatch.setattr(virtual_kbm, 'set_system_timer_resolution', lambda enabled: None)
    monkeypatch.setattr('gamepadstudio.actions.attach_to_default_desktop', lambda: None)
    mouse.run()


@pytest.mark.parametrize('refusal', ['move', 'guard'])
def test_worker_reports_once_and_stops_repeated_output_until_reset(monkeypatch, refusal):
    actions = RefusedMouse(refusal)
    mouse = virtual_kbm.VirtualMouseThread(actions)
    mouse.update_stick(.8, .5, is_desktop=refusal == 'move')
    observed = []
    def try_reactivate():
        mouse.update_stick(.8, .5, is_desktop=refusal == 'move')
        observed.append((mouse.stick_x, mouse.stick_y, mouse.acc_x, mouse.acc_y, mouse.outer_hold_time))
    run_worker(monkeypatch, mouse, hooks={5: try_reactivate, 10: try_reactivate})
    assert observed == [(0., 0., 0., 0., 0.)] * 2
    assert len(actions.attempts) == (1 if refusal == 'move' else 0)
    assert actions.guards == (0 if refusal == 'move' else 1)
    error = mouse.consume_output_error()
    assert isinstance(error, PermissionError if refusal == 'move' else OSError)
    assert mouse.consume_output_error() is None
    # Consuming the error alone cannot resume native output.
    mouse.update_stick(.8, .5)
    assert mouse.stick_x == mouse.stick_y == 0.
    mouse.clear_output_error()
    mouse.update_stick(.8, .5)
    assert (mouse.stick_x, mouse.stick_y) == (.8, .5)


def pointer_config():
    return {'active_profile': 'pointer', 'profiles': {'pointer': {}},
            'profile_modes': {'pointer': 'kbm'},
            'profile_options': {'pointer': {'right_stick_mouse': True, 'mouse': {'mode': 'desktop'}}}}


def state(x=0., y=0.):
    return {'buttons': [], 'touch': [], 'axes': [0., 0., x, y, 0., 0.],
            'available_axes': [0, 1, 2, 3, 4, 5], 'available_buttons': []}


def test_runtime_consumes_worker_failure_once_and_resume_requires_fresh_stick(monkeypatch):
    actions = RefusedMouse()
    runtime = MappingRuntime(actions, lambda *_: None, start_mouse=False)
    mouse = virtual_kbm.VirtualMouseThread(actions)
    runtime.mouse_thread = mouse
    config = pointer_config()
    runtime.update(state(), config)
    runtime.update(state(.8, .5), config)
    assert mouse.stick_x == .8
    run_worker(monkeypatch, mouse)
    with pytest.raises(PermissionError, match='辅助功能'):
        runtime.update(state(.8, .5), config)
    # Pause clears the worker latch and records already-held input as blocked.
    runtime.reset()
    assert mouse.consume_output_error() is None
    runtime.update(state(.8, .5), config, enabled=False)
    runtime.update(state(.8, .5), config)
    assert mouse.stick_x == mouse.stick_y == 0.
    runtime.update(state(), config)
    runtime.update(state(.8, .5), config)
    assert (mouse.stick_x, mouse.stick_y) == (.8, .5)
    runtime.mouse_thread = None  # direct run() is deliberately never started.


def test_agent_persists_pause_and_reports_right_stick_permission_failure(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from gamepadstudio.agent import Agent
    from gamepadstudio.studio_core import ConfigStore

    app = QApplication.instance() or QApplication([])
    broadcasts = []
    class LocalServer:
        def __init__(self, *args): pass
        def broadcast(self, event): broadcasts.append(event)
        def close(self): pass
    class Device:
        available = []
        def __init__(self): self.state = state()
        def scan(self): pass
        def read(self): return self.state
        def close(self): pass
    monkeypatch.setattr('gamepadstudio.agent.LocalServer', LocalServer)
    monkeypatch.setattr(Agent, 'apply_device_cloaking', lambda self: None)
    monkeypatch.setattr(Agent, 'apply_gamebar_shield', lambda self: None)
    monkeypatch.setattr(virtual_kbm.VirtualMouseThread, 'start', lambda self: None)
    device, actions = Device(), RefusedMouse()
    agent = Agent(tmp_path, device, actions)
    agent.timer.stop(); agent.scan_timer.stop(); agent.broadcast_timer.stop()
    mouse = agent.engine.mouse_thread
    try:
        # Identity-free stub leaves the explicit test profile in place.
        agent.config.update(pointer_config())
        agent.store.save()
        agent.poll()
        device.state = state(.8, .5)
        agent.poll()
        assert mouse.stick_x == .8 and agent.enabled
        run_worker(monkeypatch, mouse)
        agent.poll()
        assert not agent.enabled
        assert ConfigStore(tmp_path).data['mapping_enabled'] is False
        assert actions.releases > 0 and len(actions.attempts) == 1
        assert any(event.get('type') == 'notice' and '辅助功能' in event.get('message', '')
                   for event in broadcasts)
        agent.handle({'command': 'resume'})
        assert agent.enabled and ConfigStore(tmp_path).data['mapping_enabled'] is True
        agent.poll()
        assert mouse.stick_x == mouse.stick_y == 0.
        device.state = state(); agent.poll()
        device.state = state(.8, .5); agent.poll()
        assert (mouse.stick_x, mouse.stick_y) == (.8, .5)
    finally:
        agent.engine.mouse_thread = None  # deterministic loop never starts an OS thread.
        agent.close()

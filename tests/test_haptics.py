from pathlib import Path
import threading
import pytest
from gamepadstudio.haptic_engine import HapticEngine, ensure_shutter_sound_file
from gamepadstudio.studio_core import ConfigStore


class DummyDevice:
    def __init__(self):
        self.pulses = []
    def rumble_ext(self, low, high, dur):
        self.pulses.append((low, high, dur))
        return True
    def rumble(self, val):
        self.pulses.append((val, val, 100))
        return True


def test_ensure_shutter_sound_file(tmp_path):
    wav = tmp_path / "shutter.wav"
    ensure_shutter_sound_file(wav)
    assert wav.is_file() and wav.stat().st_size > 1000


def test_haptic_patterns():
    dev = DummyDevice()
    engine = HapticEngine(dev)
    engine.set_config(sound_enabled=False, haptics_enabled=True, intensity=0.8)

    # Shutter pattern
    engine.play_pattern("shutter")
    import time
    time.sleep(0.12)
    assert len(dev.pulses) >= 2
    dev.pulses.clear()

    # Tick pattern
    engine.play_pattern("tick")
    time.sleep(0.05)
    assert len(dev.pulses) == 1
    assert dev.pulses[0][2] == 18
    dev.pulses.clear()

    # Heartbeat pattern
    engine.play_pattern("heartbeat")
    time.sleep(0.2)
    assert len(dev.pulses) == 2
    dev.pulses.clear()

    # Disable haptics
    engine.set_config(sound_enabled=False, haptics_enabled=False)
    engine.play_pattern("impact")
    time.sleep(0.05)
    assert len(dev.pulses) == 0

    engine.close()


def test_config_store_haptics_options(tmp_path):
    store = ConfigStore(tmp_path)
    assert store.data['capture_sound_enabled'] is True
    assert store.data['capture_haptics_enabled'] is True
    assert store.data['haptic_engine_enabled'] is True
    assert store.data['haptic_profile'] == 'crisp'


class SwitchingDevice(DummyDevice):
    def __init__(self):
        super().__init__()
        self.metadata = {'device_key': 'pad:A'}
        self.instance_id = 1
        self.handle = 1

    def rumble_ext(self, low, high, dur):
        self.pulses.append((self.metadata['device_key'], low, high, dur))
        return True

    def select_other_device(self):
        self.metadata = {'device_key': 'pad:B'}
        self.instance_id = 2
        self.handle = 2


def pause_after_first_pulse(engine, monkeypatch):
    """Hold the worker between stages without relying on timing or sleeps."""
    waiting = threading.Event()
    resume = threading.Event()
    cancellation = engine._pattern_cancel

    def wait(gap):
        waiting.set()
        assert resume.wait(2), 'The test must release the paused worker'
        return cancellation.is_set()

    monkeypatch.setattr(cancellation, 'wait', wait)
    return waiting, resume


def test_old_device_tail_pulse_cannot_reach_new_device_before_config_refresh(monkeypatch):
    device = SwitchingDevice()
    engine = HapticEngine(device)
    engine.set_config(sound_enabled=False, haptics_enabled=True, intensity=.8)
    waiting, resume = pause_after_first_pulse(engine, monkeypatch)
    future = engine.play_pattern('heartbeat')
    try:
        assert waiting.wait(2)
        device.select_other_device()
        # Device.select changes identity before Agent.poll can call set_config.
        resume.set()
        future.result(timeout=2)
        assert len(device.pulses) == 1 and device.pulses[0][0] == 'pad:A'
        engine.set_config(sound_enabled=False, haptics_enabled=True, intensity=.2)
        engine.play_pattern('impact').result(timeout=2)
        assert device.pulses[-1] == ('pad:B', .85 * .2, .55 * .2, 85)
    finally:
        resume.set()
        engine.close()


@pytest.mark.parametrize('enabled,intensity', [(False, .8), (True, .2)])
def test_reconfigured_settings_cancel_tail_and_apply_only_to_new_patterns(monkeypatch, enabled, intensity):
    device = SwitchingDevice()
    engine = HapticEngine(device)
    engine.set_config(sound_enabled=False, haptics_enabled=True, intensity=.8)
    waiting, resume = pause_after_first_pulse(engine, monkeypatch)
    future = engine.play_pattern('shutter')
    try:
        assert waiting.wait(2)
        engine.set_config(sound_enabled=False, haptics_enabled=enabled, intensity=intensity)
        resume.set()
        future.result(timeout=2)
        assert len(device.pulses) == 1
        result = engine.play_pattern('impact')
        if enabled:
            result.result(timeout=2)
            assert device.pulses[-1] == ('pad:A', .85 * intensity, .55 * intensity, 85)
        else:
            assert result is None and len(device.pulses) == 1
    finally:
        resume.set()
        engine.close()


def test_queued_pattern_is_cancelled_before_it_can_use_new_device_settings():
    device = SwitchingDevice()
    engine = HapticEngine(device)
    engine.set_config(sound_enabled=False, haptics_enabled=True, intensity=.8)
    occupied = [threading.Event(), threading.Event()]
    release = threading.Event()

    def occupy_worker(index):
        occupied[index].set()
        assert release.wait(2)

    jobs = [engine.executor.submit(occupy_worker, index) for index in range(2)]
    try:
        assert all(event.wait(2) for event in occupied)
        queued = engine.play_pattern('impact')
        device.select_other_device()
        engine.set_config(sound_enabled=False, haptics_enabled=True, intensity=.2)
        release.set()
        queued.result(timeout=2)
        for job in jobs:
            job.result(timeout=2)
        assert device.pulses == []
    finally:
        release.set()
        engine.close()


def test_shutter_and_heartbeat_preview_buttons_dispatch_their_patterns(monkeypatch):
    engine = HapticEngine(DummyDevice())
    patterns = []
    monkeypatch.setattr(engine, 'play_pattern', patterns.append)
    try:
        engine.trigger_feedback('shutter')
        engine.trigger_feedback('heartbeat')
        assert patterns == ['shutter', 'heartbeat']
    finally:
        engine.close()


class TriggerDevice(SwitchingDevice):
    def __init__(self, supported=True):
        super().__init__()
        self.metadata['trigger_rumble'] = supported
        self.trigger_pulses = []

    def rumble_triggers(self, left, right, duration):
        self.trigger_pulses.append((self.metadata['device_key'], left, right, duration))
        return True


@pytest.mark.parametrize(('capability', 'enabled', 'expected'),
                         [(False, True, False), (True, False, False), (True, True, True)])
def test_trigger_preview_requires_capability_and_opt_in_without_main_motor_output(
        capability, enabled, expected):
    device = TriggerDevice(capability)
    engine = HapticEngine(device)
    engine.set_config(False, True, .8, trigger_rumble_enabled=enabled)
    try:
        future = engine.play_pattern('trigger_test')
        if future:
            future.result(timeout=2)
        assert bool(device.trigger_pulses) is expected
        assert not device.pulses
        if expected:
            assert device.trigger_pulses == [('pad:A', .55 * .8, .55 * .8, 180)]
    finally:
        engine.close()


def test_standard_feedback_echoes_trigger_motors_only_when_enabled():
    device = TriggerDevice()
    engine = HapticEngine(device)
    engine.set_config(False, True, .8, trigger_rumble_enabled=True)
    try:
        engine.play_pattern('tick').result(timeout=2)
        assert device.pulses == [('pad:A', 0, .35 * .8, 18)]
        assert device.trigger_pulses == [('pad:A', 0, .35 * .8, 18)]
    finally:
        engine.close()


def test_old_trigger_feedback_tail_cannot_reach_a_new_device(monkeypatch):
    device = TriggerDevice()
    engine = HapticEngine(device)
    engine.set_config(False, True, .8, trigger_rumble_enabled=True)
    waiting, resume = pause_after_first_pulse(engine, monkeypatch)
    future = engine.play_pattern('heartbeat')
    try:
        assert waiting.wait(2)
        device.select_other_device()
        resume.set()
        future.result(timeout=2)
        assert len(device.pulses) == 1 and len(device.trigger_pulses) == 1
        assert device.trigger_pulses[0][0] == 'pad:A'
    finally:
        resume.set()
        engine.close()


def test_feedback_profiles_change_real_motor_amplitudes():
    device = DummyDevice()
    engine = HapticEngine(device)
    try:
        pulses = {}
        for profile in ('crisp', 'deep', 'dynamic'):
            engine.set_config(False, True, 1., profile)
            engine.play_pattern('impact').result(timeout=2)
            pulses[profile] = device.pulses[-1]
        assert pulses['crisp'] == (.85, .55, 85)
        assert pulses['deep'][0] > pulses['crisp'][0]
        assert pulses['deep'][1] < pulses['crisp'][1]
        assert pulses['dynamic'] != pulses['crisp']
    finally:
        engine.close()


def test_worker_rechecks_device_identity_after_waiting_for_hardware_lock():
    """Select completes while a pulse waits; no stale pulse uses the new handle."""
    device = TriggerDevice()
    entered = threading.Event()
    hardware_lock = threading.RLock()

    class ObservedLock:
        def __enter__(self):
            entered.set()
            hardware_lock.acquire()
        def __exit__(self, *exc):
            hardware_lock.release()

    device._io_lock = ObservedLock()
    engine = HapticEngine(device)
    engine.set_config(False, True, .8, trigger_rumble_enabled=True)
    hardware_lock.acquire()
    try:
        future = engine.play_pattern('impact')
        assert entered.wait(2), 'The worker must reach the device lock'
        device.select_other_device()
    finally:
        hardware_lock.release()
    try:
        future.result(timeout=2)
        assert not device.pulses and not device.trigger_pulses
    finally:
        engine.close()

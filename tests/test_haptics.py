from pathlib import Path
import pytest
from dualsense5.haptic_engine import HapticEngine, ensure_shutter_sound_file
from dualsense5.studio_core import ConfigStore


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

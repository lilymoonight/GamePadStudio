from pathlib import Path
import pytest
from dualsense5.replay_service import (
    calculate_estimated_ram_gb,
    detect_hardware_encoder,
    ReplayBufferEngine,
    get_ffmpeg_path
)
from dualsense5.studio_core import ConfigStore


def test_calculate_estimated_ram_gb():
    assert calculate_estimated_ram_gb(5, 50) == 1.75 or calculate_estimated_ram_gb(5, 50) == 1.88 or 1.5 < calculate_estimated_ram_gb(5, 50) < 2.0
    assert calculate_estimated_ram_gb(10, 50) > calculate_estimated_ram_gb(5, 50)
    assert calculate_estimated_ram_gb(1, 35) > 0


def test_detect_hardware_encoder():
    ffmpeg = get_ffmpeg_path()
    if ffmpeg:
        hevc_enc = detect_hardware_encoder('hevc')
        assert hevc_enc in ('hevc_amf', 'hevc_nvenc', 'hevc_qsv', 'hevc_mf', 'libx265')
        av1_enc = detect_hardware_encoder('av1')
        assert av1_enc in ('av1_amf', 'av1_nvenc', 'av1_qsv', 'libaom-av1')


def test_replay_config_defaults_and_clamping(tmp_path):
    store = ConfigStore(tmp_path)
    assert 'replay_buffer_enabled' in store.data
    assert store.data['replay_buffer_enabled'] is False
    assert store.data['replay_buffer_minutes'] == 5
    assert store.data['replay_codec'] == 'hevc'

    # Test clamping
    store.data['replay_buffer_minutes'] = 999
    store.save()
    reloaded = ConfigStore(tmp_path)
    assert reloaded.data['replay_buffer_minutes'] == 10

    store.data['replay_buffer_minutes'] = 0
    store.save()
    reloaded = ConfigStore(tmp_path)
    assert reloaded.data['replay_buffer_minutes'] == 1


def test_replay_engine_lifecycle(tmp_path):
    engine = ReplayBufferEngine(save_dir=tmp_path, minutes=2, codec='hevc', bitrate_mbps=20)
    status = engine.get_status()
    assert status['enabled'] is False
    assert status['minutes'] == 2
    assert status['codec'] == 'hevc'
    assert status['estimated_max_gb'] > 0

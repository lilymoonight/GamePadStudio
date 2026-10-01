from pathlib import Path
import pytest
from gamepadstudio.replay_service import (
    calculate_estimated_ram_gb,
    detect_hardware_encoder,
    ReplayBufferEngine,
    get_ffmpeg_path
)
from gamepadstudio.studio_core import ConfigStore


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
    engine = ReplayBufferEngine(save_dir=tmp_path, minutes=2, codec='hevc', bitrate_mbps=20, capture_mode='game')
    status = engine.get_status()
    assert status['enabled'] is False
    assert status['minutes'] == 2
    assert status['codec'] == 'hevc'
    assert status['capture_mode'] == 'game'
    assert status['estimated_max_gb'] > 0
    assert status['is_pure_ram'] is True
    assert engine.is_pure_ram is True
    # Replay cache must NOT pollute user's album save_dir with files
    assert engine.cache_dir != engine.save_dir
    assert not (tmp_path / ".replay_cache").exists()
    assert engine._total_bytes == 0


def test_align_ts_stream():
    from gamepadstudio.replay_service import _align_ts_stream
    # Garbage bytes before valid TS packet starting with 0x47
    fake_packet_1 = b'\x47' + b'\x01' * 187
    fake_packet_2 = b'\x47' + b'\x02' * 187
    stream = b'junk_bytes_header' + fake_packet_1 + fake_packet_2
    aligned = _align_ts_stream(stream)
    assert aligned.startswith(b'\x47')
    assert len(aligned) == 188 * 2


def test_replay_buffer_zero_disk_writes(tmp_path):
    engine = ReplayBufferEngine(save_dir=tmp_path, minutes=1, codec='hevc', bitrate_mbps=10)
    # Simulate feeding chunks into RAM buffer
    dummy_ts = b'\x47' + b'\x00' * 187
    for _ in range(2000):
        engine._ram_chunks.append((1000.0, dummy_ts))
        engine._total_bytes += len(dummy_ts)

    status = engine.get_status()
    assert status['chunks_count'] == 2000
    assert status['current_size_mb'] > 0
    assert status['is_pure_ram'] is True
    # Confirm zero files written to disk in tmp_path
    assert list(tmp_path.iterdir()) == []


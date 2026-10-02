"""Offline transport diagnostics: header counters, formats and capture privacy."""
import importlib.util
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import zlib

import pytest

from gamepadstudio.dualsense_audio import OpusPacketInfo


_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'probe_dualsense_mic.py'
_SPEC = importlib.util.spec_from_file_location('cadence_dualsense_probe', _PATH)
probe = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(probe)


def frame(sequence, flags, counter=0):
    data = bytearray(78)
    data[:3] = bytes((0x31, (sequence << 4) | flags, counter))
    data[-4:] = zlib.crc32(b'\xa1' + data[:-4]).to_bytes(4, 'little')
    return bytes(data)


def test_cadence_distinguishes_report_sequence_from_audio_counter_and_wraps():
    cadence = probe.ReportCadence()
    for data, arrived in [(frame(14, 2, 254), 0), (frame(15, 1), .015),
                          (frame(0, 2, 0), .030), (frame(1, 2, 3), .045)]:
        cadence.observe(data, arrived)
    summary = cadence.summary()
    assert summary['extended_sequence_deltas'] == {1: 3}
    assert summary['audio_header_counter_deltas'] == {2: 1, 3: 1}
    assert summary['extended_report_flags'] == {1: 1, 2: 3}
    assert summary['extended_report_gap_ms'] == {'median': 15, 'max': 15}


def test_bad_crc_never_advances_cadence_counters():
    cadence = probe.ReportCadence()
    cadence.observe(frame(1, 2, 10), 0)
    bad = bytearray(frame(8, 2, 40))
    bad[-1] ^= 1
    for data in (bytes(bad), b'', bytes(78), None):
        cadence.observe(data, .015)
    cadence.observe(frame(3, 2, 12), .030)
    assert cadence.summary()['extended_sequence_deltas'] == {2: 1}
    assert cadence.summary()['audio_header_counter_deltas'] == {2: 1}
    assert cadence.summary()['extended_report_gap_ms']['median'] == 30


@pytest.mark.parametrize('interval', [0, -.1, .009, 1.001, float('inf'),
                                     float('nan'), '0.5', None, True])
def test_invalid_keepalive_stops_before_hid_reads_or_writes(interval):
    result, pcm = probe.probe(object(), 1, True, threading.Event(), object(), interval)
    assert result['verdict'] == 'ERROR'
    assert '10–1000' in result['errors'][0]
    assert not result['mic_enable_written'] and not pcm


def test_read_only_metadata_inspection_never_decodes_or_enables_mic(monkeypatch):
    now = [0.0]

    def read():
        now[0] += .25
        return frame(int(now[0] * 4), 2, int(now[0] * 4))

    monkeypatch.setattr(probe.time, 'monotonic', lambda: now[0])
    info = OpusPacketInfo(2, 'superwideband', 12000, 10, 1)
    inspector = SimpleNamespace(inspect=lambda _: info)
    result, pcm = probe.probe(SimpleNamespace(read=read), 1, False,
                              threading.Event(), packet_inspector=inspector)
    assert result['verdict'] == 'READ_ONLY_OK' and not pcm
    assert result['encoded_packet_formats'] == [dict(encoded_channels=2,
        encoded_bandwidth='superwideband', encoded_bandwidth_hz=12000,
        duration_ms=10, frame_count=1, packets=4)]
    assert result['pcm_output_sample_rate_hz'] == 48000
    assert result['pcm_output_channels'] == 1
    assert result['extended_sequence_deltas'] == {1: 3}


def test_no_save_audio_keeps_only_private_json_even_when_pcm_exists(tmp_path, monkeypatch):
    output = tmp_path / 'diagnostic.json'
    monkeypatch.setattr(probe.sys, 'platform', 'darwin')
    monkeypatch.setattr(probe.sys, 'argv', [str(_PATH), '--enable-mic',
        '--no-save-audio', '--output', str(output)])
    monkeypatch.setattr(probe.signal, 'signal', lambda *_: None)
    monkeypatch.setattr(probe, 'Transport', lambda: SimpleNamespace(
        name='Offline DS5', close=lambda: None))
    monkeypatch.setattr(probe, 'OpusDecoder', lambda: SimpleNamespace(
        lib=object(), close=lambda: None))
    monkeypatch.setattr(probe, 'OpusPacketInspector', lambda _: object())
    monkeypatch.setattr(probe, 'probe', lambda *_: (
        dict(verdict='DECODED_AUDIO', writer_stopped=True, errors=[]), bytes(960)))
    assert probe.main() == 0
    result = json.loads(output.read_text())
    assert result['audio_saved'] is False and 'wav_path' not in result
    assert not output.with_suffix('.wav').exists()
    assert output.stat().st_mode & 0o777 == 0o600

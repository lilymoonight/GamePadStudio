"""Offline codec and fake HID lifecycle checks; no device or audio I/O."""
import array
import ctypes as C
import importlib.util
import json
import math
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import zlib

import pytest

from gamepadstudio import dualsense_audio as audio


_PROBE_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'probe_dualsense_mic.py'
_SPEC = importlib.util.spec_from_file_location('offline_dualsense_probe', _PROBE_PATH)
probe = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(probe)


def report(packet=None, *, flags=0, identifier=0x31):
    """A protocol input fixture with Bluetooth's input transaction CRC."""
    result = bytearray(78)
    result[0] = identifier
    result[1] = flags
    result[2] = 0x47
    if packet is not None:
        assert len(packet) == 71
        result[3:74] = packet
    result[74:] = zlib.crc32(b'\xa1' + result[:74]).to_bytes(4, 'little')
    return bytes(result)


@pytest.mark.parametrize('value', [None, [], b'', b'\x01' + bytes(9), bytes(78),
                                 report(), report(flags=1), report(flags=0x10),
                                 report(flags=2, identifier=0x01), report(flags=2)[:-1],
                                 report(flags=2) + b'\0'])
def test_audio_packet_refuses_normal_input_and_invalid_reports(value):
    assert audio.audio_packet(value) is None


def test_audio_payload_uses_sdl_offset_three_and_requires_input_crc():
    packet = bytes(range(71))
    frame = report(packet, flags=0x32)
    assert audio.audio_packet(frame) == packet
    assert audio.audio_packet(bytearray(frame)) == packet
    bad = bytearray(frame)
    bad[10] ^= 1
    assert audio.audio_packet(bad) is None
    bad = bytearray(frame)
    bad[-4:] = zlib.crc32(b'\xa2' + bad[:-4]).to_bytes(4, 'little')
    assert audio.audio_packet(bad) is None


@pytest.mark.parametrize(('enabled', 'control'), [(True, 3), (False, 2)])
def test_mic_report_controls_only_microphone_and_has_output_transaction_crc(enabled, control):
    frame = audio.mic_control_report(17, enabled)
    assert len(frame) == 142 and frame[:5] == bytes((0x32, 0x10, 0x91, 1, control))
    assert frame[5:138] == bytes(133)
    assert frame[-4:] == zlib.crc32(b'\xa2' + frame[:138]).to_bytes(4, 'little')


@pytest.fixture
def opus_packets():
    try:
        path = audio.opus_library_path()
        library = C.CDLL(path)
    except (RuntimeError, OSError):
        pytest.skip('Offline Opus tests need libopus or the macOS pygame wheel')
    library.opus_encoder_create.argtypes = [C.c_int32, C.c_int, C.c_int, C.POINTER(C.c_int)]
    library.opus_encoder_create.restype = C.c_void_p
    library.opus_encoder_destroy.argtypes = [C.c_void_p]
    library.opus_encoder_destroy.restype = None
    library.opus_encode.argtypes = [C.c_void_p, C.POINTER(C.c_int16), C.c_int,
                                   C.POINTER(C.c_ubyte), C.c_int32]
    library.opus_encode.restype = C.c_int32
    library.opus_packet_pad.argtypes = [C.POINTER(C.c_ubyte), C.c_int32, C.c_int32]
    library.opus_packet_pad.restype = C.c_int
    error = C.c_int()
    encoder = library.opus_encoder_create(48000, 1, 2049, C.byref(error))
    assert encoder and error.value == 0

    def encode(sample_count=480, offset=0):
        pcm = (C.c_int16 * sample_count)(*[int(6000 * math.sin(2 * math.pi * 440 * (i + offset) / 48000))
                                          for i in range(sample_count)])
        encoded = (C.c_ubyte * 71)()
        size = library.opus_encode(encoder, pcm, sample_count, encoded, 71)
        assert 0 < size <= 71
        assert library.opus_packet_pad(encoded, size, 71) == 0
        return bytes(encoded)

    yield SimpleNamespace(encode=encode, library=library)
    library.opus_encoder_destroy(encoder)


def test_real_opus_round_trip_decodes_ten_ms_mono_without_audio_playback(opus_packets):
    packets = [opus_packets.encode(offset=i * 480) for i in range(4)]
    with audio.OpusDecoder(opus_packets.library) as decoder:
        pcm = b''.join(decoder.decode(audio.audio_packet(report(packet, flags=2))) for packet in packets)
        assert len(pcm) == 4 * 480 * 2
        samples = array.array('h', pcm)
        assert max(abs(value) for value in samples) > 500
    decoder.close()
    with pytest.raises(RuntimeError, match='已关闭'):
        decoder.decode(packets[0])


def test_real_opus_rejects_wrong_duration_and_corrupt_packet(opus_packets):
    with audio.OpusDecoder(opus_packets.library) as decoder:
        for packet in (opus_packets.encode(240), b'\xff' * 71):
            with pytest.raises(ValueError):
                decoder.decode(packet)
        for size in (0, 70, 72):
            with pytest.raises(ValueError, match='71'):
                decoder.decode(bytes(size))


class Function:
    def __init__(self, callback):
        self.callback = callback
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.callback(*args)


def test_opus_init_failure_cleans_nonnull_native_handle_and_keeps_typed_abi():
    def create(rate, channels, error):
        assert rate == 48000 and channels == 1
        C.cast(error, C.POINTER(C.c_int))[0] = -7
        return 0x1234567887654321
    lib = SimpleNamespace(opus_decoder_create=Function(create),
                          opus_decode=Function(lambda *_: 480),
                          opus_decoder_destroy=Function(lambda *_: None))
    with pytest.raises(RuntimeError, match='初始化失败'):
        audio.OpusDecoder(lib)
    assert lib.opus_decoder_destroy.calls == [(0x1234567887654321,)]
    assert lib.opus_decoder_create.restype is C.c_void_p
    assert lib.opus_decode.argtypes == [C.c_void_p, C.c_void_p, C.c_int32,
                                       C.c_void_p, C.c_int, C.c_int]
    assert lib.opus_decoder_destroy.restype is None


class Clock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now


class FakeTransport:
    def __init__(self, reports=None, *, clock=None, delay=0, read_error=None,
                 write_error=None, disable_error=None):
        self.reports = list(reports or [])
        self.clock, self.delay = clock, delay
        self.read_error, self.write_error, self.disable_error = read_error, write_error, disable_error
        self.reads = self.features = self.closed = 0
        self.writes = []
        self.name = 'Offline fake DualSense'

    def read(self):
        self.reads += 1
        if self.clock:
            self.clock.now += .25
        elif self.delay:
            time.sleep(self.delay)
        if self.read_error and self.reads > 1:
            raise self.read_error
        return self.reports.pop(0) if self.reports else b''

    def extended_mode(self):
        self.features += 1
        return 41

    def write(self, frame):
        self.writes.append(frame)
        if frame[4] == 3 and self.write_error:
            raise self.write_error
        if frame[4] == 2 and self.disable_error:
            raise self.disable_error

    def close(self):
        self.closed += 1


def test_no_crc_valid_baseline_never_sends_enable_or_disable(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(probe, 'time', SimpleNamespace(monotonic=clock.monotonic))
    corrupt = bytearray(report())
    corrupt[-1] ^= 1
    transport = FakeTransport([bytes(corrupt)] * 30, clock=clock)
    result, pcm = probe.probe(transport, 1, True, threading.Event(), object())
    assert result['verdict'] == 'NO_EXTENDED_REPORTS'
    assert result['extended_reports'] is False and result['writer_stopped'] is True
    assert result['mic_enable_written'] is False and result['mic_disable_written'] is False
    assert transport.features == 1 and transport.writes == [] and not pcm


def test_read_only_no_audio_is_successful_and_does_not_touch_decoder(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(probe, 'time', SimpleNamespace(monotonic=clock.monotonic))
    decoder = SimpleNamespace(decode=lambda *_: pytest.fail('Read-only must never decode'))
    transport = FakeTransport([report()] * 5, clock=clock)
    result, pcm = probe.probe(transport, 1, False, threading.Event(), decoder)
    assert result['verdict'] == 'READ_ONLY_OK' and result['capture_timed_out'] is True
    assert result['decoded_frames'] == result['valid_audio_packets'] == 0
    assert result['speech_verified'] is False and result['writer_stopped'] is True
    assert result['errors'] == [] and transport.writes == [] and not pcm


def test_read_only_counts_valid_audio_but_still_never_decodes_or_writes(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(probe, 'time', SimpleNamespace(monotonic=clock.monotonic))
    transport = FakeTransport([report()] + [report(bytes(range(71)), flags=2)] * 5, clock=clock)
    result, pcm = probe.probe(transport, 1, False, threading.Event(), object())
    assert result['valid_audio_packets'] > 0 and result['decoded_frames'] == 0
    assert result['verdict'] == 'READ_ONLY_OK' and not pcm and not transport.writes


@pytest.mark.parametrize('seconds', [0, -1, 31, float('inf'), float('nan'), '1'])
def test_invalid_duration_is_rejected_before_any_device_io(seconds):
    transport = FakeTransport()
    result, pcm = probe.probe(transport, seconds, True, threading.Event(), object())
    assert result['verdict'] == 'ERROR' and result['errors']
    assert not transport.reads and not transport.features and not transport.writes and not pcm
    assert result['enable_requested'] is True and result['writer_stopped'] is True


def test_missing_decoder_refuses_mic_enable_after_baseline():
    transport = FakeTransport([report()])
    result, _ = probe.probe(transport, 1, True, threading.Event())
    assert result['verdict'] == 'ERROR' and not transport.writes


@pytest.mark.parametrize('failure', ['read', 'write', 'disable'])
def test_mic_exception_always_attempts_disable_and_preserves_state(failure):
    transport = FakeTransport([report()] * 20, delay=.002,
                              read_error=OSError('read failed') if failure == 'read' else None,
                              write_error=OSError('write failed') if failure == 'write' else None,
                              disable_error=OSError('disable failed') if failure == 'disable' else None)
    result, pcm = probe.probe(transport, .025, True, threading.Event(), object())
    assert result['verdict'] == 'ERROR' and result['errors'] and result['enable_requested'] is True
    assert result['writer_started'] is True and result['writer_stopped'] is True
    assert transport.writes and transport.writes[-1][4] == 2
    assert result['mic_disable_written'] is (failure != 'disable')
    assert not pcm


def test_enabled_probe_times_out_and_decodes_only_valid_packets(opus_packets):
    packet = opus_packets.encode()
    corrupted = bytearray(report(packet, flags=2))
    corrupted[-1] ^= 1
    transport = FakeTransport([report(), report(), bytes(corrupted), report(packet, flags=2)], delay=.002)
    with audio.OpusDecoder(opus_packets.library) as decoder:
        result, pcm = probe.probe(transport, .025, True, threading.Event(), decoder)
    assert result['verdict'] == 'DECODED_AUDIO' and result['valid_audio_packets'] == 1
    assert result['decoded_frames'] == 1 and len(pcm) == 960
    assert result['capture_timed_out'] and result['writer_stopped'] and result['mic_disable_written']
    assert result['mic_enable_written'] and result['speech_verified'] is False
    assert transport.writes[0][4] == 3 and transport.writes[-1][4] == 2
    assert result['elapsed_seconds'] < 1


def test_writer_that_cannot_stop_is_reported_as_error(monkeypatch):
    class HungWriter:
        def __init__(self, **kwargs):
            pass
        def start(self):
            pass
        def join(self, timeout):
            assert timeout == 3
        def is_alive(self):
            return True
    monkeypatch.setattr(probe.threading, 'Thread', HungWriter)
    clock = Clock()
    monkeypatch.setattr(probe, 'time', SimpleNamespace(monotonic=clock.monotonic))
    transport = FakeTransport([report()] * 5, clock=clock)
    result, _ = probe.probe(transport, 1, True, threading.Event(), object())
    assert result['verdict'] == 'ERROR' and result['writer_stopped'] is False
    assert result['mic_disable_written'] is False and '按时停止' in result['errors'][-1]


def test_writer_start_failure_returns_state_without_joining_unstarted_thread(monkeypatch):
    class RefusedWriter:
        def __init__(self, **kwargs):
            pass
        def start(self):
            raise RuntimeError('thread startup refused')
        def join(self, **kwargs):
            pytest.fail('An unstarted thread must not be joined')
    monkeypatch.setattr(probe.threading, 'Thread', RefusedWriter)
    transport = FakeTransport([report()])
    result, _ = probe.probe(transport, 1, True, threading.Event(), object())
    assert result['verdict'] == 'ERROR' and result['enable_requested'] is True
    assert result['writer_started'] is False and result['writer_stopped'] is True
    assert not result['mic_disable_written'] and not transport.writes
    assert 'thread startup refused' in result['errors']


def test_writer_join_failure_keeps_native_handle_owned(monkeypatch):
    class FailedJoin:
        def __init__(self, **kwargs):
            pass
        def start(self):
            pass
        def join(self, **kwargs):
            raise RuntimeError('join failed')
    monkeypatch.setattr(probe.threading, 'Thread', FailedJoin)
    clock = Clock()
    monkeypatch.setattr(probe, 'time', SimpleNamespace(monotonic=clock.monotonic))
    transport = FakeTransport([report()] * 5, clock=clock)
    result, _ = probe.probe(transport, 1, True, threading.Event(), object())
    assert result['verdict'] == 'ERROR' and result['writer_stopped'] is False
    assert any('join failed' in error for error in result['errors'])


def test_main_wav_failure_keeps_live_writer_ownership_and_never_closes_hid(monkeypatch, tmp_path, capsys):
    transport = FakeTransport()
    closed = []
    decoder = SimpleNamespace(close=lambda: closed.append('decoder'))
    result = probe.initial_result(True)
    result.update(verdict='DECODED_AUDIO', writer_started=True, writer_stopped=False,
                  mic_enable_written=True)
    monkeypatch.setattr(probe, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(sys, 'argv', [
        'probe', '--enable-mic', '--seconds', '1', '--output', str(tmp_path / 'result.json')])
    monkeypatch.setattr(probe, 'Transport', lambda: transport)
    monkeypatch.setattr(probe, 'OpusDecoder', lambda: decoder)
    monkeypatch.setattr(probe, 'probe', lambda *_: (result, bytes(960)))
    monkeypatch.setattr(probe.signal, 'signal', lambda *_: None)
    def refused(*_):
        raise OSError('WAV disk full')
    monkeypatch.setattr(probe.wave, 'open', refused)
    assert probe.main() == 1
    saved = json.loads((tmp_path / 'result.json').read_text())
    assert saved['verdict'] == 'ERROR' and saved['enable_requested'] is True
    assert saved['writer_stopped'] is False and saved['mic_enable_written'] is True
    assert saved['mic_disable_written'] is False and 'WAV disk full' in saved['errors']
    assert transport.closed == 0 and closed == ['decoder']
    assert json.loads(capsys.readouterr().out)['writer_stopped'] is False


def test_main_close_exception_is_saved_with_enable_and_disable_state(monkeypatch, tmp_path):
    transport = FakeTransport()
    def failed_close():
        raise OSError('close failed')
    transport.close = failed_close
    result = probe.initial_result(False)
    result['verdict'] = 'READ_ONLY_OK'
    monkeypatch.setattr(probe, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(sys, 'argv', [
        'probe', '--seconds', '1', '--output', str(tmp_path / 'result.json')])
    monkeypatch.setattr(probe, 'Transport', lambda: transport)
    monkeypatch.setattr(probe, 'probe', lambda *_: (result, b''))
    monkeypatch.setattr(probe.signal, 'signal', lambda *_: None)
    assert probe.main() == 1
    saved = json.loads((tmp_path / 'result.json').read_text())
    assert saved['enable_requested'] is False and saved['writer_stopped'] is True
    assert saved['mic_disable_written'] is False and saved['verdict'] == 'ERROR'
    assert 'HID 关闭失败' in saved['errors'][-1]


@pytest.mark.parametrize('failure', ['enumerate', 'open'])
def test_transport_constructor_failure_releases_sdl_hid_and_keeps_pointer_abi(monkeypatch, failure):
    info = probe.HidInfo(path=b'Bluetooth_offline', vendor=0x054c, product=0x0ce6,
                         name='Offline fake DualSense', usage_page=1, usage=5)
    def enumerate_devices(*_):
        if failure == 'enumerate':
            raise OSError('enumerate failed')
        return C.pointer(info)
    def failed_open(*_):
        raise OSError('open failed')
    library = SimpleNamespace(**{
        name: Function(callback) for name, callback in {
            'SDL_hid_init': lambda: 0,
            'SDL_hid_exit': lambda: 0,
            'SDL_hid_enumerate': enumerate_devices,
            'SDL_hid_free_enumeration': lambda *_: None,
            'SDL_hid_open_path': failed_open,
            'SDL_hid_close': lambda *_: None,
            'SDL_hid_read_timeout': lambda *_: 0,
            'SDL_hid_get_feature_report': lambda *_: 0,
            'SDL_hid_write': lambda *_: 0,
        }.items()})
    monkeypatch.setattr(probe, 'load_sdl_library', lambda: library)
    with pytest.raises(OSError):
        probe.Transport()
    assert len(library.SDL_hid_exit.calls) == 1
    assert library.SDL_hid_write.calls == []
    assert library.SDL_hid_open_path.restype is C.c_void_p
    assert library.SDL_hid_read_timeout.argtypes == [C.c_void_p, C.c_void_p, C.c_size_t, C.c_int]
    if failure == 'open':
        assert len(library.SDL_hid_free_enumeration.calls) == 1

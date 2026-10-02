"""Isolated macOS DualSense BT mic diagnostic. Default: read-only.

Close GamePadStudio first. --enable-mic explicitly enables the controller mic
for at most 30 seconds and saves any decoded audio locally. It neither installs
a virtual audio device nor changes the Mac's selected audio input.
"""
from __future__ import annotations

import argparse
import array
from collections import Counter
import ctypes as C
import json
import math
import os
from pathlib import Path
import signal
import statistics
import sys
import threading
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
from gamepadstudio.device import load_sdl_library
from gamepadstudio.dualsense_audio import (FRAME_SAMPLES, OpusDecoder, RATE, audio_packet,
                                         mic_control_report, valid_extended_report)


_PCM_FRAME_BYTES = FRAME_SAMPLES * 2


def append_at_capture_time(pcm: bytearray, decoded: bytes, *, started: float,
                           arrived: float) -> int:
    """Keep missing Bluetooth audio on the capture timeline as silence."""
    if len(decoded) != _PCM_FRAME_BYTES:
        raise ValueError('Opus 解码结果长度错误。')
    # The packet represents the 10 ms ending at its arrival, not the 10 ms
    # starting there. This also keeps a packet at the deadline in range.
    target_frame = max(0, round((arrived - started) * RATE / FRAME_SAMPLES) - 1)
    missing = max(0, target_frame - len(pcm) // _PCM_FRAME_BYTES)
    if missing:
        pcm.extend(bytes(missing * _PCM_FRAME_BYTES))
    pcm.extend(decoded)
    return missing


def private_output(path: Path):
    """Keep locally captured voice and its diagnostic metadata owner-only."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        return os.fdopen(descriptor, 'wb')
    except Exception:
        os.close(descriptor)
        raise


class HidInfo(C.Structure):
    pass


HidInfo._fields_ = [
    ('path', C.c_char_p), ('vendor', C.c_ushort), ('product', C.c_ushort),
    ('serial', C.c_wchar_p), ('release', C.c_ushort),
    ('manufacturer', C.c_wchar_p), ('name', C.c_wchar_p),
    ('usage_page', C.c_ushort), ('usage', C.c_ushort),
    ('interface', C.c_int), ('interface_class', C.c_int),
    ('interface_subclass', C.c_int), ('interface_protocol', C.c_int),
    ('next', C.POINTER(HidInfo))]


class Transport:
    def __init__(self):
        self.lib = load_sdl_library()
        signatures = {
            'SDL_hid_init': ([], C.c_int), 'SDL_hid_exit': ([], C.c_int),
            'SDL_hid_enumerate': ([C.c_ushort, C.c_ushort], C.POINTER(HidInfo)),
            'SDL_hid_free_enumeration': ([C.POINTER(HidInfo)], None),
            'SDL_hid_open_path': ([C.c_char_p, C.c_int], C.c_void_p),
            'SDL_hid_close': ([C.c_void_p], None),
            'SDL_hid_read_timeout': ([C.c_void_p, C.c_void_p, C.c_size_t, C.c_int], C.c_int),
            'SDL_hid_get_feature_report': ([C.c_void_p, C.c_void_p, C.c_size_t], C.c_int),
            'SDL_hid_write': ([C.c_void_p, C.c_void_p, C.c_size_t], C.c_int),
        }
        for name, (arguments, result) in signatures.items():
            method = getattr(self.lib, name)
            method.argtypes, method.restype = arguments, result
        self.handle = None
        if self.lib.SDL_hid_init() != 0:
            raise RuntimeError('SDL HID 初始化失败。')
        self.initialized = True
        try:
            self._open_bluetooth_device()
        except Exception:
            self.close()
            raise

    def _open_bluetooth_device(self):
        devices = []
        head = self.lib.SDL_hid_enumerate(0x054c, 0x0ce6)
        try:
            current = head
            while current:
                info = current.contents
                if info.path and info.usage_page == 1 and info.usage in (4, 5):
                    devices.append((bytes(info.path), info.name or 'DualSense'))
                current = info.next
        finally:
            self.lib.SDL_hid_free_enumeration(head)
        if len(devices) != 1:
            raise RuntimeError(f'需要连接一只 DualSense，当前发现 {len(devices)} 个手柄接口。')
        self.path, self.name = devices[0]
        # SDL's macOS path includes its transport. Refuse a USB/interface guess.
        if not self.path.startswith(b'Bluetooth_'):
            raise RuntimeError('检测到的手柄不是 SDL Bluetooth 接口；诊断没有发送命令。')
        self.handle = self.lib.SDL_hid_open_path(self.path, 0)
        if not self.handle:
            raise RuntimeError('无法打开手柄 HID 接口，请先关闭其他手柄程序。')

    def read(self):
        buffer = (C.c_ubyte * 512)()
        length = self.lib.SDL_hid_read_timeout(self.handle, buffer, len(buffer), 100)
        if length < 0:
            raise RuntimeError('HID 读取失败或手柄已断开。')
        return bytes(buffer[:length])

    def extended_mode(self):
        buffer = (C.c_ubyte * 41)()
        buffer[0] = 5
        return self.lib.SDL_hid_get_feature_report(self.handle, buffer, len(buffer))

    def write(self, report):
        buffer = C.create_string_buffer(report)
        length = self.lib.SDL_hid_write(self.handle, buffer, len(report))
        if length != len(report):
            raise RuntimeError(f'HID 写入未完成：{length}/{len(report)}')

    def close(self):
        try:
            if self.handle:
                self.lib.SDL_hid_close(self.handle)
                self.handle = None
        finally:
            if getattr(self, 'initialized', False):
                self.lib.SDL_hid_exit()
                self.initialized = False


def initial_result(enable_mic):
    return dict(enable_requested=enable_mic, reports=0, audio_flagged=0,
                valid_audio_packets=0, decoded_frames=0, decode_errors=0,
                missing_audio_frames=0,
                mic_enable_written=False, mic_disable_written=False,
                writer_started=False, writer_stopped=True, capture_timed_out=False,
                errors=[], speech_verified=False)


def probe(transport, seconds, enable_mic, stop, decoder=None):
    result = initial_result(enable_mic)
    sizes, identifiers = Counter(), Counter()
    pcm = bytearray()
    audio_arrivals = []
    reports_per_second = Counter()
    audio_per_second = Counter()
    writer = None
    started = time.monotonic()
    try:
        if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 0 < seconds <= 30:
            raise ValueError('探测时长必须为大于 0 且不超过 30 秒的有限数值。')
        # A full control report must be readable before attempting an output.
        baseline_deadline = started + 3
        extended = False
        while not stop.is_set() and time.monotonic() < baseline_deadline:
            report = transport.read()
            if valid_extended_report(report):
                extended = True
                break
            if report:
                result['reports'] += 1
        if not extended and not stop.is_set():
            result['feature_05_bytes'] = transport.extended_mode()
            deadline = time.monotonic() + 3
            while not stop.is_set() and time.monotonic() < deadline:
                report = transport.read()
                if valid_extended_report(report):
                    extended = True
                    break
        result['extended_reports'] = extended
        if not extended:
            result['verdict'] = 'NO_EXTENDED_REPORTS'
            return result, pcm
        deadline = time.monotonic() + seconds
        capture_started = time.monotonic()
        if enable_mic:
            if decoder is None:
                raise RuntimeError('必须先准备 Opus 解码器，才会启用麦克风。')

            def write_loop():
                sequence = 0
                try:
                    while not stop.is_set() and time.monotonic() < deadline:
                        transport.write(mic_control_report(sequence, True))
                        result['mic_enable_written'] = True
                        sequence = (sequence + 1) & 15
                        if stop.wait(min(.5, max(0, deadline - time.monotonic()))):
                            break
                except Exception as exc:
                    result['errors'].append(str(exc))
                    stop.set()
                finally:
                    try:
                        transport.write(mic_control_report(sequence, False))
                        result['mic_disable_written'] = True
                    except Exception as exc:
                        result['errors'].append('麦克风关闭失败：' + str(exc))

            writer = threading.Thread(target=write_loop, daemon=True)
            writer.start()
            result['writer_started'] = True
            result['writer_stopped'] = False
        last_report = time.monotonic()
        while not stop.is_set() and time.monotonic() < deadline:
            report = transport.read()
            if not report:
                if time.monotonic() - last_report > 2:
                    result['errors'].append('手柄超过 2 秒没有报告，已停止探测。')
                    break
                continue
            last_report = time.monotonic()
            result['reports'] += 1
            reports_per_second[int(last_report - capture_started)] += 1
            sizes[len(report)] += 1
            identifiers[hex(report[0])] += 1
            if len(report) > 1 and report[0] == 0x31 and report[1] & 2:
                result['audio_flagged'] += 1
            packet = audio_packet(report)
            if packet is not None:
                result['valid_audio_packets'] += 1
                if enable_mic:
                    audio_arrivals.append(last_report)
                    audio_per_second[int(last_report - capture_started)] += 1
                if enable_mic:
                    try:
                        result['missing_audio_frames'] += append_at_capture_time(
                            pcm, decoder.decode(packet), started=capture_started,
                            arrived=last_report)
                        result['decoded_frames'] += 1
                    except ValueError:
                        result['decode_errors'] += 1
        if pcm:
            # Keep the final part of the timed capture as silence as well.
            capture_frames = round((min(time.monotonic(), deadline) - capture_started)
                                   * RATE / FRAME_SAMPLES)
            trailing = max(0, capture_frames - len(pcm) // _PCM_FRAME_BYTES)
            pcm.extend(bytes(trailing * _PCM_FRAME_BYTES))
            result['missing_audio_frames'] += trailing
        result['capture_timed_out'] = time.monotonic() >= deadline
        result['audio_coverage'] = round(
            result['decoded_frames'] /
            (result['decoded_frames'] + result['missing_audio_frames']), 3
        ) if pcm else 0
        result['verdict'] = ('READ_ONLY_OK' if not enable_mic else
                             'NO_AUDIO_FRAMES' if not result['decoded_frames'] else
                             'INCOMPLETE_AUDIO' if result['audio_coverage'] < .9 else
                             'DECODED_AUDIO')
    except Exception as exc:
        result['errors'].append(str(exc))
        result['verdict'] = 'ERROR'
    finally:
        stop.set()
        if writer and result['writer_started']:
            try:
                writer.join(timeout=3)
                result['writer_stopped'] = not writer.is_alive()
            except Exception as exc:
                result['writer_stopped'] = False
                result['errors'].append('麦克风线程关闭失败：' + str(exc))
            if not result['writer_stopped']:
                result['errors'].append('麦克风写入线程未能按时停止；进程退出前不会关闭仍在使用的 HID 句柄。')
        if result['errors']:
            result['verdict'] = 'ERROR'
        result['report_sizes'] = dict(sizes)
        result['report_ids'] = dict(identifiers)
        result['elapsed_seconds'] = round(time.monotonic() - started, 3)
        if enable_mic:
            result['reports_per_second'] = dict(sorted(reports_per_second.items()))
            result['audio_packets_per_second'] = dict(sorted(audio_per_second.items()))
            if len(audio_arrivals) > 1:
                gaps = [(b - a) * 1000 for a, b in zip(audio_arrivals, audio_arrivals[1:])]
                result['audio_gap_ms'] = {
                    'median': round(statistics.median(gaps), 2),
                    'max': round(max(gaps), 2),
                    'over_20ms': sum(gap > 20 for gap in gaps),
                    'over_50ms': sum(gap > 50 for gap in gaps),
                }
                result['audio_arrival_span_seconds'] = round(audio_arrivals[-1] - audio_arrivals[0], 3)
    if pcm:
        try:
            samples = array.array('h', pcm)
            result['audio_seconds'] = round(len(samples) / RATE, 3)
            result['pcm_peak'] = max(abs(value) for value in samples)
            result['pcm_rms'] = round(math.sqrt(sum(value * value for value in samples) / len(samples)), 2)
        except Exception as exc:
            result['verdict'] = 'ERROR'
            result['errors'].append('PCM 统计失败：' + str(exc))
    return result, pcm


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable-mic', action='store_true')
    parser.add_argument('--seconds', type=float, default=8)
    parser.add_argument('--output', type=Path, required=True, help='Local JSON result path; WAV uses same stem.')
    args = parser.parse_args()
    if sys.platform != 'darwin' or not 1 <= args.seconds <= 30:
        parser.error('仅支持 macOS，时长必须在 1–30 秒之间。')
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    decoder, transport = None, None
    result = initial_result(args.enable_mic)
    try:
        if args.enable_mic:
            decoder = OpusDecoder()
        transport = Transport()
        result, pcm = probe(transport, args.seconds, args.enable_mic, stop, decoder)
        result['device_name'] = transport.name
        if pcm:
            wav_path = args.output.with_suffix('.wav')
            wav_path.parent.mkdir(parents=True, exist_ok=True)
            with private_output(wav_path) as raw:
                with wave.open(raw, 'wb') as output:
                    output.setnchannels(1)
                    output.setsampwidth(2)
                    output.setframerate(RATE)
                    output.writeframes(pcm)
            result['wav_path'] = str(wav_path.resolve())
    except Exception as exc:
        # Preserve writer ownership even if saving the WAV fails. Losing the
        # writer_stopped=False marker could race SDL_hid_close with a write.
        result['verdict'] = 'ERROR'
        result['errors'].append(str(exc))
    finally:
        # Never race close with a blocked native write. The isolated process
        # owns that handle, so process exit releases it if a write failed to end.
        if transport and result.get('writer_stopped', True):
            try:
                transport.close()
            except Exception as exc:
                result['verdict'] = 'ERROR'
                result['errors'].append('HID 关闭失败：' + str(exc))
        if decoder:
            try:
                decoder.close()
            except Exception as exc:
                result['verdict'] = 'ERROR'
                result['errors'].append('Opus 关闭失败：' + str(exc))
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with private_output(args.output) as output:
            output.write(json.dumps(result, ensure_ascii=False, indent=2).encode('utf-8'))
    except OSError as exc:
        result['verdict'] = 'ERROR'
        result['errors'].append('诊断 JSON 保存失败：' + str(exc))
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result.get('verdict') in ('READ_ONLY_OK', 'DECODED_AUDIO') and not result.get('errors') else 1


if __name__ == '__main__':
    raise SystemExit(main())

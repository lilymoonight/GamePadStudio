"""Experimental DualSense Bluetooth mic protocol, separate from mapping output.

Protocol references: awalol/DS5Dongle src/audio.cpp and src/main.cpp.
SDL HID strips the 0xA1 Bluetooth transaction byte, hence flags at 1,
Opus at 3 (the firmware sees flags at 2 and Opus at 4).
Nothing in this module opens a microphone or writes to hardware on import.
"""
from __future__ import annotations

import ctypes as C
import ctypes.util
from pathlib import Path
import zlib

RATE = 48000
FRAME_SAMPLES = 480
OPUS_BYTES = 71


def mic_control_report(sequence: int, enabled: bool) -> bytes:
    """Control-only 0x32 report; do not enable speakers or haptic audio."""
    report = bytearray(142)
    report[0:5] = bytes((0x32, (sequence & 15) << 4, 0x91, 1,
                         3 if enabled else 2))
    report[-4:] = zlib.crc32(b'\xa2' + report[:-4]).to_bytes(4, 'little')
    return bytes(report)


def valid_extended_report(report: bytes) -> bool:
    """Require the complete Bluetooth input report and its transaction CRC."""
    if not isinstance(report, (bytes, bytearray)) or len(report) != 78 or report[0] != 0x31:
        return False
    expected = zlib.crc32(b'\xa1' + report[:-4]).to_bytes(4, 'little')
    return report[-4:] == expected


def audio_packet(report: bytes) -> bytes | None:
    """Accept a full, CRC-checked audio report, never ordinary control state."""
    if not valid_extended_report(report) or not report[1] & 2:
        return None
    return bytes(report[3:3 + OPUS_BYTES])


def opus_library_path() -> str:
    system = C.util.find_library('opus')
    if system:
        return system
    import pygame
    # macOS pygame wheels already ship libopus for SDL_mixer. No HAL driver,
    # global library installation, or extra Python dependency is needed.
    folder = Path(pygame.__file__).resolve().parent
    for name in ('libopus.0.dylib', 'libopus.dylib'):
        candidate = folder / '.dylibs' / name
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError('找不到 libopus；收音诊断需要 Opus 解码库。')


class OpusDecoder:
    def __init__(self, library=None):
        self.lib = library if library is not None else C.CDLL(opus_library_path())
        self.lib.opus_decoder_create.argtypes = [C.c_int32, C.c_int, C.POINTER(C.c_int)]
        self.lib.opus_decoder_create.restype = C.c_void_p
        self.lib.opus_decode.argtypes = [C.c_void_p, C.c_void_p, C.c_int32,
                                        C.c_void_p, C.c_int, C.c_int]
        self.lib.opus_decode.restype = C.c_int
        self.lib.opus_decoder_destroy.argtypes = [C.c_void_p]
        self.lib.opus_decoder_destroy.restype = None
        error = C.c_int()
        self.handle = self.lib.opus_decoder_create(RATE, 1, C.byref(error))
        if not self.handle or error.value:
            if self.handle:
                self.lib.opus_decoder_destroy(self.handle)
                self.handle = None
            raise RuntimeError(f'Opus 解码器初始化失败：{error.value}')

    def decode(self, packet: bytes) -> bytes:
        if not self.handle:
            raise RuntimeError('Opus 解码器已关闭。')
        if len(packet) != OPUS_BYTES:
            raise ValueError('DualSense 音频帧必须为 71 字节。')
        data = C.create_string_buffer(packet)
        pcm = (C.c_int16 * FRAME_SAMPLES)()
        size = self.lib.opus_decode(self.handle, data, len(packet), pcm,
                                    FRAME_SAMPLES, 0)
        if size != FRAME_SAMPLES:
            raise ValueError(f'不是有效的 10 ms Opus 音频帧：{size}')
        return bytes(pcm)

    def close(self):
        if self.handle:
            self.lib.opus_decoder_destroy(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

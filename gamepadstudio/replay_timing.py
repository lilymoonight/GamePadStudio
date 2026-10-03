"""Explicit capture timestamps and encoded-media clocks for the replay buffer.

Rawvideo has no timestamps: assigning a fixed input FPS speeds up a slow
capture loop. Streaming Matroska carries each RGB frame's monotonic timestamp
without a second encoder, image compression, or an optional media dependency.
See https://www.matroska.org/technical/elements.html (Cluster/Timecode,
SimpleBlock, V_UNCOMPRESSED/UncompressedFourCC).
"""
import math
from pathlib import Path
import struct
import threading


def _size(value):
    for width in range(1, 9):
        if value < (1 << (7 * width)) - 1:
            return ((1 << (7 * width)) | value).to_bytes(width, 'big')
    raise ValueError('Matroska element is too large')


def _element(identifier, payload):
    return identifier + _size(len(payload)) + payload


def _uint(identifier, value):
    value = int(value)
    return _element(identifier, value.to_bytes(max(1, (value.bit_length() + 7) // 8), 'big'))


def _write_all(stream, data):
    remaining = memoryview(data)
    while remaining:
        written = stream.write(remaining)
        # Buffered file-like objects commonly return None to mean success.
        if written is None:
            return
        if written <= 0:
            raise BrokenPipeError('The video encoder stopped accepting frames')
        remaining = remaining[written:]


class TimestampedRGBWriter:
    """One non-laced RGB24 track; timestamps are seconds since capture start."""
    def __init__(self, stream, width, height, fps):
        self.stream = stream
        self.width, self.height = int(width), int(height)
        self.frame_bytes = self.width * self.height * 3
        self.last_timestamp = -1
        header = (_uint(b'\x42\x86', 1) + _uint(b'\x42\xf7', 1)
                  + _uint(b'\x42\xf2', 4) + _uint(b'\x42\xf3', 8)
                  + _element(b'\x42\x82', b'matroska')
                  + _uint(b'\x42\x87', 4) + _uint(b'\x42\x85', 2))
        info = (_uint(b'\x2a\xd7\xb1', 1_000_000)
                + _element(b'\x4d\x80', b'GamePadStudio')
                + _element(b'\x57\x41', b'GamePadStudio'))
        video = (_uint(b'\xb0', self.width) + _uint(b'\xba', self.height)
                 + _element(b'\x2e\xb5\x24', b'RGB\x18'))
        track = (_uint(b'\xd7', 1) + _uint(b'\x73\xc5', 1) + _uint(b'\x83', 1)
                 + _uint(b'\x9c', 0)
                 + _uint(b'\x23\xe3\x83', round(1_000_000_000 / fps))
                 + _element(b'\x86', b'V_UNCOMPRESSED')
                 + _element(b'\xe0', video))
        _write_all(stream, _element(b'\x1a\x45\xdf\xa3', header)
                   + b'\x18\x53\x80\x67\x01\xff\xff\xff\xff\xff\xff\xff'
                   + _element(b'\x15\x49\xa9\x66', info)
                   + _element(b'\x16\x54\xae\x6b', _element(b'\xae', track)))

    def write_frame(self, rgb, timestamp):
        if len(rgb) != self.frame_bytes:
            raise ValueError('The captured frame dimensions changed during recording')
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError('Invalid video capture timestamp')
        milliseconds = max(self.last_timestamp + 1, round(timestamp * 1000))
        self.last_timestamp = milliseconds
        timecode = _uint(b'\xe7', milliseconds)
        # Track number 1, relative timecode 0, independent/keyframe, no lacing.
        block = b'\xa3' + _size(4 + len(rgb)) + b'\x81\x00\x00\x80'
        prefix = b'\x1f\x43\xb6\x75' + _size(len(timecode) + len(block) + len(rgb))
        _write_all(self.stream, prefix + timecode + block)
        _write_all(self.stream, rgb)
        self.stream.flush()
        return milliseconds / 1000


class TimestampedAVWriter:
    """RGB24 plus interleaved signed PCM16LE, on one shared capture clock.

    The caller subtracts the same recording origin from both source timestamps.
    Audio timestamps identify the first sample, rather than callback delivery.
    Tracks keep independent ordering, so delayed audio delivery does not shift
    its PTS to the newest video frame. One-microsecond ticks retain PCM timing.
    See https://www.matroska.org/technical/codec_specs.html#a_pcmintlit.
    """
    def __init__(self, stream, width, height, fps, sample_rate=48000, channels=2):
        def integer(value, name, low, high):
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f'Invalid {name}')
            return value

        self.width = integer(width, 'video width', 1, 65535)
        self.height = integer(height, 'video height', 1, 65535)
        self.sample_rate = integer(sample_rate, 'audio sample rate', 8000, 384000)
        self.channels = integer(channels, 'audio channels', 1, 8)
        if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not math.isfinite(fps) or not 0 < fps <= 240:
            raise ValueError('Invalid video FPS')
        self.stream = stream
        self.frame_bytes = self.width * self.height * 3
        self.last_timestamp = self.last_audio_timestamp = -1
        self._audio_end = None
        self._lock = threading.Lock()
        header = (_uint(b'\x42\x86', 1) + _uint(b'\x42\xf7', 1)
                  + _uint(b'\x42\xf2', 4) + _uint(b'\x42\xf3', 8)
                  + _element(b'\x42\x82', b'matroska')
                  + _uint(b'\x42\x87', 4) + _uint(b'\x42\x85', 2))
        info = (_uint(b'\x2a\xd7\xb1', 1000)
                + _element(b'\x4d\x80', b'GamePadStudio')
                + _element(b'\x57\x41', b'GamePadStudio'))
        video = (_uint(b'\xb0', self.width) + _uint(b'\xba', self.height)
                 + _element(b'\x2e\xb5\x24', b'RGB\x18'))
        video_track = (_uint(b'\xd7', 1) + _uint(b'\x73\xc5', 1) + _uint(b'\x83', 1)
                       + _uint(b'\x9c', 0)
                       + _uint(b'\x23\xe3\x83', round(1_000_000_000 / fps))
                       + _element(b'\x86', b'V_UNCOMPRESSED') + _element(b'\xe0', video))
        audio = (_element(b'\xb5', struct.pack('>d', float(self.sample_rate)))
                 + _uint(b'\x9f', self.channels) + _uint(b'\x62\x64', 16))
        audio_track = (_uint(b'\xd7', 2) + _uint(b'\x73\xc5', 2) + _uint(b'\x83', 2)
                       + _uint(b'\x9c', 0) + _element(b'\x86', b'A_PCM/INT/LIT')
                       + _element(b'\xe1', audio))
        tracks = _element(b'\xae', video_track) + _element(b'\xae', audio_track)
        _write_all(stream, _element(b'\x1a\x45\xdf\xa3', header)
                   + b'\x18\x53\x80\x67\x01\xff\xff\xff\xff\xff\xff\xff'
                   + _element(b'\x15\x49\xa9\x66', info)
                   + _element(b'\x16\x54\xae\x6b', tracks))

    @staticmethod
    def _timestamp(timestamp, kind):
        if (isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
                or not math.isfinite(timestamp) or not 0 <= timestamp < (1 << 64) / 1_000_000):
            raise ValueError(f'Invalid {kind} capture timestamp')
        return round(timestamp * 1_000_000)

    def _block(self, track, data, microseconds):
        timecode = _uint(b'\xe7', microseconds)
        # Independent blocks use their original per-track timestamp, even if
        # an earlier audio packet arrives after a later video callback.
        block = b'\xa3' + _size(4 + len(data)) + bytes((0x80 | track, 0, 0, 0x80))
        prefix = b'\x1f\x43\xb6\x75' + _size(len(timecode) + len(block) + len(data))
        _write_all(self.stream, prefix + timecode + block)
        _write_all(self.stream, data)
        self.stream.flush()

    def write_frame(self, rgb, timestamp):
        if len(rgb) != self.frame_bytes:
            raise ValueError('The captured frame dimensions changed during recording')
        microseconds = self._timestamp(timestamp, 'video')
        with self._lock:
            if microseconds < self.last_timestamp:
                raise ValueError('Video capture timestamps moved backwards')
            self._block(1, rgb, microseconds)
            self.last_timestamp = microseconds
        return microseconds / 1_000_000

    def write_audio(self, pcm, timestamp, *, sample_rate=None, channels=None):
        """Write one aligned PCM packet; format changes/overlap fail explicitly."""
        if ((sample_rate is not None and sample_rate != self.sample_rate)
                or (channels is not None and channels != self.channels)):
            raise ValueError('The audio sample rate or channel count changed during recording')
        if not isinstance(pcm, (bytes, bytearray, memoryview)):
            raise ValueError('Audio must contain PCM s16le bytes')
        data = memoryview(pcm).cast('B')
        sample_bytes = self.channels * 2
        if not data or len(data) % sample_bytes:
            raise ValueError('Audio PCM packet does not contain complete interleaved samples')
        microseconds = self._timestamp(timestamp, 'audio')
        with self._lock:
            if microseconds < self.last_audio_timestamp:
                raise ValueError('Audio capture timestamps moved backwards')
            if self._audio_end is not None and timestamp < self._audio_end - 1 / self.sample_rate:
                raise ValueError('Audio PCM packets overlap')
            self._block(2, data, microseconds)
            self.last_audio_timestamp = microseconds
            self._audio_end = timestamp + len(data) / sample_bytes / self.sample_rate
        return microseconds / 1_000_000


class TransportStreamClock:
    """Read video PES PTS from arbitrary MPEG-TS stdout chunk boundaries."""
    def __init__(self):
        self.pending = bytearray()
        self.first_pts = None
        self.previous_pts = None
        self.wrap_offset = 0
        self.last_seconds = 0.0

    def span(self, data):
        self.pending.extend(data)
        values = []
        while len(self.pending) >= 188:
            if self.pending[0] != 0x47:
                sync = self.pending.find(0x47, 1)
                if sync < 0:
                    self.pending.clear()
                    break
                del self.pending[:sync]
                continue
            packet = bytes(self.pending[:188])
            del self.pending[:188]
            if not packet[1] & 0x40:
                continue
            mode = (packet[3] >> 4) & 3
            if mode not in (1, 3):
                continue
            offset = 4 + (1 + packet[4] if mode == 3 else 0)
            payload = packet[offset:]
            if (len(payload) < 14 or payload[:3] != b'\0\0\1'
                    or not (0xe0 <= payload[3] <= 0xef or payload[3] == 0xbd)
                    or not payload[7] & 0x80 or payload[8] < 5):
                continue
            stamp = payload[9:14]
            pts = (((stamp[0] >> 1) & 7) << 30 | stamp[1] << 22
                   | (stamp[2] >> 1) << 15 | stamp[3] << 7 | stamp[4] >> 1)
            if self.previous_pts is not None and pts - self.previous_pts < -(1 << 32):
                self.wrap_offset += 1 << 33
            self.previous_pts = pts
            pts += self.wrap_offset
            if self.first_pts is None:
                self.first_pts = pts
            seconds = max(0.0, (pts - self.first_pts) / 90_000)
            self.last_seconds = max(self.last_seconds, seconds)
            values.append(seconds)
        return (min(values), self.last_seconds) if values else (self.last_seconds, self.last_seconds)


def mp4_duration_seconds(path):
    """Read the movie's actual duration from mvhd, without an FFprobe process."""
    try:
        with Path(path).open('rb') as stream:
            total = Path(path).stat().st_size

            def atoms(start, end):
                position = start
                while position + 8 <= end:
                    stream.seek(position)
                    size, name = struct.unpack('>I4s', stream.read(8))
                    header = 8
                    if size == 1:
                        size = struct.unpack('>Q', stream.read(8))[0]
                        header = 16
                    elif size == 0:
                        size = end - position
                    if size < header or position + size > end:
                        return None
                    if name == b'moov':
                        value = atoms(position + header, position + size)
                        if value is not None:
                            return value
                    elif name == b'mvhd':
                        payload = stream.read(min(size - header, 40))
                        if payload and payload[0] == 1 and len(payload) >= 32:
                            timescale = struct.unpack_from('>I', payload, 20)[0]
                            duration = struct.unpack_from('>Q', payload, 24)[0]
                        elif payload and payload[0] == 0 and len(payload) >= 20:
                            timescale, duration = struct.unpack_from('>II', payload, 12)
                        else:
                            return None
                        return duration / timescale if timescale else None
                    position += size
                return None

            return atoms(0, total)
    except (OSError, ValueError, struct.error):
        return None

"""Mux/decode generated PCM and RGB only; no microphone or screen capture."""
import io
import math
import re
import struct
import subprocess

import pytest

from gamepadstudio.replay_timing import TimestampedAVWriter, mp4_duration_seconds
from gamepadstudio.replay_service import get_ffmpeg_path, _subprocess_hidden_flags


@pytest.fixture
def ffmpeg():
    path = get_ffmpeg_path()
    if not path:
        pytest.skip('Bundled FFmpeg unavailable')
    return path


def run(ffmpeg, arguments, data=None):
    result = subprocess.run([ffmpeg, '-hide_banner', *arguments], input=data,
                            capture_output=True, timeout=15, **_subprocess_hidden_flags())
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    return result


@pytest.mark.parametrize('options', [dict(width=0), dict(height=-1), dict(fps=0), dict(fps=float('nan')),
                                    dict(sample_rate=0), dict(sample_rate=48000.5), dict(channels=0),
                                    dict(channels=9), dict(width=True)])
def test_invalid_track_format_is_rejected_before_writing_header(options):
    stream = io.BytesIO()
    arguments = dict(stream=stream, width=2, height=2, fps=30, sample_rate=48000, channels=2)
    arguments.update(options)
    with pytest.raises(ValueError, match='Invalid'):
        TimestampedAVWriter(**arguments)
    assert stream.getvalue() == b''


@pytest.mark.parametrize('timestamp', [-1, float('nan'), float('inf'), True, '1'])
def test_invalid_audio_video_timestamp_writes_no_partial_block(timestamp):
    stream = io.BytesIO()
    writer = TimestampedAVWriter(stream, 2, 2, 30)
    before = stream.getvalue()
    with pytest.raises(ValueError, match='timestamp'):
        writer.write_frame(b'\0' * 12, timestamp)
    with pytest.raises(ValueError, match='timestamp'):
        writer.write_audio(b'\0' * 4, timestamp)
    assert stream.getvalue() == before


def test_audio_format_alignment_overlap_and_per_track_order_are_checked():
    stream = io.BytesIO()
    writer = TimestampedAVWriter(stream, 2, 2, 30)
    writer.write_frame(b'\0' * 12, .5)
    # Delayed audio retains its earlier source clock rather than video .5.
    assert writer.write_audio(b'\0' * (480 * 4), .1) == .1
    before = stream.getvalue()
    for operation, match in (
        (lambda: writer.write_audio(b'bad', .2), 'complete interleaved'),
        (lambda: writer.write_audio(b'', .2), 'complete interleaved'),
        (lambda: writer.write_audio(b'\0' * 4, .2, sample_rate=44100), 'sample rate'),
        (lambda: writer.write_audio(b'\0' * 4, .2, channels=1), 'channel count'),
        (lambda: writer.write_audio(b'\0' * 4, .09), 'backwards'),
        (lambda: writer.write_audio(b'\0' * 4, .105), 'overlap'),
        (lambda: writer.write_frame(b'\0' * 12, .49), 'backwards'),
    ):
        with pytest.raises(ValueError, match=match):
            operation()
        assert stream.getvalue() == before


def test_short_pipe_writes_preserve_complete_av_stream(ffmpeg):
    class ShortPipe(io.BytesIO):
        def write(self, data):
            return super().write(data[:7])

    stream = ShortPipe()
    writer = TimestampedAVWriter(stream, 2, 2, 30, sample_rate=48000, channels=1)
    writer.write_frame(b'\xff\0\0' * 4, 0)
    pcm = struct.pack('<' + 'h' * 128, *range(-64, 64))
    writer.write_audio(pcm, 0)
    audio = run(ffmpeg, ['-loglevel', 'error', '-f', 'matroska', '-i', 'pipe:0',
                         '-map', '0:a:0', '-c:a', 'pcm_s16le', '-f', 's16le', 'pipe:1'], stream.getvalue())
    assert audio.stdout == pcm


def test_pcm_track_preserves_submillisecond_pts_and_sample_bytes(ffmpeg):
    stream = io.BytesIO()
    writer = TimestampedAVWriter(stream, 2, 2, 30, sample_rate=48000, channels=1)
    writer.write_frame(b'\0' * 12, 0)
    pcm = struct.pack('<' + 'h' * 480, *[index - 240 for index in range(480)])
    assert writer.write_audio(pcm, .125123) == .125123
    decoded = run(ffmpeg, ['-f', 'matroska', '-i', 'pipe:0', '-map', '0:a:0', '-af', 'ashowinfo',
                           '-c:a', 'pcm_s16le', '-f', 's16le', 'pipe:1'], stream.getvalue())
    assert decoded.stdout == pcm
    text = decoded.stderr.decode(errors='replace')
    pts = [float(value) for value in re.findall(r'\[Parsed_ashowinfo_[^\]]+\].*pts_time:([\d.]+)', text)]
    assert pts and pts[0] == pytest.approx(.125123, abs=1/48000)
    assert '48000 Hz, mono' in text


def test_generated_audio_and_irregular_video_encode_to_mp4_with_matching_marker_time(ffmpeg, tmp_path):
    import numpy as np

    width = height = 64
    sample_rate = 48000
    video_pts = [0, .13, .5, .77, 1.0]
    marker = .77
    stream = io.BytesIO()
    writer = TimestampedAVWriter(stream, width, height, 30, sample_rate=sample_rate, channels=2)
    # Emit video callbacks first to simulate audio arriving substantially later.
    # This must retain the independent original timestamp of each track.
    for timestamp in video_pts:
        color = (200, 30, 40) if timestamp == marker else (30, 80, 120)
        writer.write_frame(bytes(color) * (width * height), timestamp)
    count = sample_rate
    times = np.arange(count) / sample_rate
    gate = (times >= marker) & (times < marker + .05)
    left = np.rint(20000 * np.sin(2 * math.pi * 440 * times) * gate).astype('<i2')
    right = np.rint(12000 * np.sin(2 * math.pi * 880 * times) * gate).astype('<i2')
    pcm = np.column_stack((left, right)).tobytes()
    chunk_samples = 480
    for index in range(0, count, chunk_samples):
        writer.write_audio(pcm[index*4:(index+chunk_samples)*4], index / sample_rate,
                           sample_rate=sample_rate, channels=2)
    output = tmp_path / 'synthetic-av.mp4'
    run(ffmpeg, ['-loglevel', 'error', '-y', '-f', 'matroska', '-i', 'pipe:0',
                 '-map', '0:v:0', '-map', '0:a:0', '-c:v', 'libx264', '-preset', 'ultrafast',
                 '-pix_fmt', 'yuv420p', '-fps_mode', 'passthrough', '-enc_time_base', '1:1000',
                 '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(output)], stream.getvalue())
    assert mp4_duration_seconds(output) == pytest.approx(1 + 1/30, abs=.01)
    video = run(ffmpeg, ['-i', str(output), '-map', '0:v:0', '-vf', 'showinfo', '-f', 'null', '-'])
    info = video.stderr.decode(errors='replace')
    actual_pts = [float(value) for value in re.findall(r'\[Parsed_showinfo_[^\]]+\].*pts_time:([\d.]+)', info)]
    assert actual_pts == pytest.approx(video_pts, abs=.002)
    audio = run(ffmpeg, ['-i', str(output), '-map', '0:a:0', '-c:a', 'pcm_s16le', '-f', 's16le', 'pipe:1'])
    assert '48000 Hz, stereo' in audio.stderr.decode(errors='replace')
    samples = np.frombuffer(audio.stdout, dtype='<i2').reshape(-1, 2)
    onset = np.flatnonzero(np.abs(samples[:, 0].astype(int)) > 6000)[0] / sample_rate
    assert onset == pytest.approx(marker, abs=.01)
    for channel, expected_hz in ((0, 440), (1, 880)):
        spectrum = np.abs(np.fft.rfft(samples[:, channel]))
        peak_hz = np.fft.rfftfreq(len(samples), d=1/sample_rate)[spectrum.argmax()]
        assert peak_hz == pytest.approx(expected_hz, abs=20)

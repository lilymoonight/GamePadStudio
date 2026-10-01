"""Video time must follow capture timestamps rather than nominal frame count."""
import io
import struct
import subprocess

import pytest

from gamepadstudio.replay_service import get_ffmpeg_path, _subprocess_hidden_flags
from gamepadstudio.replay_timing import (
    TimestampedRGBWriter, TransportStreamClock, mp4_duration_seconds,
)


def pts_packet(seconds, *, raw_pts=None):
    pts = round(seconds * 90_000) if raw_pts is None else raw_pts
    pts &= (1 << 33) - 1
    encoded = bytes((0x21 | ((pts >> 30) & 7) << 1, (pts >> 22) & 255,
                     1 | ((pts >> 15) & 127) << 1, (pts >> 7) & 255,
                     1 | (pts & 127) << 1))
    pes = b'\0\0\1\xe0\0\0\x80\x80\x05' + encoded
    return b'\x47\x41\x00\x10' + pes + b'\xff' * (184 - len(pes))


def test_transport_clock_tracks_media_pts_across_arbitrary_pipe_boundaries():
    clock = TransportStreamClock()
    stream = pts_packet(10) + pts_packet(10.2) + pts_packet(11.4)
    spans = [clock.span(stream[:189]), clock.span(stream[189:399]), clock.span(stream[399:])]
    assert spans == [(0.0, 0.0), (0.2, 0.2), (1.4, 1.4)]
    assert clock.span(b'') == (1.4, 1.4)


def test_transport_clock_handles_pts_wrap_and_encoder_frame_reordering():
    clock = TransportStreamClock()
    clock.span(pts_packet(0, raw_pts=(1 << 33) - 9000))
    assert clock.span(pts_packet(0, raw_pts=9000)) == (.2, .2)
    assert clock.span(pts_packet(0, raw_pts=0)) == (.1, .2)


@pytest.mark.parametrize('version', [0, 1])
def test_actual_movie_duration_v0_and_v1(tmp_path, version):
    if version:
        header = b'\x01\0\0\0' + b'\0' * 16 + struct.pack('>IQ', 1000, 12_345)
    else:
        header = b'\0' * 12 + struct.pack('>II', 1000, 12_345)
    mvhd = struct.pack('>I4s', 8 + len(header), b'mvhd') + header
    movie = struct.pack('>I4s', 8 + len(mvhd), b'moov') + mvhd
    output = tmp_path / 'duration.mp4'
    output.write_bytes(movie)
    assert mp4_duration_seconds(output) == 12.345
    output.write_bytes(b'not a movie')
    assert mp4_duration_seconds(output) is None


@pytest.mark.parametrize('timestamps', [
    [0, .2, .4, .6, .8, 1.0],          # MSS captures only five FPS.
    [0, .1, .7, .8, 1.4, 1.5],        # Encoder pipe has irregular backpressure.
])
def test_slow_and_irregular_capture_preserves_playback_speed(tmp_path, timestamps):
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        pytest.skip('FFmpeg unavailable')
    stream = io.BytesIO()
    writer = TimestampedRGBWriter(stream, 64, 64, 30)
    for index, timestamp in enumerate(timestamps):
        writer.write_frame(bytes((index * 30, 50, 120)) * (64 * 64), timestamp)
    output = tmp_path / 'real-timing.mp4'
    result = subprocess.run(
        [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'matroska',
         '-i', 'pipe:0', '-c:v', 'libx264', '-preset', 'ultrafast',
         '-fps_mode', 'passthrough', '-enc_time_base', '1:1000', str(output)],
        input=stream.getvalue(), capture_output=True, timeout=15, **_subprocess_hidden_flags(),
    )
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    # Rawvideo with input -r 30 would produce six frames / 30 = 0.2 seconds.
    assert mp4_duration_seconds(output) == pytest.approx(timestamps[-1] + 1 / 30, abs=.002)
    decoded = subprocess.run(
        [ffmpeg, '-hide_banner', '-i', str(output), '-vf', 'showinfo', '-f', 'null', '-'],
        capture_output=True, timeout=15, **_subprocess_hidden_flags(),
    )
    import re
    actual_pts = [float(value) for value in re.findall(r'pts_time:([\d.]+)', decoded.stderr.decode(errors='replace'))]
    assert actual_pts == pytest.approx(timestamps, abs=.002)


def test_writer_rejects_changed_dimensions_and_invalid_timestamp():
    writer = TimestampedRGBWriter(io.BytesIO(), 2, 2, 30)
    with pytest.raises(ValueError, match='dimensions'):
        writer.write_frame(b'bad', 0)
    with pytest.raises(ValueError, match='timestamp'):
        writer.write_frame(b'\0' * 12, float('nan'))


@pytest.mark.parametrize('hdr', [False, True], ids=['sdr-srgb', 'hdr-scrgb'])
def test_gray_levels_and_bt709_tags_survive_replay_encoding(tmp_path, monkeypatch, hdr):
    """Synthetic FP16 only: never initialize a desktop capture or DXGI driver."""
    from types import SimpleNamespace
    import numpy as np
    from gamepadstudio.hdr_capture import build_hdr_capture_plan, DXGIOutput
    from gamepadstudio import display_info, hdr_capture, replay_capture
    from gamepadstudio.replay_service import ReplayBufferEngine
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        pytest.skip('FFmpeg unavailable')
    width, height = 256, 256
    bbox = dict(left=0, top=0, width=width, height=height)
    display = dict(bbox, device_name='synthetic', hdr_enabled=hdr, sdr_white_nits=240)
    plan = None
    if hdr:
        plan = build_hdr_capture_plan(bbox, display, 30, outputs=[DXGIOutput(0, 0, 'synthetic', **bbox)])
        linear = np.array([0, .05, .25, .5, 1, 2, 4, 8], dtype='<f2')
        frame = np.ones((height, width, 4), dtype='<f2')
        frame[:, :, :3] = np.repeat(linear, 32)[None, :, None]
        rgb = plan.convert_frame(frame.tobytes())
        expected_codes = np.frombuffer(rgb, dtype=np.uint8).reshape(height, width, 3)[height // 2, ::32]
    else:
        srgb = np.array([0, 16, 32, 64, 128, 192, 224, 255], dtype=np.uint8)
        rgb = np.broadcast_to(np.repeat(srgb, 32)[None, :, None], (height, width, 3)).tobytes()
        normalized = srgb.astype(float) / 255
        linear = np.where(normalized <= .04045, normalized / 12.92, ((normalized + .055) / 1.055) ** 2.4)
        bt709 = np.where(linear < .018, 4.5 * linear, 1.099 * linear ** .45 - .099)
        expected_codes = np.repeat(np.rint(bt709 * 255).astype(np.uint8)[:, None], 3, axis=1)
    stream = io.BytesIO()
    writer = TimestampedRGBWriter(stream, width, height, 30)
    for timestamp in (0, .2, .4, .6):
        writer.write_frame(rgb, timestamp)
    engine = ReplayBufferEngine(tmp_path, codec='h264', bitrate_mbps=20)
    # Configure the actual replay adapter, while replacing the hardware source
    # and native display probe. This catches color-filter integration mistakes.
    monkeypatch.setattr(display_info, 'enumerate_displays', lambda: [display])
    monkeypatch.setattr(hdr_capture, 'build_hdr_capture_plan', lambda *args, **kwargs: plan)
    monkeypatch.setattr(replay_capture, '_HDRSource', lambda *args: SimpleNamespace(close=lambda: None))
    source = replay_capture.ReplayCapture(SimpleNamespace(close=lambda: None))
    source.configure(bbox, 30)
    assert source.capture_method == ('DXGI FP16' if hdr else 'GDI')
    encoded = subprocess.run(engine._encoder_command(ffmpeg, 'libx264', source),
                             input=stream.getvalue(), capture_output=True, timeout=15,
                             **_subprocess_hidden_flags())
    assert encoded.returncode == 0, encoded.stderr.decode(errors='replace')
    output = tmp_path / 'hdr-gray.mp4'
    mux = subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'mpegts',
                          '-i', 'pipe:0', '-c', 'copy', str(output)], input=encoded.stdout,
                         capture_output=True, timeout=15, **_subprocess_hidden_flags())
    assert mux.returncode == 0, mux.stderr.decode(errors='replace')
    decoded = subprocess.run([ffmpeg, '-hide_banner', '-i', str(output), '-vf', 'showinfo',
                              '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'],
                             capture_output=True, timeout=15, **_subprocess_hidden_flags())
    assert decoded.returncode == 0, decoded.stderr.decode(errors='replace')
    info = decoded.stderr.decode(errors='replace')
    for field in ('color_range:tv', 'color_space:bt709', 'color_primaries:bt709', 'color_trc:bt709'):
        assert field in info
    centers = np.arange(16, width, 32)
    actual = np.frombuffer(decoded.stdout, dtype=np.uint8).reshape(height, width, 3)[height // 2, centers]
    assert np.abs(actual.astype(int) - expected_codes.astype(int)).max() <= 3
    assert np.all(np.diff(actual[:, 0].astype(int)) > 0)  # Highlights stay distinct after encoding.
    assert actual[:, 0].tolist() == actual[:, 1].tolist() == actual[:, 2].tolist()

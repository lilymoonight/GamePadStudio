"""Apple hardware encoding is selected only after a successful encode probe."""
import io
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from gamepadstudio import replay_service as replay
from gamepadstudio.replay_timing import TimestampedRGBWriter


@pytest.fixture
def probe(monkeypatch):
    monkeypatch.setattr(replay, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(replay, '_CACHED_ENCODERS', {})
    monkeypatch.setattr(replay, 'get_ffmpeg_path', lambda: '/fixture/ffmpeg')
    state = SimpleNamespace(calls=[], results={})

    def run(command, **kwargs):
        state.calls.append((command, kwargs))
        encoder = command[command.index('-c:v') + 1]
        result = state.results.get(encoder, 1)
        if isinstance(result, Exception):
            raise result
        return subprocess.CompletedProcess(command, result)

    monkeypatch.setattr(replay.subprocess, 'run', run)
    return state


@pytest.mark.parametrize('codec,encoder', [('hevc', 'hevc_videotoolbox'), ('h264', 'h264_videotoolbox')])
def test_apple_hardware_requires_successful_sdr_encode_and_is_cached(probe, codec, encoder):
    probe.results[encoder] = 0
    assert replay.detect_hardware_encoder(codec) == encoder
    assert len(probe.calls) == 1
    command, options = probe.calls[0]
    assert command[command.index('-f') + 1] == 'lavfi'
    assert command[command.index('-i') + 1] == 'testsrc=size=256x256:rate=30'
    assert command[command.index('-allow_sw') + 1] == '0'
    assert command[command.index('-realtime') + 1] == '1'
    assert command[command.index('-pix_fmt') + 1] == 'yuv420p'
    assert command[-3:] == ['-f', 'null', '/dev/null']
    assert options == {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL, 'timeout': 2}
    assert replay.detect_hardware_encoder(codec) == encoder
    assert len(probe.calls) == 1


@pytest.mark.parametrize('failure', [1, OSError('encoder absent'), subprocess.TimeoutExpired('ffmpeg', 2)])
@pytest.mark.parametrize('codec,hardware,software', [
    ('hevc', 'hevc_videotoolbox', 'libx265'), ('h264', 'h264_videotoolbox', 'libx264')])
def test_apple_hardware_failure_uses_the_existing_software_fallback(probe, codec, hardware, software, failure):
    probe.results[hardware] = failure
    probe.results[software] = 0
    assert replay.detect_hardware_encoder(codec) == software
    assert [cmd[cmd.index('-c:v') + 1] for cmd, _ in probe.calls] == [hardware, software]
    assert '-allow_sw' not in probe.calls[-1][0]


def test_av1_does_not_claim_nonexistent_apple_hardware_encoding(probe):
    probe.results['libaom-av1'] = 0
    assert replay.detect_hardware_encoder('av1') == 'libaom-av1'
    assert len(probe.calls) == 1
    assert '-allow_sw' not in probe.calls[0][0]


def test_missing_ffmpeg_keeps_the_existing_software_choice_without_launching(probe, monkeypatch):
    monkeypatch.setattr(replay, 'get_ffmpeg_path', lambda: None)
    assert replay.detect_hardware_encoder('hevc') == 'libx265'
    assert replay.detect_hardware_encoder('h264') == 'libx264'
    assert probe.calls == []


@pytest.mark.parametrize('codec,candidates', [
    ('hevc', ['hevc_amf', 'hevc_nvenc', 'hevc_qsv', 'hevc_mf', 'libx265']),
    ('h264', ['h264_amf', 'h264_nvenc', 'h264_qsv', 'h264_mf', 'libx264']),
    ('av1', ['av1_amf', 'av1_nvenc', 'av1_qsv', 'libaom-av1']),
])
def test_windows_probe_keeps_its_existing_hardware_priority_and_fallback(probe, monkeypatch, codec, candidates):
    monkeypatch.setattr(replay, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(replay, '_subprocess_hidden_flags', lambda: {})
    probe.results[candidates[-1]] = 0
    assert replay.detect_hardware_encoder(codec) == candidates[-1]
    assert [cmd[cmd.index('-c:v') + 1] for cmd, _ in probe.calls] == candidates
    assert all(cmd[-1] == 'NUL' and '-allow_sw' not in cmd for cmd, _ in probe.calls)


@pytest.mark.parametrize('encoder', ['hevc_videotoolbox', 'h264_videotoolbox'])
def test_replay_uses_matching_hardware_options_and_limited_bt709_sdr(tmp_path, encoder):
    engine = replay.ReplayBufferEngine(tmp_path, codec='hevc', bitrate_mbps=20)
    command = engine._encoder_command('/fixture/ffmpeg', encoder, SimpleNamespace())
    assert command[command.index('-allow_sw') + 1] == '0'
    assert command[command.index('-realtime') + 1] == '1'
    assert command[command.index('-pix_fmt') + 1] == 'yuv420p'
    assert command[command.index('-color_range') + 1] == 'tv'
    for flag in ('-color_primaries', '-color_trc', '-colorspace'):
        assert command[command.index(flag) + 1] == 'bt709'
    assert command[command.index('-b:v') + 1] == '20M'
    assert 'out_range=tv' in command[command.index('-vf') + 1]


def test_windows_and_software_replay_commands_do_not_receive_apple_options(tmp_path):
    engine = replay.ReplayBufferEngine(tmp_path)
    for encoder in ('hevc_amf', 'h264_nvenc', 'libx264', 'libx265', 'libaom-av1'):
        command = engine._encoder_command('/fixture/ffmpeg', encoder, SimpleNamespace())
        assert '-allow_sw' not in command and '-realtime' not in command


@pytest.fixture
def isolated_ffmpeg_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(replay, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(replay.shutil, 'which', lambda name: None)
    actual_is_file = Path.is_file
    monkeypatch.setattr(replay.Path, 'is_file', lambda path: path.is_relative_to(tmp_path) and actual_is_file(path))
    monkeypatch.setitem(sys.modules, 'imageio_ffmpeg', None)
    return tmp_path


@pytest.mark.skipif(sys.platform != 'darwin', reason='Unix executable permissions are macOS specific')
def test_optional_ffmpeg_wheel_is_used_only_for_a_real_executable(isolated_ffmpeg_paths, monkeypatch):
    binary = isolated_ffmpeg_paths / 'standalone-ffmpeg'
    binary.write_bytes(b'not executed by this test')
    monkeypatch.setitem(sys.modules, 'imageio_ffmpeg', SimpleNamespace(get_ffmpeg_exe=lambda: str(binary)))
    binary.chmod(0o600)
    assert replay.get_ffmpeg_path() is None
    binary.chmod(0o700)
    assert replay.get_ffmpeg_path() == str(binary)
    binary.unlink()
    assert replay.get_ffmpeg_path() is None


def test_missing_optional_ffmpeg_wheel_still_reports_unavailable(isolated_ffmpeg_paths):
    assert replay.get_ffmpeg_path() is None


def test_optional_wheel_lookup_failure_still_reports_unavailable(isolated_ffmpeg_paths, monkeypatch):
    def unavailable():
        raise RuntimeError('optional binary is unavailable')
    monkeypatch.setitem(sys.modules, 'imageio_ffmpeg', SimpleNamespace(get_ffmpeg_exe=unavailable))
    assert replay.get_ffmpeg_path() is None


def test_project_ffmpeg_takes_priority_over_optional_wheel(isolated_ffmpeg_paths, monkeypatch):
    binary = isolated_ffmpeg_paths / 'bin' / 'ffmpeg'
    binary.parent.mkdir()
    binary.write_bytes(b'fixture only')
    monkeypatch.setattr(replay, '__file__', str(isolated_ffmpeg_paths / 'gamepadstudio' / 'replay_service.py'))
    monkeypatch.setitem(sys.modules, 'imageio_ffmpeg', SimpleNamespace(
        get_ffmpeg_exe=lambda: pytest.fail('Packaged bin/ must win over optional wheel')))
    assert replay.get_ffmpeg_path() == str(binary)


@pytest.mark.parametrize('codec', ['hevc', 'h264'])
def test_installed_mac_ffmpeg_encodes_synthetic_rgb_through_the_actual_replay_command(tmp_path, codec):
    if sys.platform != 'darwin':
        pytest.skip('Native Apple hardware encoder')
    ffmpeg = replay.get_ffmpeg_path()
    if not ffmpeg:
        pytest.skip('FFmpeg is an optional dependency and is not installed')
    replay._CACHED_ENCODERS.pop(codec, None)
    encoder = replay.detect_hardware_encoder(codec)
    if not encoder.endswith('_videotoolbox'):
        pytest.skip('Installed FFmpeg/host does not provide usable VideoToolbox hardware encoding')
    stream = io.BytesIO()
    writer = TimestampedRGBWriter(stream, 256, 256, 30)
    for frame in range(3):
        writer.write_frame(bytes((frame * 60, 80, 170)) * (256 * 256), frame / 30)
    engine = replay.ReplayBufferEngine(tmp_path, codec=codec, bitrate_mbps=2)
    result = subprocess.run(engine._encoder_command(ffmpeg, encoder, SimpleNamespace()),
                            input=stream.getvalue(), capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')
    assert len(result.stdout) >= 188 and result.stdout[0] == 0x47
    # Three tiny frames can be below FFmpeg's automatic TS format-probe
    # threshold; the replay protocol already fixes this stream as MPEG-TS.
    decoded = subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'mpegts', '-i', 'pipe:0',
                              '-map', '0:v:0', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                              '-fps_mode', 'passthrough', 'pipe:1'],
                             input=result.stdout, capture_output=True, timeout=15)
    assert decoded.returncode == 0, decoded.stderr.decode('utf-8', errors='replace')
    frame_bytes = 256 * 256 * 3
    assert len(decoded.stdout) == frame_bytes * 3
    for frame in range(3):
        pixel = frame * frame_bytes + (128 * 256 + 128) * 3
        assert list(decoded.stdout[pixel:pixel + 3]) == pytest.approx([frame * 60, 80, 170], abs=8)

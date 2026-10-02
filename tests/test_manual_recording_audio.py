"""Full recorder integration with generated RGB/PCM only, never live capture."""
from collections import deque
import math
import re
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

from gamepadstudio import manual_recording as module, macos_permissions
from gamepadstudio.replay_timing import mp4_duration_seconds
from gamepadstudio.replay_service import _subprocess_hidden_flags


RATE = 48000
VIDEO_PTS = (0, .13, .5, .77, 1.0)
MARKER = .77


class SyntheticAVSource:
    """SCK-shaped source: logical bounds differ from pixels, clocks agree."""
    monitors = [dict(left=-32, top=0, width=32, height=32),
                dict(left=-32, top=0, width=32, height=32, display_id=17, scale=2)]
    frame_size = (64, 64)
    capture_method = 'synthetic-system-audio'
    color_mode = 'SDR BT.709'

    def __init__(self, recorder, *, audio_start=0, delayed=False, audio_on_cancel=False):
        self.recorder = recorder
        # A source-domain origin, deliberately unrelated to callback wall time.
        self.origin = 98765.432123
        self.audio_start = audio_start
        self.delayed = delayed
        self.audio_on_cancel = audio_on_cancel
        self.audio_format = (RATE, 2)
        self.audio_enabled = False
        self.closed = self.cancelled = False
        self.configured = None
        self.audio_error = None
        self._frame_index = 0
        self._audio_sent = False
        self.pending = deque()
        times = audio_start + np.arange(math.ceil((1-audio_start) * RATE)) / RATE
        gate = (times >= MARKER) & (times < MARKER + .05)
        left = np.rint(20000 * np.sin(2 * math.pi * 440 * times) * gate).astype('<i2')
        right = np.rint(12000 * np.sin(2 * math.pi * 880 * times) * gate).astype('<i2')
        pcm = np.column_stack((left, right)).tobytes()
        self.packets = [SimpleNamespace(pcm=pcm[index*4:(index+480)*4],
                                       timestamp=self.origin+audio_start+index/RATE,
                                       sample_rate=RATE, channels=2)
                        for index in range(0, len(times), 480)]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.cancel()
        self.closed = True

    def configure(self, bbox, fps, *, include_system_audio=False):
        self.configured = (dict(bbox), fps, include_system_audio)
        self.audio_enabled = include_system_audio

    def _deliver_audio(self):
        if not self._audio_sent:
            self.pending.extend(self.packets)
            self._audio_sent = True

    def cancel(self):
        self.cancelled = True
        if self.audio_on_cancel:
            # A real SCK callback may be queued between the final video poll
            # and stopCapture completing; cancellation must retain that PCM.
            self._deliver_audio()

    def grab(self, _):
        index = self._frame_index
        if index >= len(VIDEO_PTS):
            self.recorder._stop.set()
            return SimpleNamespace(rgb=b'', timestamp=self.origin + 1.1)
        self._frame_index += 1
        if not self.audio_on_cancel and (not self.delayed or index == len(VIDEO_PTS)-1):
            self._deliver_audio()
        stamp = VIDEO_PTS[index]
        color = (200, 30, 40) if stamp == MARKER else (30, 80, 120)
        return SimpleNamespace(rgb=bytes(color) * (64*64), timestamp=self.origin + stamp)

    def read_audio(self):
        if self.audio_error:
            raise RuntimeError(self.audio_error)
        return self.pending.popleft() if self.pending else None


@pytest.fixture
def av_recording(monkeypatch, tmp_path):
    ffmpeg = module.get_ffmpeg_path()
    if not ffmpeg:
        pytest.skip('FFmpeg unavailable; this test requires real synthetic encode/decode')
    # Scope the platform override to this module; never change process-wide
    # sys.platform or allow a native capture/permission call.
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(module, 'get_ffmpeg_path', lambda: ffmpeg)
    monkeypatch.setattr(module, 'detect_hardware_encoder', lambda _: 'libx264')
    monkeypatch.setattr(module, 'smart_foreground_info', lambda: (99, 'Synthetic Audio Window'))
    monkeypatch.setattr(macos_permissions, 'screen_capture_permission_status', lambda: {'granted': True})
    active = []

    def create(**options):
        recorder = module.ManualRecording(tmp_path, capture_mode='monitor_1', codec='h264', fps=30)
        source = SyntheticAVSource(recorder, **options)
        monkeypatch.setattr(module, 'create_replay_capture', lambda: source)
        active.append(recorder)
        return recorder, source, ffmpeg

    yield create
    for recorder in active:
        if recorder.status()['running']:
            recorder.stop()


def finish(recorder):
    assert recorder.start()
    recorder._worker.join(timeout=15)
    assert not recorder._worker.is_alive(), recorder.status()
    return recorder.status()


def decode(ffmpeg, path, arguments):
    result = subprocess.run([ffmpeg, '-hide_banner', '-i', str(path), *arguments],
                            capture_output=True, timeout=15, **_subprocess_hidden_flags())
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    return result


def assert_matching_av(ffmpeg, path):
    video = decode(ffmpeg, path, ['-map', '0:v:0', '-vf', 'showinfo', '-pix_fmt', 'rgb24',
                                  '-fps_mode', 'passthrough', '-f', 'rawvideo', 'pipe:1'])
    video_pts = [float(value) for value in re.findall(
        r'\[Parsed_showinfo_[^\]]+\].*pts_time:([\d.]+)', video.stderr.decode(errors='replace'))]
    assert video_pts == pytest.approx(VIDEO_PTS, abs=.002)
    frames = np.frombuffer(video.stdout, dtype=np.uint8).reshape(-1, 64, 64, 3)
    means = frames.mean(axis=(1, 2))
    red = np.flatnonzero(means[:, 0] - means[:, 2] > 50)
    assert red.tolist() == [3]
    video_marker_time = video_pts[red[0]]

    audio = decode(ffmpeg, path, ['-map', '0:a:0', '-af', 'ashowinfo',
                                  '-c:a', 'pcm_s16le', '-f', 's16le', 'pipe:1'])
    info = audio.stderr.decode(errors='replace')
    assert '48000 Hz, stereo' in info and 'Audio: aac' in info
    audio_pts = [float(value) for value in re.findall(
        r'\[Parsed_ashowinfo_[^\]]+\].*pts_time:([\d.]+)', info)]
    assert audio_pts and audio_pts == sorted(audio_pts)
    samples = np.frombuffer(audio.stdout, dtype='<i2').reshape(-1, 2)
    onset = np.flatnonzero(np.abs(samples[:, 0].astype(int)) > 6000)[0]
    audio_marker_time = audio_pts[0] + onset / RATE
    assert audio_marker_time == pytest.approx(video_marker_time, abs=.012)
    for channel, frequency in ((0, 440), (1, 880)):
        magnitude = np.abs(np.fft.rfft(samples[:, channel]))
        peak_hz = np.fft.rfftfreq(len(samples), d=1/RATE)[magnitude.argmax()]
        assert peak_hz == pytest.approx(frequency, abs=20)
    assert mp4_duration_seconds(path) == pytest.approx(1+1/30, abs=.012)
    return audio_pts


@pytest.mark.parametrize('audio_start,delayed', [(0, False), (.125123, True)])
def test_recorder_writes_system_audio_with_common_origin_and_original_pts(av_recording, audio_start, delayed):
    recorder, source, ffmpeg = av_recording(audio_start=audio_start, delayed=delayed)
    completed = []
    recorder.on_saved = lambda path, error: completed.append((path, error))
    status = finish(recorder)
    assert status['phase'] == 'idle' and status['last_error'] == ''
    assert status['frames'] == len(VIDEO_PTS) and status['audio'] is True
    assert completed == [(status['path'], '')]
    assert source.configured[2] is True
    assert source.closed and source.cancelled and not source.pending
    pts = assert_matching_av(ffmpeg, status['path'])
    # AAC may expose one encoder-delay frame before the first PCM timestamp.
    # Including that preroll with its actual PTS preserves the sound marker;
    # requiring the first decoded frame to equal PCM start would be incorrect.
    assert -.003 <= audio_start - pts[0] <= 1024/RATE + .003
    assert len(list(recorder.save_dir.glob('*.mp4'))) == 1
    assert not list(recorder.save_dir.glob('.*.partial.mp4'))


def test_stop_drains_final_system_audio_packets_before_finalizing_mp4(av_recording):
    recorder, source, ffmpeg = av_recording(audio_on_cancel=True)
    status = finish(recorder)
    assert status['phase'] == 'idle' and status['last_error'] == ''
    assert status['audio'] is True
    assert not source.pending
    assert_matching_av(ffmpeg, status['path'])


def test_audio_overrun_observed_at_stop_is_not_swallowed_as_normal_cancellation(av_recording, monkeypatch):
    recorder, source, _ = av_recording(audio_on_cancel=True)
    original_cancel = source.cancel

    def cancel_with_audio_error():
        original_cancel()
        source.audio_error = '系统音频队列溢出，尾部音轨不可恢复'

    monkeypatch.setattr(source, 'cancel', cancel_with_audio_error)
    completed = []
    recorder.on_saved = lambda path, error: completed.append((path, error))
    status = finish(recorder)
    assert status['phase'] == 'failed' and status['path'] == ''
    assert '队列溢出' in status['last_error']
    assert completed and completed[0][0] == '' and '队列溢出' in completed[0][1]
    assert not list(recorder.save_dir.iterdir())


@pytest.mark.parametrize('fault,match', [
    ('format', 'sample rate'), ('clock', 'timestamp'), ('overrun', '队列溢出'),
])
def test_audio_failure_removes_partial_recording_and_reports_reason(av_recording, fault, match):
    recorder, source, _ = av_recording()
    if fault == 'format':
        source.packets[0].sample_rate = 44100
    elif fault == 'clock':
        # A callback-arrival/wrong-domain timestamp must not be accepted as
        # though it were on the video's source clock.
        source.packets[0].timestamp = source.origin - .1
    else:
        source.audio_error = '系统音频队列溢出，请降低录制负载后重试'
    completed = []
    recorder.on_saved = lambda path, error: completed.append((path, error))
    status = finish(recorder)
    assert status['phase'] == 'failed' and status['path'] == ''
    assert match in status['last_error']
    assert completed and completed[0][0] == '' and match in completed[0][1]
    assert source.closed and source.cancelled
    assert not list(recorder.save_dir.iterdir())

"""ScreenCaptureKit contracts use synthetic pixels and injected native services."""
import io
import json
import subprocess
import threading
from collections import deque
from unittest.mock import Mock
from pathlib import Path
import sys
from types import SimpleNamespace

from PIL import Image
import pytest

from gamepadstudio import macos_capture as capture
from gamepadstudio import macos_display
from gamepadstudio.hdr_capture import tone_map_edr


def display(identity=1, left=0, top=0, width=8, height=4, scale=2, rate=60, hdr=False):
    return dict(display_id=identity, device_name=f'CGDisplay:{identity}', left=left, top=top,
                width=width, height=height, pixel_width=round(width*scale), pixel_height=round(height*scale),
                scale=scale, refresh_hz=rate, hdr_enabled=hdr, hdr_supported=True, sdr_white_nits=None)


def metadata(rows=None, hdr_api=True):
    return dict(version=1, displays=rows or [display()], sck_available=True, hdr_capture_available=hdr_api)


def frame(row=None, *, timestamp=10., hdr=False, transfer='bt709', rgb=None, **overrides):
    row = row or display()
    width, height = row['pixel_width'], row['pixel_height']
    value = dict(version=1, display_id=row['display_id'], width=width, height=height,
                 bytes=width*height*3, timestamp=timestamp, pixel_format='rgb24', color_primaries='bt709',
                 transfer=transfer, color_managed=True, hdr_capture=hdr, hdr_verified=hdr, tone_mapped=hdr,
                 source_pixel_format='rgba16f' if hdr else 'bgra8',
                 source_color_space='extended-linear-srgb' if hdr else 'srgb')
    value.update(overrides)
    return capture.NativeFrame(rgb or bytes((60, 90, 170))*width*height, width, height, timestamp, value)


def packet(value):
    return json.dumps(value.metadata).encode() + b'\n' + value.rgb


class Source:
    def __init__(self, value):
        self.value = value
        self.closed = False
    def latest(self):
        if self.closed:
            raise RuntimeError('cancelled')
        return self.value
    def close(self):
        self.closed = True


def adapter(rows=None, *, hdr_api=True, frames=None, permission=True):
    rows = rows or [display()]
    calls = []
    sources = []
    def factory(executable, identity, fps, transfer):
        calls.append((executable, identity, fps, transfer))
        row = next(row for row in rows if row['display_id'] == identity)
        source = Source((frames or {}).get(identity) or frame(row, hdr=row['hdr_enabled'] is True))
        sources.append(source)
        return source
    obj = capture.MacCapture(metadata=metadata(rows, hdr_api), executable='/fixture/helper', transfer='bt709',
                             source_factory=factory, permission_check=lambda: {'granted': permission, 'reason':'authorize'})
    return obj, calls, sources


@pytest.mark.parametrize('hdr', [False, True])
def test_protocol_validated_rgb_and_float_hdr(hdr):
    value = frame(hdr=hdr)
    result = capture.read_frame(io.BytesIO(packet(value)))
    assert result == value


@pytest.mark.parametrize('changes,reason', [
    ({'width':True}, '尺寸'), ({'bytes':2**32}, '尺寸'), ({'height':0}, '尺寸'),
    ({'timestamp':float('nan')}, '时间'), ({'pixel_format':'bgra8'}, '颜色'),
    ({'color_managed':False}, '颜色'), ({'transfer':'pq'}, '颜色'),
    ({'hdr_verified':False}, 'HDR'), ({'tone_mapped':False}, 'HDR'),
    ({'source_pixel_format':'bgra8'}, 'HDR'), ({'source_color_space':'srgb'}, 'HDR'),
])
def test_protocol_rejects_unverified_or_invalid_frames(changes, reason):
    value = frame(hdr=True, **changes)
    with pytest.raises(RuntimeError, match=reason):
        capture.read_frame(io.BytesIO(packet(value)))


def test_protocol_rejects_partial_header_and_payload():
    value = packet(frame())
    for data in (b'', b'{}', b'x'*65537, value[:-1]):
        with pytest.raises((RuntimeError, ValueError)):
            capture.read_frame(io.BytesIO(data))


def test_retina_geometry_configures_physical_frame_size_without_changing_cg_points():
    obj, calls, sources = adapter()
    assert obj.monitors[1]['width'] == 8 and obj.monitors[1]['pixel_width'] == 16
    obj.configure(obj.monitors[1], 30)
    assert obj.frame_size == (16, 8)
    assert obj.bbox == dict(left=0., top=0., width=8., height=4.)
    assert calls == [('/fixture/helper', 1, 30, 'bt709')]
    assert len(obj.grab(obj.bbox).rgb) == 16*8*3
    obj.close(); obj.close()
    assert all(source.closed for source in sources)


def test_equal_refresh_mixed_hdr_panorama_tone_maps_each_source_before_composite():
    rows = [display(left=-8, hdr=True), display(2, scale=1)]
    obj, calls, _ = adapter(rows)
    obj.configure(obj.monitors[0], 30)
    assert obj.frame_size == (32, 8)
    assert obj.color_mode == 'HDR → SDR BT.709'
    assert [call[1] for call in calls] == [1, 2]
    assert len(obj.grab(obj.bbox).rgb) == 32*8*3
    obj.close()


@pytest.mark.parametrize('rate', [144, None, 59.94])
def test_combined_capture_refuses_mixed_or_unconfirmed_refresh_before_starting_native_streams(rate):
    obj, calls, _ = adapter([display(left=-8), display(2, rate=rate)])
    with pytest.raises(RuntimeError, match='刷新率'):
        obj.configure(obj.monitors[0])
    assert not calls


@pytest.mark.parametrize('rows,hdr_api,reason', [([display(hdr=True)], False, 'macOS 15'),
                                              ([display(hdr=None)], True, 'EDR')])
def test_hdr_is_never_silently_downgraded(rows, hdr_api, reason):
    obj, calls, _ = adapter(rows, hdr_api=hdr_api)
    with pytest.raises(RuntimeError, match=reason):
        obj.configure(obj.monitors[0])
    assert not calls


def test_hdr_stream_cannot_return_sdr_or_a_different_display_or_pixel_dimensions():
    row = display(hdr=True)
    obj, _, sources = adapter([row], frames={1:frame(row)})
    obj.configure(obj.monitors[0])
    with pytest.raises(RuntimeError, match='浮点'):
        obj.grab(obj.bbox)
    sources[0].value = frame(row, hdr=True, display_id=2)
    with pytest.raises(RuntimeError, match='身份'):
        obj.grab(obj.bbox)
    value = frame(row, hdr=True)
    sources[0].value = capture.NativeFrame(value.rgb, value.width-2, value.height, value.timestamp, value.metadata)
    with pytest.raises(RuntimeError, match='物理像素'):
        obj.grab(obj.bbox)
    obj.close()


def test_permission_is_preflight_only_and_native_stream_is_never_started_when_denied():
    obj, calls, _ = adapter(permission=False)
    with pytest.raises(RuntimeError, match='authorize'):
        obj.configure(obj.monitors[1])
    assert not calls


def test_mixed_retina_scale_and_negative_coordinates_keep_layout_and_black_screen_gaps():
    rows = [display(left=-8, top=-2), display(2, left=2, top=0, width=4, height=2, scale=1)]
    images = [Image.new('RGB',(16,8),'red'), Image.new('RGB',(4,2),'blue')]
    union = capture.monitors_from_displays(rows)[0]
    result = capture.composite_frames(union, rows, images)
    assert result.size == (28, 8)
    assert result.getpixel((0,0)) == (255,0,0)
    assert result.getpixel((18,6)) == (0,0,0)
    assert result.getpixel((25,6)) == (0,0,255)
    cropped = capture.composite_frames(dict(left=-4,top=0,width=8,height=2),rows,images)
    assert cropped.size == (16,4) and cropped.getpixel((0,0)) == (255,0,0)
    assert cropped.getpixel((14,0)) == (0,0,255)


def test_static_frame_keeps_elapsed_time_and_new_source_pts_does_not_move_backwards(monkeypatch):
    clock = [100.]
    monkeypatch.setattr(capture.time,'monotonic',lambda:clock[0])
    obj, _, sources = adapter()
    obj.configure(obj.monitors[1])
    first = obj.grab(obj.bbox)
    clock[0] = 102.
    second = obj.grab(obj.bbox)
    sources[0].value = frame(timestamp=11.9)
    third = obj.grab(obj.bbox)
    assert first.timestamp == 100 and second.timestamp == 102
    assert third.timestamp >= second.timestamp
    obj.close()


@pytest.mark.parametrize('kind,identity', [('display',1), ('window',77)])
def test_screenshot_returns_physical_rgb_image_and_verifies_native_target(kind, identity):
    value = frame(transfer='srgb', **{f'{kind}_id':identity})
    calls = []
    def run(command, **kwargs):
        calls.append((command,kwargs))
        return subprocess.CompletedProcess(command,0,stdout=packet(value),stderr=b'')
    with capture.MacCapture(metadata=metadata(),executable='/fake',runner=run,
                             permission_check=lambda:{'granted':True}) as obj:
        result = obj.capture_display(identity) if kind == 'display' else obj.capture_window(identity)
        assert result.mode == 'RGB' and result.size == (16,8)
        assert calls[0][0] == ['/fake','shot',kind,str(identity),'srgb','30']
        value.metadata[f'{kind}_id'] = identity+1
        with pytest.raises(RuntimeError,match='身份'):
            obj.capture_display(identity) if kind == 'display' else obj.capture_window(identity)


def test_metadata_timeout_is_an_explicit_failure():
    def run(*args,**kwargs):
        raise subprocess.TimeoutExpired('fixture',8)
    with pytest.raises(RuntimeError,match='超时'):
        macos_display.display_metadata(executable='/fixture',runner=run)


def test_macos_relative_edr_tone_map_preserves_hues_and_hdr_highlight_order():
    import numpy as np
    values = np.array([[0,0,0,1],[.18,.18,.18,1],[1,1,1,1],[2,2,2,1],[8,8,8,1],[4,2,1,1]],dtype=np.float16)
    converted = list(tone_map_edr(values.tobytes(),6,1))
    assert converted == [0,0,0,125,125,125,241,241,241,251,251,251,254,254,254,253,190,142]
    assert converted[6] < converted[9] < converted[12]


def audio_packet(pcm=b'\x01\x00\xff\xff'*48, *, timestamp=10., **changes):
    header = dict(version=1, type='audio', bytes=len(pcm), timestamp=timestamp, audio_format='s16le',
                  sample_rate=48000, channels=2, audio_source='system', microphone=False)
    header.update(changes)
    return json.dumps(header).encode()+b'\n'+pcm


def test_audio_video_packets_share_the_source_clock_and_are_demultiplexed_exactly():
    stream = io.BytesIO(packet(frame(timestamp=10.))+audio_packet(timestamp=10.025)+packet(frame(timestamp=10.05)))
    assert capture.read_packet(stream).timestamp == 10.
    audio = capture.read_packet(stream)
    assert audio == capture.AudioFrame(b'\x01\x00\xff\xff'*48,10.025)
    assert capture.read_packet(stream).timestamp == 10.05
    with pytest.raises(RuntimeError,match='非图像'):
        capture.read_frame(io.BytesIO(audio_packet()))


@pytest.mark.parametrize('changes', [{'sample_rate':44100}, {'channels':1}, {'microphone':True},
                                     {'audio_source':'microphone'}, {'audio_format':'f32le'},
                                     {'bytes':3}, {'bytes':True}, {'bytes':4194308}, {'timestamp':float('nan')}])
def test_audio_protocol_rejects_undeclared_microphone_or_format_changes(changes):
    with pytest.raises(RuntimeError):
        capture.read_packet(io.BytesIO(audio_packet(**changes)))


class AudioSource(Source):
    def __init__(self, value, audio):
        super().__init__(value)
        self.audio = deque(audio)
    def read_audio(self):
        return self.audio.popleft() if self.audio else None


def test_panorama_captures_system_audio_once_and_trims_samples_before_first_video(monkeypatch):
    monkeypatch.setattr(capture.time,'monotonic',lambda:100.)
    rows = [display(left=-8), display(2)]
    calls = []
    def factory(executable, identity, fps, transfer, **kwargs):
        calls.append(kwargs)
        row = next(row for row in rows if row['display_id'] == identity)
        return AudioSource(frame(row,timestamp=10.),[
            capture.AudioFrame(b'\x01\x00\xff\xff'*480,9.98),
            capture.AudioFrame(b'\x01\x00\xff\xff'*960,9.99),
            capture.AudioFrame(b'\x02\x00\xfe\xff'*480,10.01)])
    obj = capture.MacCapture(metadata=metadata(rows),executable='/fixture',transfer='bt709',
                             source_factory=factory,permission_check=lambda:{'granted':True})
    obj.configure(obj.monitors[0],include_system_audio=True)
    assert calls == [{'system_audio':True},{}]
    assert obj.audio_format == (48000,2) and obj.read_audio() is None
    assert obj.grab(obj.bbox).timestamp == 100.
    first = obj.read_audio()
    assert first.timestamp == pytest.approx(100., abs=1/48000)
    assert len(first.pcm) in (479*4,480*4)  # float source PTS rounding can trim one extra sample
    second = obj.read_audio()
    assert second.timestamp == pytest.approx(100.01) and len(second.pcm) == 480*4
    assert obj.read_audio() is None
    obj.close()


def test_cancel_attempts_every_display_even_when_first_source_cleanup_fails():
    obj, _, sources = adapter([display(left=-8),display(2)])
    obj.configure(obj.monitors[0])
    close_first = Mock(side_effect=[RuntimeError('still running'),None])
    sources[0].close = close_first
    with pytest.raises(RuntimeError,match='still running'):
        obj.cancel()
    assert sources[1].closed
    obj.cancel()
    assert close_first.call_count == 2


def test_native_source_cleanup_retries_unreaped_process_and_closes_unblocked_pipes():
    obj = capture._NativeSource.__new__(capture._NativeSource)
    obj.condition = threading.Condition()
    obj.closed = obj.cleanup_complete = False
    obj.reader = Mock(is_alive=Mock(return_value=False))
    obj.errors = Mock(is_alive=Mock(return_value=False))
    obj.process = Mock()
    obj.process.poll.return_value = None
    obj.process.wait.side_effect = subprocess.TimeoutExpired('synthetic',2)
    obj.process.stdout = io.BytesIO(); obj.process.stderr = io.BytesIO()
    with pytest.raises(RuntimeError,match='回收'):
        obj.close()
    assert obj.process.kill.call_count == 1 and obj.process.stdout.closed and obj.process.stderr.closed
    obj.process.poll.return_value = 0
    obj.close()
    assert obj.cleanup_complete


def test_native_demux_audio_queue_overflow_is_explicit_instead_of_dropping_samples():
    obj = capture._NativeSource.__new__(capture._NativeSource)
    obj.closed = False
    obj.system_audio = True
    obj.audio = deque(); obj.audio_bytes = 0
    obj.condition = threading.Condition()
    obj.stderr = bytearray(); obj.error = ''
    obj.errors = Mock()
    block = b'\0'*192000
    obj.process = SimpleNamespace(stdout=io.BytesIO(b''.join(audio_packet(block,timestamp=10+i) for i in range(6))))
    obj._read()
    assert '溢出' in obj.error and len(obj.audio) == 5 and obj.audio_bytes == 960000


def test_replay_loop_uses_retina_physical_size_and_shared_audio_pts(monkeypatch, tmp_path):
    from gamepadstudio import replay_service as replay
    from gamepadstudio.replay_timing import TimestampedAVWriter
    engine = replay.ReplayBufferEngine(tmp_path, capture_mode='primary')
    row = display()
    class FakeCapture:
        monitors = capture.monitors_from_displays([row])
        frame_size = (16,8)
        audio_format = (48000,2)
        capture_method = 'synthetic ScreenCaptureKit'
        color_mode = 'SDR BT.709'
        configure = Mock()
        def __init__(self):
            self.index = 0
            self.pending = None
        def __enter__(self):
            return self
        def __exit__(self,*args):
            pass
        def grab(self,bbox):
            timestamp = 100 + self.index * .04
            self.index += 1
            self.pending = capture.AudioFrame(b'\0'*1920,timestamp+.005)
            return SimpleNamespace(rgb=b'\0'*(16*8*3),timestamp=timestamp)
        def read_audio(self):
            value,self.pending = self.pending,None
            return value
    source = FakeCapture()
    pts = []; audio_pts = []
    class SpyWriter(TimestampedAVWriter):
        def write_frame(self,rgb,timestamp):
            value = super().write_frame(rgb,timestamp)
            pts.append(timestamp)
            if len(pts) == 3:
                engine.running = False
            return value
        def write_audio(self,pcm,timestamp,**kwargs):
            audio_pts.append(timestamp)
            return super().write_audio(pcm,timestamp,**kwargs)
    proc = SimpleNamespace(stdin=io.BytesIO(),stdout=io.BytesIO(),stderr=io.BytesIO(),
                           poll=lambda:None,wait=lambda timeout:0,kill=lambda:None)
    monkeypatch.setattr(replay,'sys',SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(replay,'_ensure_dpi_awareness',lambda:None)
    monkeypatch.setattr(replay,'get_ffmpeg_path',lambda:'/fixture/ffmpeg')
    monkeypatch.setattr(replay,'detect_hardware_encoder',lambda codec:'h264_videotoolbox')
    monkeypatch.setattr(replay,'_assign_process_to_job',lambda value:None)
    monkeypatch.setattr(replay,'create_replay_capture',lambda:source)
    monkeypatch.setattr(replay,'get_target_monitor_bbox',lambda obj,mode:obj.monitors[1])
    monkeypatch.setattr(replay,'TimestampedAVWriter',SpyWriter)
    monkeypatch.setattr(replay.time,'sleep',lambda value:None)
    popen = Mock(return_value=proc)
    monkeypatch.setattr(replay.subprocess,'Popen',popen)
    engine.running = True
    engine._capture_and_encode_loop()
    for attr in ('_reader_thread','_stderr_thread'):
        getattr(engine,attr).join(timeout=1)
    engine._ffmpeg_proc = None
    assert not engine._last_error
    source.configure.assert_called_once_with(source.monitors[1],30,include_system_audio=True)
    assert (engine._current_width,engine._current_height) == (16,8)
    assert pts == pytest.approx([0,.04,.08])
    assert audio_pts == pytest.approx([.005,.045,.085])
    command = popen.call_args.args[0]
    assert command[command.index('-c:a')+1] == 'aac' and '0:a:0' in command
    assert engine.get_status()['audio'] and engine.get_status()['microphone'] is False


def window_adapter(*, pid=99, window_id=77, width=8, height=4, hdr=False):
    value = frame(hdr=hdr,pid=pid,window_id=window_id,window_size_points={'width':width,'height':height})
    source = AudioSource(value,[])
    factory = Mock(return_value=source)
    obj = capture.MacCapture(metadata=metadata(),executable='/fixture',transfer='bt709',source_factory=factory,
                             permission_check=lambda:{'granted':True})
    return obj,source,factory


def test_window_recording_uses_first_real_pixel_dimensions_and_filters_application_audio():
    obj,source,factory = window_adapter()
    obj.configure_window(77,30,expected_pid=99)
    factory.assert_called_once_with('/fixture',77,30,'bt709',kind='window',system_audio=True)
    assert obj.frame_size == (16,8) and obj.window_size == (8,4)
    assert obj.audio_scope == 'application' and obj.audio_format == (48000,2)
    assert len(obj.grab(None).rgb) == 16*8*3
    obj.close()
    assert source.closed


@pytest.mark.parametrize('actual_pid,actual_window',[(100,77),(99,78),(None,77),(True,77)])
def test_window_identity_mismatch_cancels_native_source_before_encoder_starts(actual_pid,actual_window):
    obj,source,_ = window_adapter(pid=actual_pid,window_id=actual_window)
    with pytest.raises(RuntimeError,match='身份'):
        obj.configure_window(77,expected_pid=99)
    assert source.closed


def test_window_id_and_pid_are_locked_even_if_foreground_app_changes():
    obj,source,_ = window_adapter()
    obj.configure_window(77,include_system_audio=False,expected_pid=99)
    assert obj.audio_scope is None and obj.audio_format is None
    source.value.metadata['pid'] = 100
    with pytest.raises(RuntimeError,match='身份'):
        obj.grab(None)
    obj.close()


@pytest.mark.parametrize('change',['point-size','physical-size'])
def test_window_resize_cannot_silently_scale_into_the_old_recording_buffer(change):
    obj,source,_ = window_adapter()
    obj.configure_window(77)
    if change == 'point-size':
        source.value.metadata['window_size_points']['height'] = 5
    else:
        value = source.value
        source.value = capture.NativeFrame(value.rgb,value.width-2,value.height,value.timestamp,value.metadata)
    with pytest.raises(RuntimeError,match='尺寸已变化'):
        obj.grab(None)
    obj.close()


def test_disappeared_window_errors_instead_of_switching_to_a_display():
    obj,source,_ = window_adapter()
    obj.configure_window(77)
    source.latest = Mock(side_effect=RuntimeError('window disappeared'))
    with pytest.raises(RuntimeError,match='disappeared'):
        obj.grab(None)
    obj.close()


def test_window_permission_failure_never_launches_a_helper():
    obj,_,factory = window_adapter()
    obj.permission_check = lambda:{'granted':False,'reason':'authorize'}
    with pytest.raises(RuntimeError,match='authorize'):
        obj.configure_window(77)
    factory.assert_not_called()


def native_helper():
    if sys.platform != 'darwin':
        pytest.skip('Native Apple synthetic pipeline')
    try:
        return macos_display.helper_path()
    except RuntimeError:
        pytest.skip('Native helper has not been built')


@pytest.mark.parametrize('planar', [False,True])
def test_native_synthetic_pcm_conversion_uses_source_pts_and_never_microphone(planar):
    import numpy as np
    command = [native_helper(),'synthetic-audio'] + (['planar'] if planar else [])
    result = subprocess.run(command,capture_output=True,timeout=10)
    assert result.returncode == 0, result.stderr.decode('utf-8','replace')
    value = capture.read_packet(io.BytesIO(result.stdout))
    assert value.timestamp == 123 and value.sample_rate == 48000 and value.channels == 2
    samples = np.frombuffer(value.pcm,dtype='<i2').reshape(-1,2)
    assert samples.shape == (4800,2) and max(abs(samples[:,0])) == 8192
    assert np.max(abs(samples[:,0].astype(int)+samples[:,1])) == 0


@pytest.mark.parametrize('transfer',['bt709','srgb'])
def test_native_synthetic_hdr_pipeline_matches_display_referred_reference(transfer):
    import numpy as np
    result = subprocess.run([native_helper(),'synthetic',transfer],capture_output=True,timeout=20)
    if result.returncode and b'Core Image render unavailable' in result.stderr:
        pytest.skip('GPU/WindowServer unavailable; opaque-alpha check correctly refused false black pixels')
    assert result.returncode == 0,result.stderr.decode('utf-8','replace')
    value = capture.read_frame(io.BytesIO(result.stdout))
    colors = np.array([[0,0,0,1],[.18,.18,.18,1],[1,1,1,1],[2,2,2,1],[8,8,8,1],[4,2,1,1]],dtype=np.float16)
    expected = tone_map_edr(colors.tobytes(),6,1,transfer=transfer)
    assert list(value.rgb) == pytest.approx(list(expected),abs=1)
    assert value.metadata['synthetic'] and value.metadata['hdr_verified'] and value.metadata['tone_mapped']

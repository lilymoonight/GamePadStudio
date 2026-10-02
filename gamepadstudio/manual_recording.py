"""Explicit start/stop recording, independent of the rolling replay buffer."""
from datetime import datetime
import math
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid

from .replay_capture import create_replay_capture
from .replay_service import (get_ffmpeg_path, detect_hardware_encoder,
                             _encoder_runtime_options, _subprocess_hidden_flags)
from .replay_timing import TimestampedRGBWriter, TimestampedAVWriter
from .screenshot_service import get_target_monitor_bbox, smart_foreground_info, sanitize_filename


class ManualRecording:
    def __init__(self, save_dir, capture_mode='game', codec='hevc', fps=30,
                 bitrate_mbps=50, on_event=None, on_saved=None):
        self.save_dir = Path(save_dir)
        self.capture_mode = capture_mode or 'game'
        self.codec = codec if codec in ('hevc', 'h264', 'av1') else 'hevc'
        self.fps = max(15, min(60, int(fps)))
        self.bitrate_mbps = max(10, min(120, int(bitrate_mbps)))
        self.on_event, self.on_saved = on_event, on_saved
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._worker = self._source = self._proc = self._stopper = None
        self._phase = 'idle'
        self._path = self._error = self.capture_method = self.color_mode = self.encoder = ''
        self._frames = 0
        self._audio_captured = False
        self.audio_scope = ''

    def _log(self, text):
        if self.on_event:
            try:
                self.on_event(text)
            except Exception:
                pass

    def status(self):
        with self._lock:
            return dict(running=self._phase in ('starting', 'recording', 'stopping'),
                        phase=self._phase, path=self._path, last_error=self._error,
                        frames=self._frames, capture_method=self.capture_method,
                        color_mode=self.color_mode, encoder=self.encoder, audio=self._audio_captured,
                        audio_scope=self.audio_scope)

    def start(self):
        with self._lock:
            if self._worker and self._worker.is_alive():
                return self._phase in ('starting', 'recording')
            if sys.platform == 'darwin':
                from .macos_permissions import screen_capture_permission_status
                permission = screen_capture_permission_status()
                if not permission.get('granted'):
                    self._error = permission.get('reason') or '请先授权屏幕录制权限'
                    self._phase = 'failed'; self._log(self._error)
                    return False
            if not get_ffmpeg_path():
                self._error = '没有可用的 FFmpeg 编码器'
                self._phase = 'failed'; self._log(self._error)
                return False
            self._stop.clear()
            self._error = self._path = ''
            self._frames = 0
            self._audio_captured = False
            self.audio_scope = ''
            self._phase = 'starting'
            self._worker = threading.Thread(target=self._record, daemon=True, name='ManualRecording')
            self._worker.start()
        return True

    def request_stop(self):
        """A controller callback must not wait for MP4 finalization."""
        with self._lock:
            if self._phase not in ('starting', 'recording', 'stopping'):
                return False
            self._phase = 'stopping'
            self._stop.set()
            if not self._stopper or not self._stopper.is_alive():
                self._stopper = threading.Thread(target=self.stop, daemon=True, name='RecordingFinalize')
                self._stopper.start()
        return True

    def stop(self):
        with self._lock:
            self._stop.set()
            source, worker = self._source, self._worker
            if self._phase in ('starting', 'recording'):
                self._phase = 'stopping'
        cancel = getattr(source, 'cancel', None)
        if cancel:
            try:
                cancel()
            except Exception as exc:
                with self._lock:
                    self._error = '停止屏幕捕获失败：'+str(exc)
        if worker and worker is not threading.current_thread():
            worker.join(timeout=6)
            if worker.is_alive():
                with self._lock:
                    proc = self._proc
                    self._error = '录像结束超时，无法确认文件完整性'
                if proc and proc.poll() is None:
                    try:
                        proc.kill()
                    except OSError as exc:
                        self._error += '；停止编码器失败：'+str(exc)
                worker.join(timeout=2)
        return self._path or None

    def _command(self, source, part):
        fallback = ('scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format=yuv420p,'
                    'setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709')
        color = getattr(source, 'output_color_args', (
            '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709', '-color_range', 'tv'))
        audio = ['-map','0:v:0','-map','0:a:0','-c:a','aac','-b:a','192k'] if getattr(source,'audio_enabled',False) else ['-an']
        return [get_ffmpeg_path(), '-hide_banner', '-loglevel', 'error', '-y',
                '-f', 'matroska', '-i', 'pipe:0', *audio,
                '-vf', getattr(source, 'encoder_filter', fallback),
                '-c:v', self.encoder, *_encoder_runtime_options(self.encoder),
                '-b:v', f'{self.bitrate_mbps}M', '-pix_fmt', 'yuv420p', *color,
                '-fps_mode', 'passthrough', '-enc_time_base', '1:1000',
                '-movflags', '+faststart', '-f', 'mp4', str(part)]

    def _record(self):
        proc = source = errors_thread = part = final = None
        writer = first = None
        errors = bytearray()
        failure = ''
        try:
            self.encoder = detect_hardware_encoder(self.codec)
            with create_replay_capture() as source:
                with self._lock:
                    if self._stop.is_set():
                        return
                    self._source = source
                explicit = re.fullmatch(r'monitor_(\d+)', self.capture_mode)
                if explicit:
                    index = int(explicit.group(1))
                    if not 1 <= index < len(source.monitors):
                        raise RuntimeError('所选显示器已断开，请重新选择')
                    bbox = dict(source.monitors[index])
                else:
                    bbox = dict(get_target_monitor_bbox(source, self.capture_mode))
                window_backend = target = None
                if self.capture_mode in ('game','window','smart','monitor') and hasattr(source,'configure_window'):
                    from .screenshot_service import get_mac_window_backend
                    window_backend = get_mac_window_backend()
                    target = window_backend.foreground_window() if self.capture_mode == 'window' else window_backend.smart_window()
                    if not target or not window_backend.verify(target):
                        raise RuntimeError('没有可录制的活动应用窗口')
                    source.configure_window(target.window_id,self.fps,include_system_audio=True,expected_pid=target.pid)
                    if not window_backend.verify(target):
                        raise RuntimeError('录制窗口在启动期间发生变化')
                elif hasattr(source, 'configure'):
                    if hasattr(source,'read_audio'):
                        source.configure(bbox,self.fps,include_system_audio=True)
                    else:
                        source.configure(bbox, self.fps)
                width, height = getattr(source, 'frame_size', None) or (int(bbox['width']) & ~1, int(bbox['height']) & ~1)
                if width < 2 or height < 2:
                    raise RuntimeError('录制区域尺寸无效')
                if not getattr(source, 'frame_size', None):
                    bbox.update(width=width, height=height)
                self.capture_method = str(getattr(source, 'capture_method', 'MSS'))
                self.color_mode = str(getattr(source, 'color_mode', 'SDR'))
                self.audio_scope = str(getattr(source,'audio_scope',''))
                self.save_dir.mkdir(parents=True, exist_ok=True)
                title = target.title if target else smart_foreground_info()[1]
                name = f'{sanitize_filename(title or "Screen")}_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}'
                final = self.save_dir / f'{name}.mp4'
                part = self.save_dir / f'.{name}.partial.mp4'
                if self._stop.is_set():
                    return
                proc = subprocess.Popen(self._command(source, part), stdin=subprocess.PIPE,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                        **_subprocess_hidden_flags())
                with self._lock:
                    self._proc = proc

                def read_errors():
                    for block in iter(lambda: proc.stderr.read(1024), b''):
                        errors.extend(block)
                        if len(errors) > 8192:
                            del errors[:-8192]
                errors_thread = threading.Thread(target=read_errors, daemon=True)
                errors_thread.start()
                if getattr(source,'audio_enabled',False):
                    rate, channels = source.audio_format
                    writer = TimestampedAVWriter(proc.stdin,width,height,self.fps,sample_rate=rate,channels=channels)
                else:
                    writer = TimestampedRGBWriter(proc.stdin, width, height, self.fps)
                first = previous = None
                last_identity_check = time.monotonic()
                while not self._stop.is_set():
                    begin = time.monotonic()
                    if target and begin-last_identity_check >= .5:
                        if not window_backend.verify(target):
                            raise RuntimeError('录制窗口已退出或进程身份变化')
                        last_identity_check = begin
                    shot = source.grab(bbox)
                    if self._stop.is_set():
                        break
                    stamp = getattr(shot, 'timestamp', begin)
                    if not isinstance(stamp, (int, float)) or not math.isfinite(stamp) or (previous is not None and stamp < previous):
                        raise RuntimeError('屏幕采集时间无效，请重新录制')
                    if proc.poll() is not None:
                        raise RuntimeError('视频编码器已退出')
                    if previous != stamp:
                        if first is None:
                            first = stamp
                        writer.write_frame(shot.rgb, stamp-first)
                        self._frames += 1
                        previous = stamp
                        with self._lock:
                            if self._phase == 'starting':
                                self._phase = 'recording'; self._log('录像已开始；再次按键停止并保存')
                    if getattr(source,'audio_enabled',False):
                        while (audio_frame := source.read_audio()) is not None:
                            writer.write_audio(audio_frame.pcm,audio_frame.timestamp-first,
                                               sample_rate=audio_frame.sample_rate,channels=audio_frame.channels)
                            self._audio_captured = True
                    self._stop.wait(max(0, 1/self.fps - (time.monotonic()-begin)))
        except Exception as exc:
            if not self._stop.is_set():
                failure = str(exc)
        finally:
            cancel = getattr(source,'cancel',None)
            if cancel:
                try:
                    cancel()
                except Exception as exc:
                    failure = failure or '停止屏幕捕获失败：'+str(exc)
            if proc and writer and first is not None and getattr(source,'audio_enabled',False):
                try:
                    # The source's context has stopped native capture, but its
                    # final PCM queue may still contain the release-time tail.
                    while (audio_frame := source.read_audio()) is not None:
                        writer.write_audio(audio_frame.pcm,audio_frame.timestamp-first,
                                           sample_rate=audio_frame.sample_rate,channels=audio_frame.channels)
                        self._audio_captured = True
                except Exception as exc:
                    failure = failure or '结束音轨失败：'+str(exc)
            if proc:
                try:
                    proc.stdin.close()
                    proc.wait(timeout=5)
                except Exception as exc:
                    failure = failure or str(exc)
                    if proc.poll() is None:
                        try:
                            proc.kill(); proc.wait(timeout=2)
                        except Exception as cleanup_error:
                            failure += '；清理编码器失败：'+str(cleanup_error)
                if errors_thread:
                    errors_thread.join(timeout=1)
                try:
                    proc.stderr.close()
                except Exception as exc:
                    failure = failure or str(exc)
                if proc.returncode and (self._frames or not self._stop.is_set()):
                    failure = failure or errors.decode('utf-8', 'replace').strip() or '视频编码失败'
            with self._lock:
                failure = failure or self._error
                try:
                    if not failure and self._frames and part and part.is_file() and part.stat().st_size:
                        part.replace(final)
                        self._path = str(final)
                    elif self._frames and not failure:
                        failure = '编码器未生成有效的视频文件'
                except OSError as exc:
                    failure = str(exc)
                self._error = failure
                self._phase = 'failed' if failure else 'idle'
                self._source = self._proc = None
            try:
                if part:
                    part.unlink(missing_ok=True)
            except OSError as exc:
                failure = failure or '清理未完成文件失败：'+str(exc)
                self._error = failure; self._phase = 'failed'
            try:
                if failure:
                    self._log('录像失败：'+failure)
                elif self._path:
                    self._log('录像已保存：'+Path(self._path).name)
            finally:
                if self.on_saved:
                    self.on_saved(self._path, failure)

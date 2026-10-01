"""Application feedback patterns using SDL motor amplitudes and timed pulses.

Device applies each channel's response curve. Independent trigger motors are
optional hardware capabilities; these pulses do not control adaptive resistance.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import math
from pathlib import Path
import sys
import threading
import time
from typing import Optional, Callable
import winsound


def ensure_shutter_sound_file(target_path: Path):
    """如果资产目录下不存在快门音效，则程序化合成高保真机械快门 WAV 音效"""
    if target_path.is_file() and target_path.stat().st_size > 500:
        return
    try:
        import wave
        import numpy as np

        sr = 44100
        dur = 0.085  # 85ms
        t = np.linspace(0, dur, int(sr * dur), endpoint=False)

        # 1. 前帘释放咔哒高频冲击 (t=0..0.015)
        c1_env = np.exp(-t / 0.003)
        click1 = np.sin(2 * np.pi * 3400 * t) * c1_env * 0.75 + np.sin(2 * np.pi * 1800 * t) * c1_env * 0.35

        # 2. 机械滑轨摩擦杂音 (t=0.008..0.035)
        noise = np.random.uniform(-1, 1, len(t))
        rasp_env = np.exp(-((t - 0.018) / 0.008)**2) * 0.28
        rasp = noise * rasp_env

        # 3. 后帘锁止撞击与机身阻尼回弹 (t=0.030..0.080)
        t2 = np.maximum(0, t - 0.030)
        c2_env = np.exp(-t2 / 0.006) * (t >= 0.030)
        click2 = np.sin(2 * np.pi * 2200 * t2) * c2_env * 0.95 + np.sin(2 * np.pi * 850 * t2) * c2_env * 0.4
        thud = np.sin(2 * np.pi * 320 * t2) * np.exp(-t2 / 0.016) * (t >= 0.030) * 0.55

        audio = click1 + rasp + click2 + thud
        audio = audio / np.max(np.abs(audio)) * 0.92
        int_audio = (audio * 32767).astype(np.int16)

        target_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(target_path), 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(int_audio.tobytes())
    except Exception:
        pass


class HapticEngine:
    """
    触觉拟真与反馈核心引擎
    管理手柄触觉马达脉冲合成与音效播放
    """
    def __init__(self, device_provider, on_notice: Optional[Callable[[str], None]] = None):
        self.device = device_provider
        self.on_notice = on_notice
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="HapticWorker")
        self._state_lock = threading.RLock()
        self._pattern_cancel = threading.Event()
        self._closed = False

        self.sound_enabled = True
        self.haptics_enabled = True
        self.intensity = 1.0  # 0.0 ~ 1.0
        self.profile = "crisp"  # crisp (清脆点触), deep (深沉阻尼), dynamic (动效模拟)
        self.trigger_rumble_enabled = False

        self.assets_dir = Path(__file__).resolve().parent / "assets"
        self.shutter_wav = self.assets_dir / "shutter.wav"
        ensure_shutter_sound_file(self.shutter_wav)

    def set_config(self, sound_enabled: bool, haptics_enabled: bool, intensity: float = 1.0,
                   profile: str = "crisp", trigger_rumble_enabled: bool = False):
        with self._state_lock:
            # A queued or two-part pattern belongs to its original device and
            # settings. Reconfiguration invalidates its remaining pulses.
            self._pattern_cancel.set()
            self._pattern_cancel = threading.Event()
            self.sound_enabled = bool(sound_enabled)
            self.haptics_enabled = bool(haptics_enabled)
            self.intensity = max(0.0, min(1.0, float(intensity)))
            if profile in ("crisp", "deep", "dynamic"):
                self.profile = profile
            self.trigger_rumble_enabled = bool(trigger_rumble_enabled)

    def _device_identity(self):
        metadata = getattr(self.device, 'metadata', None)
        if not isinstance(metadata, dict):
            metadata = getattr(self.device, 'state', None)
        metadata = metadata if isinstance(metadata, dict) else {}
        return (id(self.device), metadata.get('device_key') or metadata.get('profile_key'),
                getattr(self.device, 'instance_id', metadata.get('instance_id')),
                getattr(self.device, 'handle', None))

    def play_shutter_sound(self):
        """异步播放高保真机械快门音效（0 延迟、非阻塞）"""
        if not self.sound_enabled or self._closed:
            return
        def _worker():
            try:
                if self.shutter_wav.is_file():
                    winsound.PlaySound(str(self.shutter_wav), winsound.SND_FILENAME | winsound.SND_ASYNC)
                else:
                    # 系统提示音回退
                    winsound.MessageBeep(winsound.MB_OK)
            except Exception:
                pass
        self.executor.submit(_worker)

    def trigger_feedback(self, event_type: str = "capture"):
        """
        触发截图、回放和预览的声音与震动反馈。
        """
        if event_type == "capture":
            self.play_shutter_sound()
            self.play_pattern("shutter")
        elif event_type == "replay_saved":
            self.play_shutter_sound()
            self.play_pattern("replay_saved")
        elif event_type in ("tick", "impact", "shutter", "heartbeat", "trigger_test"):
            self.play_pattern(event_type)

    def play_pattern(self, pattern_name: str):
        """Play bounded amplitude pulses asynchronously on the current device."""
        with self._state_lock:
            if self._closed or not self.haptics_enabled or self.intensity <= 0.01:
                return
            cancellation = self._pattern_cancel
            identity = self._device_identity()
            scale = self.intensity
            profile = self.profile
            metadata = getattr(self.device, 'metadata', {})
            metadata = metadata if isinstance(metadata, dict) else {}
            trigger_enabled = (self.trigger_rumble_enabled and metadata.get('trigger_rumble') is True
                               and hasattr(self.device, 'rumble_triggers'))
            if pattern_name == 'trigger_test' and not trigger_enabled:
                return
        # Each tuple contains motor amplitudes, duration and the gap before
        # the next pulse. No delayed pulse may follow a device/config change.
        patterns = {
            "shutter": ((0.0, .42, 22, .025), (.28, .78, 48, 0)),
            "tick": ((0.0, .35, 18, 0),),
            "impact": ((.85, .55, 85, 0),),
            "heartbeat": ((.35, .15, 45, .075), (.65, .30, 70, 0)),
            "replay_saved": ((.15, .45, 35, .055), (.40, .88, 90, 0)),
            "trigger_test": ((.55, .55, 180, 0),),
        }
        pulses = patterns.get(pattern_name, ((.35, .35, 50, 0),))

        def _worker():
            try:
                for low, high, duration, gap in pulses:
                    with self._state_lock, getattr(self.device, '_io_lock', None) or nullcontext():
                        # Identity also detects Device.select changing the
                        # handle before the next poll updates settings.
                        if cancellation.is_set() or self._closed or identity != self._device_identity():
                            return
                        if profile == 'deep':
                            low, high = min(1., low * 1.2 + high * .20), high * .55
                        elif profile == 'dynamic':
                            low, high = min(1., low * .85 + high * .15), min(1., high * .85 + low * .15)
                        if pattern_name != 'trigger_test':
                            self._rumble(low * scale, high * scale, duration)
                        if trigger_enabled:
                            self.device.rumble_triggers(low * scale, high * scale, duration)
                    if gap and cancellation.wait(gap):
                        return
            except Exception:
                pass

        return self.executor.submit(_worker)

    def _rumble(self, low: float, high: float, duration_ms: int):
        """底层驱动马达调用"""
        if not self.device:
            return
        if hasattr(self.device, "rumble_ext"):
            self.device.rumble_ext(low, high, duration_ms)
        elif hasattr(self.device, "rumble"):
            self.device.rumble(max(low, high))

    def close(self):
        with self._state_lock:
            self._closed = True
            self._pattern_cancel.set()
        try:
            self.executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass

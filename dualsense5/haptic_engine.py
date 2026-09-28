"""
GamePad Studio · 触觉拟真引擎 (Haptic Simulation Engine)
1. 机械快门、棘轮刻度、打击阻尼、心跳多重高精度物理触觉波形合成
2. 独立低延迟触觉发生工作线程 (异步非阻塞，不占用手柄 300Hz 轮询循环)
3. 机械快门声音异步联动 (支持相机快门音效与静音快门)
4. 双音圈线性马达分频控制 (Low-freq 重马达 / High-freq 细腻音圈 / 独立扳机马达)
"""

from concurrent.futures import ThreadPoolExecutor
import math
from pathlib import Path
import sys
import threading
import time
from typing import Optional, Callable
import winsound


def ensure_shutter_sound_file(target_path: Path):
    """如果资产目录下不存在快门音效，则程序化合成高保真机械快门 WAV 音效（纯标准库实现，零第三方依赖）"""
    if target_path.is_file() and target_path.stat().st_size > 500:
        return
    try:
        import wave
        import struct
        import math
        import random

        sr = 44100
        dur = 0.085  # 85ms
        total_samples = int(sr * dur)
        frames = bytearray()

        for i in range(total_samples):
            t = i / sr
            # 1. 前帘释放咔哒高频冲击 (t=0..0.015)
            c1_env = math.exp(-t / 0.003)
            click1 = (math.sin(2 * math.pi * 3400 * t) * 0.75 + math.sin(2 * math.pi * 1800 * t) * 0.35) * c1_env

            # 2. 机械滑轨摩擦杂音 (t=0.008..0.035)
            rasp_env = math.exp(-((t - 0.018) / 0.008) ** 2) * 0.28
            rasp = (random.random() * 2 - 1) * rasp_env

            # 3. 后帘锁止撞击与机身阻尼回弹 (t=0.030..0.080)
            t2 = max(0.0, t - 0.030)
            if t >= 0.030:
                c2_env = math.exp(-t2 / 0.006)
                click2 = (math.sin(2 * math.pi * 2200 * t2) * 0.95 + math.sin(2 * math.pi * 850 * t2) * 0.4) * c2_env
                thud = math.sin(2 * math.pi * 320 * t2) * math.exp(-t2 / 0.016) * 0.55
            else:
                click2 = 0.0
                thud = 0.0

            val = (click1 + rasp + click2 + thud) * 0.45
            val = max(-1.0, min(1.0, val))
            sample = int(val * 32767)
            frames.extend(struct.pack('<h', sample))

        target_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(target_path), 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(frames)
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

        self.sound_enabled = True
        self.haptics_enabled = True
        self.intensity = 1.0  # 0.0 ~ 1.0
        self.profile = "crisp"  # crisp (清脆点触), deep (深沉阻尼), dynamic (动效模拟)

        self.assets_dir = Path(__file__).resolve().parent / "assets"
        self.shutter_wav = self.assets_dir / "shutter.wav"
        ensure_shutter_sound_file(self.shutter_wav)

    def set_config(self, sound_enabled: bool, haptics_enabled: bool, intensity: float = 1.0, profile: str = "crisp"):
        self.sound_enabled = bool(sound_enabled)
        self.haptics_enabled = bool(haptics_enabled)
        self.intensity = max(0.0, min(1.0, float(intensity)))
        if profile in ("crisp", "deep", "dynamic"):
            self.profile = profile

    def play_shutter_sound(self):
        """异步播放高保真机械快门音效（0 延迟、非阻塞）"""
        if not self.sound_enabled:
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
        触发综合反馈组合拳 (快门声音 + 双音圈物理脉冲)
        """
        if event_type == "capture":
            self.play_shutter_sound()
            self.play_pattern("shutter")
        elif event_type == "replay_saved":
            self.play_pattern("replay_saved")
        elif event_type == "tick":
            self.play_pattern("tick")
        elif event_type == "impact":
            self.play_pattern("impact")

    def play_pattern(self, pattern_name: str):
        """异步在手柄音圈马达上合成特定物理质感的触觉脉冲"""
        if not self.haptics_enabled or self.intensity <= 0.01:
            return

        def _worker():
            try:
                scale = self.intensity
                if pattern_name == "shutter":
                    # 机械快门两段式微脉冲：前帘微动(20ms) -> 间隙(15ms) -> 后帘闭合扎实反馈(45ms)
                    self._rumble(0.0, 0.42 * scale, 22)
                    time.sleep(0.025)
                    self._rumble(0.28 * scale, 0.78 * scale, 48)
                elif pattern_name == "tick":
                    # 机械旋钮/棘轮轻微刻度感
                    self._rumble(0.0, 0.35 * scale, 18)
                elif pattern_name == "impact":
                    # 强劲撞击阻尼反馈
                    self._rumble(0.85 * scale, 0.55 * scale, 85)
                elif pattern_name == "heartbeat":
                    # 仿真双心跳律动
                    self._rumble(0.35 * scale, 0.15 * scale, 45)
                    time.sleep(0.075)
                    self._rumble(0.65 * scale, 0.30 * scale, 70)
                elif pattern_name == "replay_saved":
                    # 回放录像保存成功双升调波纹
                    self._rumble(0.15 * scale, 0.45 * scale, 35)
                    time.sleep(0.055)
                    self._rumble(0.40 * scale, 0.88 * scale, 90)
                else:
                    self._rumble(0.35 * scale, 0.35 * scale, 50)
            except Exception:
                pass

        self.executor.submit(_worker)

    def _rumble(self, low: float, high: float, duration_ms: int):
        """底层驱动马达调用"""
        if not self.device:
            return
        if hasattr(self.device, "rumble_ext"):
            self.device.rumble_ext(low, high, duration_ms)
        elif hasattr(self.device, "rumble"):
            self.device.rumble(max(low, high))

    def close(self):
        try:
            self.executor.shutdown(wait=False)
        except Exception:
            pass

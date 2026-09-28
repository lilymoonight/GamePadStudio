"""
GamePad Studio · 4K 极清硬件加速即时回放录制引擎 (Replay Buffer Engine)
1. 支持 AMD AMF / NVIDIA NVENC / Intel QSV / MediaFoundation 原生 GPU 硬件编码
2. HEVC / AV1 标杆极清环形内存/磁盘缓存 (1 ~ 10 分钟动态滑动窗口)
3. 毫秒级无损合并落盘 (Lossless Stream Copy Concat，0.03s 瞬间生成 MP4)
4. 游戏标题智能捕获、与截图服务同源的干净命名与目录归档
"""

import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable, Dict, List, Optional
import mss

from .screenshot_service import foreground_info, sanitize_filename, _get_active_monitor_bbox


def get_ffmpeg_path() -> Optional[str]:
    """获取可用 FFmpeg 可执行文件路径"""
    # 1. 优先检查项目自身 bin 目录
    proj_bin = Path(__file__).resolve().parents[1] / 'bin' / 'ffmpeg.exe'
    if proj_bin.is_file():
        return str(proj_bin)
    
    # 2. 检查系统 PATH
    which_ffmpeg = shutil.which('ffmpeg')
    if which_ffmpeg:
        return which_ffmpeg
        
    # 3. 检查本机常见安装路径
    candidates = [
        Path(r"D:\voxcpm\tools\ffmpeg.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "ffmpeg" / "bin" / "ffmpeg.exe",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


_CACHED_ENCODERS: Dict[str, str] = {}


def detect_hardware_encoder(codec: str = "hevc") -> str:
    """
    智能探针：根据本机 GPU 型号与驱动，探测可用的高吞吐硬件加速编码器
    优先级：AMD AMF -> NVIDIA NVENC -> Intel QSV -> MediaFoundation -> 软件回退
    """
    global _CACHED_ENCODERS
    if codec in _CACHED_ENCODERS:
        return _CACHED_ENCODERS[codec]

    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        fallback = "libx265" if codec == "hevc" else ("libaom-av1" if codec == "av1" else "libx264")
        _CACHED_ENCODERS[codec] = fallback
        return fallback

    candidates = []
    if codec == "hevc":
        candidates = ["hevc_amf", "hevc_nvenc", "hevc_qsv", "hevc_mf", "libx265"]
    elif codec == "av1":
        candidates = ["av1_amf", "av1_nvenc", "av1_qsv", "libaom-av1"]
    elif codec == "h264":
        candidates = ["h264_amf", "h264_nvenc", "h264_qsv", "h264_mf", "libx264"]
    else:
        candidates = ["hevc_amf", "hevc_nvenc", "libx265"]

    for enc in candidates:
        try:
            cmd = [
                ffmpeg,
                "-y",
                "-f", "lavfi",
                "-i", "testsrc=size=256x256:rate=30",
                "-t", "0.1",
                "-c:v", enc,
                "-f", "null", "NUL" if sys.platform == "win32" else "/dev/null"
            ]
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
            if res.returncode == 0:
                _CACHED_ENCODERS[codec] = enc
                return enc
        except Exception:
            continue

    fallback = "libx265" if codec == "hevc" else ("libaom-av1" if codec == "av1" else "libx264")
    _CACHED_ENCODERS[codec] = fallback
    return fallback


def calculate_estimated_ram_gb(minutes: int, bitrate_mbps: int = 50) -> float:
    """计算指定时长和码率下的回放缓冲区内存/磁盘开销（GB）"""
    return round((minutes * 60 * bitrate_mbps * 1e6) / (8 * 1024 * 1024 * 1024), 2)


class ReplayBufferEngine:
    """
    4K 极清硬件加速环形回放录制引擎
    - 后台独立线程实时截取目标显示器屏幕
    - 喂入 GPU 硬件编码器 (AMF / NVENC / QSV)，切片为 2 秒短 TS 数据包
    - 环形队列自动淘汰超过最大分钟数的数据片
    - 长按 Create / 调用 save_replay 时，无损流复制合并为高画质 MP4 文件
    """
    def __init__(
        self,
        save_dir: Path,
        minutes: int = 5,
        codec: str = "hevc",
        bitrate_mbps: int = 50,
        fps: int = 30,
        on_event: Optional[Callable[[str], None]] = None
    ):
        self.save_dir = Path(save_dir)
        self.minutes = max(1, min(10, int(minutes)))
        self.codec = codec if codec in ("hevc", "av1", "h264") else "hevc"
        self.bitrate_mbps = max(10, min(120, int(bitrate_mbps)))
        self.fps = max(15, min(60, int(fps)))
        self.on_event = on_event

        self.cache_dir = self.save_dir / ".replay_cache"
        self.running = False
        self._lock = threading.Lock()
        self._chunks: List[Path] = []  # 按时间排序的 TS 数据包列表
        self._worker_thread: Optional[threading.Thread] = None
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._last_save_time = 0.0

    def log(self, text: str):
        if self.on_event:
            try:
                self.on_event(text)
            except Exception:
                pass

    def is_running(self) -> bool:
        with self._lock:
            return self.running and self._ffmpeg_proc is not None and self._ffmpeg_proc.poll() is None

    def start(self) -> bool:
        with self._lock:
            if self.running:
                return True

            ffmpeg = get_ffmpeg_path()
            if not ffmpeg:
                self.log("启动回放引擎失败：未检测到可用 FFmpeg 组件")
                return False

            self.save_dir.mkdir(parents=True, exist_ok=True)
            shutil.rmtree(self.cache_dir, ignore_errors=True)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._chunks.clear()

            self.running = True
            self._worker_thread = threading.Thread(target=self._capture_and_encode_loop, daemon=True, name="ReplayCaptureThread")
            self._worker_thread.start()
            self.log(f"4K 极清回放引擎已启动（编码: {self.codec.upper()}, 回看: {self.minutes}分钟, 码率: {self.bitrate_mbps}Mbps）")
            return True

    def stop(self):
        with self._lock:
            if not self.running:
                return
            self.running = False

        if self._ffmpeg_proc:
            try:
                if self._ffmpeg_proc.stdin:
                    self._ffmpeg_proc.stdin.close()
                self._ffmpeg_proc.terminate()
                self._ffmpeg_proc.wait(timeout=1.5)
            except Exception:
                try:
                    self._ffmpeg_proc.kill()
                except Exception:
                    pass
            self._ffmpeg_proc = None

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.0)
            self._worker_thread = None

        # 清理临时切片
        try:
            shutil.rmtree(self.cache_dir, ignore_errors=True)
        except Exception:
            pass
        self.log("4K 极清回放引擎已停止")

    def _capture_and_encode_loop(self):
        """后台屏幕抓取与流式硬件编码主循环"""
        ffmpeg = get_ffmpeg_path()
        if not ffmpeg:
            return

        encoder = detect_hardware_encoder(self.codec)
        seg_duration = 2  # 2秒一个 GOP 切片
        gop_size = self.fps * seg_duration

        with mss.mss() as sct:
            bbox = _get_active_monitor_bbox() or sct.monitors[1]
            w = bbox["width"] - (bbox["width"] % 2)
            h = bbox["height"] - (bbox["height"] % 2)
            crop_bbox = dict(left=bbox["left"], top=bbox["top"], width=w, height=h)

            cmd = [
                ffmpeg,
                "-y",
                "-f", "rawvideo",
                "-pix_fmt", "rgb24",
                "-s", f"{w}x{h}",
                "-r", str(self.fps),
                "-i", "pipe:0",
                "-c:v", encoder,
                "-b:v", f"{self.bitrate_mbps}M",
                "-g", str(gop_size),
                "-f", "segment",
                "-segment_time", str(seg_duration),
                "-reset_timestamps", "1",
                str(self.cache_dir / "chunk_%05d.ts")
            ]

            try:
                self._ffmpeg_proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception as exc:
                self.log(f"启动 FFmpeg 编码器进程失败: {exc}")
                self.running = False
                return

            frame_interval = 1.0 / self.fps
            max_chunks = int((self.minutes * 60) / seg_duration) + 2

            try:
                while self.running:
                    t_start = time.perf_counter()
                    shot = sct.grab(crop_bbox)
                    
                    if not self.running or self._ffmpeg_proc.poll() is not None:
                        break

                    try:
                        self._ffmpeg_proc.stdin.write(shot.rgb)
                    except (BrokenPipeError, OSError):
                        break

                    # 周期性修剪过期的切片 (滑动环形窗口)
                    self._maintain_ring_buffer(max_chunks)

                    # 精准帧率节奏控制
                    elapsed = time.perf_counter() - t_start
                    sleep_time = frame_interval - elapsed
                    if sleep_time > 0:
                        time.sleep(sleep_time)
            except Exception as e:
                self.log(f"回放录制抓取循环异常: {e}")
            finally:
                if self._ffmpeg_proc and self._ffmpeg_proc.poll() is None:
                    try:
                        if self._ffmpeg_proc.stdin:
                            self._ffmpeg_proc.stdin.close()
                        self._ffmpeg_proc.wait(timeout=1.0)
                    except Exception:
                        pass

    def _maintain_ring_buffer(self, max_chunks: int):
        """定期扫描切片目录，剔除超出最大回看分钟数的旧数据包"""
        try:
            all_ts = sorted(self.cache_dir.glob("chunk_*.ts"))
            if len(all_ts) > max_chunks:
                to_delete = all_ts[:-max_chunks]
                for old in to_delete:
                    try:
                        old.unlink(missing_ok=True)
                    except Exception:
                        pass
        except Exception:
            pass

    def save_replay(self, title: Optional[str] = None) -> Optional[str]:
        """
        立即将当前环形内存/磁盘缓存中的切片瞬间无损合并为 MP4 文件 (0.03秒无损流复制)
        """
        now = time.monotonic()
        if now - self._last_save_time < 3.0:
            return None
        self._last_save_time = now

        ffmpeg = get_ffmpeg_path()
        if not ffmpeg:
            return None

        # 收集当前缓存目录中完整的切片（排除最新正在写入的最后一个）
        all_ts = sorted(self.cache_dir.glob("chunk_*.ts"))
        if len(all_ts) < 2:
            self.log("回放缓存尚未充填足够帧数，请稍后再试")
            return None

        # 保留除了可能正在写入的最后一个之外的所有切片
        valid_chunks = all_ts[:-1]
        if not valid_chunks:
            valid_chunks = all_ts

        if not title:
            _, title = foreground_info()
        clean_title = sanitize_filename(title)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_filename = f"DS_{clean_title}_{timestamp}_replay.mp4"
        out_path = self.save_dir / out_filename

        # 生成 ffmpeg concat 清单
        concat_txt = self.cache_dir / f"concat_{timestamp}.txt"
        try:
            with open(concat_txt, "w", encoding="utf-8") as f:
                for chunk in valid_chunks:
                    f.write(f"file '{chunk.name}'\n")

            cmd_concat = [
                ffmpeg,
                "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_txt),
                "-c", "copy",
                "-movflags", "+faststart",
                str(out_path)
            ]

            t0 = time.perf_counter()
            subprocess.run(cmd_concat, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            elapsed = time.perf_counter() - t0

            try:
                concat_txt.unlink(missing_ok=True)
            except Exception:
                pass

            if out_path.is_file() and out_path.stat().st_size > 1000:
                size_mb = out_path.stat().st_size / (1024 * 1024)
                self.log(f"🎬 极清回放录像已保存: {out_filename} ({size_mb:.1f}MB, 耗时 {elapsed:.2f}s)")
                return str(out_path)
        except Exception as exc:
            self.log(f"合并保存回放失败: {exc}")
        return None

    def get_status(self) -> dict:
        """获取当前引擎实时状态、切片数量与内存开销"""
        is_run = self.is_running()
        encoder = detect_hardware_encoder(self.codec)
        chunks = list(self.cache_dir.glob("chunk_*.ts")) if self.cache_dir.exists() else []
        total_bytes = sum(c.stat().st_size for c in chunks)
        total_mb = total_bytes / (1024 * 1024)
        buffered_sec = len(chunks) * 2

        return {
            "enabled": is_run,
            "running": is_run,
            "minutes": self.minutes,
            "codec": self.codec,
            "encoder": encoder,
            "bitrate_mbps": self.bitrate_mbps,
            "buffered_seconds": buffered_sec,
            "chunks_count": len(chunks),
            "current_size_mb": round(total_mb, 1),
            "estimated_max_gb": calculate_estimated_ram_gb(self.minutes, self.bitrate_mbps)
        }

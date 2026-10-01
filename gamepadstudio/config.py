import os
from dataclasses import dataclass


def _get_pictures_dir() -> str:
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, "Pictures"),
        os.path.join(home, "图片"),
        os.path.join(home, "My Pictures"),
    ]
    for path in candidates:
        if os.path.isdir(path):
            return path
    # 兜底使用当前工作目录下的 screenshots
    return os.path.join(os.getcwd(), "screenshots")


def default_save_dir() -> str:
    return os.path.join(_get_pictures_dir(), "DualSense5")


@dataclass
class Settings:
    screenshot_button: int = int(os.environ.get("DS5_SCREENSHOT_BUTTON", 4))
    cooldown_seconds: float = float(os.environ.get("DS5_COOLDOWN_SECONDS", 0.1))
    save_dir: str = os.environ.get("DS5_SAVE_DIR") or default_save_dir()
    toast_duration_ms: int = int(os.environ.get("DS5_TOAST_MS", 300))


def ensure_directories(settings: Settings) -> None:
    os.makedirs(settings.save_dir, exist_ok=True)



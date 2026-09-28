import argparse
import configparser
import os
import sys
from pathlib import Path

from .config import Settings, ensure_directories, default_save_dir
from .controller_service import DualSenseController
from .screenshot_service import take_screenshot


def run() -> None:
    parser = argparse.ArgumentParser(description="GamePad Studio 手柄工作台")
    parser.add_argument("--config", type=str, default=None, help="配置文件路径(.ini)")
    # 将 CLI 默认设为 None，以便与配置文件/环境变量合并
    parser.add_argument("--button", type=int, default=None, help="截图按钮编号")
    parser.add_argument("--cooldown", type=float, default=None, help="截图冷却(秒)")
    parser.add_argument("--save-dir", type=str, default=None, help="截图保存目录")
    parser.add_argument("--toast-ms", type=int, default=None, help="右上角提示显示时长(毫秒)")
    parser.add_argument("--test-buttons", action="store_true", help="进入按钮测试模式")
    args = parser.parse_args()

    # 0) 若未指定配置文件，则在可执行文件/当前目录生成默认配置（不存在时）
    exe_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()

    def _write_default_ini(path: Path) -> None:
        cfg = configparser.ConfigParser()
        cfg["gamepadstudio"] = {
            "button": "4",
            "cooldown": "0.1",
            "save_dir": "",
            "toast_ms": "300",
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            cfg.write(f)

    if args.config is None:
        default_cfg = exe_dir / "GamePadStudio.ini"
        if not default_cfg.exists():
            try:
                _write_default_ini(default_cfg)
            except Exception:
                pass

    # 1) 加载配置文件
    def _candidate_config_paths():
        if args.config:
            yield Path(args.config)
        # 可执行文件所在目录（打包后）
        for name in ("GamePadStudio.ini", "gamepadstudio.ini", "config.ini"):
            yield exe_dir / name
        # 用户配置目录（Windows 优先 APPDATA）
        if os.name == "nt":
            appdata = os.environ.get("APPDATA")
            if appdata:
                yield Path(appdata) / "GamePadStudio" / "config.ini"
        # 跨平台常见位置
        yield Path.home() / ".config" / "gamepadstudio" / "config.ini"

    cfg = configparser.ConfigParser()
    cfg_path = None
    for p in _candidate_config_paths():
        if p and p.is_file():
            cfg.read(p, encoding="utf-8")
            cfg_path = p
            break

    section = "dualsense5"
    cfg_get = lambda key: (cfg.get(section, key, fallback=None) if cfg_path and cfg.has_section(section) else cfg.get("DEFAULT", key, fallback=None)) if cfg_path else None

    # 2) 环境变量
    env_button = os.environ.get("DS5_SCREENSHOT_BUTTON")
    env_cooldown = os.environ.get("DS5_COOLDOWN_SECONDS")
    env_save_dir = os.environ.get("DS5_SAVE_DIR")
    env_toast_ms = os.environ.get("DS5_TOAST_MS")

    # 3) 合并优先级：CLI > 配置文件 > 环境变量 > 内置默认
    def _int_or(value, default):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _float_or(value, default):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    final_button = args.button if args.button is not None else _int_or(cfg_get("button"), _int_or(env_button, 4))
    final_cooldown = args.cooldown if args.cooldown is not None else _float_or(cfg_get("cooldown"), _float_or(env_cooldown, 0.1))
    final_save_dir = args.save_dir if args.save_dir is not None else (cfg_get("save_dir") or env_save_dir or default_save_dir())
    final_toast_ms = args.toast_ms if args.toast_ms is not None else _int_or(cfg_get("toast_ms"), _int_or(env_toast_ms, 300))

    settings = Settings(
        screenshot_button=final_button,
        cooldown_seconds=final_cooldown,
        save_dir=final_save_dir,
        toast_duration_ms=final_toast_ms,
    )
    ensure_directories(settings)

    controller = DualSenseController(settings.screenshot_button)
    if args.test_buttons:
        controller.buttons_test()
        return

    def on_shot() -> None:
        path = take_screenshot(settings.save_dir, settings.toast_duration_ms)
        print(f"截图保存: {path}")

    controller.loop(on_shot, cooldown_seconds=settings.cooldown_seconds)



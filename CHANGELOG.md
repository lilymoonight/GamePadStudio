# Changelog

All notable changes to **GamePad Studio** are documented in this file.

## [2.2.0] - 2026-10-27

### ✨ Design Aesthetics & UI Architecture
- **Modern Industrial Brand Iconography (`studio.svg` / `glass.py`)**: Redesigned the application logo and global controller glyphs with constant-curvature circular arcs, $45^\circ$ ergonomic inner grip tangents, warm titanium body gradients, and Teenage Engineering safety-orange (`#ff5722`) primary action accents.
- **Single-Contour Controller Schematic (`reference_xbox.svg` / `controller_photo.py` / `controller_schematic.py`)**: Removed floating shoulder/trigger pills from idle display diagrams to preserve a clean front-elevation silhouette, while adding subtle `LB/RB` and `LT/RT` micro-captions and mouse hover glow (`ControllerInput`) on the interactive mapping canvas.
- **Core Cognitive Convergence**: Hid experimental AI/voice assistant navigation by default behind the `GPS_ENABLE_AI=1` feature gate so the workspace remains focused on deterministic hardware control.
- **Typographic & Layout Polish**: Fixed icon clipping on `28x28` `IconButton` controls (`padding: 0`), prevented status pill text truncation across Chinese/English locales, and enabled dynamic width adaptation (`AdjustToContents`) on all toolbar dropdowns.

### 🚀 Core Mapping & Hardware Capabilities
- **3-Tier Hardware Identification (`device_quirks.py`)**: Added zero-latency built-in VID/PID model lookup, offline-cached `SDL_GameControllerDB` + `usb.ids` resolution, automatic DirectInput Type-A/Type-B button normalization, and gyro bias auto-calibration.
- **Gyro Aiming & Left/Right Stick Pointer Selection (`virtual_kbm.py` / `virtual_kbm_ui.py` / `schema_registry.py`)**: Added high-frequency gyroscope camera/cursor aiming with configurable activation triggers (`LT`, `RT`, `LB`, `RB`, `L3`, `R3`, or always-on), sensitivity, and Y-axis inversion, plus selectable left/right stick pointer sources.
- **Directional Outer-Ring Thresholds & Double-Tap Hold (`mapping_engine.py`)**: Added independent per-direction (`up`, `down`, `left`, `right`) outer-ring stick thresholds, `LS:up`誤触过滤 (`suppress_ls_up`), Hold-to-Repeat Turbo (`turbo`, 5–30 Hz), and automatic Double-Tap Hold (`double_tap_hold`) for sprint/dash mechanics.
- **Unified Virtual Gamepad Bus (`virtual_xinput.py`)**: Added `XUSB_REPORT` (Windows `ViGEmBus`) and `HID_GAMEPAD_REPORT` (macOS `VirtualHID`) synthesis with bidirectional rumble notification callbacks and dynamic backend availability detection in `mapping_ui.py`.
- **Cross-Platform Windows & macOS Parity (`macos_actions.py` / `hidhide.py`)**: Aligned macOS `CGEvent` keyboard/mouse synthesis, media/brightness/Mission Control system actions, and UIPI / HidHide guidance with Windows behavior.

### 📦 Packaging & Build Pipeline
- Synchronized version `2.2.0` across `pyproject.toml`, `gamepadstudio/__init__.py`, `GamePadStudio.spec`, and `installer/GamePadStudio.iss`.
- Removed unused `requests` and `qrcode` entries from `requirements.txt` and added explicit PyInstaller hidden imports for new core modules.

---

## [2.1.0] - 2026-10-15

- Initial cross-platform Windows x64 & macOS 14+ release with 300Hz Spring-Mass-Damper virtual KBM, ANSI 87 interactive keybinding workbench, touchpad 10-gesture recognition, per-device response curves, and 4K HEVC/H.264 replay buffer capture.

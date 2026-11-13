# Changelog

All notable changes to **GamePad Studio** are documented in this file.

## [2.3.0] - 2026-11-13

### 🛡️ Universal Windows Compatibility & Anti-Lockout Hardening
- **Kernel XInput UIPI Bypass (`device.py`)**: Added direct `xinput1_4.dll` (`XInputGetStateEx` ordinal 100) kernel `XUSB` fallback when an elevated or anti-cheat foreground game (such as *Infinity Nikki*) blocks background `WM_INPUT` (`RAWINPUT`) messages under standard user privileges, while strictly excluding `active_virtual_xinput_slots()` to avoid feedback loops.
- **Smart Auto-Cloaking Guard (`hidhide.py` / `agent.py` / `studio.py`)**: Prevented physical controllers from ever being cloaked into a non-responsive state on unmapped native gamepad profiles, while mapping is paused, or before `ViGEmBus` is installed; toggling A↔B swap off or pausing mapping automatically restores native controller access.
- **Orphan HidHide Self-Healing & Safe Uninstall (`hidhide.py` / `entry.py` / `GamePadStudio.iss`)**: Added session-owned HidHide state tracking (`hidhide_session.json`), a lightweight `--recover-hidhide` Windows logon & startup guard for crash/power-loss recovery, and mandatory `--uncloak-all` cleanup during uninstallation even when the app is closed.
- **Composite USB XInput Co-Cloaking & Dynamic `HidHideCLI` Discovery (`hidhide.py`)**: Extended `ContainerID` matching to co-cloak/uncloak `Enum\USB` `XnaComposite` and `XboxComposite` nodes for wired and 2.4GHz controllers, added dynamic registry/PATH resolution for `HidHideCLI.exe` with `stdin=DEVNULL` non-blocking execution, and added non-destructive `--app-reg` bootstrap fallback.
- **Player 1 (`Slot 0`) Reservation & C++ Notification GC Safety (`virtual_xinput.py` / `agent.py`)**: Pre-reserved `Slot 0 (1P)` at startup for remapped profiles, added automatic `reclaim_slot_zero()` on physical controller reconnect, surfaced a `2P` slot-contention hint banner, and eliminated CPython cyclic-GC crashes (`0xc000001d`) in `ViGEmClient.dll` notification threads via `weakref` closure isolation.
- **One-Click Driver Installer & Conflict Detection (`driver_setup.py` / `studio.py`)**: Added background one-click download and launch for official signed `ViGEmBus` and `HidHide` installers (with `winget` and browser fallbacks), exclusive `HidHideClient.exe` GUI handle contention detection, one-click Steam HidHide whitelist conflict removal, and tray "Restart as Administrator" handoff (`--wait-pid`) for elevated Virtual KBM games.

### 📦 Packaging & Release Pipeline
- Synchronized version `2.3.0` across `pyproject.toml`, `gamepadstudio/__init__.py`, `GamePadStudio.spec`, and `installer/GamePadStudio.iss`.
- Updated `.github/workflows/build.yml` to build and verify fresh Windows x64 and macOS 15 release artifacts directly on `v*` tag pushes.

---

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

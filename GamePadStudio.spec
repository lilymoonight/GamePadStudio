import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

added_files = [
    ('gamepadstudio/assets', 'gamepadstudio/assets'),
    ('LICENSE', 'licenses'),
]

ffmpeg_binary = 'bin/ffmpeg.exe' if sys.platform == 'win32' else 'bin/ffmpeg'
if sys.platform == 'darwin' and not os.path.isfile(ffmpeg_binary):
    # The architecture-matched wheel avoids a global Homebrew dependency.
    import imageio_ffmpeg
    ffmpeg_binary = imageio_ffmpeg.get_ffmpeg_exe()
added_binaries = [(ffmpeg_binary, 'bin')] if os.path.exists(ffmpeg_binary) else []
if sys.platform == 'darwin':
    capture_helper = 'bin/gps-mac-capture'
    if not os.path.isfile(capture_helper):
        raise RuntimeError('Run python scripts/build_macos_capture.py before packaging macOS.')
    added_binaries.append((capture_helper, 'bin'))

# Keep the runtime path stable regardless of the wheel's versioned filename.
# TOCs use (destination, source, type); preserve executable permissions and
# PyInstaller signing by including FFmpeg as a binary instead of data.

hidden_imports = [
    'gamepadstudio',
    'gamepadstudio.actions',
    'gamepadstudio.macos_actions',
    'gamepadstudio.macos_hotkey',
    'gamepadstudio.macos_permissions',
    'gamepadstudio.agent',
    'gamepadstudio.cli',
    'gamepadstudio.config',
    'gamepadstudio.controller_art',
    'gamepadstudio.controller_catalog',
    'gamepadstudio.controller_gallery',
    'gamepadstudio.controller_photo',
    'gamepadstudio.controller_schematic',
    'gamepadstudio.controller_service',
    'gamepadstudio.device',
    'gamepadstudio.entry',
    'gamepadstudio.glass',
    'gamepadstudio.haptic_engine',
    'gamepadstudio.hidhide',
    'gamepadstudio.input_tester',
    'gamepadstudio.ipc',
    'gamepadstudio.kbm_mapper',
    'gamepadstudio.replay_service',
    'gamepadstudio.screenshot_service',
    'gamepadstudio.screenshot_hotkey',
    'gamepadstudio.screenshot_hotkey_ui',
    'gamepadstudio.studio',
    'gamepadstudio.studio_core',
    'gamepadstudio.te_widgets',
    'gamepadstudio.test_widgets',
    'gamepadstudio.virtual_kbm',
    'gamepadstudio.virtual_kbm_ui',
    'gamepadstudio.gamebar_shield',
    'gamepadstudio.i18n',
    'gamepadstudio.mapping_engine',
    'gamepadstudio.mapping_ui',
    'gamepadstudio.mapping_deck',
    'gamepadstudio.replay_capture',
    'pygame',
    'mss',
    'PySide6',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
]

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=added_binaries,
    datas=added_files,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'unittest', 'pytest'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

if sys.platform == 'darwin':
    a.binaries = [(('bin/ffmpeg' if source == ffmpeg_binary else destination), source, kind)
                  for destination, source, kind in a.binaries]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='GamePadStudio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='gamepadstudio/assets/studio.ico' if sys.platform == 'win32' else 'gamepadstudio/assets/studio.icns',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='GamePadStudio',
)

if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='GamePadStudio.app',
        icon='gamepadstudio/assets/studio.icns',
        bundle_identifier='io.github.lilymoonight.GamePadStudio',
        info_plist={
            'CFBundleShortVersionString': '2.0.1',
            'CFBundleVersion': '2.0.1',
            'NSHighResolutionCapable': True,
            'NSBluetoothAlwaysUsageDescription': '读取已连接的游戏手柄输入，并提供设备支持的反馈。',
        },
    )

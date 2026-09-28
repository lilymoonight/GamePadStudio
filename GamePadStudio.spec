# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

added_files = [
    ('dualsense5/assets', 'dualsense5/assets'),
]

hidden_imports = [
    'dualsense5',
    'dualsense5.actions',
    'dualsense5.agent',
    'dualsense5.cli',
    'dualsense5.config',
    'dualsense5.controller_art',
    'dualsense5.controller_catalog',
    'dualsense5.controller_gallery',
    'dualsense5.controller_photo',
    'dualsense5.controller_schematic',
    'dualsense5.controller_service',
    'dualsense5.device',
    'dualsense5.entry',
    'dualsense5.glass',
    'dualsense5.haptic_engine',
    'dualsense5.hidhide',
    'dualsense5.input_tester',
    'dualsense5.ipc',
    'dualsense5.kbm_mapper',
    'dualsense5.replay_service',
    'dualsense5.screenshot_service',
    'dualsense5.studio',
    'dualsense5.studio_core',
    'dualsense5.te_widgets',
    'dualsense5.test_widgets',
    'dualsense5.virtual_kbm',
    'dualsense5.virtual_kbm_ui',
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
    binaries=[],
    datas=added_files,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'unittest', 'pytest'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

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
    icon='dualsense5/assets/studio.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='GamePadStudio',
)

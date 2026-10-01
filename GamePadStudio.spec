# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

added_files = [
    ('gamepadstudio/assets', 'gamepadstudio/assets'),
]

hidden_imports = [
    'gamepadstudio',
    'gamepadstudio.actions',
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
    icon='gamepadstudio/assets/studio.ico',
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

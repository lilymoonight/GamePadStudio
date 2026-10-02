"""Build a bundled, read-only DualSense Bluetooth diagnostic application.

This script never launches the helper or requests Bluetooth permission. Launch
the built .app through macOS LaunchServices to use its own permission identity.
"""

import argparse
from pathlib import Path
import platform
import shutil
import subprocess
import sys


def build() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[2]
    parser.add_argument('--output-dir', type=Path, default=root / 'build' / 'macos')
    parser.add_argument('--sign-identity', default='-',
                        help='codesign identity; default is ad hoc signing')
    arguments = parser.parse_args()
    if sys.platform != 'darwin':
        raise SystemExit('This helper is built on macOS.')
    architecture = platform.machine()
    if architecture not in ('arm64', 'x86_64'):
        raise SystemExit(f'Unsupported architecture: {architecture}')
    source = Path(__file__).resolve().parent
    application = arguments.output_dir.resolve() / 'GamePadStudio Bluetooth Diagnostics.app'
    contents = application / 'Contents'
    executable = contents / 'MacOS' / 'gps-bluetooth-diagnostics'
    executable.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source / 'BluetoothDiagnostics-Info.plist', contents / 'Info.plist')
    subprocess.run([
        'xcrun', 'clang', '-fobjc-arc', '-O2', '-Wall', '-Wextra',
        '-Wno-deprecated-declarations', '-target', f'{architecture}-apple-macos11.0',
        '-framework', 'Foundation', '-framework', 'CoreBluetooth',
        '-framework', 'IOBluetooth', '-framework', 'IOKit',
        str(source / 'BluetoothDiagnostics.m'), '-o', str(executable),
    ], check=True)
    subprocess.run(['codesign', '--force', '--sign', arguments.sign_identity,
                    '--identifier', 'com.gamepadstudio.bluetooth-diagnostics',
                    str(application)], check=True)
    subprocess.run(['codesign', '--verify', '--strict', str(application)], check=True)
    print(application)


if __name__ == '__main__':
    build()

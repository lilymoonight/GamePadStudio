"""Build the public ScreenCaptureKit helper; only development builds need Xcode."""
from pathlib import Path
import platform
import subprocess
import sys
import tempfile


def build():
    if sys.platform != 'darwin':
        raise SystemExit('This helper is built on macOS.')
    root = Path(__file__).resolve().parents[1]
    output = root / 'bin' / 'gps-mac-capture'
    output.parent.mkdir(parents=True, exist_ok=True)
    architecture = platform.machine()
    if architecture not in ('arm64', 'x86_64'):
        raise SystemExit(f'Unsupported architecture: {architecture}')
    with tempfile.TemporaryDirectory(prefix='gps-swift-cache-') as cache:
        subprocess.run(['xcrun', 'swiftc', str(root/'gamepadstudio/native/MacCapture.swift'),
                        '-O', '-target', f'{architecture}-apple-macos14.0',
                        '-module-cache-path', cache, '-o', str(output)], check=True)
    print(output)


if __name__ == '__main__':
    build()

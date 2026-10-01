"""Restart this workspace's source UI using the regular user configuration."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project))
    parser = argparse.ArgumentParser(description='Run the current GamePad Studio Python source.')
    parser.add_argument('--data-dir', type=Path,
                        default=Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'GamePadStudio')
    parser.add_argument('--page', default='mappings',
                        choices=['home', 'mappings', 'gallery', 'input', 'settings', 'controllers', 'keyboard'])
    parser.add_argument('--lang', default='zh', choices=['auto', 'zh', 'en'])
    args = parser.parse_args()

    from PySide6.QtCore import QCoreApplication
    from gamepadstudio.ipc import request
    app = QCoreApplication([])
    root = args.data_dir.resolve()
    # Close only the Studio endpoints for this configuration, without killing
    # unrelated Python processes or changing saved mapping/curve settings.
    request(root, 'exit', role='ui', timeout=300)
    request(root, 'stop', timeout=300)
    for _ in range(30):
        app.processEvents()
        ui = request(root, 'status', role='ui', timeout=100)
        agent = request(root, 'status', timeout=100)
        if ui is None and agent is None:
            break
        time.sleep(.1)
    else:
        print('The existing Studio did not close. Close it and run Start Debug.cmd again.', file=sys.stderr)
        return 1

    return subprocess.call([sys.executable, str(project / 'main.py'), '--data-dir', str(root),
                            '--page', args.page, '--lang', args.lang], cwd=project)


if __name__ == '__main__':
    raise SystemExit(main())

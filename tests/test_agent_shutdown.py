"""OS shutdown releases simulated held input before recorder teardown."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest


@pytest.mark.skipif(sys.platform == 'win32', reason='LaunchAgent shutdown uses POSIX signals')
def test_sigterm_exits_agent_and_releases_held_input(tmp_path):
    source = r'''
from pathlib import Path
import sys
from PySide6.QtCore import QTimer
from gamepadstudio import agent
from gamepadstudio.mapping_engine import MappingRuntime
from gamepadstudio.studio_core import ConfigStore

root = Path(sys.argv[1])
store = ConfigStore(root)
store.data['mapping_enabled'] = False
store.save()
def record(text):
    with (root / 'order').open('a') as f: f.write(text + '\n')
class Device:
    def scan(self): pass
    def read(self): return None
    def close(self): pass
class Actions:
    def __init__(self): self.held = set()
    def hold(self, value, down):
        if down: self.held.add(value)
        else: self.held.discard(value)
    def release_all(self):
        record('release:' + ','.join(sorted(self.held)))
        self.held.clear()
class Hotkey:
    def __init__(self, *args): pass
    def configure(self, settings): pass
    def close(self): pass
    def status(self): return {}
agent.EmergencyHotkey = Hotkey
agent.MappingRuntime = lambda actions, dispatch: MappingRuntime(actions, dispatch, start_mouse=False)
Original = agent.Agent
class SimulatedAgent(Original):
    def __init__(self, root):
        output = Actions()
        super().__init__(root, Device(), output)
        self.timer.stop()
        self.heartbeat = QTimer(self)
        self.heartbeat.timeout.connect(lambda: None)
        self.heartbeat.start(10)
        def recorder_stop():
            assert not output.held
            record('recorder-stop')
        self.replay_engine.stop = recorder_stop
        def ready():
            output.hold('Ctrl+W', True)
            record('hold')
            (root / 'ready').touch()
        QTimer.singleShot(0, ready)
agent.Agent = SimulatedAgent
raise SystemExit(agent.run(root))
'''
    process = subprocess.Popen([sys.executable, '-c', source, str(tmp_path)],
                               cwd=Path(__file__).resolve().parents[1],
                               env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 8
        while not (tmp_path / 'ready').exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert (tmp_path / 'ready').exists(), process.communicate(timeout=2)
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, (stdout, stderr)
        order = (tmp_path / 'order').read_text().splitlines()
        assert order.index('hold') < order.index('release:Ctrl+W') < order.index('recorder-stop')
        assert not (tmp_path / 'agent.lock').exists()
        from gamepadstudio.studio_core import ConfigStore
        assert ConfigStore(tmp_path).data['mapping_enabled'] is False
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)

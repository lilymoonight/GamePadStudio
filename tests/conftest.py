"""
Pytest configuration and headless environment initialization.
Ensures Qt and SDL run safely in headless CI and container environments.
"""
import os
import sys
import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')


@pytest.fixture(autouse=True)
def isolate_workspace_output(monkeypatch):
    """UI regression tests must never send synthetic events to the desktop."""
    if sys.platform == 'darwin':
        from tests.test_unified_mapping import Actions
        from types import SimpleNamespace
        from gamepadstudio.voice_entry import VoiceEntryService
        monkeypatch.setattr('gamepadstudio.studio.create_actions', Actions)
        class Recording:
            def __init__(self, *args, **kwargs):
                self.running = False
            def status(self):
                return {'running':self.running,'phase':'recording' if self.running else 'idle', 'last_error':''}
            def start(self):
                self.running = True
                return True
            def request_stop(self):
                self.running = False
                return True
            def stop(self):
                self.running = False
        monkeypatch.setattr('gamepadstudio.agent.ManualRecording', Recording)
        monkeypatch.setattr('gamepadstudio.studio.ManualRecording', Recording)
        # New voice controls must also stay isolated when old UI fixtures
        # construct a full Studio; never inspect or activate desktop apps.
        monkeypatch.setattr('gamepadstudio.voice_entry_ui.create_voice_service',
                            lambda: VoiceEntryService(target_backend=SimpleNamespace(
                                list_running_targets=lambda: [])))

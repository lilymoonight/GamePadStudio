import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QScrollArea
from PySide6.QtTest import QTest

from gamepadstudio.antigravity_ui import AntigravityPage
from gamepadstudio.mapping_engine import ACTIONS, effective_mappings, validate_binding, binding_label
from gamepadstudio.studio import Studio
from gamepadstudio.studio_core import ACTION_NAMES, default_config


def test_antigravity_action_registered_in_mapping_engine():
    assert 'antigravity' in ACTIONS
    assert 'antigravity' in ACTION_NAMES
    binding = {'action': 'antigravity', 'target': 'com.google.antigravity', 'mode': 'focus'}
    validated = validate_binding(binding)
    assert validated['action'] == 'antigravity'
    label = binding_label(binding)
    assert 'Antigravity' in label


def test_effective_mappings_injects_antigravity_direct_trigger():
    config = {
        'active_profile': 'test_prof',
        'profiles': {'test_prof': {'0': {'short': {'action': 'none'}, 'long': {'action': 'none'}}}},
        'antigravity_link': {
            'enabled': True,
            'trigger_key': '4',
            'trigger_gesture': 'short',
            'target_bundle_id': 'com.google.antigravity',
            'action_type': 'focus'
        }
    }
    state = {'available_buttons': [0, 4]}
    mappings = effective_mappings(config, state, 'test_prof')
    assert '4' in mappings
    assert mappings['4']['short']['action'] == 'antigravity'
    assert mappings['4']['short']['target'] == 'com.google.antigravity'
    assert mappings['4']['short']['mode'] == 'focus'
    assert mappings['4']['long']['action'] == 'none'


def test_effective_mappings_disabled_when_antigravity_link_disabled():
    config = {
        'active_profile': 'test_prof',
        'profiles': {'test_prof': {'0': {'short': {'action': 'none'}}}},
        'antigravity_link': {
            'enabled': False,
            'trigger_key': '4',
            'trigger_gesture': 'short',
        }
    }
    state = {'available_buttons': [0, 4]}
    mappings = effective_mappings(config, state, 'test_prof')
    assert '4' not in mappings


def test_antigravity_page_ui_and_settings_persistence(tmp_path):
    app = QApplication.instance() or QApplication([])

    class MockStore:
        def __init__(self):
            self.root = tmp_path
            self.data = default_config(tmp_path)
            self.save_called = 0

        def save(self):
            self.save_called += 1

    studio = SimpleNamespace(
        config=default_config(tmp_path),
        store=MockStore(),
        notify=MagicMock(),
        navigate=MagicMock(),
    )

    mock_service = SimpleNamespace(
        targets=lambda: [{'bundle_id': 'com.google.antigravity', 'pid': 9876, 'generation': 1.0}],
        status=lambda: {'supported': True, 'state': 'idle', 'recording': False},
        activate=MagicMock(return_value={'ok': True, 'message': '成功聚焦'}),
        fill_test_text=MagicMock(return_value={'ok': True}),
        begin=MagicMock(),
        end=MagicMock(),
        close=MagicMock(return_value={'ok': True}),
    )

    page = AntigravityPage(studio, service=mock_service, platform='darwin')
    assert page.selected_target() == 'com.google.antigravity'
    assert '9876' in page.status_row.subtitle_label.text()

    # Test toggling direct shortcut checkbox
    assert not page.trigger_enable_cb.isChecked()
    page.trigger_enable_cb.setChecked(True)
    assert studio.config['antigravity_link']['enabled'] is True
    assert studio.store.save_called >= 1

    # Test changing trigger button
    idx = page.trigger_key_combo.findData('5')
    assert idx >= 0
    page.trigger_key_combo.setCurrentIndex(idx)
    assert studio.config['antigravity_link']['trigger_key'] == '5'

    # Test test activation button
    page.activate_target()
    mock_service.activate.assert_called_with('com.google.antigravity')
    studio.notify.assert_called()

    # Test text fill
    page.test_text_input.setText('你好 Antigravity')
    page.fill_test_text()
    mock_service.fill_test_text.assert_called_with('com.google.antigravity', '你好 Antigravity')

    # Test gamepad trigger
    page.trigger_from_gamepad({'target': 'com.google.antigravity', 'mode': 'focus'})
    mock_service.activate.assert_called_with('com.google.antigravity')

    # Test cleanup
    assert page.close_service() is True
    mock_service.close.assert_called()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from gamepadstudio.glass import STYLE
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QMessageBox
    from tests.test_battery_ui import FakeTray

    class Disconnected:
        available = []
        def scan(self): pass
        def read(self): return None
        def close(self): pass

    monkeypatch.setattr('gamepadstudio.studio.Device', Disconnected)
    monkeypatch.setattr('gamepadstudio.studio.QSystemTrayIcon', FakeTray)
    monkeypatch.setattr(QMessageBox, 'information', lambda *args, **kwargs: None)
    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    app.setStyleSheet(STYLE)
    window = Studio(tmp_path, standalone=True, lang='zh')
    window.enabled = False
    for timer in (window.timer, window.scan_timer, window.gallery_timer):
        timer.stop()
    window.poll()
    window.show()
    QTest.qWait(20)
    try:
        yield window
    finally:
        window.cleanup()
        window.hide()
        window.deleteLater()
        app.sendPostedEvents(window, QEvent.DeferredDelete)


def test_studio_workspace_navigation_to_antigravity_page(workspace):
    window = workspace
    assert 7 not in window.nav
    assert not window.voice_entry_group.isVisible()

    window.navigate(7)
    assert window.stack.currentIndex() == 7
    assert 'Antigravity' in window.page_heading.text()

    current_page = window.stack.currentWidget()
    assert isinstance(current_page, QScrollArea)
    assert current_page.widget() == window.antigravity_page
    assert current_page.horizontalScrollBar().maximum() == 0

    # Test dispatching antigravity action
    window.antigravity_page.trigger_from_gamepad = MagicMock()
    window.dispatch({'action': 'antigravity', 'target': 'com.google.antigravity'}, down=True)
    window.antigravity_page.trigger_from_gamepad.assert_called_once()

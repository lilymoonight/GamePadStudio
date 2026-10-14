"""Unit tests for the upgraded tactile sliding button Toggle (Left: 打开, Right: 关闭)."""
import os

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from gamepadstudio.glass import Toggle
from gamepadstudio import i18n


@pytest.fixture(scope='session')
def app():
    return QApplication.instance() or QApplication([])


def test_toggle_defaults_and_programmatic_change(app):
    t = Toggle()
    assert not t.isChecked()
    assert t.get_thumb_pos() == 1.0  # Right (关闭 / OFF)

    t.setChecked(True)
    assert t.isChecked()
    assert t.get_thumb_pos() == 0.0  # Left (打开 / ON)

    t.setChecked(False)
    assert not t.isChecked()
    assert t.get_thumb_pos() == 1.0  # Right (关闭 / OFF)


def test_toggle_mouse_clicks_left_and_right(app):
    t = Toggle()
    t.resize(96, 28)

    # Click left half (e.g. x=20) -> should setChecked(True)
    QTest.mouseClick(t, Qt.LeftButton, pos=QPoint(20, 14))
    assert t.isChecked() is True
    assert t.get_thumb_pos() == 0.0

    # Clicking left half again keeps it True
    QTest.mouseClick(t, Qt.LeftButton, pos=QPoint(20, 14))
    assert t.isChecked() is True

    # Click right half (e.g. x=75) -> should setChecked(False)
    QTest.mouseClick(t, Qt.LeftButton, pos=QPoint(75, 14))
    assert t.isChecked() is False
    assert t.get_thumb_pos() == 1.0

    # Clicking right half again keeps it False
    QTest.mouseClick(t, Qt.LeftButton, pos=QPoint(75, 14))
    assert t.isChecked() is False


def test_toggle_keyboard_interactions(app):
    t = Toggle()
    assert not t.isChecked()

    # Left arrow sets True (打开)
    QTest.keyClick(t, Qt.Key_Left)
    assert t.isChecked() is True

    # Right arrow sets False (关闭)
    QTest.keyClick(t, Qt.Key_Right)
    assert t.isChecked() is False

    # Spacebar toggles
    QTest.keyClick(t, Qt.Key_Space)
    assert t.isChecked() is True

    QTest.keyClick(t, Qt.Key_Space)
    assert t.isChecked() is False


def test_toggle_label_visibility_rules(app):
    # Generic labels should be hidden
    t_generic1 = Toggle('启用')
    assert not t_generic1._should_show_label()

    t_generic2 = Toggle('Enable')
    assert not t_generic2._should_show_label()

    t_empty = Toggle('')
    assert not t_empty._should_show_label()

    # Feature labels should be shown
    t_feature = Toggle('轻推慢走')
    assert t_feature._should_show_label()
    assert t_feature.sizeHint().width() > 100

    # Explicit override works
    t_override = Toggle('启用', show_label=True)
    assert t_override._should_show_label()


def test_toggle_paint_event_clean_render(app):
    t = Toggle('轻推慢走')
    t.resize(160, 34)

    pixmap = QPixmap(160, 34)
    t.render(pixmap)


def test_toggle_i18n_labels(app):
    t = Toggle()
    orig = i18n.get_language()
    try:
        i18n.set_language('zh_CN')
        on, off = t._get_labels()
        assert on == '打开'
        assert off == '关闭'

        i18n.set_language('en')
        on_en, off_en = t._get_labels()
        assert on_en == 'ON'
        assert off_en == 'OFF'
    finally:
        i18n.set_language(orig)

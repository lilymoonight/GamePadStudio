import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPainter, QImage
from PySide6.QtCore import Qt
from gamepadstudio.glass import LedSwatch
from gamepadstudio.studio_core import DEVICE_SETTING_DEFAULTS


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def test_led_swatch_off_slash_render(qapp):
    swatch = LedSwatch('#000000')
    assert swatch.color == '#000000'
    assert swatch.selected is False

    swatch.set_selected(True)
    assert swatch.selected is True

    # Paint onto a test image to ensure paintEvent executes without exception
    img = QImage(26, 26, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    painter = QPainter(img)
    swatch.paintEvent(None)


def test_led_default_setting_ps5_blue():
    # PS5 DualSense classic light blue default is #5686ff
    assert DEVICE_SETTING_DEFAULTS.get('led') == '#5686ff'

from PySide6.QtCore import QPoint, QRect
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from gamepadstudio.controller_photo import ControllerInput


def test_controller_selection_highlights_each_combination_member_and_filters_unavailable_buttons():
    app = QApplication.instance() or QApplication([])
    photo = ControllerInput()
    photo.set_family('xbox')
    photo.available = set(range(16))
    photo.resize(640, 420)
    photo.show()
    QTest.qWait(10)
    try:
        baseline = photo.grab().toImage()
        photo.select_buttons({0, 2})
        assert photo.selected_buttons == {0, 2}
        highlighted = photo.grab().toImage()
        rect = photo.product_rect()
        for key in (0, 2):
            x, y = photo.anchors()[key]
            center = QPoint(round(rect.left() + x * rect.width()), round(rect.top() + y * rect.height()))
            region = QRect(center.x() - 22, center.y() - 22, 44, 44)
            assert baseline.copy(region) != highlighted.copy(region)
        photo.available = {0}
        photo.select_buttons({0, 2, 20})
        assert photo.selected_buttons == {0}
        photo.update_state(None)
        assert photo.available == {0}
        photo.select_button(None)
        assert photo.selected is None and not photo.selected_buttons
    finally:
        photo.close()

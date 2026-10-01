"""Trigger diagram entries must identify real canonical inputs at every size."""
import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from gamepadstudio.controller_art import ControllerArt
from gamepadstudio.controller_photo import ControllerInput, ControllerPhoto


@pytest.mark.parametrize('family', ['dualsense', 'dualshock4', 'xbox', 'switch', 'generic'])
@pytest.mark.parametrize('size', [(640, 420), (340, 300)])
def test_photo_trigger_entries_click_and_select_independently_of_shoulders(family, size):
    app = QApplication.instance() or QApplication([])
    photo = ControllerInput()
    photo.set_family(family)
    photo.available = {str(key) for key in range(21)} | {'LT', 'RT'}
    photo.resize(*size)
    photo.show()
    app.processEvents()
    clicked, legacy = [], []
    photo.input_clicked.connect(clicked.append)
    photo.button_clicked.connect(legacy.append)
    try:
        baseline = photo.grab().toImage()
        for key in ('LT', 'RT'):
            region = photo.trigger_regions()[key]
            assert QRect(photo.rect()).contains(region.toAlignedRect())
            QTest.mouseClick(photo, Qt.LeftButton, pos=region.center().toPoint())
            assert clicked[-1] == key and not legacy
        photo.select_buttons(['LT', 'RT', '9'])
        assert photo.selected_buttons == {'LT', 'RT', 9}
        selected = photo.grab().toImage()
        for region in photo.trigger_regions().values():
            assert baseline.copy(region.toAlignedRect().adjusted(-2, -2, 2, 3)) != selected.copy(region.toAlignedRect().adjusted(-2, -2, 2, 3))
        rect = photo.product_rect()
        for key in (9, 10):
            x, y = photo.anchors()[key]
            position = QPoint(round(rect.left() + x * rect.width()), round(rect.top() + y * rect.height()))
            QTest.mouseClick(photo, Qt.LeftButton, pos=position)
            assert clicked[-1] == str(key) and legacy[-1] == key
    finally:
        photo.close()


def test_partial_axis_report_disables_only_the_absent_trigger_and_clears_selection():
    app = QApplication.instance() or QApplication([])
    photo = ControllerInput()
    photo.resize(640, 420)
    photo.show()
    photo.update_state({'family': 'dualsense', 'available_buttons': [9, 10],
                        'available_axes': [0, 1, 2, 3, 4], 'buttons': [], 'axes': [0.] * 6})
    app.processEvents()
    clicked = []
    photo.input_clicked.connect(clicked.append)
    try:
        photo.select_buttons(['LT', 'RT'])
        assert photo.selected_buttons == {'LT'}
        for key in ('LT', 'RT'):
            QTest.mouseClick(photo, Qt.LeftButton, pos=photo.trigger_regions()[key].center().toPoint())
        assert clicked == ['LT']
        photo.update_state({'family': 'dualsense', 'available_buttons': [9, 10],
                            'available_axes': [0, 1], 'buttons': [], 'axes': [0.] * 6})
        assert not photo.selected_buttons
        QTest.mouseClick(photo, Qt.LeftButton, pos=photo.trigger_regions()['LT'].center().toPoint())
        assert clicked == ['LT']
    finally:
        photo.close()


@pytest.mark.parametrize('sources', [{'9', '10'}, set()])
def test_canonical_empty_axis_sources_never_offer_phantom_trigger_mapping(sources):
    app = QApplication.instance() or QApplication([])
    photo = ControllerInput()
    photo.available = sources
    photo.resize(640, 420)
    photo.show()
    app.processEvents()
    clicked = []
    photo.input_clicked.connect(clicked.append)
    try:
        for region in photo.trigger_regions().values():
            QTest.mouseClick(photo, Qt.LeftButton, pos=region.center().toPoint())
        assert not clicked
    finally:
        photo.close()


def test_idle_input_and_unchanged_selection_do_not_schedule_repaints(monkeypatch):
    app = QApplication.instance() or QApplication([])
    photo = ControllerInput()
    state = {'family': 'dualsense', 'available_buttons': [9, 10],
             'available_axes': [0, 1, 2, 3, 4, 5], 'buttons': [], 'axes': [0.] * 6}
    photo.update_state(state)
    photo.select_buttons(['LT'])
    updates = []
    monkeypatch.setattr(photo, 'update', lambda: updates.append(True))
    try:
        photo.update_state(state)
        photo.select_buttons(['LT'])
        assert not updates
        photo.update_state({**state, 'available_axes': [0, 1, 2, 3]})
        assert updates and not photo.selected_buttons
        updates.clear()
        photo.update_state({**state, 'available_axes': [0, 1, 2, 3, 5]})
        assert updates and not photo.selected_buttons
    finally:
        photo.close()


@pytest.mark.parametrize('family', ['dualsense', 'dualshock4', 'xbox', 'switch', 'generic'])
def test_static_product_diagrams_keep_trigger_labels_inside_the_preview(family):
    app = QApplication.instance() or QApplication([])
    photo = ControllerPhoto(family)
    photo.resize(320, 150)
    photo.show()
    app.processEvents()
    try:
        for region in photo.trigger_regions().values():
            assert photo.rect().contains(region.toAlignedRect())
        assert not photo.grab().isNull()
    finally:
        photo.close()


@pytest.mark.parametrize('family', ['dualsense', 'xbox', 'switch', 'generic'])
def test_vector_fallback_trigger_clicks_emit_canonical_strings_and_respect_axes(family):
    app = QApplication.instance() or QApplication([])
    art = ControllerArt()
    art.set_family(family)
    art.available = {'9', '10', 'LT'}
    art.resize(640, 365)
    art.show()
    app.processEvents()
    clicked, legacy = [], []
    art.input_clicked.connect(clicked.append)
    art.button_clicked.connect(legacy.append)
    try:
        for key in ('LT', 'RT'):
            QTest.mouseClick(art, Qt.LeftButton, pos=QPoint(*art.points[key]))
        assert clicked == ['LT'] and not legacy
        QTest.mouseClick(art, Qt.LeftButton, pos=QPoint(*art.points[9]))
        assert clicked == ['LT', '9'] and legacy == [9]
    finally:
        art.close()

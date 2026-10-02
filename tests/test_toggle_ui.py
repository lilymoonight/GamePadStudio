"""The binding editor exposes keyboard and mouse toggle hold without changing old bindings."""
import os

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from gamepadstudio.mapping_ui import BindingDialog
from tests.mapping_fixtures import MappingOwner


@pytest.fixture
def owner(tmp_path):
    app = QApplication.instance() or QApplication([])
    value = MappingOwner(tmp_path)
    value.snapshot = {'family': 'dualsense', 'device_key': 'ds5:unit', 'instance_id': 1,
                      'touchpad': True, 'touchpad_count': 1, 'touchpad_fingers': 2,
                      'available_buttons': list(range(21)), 'available_axes': list(range(6)),
                      'buttons': [], 'axes': [0.] * 6, 'touch_fingers': []}
    value.store.activate_controller(value.snapshot)
    yield value
    value.close()


def test_keyboard_toggle_round_trips_and_default_hold_remains_unchanged(owner):
    original = {'short': {'action': 'hold', 'value': 'Ctrl+W', 'mode': 'toggle'},
                'long': {'action': 'none'}}
    dialog = BindingDialog(owner, '0', original, mode='kbm')
    dialog.timer.stop()
    try:
        assert dialog.hold_modes['short'].currentData() == 'toggle'
        assert not dialog.hold_mode_rows['short'].isHidden()
        assert dialog.value()['short'] == original['short']
        dialog.validate()
        assert dialog.result() == QDialog.DialogCode.Accepted
    finally:
        dialog.close()

    default = BindingDialog(owner, '0', {'short': {'action': 'hold', 'value': 'W'}}, mode='kbm')
    default.timer.stop()
    try:
        assert default.hold_modes['short'].currentData() == 'hold'
        assert default.value()['short'] == {'action': 'hold', 'value': 'W'}
    finally:
        default.close()


def test_mouse_toggle_visible_only_for_held_actions_and_not_touch(owner):
    dialog = BindingDialog(owner, 'RT', {'short': {'action': 'mouse_hold', 'value': 'right',
                                                'mode': 'toggle'}}, mode='kbm')
    dialog.timer.stop()
    try:
        assert dialog.hold_modes['short'].currentData() == 'toggle'
        assert dialog.value()['short'] == {'action': 'mouse_hold', 'value': 'right', 'mode': 'toggle'}
        dialog.action_combos['short'].setCurrentIndex(dialog.action_combos['short'].findData('mouse_click'))
        assert dialog.hold_mode_rows['short'].isHidden()
        assert 'mode' not in dialog.value()['short']
        dialog.action_combos['short'].setCurrentIndex(dialog.action_combos['short'].findData('mouse_hold'))
        dialog.inputs[0].setCurrentIndex(dialog.inputs[0].findData('TP:tap'))
        assert dialog.touch_gesture and dialog.hold_mode_rows['short'].isHidden()
        assert 'mode' not in dialog.value()['short']
    finally:
        dialog.close()

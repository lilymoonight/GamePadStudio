"""Completed touch gestures have one action, scoped to the actual device."""
import os

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from gamepadstudio.mapping_engine import canonical_trigger, validate_mappings
from gamepadstudio.mapping_ui import BindingDialog
from gamepadstudio.touch_gestures import TOUCH_INPUTS
from tests.mapping_fixtures import MappingOwner


@pytest.fixture
def owner(tmp_path):
    app = QApplication.instance() or QApplication([])
    owner = MappingOwner(tmp_path)
    owner.snapshot = {'family': 'dualsense', 'device_key': 'ds5:unit', 'instance_id': 1,
                      'touchpad': True, 'touchpad_count': 1, 'touchpad_fingers': 2,
                      'available_buttons': list(range(21)), 'available_axes': list(range(6)),
                      'buttons': [], 'axes': [0.] * 6, 'touch_fingers': []}
    owner.store.activate_controller(owner.snapshot)
    yield owner
    owner.close()


def test_touch_editor_has_one_visible_action_and_all_supported_sources(owner):
    dialog = BindingDialog(owner, 'TP:swipe_up', mode='gamepad')
    dialog.timer.stop()
    try:
        assert dialog.mode == 'kbm'
        assert set(dialog.action_combos) == {'short', 'long'}
        assert dialog.action_boxes['long'].isHidden()
        assert set(TOUCH_INPUTS) <= set(dialog.inputs[0].itemData(i) for i in range(dialog.inputs[0].count()))
        assert all(combo.isHidden() and not combo.isEnabled() for combo in dialog.inputs[1:])
        assert not dialog.capture_button.isHidden() and dialog.long_press.isHidden()
        dialog.start_capture()
        assert dialog.capturing
        dialog.capturing = False
        combo = dialog.action_combos['short']
        combo.setCurrentIndex(combo.findData('shortcut'))
        dialog.kbm_fields['short'].setText('Ctrl+S')
        dialog.validate()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.trigger() == 'TP:swipe_up'
        assert dialog.value() == {'short': {'action': 'shortcut', 'value': 'Ctrl+S'},
                                  'long': {'action': 'none'}}
    finally:
        dialog.close()


def test_one_finger_device_does_not_offer_two_finger_action(owner):
    owner.snapshot['touchpad_fingers'] = 1
    dialog = BindingDialog(owner, 'TP:tap')
    dialog.timer.stop()
    try:
        assert dialog.inputs[0].findData('TP:two_tap') == -1
        assert dialog.inputs[0].findData('TP:swipe_left') >= 0
    finally:
        dialog.close()


@pytest.mark.parametrize('replacement', [None, {'family': 'xbox', 'device_key': 'other', 'instance_id': 2}])
def test_gesture_editor_cannot_save_after_device_changes(owner, replacement):
    dialog = BindingDialog(owner, 'TP:tap')
    dialog.timer.stop()
    try:
        owner.snapshot = replacement
        dialog.validate()
        assert not dialog.save_button.isEnabled()
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert dialog.error.text()
    finally:
        dialog.close()


def test_regular_button_editor_keeps_physical_touchpad_click_separate(owner):
    dialog = BindingDialog(owner, '20', mode='kbm')
    dialog.timer.stop()
    try:
        assert set(dialog.action_combos) == {'short', 'long'}
        assert dialog.inputs[0].findData('20') >= 0
        assert dialog.inputs[0].findData('TP:tap') >= 0
        assert dialog.trigger() == '20' and not dialog.touch_gesture
        assert all(combo.findData('TP:tap') == -1 for combo in dialog.inputs[1:])
    finally:
        dialog.close()


def test_completed_touch_action_cannot_be_used_as_chord_or_repeated_long_press():
    assert canonical_trigger('tp:SWIPE_UP') == 'TP:swipe_up'
    with pytest.raises(ValueError):
        canonical_trigger('TP:tap+0')
    with pytest.raises(ValueError):
        validate_mappings({'TP:hold': {'long': {'action': 'hold', 'value': 'W'}}})


def capture_frames(owner, dialog, monkeypatch, frames):
    real_update = dialog.touch_recognizer.update
    clock = [0.]
    monkeypatch.setattr(dialog.touch_recognizer, 'update',
                        lambda state, settings=None: real_update(state, now=clock[0], settings=settings))
    dialog.start_capture()
    for now, fingers, buttons in [(0., (), ()), *frames]:
        clock[0] = now
        owner.snapshot['buttons'] = list(buttons)
        owner.snapshot['touch_fingers'] = [
            {'pad': 0, 'finger': i, 'contact': generation, 'x': x, 'y': y, 'pressure': 1}
            for i, x, y, generation in fingers]
        dialog.poll()


@pytest.mark.parametrize(('expected', 'frames'), [
    ('TP:tap', [(.01, ((0, .5, .5, 1),), ()), (.08, (), ()), (.37, (), ())]),
    ('TP:double_tap', [(.01, ((0, .5, .5, 1),), ()), (.08, (), ()),
                       (.15, ((0, .5, .5, 2),), ()), (.22, (), ())]),
    ('TP:hold', [(.01, ((0, .5, .5, 1),), ()), (.60, ((0, .5, .5, 1),), ())]),
    ('TP:swipe_up', [(.01, ((0, .5, .5, 1),), ()), (.12, ((0, .5, .2, 1),), ()), (.2, (), ())]),
    ('TP:swipe_down', [(.01, ((0, .5, .5, 1),), ()), (.12, ((0, .5, .8, 1),), ()), (.2, (), ())]),
    ('TP:swipe_left', [(.01, ((0, .5, .5, 1),), ()), (.12, ((0, .2, .5, 1),), ()), (.2, (), ())]),
    ('TP:swipe_right', [(.01, ((0, .5, .5, 1),), ()), (.12, ((0, .8, .5, 1),), ()), (.2, (), ())]),
    ('TP:two_tap', [(.01, ((0, .3, .5, 1), (1, .7, .5, 2)), ()), (.1, (), ())]),
    ('TP:scroll_up', [(.01, ((0, .3, .5, 1), (1, .7, .5, 2)), ()),
                      (.12, ((0, .3, .3, 1), (1, .7, .3, 2)), ())]),
    ('TP:scroll_down', [(.01, ((0, .3, .5, 1), (1, .7, .5, 2)), ()),
                        (.12, ((0, .3, .7, 1), (1, .7, .7, 2)), ())]),
])
def test_keyboard_target_can_capture_every_touch_source(owner, monkeypatch, expected, frames):
    dialog = BindingDialog(owner, '0', output='M', new=True, mode='kbm')
    dialog.timer.stop()
    try:
        capture_frames(owner, dialog, monkeypatch, frames)
        assert dialog.trigger() == expected and not dialog.capturing
        assert dialog.touch_gesture and dialog.mode == 'kbm'
        assert dialog.value() == {'short': {'action': 'shortcut', 'value': 'M'}, 'long': {'action': 'none'}}
        assert dialog.action_boxes['long'].isHidden()
        validate_mappings({dialog.trigger(): dialog.value()})
    finally:
        dialog.close()


def test_switching_gamepad_source_to_touch_and_back_restores_mode(owner):
    dialog = BindingDialog(owner, '0', {'short': {'action': 'gamepad_button', 'value': '2'},
                                      'long': {'action': 'capture'}}, mode='gamepad')
    dialog.timer.stop()
    try:
        assert dialog.mode == 'gamepad'
        dialog.inputs[0].setCurrentIndex(dialog.inputs[0].findData('TP:tap'))
        assert dialog.mode == 'kbm' and dialog.touch_gesture
        assert dialog.action_combos['short'].findData('gamepad_button') == -1
        assert dialog.action_combos['short'].findData('shortcut') >= 0
        assert dialog.action_boxes['long'].isHidden()
        dialog.inputs[0].setCurrentIndex(dialog.inputs[0].findData('20'))
        assert dialog.mode == 'gamepad' and not dialog.touch_gesture
        assert not dialog.action_boxes['long'].isHidden()
        assert dialog.value()['short'] == {'action': 'gamepad_button', 'value': '2'}
        assert dialog.value()['long'] == {'action': 'capture'}
    finally:
        dialog.close()


def test_capture_does_not_turn_touch_and_physical_button_into_a_chord(owner, monkeypatch):
    dialog = BindingDialog(owner, '0', output='M', new=True, mode='kbm')
    dialog.timer.stop()
    try:
        capture_frames(owner, dialog, monkeypatch, [
            (.01, ((0, .5, .5, 1),), ()),
            (.05, ((0, .5, .5, 1),), (0,)),
            (.1, ((0, .5, .5, 1),), ()),
        ])
        assert dialog.capturing
        owner.snapshot['touch_fingers'] = []
        dialog.poll()
        assert dialog.trigger() == '0' and not dialog.capturing
    finally:
        dialog.close()


def test_capture_waits_for_existing_touch_to_end_before_new_input(owner, monkeypatch):
    dialog = BindingDialog(owner, '0', output='M', new=True, mode='kbm')
    dialog.timer.stop()
    try:
        owner.snapshot['touch_fingers'] = [{'pad': 0, 'finger': 0, 'contact': 1, 'x': .5, 'y': .5}]
        dialog.start_capture()
        dialog.poll()
        assert not dialog.ready
        owner.snapshot['touch_fingers'] = []
        dialog.poll()
        assert dialog.ready and dialog.capturing
    finally:
        dialog.close()


@pytest.mark.parametrize('finish', ['accept', 'reject', 'close'])
def test_closed_editor_stops_polling_and_discards_capture(owner, monkeypatch, finish):
    dialog = BindingDialog(owner, '0', output='M', new=True, mode='kbm')
    updates = []
    real_update = dialog.touch_recognizer.update

    def observed_update(*args, **kwargs):
        updates.append(True)
        return real_update(*args, **kwargs)

    monkeypatch.setattr(dialog.touch_recognizer, 'update', observed_update)
    dialog.show()
    try:
        assert dialog.timer.isActive()
        dialog.start_capture()
        dialog.poll()
        owner.snapshot['touch_fingers'] = [
            {'pad': 0, 'finger': 0, 'contact': 1, 'x': .5, 'y': .5, 'pressure': 1}]
        dialog.poll()
        assert dialog.capturing and updates
        getattr(dialog, finish)()
        assert not dialog.timer.isActive()
        assert not dialog.capturing and not dialog.ready and not dialog.best
        assert dialog.finished_editing
        calls = len(updates)
        owner.snapshot['touch_fingers'] = []
        dialog.start_capture()
        dialog.poll()
        assert not dialog.capturing and len(updates) == calls
    finally:
        dialog.close()


def test_device_change_stops_editor_timer_until_reopened(owner):
    dialog = BindingDialog(owner, '0', mode='kbm')
    try:
        dialog.start_capture()
        owner.snapshot = None
        dialog.poll()
        assert dialog.context_changed and not dialog.capturing
        assert not dialog.timer.isActive()
        assert not dialog.save_button.isEnabled()
        dialog.close()
        assert dialog.finished_editing
    finally:
        dialog.close()

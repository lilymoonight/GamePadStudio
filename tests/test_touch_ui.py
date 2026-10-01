"""Touchpad drafts, gesture bindings and device identity remain separate."""
import copy
import os

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QDialogButtonBox

from gamepadstudio.studio_core import ConfigStore
from gamepadstudio.touch_ui import TouchGestureDialog
from tests.test_device_scope_ui import device, select, workspace


def touch_device(identity=1, family='dualsense', fingers=2):
    return dict(device(identity, family, touch=True), touchpad_fingers=fingers)


def test_touch_entry_visibility_requires_real_touch_capability(workspace):
    window, app = workspace
    assert window.row_touchpad.isHidden()
    assert window.touch_gesture_settings_row.isHidden()
    assert window.virtual_kbm_page.touchpad_btn.isHidden()
    select(window, app, device(1))
    assert window.touch_gesture_settings_row.isHidden()
    assert window.virtual_kbm_page.touchpad_btn.isHidden()
    select(window, app, touch_device(2))
    assert not window.row_touchpad.isHidden()
    assert not window.touch_gesture_settings_row.isHidden()
    assert not window.virtual_kbm_page.touchpad_btn.isHidden()
    select(window, app, touch_device(3, 'dualshock4'))
    assert not window.virtual_kbm_page.touchpad_btn.isHidden()
    select(window, app, None)
    assert window.row_touchpad.isHidden()
    assert window.touch_settings_row.isHidden()
    assert window.touch_gesture_settings_row.isHidden()
    assert window.virtual_kbm_page.touchpad_btn.isHidden()


def test_overview_settings_and_keyboard_open_correct_profile_without_writes(workspace, monkeypatch):
    window, app = workspace
    select(window, app, touch_device())
    opened = []
    def inspect(dialog):
        opened.append(dialog.profile)
        assert dialog.enabled_box.isChecked()
        assert not dialog.mouse_box.isChecked()
        assert not dialog.scroll_box.isChecked()
        dialog.reject()
        return QDialog.Rejected
    monkeypatch.setattr(TouchGestureDialog, 'exec', inspect)
    original = copy.deepcopy(window.config)
    gamepad_profile = window.current_gamepad_profile()
    keyboard_profile = window.virtual_kbm_page.current_scheme()
    window.navigate(0)
    window.touch_action_btn.click()
    window.navigate(4)
    window.touch_gesture_settings_row.control.click()
    window.navigate(6)
    window.virtual_kbm_page.touchpad_btn.click()
    assert opened == [gamepad_profile, gamepad_profile, keyboard_profile]
    assert window.config == original


def test_open_edit_reset_and_close_leave_settings_unchanged(workspace):
    window, app = workspace
    state = touch_device()
    select(window, app, state)
    original = copy.deepcopy(window.config)
    dialog = TouchGestureDialog(window, window.current_gamepad_profile())
    try:
        assert len(dialog.gesture_rows) == 10
        dialog.enabled_box.setChecked(True)
        dialog.mouse_box.setChecked(True)
        dialog.scroll_box.setChecked(True)
        dialog.sensitivity_slider.setValue(80)
        dialog.reset_settings()
        assert not dialog.enabled_box.isChecked()
        assert not dialog.mouse_box.isChecked()
        assert not dialog.scroll_box.isChecked()
        assert dialog.sensitivity_slider.value() == 50
        dialog.reject()
        assert window.config == original
    finally:
        dialog.close()


def test_apply_settings_isolates_devices_and_scroll_is_independent(workspace):
    window, app = workspace
    first, second = touch_device(1), touch_device(2)
    select(window, app, first)
    dialog = TouchGestureDialog(window, window.current_gamepad_profile())
    try:
        dialog.enabled_box.setChecked(True)
        dialog.mouse_box.setChecked(True)
        dialog.sensitivity_slider.setValue(85)
        assert dialog.apply()
        assert window.touch_mouse_box.isChecked()
    finally:
        dialog.close()
    first_settings = copy.deepcopy(window.store.settings_for(first))
    select(window, app, second)
    window.setting('touch_gestures_enabled', False)
    window.setting('touch_gesture_sensitivity', .5)
    dialog = TouchGestureDialog(window, window.virtual_kbm_page.current_scheme())
    try:
        assert not dialog.enabled_box.isChecked()
        assert not dialog.mouse_box.isChecked()
        assert not dialog.scroll_box.isChecked()
        assert dialog.sensitivity_slider.value() == 50
        dialog.scroll_box.setChecked(True)
        dialog.save()
        assert dialog.result() == QDialog.Accepted
        assert window.store.settings_for(second)['touch_scroll']
        assert not window.store.settings_for(second)['touch_gestures_enabled']
        assert window.store.settings_for(first) == first_settings
    finally:
        dialog.close()
    persisted = ConfigStore(window.store.root)
    assert persisted.settings_for(first)['touch_gesture_sensitivity'] == .85
    assert persisted.settings_for(first)['touch_gestures_enabled']
    assert persisted.settings_for(second)['touch_scroll']
    assert not persisted.settings_for(second)['touch_mouse']


def test_gesture_binding_save_is_separate_from_unsaved_switches(workspace):
    window, app = workspace
    state = touch_device()
    select(window, app, state)
    profile = window.current_gamepad_profile()
    calls = []
    def edit(token, **kwargs):
        calls.append((token, kwargs))
        window.config['profiles'][kwargs['profile']][token] = {
            'short': {'action': 'shortcut', 'value': 'F13'}, 'long': {'action': 'none'}}
        window.store.save()
    window.edit_mapping = edit
    original = copy.deepcopy(window.store.settings_for(state))
    dialog = TouchGestureDialog(window, profile)
    try:
        dialog.enabled_box.setChecked(True)
        dialog.sensitivity_slider.setValue(20)
        dialog.gesture_rows['TP:swipe_up'].edit_button.click()
        assert calls == [('TP:swipe_up', {'profile': profile, 'mode': 'kbm'})]
        assert dialog.gesture_rows['TP:swipe_up'].summary.toolTip() == 'F13'
        dialog.reject()
        assert window.store.settings_for(state) == original
        persisted = ConfigStore(window.store.root)
        assert persisted.data['profiles'][profile]['TP:swipe_up']['short']['value'] == 'F13'
    finally:
        dialog.close()


@pytest.mark.parametrize('change', ['disconnect', 'switch', 'reconnect', 'touchpad', 'two_finger'])
def test_stale_touch_editor_blocks_settings_and_action_edits(workspace, change):
    window, app = workspace
    first = touch_device()
    select(window, app, first)
    original = copy.deepcopy(window.store.settings_for(first))
    edits = []
    window.edit_mapping = lambda *args, **kwargs: edits.append((args, kwargs))
    dialog = TouchGestureDialog(window, window.current_gamepad_profile())
    try:
        dialog.enabled_box.setChecked(True)
        replacement = None if change == 'disconnect' else touch_device(2) if change == 'switch' else dict(first)
        if change == 'reconnect':
            replacement['instance_id'] = 11
        elif change == 'touchpad':
            replacement['touchpad'] = False
        elif change == 'two_finger':
            replacement['touchpad_fingers'] = 1
        select(window, app, replacement)
        QTest.qWait(120)
        assert not dialog.controls.button(QDialogButtonBox.Apply).isEnabled()
        assert not dialog.controls.button(QDialogButtonBox.Save).isEnabled()
        dialog.edit_gesture('TP:tap')
        assert dialog.apply() is False
        dialog.save()
        assert not edits
        assert dialog.result() != QDialog.Accepted
        assert window.store.settings_for(first) == original
    finally:
        dialog.close()


def test_one_finger_device_does_not_show_double_finger_options(workspace):
    window, app = workspace
    state = touch_device(fingers=1)
    select(window, app, state)
    dialog = TouchGestureDialog(window, window.current_gamepad_profile())
    try:
        assert dialog.scroll_box is None
        assert 'TP:two_tap' not in dialog.gesture_rows
        assert len(dialog.gesture_rows) == 7
        assert dialog.apply()
        assert not window.store.settings_for(state)['touch_scroll']
    finally:
        dialog.close()


def test_touchpad_toolbar_wraps_without_horizontal_scroll(workspace):
    from PySide6.QtWidgets import QScrollArea
    window, app = workspace
    select(window, app, touch_device())
    window.resize(960, 700)
    window.show()
    window.navigate(6)
    app.processEvents()
    assert not window.virtual_kbm_page.touchpad_btn.isHidden()
    areas = window.stack.currentWidget().findChildren(QScrollArea)
    assert all(area.horizontalScrollBar().maximum() == 0 for area in areas)
    dialog = TouchGestureDialog(window, window.virtual_kbm_page.current_scheme())
    try:
        dialog.resize(360, 640)
        dialog.show()
        app.processEvents()
        assert dialog.width() == 360
        assert all(area.horizontalScrollBar().maximum() == 0 for area in dialog.findChildren(QScrollArea))
        for row in dialog.gesture_rows.values():
            assert row.edit_button.geometry().right() < row.width()
    finally:
        dialog.close()


def test_keyboard_feedback_distinguishes_touch_events_and_scroll(workspace):
    window, app = workspace
    select(window, app, touch_device())
    page = window.virtual_kbm_page
    page.update_feedback({'events': [{'trigger': 'TP:swipe_up', 'gesture': 'touch', 'action': 'F13'}]}, connected=True)
    assert '短按' not in page.live.text()
    assert '长按' not in page.live.text()
    assert 'F13' in page.live.text()
    page.update_feedback({'events': [{'trigger': 'TP:two_tap', 'gesture': 'scroll', 'action': 'mouse wheel'}]}, connected=True)
    assert '滚动 → mouse wheel' in page.live.text()
    assert '短按' not in page.live.text()
    page.update_feedback({'events': [{'trigger': '0', 'gesture': 'long', 'action': 'F14'}]}, connected=True)
    assert '长按 → F14' in page.live.text()


def test_preview_waits_for_release_and_recognizes_without_os_output(workspace, monkeypatch):
    from tests.test_touch_gestures import frame
    window, app = workspace
    state = touch_device()
    select(window, app, state)
    scope = state['device_key']
    window.snapshot = dict(frame((0, .5, .5), identity=scope), name=state['name'])
    writes, outputs = [], []
    monkeypatch.setattr(window, 'setting', lambda *args: writes.append(args))
    monkeypatch.setattr(window.actions, 'move_mouse', lambda *args: outputs.append(args))
    monkeypatch.setattr(window.actions, 'shortcut', lambda *args: outputs.append(args))
    dialog = TouchGestureDialog(window, window.current_gamepad_profile())
    try:
        dialog.timer.stop()
        dialog.preview_recognizer.reset(block_until_release=True)
        original = copy.deepcopy(window.config)
        def preview(now, *fingers):
            window.snapshot = dict(frame(*fingers, identity=scope), name=state['name'])
            return dialog.refresh_preview(now)
        assert preview(0, (0, .5, .5))['mode'] == 'cancelled'
        assert dialog.preview_contacts == 1
        assert dialog.preview_last_gesture is None
        preview(.6, (0, .5, .5))
        assert dialog.preview_last_gesture is None
        preview(.7)
        preview(.8, (0, .5, .5, 2))
        preview(.9)
        preview(1.2)
        assert dialog.preview_last_gesture == 'TP:tap'
        assert '轻触' in dialog.preview_event_label.text()
        preview(1.3, (0, .5, .5, 3))
        preview(1.4)
        preview(1.5, (0, .5, .5, 4))
        preview(1.6)
        assert dialog.preview_last_gesture == 'TP:double_tap'
        assert not writes and not outputs
        assert window.config == original
    finally:
        dialog.close()


def test_preview_scroll_and_draft_sensitivity_are_live_and_scope_guard_clears(workspace):
    from tests.test_touch_gestures import frame
    window, app = workspace
    state = touch_device()
    select(window, app, state)
    scope = state['device_key']
    dialog = TouchGestureDialog(window, window.current_gamepad_profile())
    try:
        dialog.timer.stop()
        dialog.preview_recognizer.reset(block_until_release=True)
        original = copy.deepcopy(window.config)
        def preview(now, *fingers):
            window.snapshot = dict(frame(*fingers, identity=scope), name=state['name'])
            return dialog.refresh_preview(now)
        preview(0)
        preview(.1, (0, .3, .3), (1, .7, .3))
        result = preview(.2, (0, .3, .4), (1, .7, .4))
        assert result['scroll_steps']
        assert dialog.preview_contacts == 2
        assert dialog.preview_last_gesture == 'TP:scroll_down'
        assert '双指向下滚动' in dialog.preview_event_label.text()
        assert dialog.preview_bind_btn.isEnabled()
        dialog.sensitivity_slider.setValue(100)
        assert dialog.preview_last_gesture is None
        assert preview(.3, (0, .3, .4), (1, .7, .4))['mode'] == 'cancelled'
        preview(.4)
        preview(.5, (0, .5, .5, 3))
        preview(.6, (0, .62, .5, 3))
        preview(.7)
        assert dialog.preview_last_gesture == 'TP:swipe_right'
        assert window.config == original
        window.snapshot = touch_device(2)
        assert dialog.refresh_preview(.8) is None
        assert dialog.preview_contacts == 0
        assert dialog.preview_last_gesture is None
        assert not dialog.timer.isActive()
    finally:
        dialog.close()


def test_recognized_scroll_binds_the_actual_source_and_keeps_other_drafts(workspace):
    from tests.test_touch_gestures import frame
    window, app = workspace
    state = touch_device()
    select(window, app, state)
    profile = window.virtual_kbm_page.current_scheme()
    window.setting('touch_gestures_enabled', False)
    calls = []
    def bind(token, **kwargs):
        calls.append((token, kwargs))
        window.mapping_change({'op': 'binding', 'profile': kwargs['profile'], 'trigger': token,
                               'mapping': {'short': {'action': 'shortcut', 'value': 'F13'},
                                           'long': {'action': 'none'}}})
    window.edit_mapping = bind
    dialog = TouchGestureDialog(window, profile)
    try:
        dialog.timer.stop()
        assert not dialog.preview_bind_btn.isEnabled()
        dialog.mouse_box.setChecked(True)
        dialog.sensitivity_slider.setValue(80)
        def preview(now, *fingers):
            window.snapshot = dict(frame(*fingers, identity=state['device_key']), name=state['name'])
            return dialog.refresh_preview(now)
        preview(0)
        preview(.1, (0, .3, .3), (1, .7, .3))
        preview(.2, (0, .3, .4), (1, .7, .4))
        assert dialog.preview_bind_btn.isEnabled()
        dialog.preview_bind_btn.click()
        assert calls == [('TP:scroll_down', {'profile': profile, 'mode': 'kbm'})]
        assert dialog.enabled_box.isChecked()
        assert dialog.mouse_box.isChecked() and dialog.sensitivity_slider.value() == 80
        assert dialog.gesture_rows['TP:scroll_down'].summary.toolTip() == 'F13'
        assert dialog.apply()
        assert window.store.settings_for(state)['touch_gestures_enabled']
        dialog.clear_preview()
        assert not dialog.preview_bind_btn.isEnabled()
    finally:
        dialog.close()


def test_touch_editor_can_activate_its_preset_and_device_switch_blocks_it(workspace):
    window, app = workspace
    state = touch_device()
    select(window, app, state)
    profile = window.virtual_kbm_page.current_scheme()
    original = window.config['active_profile']
    assert original != profile
    dialog = TouchGestureDialog(window, profile)
    try:
        assert not dialog.activate_btn.isHidden()
        assert window.config['active_profile'] == original
        dialog.activate_btn.click()
        assert window.config['active_profile'] == profile
        assert dialog.activate_btn.isHidden()
        select(window, app, touch_device(2))
        new_active = window.config['active_profile']
        dialog.activate_profile()
        assert window.config['active_profile'] == new_active
    finally:
        dialog.close()


def test_nikki_layout_names_gestures_and_scrolls_without_press_duration(workspace):
    from PySide6.QtWidgets import QLabel
    from gamepadstudio.virtual_kbm_ui import NikkiLayoutDialog
    window, app = workspace
    select(window, app, touch_device())
    dialog = NikkiLayoutDialog(window, window.virtual_kbm_page.current_scheme())
    try:
        for token, name in [('TP:swipe_left', '触摸左滑'), ('TP:scroll_up', '双指向上滚动')]:
            tile = dialog.trigger_cards[token]
            texts = [label.text() for label in tile.findChildren(QLabel)]
            assert any(name in text for text in texts)
            assert not any('短按' in text or '长按' in text for text in texts)
    finally:
        dialog.close()


def test_nikki_touch_editor_explains_actual_menu_outputs(workspace):
    window, app = workspace
    select(window, app, touch_device())
    profile = window.virtual_kbm_page.current_scheme()
    dialog = TouchGestureDialog(window, profile)
    try:
        assert dialog.gesture_rows['TP:swipe_left'].summary.toolTip() == '衣柜 · C'
        assert dialog.gesture_rows['TP:swipe_right'].summary.toolTip() == '背包 · B'
        assert dialog.gesture_rows['TP:swipe_up'].summary.toolTip() == '任务 · U'
        assert dialog.gesture_rows['TP:swipe_down'].summary.toolTip() == '地图 · M'
        window.config['profiles'][profile]['TP:swipe_left']['short']['value'] = 'M'
        dialog.refresh_bindings()
        assert dialog.gesture_rows['TP:swipe_left'].summary.toolTip() == '地图 · M'
    finally:
        dialog.close()


def test_closing_unshown_touch_editor_stops_the_preview(workspace):
    window, app = workspace
    select(window, app, touch_device())
    dialog = TouchGestureDialog(window, window.virtual_kbm_page.current_scheme())
    assert dialog.timer.isActive()
    dialog.preview_last_gesture = 'TP:tap'
    dialog.close()
    assert not dialog.timer.isActive()
    assert dialog.preview_last_gesture is None


@pytest.mark.parametrize('outer_dialog_open, seconds', [(True, 2), (False, 0)])
def test_closing_binding_editor_keeps_outer_touch_preview_suspended(workspace, monkeypatch,
                                                                  outer_dialog_open, seconds):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QApplication
    from gamepadstudio.mapping_ui import BindingDialog
    window, app = workspace
    select(window, app, touch_device())
    calls = []
    client = window.client
    window.client = SimpleNamespace(send=lambda *args, **kwargs: calls.append((args, kwargs)))
    window.remote = True
    monkeypatch.setattr(BindingDialog, 'exec', lambda dialog: QDialog.Rejected)
    monkeypatch.setattr(QApplication, 'activeModalWidget', lambda: window if outer_dialog_open else None)
    try:
        window.edit_mapping('TP:tap', profile=window.virtual_kbm_page.current_scheme(), mode='kbm')
        assert calls[-1] == (('suspend',), {'seconds': seconds})
    finally:
        window.remote = False
        window.client = client

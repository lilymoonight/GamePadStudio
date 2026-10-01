"""Curves are drafts until applied, and belong to one connected device."""
import copy
import os

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QDialogButtonBox

from gamepadstudio.curve_ui import CurveDialog
from gamepadstudio.response_curves import curve_preset, evaluate_curve
from gamepadstudio.studio_core import ConfigStore, profile_scope
from tests.test_device_scope_ui import device, select, workspace


def test_settings_show_only_connected_hardware_curves(workspace):
    window, app = workspace
    assert all(row.isHidden() for row in window.curve_rows.values())
    xbox = device(1)
    select(window, app, xbox)
    assert not window.curve_rows['trigger'].isHidden()
    assert not window.curve_rows['rumble'].isHidden()
    assert window.curve_rows['trigger_rumble'].isHidden()
    xbox['trigger_rumble'] = True
    select(window, app, xbox)
    assert not window.curve_rows['trigger_rumble'].isHidden()
    assert window.store.settings_for(xbox)['trigger_rumble_enabled'] is False
    switch = device(2, 'switch')
    select(window, app, switch)
    assert window.curve_rows['trigger'].isHidden()
    raw = device(3, 'generic', rumble=False, axes=[])
    raw['is_gamecontroller'] = False
    select(window, app, raw)
    assert all(row.isHidden() for row in window.curve_rows.values())
    select(window, app, None)
    assert all(row.isHidden() for row in window.curve_rows.values())


def test_open_preset_edit_and_close_have_no_persistent_side_effect(workspace):
    window, app = workspace
    state = device(1)
    select(window, app, state)
    before = copy.deepcopy(window.config)
    dialog = CurveDialog(window, 'trigger')
    try:
        assert dialog.channel_combo.itemText(0) == 'LT'
        assert dialog.channel_combo.itemText(1) == 'RT'
        dialog.set_preset('sensitive')
        dialog.point_fields[1].setValue(70)
        dialog.deadzone_field.setValue(8)
        dialog.channel_combo.setCurrentIndex(1)
        dialog.set_preset('precise')
        dialog.reject()
        assert window.config == before
    finally:
        dialog.close()


def test_apply_isolates_channels_and_same_model_devices(workspace):
    window, app = workspace
    first, second = device(1), device(2)
    select(window, app, first)
    dialog = CurveDialog(window, 'trigger')
    try:
        dialog.set_preset('sensitive')
        dialog.deadzone_field.setValue(6)
        dialog.saturation_field.setValue(91)
        assert dialog.point_labels[0].text() == '27.25% 输入'
        assert dialog.point_fields[0].accessibleName() == '27.25% 输入的输出值'
        left = copy.deepcopy(dialog.drafts['left'])
        dialog.channel_combo.setCurrentIndex(1)
        dialog.set_preset('precise')
        assert dialog.apply()
        first_curves = copy.deepcopy(window.store.settings_for(first)['trigger_curves'])
        assert first_curves['left'] == left
        assert first_curves['right'] == curve_preset('precise')
    finally:
        dialog.close()
    select(window, app, second)
    assert window.store.settings_for(second)['trigger_curves']['left'] == curve_preset('linear')
    assert window.store.settings_for(first)['trigger_curves'] == first_curves
    persisted = ConfigStore(window.store.root)
    assert persisted.settings_for(first)['trigger_curves'] == first_curves
    assert persisted.settings_for(second)['trigger_curves']['right'] == curve_preset('linear')


@pytest.mark.parametrize('change', ['switch', 'disconnect', 'reconnect', 'capability'])
def test_stale_dialog_cannot_save_or_preview(workspace, change):
    window, app = workspace
    first = device(1)
    select(window, app, first)
    dialog = CurveDialog(window, 'rumble')
    previews = []
    window.preview_response_curve = lambda *args: previews.append(args)
    try:
        dialog.set_preset('sensitive')
        original = copy.deepcopy(window.store.settings_for(first))
        replacement = device(2) if change == 'switch' else None
        if change == 'reconnect':
            replacement = dict(first, instance_id=11)
        elif change == 'capability':
            replacement = dict(first, rumble=False)
        select(window, app, replacement)
        assert dialog.apply() is False
        dialog.preview()
        dialog.save()
        assert not previews
        assert dialog.result() != QDialog.Accepted
        assert not dialog.controls.button(QDialogButtonBox.Save).isEnabled()
        assert not dialog.status.isHidden()
        assert window.store.settings_for(first) == original
    finally:
        dialog.close()


def test_numeric_drag_and_keyboard_points_remain_monotonic(workspace):
    window, app = workspace
    select(window, app, device(1))
    dialog = CurveDialog(window, 'trigger')
    try:
        dialog.show()
        app.processEvents()
        dialog.point_fields[0].setValue(90)
        assert dialog.drafts['left']['points'][1] == .5
        point = dialog.plot.point_position(2).toPoint()
        QTest.mousePress(dialog.plot, Qt.LeftButton, pos=point)
        target = QPoint(point.x(), int(dialog.plot.graph_rect().top()))
        QTest.mouseMove(dialog.plot, target)
        QTest.mouseRelease(dialog.plot, Qt.LeftButton, pos=target)
        assert dialog.drafts['left']['points'][2] == .75
        QTest.keyClick(dialog.plot, Qt.Key_Left)
        QTest.keyClick(dialog.plot, Qt.Key_Down)
        points = dialog.drafts['left']['points']
        assert points == sorted(points)
        assert points[0] == 0 and points[4] == 1
        assert points[1] == pytest.approx(.49)
    finally:
        dialog.close()


def test_live_trigger_reads_current_snapshot_and_selected_channel(workspace):
    window, app = workspace
    state = device(1, 'dualsense')
    state['axes'][4:6] = [.6, .2]
    select(window, app, state)
    dialog = CurveDialog(window, 'trigger')
    try:
        dialog.set_preset('precise')
        dialog.refresh_live()
        assert dialog.plot.raw_value == .6
        assert dialog.plot.processed_value == pytest.approx(evaluate_curve(.6, curve_preset('precise')))
        assert '60%' in dialog.live_label.text()
        assert dialog.channel_combo.itemText(0) == 'L2'
        dialog.channel_combo.setCurrentIndex(1)
        assert dialog.plot.raw_value == .2
        window.snapshot = dict(state, axes=[0., 0., 0., 0., .9, .3])
        dialog.refresh_live()
        assert dialog.plot.raw_value == .3
    finally:
        dialog.close()


def test_trigger_rumble_preview_drafts_without_opt_in_or_save(workspace):
    window, app = workspace
    state = dict(device(1), trigger_rumble=True)
    select(window, app, state)
    calls = []
    window.preview_response_curve = lambda *args: calls.append(args) or True
    original = copy.deepcopy(window.config)
    dialog = CurveDialog(window, 'trigger_rumble')
    try:
        assert dialog.travel_controls.isHidden()
        assert dialog.enabled_box is not None and not dialog.enabled_box.isChecked()
        dialog.channel_combo.setCurrentIndex(1)
        dialog.set_preset('precise')
        dialog.preview_slider.setValue(40)
        dialog.preview()
        assert calls == [('trigger_rumble', 'right', curve_preset('precise'), .4)]
        assert window.config == original
        dialog.enabled_box.setChecked(True)
        assert dialog.apply()
        assert window.store.settings_for(state)['trigger_rumble_enabled'] is True
        assert window.store.settings_for(state)['trigger_rumble_curves']['right'] == curve_preset('precise')
    finally:
        dialog.close()


def test_single_analog_trigger_does_not_invent_other_channel(workspace):
    window, app = workspace
    state = device(1, axes=[0, 1, 2, 3, 5])
    select(window, app, state)
    dialog = CurveDialog(window, 'trigger')
    try:
        assert dialog.channels == ['right']
        assert dialog.channel_combo.itemText(0) == 'RT'
        original_left = copy.deepcopy(window.store.settings_for(state)['trigger_curves']['left'])
        dialog.set_preset('sensitive')
        dialog.save()
        assert dialog.result() == QDialog.Accepted
        assert window.store.settings_for(state)['trigger_curves']['left'] == original_left
    finally:
        dialog.close()


def test_remote_preview_passes_exact_device_identity_and_reports_failure(workspace, monkeypatch):
    window, app = workspace
    state = device(1)
    select(window, app, state)
    calls = []
    def respond(root, command, **values):
        calls.append((command, values))
        return {'ok': True, 'supported': False}
    monkeypatch.setattr('gamepadstudio.studio.request', respond)
    window.remote = True
    try:
        assert window.preview_response_curve('rumble', 'low', curve_preset('sensitive')) is False
        command, values = calls[0]
        assert command == 'preview_curve'
        assert values['device_scope'] == profile_scope(state)
        assert values['instance_id'] == state['instance_id']
        assert values['curve'] == curve_preset('sensitive')
    finally:
        window.remote = False


def test_standalone_feedback_reconfiguration_and_cleanup(workspace, monkeypatch):
    window, app = workspace
    assert hasattr(window, 'haptic_engine')
    state = dict(device(1), trigger_rumble=True)
    curves = []
    window.device.set_response_curves = lambda settings: curves.append(copy.deepcopy(settings))
    select(window, app, state)
    assert window.haptic_engine.haptics_enabled
    assert not window.haptic_engine.trigger_rumble_enabled
    window.setting('trigger_rumble_enabled', True)
    assert window.haptic_engine.trigger_rumble_enabled
    assert curves[-1]['trigger_rumble_enabled']
    select(window, app, None)
    assert not window.haptic_engine.haptics_enabled
    window.cleanup()
    assert window.haptic_engine._closed


def test_mapping_page_has_supported_curve_entries_and_selected_channel(workspace):
    window, app = workspace
    state = dict(device(1, 'dualsense'), trigger_rumble=True)
    select(window, app, state)
    opened = []
    window.open_curve_editor = lambda kind, channel=None: opened.append((kind, channel))
    window.navigate(1)
    app.processEvents()
    assert all(not control.isHidden() for control in window.mapping_deck.curve_buttons.values())
    window.select_mapping_trigger('R2')
    assert window.mapping_deck.selected_trigger == 'RT'
    assert 'R2' in window.mapping_deck.curve_buttons['trigger'].text()
    window.mapping_deck.curve_buttons['trigger'].click()
    window.mapping_deck.curve_buttons['trigger_rumble'].click()
    window.mapping_deck.curve_buttons['rumble'].click()
    assert opened == [('trigger', 'right'), ('trigger_rumble', 'right'), ('rumble', None)]
    select(window, app, device(2, 'switch'))
    assert window.mapping_deck.curve_buttons['trigger'].isHidden()
    assert window.mapping_deck.curve_buttons['trigger_rumble'].isHidden()
    assert not window.mapping_deck.curve_buttons['rumble'].isHidden()
    select(window, app, None)
    assert window.mapping_deck.curve_controls.isHidden()


def test_canonical_trigger_selection_survives_refresh_and_updates_diagram(workspace):
    window, app = workspace
    state = device(1, 'dualsense')
    select(window, app, state)
    window.resize(960, 700)
    window.show()
    window.navigate(1)
    app.processEvents()
    assert {'LT', 'RT'} <= window.mapping_art.available
    assert window.mapping_boxes['LT'][1].text() == 'L2'
    assert window.mapping_boxes['RT'][1].text() == 'R2'
    assert not window.mapping_boxes['LT'][0].isHidden()
    assert window.select_mapping_trigger('R2')
    assert window.mapping_deck.selected_trigger == 'RT'
    assert window.mapping_art.selected_buttons == {'RT'}
    assert window.mapping_boxes['RT'][0].isChecked()
    app.processEvents()
    tile = window.mapping_boxes['RT'][0]
    bottom = tile.mapTo(window.mapping_inputs.viewport(), tile.rect().bottomRight())
    assert bottom.y() < window.mapping_inputs.viewport().height()
    select(window, app, dict(state))
    assert window.mapping_deck.selected_trigger == 'RT'
    assert window.mapping_art.selected_buttons == {'RT'}
    assert window.select_mapping_trigger('L1+L2')
    assert window.mapping_deck.selected_trigger == '9+LT'
    assert window.mapping_art.selected_buttons == {9, 'LT'}
    select(window, app, dict(state))
    assert window.mapping_deck.selected_trigger == '9+LT'
    assert window.mapping_art.selected_buttons == {9, 'LT'}
    window.mapping_art.input_clicked.emit('LT')
    assert window.mapping_deck.selected_trigger == 'LT'
    assert window.selected_key == 'LT'
    window.mapping_boxes['RT'][0].click()
    assert window.mapping_deck.selected_trigger == 'RT'
    assert window.mapping_art.selected_buttons == {'RT'}
    select(window, app, device(2, 'switch'))
    assert window.mapping_boxes['LT'][1].text() == 'ZL'
    assert window.mapping_boxes['RT'][1].text() == 'ZR'
    assert not window.mapping_boxes['LT'][0].isHidden()
    window.mapping_boxes['LT'][0].click()
    assert window.mapping_deck.selected_trigger == 'LT'
    assert window.mapping_deck.curve_buttons['trigger'].isHidden()
    select(window, app, device(3, 'generic', rumble=False, axes=[]))
    assert window.mapping_boxes['LT'][0].isHidden()
    assert window.mapping_boxes['RT'][0].isHidden()


def test_poll_refreshes_capabilities_without_device_identity_change(workspace, monkeypatch):
    window, app = workspace
    initial = device(1, rumble=False, axes=[0, 1, 2, 3])
    snapshots = [initial]
    window.device.read = lambda: snapshots[0]
    window.poll()
    assert window.mapping_deck.curve_controls.isHidden()
    assert all(row.isHidden() for row in window.curve_rows.values())
    previous_identity = window.device_identity
    snapshots[0] = dict(initial, available_axes=list(range(6)), rumble=True, trigger_rumble=True)
    window.poll()
    assert window.device_identity == previous_identity
    assert all(not control.isHidden() for control in window.mapping_deck.curve_buttons.values())
    assert all(not row.isHidden() for row in window.curve_rows.values())
    snapshots[0] = dict(initial)
    window.poll()
    assert window.mapping_deck.curve_controls.isHidden()
    assert all(row.isHidden() for row in window.curve_rows.values())


def test_context_curve_editor_opens_requested_right_channel(workspace, monkeypatch):
    window, app = workspace
    select(window, app, device(1))
    selected = []
    def inspect_dialog(dialog):
        selected.append(dialog.active_channel)
        dialog.reject()
        return QDialog.Rejected
    monkeypatch.setattr(CurveDialog, 'exec', inspect_dialog)
    original = copy.deepcopy(window.config)
    window.open_curve_editor('trigger', channel='right')
    assert selected == ['right']
    assert window.config == original

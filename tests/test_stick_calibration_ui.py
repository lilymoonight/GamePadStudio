"""Fresh-frame calibration UI contracts, with real Qt and isolated persistence.

Controller reads, the tray and native output are fakes; no input reaches Windows.
"""
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, Signal
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QScrollArea, QWidget

from gamepadstudio.i18n import get_language, init_language
from gamepadstudio.stick_calibration_ui import StickMeasurementDialog
from gamepadstudio.studio import STYLE
from gamepadstudio.studio_core import ConfigStore, pointer_input_signature, profile_scope
from tests.test_device_scope_ui import device
from tests.test_emergency_hotkey_ui import hotkey_app, hotkey_workspace


@pytest.fixture(autouse=True)
def readable_offscreen_fonts(hotkey_app):
    # The offscreen Windows plugin does not enumerate installed system fonts.
    # Load an existing font explicitly so geometry/screenshots use real glyphs.
    previous = hotkey_app.font()
    added = []
    if not QFontDatabase.families():
        for name in ('msyh.ttc', 'segoeui.ttf', 'consola.ttf'):
            path = Path('C:/Windows/Fonts') / name
            if path.exists():
                font_id = QFontDatabase.addApplicationFont(str(path))
                if font_id >= 0:
                    added.append(font_id)
    hotkey_app.setFont(QFont('Microsoft YaHei UI', 9))
    yield
    hotkey_app.setFont(previous)
    for font_id in added:
        QFontDatabase.removeApplicationFont(font_id)


@pytest.fixture
def measurement_owner(tmp_path, monkeypatch, hotkey_app, request):
    options = getattr(request, 'param', {})
    clock = SimpleNamespace(now=100.)
    monkeypatch.setattr('gamepadstudio.stick_calibration_ui.time.monotonic', lambda: clock.now)

    class Socket(QObject):
        disconnected = Signal()

    class Client(QObject):
        event = Signal(dict)

        def __init__(self, parent):
            super().__init__(parent)
            self.connected = True
            self.socket = Socket(self)

    class Owner(QWidget):
        device_sample = Signal(object)

        def __init__(self):
            super().__init__()
            self.snapshot = device(21)
            self.snapshot['axes'][2:4] = [.02, -.01]
            self.remote = options.get('remote', False)
            self.client = Client(self)
            self.store = ConfigStore(tmp_path)
            self.store.activate_controller(self.snapshot)
            self.config = self.store.data
            self.store.save()
            self.changes = []
            self.fail = False
            self.notice = QLabel('', self)

        def mapping_change(self, change):
            self.changes.append(copy.deepcopy(change))
            if self.fail:
                self.notice.setText('保存失败：磁盘不可写')
                return False
            self.store.apply_mapping_change(change, self.snapshot)
            return True

    owner = Owner()
    dialogs = []

    def open_dialog():
        dialog = StickMeasurementDialog(owner)
        dialog.timer.stop()
        dialogs.append(dialog)
        return dialog

    yield owner, hotkey_app, clock, open_dialog
    for dialog in dialogs:
        dialog.close()
    owner.close()
    owner.deleteLater()
    # Flush only this fixture's owner. A process-wide DeferredDelete flush can
    # tear down unrelated Qt test objects still owned by other fixtures.
    hotkey_app.sendPostedEvents(owner, QEvent.DeferredDelete)


def emit_frame(owner, clock, *, step=.033, state=None):
    clock.now += step
    frame = copy.deepcopy(owner.snapshot if state is None else state)
    if owner.remote:
        owner.client.event.emit({'type': 'state', 'device': frame})
    else:
        owner.device_sample.emit(frame)


def complete(owner, clock, dialog):
    dialog.start()
    for _ in range(130):
        emit_frame(owner, clock)
        if dialog.calibration.phase in ('complete', 'failed'):
            break
    assert dialog.calibration.phase == 'complete', dialog.calibration.status()
    assert dialog.calibration.status()['samples'] >= 60


def test_opening_does_not_measure_modify_or_save(measurement_owner):
    owner, _, _, open_dialog = measurement_owner
    before, file_before = copy.deepcopy(owner.config), owner.store.path.read_bytes()
    dialog = open_dialog()
    assert dialog.calibration.phase == 'idle'
    assert not dialog.apply_button.isEnabled()
    assert dialog.profile_combo.currentData() in owner.store.profiles_for(owner.snapshot, 'kbm')
    assert owner.changes == [] and owner.config == before
    assert owner.store.path.read_bytes() == file_before


@pytest.mark.parametrize('after_measurement', [False, True])
def test_cancel_discards_even_completed_recommendation(measurement_owner, after_measurement):
    owner, _, clock, open_dialog = measurement_owner
    before, file_before = copy.deepcopy(owner.config), owner.store.path.read_bytes()
    dialog = open_dialog()
    if after_measurement:
        complete(owner, clock, dialog)
        assert dialog.apply_button.isEnabled()
    dialog.buttons.rejected.emit()
    assert dialog.result() == QDialog.Rejected
    assert dialog.disconnected and not dialog.timer.isActive()
    assert owner.config == before and owner.store.path.read_bytes() == file_before
    assert owner.changes == []


@pytest.mark.parametrize('measurement_owner', [{}, {'remote': True}], indirect=True)
def test_fresh_unchanged_values_are_valid_new_samples(measurement_owner):
    owner, _, clock, open_dialog = measurement_owner
    dialog = open_dialog()
    complete(owner, clock, dialog)
    assert dialog.progress_bar.value() == 100
    assert dialog.calibration.result['recommended_deadzone'] == .05
    assert dialog.apply_button.isEnabled() and dialog.measure_button.isEnabled()
    assert owner.changes == []
    assert '5%' in dialog.result_label.text()


def test_timer_never_makes_samples_from_cached_snapshot(measurement_owner):
    owner, _, clock, open_dialog = measurement_owner
    dialog = open_dialog()
    dialog.start()
    for _ in range(4):
        clock.now += .04
        dialog.tick()
    assert dialog.calibration.status()['samples'] == 0
    assert dialog.calibration.phase == 'failed'
    assert not dialog.apply_button.isEnabled() and owner.changes == []
    assert '新数据' in dialog.message.text()


@pytest.mark.parametrize('measurement_owner', [{'remote': True}], indirect=True)
def test_remote_poll_and_nonstate_events_cannot_sample(measurement_owner):
    owner, _, clock, open_dialog = measurement_owner
    dialog = open_dialog()
    dialog.start()
    for _ in range(3):
        clock.now += .04
        owner.device_sample.emit(owner.snapshot)
        owner.client.event.emit({'type': 'status', 'device': owner.snapshot})
        owner.client.event.emit({'type': 'battery', 'device': owner.snapshot})
        dialog.tick()
    assert dialog.calibration.phase == 'settling'
    assert dialog.calibration.status()['samples'] == 0
    clock.now += .04
    dialog.tick()
    assert dialog.calibration.phase == 'failed'
    assert not dialog.apply_button.isEnabled()


@pytest.mark.parametrize('measurement_owner', [{}, {'remote': True}], indirect=True)
@pytest.mark.parametrize('change', ['disconnect', 'instance', 'physical', 'axes', 'buttons', 'family'])
def test_device_or_capability_change_permanently_invalidates(measurement_owner, change):
    owner, _, clock, open_dialog = measurement_owner
    original = copy.deepcopy(owner.snapshot)
    dialog = open_dialog()
    complete(owner, clock, dialog)
    changed = copy.deepcopy(original)
    if change == 'disconnect':
        changed = None
    elif change == 'instance':
        changed['instance_id'] += 1
    elif change == 'physical':
        changed['device_key'] = 'fixture:other'
    elif change == 'axes':
        changed['available_axes'] = [0, 1, 4, 5]
    elif change == 'buttons':
        changed['available_buttons'] = list(range(14))
    else:
        changed['family'] = 'dualsense'
    owner.snapshot = changed
    if owner.remote:
        owner.client.event.emit({'type': 'state', 'device': changed})
    else:
        owner.device_sample.emit(changed)
    assert dialog.invalidated and not dialog.apply_button.isEnabled()
    owner.snapshot = original
    emit_frame(owner, clock)
    dialog.start()
    dialog.save()
    assert dialog.invalidated and not dialog.measure_button.isEnabled()
    assert owner.changes == []


@pytest.mark.parametrize('measurement_owner', [{'remote': True}], indirect=True)
def test_remote_disconnect_invalidates_even_with_cached_valid_device(measurement_owner):
    owner, _, clock, open_dialog = measurement_owner
    dialog = open_dialog()
    complete(owner, clock, dialog)
    owner.client.connected = False
    dialog.tick()
    assert dialog.invalidated and not dialog.apply_button.isEnabled()
    owner.client.connected = True
    emit_frame(owner, clock)
    dialog.save()
    assert dialog.invalidated and owner.changes == []


@pytest.mark.parametrize('measurement_owner', [{'remote': True}], indirect=True)
def test_brief_socket_disconnect_cannot_revive_result_before_tick(measurement_owner):
    owner, _, clock, open_dialog = measurement_owner
    dialog = open_dialog()
    complete(owner, clock, dialog)
    owner.client.connected = False
    owner.client.socket.disconnected.emit()
    owner.client.connected = True
    emit_frame(owner, clock)
    assert dialog.invalidated and not dialog.apply_button.isEnabled()
    assert not dialog.measure_button.isEnabled()
    dialog.save()
    assert owner.changes == []


@pytest.mark.parametrize('measurement_owner', [{'remote': True}], indirect=True)
def test_closed_dialog_is_unbound_from_socket_disconnect(measurement_owner):
    owner, _, clock, open_dialog = measurement_owner
    dialog = open_dialog()
    dialog.show()
    complete(owner, clock, dialog)
    before = dialog.calibration.status()
    dialog.close()
    owner.client.socket.disconnected.emit()
    emit_frame(owner, clock)
    assert not dialog.invalidated and dialog.calibration.status() == before


def test_deleted_target_rejects_apply_without_selecting_another(measurement_owner):
    owner, _, clock, open_dialog = measurement_owner
    dialog = open_dialog()
    complete(owner, clock, dialog)
    target = dialog.profile_combo.currentData()
    owner.config['profiles'].pop(target)
    dialog.tick()
    dialog.save()
    assert not dialog.apply_button.isEnabled() and owner.changes == []


def test_apply_changes_only_selected_kbm_deadzone_without_activation(measurement_owner):
    owner, _, clock, open_dialog = measurement_owner
    source = owner.store.profiles_for(owner.snapshot, 'kbm')[0]
    owner.store.apply_mapping_change({'op': 'create', 'profile': '另一套键鼠', 'source': source}, owner.snapshot)
    active = owner.store.profiles_for(owner.snapshot, 'gamepad')[0]
    owner.store.remember_profile(owner.snapshot, active)
    owner.config['profile_options']['另一套键鼠']['mouse'].update(
        mode='game', sensitivity=31, invert_y=True, smoothing=.08, deadzone=.09)
    owner.config['profile_options']['另一套键鼠']['input']['axis_press'] = .7
    owner.store.save()
    before = copy.deepcopy(owner.config)
    dialog = open_dialog()
    assert set(dialog.profile_combo.itemData(i) for i in range(dialog.profile_combo.count())) == {
        source, '另一套键鼠'}
    assert dialog.profile_combo.findData(active) == -1
    dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData('另一套键鼠'))
    complete(owner, clock, dialog)
    recommended = dialog.calibration.result['recommended_deadzone']
    dialog.save()
    expected = copy.deepcopy(before)
    expected['profile_options']['另一套键鼠']['mouse']['deadzone'] = recommended
    expected['mapping_revision'] += 1
    assert owner.config == expected
    assert owner.config['active_profile'] == active
    assert owner.config['controller_profiles'][profile_scope(owner.snapshot)] == active
    assert dialog.result() == QDialog.Accepted and dialog.disconnected
    restored = ConfigStore(owner.store.root).data
    for key in ('profiles', 'profile_options', 'controller_profiles', 'active_profile',
                'device_settings', 'mapping_revision'):
        assert restored[key] == owner.config[key]
    assert owner.changes == [{'op': 'pointer_deadzone', 'profile': '另一套键鼠',
                              'deadzone': recommended,
                              'expected_input_signature': pointer_input_signature(owner.snapshot)}]


def test_failed_apply_keeps_draft_open_and_allows_retry(measurement_owner):
    owner, _, clock, open_dialog = measurement_owner
    dialog = open_dialog()
    dialog.show()
    complete(owner, clock, dialog)
    before = copy.deepcopy(owner.config)
    owner.fail = True
    dialog.save()
    assert dialog.isVisible() and dialog.result() == QDialog.Rejected
    assert not dialog.disconnected and dialog.apply_button.isEnabled()
    assert owner.config == before and '磁盘不可写' in dialog.message.text()
    owner.fail = False
    dialog.save()
    assert dialog.result() == QDialog.Accepted and dialog.disconnected


@pytest.mark.parametrize('reason', ['offset', 'noise', 'motion'])
def test_unreliable_measurements_explain_why_apply_is_unavailable(measurement_owner, reason):
    owner, _, clock, open_dialog = measurement_owner
    dialog = open_dialog()
    if reason == 'offset':
        owner.snapshot['axes'][2:4] = [.18, 0.]
    elif reason == 'motion':
        owner.snapshot['axes'][2:4] = [.5, 0.]
    dialog.start()
    for index in range(130):
        if reason == 'noise':
            owner.snapshot['axes'][2:4] = [.02 if index % 2 else -.02, 0.]
        emit_frame(owner, clock)
        if dialog.calibration.phase in ('complete', 'failed'):
            break
    assert not dialog.apply_button.isEnabled()
    assert dialog.measure_button.isEnabled()
    assert dialog.message.text() or dialog.result_label.text()
    if reason == 'motion':
        assert dialog.calibration.phase == 'failed' and '移动' in dialog.message.text()
    else:
        assert dialog.calibration.phase == 'complete'
        assert dialog.calibration.result['recommended_deadzone'] is None
        assert dialog.result_label.text() == dialog.calibration.error
        assert '不建议' in dialog.result_label.text()
    dialog.save()
    assert owner.changes == []


def test_remeasure_clears_old_recommendation_and_waits_for_new_frames(measurement_owner):
    owner, _, clock, open_dialog = measurement_owner
    dialog = open_dialog()
    complete(owner, clock, dialog)
    dialog.start()
    assert dialog.calibration.phase == 'settling'
    assert dialog.calibration.result is None and dialog.result_label.text() == ''
    assert not dialog.apply_button.isEnabled() and not dialog.measure_button.isEnabled()
    dialog.save()
    assert owner.changes == []


@pytest.mark.parametrize('measurement_owner', [{}, {'remote': True}], indirect=True)
def test_close_disconnects_samples_and_timer_idempotently(measurement_owner):
    owner, _, clock, open_dialog = measurement_owner
    dialog = open_dialog()
    dialog.show()
    dialog.start()
    emit_frame(owner, clock)
    before = dialog.calibration.status()
    dialog.close()
    dialog.disconnect_samples()
    assert dialog.disconnected and not dialog.timer.isActive()
    emit_frame(owner, clock)
    assert dialog.calibration.status() == before


def select_state(window, app, fake_device, state):
    fake_device.state = state
    window.snapshot = state
    window.update_controller_ui(state)
    window.tester.update_state(state)
    app.processEvents()


@pytest.mark.parametrize('unsupported', [None, [0, 1], [0, 1, 2]])
def test_measurement_entry_requires_actual_two_rs_axes(hotkey_workspace, unsupported, monkeypatch):
    window, app, fake_device, notices = hotkey_workspace
    state = None if unsupported is None else device(21, axes=unsupported)
    select_state(window, app, fake_device, state)
    assert not window.tester.measure_stick_button.isEnabled()
    monkeypatch.setattr(StickMeasurementDialog, 'exec', lambda _: pytest.fail('Unsupported dialog opened'))
    window.open_stick_measurement()
    assert '未提供' in notices[-1]


def test_standalone_device_read_signal_is_the_measurement_source(hotkey_workspace, monkeypatch):
    window, app, fake_device, _ = hotkey_workspace
    state = device(21)
    state['axes'][2:4] = [.02, -.01]
    select_state(window, app, fake_device, state)
    clock = SimpleNamespace(now=100.)
    monkeypatch.setattr('gamepadstudio.stick_calibration_ui.time.monotonic', lambda: clock.now)
    window.enabled = False
    dialog = StickMeasurementDialog(window)
    dialog.timer.stop()
    try:
        dialog.start()
        for _ in range(120):
            clock.now += .033
            window.poll()
            if dialog.calibration.phase == 'complete':
                break
        assert dialog.calibration.phase == 'complete'
        assert window.actions.calls == []
        assert dialog.apply_button.isEnabled()
    finally:
        dialog.close()
        dialog.deleteLater()
        app.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.mark.parametrize('hotkey_workspace', [{'remote': True}], indirect=True)
def test_remote_mapping_request_keeps_identity_and_stale_confirmation_open(hotkey_workspace, monkeypatch):
    window, app, fake_device, _ = hotkey_workspace
    state = device(21)
    select_state(window, app, fake_device, state)
    clock = SimpleNamespace(now=100.)
    monkeypatch.setattr('gamepadstudio.stick_calibration_ui.time.monotonic', lambda: clock.now)
    dialog = StickMeasurementDialog(window)
    dialog.timer.stop()
    captured = []

    def reject_request(root, command, **kwargs):
        captured.append((command, copy.deepcopy(kwargs)))
        return {'ok': False, 'error': '手柄连接已变化，请重新测量'}

    monkeypatch.setattr('gamepadstudio.studio.request', reject_request)
    try:
        dialog.show()
        complete(window, clock, dialog)
        before = copy.deepcopy(window.config)
        dialog.save()
        assert captured[-1][0] == 'mapping_change'
        assert captured[-1][1]['device_scope'] == profile_scope(state)
        assert captured[-1][1]['instance_id'] == state['instance_id']
        assert captured[-1][1]['change']['expected_input_signature'] == pointer_input_signature(state)
        assert dialog.isVisible() and not dialog.disconnected
        assert window.config == before and '连接已变化' in dialog.message.text()
    finally:
        dialog.close()
        dialog.deleteLater()
        app.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.mark.parametrize('hotkey_workspace', [{'remote': True}], indirect=True)
def test_offline_backend_cannot_open_measurement(hotkey_workspace, monkeypatch):
    window, app, fake_device, notices = hotkey_workspace
    select_state(window, app, fake_device, device(21))
    window.client.connected = False
    monkeypatch.setattr(StickMeasurementDialog, 'exec', lambda _: pytest.fail('Offline dialog opened'))
    window.open_stick_measurement()
    assert '后台未连接' in notices[-1]


@pytest.mark.parametrize('hotkey_workspace', [{}, {'remote': True}], indirect=True)
def test_opening_entry_releases_outputs_and_remote_lease_before_dialog(hotkey_workspace, monkeypatch):
    window, app, fake_device, _ = hotkey_workspace
    select_state(window, app, fake_device, device(21))
    window.actions.hold('E', True)
    window.actions.mouse_button('left', True)
    opened = []

    def inspect_dialog(dialog):
        opened.append(dialog)
        assert not window.actions.keys and not window.actions.mouse
        assert dialog.owner is window
        assert dialog.calibration.phase == 'idle'
        if window.remote:
            assert window.client.sent[-1] == ('suspend', {'seconds': 2})
        dialog.reject()
        return QDialog.Rejected

    monkeypatch.setattr(StickMeasurementDialog, 'exec', inspect_dialog)
    window.open_stick_measurement()
    assert len(opened) == 1 and opened[0].disconnected
    if window.remote:
        assert window.client.sent[-1] == ('suspend', {'seconds': 0})
    app.sendPostedEvents(None, QEvent.DeferredDelete)


def screenshot(widget, name):
    root = Path(__file__).resolve().parents[1] / 'artifacts' / 'stick-calibration-ui'
    root.mkdir(parents=True, exist_ok=True)
    assert widget.grab().save(str(root / name))


@pytest.mark.parametrize('lang,width', [('zh', 490), ('en', 350)])
def test_dialog_localization_small_width_keeps_actions_reachable(measurement_owner, lang, width):
    owner, app, clock, open_dialog = measurement_owner
    init_language(lang)
    owner.setStyleSheet(STYLE)
    dialog = open_dialog()
    complete(owner, clock, dialog)
    dialog.resize(width, 620 if lang == 'en' else 540)
    dialog.show()
    app.processEvents()
    assert dialog.width() == width
    for widget in (dialog.profile_combo, dialog.apply_button,
                   dialog.buttons.button(QDialogButtonBox.Close), dialog.measure_button):
        rect = widget.rect()
        assert dialog.rect().contains(widget.mapTo(dialog, rect.topLeft()))
        assert dialog.rect().contains(widget.mapTo(dialog, rect.bottomRight()))
    assert dialog.apply_button.isEnabled()
    assert ('建议' in dialog.result_label.text()) if lang == 'zh' else ('Suggested' in dialog.result_label.text())
    screenshot(dialog, f'dialog-{lang}-{width}.png')


@pytest.mark.parametrize('hotkey_workspace', ['zh', 'en'], indirect=True)
@pytest.mark.parametrize('width', [950, 1440])
def test_telemetry_entry_fits_narrow_and_full_workspace(hotkey_workspace, width):
    window, app, fake_device, _ = hotkey_workspace
    window.setStyleSheet(STYLE)
    window.resize(width, 850)
    state = device(21)
    state['name'] = 'Xbox Wireless Controller · Fixture'
    select_state(window, app, fake_device, state)
    window.poll()
    window.navigate(3)
    window.show()
    app.processEvents()
    page = window.stack.currentWidget()
    assert isinstance(page, QScrollArea)
    viewport = page.viewport()
    button = window.tester.measure_stick_button
    assert button.isVisible() and button.isEnabled()
    assert viewport.rect().contains(button.mapTo(viewport, QPoint(0, 0)))
    assert viewport.rect().contains(button.mapTo(viewport, button.rect().bottomRight()))
    assert page.horizontalScrollBar().maximum() == 0
    screenshot(window, f'telemetry-{get_language()}-{width}.png')

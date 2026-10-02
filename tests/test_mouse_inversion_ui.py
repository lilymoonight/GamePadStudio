"""Camera direction is a draft preference of one device-owned KBM preset."""
import copy

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox

from gamepadstudio.i18n import init_language
from gamepadstudio.mapping_engine import MappingRuntime
from gamepadstudio.studio_core import ConfigStore, profile_scope
from gamepadstudio.virtual_kbm import VirtualMouseThread
from gamepadstudio.virtual_kbm_ui import KbmFeelDialog
from tests.test_application_profiles_integration import remote_workspace, sync_remote
from tests.test_device_scope_ui import device, select, workspace
from tests.test_unified_mapping import Actions


@pytest.fixture
def feel_workspace(workspace):
    window, app = workspace
    window.engine.close()
    window.actions = Actions()
    window.engine = MappingRuntime(window.actions, window.dispatch, start_mouse=False)
    select(window, app, device(101))
    window.store.save()
    yield window, app
    for dialog in window.findChildren(KbmFeelDialog):
        dialog.device_timer.stop()
        dialog.close()


def close_dialog(dialog):
    dialog.device_timer.stop()
    dialog.close()


def choose_inverted(dialog, inverted=True):
    dialog.invert_y.setCurrentIndex(dialog.invert_y.findData(inverted))


def test_missing_preference_defaults_to_normal_without_writing(feel_workspace):
    window, _ = feel_workspace
    profile = window.virtual_kbm_page.current_scheme()
    window.config['profile_options'][profile]['mouse'].pop('invert_y', None)
    before = copy.deepcopy(window.config)
    before_file = window.store.path.read_bytes()
    dialog = KbmFeelDialog(window, profile)
    try:
        assert dialog.has_pointer
        assert dialog.invert_y.currentData() is False
        assert dialog.options()['mouse']['invert_y'] is False
        dialog.reject()
        assert window.config == before
        assert window.store.path.read_bytes() == before_file
    finally:
        close_dialog(dialog)


def test_actual_editor_save_persists_boolean_and_reopens_inverted(feel_workspace, monkeypatch):
    window, _ = feel_workspace
    profile = window.virtual_kbm_page.current_scheme()
    original_mouse = copy.deepcopy(window.config['profile_options'][profile]['mouse'])

    def confirm(dialog):
        choose_inverted(dialog)
        dialog.validate()
        assert dialog.result() == QDialog.Accepted
        return dialog.result()

    monkeypatch.setattr(KbmFeelDialog, 'exec', confirm)
    window.virtual_kbm_page.mouse_settings()
    stored = window.config['profile_options'][profile]['mouse']
    assert stored['invert_y'] is True
    for key, value in original_mouse.items():
        if key == 'invert_y':
            continue
        if isinstance(value, (int, float)):
            assert stored[key] == pytest.approx(value)
        else:
            assert stored[key] == value
    assert ConfigStore(window.store.root).data['profile_options'][profile]['mouse']['invert_y'] is True
    reopened = KbmFeelDialog(window, profile)
    try:
        assert reopened.invert_y.currentData() is True
    finally:
        close_dialog(reopened)
    assert not window.actions.calls


def test_cancel_actual_editor_does_not_save_direction_or_mode(feel_workspace, monkeypatch):
    window, _ = feel_workspace
    before = copy.deepcopy(window.config)
    before_file = window.store.path.read_bytes()

    def cancel(dialog):
        choose_inverted(dialog)
        dialog.mode.setCurrentIndex(dialog.mode.findData('desktop'))
        dialog.reject()
        return dialog.result()

    monkeypatch.setattr(KbmFeelDialog, 'exec', cancel)
    window.virtual_kbm_page.mouse_settings()
    assert window.config == before
    assert window.store.path.read_bytes() == before_file


def test_desktop_disables_direction_but_retains_preference_when_returning_to_game(feel_workspace):
    window, _ = feel_workspace
    profile = window.virtual_kbm_page.current_scheme()
    dialog = KbmFeelDialog(window, profile)
    try:
        choose_inverted(dialog)
        assert dialog.invert_y.isEnabled()
        dialog.mode.setCurrentIndex(dialog.mode.findData('desktop'))
        assert not dialog.invert_y.isEnabled()
        assert dialog.invert_y.toolTip()
        assert dialog.options()['mouse']['mode'] == 'desktop'
        assert dialog.options()['mouse']['invert_y'] is True
        dialog.validate()
        assert window.mapping_change({'op': 'options', 'profile': profile, 'options': dialog.options()})
    finally:
        close_dialog(dialog)
    reopened = KbmFeelDialog(window, profile)
    try:
        assert not reopened.invert_y.isEnabled()
        assert reopened.invert_y.currentData() is True
        reopened.mode.setCurrentIndex(reopened.mode.findData('game'))
        assert reopened.invert_y.isEnabled()
        assert reopened.invert_y.currentData() is True
    finally:
        close_dialog(reopened)


def test_reset_default_returns_direction_to_normal(feel_workspace, monkeypatch):
    window, _ = feel_workspace
    profile = window.virtual_kbm_page.current_scheme()
    assert window.mapping_change({'op': 'options', 'profile': profile, 'options': {'mouse': {'invert_y': True}}})
    window.navigate(6)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.Yes)
    window.reset_profile()
    dialog = KbmFeelDialog(window, profile)
    try:
        assert dialog.invert_y.currentData() is False
    finally:
        close_dialog(dialog)


def test_device_and_preset_directions_are_independent(feel_workspace):
    window, app = feel_workspace
    first = copy.deepcopy(window.snapshot)
    first_profile = window.virtual_kbm_page.current_scheme()
    assert window.mapping_change({'op': 'options', 'profile': first_profile, 'options': {'mouse': {'invert_y': True}}})
    assert window.mapping_change({'op': 'create', 'profile': 'Normal camera', 'mode': 'kbm', 'source': first_profile})
    assert window.mapping_change({'op': 'options', 'profile': 'Normal camera', 'options': {'mouse': {'invert_y': False}}})
    select(window, app, device(202))
    second_profile = window.virtual_kbm_page.current_scheme()
    second = KbmFeelDialog(window, second_profile)
    try:
        assert second.invert_y.currentData() is False
        assert profile_scope(window.snapshot) != window.config['profile_devices'][first_profile]
    finally:
        close_dialog(second)
    select(window, app, first)
    for profile, expected in ((first_profile, True), ('Normal camera', False)):
        dialog = KbmFeelDialog(window, profile)
        try:
            assert dialog.invert_y.currentData() is expected
        finally:
            close_dialog(dialog)
    persisted = ConfigStore(window.store.root)
    assert persisted.data['profile_options'][first_profile]['mouse']['invert_y'] is True
    assert persisted.data['profile_options']['Normal camera']['mouse']['invert_y'] is False


@pytest.mark.parametrize('replacement', ['disconnect', 'other_device', 'new_instance'])
def test_changed_device_prevents_direction_draft_confirmation(feel_workspace, replacement):
    window, _ = feel_workspace
    profile = window.virtual_kbm_page.current_scheme()
    before = copy.deepcopy(window.config)
    dialog = KbmFeelDialog(window, profile)
    try:
        choose_inverted(dialog)
        if replacement == 'disconnect':
            window.snapshot = None
        elif replacement == 'other_device':
            window.snapshot = device(202)
        else:
            old_scope = profile_scope(window.snapshot)
            window.snapshot['instance_id'] += 1
            assert profile_scope(window.snapshot) == old_scope
        dialog.validate()
        assert dialog.result() != QDialog.Accepted
        assert not dialog.controls.button(QDialogButtonBox.Save).isEnabled()
        assert window.config == before
    finally:
        close_dialog(dialog)


def test_without_rs_direction_is_hidden_and_existing_preference_is_preserved(feel_workspace):
    window, app = feel_workspace
    profile = window.virtual_kbm_page.current_scheme()
    window.config['profile_options'][profile]['mouse']['invert_y'] = True
    reduced = copy.deepcopy(window.snapshot)
    reduced['available_axes'] = [0, 1, 4, 5]
    select(window, app, reduced)
    dialog = KbmFeelDialog(window, profile)
    try:
        dialog.show()
        app.processEvents()
        assert not dialog.has_pointer
        assert not dialog.invert_y.isVisible()
        assert dialog.options()['mouse']['invert_y'] is True
    finally:
        close_dialog(dialog)


@pytest.mark.parametrize('language', ['zh', 'en'])
def test_narrow_editor_direction_and_save_are_reachable(feel_workspace, language):
    window, app = feel_workspace
    init_language(language)
    dialog = KbmFeelDialog(window, window.virtual_kbm_page.current_scheme())
    try:
        dialog.resize(440, 500)
        dialog.show()
        app.processEvents()
        assert dialog.width() == 440
        assert dialog.area.horizontalScrollBar().maximum() == 0
        dialog.area.ensureWidgetVisible(dialog.invert_y)
        app.processEvents()
        field = dialog.invert_y
        assert field.isVisible() and field.isEnabled()
        assert dialog.area.viewport().rect().contains(field.mapTo(dialog.area.viewport(), QPoint(0, 0)))
        assert dialog.area.viewport().rect().contains(field.mapTo(dialog.area.viewport(), field.rect().bottomRight()))
        choose_inverted(dialog)
        assert field.currentData() is True
        save = dialog.controls.button(QDialogButtonBox.Save)
        assert save.isVisible() and save.isEnabled()
        assert dialog.rect().contains(save.mapTo(dialog, save.rect().bottomRight()))
    finally:
        close_dialog(dialog)


def test_remote_application_switch_passes_direction_to_real_runtime_mouse_thread(remote_workspace):
    window, agent, app, state, foreground, _, nikki, actions, _ = remote_workspace
    assert window.mapping_change({'op': 'options', 'profile': nikki,
                                 'options': {'mouse': {'mode': 'game', 'invert_y': True}}})
    assert window.mapping_change({'op': 'create', 'profile': 'Other game camera', 'mode': 'kbm', 'source': nikki})
    assert window.mapping_change({'op': 'options', 'profile': 'Other game camera',
                                 'options': {'mouse': {'mode': 'game', 'invert_y': False}}})
    assert window.mapping_change({'op': 'application_profiles', 'settings': {'enabled': True, 'rules': [
        {'executable': foreground['executable'], 'profile': nikki},
        {'executable': r'C:\Games\OtherGame.exe', 'profile': 'Other game camera'}]}})
    agent.engine.close()
    agent.engine = MappingRuntime(actions, agent.dispatch, start_mouse=False)
    thread = VirtualMouseThread(actions)
    agent.engine.mouse_thread = thread  # Real configure/update logic; no background thread or OS movement.
    try:
        foreground.update(hwnd=91, pid=123456)
        agent.update_application_profile(force=True)
        agent.engine.update(state, agent.config, enabled=False, now=1)
        sync_remote(window, agent, app)
        assert agent.config['active_profile'] == nikki
        assert thread.invert_y is True
        assert window.virtual_kbm_page.scheme_active_badge.text() == '应用自动生效'
        foreground.update(hwnd=92, pid=123457, executable=r'C:\Games\OtherGame.exe')
        agent.update_application_profile(force=True)
        agent.engine.update(state, agent.config, enabled=False, now=2)
        sync_remote(window, agent, app)
        assert agent.config['active_profile'] == 'Other game camera'
        assert thread.invert_y is False
        assert window.virtual_kbm_page.current_scheme() == 'Other game camera'
        assert window.virtual_kbm_page.scheme_active_badge.text() == '应用自动生效'
        assert not actions.calls
    finally:
        agent.engine.mouse_thread = None  # An unstarted threading.Thread cannot be joined by close().
        thread.stop()

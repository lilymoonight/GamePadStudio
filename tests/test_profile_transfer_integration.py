"""Portable files cross the real Studio/Agent contract without OS input."""
import copy

import pytest
from PySide6.QtWidgets import QDialog, QFileDialog

from gamepadstudio.mapping_engine import MappingRuntime
from gamepadstudio.profile_transfer import export_profile, load_profile_file
from gamepadstudio.profile_transfer_ui import ProfileImportDialog
from gamepadstudio.studio_core import ConfigStore, profile_scope
from tests.test_application_profiles_integration import remote_workspace, sync_remote
from tests.test_device_scope_ui import device, select, workspace
from tests.test_unified_mapping import Actions, entry


def package_for(window, state, name):
    package = export_profile(window.config, state, name)
    package['profile']['mappings'] = {
        '0': entry('hold', 'F'), '0+9': entry('shortcut', 'Ctrl+S'), 'LT': entry('hold', 'Q')}
    return package


def test_remote_import_preserves_automatic_active_manual_baseline_and_rules(remote_workspace):
    window, agent, app, state, _, native, nikki, _, ui_actions = remote_workspace
    before = copy.deepcopy(agent.config)
    enabled = agent.enabled
    package = package_for(window, state, nikki)
    dialog = ProfileImportDialog(window, package)
    try:
        dialog.save()
        assert dialog.result() == QDialog.Accepted
        imported = window.last_imported_profile
        assert imported == agent.store.last_imported_profile
        assert set(agent.config['profiles']) - set(before['profiles']) == {imported}
        assert agent.config['profile_devices'][imported] == profile_scope(state)
        assert agent.config['active_profile'] == nikki
        assert agent.config['controller_profiles'][profile_scope(state)] == native
        assert agent.config['application_profiles'] == before['application_profiles']
        assert agent.application_profile['automatic']
        assert agent.enabled == enabled
        for capabilities in ({}, {'rumble': False}, {'available_axes': [0, 1]}):
            state.update(capabilities)
            window.update_controller_ui(state)
            sync_remote(window, agent, app)
            assert window.config['active_profile'] == window.profile_combo.currentText() == nikki
            assert window.virtual_kbm_page.current_scheme() == nikki
            assert window.virtual_kbm_page.scheme_active_badge.text() == '应用自动生效'
        assert window.config['profiles'][imported] == agent.config['profiles'][imported]
        persisted = ConfigStore(agent.root)
        assert persisted.data['active_profile'] == nikki
        assert persisted.data['controller_profiles'][profile_scope(state)] == native
        assert persisted.data['profiles'][imported] == agent.config['profiles'][imported]
        assert not ui_actions.calls
    finally:
        dialog.reject()


def test_remote_cancel_never_contacts_backend_or_changes_saved_profile(remote_workspace):
    window, agent, _, state, _, _, nikki, _, _ = remote_workspace
    before = copy.deepcopy(agent.config)
    ui_before = copy.deepcopy(window.config)
    before_file = agent.store.path.read_bytes()
    dialog = ProfileImportDialog(window, package_for(window, state, nikki))
    dialog.name_edit.setText('Cancelled remote draft')
    dialog.reject()
    assert agent.config == before
    assert window.config == ui_before
    assert agent.store.path.read_bytes() == before_file
    assert not hasattr(window, 'last_imported_profile')
    assert agent.application_profile['automatic']


def test_remote_authoritative_rejection_keeps_draft_and_does_not_fake_success(remote_workspace, monkeypatch):
    window, agent, _, state, _, _, nikki, _, _ = remote_workspace
    before = copy.deepcopy(agent.config)
    ui_before = copy.deepcopy(window.config)
    notifications = []
    monkeypatch.setattr(window, 'notify', notifications.append)
    monkeypatch.setattr('gamepadstudio.studio.request',
                        lambda *args, **kwargs: {'ok': False, 'error': '后台未连接，修改尚未保存'})
    dialog = ProfileImportDialog(window, package_for(window, state, nikki))
    try:
        dialog.name_edit.setText('Keep my preview')
        dialog.save()
        assert dialog.result() != QDialog.Accepted
        assert dialog.name_edit.text() == 'Keep my preview'
        assert agent.config == before
        assert window.config == ui_before
        assert not hasattr(window, 'last_imported_profile')
        assert notifications == ['后台未连接，修改尚未保存']
    finally:
        dialog.reject()


@pytest.mark.parametrize('backend_change', ['new_instance', 'other_scope', 'capabilities'])
def test_remote_revalidates_device_when_ui_snapshot_has_not_received_change(remote_workspace, backend_change):
    window, agent, _, state, _, _, nikki, _, _ = remote_workspace
    before = copy.deepcopy(agent.config)
    ui_before = copy.deepcopy(window.config)
    before_file = agent.store.path.read_bytes()
    dialog = ProfileImportDialog(window, package_for(window, state, nikki))
    try:
        agent.state = copy.deepcopy(state)
        if backend_change == 'new_instance':
            agent.state['instance_id'] += 1
        elif backend_change == 'other_scope':
            agent.state = device(999)
        else:
            agent.state['available_axes'] = [0, 1]
        assert dialog.check_device()  # The UI has not yet learned about the race.
        dialog.save()
        assert dialog.result() != QDialog.Accepted
        assert agent.config == before
        assert window.config == ui_before
        assert agent.store.path.read_bytes() == before_file
        assert agent.application_profile['automatic']
    finally:
        dialog.reject()


def test_file_export_then_ui_import_preserves_payload_without_system_settings(remote_workspace, tmp_path, monkeypatch):
    window, agent, app, state, _, native, nikki, _, _ = remote_workspace
    target = tmp_path / 'my-backup.gpsprofile.json'
    before = copy.deepcopy(agent.config)
    captured_options = []

    def save_file(*args, **kwargs):
        captured_options.append(kwargs['options'])
        return str(target), ''

    monkeypatch.setattr(QFileDialog, 'getSaveFileName', save_file)
    window.export_profile(nikki)
    package = load_profile_file(target)
    assert set(package) == {'format', 'version', 'source', 'profile'}
    assert set(package['source']) == {'family', 'input_kind'}
    assert set(package['profile']) == {'name', 'mode', 'mappings', 'options'}
    assert package['profile']['options'] == before['profile_options'][nikki]
    assert agent.config == before

    def open_file(*args, **kwargs):
        captured_options.append(kwargs['options'])
        return str(target), ''

    def confirm(dialog):
        dialog.name_edit.setText('File round trip')
        dialog.save()
        return dialog.result()

    monkeypatch.setattr(QFileDialog, 'getOpenFileName', open_file)
    monkeypatch.setattr(ProfileImportDialog, 'exec', confirm)
    window.import_profile()
    sync_remote(window, agent, app)
    imported = window.last_imported_profile
    assert imported == 'File round trip'
    assert agent.config['profiles'][imported] == package['profile']['mappings']
    assert agent.config['profile_options'][imported] == package['profile']['options']
    assert agent.config['active_profile'] == nikki
    assert agent.config['controller_profiles'][profile_scope(state)] == native
    assert agent.application_profile['automatic']
    assert all(value & QFileDialog.DontUseNativeDialog for value in captured_options)
    assert len(captured_options) == 2


def test_bad_external_file_is_reported_before_any_preview_or_write(remote_workspace, tmp_path, monkeypatch):
    window, agent, _, _, _, _, _, _, _ = remote_workspace
    target = tmp_path / 'broken.gpsprofile.json'
    target.write_text('{"format":"gamepadstudio-profile","format":"other"}', encoding='utf-8')
    before = copy.deepcopy(agent.config)
    ui_before = copy.deepcopy(window.config)
    before_file = agent.store.path.read_bytes()
    notifications = []
    monkeypatch.setattr(window, 'notify', notifications.append)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args, **kwargs: (str(target), ''))

    def unexpected_preview(_dialog):
        pytest.fail('A rejected external file must not reach preview')

    monkeypatch.setattr(ProfileImportDialog, 'exec', unexpected_preview)
    window.import_profile()
    assert notifications and '重复' in notifications[-1]
    assert agent.config == before
    assert window.config == ui_before
    assert agent.store.path.read_bytes() == before_file


def test_explicit_selection_after_import_releases_old_hold_and_waits_for_physical_release(remote_workspace):
    window, agent, app, state, _, _, nikki, actions, ui_actions = remote_workspace
    agent.engine.close()
    agent.engine = MappingRuntime(actions, agent.dispatch, start_mouse=False)
    assert window.mapping_change({'op': 'binding', 'profile': nikki, 'trigger': '0', 'mapping': entry('hold', 'E')})
    state['buttons'] = [0]
    agent.engine.update(state, agent.config, now=1, enabled=True)
    agent.engine.update(state, agent.config, now=1.2, enabled=True)
    assert actions.keys[ord('E')] == 1
    dialog = ProfileImportDialog(window, package_for(window, state, nikki))
    try:
        dialog.save()
        imported = window.last_imported_profile
        assert agent.config['active_profile'] == nikki
        assert not any(actions.keys.values())
        window.change_profile(imported)
        sync_remote(window, agent, app)
        assert window.profile_combo.currentText() == imported
        assert not agent.application_profile['automatic']
        agent.engine.update(state, agent.config, now=2, enabled=True)
        agent.engine.update(state, agent.config, now=2.2, enabled=True)
        assert not any(actions.keys.values())
        state['buttons'] = []
        agent.engine.update(state, agent.config, now=2.3, enabled=True)
        state['buttons'] = [0]
        agent.engine.update(state, agent.config, now=3, enabled=True)
        agent.engine.update(state, agent.config, now=3.2, enabled=True)
        assert actions.keys[ord('F')] == 1
        assert not ui_actions.calls
    finally:
        dialog.reject()


def test_standalone_import_keeps_pause_and_current_selection_with_owned_new_preset(workspace):
    window, app = workspace
    state = device(101)
    select(window, app, state)
    window.engine.close()
    window.actions = Actions()
    window.engine = MappingRuntime(window.actions, window.dispatch, start_mouse=False)
    before = copy.deepcopy(window.config)
    nikki = window.store.profiles_for(state, 'kbm')[0]
    dialog = ProfileImportDialog(window, package_for(window, state, nikki))
    try:
        dialog.name_edit.setText('Standalone backup')
        dialog.save()
        assert dialog.result() == QDialog.Accepted
        assert window.last_imported_profile == 'Standalone backup'
        assert window.config['profile_devices']['Standalone backup'] == profile_scope(state)
        assert window.config['active_profile'] == before['active_profile']
        assert window.config['controller_profiles'] == before['controller_profiles']
        assert not window.enabled
        assert not window.actions.calls
        persisted = ConfigStore(window.store.root)
        assert persisted.data['profiles']['Standalone backup'] == window.config['profiles']['Standalone backup']
    finally:
        dialog.reject()


def test_transfer_entries_require_a_connected_device_in_both_existing_menus(workspace):
    window, app = workspace
    actions = (window.import_profile_action, window.export_profile_action,
               window.virtual_kbm_page.import_profile_action, window.virtual_kbm_page.export_profile_action)
    assert all(not action.isEnabled() for action in actions)
    select(window, app, device(101))
    assert all(action.isEnabled() for action in actions)
    select(window, app, None)
    assert all(not action.isEnabled() for action in actions)

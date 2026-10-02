"""Portable import previews are drafts tied to one physical device session."""
import copy

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QWidget

from gamepadstudio.i18n import get_language_preference, init_language
from gamepadstudio.profile_transfer import export_profile
from gamepadstudio.profile_transfer_ui import ProfileImportDialog
from gamepadstudio.studio_core import ConfigStore, profile_scope
from tests.test_device_scope_ui import device
from tests.test_unified_mapping import entry


@pytest.fixture
def transfer_owner(tmp_path):
    app = QApplication.instance() or QApplication([])
    previous = get_language_preference()
    init_language('zh')

    class Owner(QWidget):
        def __init__(self):
            super().__init__()
            self.snapshot = device(101)
            self.store = ConfigStore(tmp_path)
            self.store.activate_controller(self.snapshot)
            self.store.save()
            self.config = self.store.data
            self.changes = []
            self.allow_save = True

        def mapping_change(self, change):
            self.changes.append(copy.deepcopy(change))
            if not self.allow_save:
                return False
            self.store.apply_mapping_change(change, self.snapshot)
            return True

    owner = Owner()
    name = owner.store.profiles_for(owner.snapshot, 'kbm')[0]
    package = export_profile(owner.config, owner.snapshot, name)
    package['source']['family'] = 'dualsense'
    package['profile']['mappings'] = {
        '0': entry('hold', 'E', 'suppress'),
        '9+0': entry('shortcut', 'Ctrl+S'),
        'LT': entry('hold', 'Q'),
        '20': entry('hold', 'F'),
        'TP:tap': entry('mouse_click', 'left'),
    }
    yield owner, app, package
    owner.close()
    init_language(previous)


def test_preview_shows_accepted_and_skipped_sources_without_mutating_configuration(transfer_owner):
    owner, _, package = transfer_owner
    before = copy.deepcopy(owner.config)
    dialog = ProfileImportDialog(owner, package)
    try:
        assert {row['trigger'] for row in dialog.preview['accepted']} == {'0', '0+9', 'LT'}
        assert {row['trigger'] for row in dialog.preview['skipped']} == {'20', 'TP:tap'}
        visible = [dialog.tree.topLevelItem(index) for index in range(dialog.tree.topLevelItemCount())]
        assert len(visible) == 6  # Three accepted, a separator, and two skipped.
        assert any(item.text(1) == 'Ctrl+S' for item in visible)
        assert any(item.text(0) == '因设备差异跳过' for item in visible)
        assert any('不支持' in item.text(1) for item in visible)
        assert any('专属' in item.text(1) for item in visible)
        assert '3' in dialog.summary.text() and '2' in dialog.summary.text()
        assert owner.changes == []
        assert owner.config == before
        package['profile']['name'] = 'Changed after preview'
        assert dialog.package['profile']['name'] != package['profile']['name']
    finally:
        dialog.reject()


def test_cancel_discards_name_edits_and_never_writes(transfer_owner):
    owner, _, package = transfer_owner
    before = copy.deepcopy(owner.config)
    before_file = owner.store.path.read_bytes()
    dialog = ProfileImportDialog(owner, package)
    dialog.name_edit.setText('My edited backup')
    dialog.reject()
    assert dialog.result() == QDialog.Rejected
    assert owner.changes == []
    assert owner.config == before
    assert owner.store.path.read_bytes() == before_file
    assert not dialog.timer.isActive()


def test_confirm_import_creates_owned_unique_preset_and_preserves_active_baseline(transfer_owner):
    owner, _, package = transfer_owner
    before = copy.deepcopy(owner.config)
    source_name = package['profile']['name']
    dialog = ProfileImportDialog(owner, package)
    try:
        dialog.save()
        assert dialog.result() == QDialog.Accepted
        assert len(owner.changes) == 1
        change = owner.changes[0]
        assert change['op'] == 'import_profile'
        assert change['expected_inputs'] == dialog.preview['input_signature']
        imported = owner.store.last_imported_profile
        assert imported != source_name
        assert set(owner.config['profiles']) - set(before['profiles']) == {imported}
        assert owner.config['profiles'][source_name] == before['profiles'][source_name]
        assert owner.config['profile_devices'][imported] == profile_scope(owner.snapshot)
        assert set(owner.config['profiles'][imported]) == {'0', '0+9', 'LT'}
        assert owner.config['profile_options'][imported] == package['profile']['options']
        assert owner.config['active_profile'] == before['active_profile']
        assert owner.config['controller_profiles'] == before['controller_profiles']
        assert owner.config['application_profiles'] == before['application_profiles']
        assert ConfigStore(owner.store.root).data['profiles'][imported] == owner.config['profiles'][imported]
        assert not dialog.timer.isActive()
    finally:
        dialog.close()


def test_backend_rejection_keeps_preview_and_name_for_retry(transfer_owner):
    owner, _, package = transfer_owner
    owner.allow_save = False
    before = copy.deepcopy(owner.config)
    dialog = ProfileImportDialog(owner, package)
    try:
        dialog.name_edit.setText('Retry after reconnecting the backend')
        dialog.save()
        assert dialog.result() != QDialog.Accepted
        assert dialog.name_edit.text() == 'Retry after reconnecting the backend'
        assert dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()
        assert owner.config == before
        assert len(owner.changes) == 1
    finally:
        dialog.reject()


@pytest.mark.parametrize('replacement', [
    'disconnect', 'other_device', 'new_instance', 'family', 'buttons', 'axes', 'raw_input',
])
def test_changed_device_or_capability_permanently_invalidates_preview(transfer_owner, replacement):
    owner, _, package = transfer_owner
    original = copy.deepcopy(owner.snapshot)
    before = copy.deepcopy(owner.config)
    dialog = ProfileImportDialog(owner, package)
    try:
        if replacement == 'disconnect':
            owner.snapshot = None
        elif replacement == 'other_device':
            owner.snapshot = device(202)
        elif replacement == 'new_instance':
            owner.snapshot['instance_id'] += 1
            assert profile_scope(owner.snapshot) == profile_scope(original)
        elif replacement == 'family':
            owner.snapshot['family'] = 'dualsense'
        elif replacement == 'buttons':
            owner.snapshot['available_buttons'] = [0, 1, 4]
        elif replacement == 'axes':
            owner.snapshot['available_axes'] = [0, 1]
        else:
            owner.snapshot.update(is_gamecontroller=False, model_key='raw:test-model')
        dialog.save()
        assert not dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()
        assert dialog.message.text()
        assert dialog.result() != QDialog.Accepted
        assert owner.changes == []
        assert owner.config == before
        owner.snapshot = original
        assert not dialog.check_device()
    finally:
        dialog.reject()


def test_empty_compatible_mapping_preview_cannot_be_confirmed(transfer_owner):
    owner, _, package = transfer_owner
    package['profile']['mappings'] = {'TP:tap': entry('mouse_click', 'left')}
    dialog = ProfileImportDialog(owner, package)
    try:
        assert not dialog.preview['accepted']
        assert dialog.preview['skipped']
        assert not dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()
        dialog.save()
        assert owner.changes == []
    finally:
        dialog.reject()


@pytest.mark.parametrize('language', ['zh', 'en'])
def test_narrow_preview_keeps_confirmation_visible_and_full_values_reachable(transfer_owner, language):
    owner, app, package = transfer_owner
    init_language(language)
    package['profile']['mappings'] = {
        str(index): entry('shortcut', 'Ctrl+Shift+Alt+PrintScreen') for index in range(30)}
    dialog = ProfileImportDialog(owner, package)
    try:
        dialog.resize(350, 420)
        dialog.show()
        app.processEvents()
        assert dialog.width() == 350
        button = dialog.buttons.button(QDialogButtonBox.Ok)
        assert button.isVisible() and button.isEnabled()
        assert dialog.rect().contains(button.mapTo(dialog, QPoint(0, 0)))
        assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
        assert dialog.tree.verticalScrollBar().maximum() > 0
        accepted = dialog.tree.topLevelItem(0)
        assert accepted.toolTip(1) == 'Ctrl+Shift+Alt+PrintScreen'
        dialog.tree.scrollToItem(dialog.tree.topLevelItem(dialog.tree.topLevelItemCount() - 1))
        app.processEvents()
        assert dialog.tree.verticalScrollBar().value() > 0
    finally:
        dialog.reject()

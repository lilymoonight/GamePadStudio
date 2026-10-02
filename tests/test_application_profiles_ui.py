"""Application rules remain editable drafts owned by one physical session."""
import copy

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QScrollArea, QWidget

from gamepadstudio.application_profiles_ui import ApplicationProfilesDialog
from gamepadstudio.i18n import get_language_preference, init_language
from gamepadstudio.studio_core import ConfigStore, profile_scope
from tests.test_device_scope_ui import device


@pytest.fixture
def rules_owner(tmp_path):
    app = QApplication.instance() or QApplication([])
    previous = get_language_preference()
    init_language('zh')

    class Owner(QWidget):
        def __init__(self):
            super().__init__()
            self.snapshot = device(101)
            self.store = ConfigStore(tmp_path)
            self.store.activate_controller(self.snapshot)
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
    yield owner, app
    owner.close()
    init_language(previous)


def rule(owner, path=r'C:\Games\InfinityNikki\InfinityNikki.exe'):
    return {'executable': path, 'profile': owner.store.profiles_for(owner.snapshot, 'kbm')[0]}


def test_dialog_lists_only_current_device_presets(rules_owner):
    owner, _ = rules_owner
    first = copy.deepcopy(owner.snapshot)
    foreign = device(202)
    owner.store.activate_controller(foreign)
    foreign_profiles = set(owner.store.profiles_for(foreign))
    owner.store.activate_controller(first)
    dialog = ApplicationProfilesDialog(owner)
    try:
        dialog.add_rule(rule(owner))
        combo = dialog.rows[0].profile_combo
        choices = {combo.itemData(index) for index in range(combo.count())}
        assert choices == set(owner.store.profiles_for(first))
        assert choices.isdisjoint(foreign_profiles)
        assert combo.currentData() == rule(owner)['profile']
    finally:
        dialog.reject()


def test_cancel_discards_toggle_and_rule_edits_without_writing(rules_owner):
    owner, _ = rules_owner
    owner.store.apply_mapping_change({'op': 'application_profiles',
                                     'settings': {'enabled': False, 'rules': [rule(owner)]}}, owner.snapshot)
    before = copy.deepcopy(owner.config)
    dialog = ApplicationProfilesDialog(owner)
    dialog.enabled_box.setChecked(True)
    dialog.rows[0].profile_combo.setCurrentIndex(0)
    dialog.add_rule(rule(owner, r'C:\Tools\PhotoEditor.exe'))
    dialog.remove_rule(dialog.rows[0])
    dialog.reject()
    assert dialog.result() == QDialog.Rejected
    assert owner.changes == []
    assert owner.config == before
    assert ConfigStore(owner.store.root).data['application_profiles'] == before['application_profiles']
    assert not dialog.timer.isActive()


def test_save_persists_complete_current_device_draft_once(rules_owner):
    owner, _ = rules_owner
    dialog = ApplicationProfilesDialog(owner)
    try:
        expected_rule = rule(owner)
        dialog.add_rule(expected_rule)
        dialog.enabled_box.setChecked(True)
        dialog.save()
        expected = {'enabled': True, 'rules': [expected_rule]}
        assert dialog.result() == QDialog.Accepted
        assert owner.changes == [{'op': 'application_profiles', 'settings': expected}]
        loaded = ConfigStore(owner.store.root)
        assert loaded.application_settings(owner.snapshot) == expected
        assert set(loaded.data['application_profiles']) == {profile_scope(owner.snapshot)}
        assert not dialog.timer.isActive()
    finally:
        dialog.close()


def test_failed_authoritative_save_keeps_draft_open(rules_owner):
    owner, _ = rules_owner
    owner.allow_save = False
    before = copy.deepcopy(owner.config)
    dialog = ApplicationProfilesDialog(owner)
    try:
        dialog.add_rule(rule(owner))
        dialog.enabled_box.setChecked(True)
        dialog.save()
        assert dialog.result() != QDialog.Accepted
        assert dialog.enabled_box.isChecked()
        assert len(dialog.rows) == 1
        assert dialog.buttons.button(QDialogButtonBox.Save).isEnabled()
        assert owner.config == before
    finally:
        dialog.reject()


@pytest.mark.parametrize('replacement', ['disconnect', 'other_device', 'new_instance'])
def test_save_refuses_device_change_even_if_scope_is_same(rules_owner, replacement):
    owner, _ = rules_owner
    original = copy.deepcopy(owner.snapshot)
    before = copy.deepcopy(owner.config)
    dialog = ApplicationProfilesDialog(owner)
    try:
        dialog.add_rule(rule(owner))
        dialog.enabled_box.setChecked(True)
        if replacement == 'disconnect':
            owner.snapshot = None
        elif replacement == 'other_device':
            owner.snapshot = device(202)
        else:
            owner.snapshot = dict(original, instance_id=999)
            assert profile_scope(owner.snapshot) == profile_scope(original)
        dialog.save()
        assert dialog.result() != QDialog.Accepted
        assert not dialog.buttons.button(QDialogButtonBox.Save).isEnabled()
        assert not dialog.add_button.isEnabled()
        assert owner.changes == []
        assert owner.config == before
        owner.snapshot = original
        assert not dialog.check_device()  # Returning cannot revive a stale edit session.
    finally:
        dialog.reject()


@pytest.mark.parametrize('language', ['zh', 'en'])
def test_narrow_dialog_keeps_save_visible_and_rules_scrollable(rules_owner, language):
    owner, app = rules_owner
    init_language(language)
    dialog = ApplicationProfilesDialog(owner)
    try:
        for index in range(8):
            dialog.add_rule(rule(owner, rf'C:\Games\A Very Long Installation Folder\Game{index}.exe'))
        dialog.resize(350, 520)
        dialog.show()
        app.processEvents()
        assert dialog.width() == 350
        save = dialog.buttons.button(QDialogButtonBox.Save)
        assert save.isVisible() and save.isEnabled()
        assert dialog.rect().contains(save.mapTo(dialog, QPoint(0, 0)))
        assert dialog.rect().contains(save.mapTo(dialog, save.rect().bottomRight()))
        area = dialog.findChild(QScrollArea)
        assert area.horizontalScrollBar().maximum() == 0
        assert area.verticalScrollBar().maximum() > 0
        for row in dialog.rows:
            assert row.profile_combo.width() > 50
            assert row.path_label.toolTip() == row.executable
    finally:
        dialog.reject()

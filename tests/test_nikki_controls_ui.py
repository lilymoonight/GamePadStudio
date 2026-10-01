import copy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from gamepadstudio.kbm_mapper import NIKKI_PROFILE_NAME
from gamepadstudio.i18n import get_language_preference, init_language
from gamepadstudio.mapping_ui import BindingDialog
from gamepadstudio.virtual_kbm_ui import KbmFeelDialog, NikkiLayoutDialog, VirtualKbmPage
from tests.mapping_fixtures import MappingOwner


@pytest.fixture
def controls_view(tmp_path):
    app = QApplication.instance() or QApplication([])
    previous_language = get_language_preference()
    init_language('zh')
    owner = MappingOwner(tmp_path)
    page = VirtualKbmPage(owner)
    owner.page = page
    yield owner, page
    page.close()
    owner.close()
    init_language(previous_language)


def test_feel_edits_selected_profile_and_preserves_other_options(controls_view, monkeypatch):
    owner, page = controls_view
    active = owner.config['active_profile']
    original_active = copy.deepcopy(owner.config['profile_options'].get(active))
    options = owner.config['profile_options'][NIKKI_PROFILE_NAME]
    options['mouse']['future_curve'] = 'gentle'
    options['input']['future_setting'] = {'enabled': True}
    options['unrelated_option'] = 73

    def save(dialog):
        dialog.mouse_fields['sensitivity'].setValue(31)
        dialog.input_fields['trigger_press'].setValue(36)
        dialog.input_fields['trigger_release'].setValue(23)
        dialog.chord_window.setValue(60)
        dialog.walk_toggle.setChecked(False)
        dialog.validate()
        return dialog.result()

    monkeypatch.setattr(KbmFeelDialog, 'exec', save)
    page.mouse_settings()

    assert owner.config['active_profile'] == active
    assert owner.config['profile_options'].get(active) == original_active
    saved = owner.config['profile_options'][NIKKI_PROFILE_NAME]
    assert saved['mouse']['sensitivity'] == 31
    assert saved['mouse']['future_curve'] == 'gentle'
    assert saved['input']['trigger_press'] == .36
    assert saved['input']['trigger_release'] == .23
    assert saved['input']['chord_window'] == .06
    assert saved['input']['future_setting'] == {'enabled': True}
    assert 'walk_press' not in saved['input'] and 'walk_release' not in saved['input']
    assert saved['unrelated_option'] == 73
    assert owner.changes[-1]['profile'] == NIKKI_PROFILE_NAME
    assert set(owner.changes[-1]['options']) == {'mouse', 'input'}


def test_cancelling_feel_does_not_write_any_profile(controls_view, monkeypatch):
    owner, page = controls_view
    original = copy.deepcopy(owner.config)

    def cancel(dialog):
        dialog.mouse_fields['sensitivity'].setValue(80)
        dialog.input_fields['stick_press'].setValue(90)
        dialog.walk_toggle.setChecked(False)
        dialog.reject()
        return dialog.result()

    monkeypatch.setattr(KbmFeelDialog, 'exec', cancel)
    page.mouse_settings()

    assert owner.config == original
    assert not owner.changes


@pytest.mark.parametrize('fields, message', [
    ({'trigger_press': 25, 'trigger_release': 25}, '松开位置'),
    ({'stick_press': 20, 'stick_release': 21}, '松开位置'),
    ({'walk_press': 72, 'walk_release': 62}, '慢走区间'),
    ({'stick_press': 65, 'walk_press': 62, 'walk_release': 72}, '慢走区间'),
])
def test_feel_rejects_invalid_response_order(controls_view, fields, message):
    owner, _ = controls_view
    original = copy.deepcopy(owner.config)
    dialog = KbmFeelDialog(owner, NIKKI_PROFILE_NAME)
    try:
        dialog.show()
        for key, value in fields.items():
            dialog.input_fields[key].setValue(value)
        QTest.mouseClick(dialog.controls.button(QDialogButtonBox.Save), Qt.LeftButton)

        assert dialog.result() != QDialog.Accepted
        assert not dialog.error.isHidden()
        assert message in dialog.error.text()
        assert owner.config == original
        assert not owner.changes
    finally:
        dialog.close()


def test_custom_profile_keeps_legacy_response_defaults_and_has_no_walk(controls_view):
    owner, page = controls_view
    owner.config['profiles']['Custom keyboard'] = {'0': {'short': {'action': 'hold', 'value': 'Space'}}}
    owner.config['profile_modes']['Custom keyboard'] = 'kbm'
    owner.config['profile_devices']['Custom keyboard'] = 'offline:xinput'
    owner.config['profile_options']['Custom keyboard'] = {'custom': 14}
    owner.change_profile('Custom keyboard')
    original = copy.deepcopy(owner.config)
    dialog = KbmFeelDialog(owner, 'Custom keyboard')
    try:
        assert dialog.input_fields['stick_press'].value() == 50
        assert dialog.input_fields['stick_release'].value() == 35
        assert dialog.input_fields['trigger_press'].value() == 55
        assert dialog.input_fields['trigger_release'].value() == 35
        assert dialog.chord_window.value() == 80
        assert not dialog.walk_toggle.isEnabled()
        assert not dialog.walk_toggle.isChecked()
        assert not page.layout_btn.isEnabled()
        assert owner.config == original
    finally:
        dialog.close()


@pytest.mark.parametrize('axes, expected', [
    ([0, 0, 0, 0, .30, 0], 'LT'),
    ([0, -.40, 0, 0, 0, 0], 'LS:up'),
    ([0, -.94, 0, 0, 0, 0], 'LS:up'),
])
def test_capture_uses_edited_profile_response_and_ignores_walk_regions(controls_view, axes, expected):
    owner, _ = controls_view
    assert owner.config['active_profile'] != NIKKI_PROFILE_NAME
    # The active profile deliberately needs a deeper pull than the profile being edited.
    owner.config['profile_options'].setdefault(owner.config['active_profile'], {})['input'] = {
        'trigger_press': .90, 'trigger_release': .75, 'stick_press': .80, 'stick_release': .65,
    }
    dialog = BindingDialog(owner, profile=NIKKI_PROFILE_NAME, mode='kbm')
    dialog.timer.stop()
    try:
        dialog.start_capture()
        owner.snapshot = {'buttons': [], 'axes': [0.] * 6}
        dialog.poll()
        owner.snapshot = {'buttons': [], 'axes': axes}
        dialog.poll()
        assert dialog.best == {expected}
        owner.snapshot = {'buttons': [], 'axes': [0.] * 6}
        dialog.poll()
        assert dialog.trigger() == expected
        assert not dialog.capturing
        assert all(c.currentData() not in ('LS:inner', 'LS:outer') for c in dialog.inputs)
    finally:
        dialog.close()


def test_layout_shows_actual_outputs_hints_and_missing_bindings(controls_view):
    owner, page = controls_view
    active = owner.config['active_profile']
    owner.config['profiles'][NIKKI_PROFILE_NAME]['0'] = {
        'short': {'action': 'hold', 'value': 'N'},
        'long': {'action': 'shortcut', 'value': 'F10'}, 'long_press': .77,
    }
    owner.config['profiles'][NIKKI_PROFILE_NAME].pop('9+10', None)
    dialog = NikkiLayoutDialog(owner, NIKKI_PROFILE_NAME)
    try:
        assert page.layout_btn.isEnabled()
        tile = dialog.trigger_cards['0']
        assert 'N' in tile.output_labels['short'].text()
        assert '服装进化' == tile.hint_labels['short'].text()
        assert 'F10' in tile.output_labels['long'].text()
        assert '联机' == tile.hint_labels['long'].text()
        assert '9+10' not in dialog.trigger_cards
        assert owner.config['active_profile'] == active
        dialog.edit('0')
        assert owner.edits[-1] == (('0',), {'profile': NIKKI_PROFILE_NAME, 'mode': 'kbm'})
        owner.config['profiles'][NIKKI_PROFILE_NAME]['0']['short']['value'] = 'Space'
        dialog.refresh()
        assert dialog.trigger_cards['0'].hint_labels['short'].text() == '跳跃 / 漂浮 / 自行车跳跃'
    finally:
        dialog.close()


def test_controls_dialogs_fit_narrow_window_with_vertical_scrolling(controls_view):
    owner, _ = controls_view
    owner.resize(960, 640)
    for dialog_type in (KbmFeelDialog, NikkiLayoutDialog):
        dialog = dialog_type(owner, NIKKI_PROFILE_NAME)
        try:
            for width in (720, 440, 780):
                dialog.resize(width, 560)
                dialog.show()
                QTest.qWait(5)
                assert dialog.area.horizontalScrollBar().maximum() == 0
                assert dialog.area.widget().width() <= dialog.area.viewport().width()
                assert dialog.area.verticalScrollBar().maximum() > 0
        finally:
            dialog.close()

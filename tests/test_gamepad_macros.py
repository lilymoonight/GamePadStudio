"""Tests for pure Gamepad-to-Gamepad macros: remap, chords, turbo, and profile modes."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import pytest
from PySide6.QtWidgets import QApplication

from gamepadstudio.mapping_engine import (GestureEngine, validate_mappings, canonical_trigger,
                                         binding_label, output_tokens)
from gamepadstudio.studio_core import profile_mode, default_config, ACTION_NAMES
from gamepadstudio.kbm_mapper import NIKKI_PROFILE_NAME
from gamepadstudio.mapping_ui import BindingDialog, GamepadTargetSelector


def test_profile_mode_classification(tmp_path):
    config = default_config(tmp_path)
    assert profile_mode(config, '主机体验') == 'gamepad'
    assert profile_mode(config, '动作与格斗宏') == 'gamepad'
    assert '桌面导航' not in config['profiles']
    assert profile_mode(config, NIKKI_PROFILE_NAME) == 'kbm'


def test_gamepad_macro_validation_and_tokens():
    mappings = {
        '0': {'short': {'action': 'none'}, 'long': {'action': 'gamepad_turbo', 'value': '0', 'rate_hz': 20}},
        '2': {'short': {'action': 'none'}, 'long': {'action': 'gamepad_chord', 'value': '2+3'}},
        '9+10': {'short': {'action': 'gamepad_chord', 'value': '9+10+3'}, 'long': {'action': 'none'}},
        '1': {'short': {'action': 'gamepad_button', 'value': '2'}, 'long': {'action': 'none'}},
    }
    validated = validate_mappings(mappings)
    assert '0' in validated and '2' in validated and '9+10' in validated and '1' in validated

    assert output_tokens({'action': 'gamepad_button', 'value': '2'}) == ['pad:2']
    assert output_tokens({'action': 'gamepad_chord', 'value': '2+3'}) == ['pad:2', 'pad:3']
    assert output_tokens({'action': 'gamepad_turbo', 'value': '0'}) == ['pad_turbo:0']


def test_gamepad_macro_gesture_dispatch():
    events = []
    def dispatch(binding, down):
        events.append((binding['action'], binding.get('value'), down))

    engine = GestureEngine(dispatch, threshold=0.5)
    maps = {
        '2': {'short': {'action': 'gamepad_button', 'value': '3'},
              'long': {'action': 'gamepad_chord', 'value': '2+3'}}
    }

    # Short press of button 2 (Square -> outputs Triangle)
    engine.update({'2'}, maps, 0.0)
    engine.update({'2'}, maps, 0.2)
    engine.update(set(), maps, 0.25)
    # At 0.35s, 65ms tap interval has elapsed, release fires
    engine.update(set(), maps, 0.35)
    assert ('gamepad_button', '3', True) in events
    assert ('gamepad_button', '3', False) in events

    events.clear()
    # Long press of button 2 (Square -> triggers Chord 2+3)
    engine.update({'2'}, maps, 1.0)
    engine.update({'2'}, maps, 1.6)
    assert ('gamepad_chord', '2+3', True) in events
    engine.update(set(), maps, 2.0)
    assert ('gamepad_chord', '2+3', False) in events


@pytest.mark.parametrize('action,value', [('gamepad_button', '3'),
                                        ('gamepad_chord', '2+3'), ('gamepad_turbo', '0')])
def test_mapping_runtime_routes_gamepad_holds_to_gamepad_dispatch(action, value):
    from gamepadstudio.mapping_engine import MappingRuntime
    from tests.test_unified_mapping import Actions, entry, frame
    actions = Actions()
    dispatched = []
    runtime = MappingRuntime(actions, lambda binding, down: dispatched.append(
        (binding['action'], binding['value'], down)), start_mouse=False)
    config = {'active_profile': 'test', 'profiles': {'test': {'0': entry(action, value)}}}
    runtime.update(frame([0]), config, now=0)
    runtime.update(frame(), config, now=.1)
    assert dispatched == [(action, value, True), (action, value, False)]
    assert not actions.calls and not runtime.feedback()['outputs']


def test_gamepad_target_selector_ui(tmp_path):
    app = QApplication.instance() or QApplication([])
    selector = GamepadTargetSelector('dualsense')
    selector.set_mode('single')
    selector._on_btn_clicked('1')
    assert selector.get_selected_single() == '1'

    selector.set_mode('chord')
    selector._on_btn_clicked('2')
    selector._on_btn_clicked('3')
    chord = selector.get_selected_chord()
    assert '2' in chord and '3' in chord

    selector.set_mode('turbo')
    selector.turbo_spin.setValue(18)
    assert selector.get_turbo_rate() == 18


def test_binding_dialog_pure_gamepad_configuration(tmp_path, monkeypatch):
    from gamepadstudio.studio import Studio
    app = QApplication.instance() or QApplication([])
    studio = Studio(tmp_path, standalone=True)
    try:
        # Edit button 0 (A / Cross)
        mapping_data = {
            'short': {'action': 'none'},
            'long': {'action': 'gamepad_chord', 'value': '2+3'},
            'long_press': 0.65
        }
        dialog = BindingDialog(studio, trigger='0', mapping=mapping_data)
        val = dialog.value()
        assert val['short']['action'] == 'none'
        assert val['long']['action'] == 'gamepad_chord'
        assert '2' in val['long']['value'] and '3' in val['long']['value']
        assert val['long_press'] == 0.65
    finally:
        studio.cleanup()
        studio.hide()


def test_dropdown_isolation_gamepad_vs_kbm(tmp_path):
    from gamepadstudio.studio import Studio
    app = QApplication.instance() or QApplication([])
    studio = Studio(tmp_path, standalone=True)
    try:
        studio.refresh_mappings()
        # Page 1: Gamepad mapping combo MUST ONLY contain gamepad profiles
        mapping_items = [studio.mapping_combo.itemText(i) for i in range(studio.mapping_combo.count())]
        assert mapping_items == studio.store.profiles_for(studio.snapshot, 'gamepad')
        assert '主机体验' not in mapping_items
        assert '动作与格斗宏' not in mapping_items
        assert '桌面导航' not in mapping_items
        assert NIKKI_PROFILE_NAME not in mapping_items

        # Page 6: Virtual KBM scheme combo MUST ONLY contain KBM profiles
        scheme_items = [studio.virtual_kbm_page.scheme_combo.itemText(i) for i in range(studio.virtual_kbm_page.scheme_combo.count())]
        assert scheme_items == [NIKKI_PROFILE_NAME]
        assert '主机体验' not in scheme_items
        assert '动作与格斗宏' not in scheme_items

        # Page 0: Profile combo contains only the current input device's profiles.
        profile_items = [studio.profile_combo.itemText(i) for i in range(studio.profile_combo.count())]
        assert profile_items == studio.store.profiles_for(studio.snapshot)
        assert '主机体验' not in profile_items
        assert '动作与格斗宏' not in profile_items
        assert '桌面导航' not in profile_items
        assert NIKKI_PROFILE_NAME in profile_items
    finally:
        studio.cleanup()
        studio.hide()


def test_mode_inheritance_on_create_profile(tmp_path):
    from gamepadstudio.studio import Studio
    app = QApplication.instance() or QApplication([])
    studio = Studio(tmp_path, standalone=True)
    try:
        # Create a gamepad profile
        studio.mapping_change({'op': 'create', 'profile': '自定义手柄宏', 'mode': 'gamepad'})
        assert profile_mode(studio.config, '自定义手柄宏') == 'gamepad'
        mapping_items = [studio.mapping_combo.itemText(i) for i in range(studio.mapping_combo.count())]
        scheme_items = [studio.virtual_kbm_page.scheme_combo.itemText(i) for i in range(studio.virtual_kbm_page.scheme_combo.count())]
        assert '自定义手柄宏' in mapping_items
        assert '自定义手柄宏' not in scheme_items

        # Create a KBM profile
        studio.mapping_change({'op': 'create', 'profile': '自定义键鼠预设', 'mode': 'kbm'})
        assert profile_mode(studio.config, '自定义键鼠预设') == 'kbm'
        mapping_items = [studio.mapping_combo.itemText(i) for i in range(studio.mapping_combo.count())]
        scheme_items = [studio.virtual_kbm_page.scheme_combo.itemText(i) for i in range(studio.virtual_kbm_page.scheme_combo.count())]
        assert '自定义键鼠预设' in scheme_items
        assert '自定义键鼠预设' not in mapping_items
    finally:
        studio.cleanup()
        studio.hide()


def test_kbm_and_gamepad_dialog_modes(tmp_path):
    from gamepadstudio.studio import Studio
    app = QApplication.instance() or QApplication([])
    studio = Studio(tmp_path, standalone=True)
    try:
        # Gamepad mode dialog
        g_dialog = BindingDialog(studio, '0', profile=studio.current_gamepad_profile(), mode='gamepad')
        assert g_dialog.mode == 'gamepad'
        assert 'gamepad_button' in [g_dialog.action_combos['short'].itemData(i) for i in range(g_dialog.action_combos['short'].count())]
        assert 'hold' not in [g_dialog.action_combos['short'].itemData(i) for i in range(g_dialog.action_combos['short'].count())]

        # KBM mode dialog
        k_dialog = BindingDialog(studio, '0', profile=NIKKI_PROFILE_NAME, mode='kbm')
        assert k_dialog.mode == 'kbm'
        assert 'hold' in [k_dialog.action_combos['short'].itemData(i) for i in range(k_dialog.action_combos['short'].count())]
        assert 'gamepad_button' not in [k_dialog.action_combos['short'].itemData(i) for i in range(k_dialog.action_combos['short'].count())]
    finally:
        studio.cleanup()
        studio.hide()

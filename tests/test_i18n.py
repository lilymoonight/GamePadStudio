"""
Unit tests for GamePad Studio internationalization (i18n).
Verifies bilingual translations, language switching, profile compatibility,
and UI component integration.
"""
from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication

from gamepadstudio import i18n
from gamepadstudio.i18n import tr, tr_button, tr_profile, init_language, set_language, get_language, is_english
from gamepadstudio.controller_catalog import button_labels, axis_labels, get_catalog_entry
from gamepadstudio.studio_core import get_action_names, get_buttons
from gamepadstudio.glass import Indicator, TOKENS
from gamepadstudio.studio import Studio


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_i18n_core_language_switch():
    init_language('en')
    assert get_language() == 'en'
    assert is_english() is True
    assert tr('设备概览') == 'Overview'
    assert tr('虚拟键鼠') == 'Virtual KBM'
    assert tr('按键配置') == 'Key Mapping'
    assert tr('系统设置') == 'Settings'

    init_language('zh')
    assert get_language() == 'zh'
    assert is_english() is False
    assert tr('设备概览') == '设备概览'
    assert tr('虚拟键鼠') == '虚拟键鼠'

    # Test listener callback
    called = []
    i18n.register_language_listener(lambda: called.append(True))
    set_language('en')
    assert len(called) == 1
    assert get_language() == 'en'

    # Reset back to auto / zh
    init_language('zh')


def test_i18n_tr_formatting():
    init_language('en')
    msg = tr('已清理 {count} 张未收藏截图', count=5)
    assert msg == 'Cleaned 5 unfavorited captures'

    init_language('zh')
    msg_zh = tr('已清理 {count} 张未收藏截图', count=5)
    assert msg_zh == '已清理 5 张未收藏截图'


def test_i18n_button_and_profile_translations():
    init_language('en')
    assert tr_button('×  交叉') == '×  Cross'
    assert tr_button('方向键 ↑') == 'D-Pad ↑'
    assert tr_profile('主机体验') == 'Console Standard'
    assert tr_profile('桌面导航') == 'Desktop Navigation'
    assert tr_profile('3D 动作通用预设') == '3D Action Preset'

    init_language('zh')
    assert tr_button('×  交叉') == '×  交叉'
    assert tr_profile('主机体验') == '主机体验'


def test_controller_catalog_bilingual():
    btn_en = button_labels('dualsense', lang='en')
    assert btn_en[0] == '×  Cross'
    assert btn_en[11] == 'D-Pad ↑'

    btn_zh = button_labels('dualsense', lang='zh')
    assert btn_zh[0] == '×  交叉'
    assert btn_zh[11] == '方向键 ↑'

    axis_en = axis_labels('dualsense', lang='en')
    assert axis_en[0] == 'Left Stick X'
    assert 'L2' in axis_en

    axis_zh = axis_labels('dualsense', lang='zh')
    assert axis_zh[0] == '左摇杆 X'

    entry = get_catalog_entry('generic', lang='en')
    assert entry['name'] == 'Universal Gamepad / Peripherals'


def test_studio_core_bilingual():
    actions_en = get_action_names(lang='en')
    assert actions_en['capture'] == 'Take Screenshot'
    assert actions_en['replay_record'] == 'Instant Replay (Save Clip)'

    actions_zh = get_action_names(lang='zh')
    assert actions_zh['capture'] == '保存截图'
    assert actions_zh['replay_record'] == '保存精彩瞬间 (回放录制)'

    buttons_en = get_buttons(lang='en')
    assert buttons_en[0] == '×  Cross'


def test_glass_indicator_english_tokens(qapp):
    ind = Indicator()
    ind.setText('Connected')
    assert ind.color == TOKENS['green']
    assert ind.symbol == 'connected'

    ind.setText('Disconnected')
    assert ind.color == TOKENS['ink_dim']
    assert ind.symbol == 'disconnected'

    ind.setText('Running')
    assert ind.color == TOKENS['green']
    assert ind.symbol == 'connected'

    ind.kind = 'power'
    ind.setText('External Power')
    assert ind.symbol == 'bolt'
    assert ind.color == TOKENS['accent']

    ind.setText('Battery Low')
    assert ind.symbol == 'battery_low'
    assert ind.color == TOKENS['amber']


def test_studio_gui_english_initialization(qapp, tmp_path):
    root = tmp_path / 'studio_i18n_test'
    root.mkdir(parents=True, exist_ok=True)

    # Initialize studio with English
    win = Studio(root, standalone=True, lang='en')
    try:
        assert win.lang_combo.currentText() == 'English (US)'
        assert is_english() is True

        # Check navigation item tooltips
        tooltips = [b.toolTip() for b in win.nav.values()]
        assert 'Overview' in tooltips
        assert 'Key Mapping' in tooltips
        assert 'Captures Gallery' in tooltips
        assert 'Telemetry' in tooltips
        assert 'Settings' in tooltips
        assert 'Virtual KBM' in tooltips
        assert 'Controller Library' in tooltips

        # Test changing profile by English name
        win.change_profile('Console Standard')
        assert win.config['active_profile'] == '主机体验'

        # Test language selector change to Simplified Chinese
        zh_index = win.lang_combo.findText('简体中文 (Simplified Chinese)')
        assert zh_index >= 0
        win.lang_combo.setCurrentIndex(zh_index)
        assert get_language() == 'zh'

        # Switch back to English
        en_index = win.lang_combo.findText('English (US)')
        win.lang_combo.setCurrentIndex(en_index)
        assert get_language() == 'en'
    finally:
        win.close()

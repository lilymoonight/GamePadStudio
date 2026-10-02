from types import SimpleNamespace
import pytest
from gamepadstudio.gamebar_shield import (
    is_gamebar_shield_active,
    enable_gamebar_shield,
    disable_gamebar_shield,
    get_gamebar_shield_summary,
)
from gamepadstudio.i18n import tr, set_language, get_language


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    """Never change the user's Windows settings while running tests."""
    from gamepadstudio import gamebar_shield as shield
    monkeypatch.setattr(shield, 'sys', SimpleNamespace(platform='win32'))
    values = {}
    monkeypatch.setattr(shield, '_read_reg_dword', lambda root, key, name: values.get((root, key, name)))
    def write(root, key, name, value):
        values[(root, key, name)] = value
        return True
    def delete(root, key, name):
        values.pop((root, key, name), None)
        return True
    monkeypatch.setattr(shield, '_write_reg_dword', write)
    monkeypatch.setattr(shield, '_delete_reg_value', delete)
    monkeypatch.setattr(shield, '_broadcast_setting_change', lambda: None)
    monkeypatch.setattr(shield, '_refresh_gamebar_processes', lambda: True)
    return values


def test_restore_preserves_missing_values_and_custom_settings(isolated_registry):
    from gamepadstudio.gamebar_shield import TARGET_REG_ITEMS
    root, key, name, _, _ = TARGET_REG_ITEMS[0]
    isolated_registry[(root, key, name)] = 0
    before = dict(isolated_registry)
    ok, _, backup = enable_gamebar_shield()
    assert ok
    assert disable_gamebar_shield(backup)[0]
    assert isolated_registry == before


def test_failed_restore_is_reported_and_backup_kept(tmp_path, monkeypatch):
    from gamepadstudio import gamebar_shield as shield
    from gamepadstudio.studio_core import ConfigStore
    store = ConfigStore(tmp_path)
    assert shield.set_gamebar_shield(store, True)[0]
    backup = dict(store.data['gamebar_shield_backup'])
    monkeypatch.setattr(shield, '_delete_reg_value', lambda *args: False)
    assert not shield.set_gamebar_shield(store, False)[0]
    assert store.data['gamebar_shield_enabled']
    assert store.data['gamebar_shield_backup'] == backup


def test_backup_is_persisted_before_first_system_write(tmp_path, monkeypatch):
    from gamepadstudio import gamebar_shield as shield
    from gamepadstudio.studio_core import ConfigStore
    store = ConfigStore(tmp_path)
    original = shield._write_reg_dword
    def write(*args):
        assert len(ConfigStore(tmp_path).data['gamebar_shield_backup']) == len(shield.TARGET_REG_ITEMS)
        return original(*args)
    monkeypatch.setattr(shield, '_write_reg_dword', write)
    assert shield.set_gamebar_shield(store, True)[0]
    assert shield.set_gamebar_shield(store, False)[0]
    assert store.data['gamebar_shield_backup'] == {}


def test_enable_failure_does_not_claim_success(tmp_path, monkeypatch):
    from gamepadstudio import gamebar_shield as shield
    from gamepadstudio.studio_core import ConfigStore
    store = ConfigStore(tmp_path)
    monkeypatch.setattr(shield, '_write_reg_dword', lambda *args: False)
    assert not shield.set_gamebar_shield(store, True)[0]
    assert not store.data['gamebar_shield_enabled']
    assert store.data['gamebar_shield_backup']


def test_gamebar_refresh_failure_is_not_silently_reported_as_success(monkeypatch):
    from gamepadstudio import gamebar_shield as shield
    monkeypatch.setattr(shield, '_refresh_gamebar_processes', lambda: False)
    ok, message, backup = enable_gamebar_shield()
    assert not ok and backup and '游戏栏未能关闭' in message


def test_gamebar_shield_lifecycle():
    initial_summary = get_gamebar_shield_summary()
    assert "active" in initial_summary
    assert "details" in initial_summary

    # 1. 开启屏蔽
    ok, msg, backup = enable_gamebar_shield()
    assert ok is True
    assert isinstance(backup, dict)
    assert is_gamebar_shield_active() is True

    summary_enabled = get_gamebar_shield_summary()
    assert summary_enabled["active"] is True
    assert summary_enabled["details"]["UseNexusForGameBarEnabled"] == 0
    assert summary_enabled["details"]["AppCaptureEnabled"] == 0
    assert summary_enabled["details"]["GameDVR_Enabled"] == 0

    # 2. 关闭并安全恢复原始状态
    ok_dis, msg_dis = disable_gamebar_shield(backup)
    assert ok_dis is True
    assert is_gamebar_shield_active() is False

    summary_disabled = get_gamebar_shield_summary()
    assert summary_disabled["active"] is False


def test_gamebar_shield_i18n():
    orig_lang = get_language()
    try:
        set_language("zh")
        zh_title = tr("屏蔽 Windows 截图与 Game Bar 弹窗")
        assert zh_title == "屏蔽 Windows 截图与 Game Bar 弹窗"
        zh_short = tr("屏蔽系统截图弹窗")
        assert zh_short == "屏蔽系统截图弹窗"

        set_language("en")
        en_title = tr("屏蔽 Windows 截图与 Game Bar 弹窗")
        assert "Suppress" in en_title or "Game Bar" in en_title
        en_short = tr("屏蔽系统截图弹窗")
        assert "Suppress" in en_short or "Screenshot" in en_short
    finally:
        set_language(orig_lang)

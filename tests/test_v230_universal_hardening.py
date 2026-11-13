"""Regression suite for GamePad Studio v2.3.0 universal compatibility & anti-lockout hardening."""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import gamepadstudio
from gamepadstudio import driver_setup, entry, hidhide
from gamepadstudio.i18n import set_language, tr
from gamepadstudio.kbm_mapper import NIKKI_PROFILE_NAME
from gamepadstudio.studio_core import ConfigStore


def test_version_230_synchronized_and_installer_hardened():
    project_root = Path(__file__).resolve().parents[1]
    assert gamepadstudio.__version__ == '2.3.0'

    pyproject = (project_root / 'pyproject.toml').read_text(encoding='utf-8')
    assert 'version = "2.3.0"' in pyproject

    spec_text = (project_root / 'GamePadStudio.spec').read_text(encoding='utf-8')
    assert "'CFBundleShortVersionString': '2.3.0'" in spec_text
    assert "'gamepadstudio.driver_setup'" in spec_text

    iss_text = (project_root / 'installer' / 'GamePadStudio.iss').read_text(encoding='utf-8')
    assert '#define AppVersion "2.3.0"' in iss_text
    assert '--recover-hidhide' in iss_text
    assert '--uncloak-all' in iss_text


def test_v230_bilingual_translation_parity():
    set_language('en')
    try:
        keys = [
            '以管理员身份重启',
            '一键安装虚拟手柄驱动',
            '一键安装 HidHide 驱动',
            '一键开启原始输入屏蔽',
            '重新检测并开启',
            '一键移除 Steam 冲突',
            '⚠ 检测到官方 HidHide 配置窗口正在运行并独占驱动句柄，请先关闭该窗口后再点击开启屏蔽。',
            '⚠ 检测到 Steam 位于 HidHide 白名单中，会导致游戏同时收到原按键与互换按键（双重输入）。',
        ]
        for key in keys:
            translated = tr(key)
            assert translated != key, f'Untranslated v2.3.0 UI string: {key}'
    finally:
        set_language('zh')


def test_should_auto_cloak_prevents_dead_controller_states(tmp_path, monkeypatch):
    store = ConfigStore(tmp_path)
    state = {
        'instance_id': 1,
        'family': 'xbox',
        'device_key': 'xbox:045e:0b13:pad1',
        'profile_key': 'xbox:045e:0b13:pad1',
        'available_buttons': list(range(15)),
        'axes': [0.0] * 6,
    }
    store.activate_controller(state)
    default_gp_prof = store.data['active_profile']

    # 1. Default unmapped 'gamepad' profile MUST NEVER auto-cloak (no virtual pad exists)
    monkeypatch.setattr('gamepadstudio.virtual_xinput.virtual_gamepad_backend_available', lambda: True)
    assert hidhide.should_auto_cloak_controller(store.data, state, mapping_enabled=True) is False

    # 2. Switching to 'kbm' profile DOES auto-cloak without needing ViGEmBus
    monkeypatch.setattr('gamepadstudio.virtual_xinput.virtual_gamepad_backend_available', lambda: False)
    store.data['active_profile'] = NIKKI_PROFILE_NAME
    assert hidhide.should_auto_cloak_controller(store.data, state, mapping_enabled=True) is True
    # Paused mapping uncloaks even in KBM mode
    assert hidhide.should_auto_cloak_controller(store.data, state, mapping_enabled=False) is False

    # 3. Enabling A<->B swap on 'gamepad' profile:
    #    - If ViGEmBus is NOT ready yet, MUST NOT cloak (otherwise controller goes completely dead!)
    store.data['active_profile'] = default_gp_prof
    store.apply_mapping_change({'op': 'swap_ab_xy', 'profile': default_gp_prof, 'pairs': [['0', '1']]}, state)
    assert hidhide.should_auto_cloak_controller(store.data, state, mapping_enabled=True) is False

    #    - Once ViGEmBus IS ready, auto-cloak engages cleanly!
    monkeypatch.setattr('gamepadstudio.virtual_xinput.virtual_gamepad_backend_available', lambda: True)
    assert hidhide.should_auto_cloak_controller(store.data, state, mapping_enabled=True) is True

    # 4. Toggling A<->B swap back OFF immediately disables auto-cloak so native controller works again!
    store.apply_mapping_change({'op': 'swap_ab_xy', 'profile': default_gp_prof, 'pairs': [['0', '1']]}, state)
    assert hidhide.should_auto_cloak_controller(store.data, state, mapping_enabled=True) is False


def test_dynamic_hidhide_cli_discovery_and_bootstrap_app_reg_fallback(tmp_path, monkeypatch):
    fake_cli = tmp_path / 'CustomDrive' / 'HidHide' / 'x64' / 'HidHideCLI.exe'
    fake_cli.parent.mkdir(parents=True)
    fake_cli.write_bytes(b'MZ' + b'\x00' * 128)
    fake_app = tmp_path / 'GamePadStudio.exe'
    fake_app.write_bytes(b'MZ' + b'\x00' * 128)

    monkeypatch.setattr(hidhide, 'sys', SimpleNamespace(platform='win32', executable=str(fake_app)))
    monkeypatch.setattr(hidhide, '_iter_registry_hidhide_dirs', lambda: [str(tmp_path / 'CustomDrive' / 'HidHide')])

    resolved = hidhide.resolve_hidhide_cli_path()
    assert resolved == str(fake_cli)

    cli_calls = []
    client = MagicMock()
    client.is_driver_installed.return_value = True
    client._send_ioctl.return_value = (True, b'\x01')  # Active filtering
    client.allow_current_input_app.return_value = False  # Direct IOCTL blocked

    monkeypatch.setattr(
        hidhide,
        '_run_hidhide_cli',
        lambda path, args, timeout=8.0: (cli_calls.append((path, list(args))) or 0),
    )
    ok, warning = hidhide.ensure_current_app_input_access(client)
    assert ok is True and warning == ''
    assert cli_calls == [(str(fake_cli), ['--app-reg', str(fake_app)])]


def test_orphan_hidhide_session_recovery_and_cli_entry(tmp_path, monkeypatch):
    win_sys = SimpleNamespace(**vars(sys))
    win_sys.platform = 'win32'
    monkeypatch.setattr(hidhide, 'sys', win_sys)

    dev_path = r'\\?\HID#VID_045E&PID_0B13#7&12345&0&0000'
    hidhide.record_hidhide_session({(0x045E, 0x0B13, dev_path)}, root=tmp_path)
    session_file = tmp_path / 'hidhide_session.json'
    assert session_file.is_file()

    uncloaked = []
    fake_client = MagicMock()
    fake_client.is_driver_installed.return_value = True
    fake_client.get_blacklist.return_value = []
    fake_client.uncloak_controller.side_effect = lambda v, p, **kw: (uncloaked.append((v, p, kw)) or (True, 'ok'))
    monkeypatch.setattr(hidhide, 'HidHideClient', lambda: fake_client)

    # 1. While Agent or UI is alive, orphan recovery MUST NOT disturb active session
    monkeypatch.setattr('gamepadstudio.ipc.is_endpoint_alive', lambda root, role: role == 'agent')
    ok, msg = hidhide.recover_orphan_hidhide_session(tmp_path)
    assert ok is True
    assert not uncloaked
    assert session_file.is_file()

    # 2. When neither Agent nor UI is alive (post-crash / next reboot), --recover-hidhide restores access!
    monkeypatch.setattr('gamepadstudio.ipc.is_endpoint_alive', lambda root, role: False)
    with patch.object(sys, 'argv', ['GamePadStudio', '--recover-hidhide', '--data-dir', str(tmp_path)]):
        rc = entry.run()
    assert rc == 0
    assert uncloaked == [(0x045E, 0x0B13, {'device_path': dev_path})]
    fake_client.set_active.assert_called_with(False)
    assert not session_file.exists()


def test_steam_whitelist_conflict_detection_and_one_click_driver_installer(tmp_path, monkeypatch):
    wl = [
        r'D:\GamePadStudio\GamePadStudio.exe',
        r'C:\Program Files (x86)\Steam\steam.exe',
        r'C:\Program Files (x86)\Steam\GameOverlayUI.exe',
    ]
    conflicts = driver_setup.find_steam_whitelist_conflicts(wl)
    assert len(conflicts) == 2

    saved_wl = []
    fake_hh = MagicMock()
    fake_hh.is_driver_installed.return_value = True
    fake_hh.get_whitelist.return_value = list(wl)
    fake_hh.set_whitelist.side_effect = lambda items: (saved_wl.extend(items) or True)

    monkeypatch.setattr(driver_setup, 'sys', SimpleNamespace(platform='win32'))
    ok, msg = driver_setup.remove_steam_from_hidhide_whitelist(fake_hh)
    assert ok is True
    assert saved_wl == [r'D:\GamePadStudio\GamePadStudio.exe']

    # Test one-click driver download & launch with mocked HTTP payload (valid MZ header >= 64 KiB)
    launched = []
    fake_exe_bytes = b'MZ' + (b'\x90' * 70000)

    class _FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()
            return False

    monkeypatch.setattr(
        driver_setup,
        'resolve_latest_driver_installer_url',
        lambda kind, timeout=4.0: ('https://example.invalid/ViGEmBus_Setup.exe', 'ViGEmBus_Setup.exe'),
    )
    monkeypatch.setattr(driver_setup.urllib.request, 'urlopen', lambda req, timeout=25.0: _FakeResp(fake_exe_bytes))
    res = driver_setup.download_and_launch_driver_installer(
        'vigembus',
        target_dir=tmp_path / 'drivers',
        opener=lambda p: launched.append(p),
    )
    assert res['ok'] is True and res['launched'] is True
    assert len(launched) == 1 and Path(launched[0]).name == 'ViGEmBus_Setup.exe'

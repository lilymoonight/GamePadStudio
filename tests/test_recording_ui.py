"""Recording monitor restrictions are independent of screenshot preferences."""
import json
from contextlib import nullcontext
from types import SimpleNamespace

from tests.test_device_scope_ui import workspace
from gamepadstudio.studio_core import ConfigStore


def displays(monkeypatch, refresh=(60, 144), hdr=(False, False)):
    physical = [dict(left=index * 1920, top=0, width=1920, height=1080,
                     device_name=f'DISPLAY{index + 1}', refresh_hz=hz, hdr_enabled=color,
                     sdr_white_nits=80, friendly_name=f'Display {index + 1}')
                for index, (hz, color) in enumerate(zip(refresh, hdr))]
    monitors = [dict(left=0, top=0, width=len(physical) * 1920, height=1080), *physical]
    monkeypatch.setattr('gamepadstudio.display_info.enumerate_displays', lambda: physical)
    monkeypatch.setattr('mss.mss', lambda: nullcontext(SimpleNamespace(monitors=monitors)))
    monkeypatch.setattr('gamepadstudio.replay_capture.create_replay_capture',
                        lambda: nullcontext(SimpleNamespace(monitors=monitors)))


def test_recording_all_disabled_for_mixed_refresh_but_screenshot_all_remains(workspace, monkeypatch):
    window, app = workspace
    displays(monkeypatch)
    window.refresh_recording_displays()
    all_index = window.replay_mode_combo.findData('all')
    assert not window.replay_mode_combo.model().item(all_index).isEnabled()
    assert '60' in window.recording_display_hint.text() and '144' in window.recording_display_hint.text()
    assert window.mode_combo.model().item(window.mode_combo.findData('all')).isEnabled()
    window.replay_mode_combo.setCurrentIndex(window.replay_mode_combo.findData('monitor_1'))
    window.mode_combo.setCurrentIndex(window.mode_combo.findData('all'))
    assert window.config['replay_capture_mode'] == 'monitor_1'
    assert window.config['capture_mode'] == 'all'


def test_saved_all_selection_cannot_enable_recording_after_display_change(workspace, monkeypatch):
    window, app = workspace
    displays(monkeypatch, refresh=(60, 59.94006))
    window.setting('replay_capture_mode', 'all')
    window.refresh_recording_displays()
    window.replay_toggle.setChecked(True)
    assert not window.replay_toggle.isChecked()
    assert not window.config['replay_buffer_enabled']
    assert window.config['replay_capture_mode'] == 'all'
    assert '刷新率不同' in window.notice.text()


def test_same_refresh_sdr_panorama_allowed_and_hdr_panorama_explained(workspace, monkeypatch):
    window, app = workspace
    displays(monkeypatch, refresh=(60, 60))
    window.refresh_recording_displays()
    assert window.replay_mode_combo.model().item(window.replay_mode_combo.findData('all')).isEnabled()
    displays(monkeypatch, refresh=(60, 60), hdr=(False, True))
    window.refresh_recording_displays()
    assert not window.replay_mode_combo.model().item(window.replay_mode_combo.findData('all')).isEnabled()
    assert 'HDR' in window.recording_display_hint.text()
    hdr_index = window.replay_mode_combo.findData('monitor_2')
    assert window.replay_mode_combo.model().item(hdr_index).isEnabled()
    assert 'HDR' in window.replay_mode_combo.itemText(hdr_index)


def test_old_recording_monitor_inherits_then_stays_separate_from_screenshots(tmp_path):
    store = ConfigStore(tmp_path)
    original = dict(store.data)
    original.pop('replay_capture_mode')
    original['capture_mode'] = 'all'
    store.path.write_text(json.dumps(original, ensure_ascii=False), encoding='utf-8')
    migrated = ConfigStore(tmp_path)
    assert migrated.data['replay_capture_mode'] == 'all'
    migrated.data['capture_mode'] = 'monitor_1'
    migrated.save()
    loaded = ConfigStore(tmp_path)
    assert loaded.data['capture_mode'] == 'monitor_1'
    assert loaded.data['replay_capture_mode'] == 'all'

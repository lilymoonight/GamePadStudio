"""Voice controls use fake services and never send desktop input."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from gamepadstudio import voice_entry_ui
from gamepadstudio.i18n import get_language_preference, init_language
from tests.test_platform_ui import mac_workspace


class FakeVoiceService:
    def __init__(self):
        self.calls = []
        self.rows = [{'bundle_id': 'com.openai.codex', 'pid': 321, 'name': 'Codex', 'generation': 'a'}]
        self.result = {'ok': True, 'code': 'idle', 'message': '先定位目标应用的输入框，再检查语音入口。',
                       'phase': 'idle', 'target': {}, 'input_focused': False,
                       'dictation_supported': False, 'recording': False, 'stop_required': False,
                       'draft': '', 'manual_send': True}
        self.activate_result = None

    def targets(self):
        self.calls.append(('targets',))
        return list(self.rows)

    def status(self):
        self.calls.append(('status',))
        return dict(self.result)

    def activate(self, bundle_id):
        self.calls.append(('activate', bundle_id))
        self.result.update(target={'bundle_id': bundle_id}, input_focused=True,
                           dictation_supported=True, message='输入框已定位')
        if self.activate_result:
            self.result.update(self.activate_result)
        return dict(self.result)

    def begin(self, bundle_id):
        self.calls.append(('begin', bundle_id))
        self.result.update(phase='recording', recording=True, stop_required=True, message='录音中')
        return dict(self.result)

    def end(self):
        self.calls.append(('end',))
        self.result.update(phase='idle', recording=False, stop_required=False,
                           message='已结束，请手动发送', draft='识别的文字')
        return dict(self.result)

    def fill_test_text(self, bundle_id, text):
        self.calls.append(('fill_test_text', bundle_id, text))
        return {**self.result, 'draft': text, 'message': '已填入草稿，请手动发送'}

    def close(self):
        self.calls.append(('close',))

    def send(self, *args):
        pytest.fail('Voice tests must not submit messages')


@pytest.fixture
def voice_panel():
    app = QApplication.instance() or QApplication([])
    previous = get_language_preference()
    init_language('zh')
    service = FakeVoiceService()
    panel = voice_entry_ui.VoiceEntryGroup(service=service, platform='darwin')
    panel.resize(560, 650)
    panel.show()
    QTest.qWait(5)
    yield SimpleNamespace(app=app, panel=panel, service=service)
    panel.close_service()
    panel.hide()
    panel.deleteLater()
    app.sendPostedEvents(panel, QEvent.DeferredDelete)
    init_language(previous)


def test_initial_voice_panel_only_observes_and_defaults_to_empty_text(voice_panel):
    p, s = voice_panel.panel, voice_panel.service
    assert s.calls == [('targets',), ('status',)]
    assert p.test_text.text() == ''
    assert not p.fill_button.isEnabled() and not p.hold_button.isEnabled()
    assert p.activate_button.isEnabled()
    assert 'PID 321' in p.target_row.subtitle_label.text()
    assert p.target_combo.currentData() == 'com.openai.codex'


def test_changing_target_and_refreshing_never_activates_or_fills(voice_panel):
    p, s = voice_panel.panel, voice_panel.service
    p.target_combo.setCurrentIndex(1)
    assert p.selected_target() == 'com.google.antigravity'
    assert '未运行' in p.target_row.subtitle_label.text()
    s.rows.append({'bundle_id': 'com.google.antigravity', 'pid': 999, 'name': 'Google Antigravity'})
    p.refresh_button.click()
    assert 'PID 999' in p.target_row.subtitle_label.text()
    assert all(call[0] in ('targets', 'status') for call in s.calls)


def test_test_text_is_written_only_after_explicit_fill_click(voice_panel):
    p, s = voice_panel.panel, voice_panel.service
    p.test_text.setText('保留这段测试文字\n不发送')
    assert not any(call[0] == 'fill_test_text' for call in s.calls)
    p.fill_button.click()
    assert s.calls[-1] == ('fill_test_text', 'com.openai.codex', p.test_text.text())
    assert '手动发送' in p.status_label.text()
    assert s.result['manual_send'] is True


def test_hold_press_and_release_begin_then_end_without_send(voice_panel):
    p, s = voice_panel.panel, voice_panel.service
    p.activate_button.click()
    assert p.hold_button.isEnabled()
    QTest.mousePress(p.hold_button, Qt.LeftButton)
    assert s.calls[-1] == ('begin', 'com.openai.codex')
    assert p._recording and p.voice_timer.isActive()
    assert not p.target_combo.isEnabled() and not p.fill_button.isEnabled()
    QTest.mouseRelease(p.hold_button, Qt.LeftButton)
    assert s.calls[-1] == ('end',)
    assert not p._recording and not p.voice_timer.isActive()
    assert '手动发送' in p.status_label.text()


def test_lost_release_is_stopped_at_test_hold_limit(voice_panel, monkeypatch):
    p, s = voice_panel.panel, voice_panel.service
    now = [100.]
    monkeypatch.setattr(voice_entry_ui, 'monotonic', lambda: now[0])
    p.activate_button.click()
    p.begin_voice()
    now[0] = 159.9
    p._observe_voice()
    assert p._stop_required and not any(call[0] == 'end' for call in s.calls)
    now[0] = 160.
    p._observe_voice()
    assert s.calls[-1] == ('end',)
    assert p._release_requested and not p._stop_required and not p.voice_timer.isActive()
    assert '60 秒测试上限' in p.status_label.text()
    assert '手柄语音' in p.limit_hint.text()


def test_timeout_includes_start_wait_and_keeps_ownership_until_stop_confirmed(voice_panel, monkeypatch):
    p, s = voice_panel.panel, voice_panel.service
    now = [0.]
    monkeypatch.setattr(voice_entry_ui, 'monotonic', lambda: now[0])
    p.activate_button.click()
    def begin(bundle_id):
        s.calls.append(('begin', bundle_id))
        s.result.update(phase='starting', recording=False, stop_required=True)
        return dict(s.result)
    def end():
        s.calls.append(('end',))
        if s.result['phase'] == 'recording':
            s.result.update(phase='stopping', message='正在确认停止')
        return dict(s.result)
    monkeypatch.setattr(s, 'begin', begin)
    monkeypatch.setattr(s, 'end', end)
    monkeypatch.setattr(s, 'close', lambda: dict(s.result))
    p.begin_voice()
    now[0] = 60.
    p._observe_voice()
    assert s.calls[-1] == ('end',)
    assert p._release_requested and p._stop_required and p.voice_timer.isActive()
    assert not p._recording and p._phase == 'starting'
    s.result.update(phase='recording', recording=True)
    p._observe_voice()
    assert p._phase == 'stopping' and p._stop_required
    assert p.close_service() is False
    assert not p._closed and p.voice_timer.isActive()
    s.result.update(phase='idle', recording=False, stop_required=False)
    p._observe_voice()
    assert not p._stop_required and not p.voice_timer.isActive()
    assert p.close_service() is True


def test_slow_start_already_past_limit_is_stopped_when_begin_returns(voice_panel, monkeypatch):
    p, s = voice_panel.panel, voice_panel.service
    now = [0.]
    monkeypatch.setattr(voice_entry_ui, 'monotonic', lambda: now[0])
    p.activate_button.click()
    original = s.begin
    def delayed(bundle_id):
        now[0] = 61.
        return original(bundle_id)
    monkeypatch.setattr(s, 'begin', delayed)
    p.begin_voice()
    assert s.calls[-2:] == [('begin', 'com.openai.codex'), ('end',)]
    assert not p._stop_required and p._release_requested


def test_lost_mouse_grab_requests_verified_stop(voice_panel):
    p, s = voice_panel.panel, voice_panel.service
    p.activate_button.click()
    p.begin_voice()
    QApplication.sendEvent(p.hold_button, QEvent(QEvent.UngrabMouse))
    assert s.calls[-1] == ('end',)
    assert p._release_requested and not p._stop_required
    assert '鼠标抓取已丢失' in p.status_label.text()


def test_mouse_grab_loss_during_native_activation_defers_stop_until_start_returns(voice_panel, monkeypatch):
    p, s = voice_panel.panel, voice_panel.service
    p.activate_button.click()
    original = s.begin
    def activates_other_app(bundle_id):
        QApplication.sendEvent(p.hold_button, QEvent(QEvent.UngrabMouse))
        assert p._release_requested
        assert not any(call[0] == 'end' for call in s.calls)
        return original(bundle_id)
    monkeypatch.setattr(s, 'begin', activates_other_app)
    p.begin_voice()
    assert s.calls[-2:] == [('begin', 'com.openai.codex'), ('end',)]
    assert not p._stop_required


def test_window_deactivation_does_not_cancel_normal_target_activation(voice_panel):
    p, s = voice_panel.panel, voice_panel.service
    p.activate_button.click()
    p.begin_voice()
    QApplication.sendEvent(p.hold_button, QEvent(QEvent.WindowDeactivate))
    assert p._stop_required and not any(call[0] == 'end' for call in s.calls)


def test_missing_verified_voice_interface_explains_disabled_hold(voice_panel):
    p, s = voice_panel.panel, voice_panel.service
    s.activate_result = {'dictation_supported': False, 'code': 'unsupported',
                         'message': '目标应用没有可验证的开始与停止语音控件'}
    p.activate_button.click()
    assert not p.hold_button.isEnabled()
    assert '没有可验证' in p.status_label.text()
    p.begin_voice()
    assert not any(call[0] == 'begin' for call in s.calls)


def test_permission_failure_offers_explicit_settings_action(voice_panel, monkeypatch):
    p, s = voice_panel.panel, voice_panel.service
    links = []
    monkeypatch.setattr(voice_entry_ui.QDesktopServices, 'openUrl', lambda url: links.append(url.toString()))
    s.activate_result = {'ok': False, 'dictation_supported': False, 'code': 'accessibility_permission',
                         'message': '请在辅助功能授权当前运行程序'}
    p.activate_button.click()
    assert links == [] and p.permission_button.isVisible()
    assert not p.hold_button.isEnabled()
    p.permission_button.click()
    assert links == [voice_entry_ui.ACCESSIBILITY_URL]


def test_release_during_starting_stops_after_recording_acknowledgement(voice_panel, monkeypatch):
    p, s = voice_panel.panel, voice_panel.service
    p.activate_button.click()

    def begin(bundle_id):
        s.calls.append(('begin', bundle_id))
        s.result.update(recording=False, stop_required=True, phase='starting')
        return dict(s.result)

    original_end = s.end
    def end():
        if s.result['phase'] == 'starting':
            s.calls.append(('end_wait_for_start',))
            return dict(s.result)
        return original_end()

    monkeypatch.setattr(s, 'begin', begin)
    monkeypatch.setattr(s, 'end', end)
    p.begin_voice()
    assert not p._recording and p._stop_required
    p.end_voice()
    assert s.calls[-1] == ('end_wait_for_start',)
    s.result.update(recording=True, phase='recording')
    p._observe_voice()
    assert s.calls[-2:] == [('status',), ('end',)]
    assert not p._stop_required and not p.voice_timer.isActive()


def test_close_stops_active_voice_once_and_cancels_pending_poll(voice_panel):
    p, s = voice_panel.panel, voice_panel.service
    p.activate_button.click()
    p.begin_voice()
    p.close_service()
    p.close_service()
    p._observe_voice()
    assert s.calls[-2:] == [('end',), ('close',)]
    assert not p.voice_timer.isActive()


def test_external_recording_is_observed_without_taking_over_stop(voice_panel):
    p, s = voice_panel.panel, voice_panel.service
    p.activate_button.click()
    s.result.update(recording=True, stop_required=False, phase='ready')
    p.refresh_status()
    assert not p.hold_button.isEnabled() and not p.stop_button.isVisible()
    assert '目标应用内停止' in p.status_label.text()
    p.end_voice()
    assert not any(call[0] == 'end' for call in s.calls)
    p.close_service()
    assert s.calls[-1] == ('close',)
    assert not any(call[0] == 'end' for call in s.calls)


def test_close_waits_until_native_stop_is_confirmed(voice_panel, monkeypatch):
    p, s = voice_panel.panel, voice_panel.service
    p.activate_button.click()
    p.begin_voice()
    def pending_stop():
        s.result.update(phase='stopping', stop_required=True, message='请确认目标应用已停止录音')
        return dict(s.result)
    monkeypatch.setattr(s, 'end', pending_stop)
    monkeypatch.setattr(s, 'close', lambda: dict(s.result) if s.result['phase'] == 'idle' else pending_stop())
    assert p.close_service() is False
    assert not p._closed and p.voice_timer.isActive()
    assert '确认' in p.status_label.text()
    s.result.update(recording=False, stop_required=False, phase='idle')
    p._observe_voice()
    assert p.close_service() is True


@pytest.fixture
def workspace_voice_service(monkeypatch):
    service = FakeVoiceService()
    monkeypatch.setattr(voice_entry_ui, 'create_voice_service', lambda: service)
    return service


@pytest.fixture
def voice_workspace(workspace_voice_service, mac_workspace):
    return mac_workspace


def test_voice_settings_are_independent_of_saved_mapping_config(voice_workspace, workspace_voice_service):
    import copy
    w = voice_workspace.window
    before = copy.deepcopy(w.config)
    assert w.voice_entry_group in w.settings_groups
    p = w.voice_entry_group
    p.target_combo.setCurrentIndex(1)
    p.activate_button.click()
    p.test_text.setText('explicit draft only')
    p.fill_button.click()
    assert workspace_voice_service.calls[-1] == ('fill_test_text', 'com.google.antigravity', 'explicit draft only')
    assert w.config == before


def test_window_close_waits_for_owned_voice_stop(voice_workspace, workspace_voice_service, monkeypatch):
    w = voice_workspace.window
    p, s = w.voice_entry_group, workspace_voice_service
    p.activate_button.click()
    p.begin_voice()
    def pending():
        s.result.update(phase='stopping', stop_required=True, message='请在目标应用中确认停止')
        return dict(s.result)
    monkeypatch.setattr(s, 'end', pending)
    monkeypatch.setattr(s, 'close', lambda: dict(s.result) if s.result['phase'] == 'idle' else pending())
    w.show()
    w.close()
    assert not w.closed and w.stack.currentIndex() == 4
    assert '确认停止' in p.status_label.text()
    s.result.update(phase='idle', recording=False, stop_required=False)
    p._observe_voice()
    w.close()
    assert w.closed and p._closed


def test_quit_app_respects_pending_voice_stop_before_quitting_application(
        voice_workspace, workspace_voice_service, monkeypatch):
    w = voice_workspace.window
    p, s = w.voice_entry_group, workspace_voice_service
    quits = []
    monkeypatch.setattr(QApplication, 'quit', lambda: quits.append(True))
    p.activate_button.click()
    p.begin_voice()
    def pending():
        s.result.update(phase='stopping', stop_required=True, message='请在目标应用中确认停止')
        return dict(s.result)
    monkeypatch.setattr(s, 'end', pending)
    monkeypatch.setattr(s, 'close', lambda: dict(s.result) if s.result['phase'] == 'idle' else pending())
    w.show()
    w.quit_app()
    assert quits == []
    assert not w.closed and not p._closed
    assert w.isVisible() and p.voice_timer.isActive()
    s.result.update(phase='idle', recording=False, stop_required=False)
    p._observe_voice()
    w.quit_app()
    assert quits == [True]
    assert w.closed and p._closed and not p.voice_timer.isActive()


def test_windows_voice_controls_are_disabled_without_creating_native_service():
    app = QApplication.instance() or QApplication([])
    p = voice_entry_ui.VoiceEntryGroup(platform='win32', service_factory=lambda: pytest.fail('Unexpected native service'))
    try:
        assert not p.activate_button.isEnabled()
        assert not p.hold_button.isEnabled()
        assert not p.refresh_button.isEnabled()
        assert 'macOS' in p.status_label.text()
    finally:
        p.close_service()
        p.deleteLater()
        app.sendPostedEvents(p, QEvent.DeferredDelete)


def test_missing_optional_backend_can_be_retried_without_input():
    app = QApplication.instance() or QApplication([])
    def missing():
        raise ImportError('missing optional voice backend')
    p = voice_entry_ui.VoiceEntryGroup(platform='darwin', service_factory=missing)
    try:
        assert 'missing optional' in p.status_label.text()
        assert p.refresh_button.isEnabled() and not p.activate_button.isEnabled()
        s = FakeVoiceService()
        p.service_factory = lambda: s
        p.refresh_button.click()
        assert s.calls == [('targets',), ('status',)]
        assert p.activate_button.isEnabled()
    finally:
        p.close_service()
        p.deleteLater()
        app.sendPostedEvents(p, QEvent.DeferredDelete)

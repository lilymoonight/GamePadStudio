"""Voice workflow tests use only in-memory apps and observable fake controls."""
from types import SimpleNamespace

import pytest

from gamepadstudio.voice_entry import TARGETS, VoiceEntryService


CODEX = 'com.openai.codex'
ANTIGRAVITY = 'com.google.antigravity'


class TargetError(RuntimeError):
    def __init__(self, code, message, action_requested=None):
        super().__init__(message)
        self.code = code
        self.action_requested = action_requested


class FakeTarget:
    def __init__(self, bundle_id, app):
        self.bundle_id, self.pid, self.generation = bundle_id, app['pid'], app['generation']
        self.closed = 0

    def close(self):
        self.closed += 1


class FakeApps:
    def __init__(self):
        self.calls = []
        self.focus_succeeds = True
        self.apps = {
            key: dict(pid=100 + index, generation=f'process:{index}', running=True, frontmost=False,
                      input_focused=False, target_available=True, draft='原有草稿\n')
            for index, key in enumerate(TARGETS)
        }

    def list_running_targets(self):
        self.calls.append(('list',))
        return [dict(bundle_id=key, name='display names are not trusted', pid=app['pid'], generation=app['generation'])
                for key, app in self.apps.items() if app['running']]

    def _validate(self, target):
        app = self.apps[target.bundle_id]
        if app['generation'] != target.generation:
            raise TargetError('stale_target', '目标进程已变化，请重新定位')
        return app

    def locate(self, bundle_id):
        self.calls.append(('locate', bundle_id))
        app = self.apps[bundle_id]
        if not app['running']:
            raise TargetError('not_running', '目标应用未运行')
        return FakeTarget(bundle_id, app)

    def wake(self, bundle_id):
        if not self.apps[bundle_id]['running']:
            raise TargetError('not_running', '目标应用未运行')
        self.calls.append(('wake', bundle_id))
        return {'activation_requested': True}

    def activate(self, target):
        app = self._validate(target)
        self.calls.append(('activate', target.bundle_id, target.generation))
        for item in self.apps.values():
            item['frontmost'] = item['input_focused'] = False
        app['frontmost'] = True
        app['input_focused'] = self.focus_succeeds

    def inspect(self, target):
        self.calls.append(('inspect', target.bundle_id))
        return dict(self._validate(target))

    def append_text(self, target, text):
        app = self._validate(target)
        if not app['frontmost'] or not app['input_focused']:
            raise TargetError('focus_changed', '输入焦点已变化')
        self.calls.append(('append', target.bundle_id, text))
        app['draft'] += text


class FakeDictation:
    def __init__(self, apps):
        self.apps = apps
        self.calls = []
        self.state = 'idle'
        self.supported = True
        self.start_delayed = False
        self.stop_delayed = False
        self.processing = False
        self.start_error = None
        self.stop_error = None

    def inspect(self, target):
        self.calls.append(('inspect', target.bundle_id))
        return {'supported': self.supported, 'state': self.state,
                'reason': '' if self.supported else '没有可靠的开始和停止听写控件'}

    def start(self, target):
        self.calls.append(('start', target.bundle_id))
        if not self.start_delayed:
            self.state = 'recording'
        if self.start_error:
            raise self.start_error

    def stop(self, target):
        self.calls.append(('stop', target.bundle_id))
        if self.stop_error:
            raise self.stop_error
        if not self.stop_delayed:
            self.state = 'processing' if self.processing else 'idle'
            self.apps.apps[target.bundle_id]['draft'] += '听写文字'


@pytest.fixture
def workflow():
    apps = FakeApps()
    native = FakeDictation(apps)
    return VoiceEntryService(apps, native), apps, native


def actions(calls):
    return [call for call in calls if call[0] not in ('inspect', 'list', 'locate')]


def test_initialization_targets_and_status_are_read_only(workflow):
    service, apps, native = workflow
    assert apps.calls == native.calls == []
    assert [row['name'] for row in service.targets()] == ['Codex', 'Google Antigravity']
    status = service.status()
    assert status['phase'] == 'idle' and not status['stop_required']
    assert status['manual_send'] and status['target'] == {}
    assert actions(apps.calls) == actions(native.calls) == []


def test_targets_only_include_supported_running_bundle_ids(workflow):
    service, apps, _ = workflow
    apps.apps[CODEX]['running'] = False
    apps.apps['com.unrelated.editor'] = dict(apps.apps[ANTIGRAVITY])
    assert [row['bundle_id'] for row in service.targets()] == [ANTIGRAVITY]


@pytest.mark.parametrize('bundle_id', [CODEX, ANTIGRAVITY])
def test_activate_observes_running_window_and_input_without_starting_audio(workflow, bundle_id):
    service, apps, native = workflow
    result = service.activate(bundle_id)
    assert result['ok'] and result['phase'] == 'ready'
    assert result['target']['bundle_id'] == bundle_id and result['input_focused']
    assert result['draft'] == '原有草稿\n'
    assert result['steps'] == ['application_awakened', 'composer_located', 'composer_focused']
    assert actions(native.calls) == []
    before = actions(apps.calls)
    service.status()
    assert actions(apps.calls) == before


def test_activate_rejects_missing_apps_arbitrary_targets_and_unconfirmed_focus(workflow):
    service, apps, _ = workflow
    assert service.activate('com.unrelated.editor')['code'] == 'unsupported_target'
    assert apps.calls == []
    apps.apps[CODEX]['running'] = False
    assert service.activate(CODEX)['code'] == 'not_running'
    assert actions(apps.calls) == []
    apps.apps[CODEX]['running'] = True
    apps.focus_succeeds = False
    result = service.activate(CODEX)
    assert not result['ok'] and result['code'] == 'input_not_focused'


def test_unsupported_dictation_has_an_explicit_reason_and_never_starts(workflow):
    service, apps, native = workflow
    native.supported = False
    focused = service.activate(CODEX)
    assert focused['ok'] and not focused['dictation_supported']
    assert '没有可靠' in focused['message']
    result = service.begin(CODEX)
    assert not result['ok'] and result['code'] == 'dictation_unsupported'
    assert result['phase'] == 'unsupported' and not result['stop_required']
    assert actions(native.calls) == []


def test_voice_memo_diagnostic_is_not_treated_as_text_dictation(workflow, monkeypatch):
    service, _, native = workflow
    monkeypatch.setattr(native, 'inspect', lambda target: {
        'supported': False, 'state': 'unknown', 'mode': 'native_voice_memo',
        'reason': '仅观察到 Record voice memo，尚未确认文字听写及停止控件'})
    focused = service.activate(ANTIGRAVITY)
    assert focused['ok'] and focused['native_voice_mode'] == 'native_voice_memo'
    assert not focused['dictation_supported'] and 'Record voice memo' in focused['message']
    assert service.begin(ANTIGRAVITY)['code'] == 'dictation_unsupported'
    assert not service.status()['stop_required']
    assert actions(native.calls) == []


def test_partial_native_interface_is_unsupported_instead_of_guessing_shortcuts():
    apps = FakeApps()
    apps.dictation_status = lambda target: {'supported': True, 'state': 'idle'}
    apps.start_dictation = lambda target: pytest.fail('No reliable stop interface')
    service = VoiceEntryService(apps)
    result = service.begin(CODEX)
    assert result['code'] == 'dictation_unsupported' and not result['stop_required']


def test_native_target_backend_methods_are_the_default_dictation_bridge():
    apps = FakeApps()
    native = FakeDictation(apps)
    apps.dictation_status, apps.start_dictation, apps.stop_dictation = native.inspect, native.start, native.stop
    service = VoiceEntryService(apps)
    assert service.begin(CODEX)['recording']
    assert service.end()['code'] == 'dictation_stopped'
    assert actions(native.calls) == [('start', CODEX), ('stop', CODEX)]


@pytest.mark.parametrize('bundle_id', [CODEX, ANTIGRAVITY])
def test_hold_release_stops_observed_recording_and_preserves_unsent_draft(workflow, bundle_id):
    service, apps, native = workflow
    started = service.begin(bundle_id)
    assert started['ok'] and started['phase'] == 'recording' and started['stop_required']
    result = service.end()
    assert result['ok'] and result['phase'] == 'ready' and not result['recording']
    assert not result['stop_required'] and result['draft'] == '原有草稿\n听写文字'
    assert result['manual_send']
    assert actions(native.calls) == [('start', bundle_id), ('stop', bundle_id)]
    assert all(call[0] in ('list', 'locate', 'wake', 'activate', 'inspect', 'append') for call in apps.calls)
    service.end()
    assert len(actions(native.calls)) == 2


def test_external_recording_is_not_adopted_or_stopped(workflow):
    service, _, native = workflow
    native.state = 'recording'
    result = service.begin(CODEX)
    assert not result['ok'] and result['code'] == 'dictation_not_idle'
    assert not result['stop_required']
    service.end()
    assert actions(native.calls) == []


def test_busy_session_blocks_new_targets_test_text_and_duplicate_start(workflow):
    service, apps, native = workflow
    service.begin(CODEX)
    before = actions(apps.calls)
    for operation in (lambda: service.activate(ANTIGRAVITY), lambda: service.begin(CODEX),
                      lambda: service.fill_test_text(ANTIGRAVITY, '测试')):
        assert operation()['code'] == 'recording_busy'
    assert actions(apps.calls) == before
    assert actions(native.calls) == [('start', CODEX)]


def test_release_during_start_waits_for_observed_recording_without_blind_stop(workflow):
    service, _, native = workflow
    native.start_delayed = True
    result = service.begin(CODEX)
    assert result['phase'] == 'starting' and not result['recording'] and result['stop_required']
    pending = service.end()
    assert pending['phase'] == 'starting' and pending['stop_required']
    assert actions(native.calls) == [('start', CODEX)]
    native.state = 'recording'
    confirmed = service.status()
    assert confirmed['ok'] and confirmed['phase'] == 'recording'
    assert confirmed['code'] == 'dictation_recording'
    assert '正在听写' in confirmed['message']
    assert confirmed['steps'].count('dictation_recording_observed') == 1
    assert service.status()['steps'].count('dictation_recording_observed') == 1
    assert service.end()['phase'] == 'ready'
    assert actions(native.calls) == [('start', CODEX), ('stop', CODEX)]


def test_delayed_recording_confirmation_clears_a_prior_start_observation_error(workflow):
    service, _, native = workflow
    native.start_delayed = True
    native.start_error = TargetError('dictation_unconfirmed', '尚未观察到录音状态')
    pending = service.begin(CODEX)
    assert not pending['ok'] and pending['stop_required']
    native.state = 'recording'
    confirmed = service.status()
    assert confirmed['ok'] and confirmed['code'] == 'dictation_recording'
    assert confirmed['phase'] == 'recording' and '正在听写' in confirmed['message']
    assert confirmed['steps'].count('dictation_recording_observed') == 1
    assert service.status()['steps'].count('dictation_recording_observed') == 1
    assert actions(native.calls) == [('start', CODEX)]
    assert service.close()['phase'] == 'closed'


def test_delayed_stop_is_requested_once_and_status_only_observes_completion(workflow):
    service, _, native = workflow
    service.begin(CODEX)
    native.stop_delayed = True
    assert service.end()['phase'] == 'stopping'
    service.end(); service.status()
    assert actions(native.calls) == [('start', CODEX), ('stop', CODEX)]
    native.state = 'idle'
    result = service.status()
    assert result['phase'] == 'ready' and not result['stop_required']


def test_processing_can_complete_after_microphone_stops_without_submitting(workflow):
    service, _, native = workflow
    service.begin(CODEX)
    native.processing = True
    result = service.end()
    assert result['phase'] == 'processing' and not result['recording'] and not result['stop_required']
    native.state = 'idle'
    assert service.status()['code'] == 'transcription_ready'
    assert actions(native.calls) == [('start', CODEX), ('stop', CODEX)]


def test_stop_after_focus_change_returns_to_the_original_verified_target(workflow):
    service, apps, native = workflow
    service.begin(CODEX)
    apps.apps[CODEX]['frontmost'] = False
    apps.apps[ANTIGRAVITY]['frontmost'] = True
    assert service.end()['code'] == 'dictation_stopped'
    assert [call[1] for call in actions(apps.calls) if call[0] == 'activate'] == [CODEX, CODEX]
    assert actions(native.calls) == [('start', CODEX), ('stop', CODEX)]


def test_stale_process_generation_cannot_receive_text_or_stop_another_session(workflow):
    service, apps, native = workflow
    service.begin(CODEX)
    apps.apps[CODEX]['generation'] = 'reused-pid:new-process'
    result = service.end()
    assert not result['ok'] and result['code'] == 'stale_target' and result['stop_required']
    assert actions(native.calls) == [('start', CODEX)]


def test_start_failure_after_recording_started_remains_owned_for_cleanup(workflow):
    service, _, native = workflow
    native.start_error = TargetError('start_confirmation_failed', '等待确认失败')
    result = service.begin(CODEX)
    assert not result['ok'] and result['recording'] and result['stop_required']
    assert service.end()['code'] == 'dictation_stopped'


def test_failed_stop_remains_owned_and_a_verified_control_can_be_retried(workflow):
    service, _, native = workflow
    service.begin(CODEX)
    native.stop_error = TargetError('stop_failed', '未尝试操作，控件暂时不可用', action_requested=False)
    assert service.end()['stop_required']
    native.stop_error = None
    assert service.end()['code'] == 'dictation_stopped'


@pytest.mark.parametrize('action_requested', [True, None])
def test_possible_stop_action_is_not_retried_until_idle_is_observed(workflow, action_requested):
    service, _, native = workflow
    service.begin(CODEX)
    native.stop_error = TargetError('stop_timeout', '停止操作结果尚未确认', action_requested=action_requested)
    assert service.end()['stop_required']
    assert service.end()['stop_required'] and service.status()['stop_required']
    assert actions(native.calls) == [('start', CODEX), ('stop', CODEX)]
    native.state = 'idle'
    assert not service.status()['stop_required']
    assert service.close()['phase'] == 'closed'


def test_closed_target_clears_only_its_owned_session_without_touching_other_apps(workflow):
    service, apps, native = workflow
    service.begin(CODEX)
    apps.apps[CODEX]['running'] = False
    result = service.status()
    assert result['code'] == 'target_closed' and not result['stop_required']
    assert actions(native.calls) == [('start', CODEX)]


@pytest.mark.parametrize('code', ['permission_required', 'composer_changed', 'ax_error'])
def test_unavailable_accessibility_is_not_mistaken_for_a_stopped_microphone(workflow, monkeypatch, code):
    service, apps, native = workflow
    service.begin(CODEX)
    original = apps.inspect
    monkeypatch.setattr(apps, 'inspect', lambda target: {
        'running': False, 'code': code, 'reason': '无法确认录音状态'})
    observed = service.status()
    assert observed['code'] == code and observed['stop_required'] and not observed['ok']
    assert not service.close()['ok']
    assert service._target is not None
    assert actions(native.calls) == [('start', CODEX)]
    monkeypatch.setattr(apps, 'inspect', original)
    assert service.close()['phase'] == 'closed'


def test_text_test_keeps_unicode_and_newlines_as_text_and_preserves_existing_draft(workflow):
    service, apps, native = workflow
    result = service.fill_test_text(ANTIGRAVITY, '测试文字\n$(literal text)')
    assert result['ok'] and result['code'] == 'text_filled' and result['manual_send']
    assert result['draft'] == '原有草稿\n测试文字\n$(literal text)'
    assert actions(native.calls) == []
    assert actions(apps.calls)[-1] == ('append', ANTIGRAVITY, '测试文字\n$(literal text)')


@pytest.mark.parametrize('text', ['', None, 123, 'bad\0text', 'x' * 8193])
def test_invalid_test_text_is_rejected_before_focusing_any_app(workflow, text):
    service, apps, native = workflow
    assert service.fill_test_text(CODEX, text)['code'] == 'invalid_text'
    assert apps.calls == native.calls == []


def test_close_stops_an_owned_session_and_releases_target_once(workflow):
    service, _, native = workflow
    service.begin(CODEX)
    target = service._target
    assert service.close()['phase'] == 'closed'
    assert target.closed == 1
    service.close()
    assert target.closed == 1 and service.targets() == []
    assert actions(native.calls) == [('start', CODEX), ('stop', CODEX)]
    assert service.activate(CODEX)['code'] == 'closed'


def test_close_does_not_discard_an_unconfirmed_recording(workflow):
    service, _, native = workflow
    service.begin(CODEX)
    native.state = 'unknown'
    target = service._target
    result = service.close()
    assert not result['ok'] and result['code'] == 'dictation_stop_required'
    assert result['stop_required'] and not target.closed
    native.state = 'recording'
    assert service.close()['phase'] == 'closed'
    assert target.closed == 1


def test_default_factory_does_not_enumerate_or_operate_apps_until_explicit_calls(monkeypatch):
    import sys
    apps = FakeApps()
    monkeypatch.setitem(sys.modules, 'gamepadstudio.macos_app_target', SimpleNamespace(MacAppTarget=lambda: apps))
    service = VoiceEntryService()
    assert apps.calls == []
    assert service.status()['phase'] == 'idle'
    assert apps.calls == []


@pytest.mark.parametrize('switch_app', [False, True])
def test_native_adapter_bridge_stops_after_dictation_replaces_the_composer(switch_app):
    from gamepadstudio.macos_app_target import MacAppTarget
    from tests.test_macos_app_target import FakeNative

    native = FakeNative()
    service = VoiceEntryService(MacAppTarget(native))
    try:
        assert service.begin(CODEX)['recording']
        # A recording overlay may move focus and remove composer semantics.
        # Stop must still use the original window's explicit Stop control.
        native.app.attributes['AXFocusedUIElement'] = native.voice
        native.composer.attributes['AXDescription'] = 'Recording overlay'
        if switch_app:
            native.front = dict(bundle_id='com.unrelated.editor', pid=90, generation=4.0)
        after_start = len(native.calls)
        observed = service.status()
        assert observed['running'] and observed['recording'] and observed['stop_required']
        result = service.end()
        assert result['ok'] and result['code'] == 'dictation_stopped'
        assert not result['stop_required']
        assert [call for call in native.calls if call[0] == 'action' and call[2] == 'AXPress'] == [
            ('action', 'voice', 'AXPress'), ('action', 'voice', 'AXPress')]
        assert native.composer.attributes['AXValue'] == '草稿😀'
        assert not any(call[:3] == ('set', 'composer', 'AXFocused')
                       for call in native.calls[after_start:])
        if switch_app:
            assert native.front['bundle_id'] == CODEX
            assert ('action', 'window', 'AXRaise') in native.calls[after_start:]
    finally:
        service.close()
    assert all(ref.closed for ref in native.references)


def test_native_adapter_bridge_appends_unicode_without_overwriting_or_submitting():
    from gamepadstudio.macos_app_target import MacAppTarget
    from tests.test_macos_app_target import FakeNative

    native = FakeNative()
    service = VoiceEntryService(MacAppTarget(native))
    try:
        result = service.fill_test_text(CODEX, '测试文字😀\n下一行')
        assert result['ok'] and result['manual_send']
        assert result['draft'] == '草稿😀\n测试文字😀\n下一行'
        assert ('set', 'composer', 'AXSelectedTextRange', (4, 0)) in native.calls
        assert not any(call[0] == 'action' and call[2] == 'AXPress' for call in native.calls)
        assert not any(call[0] == 'set' and call[2] == 'AXValue' for call in native.calls)
    finally:
        service.close()
    assert all(ref.closed for ref in native.references)


def test_native_stop_restores_original_window_when_same_app_focuses_another_window():
    from gamepadstudio.macos_app_target import MacAppTarget
    from tests.test_macos_app_target import FakeNative, Node

    native = FakeNative()
    service = VoiceEntryService(MacAppTarget(native))
    try:
        assert service.begin(CODEX)['recording']
        native.app.attributes['AXFocusedWindow'] = Node('other-window', {'AXRole': 'AXWindow'})
        native.app.attributes['AXFocusedUIElement'] = native.voice
        native.composer.attributes['AXDescription'] = 'Recording overlay'
        after_start = len(native.calls)
        assert native.front['bundle_id'] == CODEX
        stopped = service.end()
        assert stopped['ok'] and stopped['code'] == 'dictation_stopped'
        assert not stopped['stop_required']
        assert native.app.attributes['AXFocusedWindow'] is native.window
        assert ('action', 'window', 'AXRaise') in native.calls[after_start:]
        assert [call for call in native.calls[after_start:]
                if call[0] == 'action' and call[2] == 'AXPress'] == [('action', 'voice', 'AXPress')]
        assert not any(call[:3] == ('set', 'composer', 'AXFocused')
                       for call in native.calls[after_start:])
        assert native.composer.attributes['AXValue'] == '草稿😀'
    finally:
        service.close()
    assert all(ref.closed for ref in native.references)


def test_native_permission_loss_retains_recording_until_verified_stop_is_possible():
    from gamepadstudio.macos_app_target import MacAppTarget
    from tests.test_macos_app_target import FakeNative

    native = FakeNative()
    service = VoiceEntryService(MacAppTarget(native))
    assert service.begin(CODEX)['recording']
    target = service._target
    native.allowed = False
    assert service.status()['stop_required']
    assert not service.close()['ok'] and not target._closed
    assert native.voice.attributes['AXDescription'] == 'Stop dictation'
    native.allowed = True
    assert service.close()['phase'] == 'closed' and target._closed
    assert native.voice.attributes['AXDescription'] == 'Start dictation'
    assert all(ref.closed for ref in native.references)


def test_native_asynchronous_labels_require_one_start_and_one_stop_press():
    from gamepadstudio.macos_app_target import MacAppTarget
    from tests.test_macos_app_target import FakeNative

    native = FakeNative()
    native.voice_transition = False
    service = VoiceEntryService(MacAppTarget(native))

    def presses():
        return [call for call in native.calls if call[0] == 'action' and call[2] == 'AXPress']

    try:
        started = service.begin(CODEX)
        assert started['ok'] and started['phase'] == 'starting' and started['stop_required']
        assert not started['recording']
        assert service.end()['phase'] == 'starting'
        assert service.status()['stop_required']
        assert presses() == [('action', 'voice', 'AXPress')]
        native.voice.attributes['AXDescription'] = 'Stop dictation'
        confirmed = service.status()
        assert confirmed['ok'] and confirmed['code'] == 'dictation_recording'
        assert confirmed['steps'].count('dictation_recording_observed') == 1
        stopping = service.end()
        assert stopping['ok'] and stopping['phase'] == 'stopping' and stopping['stop_required']
        assert service.end()['stop_required'] and service.status()['stop_required']
        assert presses() == [('action', 'voice', 'AXPress'), ('action', 'voice', 'AXPress')]
        native.voice.attributes['AXDescription'] = 'Start dictation'
        stopped = service.status()
        assert stopped['code'] == 'dictation_stopped' and not stopped['stop_required']
        assert stopped['phase'] == 'ready' and stopped['manual_send']
        assert native.composer.attributes['AXValue'] == '草稿😀'
    finally:
        # Tests only mutate this fake label; no native UI or audio is used.
        native.voice.attributes['AXDescription'] = 'Start dictation'
        service.close()
    assert all(ref.closed for ref in native.references)


def test_native_start_prevalidation_failure_releases_recording_responsibility(monkeypatch):
    from gamepadstudio.macos_app_target import MacAppTarget
    from tests.test_macos_app_target import FakeNative

    native = FakeNative()
    adapter = MacAppTarget(native)
    start = adapter.start_dictation

    def focus_changes_before_start(target):
        native.front = dict(bundle_id='com.unrelated.editor', pid=90, generation=4.0)
        return start(target)

    monkeypatch.setattr(adapter, 'start_dictation', focus_changes_before_start)
    service = VoiceEntryService(adapter)
    result = service.begin(CODEX)
    assert not result['ok'] and result['code'] == 'focus_changed'
    assert not result['stop_required']
    assert not any(call[0] == 'action' and call[2] == 'AXPress' for call in native.calls)
    assert service.close()['phase'] == 'closed'
    assert all(ref.closed for ref in native.references)


def test_native_stop_timeout_is_not_retried_before_delayed_idle_confirmation(monkeypatch):
    from gamepadstudio.macos_app_target import MacAppTarget, TargetError as NativeTargetError
    from tests.test_macos_app_target import FakeNative

    native = FakeNative()
    service = VoiceEntryService(MacAppTarget(native))
    assert service.begin(CODEX)['recording']
    target = service._target
    native.voice_transition = False
    action = native.action
    timeout = NativeTargetError('accessibility_error', 'AXPress timed out after attempting stop')

    def stop_times_out(ref, name):
        action(ref, name)
        if name == 'AXPress':
            raise timeout

    monkeypatch.setattr(native, 'action', stop_times_out)
    failed = service.end()
    assert not failed['ok'] and failed['stop_required']
    assert timeout.action_requested is True
    assert service.end()['stop_required'] and service.status()['stop_required']
    assert not service.close()['ok'] and not target._closed
    assert [call for call in native.calls if call[0] == 'action' and call[2] == 'AXPress'] == [
        ('action', 'voice', 'AXPress'), ('action', 'voice', 'AXPress')]
    native.voice.attributes['AXDescription'] = 'Start dictation'
    assert not service.status()['stop_required']
    assert service.close()['phase'] == 'closed' and target._closed
    assert native.composer.attributes['AXValue'] == '草稿😀'
    assert all(ref.closed for ref in native.references)

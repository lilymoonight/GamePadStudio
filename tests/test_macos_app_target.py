"""Native app targeting with fake AX refs; no real application UI access."""
from dataclasses import dataclass, field
import ctypes as C
import sys

import pytest

from gamepadstudio import macos_app_target as targets


CODEX = 'com.openai.codex'
ANTIGRAVITY = 'com.google.antigravity'


@dataclass
class Node:
    key: str
    attributes: dict = field(default_factory=dict)
    writable: set = field(default_factory=set)


class Ref:
    def __init__(self, native, node):
        self.native, self.node, self.closed = native, node, False
        native.references.append(self)

    def close(self):
        self.closed = True


class FakeNative:
    def __init__(self):
        self.references = []
        self.calls = []
        self.allowed = True
        self.activation_success = True
        self.activation_deferred = False
        self.focus_deferred = False
        self.on_range = None
        self.on_append = None
        self.voice_transition = True
        self.apps = [dict(bundle_id=CODEX, pid=42, generation=10.0, name='Local app name')]
        self.front = dict(self.apps[0])
        self.composer = Node('composer', dict(AXRole='AXTextArea', AXDescription='Chat message',
                                             AXEnabled=True, AXValue='草稿😀', AXSelectedTextRange=(0, 0)),
                             {'AXFocused', 'AXSelectedTextRange', 'AXSelectedText'})
        self.editor = Node('editor', dict(AXRole='AXTextArea', AXDescription='Code editor',
                                         AXEnabled=True, AXValue='message = 1'),
                           {'AXFocused', 'AXValue', 'AXSelectedTextRange', 'AXSelectedText'})
        self.voice = Node('voice', dict(AXRole='AXButton', AXDescription='Start dictation', AXEnabled=True))
        self.send = Node('send', dict(AXRole='AXButton', AXDescription='Send message', AXEnabled=True))
        self.window = Node('window', dict(AXRole='AXWindow', AXTitle='Existing conversation',
                                         AXChildren=[self.composer, self.editor, self.voice, self.send]))
        self.app = Node('app', dict(AXFocusedWindow=self.window, AXMainWindow=self.window,
                                   AXWindows=[self.window], AXFocusedUIElement=self.composer))

    def running_apps(self):
        self.calls.append(('running',))
        return [dict(app) for app in self.apps]

    def frontmost(self):
        return dict(self.front) if self.front else None

    def trusted(self):
        self.calls.append(('trusted',))
        return self.allowed

    def activate(self, app):
        self.calls.append(('activate', dict(app)))
        if self.activation_success and not self.activation_deferred:
            self.front = dict(app)
        return self.activation_success

    def application(self, pid):
        self.calls.append(('application', pid))
        return Ref(self, self.app)

    def retain(self, ref):
        assert not ref.closed
        return Ref(self, ref.node)

    @staticmethod
    def release(ref):
        if ref:
            ref.close()

    @staticmethod
    def identity(ref):
        assert not ref.closed
        return ref.node.key

    @staticmethod
    def equal(first, second):
        return bool(first and second and not first.closed and not second.closed and first.node is second.node)

    def get(self, ref, name):
        assert not ref.closed
        value = ref.node.attributes.get(name)
        if isinstance(value, Node):
            return Ref(self, value)
        if isinstance(value, list):
            return [Ref(self, node) for node in value]
        return value

    def settable(self, ref, name):
        return name in ref.node.writable

    def set(self, ref, name, value):
        assert not ref.closed
        self.calls.append(('set', ref.node.key, name, value))
        if name == 'AXFocused':
            if not self.focus_deferred:
                self.app.attributes['AXFocusedUIElement'] = ref.node
        elif name == 'AXSelectedTextRange':
            ref.node.attributes[name] = value
            if self.on_range:
                self.on_range()
        elif name == 'AXSelectedText':
            draft = ref.node.attributes['AXValue'].encode('utf-16-le')
            offset, length = ref.node.attributes['AXSelectedTextRange']
            assert length == 0
            ref.node.attributes['AXValue'] = (draft[:offset * 2] + value.encode('utf-16-le')
                                               + draft[offset * 2:]).decode('utf-16-le')
            if self.on_append:
                self.on_append()
        elif name == 'AXValue':
            assert value  # Controlled fallback must never clear the draft.
            ref.node.attributes['AXValue'] = value
            if self.on_append:
                self.on_append()
        else:
            raise AssertionError('Unexpected Accessibility mutation')

    def action(self, ref, name):
        assert not ref.closed
        self.calls.append(('action', ref.node.key, name))
        if name == 'AXRaise':
            self.app.attributes['AXFocusedWindow'] = ref.node
        elif name == 'AXPress':
            assert ref.node is self.voice
            if self.voice_transition:
                old = self.voice.attributes['AXDescription']
                self.voice.attributes['AXDescription'] = ('Stop dictation' if old == 'Start dictation'
                                                         else 'Start dictation')
        else:
            raise AssertionError('Unexpected native action')


@pytest.fixture
def native():
    backend = FakeNative()
    yield backend
    assert all(ref.closed for ref in backend.references), 'Every retained AX reference must be released'


def test_constructor_and_running_status_never_create_ax_or_activate(native, monkeypatch):
    monkeypatch.setattr(targets, '_MacNative', lambda: pytest.fail('Constructor must remain lazy'))
    targets.MacAppTarget()
    adapter = targets.MacAppTarget(native)
    native.apps += [dict(bundle_id='other.app', pid=99, generation=30, name='Other')]
    assert adapter.list_running_targets() == [dict(native.apps[0], name='Codex')]
    assert native.calls == [('running',)]


@pytest.mark.parametrize('bundle', ['other.app', '', None])
def test_unknown_target_is_rejected_before_any_native_call(native, bundle):
    adapter = targets.MacAppTarget(native)
    for method in (adapter.wake, adapter.locate):
        with pytest.raises(targets.TargetError, match='只能选择'):
            method(bundle)
    assert native.calls == []


@pytest.mark.parametrize(('apps', 'code'), [([], 'not_running'),
    ([dict(bundle_id=CODEX, pid=42, generation=None)], 'generation_unavailable'),
    ([dict(bundle_id=CODEX, pid=42, generation=1), dict(bundle_id=CODEX, pid=43, generation=2)], 'ambiguous_application')])
def test_absent_or_ambiguous_process_never_activates_or_reads_ax(native, apps, code):
    native.apps = apps
    with pytest.raises(targets.TargetError) as error:
        targets.MacAppTarget(native).locate(CODEX)
    assert error.value.code == code
    assert not any(call[0] in ('application', 'activate') for call in native.calls)


def test_denied_ax_permission_has_reason_without_prompt_or_app_activation(native):
    native.allowed = False
    with pytest.raises(targets.TargetError) as error:
        targets.MacAppTarget(native).locate(CODEX)
    assert error.value.code == 'permission_required' and '辅助功能' in error.value.reason
    assert not any(call[0] in ('application', 'activate') for call in native.calls)


def test_wake_is_separate_from_ax_and_locate_never_activates(native):
    adapter = targets.MacAppTarget(native)
    native.allowed = False
    assert adapter.wake(CODEX)['activation_requested'] is True
    assert not any(call[0] == 'application' for call in native.calls)
    native.allowed = True
    native.calls.clear()
    with adapter.locate(CODEX) as target:
        assert target.draft == '草稿😀' and target.window_title == 'Existing conversation'
        assert target.pid == 42 and target.generation == 10
    assert not any(call[0] == 'activate' for call in native.calls)


@pytest.mark.parametrize('label', ['Code editor', 'Search chat messages', 'Find in file', '', 'Message filter',
                                 'Commit message', 'Command prompt'])
def test_focused_or_only_text_area_without_composer_semantics_is_rejected(native, label):
    native.composer.attributes['AXDescription'] = label
    native.window.attributes['AXChildren'] = [native.composer]
    with pytest.raises(targets.TargetError) as error:
        targets.MacAppTarget(native).locate(CODEX)
    assert error.value.code == 'composer_not_found'


@pytest.mark.parametrize('change', [dict(AXEnabled=False), dict(AXHidden=True),
                                   dict(AXSubrole='AXSecureTextField'), dict(AXValue=None)])
def test_disabled_hidden_secure_or_unreadable_composer_is_rejected(native, change):
    native.composer.attributes.update(change)
    with pytest.raises(targets.TargetError):
        targets.MacAppTarget(native).locate(CODEX)


def test_two_semantic_composers_fail_closed_even_if_one_is_focused(native):
    duplicate = Node('duplicate', dict(native.composer.attributes), set(native.composer.writable))
    native.window.attributes['AXChildren'].append(duplicate)
    with pytest.raises(targets.TargetError) as error:
        targets.MacAppTarget(native).locate(CODEX)
    assert error.value.code == 'ambiguous_composer'


def test_explicit_camelcase_chat_input_identifier_is_semantic(native):
    native.composer.attributes['AXDescription'] = ''
    native.composer.attributes['AXIdentifier'] = 'chatInput'
    with targets.MacAppTarget(native).locate(CODEX) as target:
        assert target._composer.node is native.composer


def test_observed_antigravity_message_input_and_memo_never_press_record_or_send(native):
    native.apps[0]['bundle_id'] = ANTIGRAVITY
    native.front = dict(native.apps[0])
    native.composer.attributes.update(AXDescription='Message input',
                                      AXPlaceholderValue='Ask anything, @ to mention, / for actions')
    native.voice.attributes['AXDescription'] = 'Record voice memo'
    native.send.attributes.update(AXDescription='Send message', AXEnabled=False)
    adapter = targets.MacAppTarget(native)
    with adapter.locate(ANTIGRAVITY) as target:
        assert target._composer.node is native.composer
        state = adapter.dictation_status(target)
        assert state['supported'] is False and state['state'] == 'unknown'
        assert state['mode'] == 'native_voice_memo' and '停止控件' in state['reason']
        for action in (adapter.start_dictation, adapter.stop_dictation):
            with pytest.raises(targets.TargetError):
                action(target)
    assert not any(call[0] in ('action', 'set') for call in native.calls)


def test_multiple_windows_without_focused_or_main_window_are_ambiguous(native):
    native.app.attributes.update(AXFocusedWindow=None, AXMainWindow=None,
                                 AXWindows=[native.window, Node('second', dict(AXRole='AXWindow'))])
    with pytest.raises(targets.TargetError) as error:
        targets.MacAppTarget(native).locate(CODEX)
    assert error.value.code == 'ambiguous_window'


def test_current_window_wins_over_other_windows_with_composers(native):
    second = Node('second', dict(AXRole='AXWindow', AXChildren=[native.composer]))
    native.app.attributes['AXWindows'] = [native.window, second]
    with targets.MacAppTarget(native).locate(CODEX) as target:
        assert target._window.node is native.window


@pytest.mark.parametrize('attribute', ['AXMinimized', 'AXModal'])
def test_minimized_or_modal_window_is_rejected(native, attribute):
    native.window.attributes[attribute] = True
    with pytest.raises(targets.TargetError) as error:
        targets.MacAppTarget(native).locate(CODEX)
    assert error.value.code == 'window_unavailable'


def test_cyclic_ax_tree_is_bounded_and_unique_composer_remains_identifiable(native):
    native.window.attributes['AXChildren'].append(native.window)
    with targets.MacAppTarget(native).locate(CODEX) as target:
        assert target._composer.node is native.composer


def test_focus_only_changes_target_window_and_composer(native):
    adapter = targets.MacAppTarget(native)
    native.app.attributes['AXFocusedUIElement'] = native.editor
    with adapter.locate(CODEX) as target:
        assert adapter.inspect(target)['input_focused'] is False
        status = adapter.focus(target)
        assert status['input_focused'] is True
    changes = [call for call in native.calls if call[0] in ('action', 'set')]
    assert changes == [('action', 'window', 'AXRaise'), ('set', 'composer', 'AXFocused', True)]


class Clock:
    def __init__(self, on_wait=None):
        self.now = 0
        self.on_wait = on_wait

    def monotonic(self):
        return self.now

    def wait(self, duration):
        assert 0 < duration <= .025
        self.now += duration
        if self.on_wait:
            self.on_wait(self.now)


def test_async_activation_must_finish_before_locate_or_text_mutation(native):
    clock = Clock(lambda now: setattr(native, 'front', dict(native.apps[0])) if now >= .075 else None)
    adapter = targets.MacAppTarget(native, clock=clock.monotonic, wait=clock.wait)
    native.front = None
    native.activation_deferred = True
    assert adapter.wake(CODEX)['activation_requested']
    assert .075 <= clock.now < .8
    assert not any(call[0] in ('application', 'set', 'action') for call in native.calls)
    with adapter.locate(CODEX) as target:
        assert adapter.focus(target)['input_focused']
    assert len([call for call in native.calls if call[0] == 'activate']) == 1


def test_activation_confirmation_timeout_is_bounded_and_does_not_repeat_activation(native):
    clock = Clock()
    adapter = targets.MacAppTarget(native, clock=clock.monotonic, wait=clock.wait)
    native.front = None
    native.activation_deferred = True
    with pytest.raises(targets.TargetError) as error:
        adapter.wake(CODEX)
    assert error.value.code == 'activation_pending'
    assert .8 <= clock.now <= .825
    assert len([call for call in native.calls if call[0] == 'activate']) == 1
    assert not any(call[0] in ('application', 'set', 'action') for call in native.calls)


def test_application_restart_during_activation_confirmation_aborts(native):
    clock = Clock(lambda now: native.apps[0].update(generation=11))
    adapter = targets.MacAppTarget(native, clock=clock.monotonic, wait=clock.wait)
    native.front = None
    native.activation_deferred = True
    with pytest.raises(targets.TargetError) as error:
        adapter.wake(CODEX)
    assert error.value.code == 'stale_target' and clock.now <= .025
    assert len([call for call in native.calls if call[0] == 'activate']) == 1


def test_async_electron_focus_is_read_confirmed_after_only_one_native_focus_write(native):
    native.app.attributes['AXFocusedUIElement'] = Node('web area', dict(AXRole='AXWebArea'))
    native.focus_deferred = True
    clock = Clock(lambda now: native.app.attributes.update(AXFocusedUIElement=native.composer)
                  if now >= .075 else None)
    adapter = targets.MacAppTarget(native, clock=clock.monotonic, wait=clock.wait)
    with adapter.locate(CODEX) as target:
        assert adapter.focus(target)['input_focused']
    assert .075 <= clock.now < .6
    assert [call for call in native.calls if call[0] in ('action', 'set')] == [
        ('action', 'window', 'AXRaise'), ('set', 'composer', 'AXFocused', True)]


def test_focus_confirmation_timeout_is_bounded_without_rewriting_focus(native):
    native.app.attributes['AXFocusedUIElement'] = native.editor
    native.focus_deferred = True
    clock = Clock()
    adapter = targets.MacAppTarget(native, clock=clock.monotonic, wait=clock.wait)
    with adapter.locate(CODEX) as target:
        with pytest.raises(targets.TargetError) as error:
            adapter.focus(target)
        assert error.value.code == 'focus_changed'
    assert .6 <= clock.now <= .625
    assert len([call for call in native.calls if call[:3] == ('set', 'composer', 'AXFocused')]) == 1


def test_append_keeps_draft_and_uses_utf16_end_without_value_overwrite_or_submit(native):
    adapter = targets.MacAppTarget(native)
    with adapter.locate(CODEX) as target:
        result = adapter.append_text(target, '新的文字')
        assert result == '草稿😀\n新的文字' and target.draft == result
    changes = [call for call in native.calls if call[0] in ('set', 'action')]
    assert changes == [('set', 'composer', 'AXSelectedTextRange', (4, 0)),
                       ('set', 'composer', 'AXSelectedText', '\n新的文字')]


def test_append_reads_latest_draft_instead_of_overwriting_located_snapshot(native):
    adapter = targets.MacAppTarget(native)
    with adapter.locate(CODEX) as target:
        native.composer.attributes['AXValue'] = '用户后来输入的草稿 '
        assert adapter.append_text(target, '转写文字') == '用户后来输入的草稿 转写文字'


@pytest.mark.parametrize('change', ['pid', 'generation', 'frontmost', 'window', 'element', 'semantics'])
def test_stale_generation_or_changed_focus_refuses_every_text_write(native, change):
    adapter = targets.MacAppTarget(native)
    with adapter.locate(CODEX) as target:
        if change in ('pid', 'generation'):
            native.apps[0][change] += 1
        elif change == 'frontmost':
            native.front = dict(bundle_id='other.app', pid=99, generation=20)
        elif change == 'window':
            native.app.attributes['AXFocusedWindow'] = Node('other window')
        elif change == 'element':
            native.app.attributes['AXFocusedUIElement'] = native.editor
        else:
            native.composer.attributes['AXDescription'] = 'Code editor'
        with pytest.raises(targets.TargetError):
            adapter.append_text(target, '不应写入')
    assert not any(call[0] in ('set', 'action') for call in native.calls)


@pytest.mark.parametrize('change', ['draft', 'focus', 'selection'])
def test_changes_during_caret_preparation_do_not_mutate_draft(native, change):
    adapter = targets.MacAppTarget(native)
    def changed():
        if change == 'draft':
            native.composer.attributes['AXValue'] = '用户最新输入'
        elif change == 'focus':
            native.front = None
        else:
            native.composer.attributes['AXSelectedTextRange'] = (0, 1)
    native.on_range = changed
    with adapter.locate(CODEX) as target:
        with pytest.raises(targets.TargetError):
            adapter.append_text(target, '转写')
    assert not any(call[:3] == ('set', 'composer', 'AXSelectedText') for call in native.calls)


def test_no_writable_append_attribute_refuses_every_mutation(native):
    native.composer.writable = {'AXFocused'}
    adapter = targets.MacAppTarget(native)
    with adapter.locate(CODEX) as target:
        with pytest.raises(targets.TargetError) as error:
            adapter.append_text(target, '转写')
        assert error.value.code == 'append_unsupported'
    assert not any(call[0] == 'set' for call in native.calls)


def test_axvalue_fallback_preserves_latest_draft_and_appends_without_selection_or_submit(native):
    native.composer.writable = {'AXFocused', 'AXValue'}
    adapter = targets.MacAppTarget(native)
    with adapter.locate(CODEX) as target:
        native.composer.attributes['AXValue'] = '用户后来输入的草稿😀'
        assert adapter.append_text(target, '转写文字') == '用户后来输入的草稿😀\n转写文字'
    assert [call for call in native.calls if call[0] in ('set', 'action')] == [
        ('set', 'composer', 'AXValue', '用户后来输入的草稿😀\n转写文字')]


def test_axvalue_fallback_refuses_draft_changed_during_final_validation(native, monkeypatch):
    native.composer.writable = {'AXFocused', 'AXValue'}
    adapter = targets.MacAppTarget(native)
    with adapter.locate(CODEX) as target:
        real_get = native.get
        reads = 0
        def changed(ref, name):
            nonlocal reads
            if name == 'AXValue' and ref.node is native.composer:
                reads += 1
                if reads == 2:
                    native.composer.attributes['AXValue'] = '用户即时修改的草稿'
            return real_get(ref, name)
        monkeypatch.setattr(native, 'get', changed)
        with pytest.raises(targets.TargetError) as error:
            adapter.append_text(target, '不应写入')
        assert error.value.code == 'draft_changed'
    assert native.composer.attributes['AXValue'] == '用户即时修改的草稿'
    assert not any(call[0] == 'set' for call in native.calls)


def test_axvalue_write_failure_is_not_retried_or_switched_to_another_method(native, monkeypatch):
    native.composer.writable = {'AXFocused', 'AXValue'}
    adapter = targets.MacAppTarget(native)
    def refused(ref, name, value):
        native.calls.append(('set', ref.node.key, name, value))
        raise targets.TargetError('ax_error', 'Simulated failed AX write')
    monkeypatch.setattr(native, 'set', refused)
    with adapter.locate(CODEX) as target:
        with pytest.raises(targets.TargetError, match='failed AX write'):
            adapter.append_text(target, '转写')
    assert len([call for call in native.calls if call[0] == 'set']) == 1
    assert native.composer.attributes['AXValue'] == '草稿😀'


def test_axvalue_fallback_mismatched_readback_is_not_retried(native):
    native.composer.writable = {'AXFocused', 'AXValue'}
    native.on_append = lambda: native.composer.attributes.update(AXValue='Application changed the result')
    adapter = targets.MacAppTarget(native)
    with adapter.locate(CODEX) as target:
        with pytest.raises(targets.TargetError) as error:
            adapter.append_text(target, '转写')
        assert error.value.code == 'append_unconfirmed'
    assert len([call for call in native.calls if call[0] == 'set']) == 1


@pytest.mark.parametrize('text', ['', ' ', None, '\0', pytest.param('a' * 100001, id='over_limit')])
def test_invalid_text_is_rejected_without_native_mutation(native, text):
    adapter = targets.MacAppTarget(native)
    with adapter.locate(CODEX) as target:
        with pytest.raises(targets.TargetError) as error:
            adapter.append_text(target, text)
        assert error.value.code == 'invalid_text'
    assert not any(call[0] == 'set' for call in native.calls)


def test_append_mismatch_is_reported_and_never_retried_or_submitted(native):
    adapter = targets.MacAppTarget(native)
    native.on_append = lambda: native.composer.attributes.update(AXValue='Unexpected application result')
    with adapter.locate(CODEX) as target:
        with pytest.raises(targets.TargetError) as error:
            adapter.append_text(target, '转写')
        assert error.value.code == 'append_unconfirmed'
    assert len([call for call in native.calls if call[:3] == ('set', 'composer', 'AXSelectedText')]) == 1
    assert not any(call[0] == 'action' for call in native.calls)


def test_closed_or_foreign_target_cannot_be_used(native):
    adapter = targets.MacAppTarget(native)
    target = adapter.locate(CODEX)
    with pytest.raises(targets.TargetError):
        targets.MacAppTarget(native).append_text(target, '转写')
    target.close()
    target.close()
    assert adapter.inspect(target)['code'] == 'stale_target'
    with pytest.raises(targets.TargetError):
        adapter.append_text(target, '转写')


def test_dictation_start_stop_require_known_state_and_only_press_voice_control(native):
    adapter = targets.MacAppTarget(native)
    with adapter.locate(CODEX) as target:
        assert adapter.dictation_status(target) == dict(supported=True, state='idle', reason='')
        assert adapter.start_dictation(target)['state'] == 'recording'
        assert adapter.stop_dictation(target)['state'] == 'idle'
        with pytest.raises(targets.TargetError):
            adapter.stop_dictation(target)
    assert [call for call in native.calls if call[0] == 'action'] == [
        ('action', 'voice', 'AXPress'), ('action', 'voice', 'AXPress')]


def test_stop_remains_owned_when_recording_hides_composer_and_focuses_stop_button(native):
    adapter = targets.MacAppTarget(native)
    with adapter.locate(CODEX) as target:
        assert adapter.start_dictation(target)['state'] == 'recording'
        native.composer.attributes['AXHidden'] = True
        native.app.attributes['AXFocusedUIElement'] = native.voice
        observed = adapter.inspect(target)
        assert observed['running'] is True and observed['target_available'] is False
        assert observed['code'] == 'composer_changed'
        assert adapter.dictation_status(target)['state'] == 'recording'
        assert adapter.stop_dictation(target)['state'] == 'idle'
    assert [call for call in native.calls if call[0] == 'action'] == [
        ('action', 'voice', 'AXPress'), ('action', 'voice', 'AXPress')]


def test_stop_refuses_foreground_window_change_even_with_visible_stop_button(native):
    adapter = targets.MacAppTarget(native)
    with adapter.locate(CODEX) as target:
        adapter.start_dictation(target)
        native.app.attributes['AXFocusedWindow'] = Node('different window', dict(AXRole='AXWindow'))
        with pytest.raises(targets.TargetError) as error:
            adapter.stop_dictation(target)
        assert error.value.code == 'focus_changed'
    assert [call for call in native.calls if call[0] == 'action'] == [('action', 'voice', 'AXPress')]


def test_recording_window_can_be_restored_without_a_visible_composer(native):
    adapter = targets.MacAppTarget(native)
    with adapter.locate(CODEX) as target:
        adapter.start_dictation(target)
        native.composer.attributes.update(AXDescription='Hidden former composer', AXHidden=True)
        native.app.attributes['AXFocusedUIElement'] = native.voice
        native.front = dict(bundle_id='other.app', pid=99, generation=20)
        status = adapter.activate_window(target)
        assert status['running'] is True and status['frontmost'] is True
        assert status['input_focused'] is False and status['target_available'] is False
        assert adapter.stop_dictation(target)['state'] == 'idle'
    assert [call for call in native.calls if call[0] == 'action'] == [
        ('action', 'voice', 'AXPress'), ('action', 'window', 'AXRaise'), ('action', 'voice', 'AXPress')]
    assert not any(call[0] == 'set' for call in native.calls)


def test_recording_window_restore_after_process_restart_performs_no_native_actions(native):
    adapter = targets.MacAppTarget(native)
    with adapter.locate(CODEX) as target:
        native.front = None
        native.apps[0]['generation'] += 1
        with pytest.raises(targets.TargetError) as error:
            adapter.activate_window(target)
        assert error.value.code == 'stale_target'
    assert not any(call[0] in ('action', 'set', 'activate') for call in native.calls)


def test_recording_window_restore_confirms_async_raise_without_repeating_it(native, monkeypatch):
    clock = Clock(lambda now: native.app.attributes.update(AXFocusedWindow=native.window)
                  if now >= .05 else None)
    adapter = targets.MacAppTarget(native, clock=clock.monotonic, wait=clock.wait)
    with adapter.locate(CODEX) as target:
        native.composer.attributes['AXHidden'] = True
        native.app.attributes['AXFocusedWindow'] = Node('different window', dict(AXRole='AXWindow'))
        def raise_later(ref, name):
            native.calls.append(('action', ref.node.key, name))
        monkeypatch.setattr(native, 'action', raise_later)
        assert adapter.activate_window(target)['frontmost'] is True
    assert .05 <= clock.now <= .075
    assert [call for call in native.calls if call[0] in ('action', 'set')] == [('action', 'window', 'AXRaise')]


@pytest.mark.parametrize('label', ['Dictation', 'Microphone', 'Voice input', 'Send', 'Run'])
def test_unknown_or_submit_voice_control_is_never_pressed(native, label):
    native.voice.attributes['AXDescription'] = label
    adapter = targets.MacAppTarget(native)
    with adapter.locate(CODEX) as target:
        assert adapter.dictation_status(target)['state'] == 'unknown'
        with pytest.raises(targets.TargetError):
            adapter.start_dictation(target)
    assert not any(call[0] == 'action' for call in native.calls)


@pytest.mark.parametrize('identifier', ['send', 'sendMessage', 'submitTask', 'run'])
def test_voice_control_with_conflicting_send_identifier_is_rejected(native, identifier):
    native.voice.attributes['AXIdentifier'] = identifier
    adapter = targets.MacAppTarget(native)
    with adapter.locate(CODEX) as target:
        assert adapter.dictation_status(target)['supported'] is False
        with pytest.raises(targets.TargetError):
            adapter.start_dictation(target)
    assert not any(call[0] == 'action' for call in native.calls)


@pytest.mark.parametrize('method,initial,destination', [
    ('start_dictation', 'Start dictation', 'Stop dictation'),
    ('stop_dictation', 'Stop dictation', 'Start dictation'),
])
def test_delayed_dictation_state_is_pending_after_one_successful_action(native, method, initial, destination):
    native.voice_transition = False
    native.voice.attributes['AXDescription'] = initial
    adapter = targets.MacAppTarget(native)
    with adapter.locate(CODEX) as target:
        result = getattr(adapter, method)(target)
        assert result['action_requested'] and result['pending']
        assert result['state'] == ('idle' if initial == 'Start dictation' else 'recording')
        assert adapter.dictation_status(target)['state'] == result['state']
        native.voice.attributes['AXDescription'] = destination
        assert adapter.dictation_status(target)['state'] == ('recording' if method == 'start_dictation' else 'idle')
    assert [call for call in native.calls if call[0] == 'action'] == [('action', 'voice', 'AXPress')]


def test_failed_dictation_press_remains_an_error(native, monkeypatch):
    def fail_press(ref, action):
        raise targets.TargetError('accessibility_error', 'AXPress refused')

    monkeypatch.setattr(native, 'action', fail_press)
    adapter = targets.MacAppTarget(native)
    with adapter.locate(CODEX) as target:
        with pytest.raises(targets.TargetError, match='AXPress refused') as error:
            adapter.start_dictation(target)
        assert error.value.code == 'accessibility_error'
        assert error.value.reason == 'AXPress refused'
        assert error.value.action_requested is True
    assert native.voice.attributes['AXDescription'] == 'Start dictation'


@pytest.mark.parametrize('method,label', [
    ('start_dictation', 'Start dictation'), ('stop_dictation', 'Stop dictation'),
])
@pytest.mark.parametrize('failure', ['permission', 'control_changed'])
def test_dictation_validation_errors_report_no_press_attempt(native, method, label, failure):
    native.voice.attributes['AXDescription'] = label
    adapter = targets.MacAppTarget(native)
    with adapter.locate(CODEX) as target:
        if failure == 'permission':
            native.allowed = False
        else:
            native.voice.attributes['AXDescription'] = 'Unverified voice control'
        with pytest.raises(targets.TargetError) as error:
            getattr(adapter, method)(target)
        assert error.value.action_requested is False
        assert error.value.code == ('permission_required' if failure == 'permission' else 'dictation_unknown')
    assert not any(call[0] == 'action' for call in native.calls)


def test_unexpected_dictation_discovery_error_is_preserved_without_a_press_attempt(native, monkeypatch):
    adapter = targets.MacAppTarget(native)
    original_error = OSError('AX discovery interrupted')

    def fail_discovery(target):
        raise original_error

    monkeypatch.setattr(adapter, '_voice_controls', fail_discovery)
    with adapter.locate(CODEX) as target:
        with pytest.raises(OSError) as error:
            adapter.start_dictation(target)
        assert error.value is original_error and error.value.action_requested is False
    assert not any(call[0] == 'action' for call in native.calls)


@pytest.mark.parametrize('method,label', [
    ('start_dictation', 'Start dictation'), ('stop_dictation', 'Stop dictation'),
])
def test_dictation_timeout_after_application_acted_preserves_attempt_metadata(native, monkeypatch, method, label):
    native.voice.attributes['AXDescription'] = label
    action = native.action
    original_error = targets.TargetError('ax_error', 'AX request timed out (-25204)')

    def act_then_timeout(ref, name):
        action(ref, name)
        raise original_error

    monkeypatch.setattr(native, 'action', act_then_timeout)
    adapter = targets.MacAppTarget(native)
    with adapter.locate(CODEX) as target:
        with pytest.raises(targets.TargetError) as error:
            getattr(adapter, method)(target)
        assert error.value is original_error and error.value.action_requested is True
        assert error.value.code == 'ax_error' and error.value.reason == 'AX request timed out (-25204)'
        assert native.voice.attributes['AXDescription'] != label
        adapter.dictation_status(target)  # Observation issues no second Press.
    assert [call for call in native.calls if call[0] == 'action'] == [('action', 'voice', 'AXPress')]


def test_observation_failure_after_successful_dictation_press_remains_pending(native, monkeypatch):
    adapter = targets.MacAppTarget(native)

    def fail_observe(target):
        raise OSError('AX observation interrupted')

    monkeypatch.setattr(adapter, 'dictation_status', fail_observe)
    with adapter.locate(CODEX) as target:
        result = adapter.start_dictation(target)
        assert result == dict(supported=False, state='unknown', reason='AX observation interrupted',
                              action_requested=True, pending=True)
    assert [call for call in native.calls if call[0] == 'action'] == [('action', 'voice', 'AXPress')]


@pytest.mark.skipif(sys.platform != 'darwin', reason='Native CF/AX types are macOS only')
def test_native_binding_and_offline_cf_unicode_range_without_app_access():
    # Loading the frameworks and creating local CF values does not enumerate
    # windows, query any running app's AX tree, focus controls, or write input.
    native = targets._MacNative()
    text = '已有草稿😀\n第二行\x00尾部'
    string = native._cfstring(text)
    try:
        assert native._convert(string) == text
    finally:
        native.cf.CFRelease(string)
    location = targets.CFRange(4, 0)
    value = native.ax.AXValueCreate(4, C.byref(location))
    try:
        assert value and native._convert(value) == (4, 0)
    finally:
        if value:
            native.cf.CFRelease(value)
    assert native.ax.AXUIElementCreateApplication.restype is C.c_void_p
    assert native.ax.AXUIElementCopyAttributeValue.argtypes == [
        C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p)]
    assert native.ax.AXUIElementIsAttributeSettable.argtypes[-1] == C.POINTER(C.c_ubyte)
    assert native.ax.AXUIElementSetAttributeValue.restype is C.c_int32
    assert native.cf.CFStringGetCharacters.argtypes[1] is targets.CFRange
    assert targets.CFRange.location.size == C.sizeof(C.c_long)

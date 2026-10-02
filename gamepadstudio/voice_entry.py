"""Observed, manual-send voice entry for already running desktop applications.

This service owns no microphone, controller, clipboard or submission key. App
operations are injected so tests cannot focus or type into a real application.
The native target backend verifies process/window/composer identity, and its
dictation backend must expose explicit start/stop controls and observable state.
"""
from __future__ import annotations


TARGETS = {'com.openai.codex': 'Codex', 'com.google.antigravity': 'Google Antigravity'}


class _TargetDictation:
    def __init__(self, backend):
        self.backend = backend

    def inspect(self, target):
        reader = getattr(self.backend, 'dictation_status', None)
        if not all(callable(getattr(self.backend, name, None))
                   for name in ('dictation_status', 'start_dictation', 'stop_dictation')):
            return {'supported': False, 'state': 'unknown',
                    'reason': '当前应用没有可验证的听写开始和停止控件，请使用应用内听写入口'}
        return reader(target)

    def start(self, target):
        return self.backend.start_dictation(target)

    def stop(self, target):
        return self.backend.stop_dictation(target)


class VoiceEntryService:
    """Locate, focus and control one verified dictation session at a time."""
    def __init__(self, target_backend=None, dictation_backend=None):
        if target_backend is None:
            from .macos_app_target import MacAppTarget
            target_backend = MacAppTarget()
        self.backend = target_backend
        self.dictation = dictation_backend if dictation_backend is not None else _TargetDictation(self.backend)
        self._target = None
        self._bundle_id = ''
        self._owned = False
        self._start_pending = False
        self._stop_sent = False
        self._closed = False
        self._phase = 'idle'
        self._ok = True
        self._code = 'select_target'
        self._message = '请选择已运行的目标应用，并定位其输入框'
        self._steps = []

    def _result(self, ok, code, message, phase=None):
        self._ok, self._code, self._message = ok, code, message
        if phase is not None:
            self._phase = phase
        return self._snapshot()

    def _failure(self, error, default='target_error'):
        return self._result(False, getattr(error, 'code', default), str(error), 'error')

    def _metadata(self, observed=None):
        if self._target is None:
            return {}
        observed = observed or {}
        return {'bundle_id': self._bundle_id, 'name': TARGETS.get(self._bundle_id, ''),
                'pid': observed.get('pid', getattr(self._target, 'pid', 0)),
                'generation': observed.get('generation', getattr(self._target, 'generation', None))}

    def _observe(self):
        if self._target is None:
            return {}, {'supported': False, 'state': 'unknown', 'reason': self._message}
        observed = self.backend.inspect(self._target)
        native = self.dictation.inspect(self._target) if observed.get('running', False) else {
            'supported': False, 'state': 'unknown',
            'reason': observed.get('reason') or '目标应用已退出或窗口已关闭，请重新定位'}
        return observed, native

    def _snapshot(self, observed=None, native=None):
        if observed is None or native is None:
            try:
                observed, native = self._observe()
            except Exception as error:
                observed, native = {}, {'supported': False, 'state': 'unknown', 'reason': str(error)}
        reason = native.get('reason', '')
        message = self._message
        if not native.get('supported', False) and self._phase in ('idle', 'ready'):
            message = reason or '当前应用的听写入口不可用'
        return {'ok': self._ok, 'code': self._code, 'message': message, 'reason': reason,
                'phase': self._phase, 'target': self._metadata(observed),
                'running': bool(observed.get('running', False)),
                'input_focused': bool(observed.get('input_focused', False)),
                'dictation_supported': bool(native.get('supported', False)),
                'native_voice_mode': native.get('mode', 'text_dictation' if native.get('supported') else 'unknown'),
                'recording': native.get('state') == 'recording',
                'dictation_state': native.get('state', 'unknown'), 'stop_required': self._owned,
                'draft': observed.get('draft', ''), 'manual_send': True, 'steps': list(self._steps)}

    def targets(self):
        """Read process metadata only; never launch, focus or request consent."""
        if self._closed:
            return []
        try:
            return [dict(row, name=TARGETS[row['bundle_id']])
                    for row in self.backend.list_running_targets()
                    if row.get('bundle_id') in TARGETS]
        except Exception as error:
            self._failure(error, 'target_list_failed')
            return []

    def status(self):
        """Observe transitions without issuing application actions."""
        if self._closed:
            return self._snapshot({}, {'supported': False, 'state': 'unknown', 'reason': ''})
        try:
            observed, native = self._observe()
            state = native.get('state', 'unknown')
            if self._owned:
                if not observed.get('running', False):
                    code = observed.get('code')
                    if code and code not in ('not_running', 'stale_target'):
                        # AX permission loss or a replaced composer is not
                        # proof that the app's microphone stopped recording.
                        self._ok, self._code, self._message, self._phase = (
                            False, code, observed.get('reason') or '无法确认目标录音状态，请在应用中停止听写', 'error')
                    else:
                        self._owned = self._start_pending = self._stop_sent = False
                        self._ok, self._code, self._message, self._phase = (
                            False, 'target_closed', '目标应用已退出或重新启动，请重新定位', 'error')
                elif state == 'recording':
                    if self._start_pending:
                        self._steps.append('dictation_recording_observed')
                        self._ok, self._code, self._message = (
                            True, 'dictation_recording', '正在听写，释放后停止并保留文字')
                    self._start_pending = False
                    self._phase = 'stopping' if self._stop_sent else 'recording'
                elif (self._stop_sent or not self._start_pending) and state in ('idle', 'processing'):
                    self._owned = self._start_pending = self._stop_sent = False
                    self._ok, self._code = True, 'dictation_stopped'
                    self._phase = 'processing' if state == 'processing' else 'ready'
                    self._message = '听写已停止，等待转写完成后手动发送' if state == 'processing' else '听写已停止，请检查文字后手动发送'
                    self._steps.append('dictation_stopped')
            elif self._phase == 'processing' and state == 'idle':
                self._ok, self._code, self._message, self._phase = (
                    True, 'transcription_ready', '转写已完成，请检查文字后手动发送', 'ready')
                self._steps.append('transcription_ready')
            return self._snapshot(observed, native)
        except Exception as error:
            return self._failure(error, 'target_inspection_failed')

    def _close_target(self):
        if self._target is not None:
            self._target.close()
        self._target = None
        self._bundle_id = ''

    def activate(self, bundle_id):
        if self._closed:
            return self._result(False, 'closed', '语音入口已关闭', 'closed')
        if self._owned:
            return self._result(False, 'recording_busy', '请先停止当前听写，再切换目标应用')
        if bundle_id not in TARGETS:
            return self._result(False, 'unsupported_target', '请选择 Codex 或 Google Antigravity', 'error')
        try:
            self._close_target()
            wake = getattr(self.backend, 'wake', None)
            if callable(wake):
                wake(bundle_id)  # Existing process only; the backend never launches apps.
            target = self.backend.locate(bundle_id)
            self._target, self._bundle_id = target, bundle_id
            self._steps = (['application_awakened'] if callable(wake) else []) + ['composer_located']
            self.backend.activate(target)
            observed, native = self._observe()
            if not observed.get('running') or not observed.get('frontmost') or not observed.get('input_focused'):
                return self._result(False, 'input_not_focused', '未确认目标输入框已聚焦，请重新定位', 'error')
            self._steps.append('composer_focused')
            return self._result(True, 'input_ready', '输入框已定位；释放听写后请手动发送', 'ready')
        except Exception as error:
            return self._failure(error, 'activation_failed')

    def begin(self, bundle_id):
        if self._owned:
            return self._result(False, 'recording_busy', '当前听写尚未停止，不能重复开始')
        result = self.activate(bundle_id)
        if not result['ok']:
            return result
        try:
            observed, native = self._observe()
            if not native.get('supported', False):
                return self._result(False, 'dictation_unsupported', native.get('reason') or '当前没有可靠的原生听写入口', 'unsupported')
            if native.get('state') != 'idle':
                return self._result(False, 'dictation_not_idle', '目标听写状态不是空闲，请先在应用中检查录音状态', 'error')
            # Track before acting: an exception after AXPress may still have
            # started a recording that must be observed and stopped later.
            self._owned = self._start_pending = True
            self._stop_sent = False
            self._steps.append('dictation_start_requested')
            self.dictation.start(self._target)
            observed, native = self._observe()
            if native.get('state') == 'recording':
                self._start_pending = False
                self._steps.append('dictation_recording_observed')
                return self._result(True, 'dictation_recording', '正在听写，释放后停止并保留文字', 'recording')
            return self._result(True, 'dictation_start_pending', '已请求开始听写，正在等待录音状态确认', 'starting')
        except Exception as error:
            if getattr(error, 'action_requested', None) is False:
                self._owned = self._start_pending = self._stop_sent = False
            return self._failure(error, 'dictation_start_failed')

    def end(self):
        if not self._owned:
            return self._result(True, 'not_recording', '本入口没有需要停止的听写')
        try:
            observed, native = self._observe()
            if not observed.get('running', False):
                return self.status()
            state = native.get('state', 'unknown')
            if self._stop_sent:
                return self.status()
            if state == 'idle' and not self._start_pending:
                self._owned = False
                return self._result(True, 'dictation_stopped', '听写已经停止，请检查文字后手动发送', 'ready')
            if state == 'idle' and self._start_pending:
                return self._result(True, 'dictation_start_pending',
                                    '开始听写尚未确认，等待状态出现后再停止', 'starting')
            if state != 'recording' or not native.get('supported', False):
                return self._result(False, 'dictation_stop_unconfirmed',
                                    '尚未观察到可停止的录音，请检查目标应用的听写状态', 'stopping')
            restore = getattr(self.backend, 'activate_window', None)
            # The app can be frontmost while another of its windows has focus.
            # Restore the original window without focusing a hidden composer.
            if callable(restore):
                restore(self._target)
            elif not observed.get('frontmost', False):
                self.backend.activate(self._target)
            self._stop_sent = True
            self._steps.append('dictation_stop_requested')
            self.dictation.stop(self._target)
            self._phase = 'stopping'
            self._ok, self._code, self._message = True, 'dictation_stop_pending', '已请求停止，正在确认；文字不会自动发送'
            return self.status()
        except Exception as error:
            # A failed AXPress may still have taken effect. Retrying a toggle
            # is safe only when the backend confirms no action was attempted.
            if getattr(error, 'action_requested', None) is False:
                self._stop_sent = False
            return self._failure(error, 'dictation_stop_failed')

    def fill_test_text(self, bundle_id, text):
        if not isinstance(text, str) or not text or '\0' in text or len(text) > 8192:
            return self._result(False, 'invalid_text', '请输入不超过 8192 字符的测试文字', 'error')
        result = self.activate(bundle_id)
        if not result['ok']:
            return result
        try:
            self.backend.append_text(self._target, text)
            self._steps.append('test_text_appended')
            return self._result(True, 'text_filled', '文字已填入并保留原有草稿，请检查后手动发送', 'ready')
        except Exception as error:
            return self._failure(error, 'text_fill_failed')

    def close(self):
        if self._closed:
            return self.status()
        if self._owned:
            result = self.end()
            if result['stop_required']:
                return self._result(False, 'dictation_stop_required', '听写尚未确认停止，请在目标应用中停止录音', 'stopping')
        self._close_target()
        self._closed = True
        return self._result(True, 'closed', '语音入口已关闭', 'closed')

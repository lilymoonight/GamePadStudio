"""An explicit desktop voice test, independent of gamepad mappings."""
from __future__ import annotations

import sys
from time import monotonic

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .glass import AppleGroup, AppleRow, TOKENS
from .i18n import tr


TARGET_CHOICES = (('Codex', 'com.openai.codex'), ('Google Antigravity', 'com.google.antigravity'))
ACCESSIBILITY_URL = 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility'
TEST_HOLD_LIMIT_SECONDS = 60


def create_voice_service():
    from .voice_entry import VoiceEntryService
    return VoiceEntryService()


def _caption(text):
    label = QLabel(tr(text))
    label.setObjectName('caption')
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    return label


def _button(text, callback=None):
    button = QPushButton(tr(text))
    button.setObjectName('pill')
    button.setCursor(Qt.PointingHandCursor)
    if callback:
        button.clicked.connect(callback)
    return button


class VoiceEntryGroup(AppleGroup):
    def __init__(self, parent=None, *, service=None, service_factory=None, platform=None):
        super().__init__(parent)
        self.vbox.setAlignment(Qt.AlignTop)
        self.platform = platform if platform is not None else sys.platform
        self.service = service
        self.service_factory = service_factory or create_voice_service
        self._closed = False
        self._recording = False
        self._stop_required = False
        self._phase = 'idle'
        self._release_requested = False
        self._begin_in_progress = False
        self._hold_started_at = None
        self._stop_reason = ''
        self._voice_supported = False
        self._running = []
        self._service_error = ''
        self.voice_timer = QTimer(self)
        self.voice_timer.setInterval(120)
        self.voice_timer.timeout.connect(self._observe_voice)
        heading = QLabel(tr('语音入口'))
        heading.setObjectName('eyebrow')
        heading.setStyleSheet(f"color: {TOKENS['ink']}; font-size: 14px; font-weight: 700; padding: 14px 16px 10px;")
        self.vbox.addWidget(heading)
        intro = _caption('先定位目标输入框，再按住说话；松开后保留草稿，由你手动发送。')
        intro.setContentsMargins(16, 0, 16, 10)
        self.vbox.addWidget(intro)

        self.target_combo = QComboBox()
        for name, bundle_id in TARGET_CHOICES:
            self.target_combo.addItem(name, bundle_id)
        self.target_combo.setMinimumWidth(150)
        self.target_combo.setMaximumWidth(240)
        self.refresh_button = _button('刷新运行状态', self.refresh_status)
        target_control = QWidget()
        target_layout = QVBoxLayout(target_control)
        target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.setSpacing(6)
        target_layout.addWidget(self.target_combo)
        target_layout.addWidget(self.refresh_button)
        self.target_row = AppleRow('globe', (TOKENS['accent'], TOKENS['accent_lo']), tr('目标应用'),
                                   tr('只读检查运行状态'), target_control)
        self.add_row(self.target_row)

        self.activate_button = _button('唤起并定位输入框', self.activate_target)
        self.add_row(AppleRow('keyboard', (TOKENS['cyan'], TOKENS['accent_lo']), tr('窗口与输入框'),
                              tr('点击后才切换到目标应用并激活已有输入框'), self.activate_button))

        test_row = QWidget()
        test_layout = QVBoxLayout(test_row)
        test_layout.setContentsMargins(16, 10, 16, 10)
        test_layout.setSpacing(6)
        test_layout.addWidget(_caption('测试文字 · 点击填入后仅写入目标草稿'))
        self.test_text = QLineEdit()
        self.test_text.setPlaceholderText(tr('输入可选测试文字'))
        self.test_text.setAccessibleName(tr('测试文字'))
        test_layout.addWidget(self.test_text)
        self.fill_button = _button('填入测试文字', self.fill_test_text)
        test_layout.addWidget(self.fill_button, 0, Qt.AlignLeft)
        self.add_row(test_row)

        voice_row = QWidget()
        voice_layout = QVBoxLayout(voice_row)
        voice_layout.setContentsMargins(16, 10, 16, 10)
        voice_layout.setSpacing(8)
        self.hold_button = _button('按住说话测试')
        self.hold_button.setAccessibleName(tr('按住说话测试'))
        self.hold_button.pressed.connect(self.begin_voice)
        self.hold_button.released.connect(self.end_voice)
        self.hold_button.installEventFilter(self)
        voice_layout.addWidget(self.hold_button, 0, Qt.AlignLeft)
        self.limit_hint = _caption('当前独立测试最多 60 秒（含启动等待）；手柄语音功能的时长将单独设定。')
        voice_layout.addWidget(self.limit_hint)
        self.stop_button = _button('停止语音', self.end_voice)
        self.stop_button.hide()
        voice_layout.addWidget(self.stop_button, 0, Qt.AlignLeft)
        self.status_label = _caption('先定位目标应用的输入框，再检查语音入口。')
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        voice_layout.addWidget(self.status_label)
        self.permission_button = _button('打开辅助功能设置', self.open_accessibility_settings)
        self.permission_button.hide()
        voice_layout.addWidget(self.permission_button, 0, Qt.AlignLeft)
        self.add_row(voice_row)
        self.target_combo.currentIndexChanged.connect(self.target_changed)
        self.test_text.textChanged.connect(self._update_controls)
        self.refresh_status()

    def selected_target(self):
        return self.target_combo.currentData()

    def _ensure_service(self):
        if self.platform != 'darwin' or self._closed:
            return False
        if self.service is None:
            try:
                self.service = self.service_factory()
                self._service_error = ''
            except (ImportError, OSError, RuntimeError, ValueError) as exc:
                self._service_error = str(exc)
                return False
        return True

    def _call(self, name, *args):
        if not self._ensure_service():
            result = {'ok': False, 'code': 'unsupported', 'message': self._service_error or tr('语音测试入口目前仅支持 macOS。'),
                      'recording': self._recording, 'stop_required': self._stop_required,
                      'phase': self._phase, 'dictation_supported': False}
        else:
            try:
                result = getattr(self.service, name)(*args)
                if not isinstance(result, dict):
                    raise RuntimeError(tr('语音入口返回了无效状态，请刷新后重试。'))
            except (OSError, RuntimeError, ValueError) as exc:
                result = {'ok': False, 'code': 'error', 'message': str(exc),
                          'recording': self._recording, 'stop_required': self._stop_required,
                          'phase': self._phase, 'dictation_supported': False}
        self._present(result)
        return result

    def _present(self, result):
        self._recording = bool(result.get('recording', False))
        self._stop_required = bool(result.get('stop_required', self._recording))
        self._phase = result.get('phase', 'recording' if self._recording else 'idle')
        target = result.get('target') or {}
        same_target = not target or target.get('bundle_id') == self.selected_target()
        self._voice_supported = bool(same_target and result.get('dictation_supported', False))
        self.status_label.setText(tr(result.get('message') or ('语音已开始；松开按钮后结束。' if self._recording else '先定位目标应用的输入框，再检查语音入口。')))
        if self._recording and not self._stop_required:
            self.status_label.setText(tr('目标应用正在录音；请在目标应用内停止后再测试。'))
        if self._stop_reason:
            self.status_label.setText(tr(self._stop_reason) + ' ' + self.status_label.text())
        if not self._stop_required:
            self._hold_started_at = None
        code = str(result.get('code', '')).lower()
        self.permission_button.setVisible(self.platform == 'darwin' and any(value in code for value in ('permission', 'accessibility', 'ax_not_trusted')))
        if self._session_pending() and not self._closed:
            self.voice_timer.start()
        else:
            self.voice_timer.stop()
        self._update_controls()

    def _session_pending(self):
        return self._recording or self._stop_required or self._phase in ('starting', 'stopping', 'processing')

    def _update_controls(self, *args):
        enabled = self.platform == 'darwin' and not self._closed and self.service is not None
        pending = self._session_pending()
        for control in (self.target_combo, self.refresh_button, self.activate_button, self.test_text):
            control.setEnabled(enabled and not pending)
        self.fill_button.setEnabled(enabled and not pending and bool(self.test_text.text().strip()))
        self.hold_button.setEnabled(enabled and (self._stop_required or (self._voice_supported and not pending)))
        self.hold_button.setText(tr('正在说话 · 松开结束' if self._stop_required else '按住说话测试'))
        self.hold_button.setToolTip('' if self._voice_supported else self.status_label.text())
        self.stop_button.setVisible(self._stop_required)
        self.stop_button.setEnabled(enabled and self._stop_required)
        # A failed optional backend import can be retried after installation.
        self.refresh_button.setEnabled(self.platform == 'darwin' and not self._closed and not pending)

    def refresh_status(self, *args):
        if self._closed or self._session_pending():
            return
        if not self._ensure_service():
            self.target_row.subtitle_label.setText(tr('语音测试入口目前仅支持 macOS。') if self.platform != 'darwin' else tr('语音入口服务尚未就绪。'))
            self.status_label.setText(self._service_error or self.target_row.subtitle_label.text())
            self._update_controls()
            return
        try:
            self._running = self.service.targets()
        except (OSError, RuntimeError, ValueError) as exc:
            self._present({'ok': False, 'code': 'error', 'message': str(exc)})
            return
        self._update_running_label()
        self._call('status')

    def _update_running_label(self):
        target = next((item for item in self._running if item.get('bundle_id') == self.selected_target() and item.get('pid')), None)
        self.target_row.subtitle_label.setText(tr('已运行 · PID {pid}', pid=target['pid']) if target else tr('目标应用未运行，请先打开应用。'))

    def target_changed(self, *args):
        if self._closed:
            return
        if self._stop_required:
            self.end_voice()
        self._voice_supported = False
        self._stop_reason = ''
        self.status_label.setText(tr('先定位目标应用的输入框，再检查语音入口。'))
        self.permission_button.hide()
        self._update_running_label()
        self._update_controls()

    def activate_target(self, *args):
        if not self._session_pending() and not self._closed:
            self._stop_reason = ''
            self._call('activate', self.selected_target())

    def fill_test_text(self, *args):
        text = self.test_text.text()
        if text.strip() and not self._session_pending() and not self._closed:
            self._stop_reason = ''
            self._call('fill_test_text', self.selected_target(), text)

    def begin_voice(self):
        if not self._closed and not self._session_pending() and self._voice_supported:
            self._release_requested = False
            self._stop_reason = ''
            self._hold_started_at = monotonic()
            self._begin_in_progress = True
            try:
                self._call('begin', self.selected_target())
            finally:
                self._begin_in_progress = False
            self._check_hold_limit()
            # Native activation may release Qt's mouse grab before begin()
            # returns. Defer stop until the service owns that start request.
            if self._release_requested and self._stop_required:
                self._call('end')

    def end_voice(self, *args):
        self._release_requested = True
        if not self._closed and not self._begin_in_progress and self._stop_required:
            self._call('end')

    def eventFilter(self, watched, event):
        if (event.type() == QEvent.UngrabMouse and watched is getattr(self, 'hold_button', None)
                and not self._closed and not self._release_requested
                and (self._begin_in_progress or self._stop_required)):
            self._stop_reason = '鼠标抓取已丢失，已请求停止测试录音。'
            self.end_voice()
        return super().eventFilter(watched, event)

    def _check_hold_limit(self):
        if (self._stop_required and not self._release_requested and self._hold_started_at is not None
                and monotonic() - self._hold_started_at >= TEST_HOLD_LIMIT_SECONDS):
            self._release_requested = True
            self._stop_reason = '已达到 60 秒测试上限，已请求停止。'
            return True
        return False

    def _observe_voice(self):
        if self._closed:
            self.voice_timer.stop()
            return
        self._call('status')
        timed_out = self._check_hold_limit()
        if self._release_requested and self._stop_required and (timed_out or self._phase == 'recording'):
            self._call('end')

    def open_accessibility_settings(self, *args):
        if self.platform == 'darwin' and not self._closed:
            QDesktopServices.openUrl(QUrl(ACCESSIBILITY_URL))

    def close_service(self):
        if self._closed:
            return True
        self.end_voice()
        if self.service is not None:
            try:
                result = self.service.close()
                if isinstance(result, dict):
                    self._present(result)
                    if result.get('stop_required'):
                        return False
            except (OSError, RuntimeError, ValueError) as exc:
                if self._stop_required:
                    self.status_label.setText(str(exc))
                    return False
        self._closed = True
        self.voice_timer.stop()
        self._update_controls()
        return True

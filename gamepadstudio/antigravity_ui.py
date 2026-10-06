"""Antigravity & AI workflow workspace page for GamePad Studio.

Provides target application inspection, direct gamepad shortcut configuration,
and non-destructive draft / voice dictation testing for Google Antigravity & Codex.
"""
from __future__ import annotations

import copy
import sys
from time import monotonic

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .glass import AppleGroup, AppleRow, TOKENS
from .i18n import tr


TARGET_CHOICES = (
    ('Google Antigravity', 'com.google.antigravity'),
    ('OpenAI Codex', 'com.openai.codex'),
)

TRIGGER_BUTTON_CHOICES = [
    ('Create / Share (按键 4 / 15)', '4'),
    ('Guide / PS / Home (按键 5)', '5'),
    ('触摸板点击 (按键 20 / Touchpad)', '20'),
    ('R3 右摇杆按下 (按键 8)', '8'),
    ('L3 左摇杆按下 (按键 7)', '7'),
    ('L1 + R1 (肩键组合 9+10)', '9+10'),
    ('三角 / Y (按键 3)', '3'),
    ('方块 / X (按键 2)', '2'),
]

TRIGGER_GESTURE_CHOICES = [
    ('短按 (Short Press)', 'short'),
    ('长按 (Long Press)', 'long'),
]

ACTION_TYPE_CHOICES = [
    ('唤起并聚焦输入框 (Focus Composer)', 'focus'),
    ('唤起并开启语音听写 (Focus & Dictate)', 'dictate'),
]

ACCESSIBILITY_URL = 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility'


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
    btn = QPushButton(tr(text))
    btn.setObjectName('pill')
    btn.setCursor(Qt.PointingHandCursor)
    if callback:
        btn.clicked.connect(callback)
    return btn


class AntigravityPage(QWidget):
    """Dedicated Antigravity & AI workflow control center."""

    def __init__(self, studio, parent=None, *, service=None, service_factory=None, platform=None):
        super().__init__(parent)
        self.studio = studio
        self.platform = platform if platform is not None else sys.platform
        self.service = service
        self.service_factory = service_factory or create_voice_service
        self._closed = False
        self._service_error = ''
        self._running_targets = []
        self._recording = False
        self._stop_required = False
        self._phase = 'idle'
        self._voice_supported = False
        self._stop_reason = ''
        self._hold_started_at = None
        self._begin_in_progress = False
        self._release_requested = False

        self.voice_timer = QTimer(self)
        self.voice_timer.setInterval(120)
        self.voice_timer.timeout.connect(self._observe_voice)

        self._build_ui()
        self.load_settings()
        self.refresh_status()

    def _build_ui(self):
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(16)
        self.vbox = vbox

        # ── 01. 目标应用与实时状态 ─────────────────────────────────
        card_target = AppleGroup()
        hdr_target = QLabel('  01 // TARGET APPLICATION & STATUS · ' + tr('目标应用与实时状态'))
        hdr_target.setObjectName('eyebrow')
        hdr_target.setStyleSheet(f"color: {TOKENS['accent']}; font-size: 10.5px; font-weight: 700; padding: 8px 16px 2px 16px;")
        card_target.vbox.addWidget(hdr_target)

        # 目标应用选择
        self.target_combo = QComboBox()
        for name, bundle_id in TARGET_CHOICES:
            self.target_combo.addItem(name, bundle_id)
        self.target_combo.setMinimumWidth(180)
        self.target_combo.setMaximumWidth(260)
        self.target_combo.currentIndexChanged.connect(self._on_target_changed)
        card_target.add_row(AppleRow(
            'globe', (TOKENS['accent'], TOKENS['accent_lo']),
            tr('目标应用'), tr('选择联动客户端；支持已运行的桌面会话。'),
            self.target_combo
        ))

        # 运行状态
        status_row_widget = QWidget()
        status_layout = QHBoxLayout(status_row_widget)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(10)
        self.refresh_btn = _button('刷新运行状态', self.refresh_status)
        status_layout.addWidget(self.refresh_btn)

        self.status_row = AppleRow(
            'sparkle', (TOKENS['purple'], TOKENS['accent_lo']),
            tr('运行状态'), tr('正在检查运行状态...'),
            status_row_widget
        )
        card_target.add_row(self.status_row)

        # 窗口与对话框聚焦测试
        self.test_focus_btn = _button('唤起并定位输入框', self.activate_target)
        card_target.add_row(AppleRow(
            'keyboard', (TOKENS['cyan'], TOKENS['accent_lo']),
            tr('窗口与输入框'), tr('立即测试：带到前台、提升窗口并将光标激活至 Prompt 对话框。'),
            self.test_focus_btn
        ))

        # 辅助功能授权按钮 (仅未授权时显示)
        self.permission_btn = _button('打开辅助功能设置', self.open_accessibility_settings)
        self.permission_btn.hide()
        self.permission_row = AppleRow(
            'shield', (TOKENS['amber'], TOKENS['accent_lo']),
            tr('辅助功能授权'), tr('需要 macOS 辅助功能权限以定位窗口和聚焦输入框。'),
            self.permission_btn
        )
        self.permission_row.hide()
        card_target.add_row(self.permission_row)
        vbox.addWidget(card_target)

        # ── 02. 手柄全局直达按键 ─────────────────────────────────
        card_trigger = AppleGroup()
        hdr_trigger = QLabel('  02 // GAMEPAD QUICK TRIGGER · ' + tr('手柄全局直达键'))
        hdr_trigger.setObjectName('eyebrow')
        hdr_trigger.setStyleSheet(f"color: {TOKENS['cyan']}; font-size: 10.5px; font-weight: 700; padding: 8px 16px 2px 16px;")
        card_trigger.vbox.addWidget(hdr_trigger)

        # 直达开关
        self.trigger_enable_cb = QCheckBox(tr('开启手柄全局直达'))
        self.trigger_enable_cb.stateChanged.connect(self._save_trigger_settings)
        card_trigger.add_row(AppleRow(
            'controller', (TOKENS['cyan'], TOKENS['accent_lo']),
            tr('全局快捷呼出'), tr('启用后，在任何游戏或桌面中按下指定手柄键即可直接唤起对话框。'),
            self.trigger_enable_cb
        ))

        # 触发按键选择
        self.trigger_key_combo = QComboBox()
        for name, key_val in TRIGGER_BUTTON_CHOICES:
            self.trigger_key_combo.addItem(name, key_val)
        self.trigger_key_combo.setMinimumWidth(210)
        self.trigger_key_combo.currentIndexChanged.connect(self._save_trigger_settings)
        card_trigger.add_row(AppleRow(
            'circle', (TOKENS['ink_2'], TOKENS['elevated']),
            tr('触发按键'), tr('选择用于唤起 Antigravity 的手柄物理按键或组合。'),
            self.trigger_key_combo
        ))

        # 触发手势与动作
        opts_widget = QWidget()
        opts_layout = QHBoxLayout(opts_widget)
        opts_layout.setContentsMargins(0, 0, 0, 0)
        opts_layout.setSpacing(8)

        self.trigger_gesture_combo = QComboBox()
        for name, g_val in TRIGGER_GESTURE_CHOICES:
            self.trigger_gesture_combo.addItem(name, g_val)
        self.trigger_gesture_combo.setMinimumWidth(130)
        self.trigger_gesture_combo.currentIndexChanged.connect(self._save_trigger_settings)
        opts_layout.addWidget(self.trigger_gesture_combo)

        self.action_type_combo = QComboBox()
        for name, a_val in ACTION_TYPE_CHOICES:
            self.action_type_combo.addItem(name, a_val)
        self.action_type_combo.setMinimumWidth(200)
        self.action_type_combo.currentIndexChanged.connect(self._save_trigger_settings)
        opts_layout.addWidget(self.action_type_combo)

        card_trigger.add_row(AppleRow(
            'sliders', (TOKENS['purple'], TOKENS['accent_lo']),
            tr('手势与动作'), tr('设置触发手势（短按/长按）以及触发后的联动行为。'),
            opts_widget
        ))
        vbox.addWidget(card_trigger)

        # ── 03. 手柄预设映射联动 ─────────────────────────────────
        card_mappings = AppleGroup()
        hdr_map = QLabel('  03 // PROFILE MAPPINGS · ' + tr('手柄预设按键自由映射'))
        hdr_map.setObjectName('eyebrow')
        hdr_map.setStyleSheet(f"color: {TOKENS['green']}; font-size: 10.5px; font-weight: 700; padding: 8px 16px 2px 16px;")
        card_mappings.vbox.addWidget(hdr_map)

        nav_map_btn = _button('前往按键配置 ›', lambda: self.studio.navigate(1))
        nav_map_btn.setFixedWidth(130)
        card_mappings.add_row(AppleRow(
            'mapping', (TOKENS['green'], TOKENS['accent_lo']),
            tr('在手柄按键配置中绑定'),
            tr('除全局直达键外，也可以在任意手柄预设中，将按键目标动作设置为「唤起 Antigravity 对话框」。'),
            nav_map_btn
        ))
        vbox.addWidget(card_mappings)

        # ── 04. 文本与语音输入测试 ─────────────────────────────────
        card_test = AppleGroup()
        hdr_test = QLabel('  04 // TEST & VERIFICATION · ' + tr('文本与语音测试'))
        hdr_test.setObjectName('eyebrow')
        hdr_test.setStyleSheet(f"color: {TOKENS['amber']}; font-size: 10.5px; font-weight: 700; padding: 8px 16px 2px 16px;")
        card_test.vbox.addWidget(hdr_test)

        test_row_widget = QWidget()
        test_vbox = QVBoxLayout(test_row_widget)
        test_vbox.setContentsMargins(16, 10, 16, 12)
        test_vbox.setSpacing(10)

        # 文本填入测试行
        text_hbox = QHBoxLayout()
        text_hbox.setSpacing(10)
        self.test_text_input = QLineEdit()
        self.test_text_input.setPlaceholderText(tr('输入测试文字（追加至目标草稿，不发送 Enter）'))
        self.test_text_input.setAccessibleName(tr('测试文字'))
        text_hbox.addWidget(self.test_text_input, 1)

        self.fill_text_btn = _button('填入测试文字', self.fill_test_text)
        text_hbox.addWidget(self.fill_text_btn)
        test_vbox.addLayout(text_hbox)

        # 语音听写测试行
        voice_hbox = QHBoxLayout()
        voice_hbox.setSpacing(10)
        self.hold_voice_btn = _button('按住说话测试')
        self.hold_voice_btn.pressed.connect(self.begin_voice)
        self.hold_voice_btn.released.connect(self.end_voice)
        voice_hbox.addWidget(self.hold_voice_btn)

        self.stop_voice_btn = _button('停止语音', self.end_voice)
        self.stop_voice_btn.hide()
        voice_hbox.addWidget(self.stop_voice_btn)
        voice_hbox.addStretch()
        test_vbox.addLayout(voice_hbox)

        self.hint_caption = _caption('提示：无线模式下推荐使用 Mac 机身阵列麦克风或 AirPods；有线连接可直接使用手柄麦克风。')
        test_vbox.addWidget(self.hint_caption)

        self.test_status_label = _caption('就绪：先确认目标应用已运行并完成输入框定位。')
        self.test_status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.test_status_label.setStyleSheet(f"color: {TOKENS['ink_2']}; font-size: 12px; font-weight: 600; padding-top: 4px;")
        test_vbox.addWidget(self.test_status_label)

        card_test.add_row(test_row_widget)
        vbox.addWidget(card_test)

    def selected_target(self):
        return self.target_combo.currentData() or 'com.google.antigravity'

    def _ensure_service(self):
        if self.platform != 'darwin' or self._closed:
            return False
        if self.service is None:
            try:
                self.service = self.service_factory()
                self._service_error = ''
            except Exception as exc:
                self.service = None
                self._service_error = str(exc)
                return False
        return True

    def load_settings(self):
        link_cfg = self.studio.config.get('antigravity_link', {})
        self.trigger_enable_cb.blockSignals(True)
        self.trigger_enable_cb.setChecked(bool(link_cfg.get('enabled', False)))
        self.trigger_enable_cb.blockSignals(False)

        key = str(link_cfg.get('trigger_key', '4'))
        idx = self.trigger_key_combo.findData(key)
        if idx >= 0:
            self.trigger_key_combo.blockSignals(True)
            self.trigger_key_combo.setCurrentIndex(idx)
            self.trigger_key_combo.blockSignals(False)

        gesture = link_cfg.get('trigger_gesture', 'short')
        g_idx = self.trigger_gesture_combo.findData(gesture)
        if g_idx >= 0:
            self.trigger_gesture_combo.blockSignals(True)
            self.trigger_gesture_combo.setCurrentIndex(g_idx)
            self.trigger_gesture_combo.blockSignals(False)

        action_mode = link_cfg.get('action_type', 'focus')
        a_idx = self.action_type_combo.findData(action_mode)
        if a_idx >= 0:
            self.action_type_combo.blockSignals(True)
            self.action_type_combo.setCurrentIndex(a_idx)
            self.action_type_combo.blockSignals(False)

        bundle = link_cfg.get('target_bundle_id')
        if bundle:
            t_idx = self.target_combo.findData(bundle)
            if t_idx >= 0:
                self.target_combo.blockSignals(True)
                self.target_combo.setCurrentIndex(t_idx)
                self.target_combo.blockSignals(False)

    def _save_trigger_settings(self, *args):
        if self._closed:
            return
        cfg = {
            'enabled': self.trigger_enable_cb.isChecked(),
            'trigger_key': self.trigger_key_combo.currentData(),
            'trigger_gesture': self.trigger_gesture_combo.currentData(),
            'action_type': self.action_type_combo.currentData(),
            'target_bundle_id': self.selected_target(),
        }
        self.studio.config['antigravity_link'] = cfg
        self.studio.store.save()
        self.studio.notify(tr('手柄直达设置已更新并保存'))

    def refresh_status(self, *args):
        if self._closed:
            return
        if not self._ensure_service():
            msg = tr('应用窗口与输入框自动定位仅在 macOS 上可用。') if self.platform != 'darwin' else (self._service_error or tr('语音入口服务尚未就绪。'))
            self.status_row.subtitle_label.setText(msg)
            self.test_status_label.setText(msg)
            return

        try:
            self._running_targets = self.service.targets()
        except Exception as exc:
            self.status_row.subtitle_label.setText(f"检查失败: {exc}")
            return

        target_bundle = self.selected_target()
        matched = next((item for item in self._running_targets if item.get('bundle_id') == target_bundle and item.get('pid')), None)
        if matched:
            self.status_row.subtitle_label.setText(tr('● 已运行 · PID {pid}', pid=matched['pid']))
            self.status_row.subtitle_label.setStyleSheet(f"font-size: 11.5px; color: {TOKENS['green']}; font-weight: 600;")
        else:
            self.status_row.subtitle_label.setText(tr('○ 未检测到运行进程 · 请先启动应用'))
            self.status_row.subtitle_label.setStyleSheet(f"font-size: 11.5px; color: {TOKENS['ink_3']}; font-weight: 500;")

        self._call('status')

    def _on_target_changed(self, *args):
        if self._closed:
            return
        self.refresh_status()
        self._save_trigger_settings()

    def activate_target(self, *args):
        if self._closed:
            return
        result = self._call('activate', self.selected_target())
        if result.get('ok'):
            self.test_status_label.setText(tr('已成功唤起并聚焦输入框；光标已就绪。'))
            self.studio.notify(tr('已成功唤起并激活对话框'))
        else:
            msg = result.get('message') or tr('唤起失败，请检查应用是否运行')
            self.test_status_label.setText(msg)
            self.studio.notify(msg)

    def trigger_from_gamepad(self, binding=None):
        """Called when gamepad triggers an Antigravity action."""
        if self._closed:
            return
        target = (binding or {}).get('target') or self.selected_target()
        mode = (binding or {}).get('mode') or self.action_type_combo.currentData() or 'focus'

        result = self._call('activate', target)
        if result.get('ok'):
            self.studio.notify(tr('已唤起 Antigravity 并激活输入框'))
            self.test_status_label.setText(tr('手柄触发成功：窗口已提升并激活焦点'))
            if mode == 'dictate' and result.get('dictation_supported'):
                self.begin_voice()
        else:
            reason = result.get('message') or tr('目标应用未运行或无法定位')
            self.studio.notify(f"Antigravity 唤起: {reason}")
            self.test_status_label.setText(f"手柄触发反馈: {reason}")

    def fill_test_text(self, *args):
        text = self.test_text_input.text().strip()
        if not text:
            self.test_status_label.setText(tr('请先输入测试文本'))
            return
        result = self._call('fill_test_text', self.selected_target(), text)
        if result.get('ok'):
            self.test_status_label.setText(tr('已成功将测试文本追加至目标草稿'))
            self.studio.notify(tr('已追加测试文本'))
        else:
            self.test_status_label.setText(result.get('message') or tr('写入草稿失败'))

    def begin_voice(self):
        if self._closed or not self._voice_supported:
            return
        self._release_requested = False
        self._hold_started_at = monotonic()
        self._begin_in_progress = True
        try:
            self._call('begin', self.selected_target())
        finally:
            self._begin_in_progress = False
        if self._release_requested and self._stop_required:
            self._call('end')

    def end_voice(self, *args):
        if self._closed:
            return
        if self._begin_in_progress:
            self._release_requested = True
            return
        if self._stop_required or self._recording:
            self._call('end')

    def _observe_voice(self):
        if self._closed:
            self.voice_timer.stop()
            return
        self._call('status')

    def open_accessibility_settings(self, *args):
        if self.platform == 'darwin':
            QDesktopServices.openUrl(QUrl(ACCESSIBILITY_URL))

    def _call(self, name, *args):
        if not self._ensure_service():
            result = {
                'ok': False,
                'code': 'unsupported',
                'message': self._service_error or tr('语音测试入口目前仅支持 macOS。'),
                'recording': self._recording,
                'stop_required': self._stop_required,
                'phase': self._phase,
                'dictation_supported': False,
            }
        else:
            try:
                result = getattr(self.service, name)(*args)
                if not isinstance(result, dict):
                    result = {'ok': False, 'code': 'error', 'message': tr('返回了无效状态')}
            except Exception as exc:
                result = {
                    'ok': False,
                    'code': 'error',
                    'message': str(exc),
                    'recording': self._recording,
                    'stop_required': self._stop_required,
                    'phase': self._phase,
                    'dictation_supported': False,
                }
        self._present(result)
        return result

    def _present(self, result):
        self._recording = bool(result.get('recording', False))
        self._stop_required = bool(result.get('stop_required', self._recording))
        self._phase = result.get('phase', 'recording' if self._recording else 'idle')
        self._voice_supported = bool(result.get('dictation_supported', False))

        msg = result.get('message') or ''
        if msg:
            self.test_status_label.setText(tr(msg))

        code = str(result.get('code', '')).lower()
        needs_perm = self.platform == 'darwin' and any(v in code for v in ('permission', 'accessibility', 'ax_not_trusted'))
        self.permission_btn.setVisible(needs_perm)
        self.permission_row.setVisible(needs_perm)

        self.stop_voice_btn.setVisible(self._stop_required)
        self.hold_voice_btn.setEnabled(self._voice_supported or self._stop_required)

        if self._recording or self._stop_required or self._phase in ('starting', 'stopping', 'processing'):
            if not self.voice_timer.isActive():
                self.voice_timer.start()
        else:
            self.voice_timer.stop()

    def close_service(self):
        if self._closed:
            return True
        self.end_voice()
        if self.service is not None:
            try:
                self.service.close()
            except Exception:
                pass
        self._closed = True
        return True

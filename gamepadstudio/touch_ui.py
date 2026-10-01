"""Device-bound touchpad controls and single-action gesture bindings."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                              QPushButton, QScrollArea, QSizePolicy, QSlider,
                              QVBoxLayout, QWidget)

from .glass import AppleGroup, AppleRow, TOKENS, Toggle, glyph as icon
from .i18n import tr, tr_profile
from .mapping_engine import binding_label, effective_mappings, profile_family
from .studio_core import device_config, is_nikki_profile, profile_scope
from .touch_gestures import TouchGestureRecognizer, normalize_touch_sensitivity, touch_sources


TAP_GESTURES = (('TP:tap', '•', '轻触'), ('TP:double_tap', '2×', '双击'),
                ('TP:hold', '◷', '按住'), ('TP:two_tap', 'Ⅱ', '双指轻触'))
SWIPE_GESTURES = (('TP:swipe_up', '↑', '向上滑动'), ('TP:swipe_down', '↓', '向下滑动'),
                  ('TP:swipe_left', '←', '向左滑动'), ('TP:swipe_right', '→', '向右滑动'))
SCROLL_GESTURES = (('TP:scroll_up', 'Ⅱ↑', '双指向上滚动'),
                   ('TP:scroll_down', 'Ⅱ↓', '双指向下滚动'))


def supports_touch(state):
    return bool(touch_sources(state))


def supports_two_finger(state):
    return 'TP:two_tap' in touch_sources(state)


def _identity(state):
    return (profile_scope(state), state.get('instance_id')) if state else None


def _label(text, kind='caption'):
    widget = QLabel(text)
    widget.setObjectName(kind)
    widget.setWordWrap(True)
    return widget


class GestureRow(QWidget):
    def __init__(self, token, symbol, title, edit, parent=None):
        super().__init__(parent)
        self.token = token
        self.setMinimumHeight(36)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 3, 10, 3)
        layout.setSpacing(10)
        glyph = QLabel(symbol)
        glyph.setFixedWidth(24)
        glyph.setAlignment(Qt.AlignCenter)
        glyph.setStyleSheet(f"color: {TOKENS['accent']}; font-size: 18px;")
        if token == 'TP:hold':
            glyph.setPixmap(icon('timer', TOKENS['accent']).pixmap(20, 20))
        layout.addWidget(glyph)
        title_label = QLabel(tr(title))
        title_label.setStyleSheet(f"color: {TOKENS['ink']}; font-size: 12px;")
        layout.addWidget(title_label)
        self.summary = QLabel()
        self.summary.setObjectName('caption')
        self.summary.setMinimumWidth(0)
        self.summary.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.summary, 1)
        self.edit_button = QPushButton(tr('编辑'))
        self.edit_button.setObjectName('pill')
        self.edit_button.setStyleSheet('font-size: 12px; padding: 4px 10px; min-height: 22px;')
        self.edit_button.setCursor(Qt.PointingHandCursor)
        self.edit_button.setAccessibleName(tr('编辑') + ' · ' + tr(title))
        self.edit_button.clicked.connect(lambda: edit(token))
        layout.addWidget(self.edit_button)
        self._summary_text = ''

    def set_summary(self, text):
        self._summary_text = text
        self.summary.setToolTip(text)
        self.summary.setAccessibleName(text)
        self._elide()

    def _elide(self):
        self.summary.setText(self.summary.fontMetrics().elidedText(
            self._summary_text, Qt.ElideRight, max(0, self.summary.width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()


class TouchGestureDialog(QDialog):
    def __init__(self, owner, profile):
        super().__init__(owner)
        self.owner, self.profile = owner, profile
        self.device_identity = _identity(owner.snapshot)
        self.has_two_finger = supports_two_finger(owner.snapshot)
        self.invalidated = False
        self.preview_recognizer = TouchGestureRecognizer()
        self.preview_recognizer.reset(block_until_release=True)
        self.preview_last_gesture = None
        self.preview_contacts = 0
        self.setWindowTitle(tr('触摸板手势'))
        screen = owner.screen()
        available_height = screen.availableGeometry().height() if screen else 940
        self.resize(540, min(880, max(560, available_height - 100)))
        self.setMinimumWidth(350)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(_label(tr('触摸板手势'), 'heading'))
        layout.addWidget(_label((owner.snapshot or {}).get('name', tr('未连接手柄'))))
        layout.addWidget(_label(tr('映射预设：{profile}', profile=tr_profile(profile))))
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setFrameShape(QScrollArea.NoFrame)
        self.body = QWidget()
        content = QVBoxLayout(self.body)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(9)
        preview = AppleGroup()
        preview_body = QWidget()
        preview_layout = QVBoxLayout(preview_body)
        preview_layout.setContentsMargins(12, 6, 12, 6)
        preview_layout.setSpacing(4)
        preview_header = QHBoxLayout()
        preview_header.addWidget(_label(tr('实时识别预览'), 'section'), 1)
        self.preview_contacts_label = _label(tr('接触：{count} 指', count=0))
        preview_header.addWidget(self.preview_contacts_label)
        preview_layout.addLayout(preview_header)
        self.preview_event_label = _label(tr('最近识别：{gesture}', gesture=tr('等待手势')))
        self.preview_event_label.setStyleSheet(f"color: {TOKENS['accent']};")
        preview_action = QHBoxLayout()
        preview_action.addWidget(self.preview_event_label, 1)
        self.preview_bind_btn = QPushButton(tr('绑定此手势'))
        self.preview_bind_btn.setObjectName('pill')
        self.preview_bind_btn.setEnabled(False)
        self.preview_bind_btn.clicked.connect(lambda: self.edit_gesture(self.preview_last_gesture))
        preview_action.addWidget(self.preview_bind_btn)
        preview_layout.addLayout(preview_action)
        self.preview_hint = _label(tr('在触摸板上试用；预览只显示结果，不执行动作。'))
        preview_layout.addWidget(self.preview_hint)
        preview.add_row(preview_body)
        content.addWidget(preview)
        profile_state = QWidget()
        profile_layout = QHBoxLayout(profile_state)
        profile_layout.setContentsMargins(0, 0, 0, 0)
        self.profile_state_label = _label('')
        profile_layout.addWidget(self.profile_state_label, 1)
        self.activate_btn = QPushButton(tr('设为当前生效'))
        self.activate_btn.setObjectName('primary')
        self.activate_btn.clicked.connect(self.activate_profile)
        profile_layout.addWidget(self.activate_btn)
        content.addWidget(profile_state)
        settings = device_config(owner.config, owner.snapshot)
        group = AppleGroup()
        badge = (TOKENS['green'], TOKENS['green'])
        self.enabled_box = Toggle(tr('启用'))
        self.enabled_box.setChecked(bool(settings.get('touch_gestures_enabled', False)))
        group.add_row(AppleRow('touchpad', badge, tr('手势识别'),
                              tr('轻触、按住与滑动执行已配置动作'), self.enabled_box))
        self.mouse_box = Toggle(tr('启用'))
        self.mouse_box.setChecked(bool(settings.get('touch_mouse', False)))
        group.add_row(AppleRow('mouse', badge, tr('单指鼠标'),
                              tr('单指滑动移动 Windows 鼠标指针'), self.mouse_box))
        self.scroll_box = None
        if self.has_two_finger:
            self.scroll_box = Toggle(tr('启用'))
            self.scroll_box.setChecked(bool(settings.get('touch_scroll', False)))
            group.add_row(AppleRow('touchpad', badge, tr('默认滚轮'),
                                  tr('未设置滚动映射时，双指滚动使用鼠标滚轮'), self.scroll_box))
        sensitivity = QWidget()
        sensitivity_layout = QHBoxLayout(sensitivity)
        sensitivity_layout.setContentsMargins(0, 0, 0, 0)
        sensitivity_layout.setSpacing(8)
        self.sensitivity_slider = QSlider(Qt.Horizontal)
        self.sensitivity_slider.setRange(0, 100)
        self.sensitivity_slider.setValue(round(normalize_touch_sensitivity(
            settings.get('touch_gesture_sensitivity', .5)) * 100))
        self.sensitivity_slider.setAccessibleName(tr('识别灵敏度'))
        self.sensitivity_slider.setMinimumWidth(90)
        self.sensitivity_slider.setMaximumWidth(150)
        sensitivity_layout.addWidget(self.sensitivity_slider)
        self.sensitivity_value = QLabel(f'{self.sensitivity_slider.value()}%')
        self.sensitivity_value.setObjectName('caption')
        sensitivity_layout.addWidget(self.sensitivity_value)
        self.sensitivity_slider.valueChanged.connect(lambda value: self.sensitivity_value.setText(f'{value}%'))
        self.sensitivity_slider.valueChanged.connect(self.clear_preview)
        group.add_row(AppleRow('touchpad', badge, tr('识别灵敏度'),
                              tr('较高灵敏度需要更短的滑动距离'), sensitivity))
        content.addWidget(group)
        content.addWidget(_label(tr('动作单独保存；开关与灵敏度点击应用后保存。')))
        self.gesture_rows = {}
        available = set(touch_sources(owner.snapshot))
        for title, entries in (('轻触与按住', TAP_GESTURES), ('滑动', SWIPE_GESTURES),
                               ('双指滚动映射', SCROLL_GESTURES)):
            if not any(token in available for token, _, _ in entries):
                continue
            content.addWidget(_label(tr(title), 'section'))
            gesture_group = AppleGroup()
            for token, symbol, name in entries:
                if token not in available:
                    continue
                row = GestureRow(token, symbol, name, self.edit_gesture)
                gesture_group.add_row(row)
                self.gesture_rows[token] = row
            content.addWidget(gesture_group)
        content.addWidget(_label(tr('按住达到识别阈值只触发一次；滑动在抬指后触发。')))
        content.addWidget(_label(tr('双指滚动每格触发一次；已绑定动作优先于默认滚轮。')))
        content.addWidget(_label(tr('每个手势触发一次动作；按下触摸板仍使用独立按键映射。')))
        area.setWidget(self.body)
        layout.addWidget(area, 1)
        self.status = _label('')
        self.status.hide()
        layout.addWidget(self.status)
        self.controls = QDialogButtonBox(QDialogButtonBox.Reset | QDialogButtonBox.Cancel |
                                        QDialogButtonBox.Apply | QDialogButtonBox.Save)
        for role, title in ((QDialogButtonBox.Reset, '重置'), (QDialogButtonBox.Cancel, '关闭'),
                            (QDialogButtonBox.Apply, '应用'), (QDialogButtonBox.Save, '保存')):
            self.controls.button(role).setText(tr(title))
        self.controls.button(QDialogButtonBox.Save).setObjectName('primary')
        self.controls.button(QDialogButtonBox.Save).setToolTip(tr('保存并关闭'))
        self.controls.rejected.connect(self.reject)
        self.controls.accepted.connect(self.save)
        self.controls.button(QDialogButtonBox.Apply).clicked.connect(self.apply)
        self.controls.button(QDialogButtonBox.Reset).clicked.connect(self.reset_settings)
        layout.addWidget(self.controls)
        self.refresh_bindings()
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.refresh_preview)
        self.timer.start()
        self.refresh_preview()

    def guard_device(self):
        state = self.owner.snapshot
        valid = (not self.invalidated and self.device_identity is not None and
                 _identity(state) == self.device_identity and supports_touch(state) and
                 supports_two_finger(state) == self.has_two_finger and
                 self.profile in self.owner.store.profiles_for(state))
        if not valid:
            self.invalidated = True
            self.clear_preview()
            self.preview_hint.setText(tr('预览已停止'))
            self.body.setEnabled(False)
            for role in (QDialogButtonBox.Save, QDialogButtonBox.Apply, QDialogButtonBox.Reset):
                self.controls.button(role).setEnabled(False)
            self.status.setText(tr('输入设备或触摸板能力已改变，请重新打开手势编辑。'))
            self.status.setStyleSheet(f"color: {TOKENS['orange']};")
            self.status.show()
            if hasattr(self, 'timer'):
                self.timer.stop()
        return valid

    def clear_preview(self):
        self.preview_recognizer.reset(block_until_release=True)
        self.preview_last_gesture = None
        self.preview_contacts = 0
        self.preview_contacts_label.setText(tr('接触：{count} 指', count=0))
        self.preview_event_label.setText(tr('最近识别：{gesture}', gesture=tr('等待手势')))
        self.preview_bind_btn.setEnabled(False)

    def refresh_preview(self, now=None):
        if not self.guard_device():
            return None
        # A separate pure recognizer never reaches the runtime or OS outputs.
        result = self.preview_recognizer.update(
            self.owner.snapshot, now=now,
            settings={'touch_gesture_sensitivity': self.sensitivity_slider.value() / 100.})
        self.preview_contacts = result['contacts']
        self.preview_contacts_label.setText(tr('接触：{count} 指', count=self.preview_contacts))
        if result['scroll_steps']:
            self.preview_last_gesture = 'TP:scroll_up' if result['scroll_steps'] > 0 else 'TP:scroll_down'
        elif result['events']:
            self.preview_last_gesture = result['events'][-1]
        names = {token: tr(title) for token, _, title in TAP_GESTURES + SWIPE_GESTURES + SCROLL_GESTURES}
        self.preview_bind_btn.setEnabled(self.preview_last_gesture in touch_sources(self.owner.snapshot))
        self.preview_event_label.setText(tr('最近识别：{gesture}', gesture=names.get(
            self.preview_last_gesture, tr('等待手势'))))
        if (self.owner.snapshot.get('touch_valid') is False or
                self.owner.snapshot.get('touch_read_error')):
            hint = tr('暂未读取到有效触点')
        elif result['mode'] == 'cancelled':
            hint = tr('请先抬起所有手指，再试一次。')
        else:
            hint = tr('在触摸板上试用；预览只显示结果，不执行动作。')
        self.preview_hint.setText(hint)
        return result

    def refresh_bindings(self):
        from .kbm_mapper import nikki_binding_hint
        mappings = effective_mappings(self.owner.config, self.owner.snapshot, self.profile)
        family = profile_family(self.owner.config, self.owner.snapshot, self.profile)
        for token, row in self.gesture_rows.items():
            binding = mappings.get(token, {}).get('short', {})
            text = tr('未设置') if binding.get('action', 'none') == 'none' else tr(binding_label(binding, family))
            hint = nikki_binding_hint(binding) if is_nikki_profile(self.owner.config, self.profile) else ''
            if hint:
                text = tr(hint).split(' / ')[0] + ' · ' + text
            row.set_summary(text)
        active = self.owner.config.get('active_profile') == self.profile
        self.profile_state_label.setText(tr('此预设正在生效') if active else tr('此预设尚未生效，设为当前预设后执行动作。'))
        self.activate_btn.setVisible(not active)

    def activate_profile(self):
        if self.guard_device():
            self.owner.mapping_change({'op': 'select', 'profile': self.profile})
            self.refresh_bindings()

    def edit_gesture(self, token):
        if self.guard_device() and token in touch_sources(self.owner.snapshot):
            # Gestures emit a single action, including when saved in a gamepad profile.
            was_enabled = bool(device_config(self.owner.config, self.owner.snapshot).get('touch_gestures_enabled'))
            self.owner.edit_mapping(token, profile=self.profile, mode='kbm')
            if self.guard_device():
                enabled = bool(device_config(self.owner.config, self.owner.snapshot).get('touch_gestures_enabled'))
                if enabled != was_enabled:
                    self.enabled_box.setChecked(enabled)
                self.refresh_bindings()

    def reset_settings(self):
        self.enabled_box.setChecked(False)
        self.mouse_box.setChecked(False)
        if self.scroll_box is not None:
            self.scroll_box.setChecked(False)
        self.sensitivity_slider.setValue(50)

    def apply(self):
        if not self.guard_device():
            return False
        values = {'touch_gestures_enabled': self.enabled_box.isChecked(),
                  'touch_mouse': self.mouse_box.isChecked(),
                  'touch_gesture_sensitivity': self.sensitivity_slider.value() / 100.}
        if self.scroll_box is not None:
            values['touch_scroll'] = self.scroll_box.isChecked()
        for key, value in values.items():
            self.owner.setting(key, value)
        self.owner.refresh_device_settings_ui(self.owner.snapshot)
        self.status.setText(tr('已应用到此手柄'))
        self.status.setStyleSheet(f"color: {TOKENS['green']};")
        self.status.show()
        return True

    def save(self):
        if self.apply():
            self.accept()

    def done(self, result):
        self.timer.stop()
        self.clear_preview()
        super().done(result)

    def closeEvent(self, event):
        self.timer.stop()
        self.clear_preview()
        super().closeEvent(event)

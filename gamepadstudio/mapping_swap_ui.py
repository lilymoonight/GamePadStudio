"""Preview and confirm one complete, device-owned binding exchange."""
import copy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
                              QLabel, QSizePolicy, QVBoxLayout, QWidget)

from .controller_glyphs import display_parts, make_token_label
from .i18n import tr, tr_profile
from .mapping_engine import canonical_trigger, binding_label, input_sources, trigger_label
from .studio_core import device_config, mapping_input_signature, profile_mode, profile_scope, swap_binding_entry

_SCROLL_GATE = '请先在触摸板设置中开启手势绑定，再交换滚动手势'


def caption(text='', kind='caption', parent=None):
    label = QLabel(text, parent)
    label.setObjectName(kind)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setTextFormat(Qt.PlainText)
    return label


def can_swap_bindings(owner, profile, first=None):
    """Keep every compact entry gated by the selected physical input device."""
    state = getattr(owner, 'snapshot', None)
    if not callable(getattr(owner, 'open_mapping_swap', None)):
        return False
    if getattr(owner, 'remote', False) and not owner.client.connected:
        return False
    try:
        mapping_input_signature(state)
        if owner.config.get('profile_devices', {}).get(profile) != profile_scope(state):
            return False
        if profile not in owner.config.get('profiles', {}):
            return False
        available = input_sources(state)
        if first is None:
            return len(available) >= 2
        first = canonical_trigger(first)
        swap_binding_entry(owner.config, state, profile, first)
        return any(key != first and key.startswith('TP:') == first.startswith('TP:') for key in available)
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


class SwapPreview(QWidget):
    """Borderless before/after text with the controller's actual button marks."""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.title = caption('', 'section', self)
        self.title.hide()
        self.tokens = QHBoxLayout()
        self.tokens.setContentsMargins(0, 0, 0, 0)
        self.tokens.setSpacing(4)
        layout.addLayout(self.tokens)
        self.short = caption(parent=self)
        self.long = caption(parent=self)
        self.threshold = caption(parent=self)
        layout.addWidget(self.short)
        layout.addWidget(self.long)
        layout.addWidget(self.threshold)

    def show_exchange(self, trigger, before, after, family):
        while self.tokens.count():
            item = self.tokens.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.title.setText(trigger_label(trigger, family))
        self.setAccessibleName(self.title.text())
        for index, key in enumerate(display_parts(trigger)):
            if index:
                self.tokens.addWidget(caption('+', parent=self))
            self.tokens.addWidget(make_token_label(key, family, parent=self, size=14))
        self.tokens.addStretch()
        for gesture, label, text in (('short', self.short, '短按'), ('long', self.long, '长按')):
            old = tr('不触发') if before[gesture].get('action') == 'suppress' else tr(binding_label(before[gesture], family))
            new = tr('不触发') if after[gesture].get('action') == 'suppress' else tr(binding_label(after[gesture], family))
            label.setText(tr(text) + '  ' + old + '  →  ' + new)
        self.threshold.setText(tr('长按阈值') + f"  {before['long_press']:g} → {after['long_press']:g} " + tr('秒'))
        self.setToolTip(self.title.text() + '\n' + '\n'.join(
            label.text() for label in (self.short, self.long, self.threshold)))


class MappingSwapDialog(QDialog):
    def __init__(self, owner, profile, first):
        super().__init__(owner)
        self.owner, self.profile = owner, profile
        state = owner.snapshot
        self.input_signature = mapping_input_signature(state)
        self.identity = (profile_scope(state), state['instance_id'])
        self.family = state.get('family', 'generic')
        self.invalidated = False
        self.disconnected = False
        self.offline_signal = None
        self.sample_signal = None
        available = input_sources(state)
        candidates = list(available)
        for trigger in owner.config.get('profiles', {}).get(profile, {}):
            try:
                canonical = canonical_trigger(trigger)
            except ValueError:
                continue
            if '+' in canonical and set(canonical.split('+')) <= set(available) and canonical not in candidates:
                candidates.append(canonical)
        first = canonical_trigger(first)
        if first not in candidates:
            raise ValueError('请选择当前手柄支持的输入按键')
        self.entries = {}
        for key in candidates:
            try:
                self.entries[key] = swap_binding_entry(owner.config, state, profile, key)
            except ValueError:
                if key == first:
                    raise
        candidates = [key for key in candidates if key in self.entries]
        self.setWindowTitle(tr('交换绑定'))
        self.resize(490, 510)
        self.setMinimumWidth(350)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(caption(tr('交换绑定'), 'heading'))
        layout.addWidget(caption(tr_profile(profile), 'section'))
        layout.addWidget(caption(tr('交换两处完整的短按、长按与时长设置。')))
        if profile_mode(owner.config, profile) == 'gamepad':
            layout.addWidget(caption(tr('手柄输出配置；当前未接入系统虚拟手柄后端。')))
        self.first_combo, self.second_combo = QComboBox(), QComboBox()
        for combo, title in ((self.first_combo, '第一个输入'), (self.second_combo, '第二个输入')):
            combo.setAccessibleName(tr(title))
            combo.setMinimumWidth(0)
            combo.setMinimumContentsLength(8)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            for key in candidates:
                combo.addItem(trigger_label(key, self.family), key)
            layout.addWidget(caption(tr(title)))
            layout.addWidget(combo)
        self.first_combo.setCurrentIndex(candidates.index(first))
        second = next((key for key in candidates if key != first
                       and key.startswith('TP:') == first.startswith('TP:')), first)
        self.second_combo.setCurrentIndex(candidates.index(second))
        layout.addWidget(caption(tr('当前 → 交换后'), 'section'))
        self.first_preview, self.second_preview = SwapPreview(self), SwapPreview(self)
        layout.addWidget(self.first_preview)
        layout.addWidget(self.second_preview)
        layout.addStretch(1)
        self.message = caption(parent=self)
        layout.addWidget(self.message)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.swap_button = self.buttons.button(QDialogButtonBox.Save)
        self.swap_button.setText(tr('确认交换'))
        self.buttons.button(QDialogButtonBox.Cancel).setText(tr('取消'))
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.first_combo.currentIndexChanged.connect(self.refresh_preview)
        self.second_combo.currentIndexChanged.connect(self.refresh_preview)
        if owner.remote:
            self.sample_signal = owner.client.event
            self.sample_signal.connect(self.agent_event)
            socket = getattr(owner.client, 'socket', None)
            if socket is not None:
                self.offline_signal = socket.disconnected
                self.offline_signal.connect(self.backend_disconnected)
        else:
            self.sample_signal = getattr(owner, 'device_sample', None)
            if self.sample_signal is not None:
                self.sample_signal.connect(self.on_state)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_context)
        self.timer.start(100)
        self.refresh_preview()

    def _invalidate(self):
        self.invalidated = True
        self.message.setText(tr('设备、输入能力或映射已变化，请重新打开交换。'))

    def on_state(self, state):
        try:
            signature = mapping_input_signature(state)
            identity = (profile_scope(state), state['instance_id'])
            if signature != self.input_signature or identity != self.identity:
                self._invalidate()
        except (ValueError, TypeError, KeyError):
            self._invalidate()
        self.check_context()

    def agent_event(self, message):
        if message.get('type') == 'state':
            self.on_state(message.get('device'))

    def backend_disconnected(self):
        self._invalidate()
        self.check_context()

    def check_context(self, *_):
        first, second = self.first_combo.currentData(), self.second_combo.currentData()
        state = self.owner.snapshot
        try:
            identity = (profile_scope(state), (state or {}).get('instance_id'))
            if (identity != self.identity or mapping_input_signature(state) != self.input_signature
                    or (self.owner.remote and not self.owner.client.connected)):
                self._invalidate()
            for key in (first, second):
                if swap_binding_entry(self.owner.config, state, self.profile, key) != self.entries[key]:
                    self._invalidate()
        except (ValueError, TypeError, KeyError):
            self._invalidate()
        compatible = bool(first and second and first != second
                          and first.startswith('TP:') == second.startswith('TP:'))
        different = bool(first in self.entries and second in self.entries
                         and self.entries[first] != self.entries[second])
        has_action = any(self.entries[key][gesture].get('action', 'none') != 'none'
                         for key in (first, second) for gesture in ('short', 'long') if key in self.entries)
        settings = device_config(self.owner.config, state)
        scroll_blocked = (any(key in ('TP:scroll_up', 'TP:scroll_down') for key in (first, second))
                          and settings.get('touch_scroll') and not settings.get('touch_gestures_enabled'))
        self.swap_button.setEnabled(not self.invalidated and compatible and different and has_action and not scroll_blocked)
        self.first_combo.setEnabled(not self.invalidated)
        self.second_combo.setEnabled(not self.invalidated)
        if self.invalidated:
            self._invalidate()
        elif scroll_blocked:
            self.message.setText(tr(_SCROLL_GATE))
        elif self.message.text() == tr(_SCROLL_GATE):
            self.message.clear()
        return self.swap_button.isEnabled()

    @staticmethod
    def _after_entry(trigger, entry):
        result = copy.deepcopy(entry)
        if trigger in ('TP:scroll_up', 'TP:scroll_down') and result['short'].get('action') == 'none':
            result['short']['action'] = 'suppress'
        return result

    def refresh_preview(self, *_):
        first, second = self.first_combo.currentData(), self.second_combo.currentData()
        if first in self.entries and second in self.entries:
            self.first_preview.show_exchange(first, self.entries[first], self._after_entry(first, self.entries[second]), self.family)
            self.second_preview.show_exchange(second, self.entries[second], self._after_entry(second, self.entries[first]), self.family)
        if first == second:
            self.message.setText(tr('请选择两个不同的输入。'))
        elif first.startswith('TP:') != second.startswith('TP:'):
            self.message.setText(tr('触摸手势只能与触摸手势交换。'))
        elif (self.entries[first] == self.entries[second]
              or all(self.entries[key][gesture].get('action', 'none') == 'none'
                     for key in (first, second) for gesture in ('short', 'long'))):
            self.message.setText(tr('两处配置相同，无需交换。'))
        else:
            self.message.clear()
        self.check_context()

    def save(self):
        if not self.check_context():
            return
        first, second = self.first_combo.currentData(), self.second_combo.currentData()
        change = {'op': 'swap_bindings', 'profile': self.profile, 'first': first, 'second': second,
                  'expected_inputs': copy.deepcopy(self.input_signature),
                  'expected_first': copy.deepcopy(self.entries[first]),
                  'expected_second': copy.deepcopy(self.entries[second])}
        if self.owner.mapping_change(change):
            self.accept()
        else:
            notice = getattr(self.owner, 'notice', None)
            self.message.setText(notice.text() if notice is not None else tr('交换失败，配置未更改。'))

    def disconnect_samples(self):
        self.timer.stop()
        if not self.disconnected:
            if self.sample_signal is not None:
                self.sample_signal.disconnect(self.agent_event if self.owner.remote else self.on_state)
            if self.offline_signal is not None:
                self.offline_signal.disconnect(self.backend_disconnected)
            self.disconnected = True

    def done(self, result):
        self.disconnect_samples()
        super().done(result)

    def closeEvent(self, event):
        self.disconnect_samples()
        super().closeEvent(event)

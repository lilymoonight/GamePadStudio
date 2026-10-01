"""Shared mapping editor and live binding list, used from either input view."""
import copy
import time
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QLineEdit, QDialogButtonBox, QListWidget, QListWidgetItem,
    QFormLayout, QFileDialog, QMessageBox, QDoubleSpinBox)
from .mapping_engine import (INPUTS, InputNormalizer, canonical_trigger, trigger_label,
                             binding_label, validate_mappings, effective_mappings, profile_family)
from .studio_core import ACTION_NAMES


class KeySequenceField(QLineEdit):
    """Records simultaneous non-modifier keys too (e.g. W + Space)."""
    def __init__(self, value='', parent=None):
        super().__init__(value, parent)
        self.recording = False
        self.held = set()
        self.keys = []
        self.setPlaceholderText('Ctrl+Shift+S / W+Space')

    def start_recording(self):
        self.recording = True; self.held.clear(); self.keys.clear()
        self.setPlaceholderText('同时按下目标按键，再松开'); self.clear(); self.setFocus()

    def keyPressEvent(self, event):
        if not self.recording:
            return super().keyPressEvent(event)
        if event.isAutoRepeat():
            return
        key = event.key()
        name = {Qt.Key_Control: 'Ctrl', Qt.Key_Shift: 'Shift', Qt.Key_Alt: 'Alt',
                Qt.Key_Meta: 'Win'}.get(key, QKeySequence(key).toString(QKeySequence.PortableText))
        if name and name not in self.keys:
            self.keys.append(name)
        self.held.add(key)
        self.setText('+'.join(self.keys))
        event.accept()

    def keyReleaseEvent(self, event):
        if not self.recording:
            return super().keyReleaseEvent(event)
        if event.isAutoRepeat():
            return
        self.held.discard(event.key())
        if not self.held:
            self.recording = False
        event.accept()


class BindingDialog(QDialog):
    def __init__(self, owner, trigger='0', mapping=None, output=None, new=False):
        super().__init__(owner)
        self.owner = owner
        self.new_binding = new
        self.profile = owner.config['active_profile']
        state = owner.snapshot or {}
        self.family = profile_family(owner.config, owner.snapshot)
        self.setWindowTitle('编辑映射'); self.setMinimumWidth(600)
        self.normalizer = InputNormalizer()
        self.capturing = False; self.ready = False; self.best = set()
        self.original_trigger = canonical_trigger(trigger)
        layout = QVBoxLayout(self); layout.setSpacing(14)
        layout.addWidget(QLabel(self.profile))
        source = QHBoxLayout(); self.inputs = []
        parts = self.original_trigger.split('+')
        for i in range(4):
            combo = QComboBox(); combo.addItem('—', '')
            for key in INPUTS:
                combo.addItem(trigger_label(key, self.family), key)
            combo.setCurrentIndex(combo.findData(parts[i] if i < len(parts) else ''))
            self.inputs.append(combo); source.addWidget(combo)
        layout.addLayout(source)
        self.capture_button = QPushButton('从手柄录入组合键')
        self.capture_button.clicked.connect(self.start_capture); layout.addWidget(self.capture_button)
        self.live = QLabel(''); self.live.setWordWrap(True); layout.addWidget(self.live)
        mapping = copy.deepcopy(mapping or {})
        if output:
            from .mapping_engine import legacy_action
            mapping['short'] = legacy_action(output)
        self.fields = {}
        for gesture, title in [('short', '短按'), ('long', '长按')]:
            form = QFormLayout(); form.addRow(QLabel(title))
            action = QComboBox()
            for key, name in ACTION_NAMES.items():
                action.addItem(name, key)
            binding = mapping.get(gesture, {})
            action.setCurrentIndex(max(0, action.findData(binding.get('action', 'none'))))
            form.addRow('动作', action)
            field = KeySequenceField(binding.get('value', ''))
            record = QPushButton('录入键盘组合'); record.clicked.connect(field.start_recording)
            keyrow = QWidget(); kl = QHBoxLayout(keyrow); kl.setContentsMargins(0,0,0,0); kl.addWidget(field,1); kl.addWidget(record)
            form.addRow(keyrow)
            mouse = QComboBox()
            for value, name in [('left','左键'),('right','右键'),('middle','中键'),('up','向上'),('down','向下')]:
                mouse.addItem(name, value)
            mouse.setCurrentIndex(max(0, mouse.findData(binding.get('value')))); form.addRow(mouse)
            path = QLineEdit(binding.get('executable', '')); path.setPlaceholderText('应用路径')
            browse = QPushButton('选择应用'); browse.clicked.connect(lambda checked=False, f=path: self.browse(f))
            args = QLineEdit(binding.get('arguments', '')); args.setPlaceholderText('启动参数（可选）')
            form.addRow(path); form.addRow(browse); form.addRow(args)
            def update_fields(index=0, a=action, k=keyrow, m=mouse, p=path, b=browse, x=args):
                key = a.currentData()
                k.setVisible(key in ('hold', 'shortcut')); m.setVisible(key in ('mouse_hold', 'mouse_click', 'wheel'))
                for w in (p,b,x): w.setVisible(key == 'launch')
            action.currentIndexChanged.connect(update_fields); update_fields()
            self.fields[gesture] = (action, field, mouse, path, args)
            layout.addLayout(form)
        timing = QHBoxLayout(); timing.addWidget(QLabel('长按时长'))
        self.long_press = QDoubleSpinBox(); self.long_press.setRange(.15,3.); self.long_press.setSingleStep(.05); self.long_press.setSuffix(' 秒')
        self.long_press.setValue(mapping.get('long_press', owner.config.get('long_press', .65)))
        timing.addWidget(self.long_press); timing.addStretch(); layout.addLayout(timing)
        hint = QLabel('组合键优先 · 长按后不触发短按')
        hint.setWordWrap(True); layout.addWidget(hint)
        self.error = QLabel(''); self.error.setWordWrap(True); layout.addWidget(self.error)
        controls = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        controls.button(QDialogButtonBox.Save).setText('保存');controls.button(QDialogButtonBox.Cancel).setText('取消')
        controls.accepted.connect(self.validate); controls.rejected.connect(self.reject); layout.addWidget(controls)
        self.timer = QTimer(self); self.timer.timeout.connect(self.poll); self.timer.start(16)

    def browse(self, field):
        value, _ = QFileDialog.getOpenFileName(self, '选择应用', '', '程序 (*.exe);;所有文件 (*)')
        if value: field.setText(value)

    def start_capture(self):
        self.capturing = True; self.ready = False; self.best = set()
        self.capture_button.setText('先松开按键，再同时按下组合键')
        self.error.clear()

    def poll(self):
        state = self.owner.snapshot
        current = self.normalizer.update(state)
        current.discard('LS:outer')  # selectable explicitly; avoid duplicating a pushed direction
        if not self.capturing:
            self.live.setText(' + '.join(trigger_label(k, self.family) for k in sorted(current)) if current else '等待输入' if state else '未连接 · 可手动设置')
            return
        if not self.ready:
            self.ready = not current
            return
        # Capture an actual simultaneous set, never a union of sequential taps.
        if len(current) > len(self.best):
            self.best = current
        if current:
            self.live.setText(trigger_label('+'.join(current), self.family) if len(current) <= 4 else '最多支持 4 个按键')
        elif self.best:
            if len(self.best) > 4:
                self.error.setText('最多支持 4 个按键，请重新录入'); self.best.clear(); return
            parts = canonical_trigger('+'.join(self.best)).split('+')
            for i, combo in enumerate(self.inputs):
                combo.setCurrentIndex(combo.findData(parts[i] if i < len(parts) else ''))
            self.capturing = False; self.capture_button.setText('重新录入组合键')

    def trigger(self):
        return canonical_trigger('+'.join(c.currentData() for c in self.inputs if c.currentData()))

    def value(self):
        mapping = {}
        for g, (action, field, mouse, path, args) in self.fields.items():
            key = action.currentData()
            binding = {'action': key}
            if key in ('hold', 'shortcut'): binding['value'] = field.text().strip()
            if key in ('mouse_hold', 'mouse_click', 'wheel'): binding['value'] = mouse.currentData()
            if key == 'launch': binding.update(executable=path.text().strip(), arguments=args.text())
            mapping[g] = binding
        mapping['long_press'] = self.long_press.value()
        return mapping

    def validate(self):
        try:
            if self.capturing: raise ValueError('请先完成手柄录入')
            validate_mappings({self.trigger(): self.value()})
            if self.new_binding or self.trigger() != self.original_trigger:
                existing = self.owner.config['profiles'].get(self.profile, {}).get(self.trigger(), {})
                if any(existing.get(g, {}).get('action', 'none') != 'none' for g in ('short','long')):
                    if QMessageBox.question(self, '替换映射', '这个组合键已有映射，是否替换？') != QMessageBox.Yes:
                        return
            self.accept()
        except ValueError as exc:
            self.error.setText(str(exc))


class BindingList(QWidget):
    def __init__(self, owner, parent=None):
        super().__init__(parent); self.owner = owner
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0)
        tools = QHBoxLayout(); tools.addWidget(QLabel('当前配置全部映射 (单击选择 · 双击编辑 · 实时按键高亮)'), 1)
        for title, callback in [('添加', self.add), ('编辑', self.edit), ('清除', self.clear)]:
            button = QPushButton(title); button.clicked.connect(callback); tools.addWidget(button)
        layout.addLayout(tools)
        self.list = QListWidget(); self.list.setMinimumHeight(110)
        self.list.itemDoubleClicked.connect(lambda item: self.edit()); layout.addWidget(self.list)
        self.rows = {}; self.active = set()

    def refresh(self):
        selected = self.list.currentItem().data(Qt.UserRole) if self.list.currentItem() else None
        self.list.clear(); self.rows.clear()
        state = self.owner.snapshot or {}
        family = profile_family(self.owner.config, self.owner.snapshot)
        for trigger, entry in effective_mappings(self.owner.config, self.owner.snapshot).items():
            if all(entry.get(g, {}).get('action', 'none') == 'none' for g in ('short', 'long')): continue
            text = trigger_label(trigger, family) + '  →  ' + binding_label(entry.get('short', {}))
            if entry.get('long', {}).get('action', 'none') != 'none':
                text += '    长按：' + binding_label(entry['long'])
            item = QListWidgetItem(text); item.setData(Qt.UserRole, trigger); self.list.addItem(item); self.rows[trigger] = item
            if trigger == selected: self.list.setCurrentItem(item)

    def add(self): self.owner.edit_mapping('0', new=True)

    def edit(self):
        if self.list.currentItem(): self.owner.edit_mapping(self.list.currentItem().data(Qt.UserRole))

    def clear(self):
        if self.list.currentItem():
            self.owner.mapping_change({'op': 'unbind', 'trigger': self.list.currentItem().data(Qt.UserRole)})

    def feedback(self, data):
        from PySide6.QtGui import QColor
        active = set(data.get('active', []))
        if active == self.active: return
        self.active = active
        for trigger, item in self.rows.items():
            item.setForeground(QColor('#ff9f0a' if trigger in active else '#dddddd'))

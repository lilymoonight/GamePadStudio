"""Shared mapping editor and live binding list for pure Gamepad-to-Gamepad macro production."""
import copy
import time
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QColor
from PySide6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QLineEdit, QDialogButtonBox, QListWidget, QListWidgetItem,
    QFormLayout, QFileDialog, QMessageBox, QDoubleSpinBox, QGridLayout, QSpinBox, QGroupBox)
from .mapping_engine import (INPUTS, GAMEPAD_TARGETS, InputNormalizer, canonical_trigger, trigger_label,
                             binding_label, validate_mappings, effective_mappings, profile_family)
from .studio_core import ACTION_NAMES
from .glass import TOKENS


class GamepadTargetSelector(QWidget):
    """Tactile target button and chord selector for pure Gamepad-to-Gamepad actions."""

    def __init__(self, family='dualsense', parent=None):
        super().__init__(parent)
        self.family = family
        self.mode = 'single'  # 'single', 'chord', 'turbo'
        self.selected = {'0'}
        self.turbo_rate = 15

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # 2-row button grid
        self.grid = QGridLayout()
        self.grid.setSpacing(6)
        self.buttons = {}

        row1 = ['0', '1', '2', '3', '9', '10', 'LT', 'RT', '15']
        row2 = ['11', '12', '13', '14', '7', '8', '4', '6', '5']

        def clean_lbl(k):
            raw = trigger_label(k, self.family)
            return (raw.replace('方向键 ', '').replace('D-Pad ', '')
                       .replace('  交叉', '').replace('  Cross', '')
                       .replace('  圆圈', '').replace('  Circle', '')
                       .replace('  方块', '').replace('  Square', '')
                       .replace('  三角', '').replace('  Triangle', '')
                       .replace('左摇杆按下', 'L3').replace('右摇杆按下', 'R3')
                       .replace('Left Stick Click', 'L3').replace('Right Stick Click', 'R3')
                       .replace('左缓冲键', 'LB').replace('右缓冲键', 'RB')
                       .replace('Left Bumper', 'LB').replace('Right Bumper', 'RB')
                       .replace('触摸板按键', 'TP').replace('Touchpad Click', 'TP'))

        for col, k in enumerate(row1):
            btn = QPushButton(clean_lbl(k))
            btn.setCheckable(True)
            btn.setFixedSize(54, 30)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda chk=False, key=k: self._on_btn_clicked(key))
            self.grid.addWidget(btn, 0, col)
            self.buttons[k] = btn

        for col, k in enumerate(row2):
            btn = QPushButton(clean_lbl(k))
            btn.setCheckable(True)
            btn.setFixedSize(54, 30)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda chk=False, key=k: self._on_btn_clicked(key))
            self.grid.addWidget(btn, 1, col)
            self.buttons[k] = btn

        layout.addLayout(self.grid)

        # Status & Turbo Controls
        self.info_row = QHBoxLayout()
        self.info_row.setSpacing(10)
        self.info_label = QLabel(self._summary_text())
        self.info_label.setStyleSheet(f"color: {TOKENS['ink_2']}; font-size: 11.5px; font-weight: 600;")
        self.info_row.addWidget(self.info_label, 1)

        self.turbo_box = QWidget()
        tb_l = QHBoxLayout(self.turbo_box)
        tb_l.setContentsMargins(0, 0, 0, 0)
        tb_l.setSpacing(6)
        tb_title = QLabel('连发速率:')
        tb_title.setStyleSheet(f"color: {TOKENS['ink_2']}; font-size: 11px;")
        tb_l.addWidget(tb_title)
        self.turbo_spin = QSpinBox()
        self.turbo_spin.setRange(5, 30)
        self.turbo_spin.setValue(15)
        self.turbo_spin.setSuffix(' Hz')
        self.turbo_spin.valueChanged.connect(self._on_turbo_changed)
        tb_l.addWidget(self.turbo_spin)
        self.info_row.addWidget(self.turbo_box)
        self.turbo_box.hide()

        layout.addLayout(self.info_row)
        self._update_styles()

    def _on_turbo_changed(self, v):
        self.turbo_rate = v
        self.info_label.setText(self._summary_text())

    def _on_btn_clicked(self, key):
        if self.mode in ('single', 'turbo'):
            self.selected = {key}
        else:
            if key in self.selected:
                if len(self.selected) > 1:
                    self.selected.remove(key)
            else:
                if len(self.selected) < 4:
                    self.selected.add(key)
        self._update_styles()

    def _update_styles(self):
        for k, btn in self.buttons.items():
            is_sel = k in self.selected
            btn.blockSignals(True)
            btn.setChecked(is_sel)
            btn.blockSignals(False)
            if is_sel:
                btn.setStyleSheet(
                    f"background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ff6d3b, stop:1 #ff5722); "
                    f"color: #ffffff; font-weight: 700; border: 1px solid rgba(255,255,255,0.45); border-radius: 6px;"
                )
            else:
                btn.setStyleSheet(
                    f"background: {TOKENS['surface']}; color: {TOKENS['ink_2']}; font-weight: 600; "
                    f"border: 1px solid {TOKENS['border']}; border-radius: 6px;"
                )
        self.info_label.setText(self._summary_text())

    def _summary_text(self):
        if not self.selected:
            return '请在上方选择目标手柄按键'
        names = [trigger_label(k, self.family) for k in sorted(self.selected, key=lambda x: str(x))]
        if self.mode == 'chord':
            return '🎯 目标手柄组合宏: ' + ' + '.join(names) + ' (一键同时触发)'
        elif self.mode == 'turbo':
            return '⚡ 目标手柄连发: ' + names[0] + f' ({self.turbo_rate} 次/秒)'
        else:
            return '🎯 映射为目标手柄按键: ' + names[0]

    def set_mode(self, mode):
        self.mode = mode
        self.turbo_box.setVisible(mode == 'turbo')
        if mode in ('single', 'turbo') and len(self.selected) > 1:
            self.selected = {next(iter(sorted(self.selected)))}
        self._update_styles()

    def set_value(self, val):
        if not val:
            return
        parts = str(val).split('+')
        self.selected = set(parts)
        self._update_styles()

    def get_selected_single(self):
        return next(iter(self.selected), '0')

    def get_selected_chord(self):
        return '+'.join(sorted(self.selected, key=lambda x: str(x)))

    def get_turbo_rate(self):
        return self.turbo_spin.value()


class KeySequenceField(QLineEdit):
    """Fallback field for legacy KBM mappings."""
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
    """Configuration dialog supporting either pure Gamepad macro editing or pure Virtual KBM editing."""

    def __init__(self, owner, trigger='0', mapping=None, output=None, new=False, profile=None, mode=None):
        super().__init__(owner)
        self.owner = owner
        self.new_binding = new
        self.profile = profile or owner.config.get('active_profile')
        self.family = profile_family(owner.config, owner.snapshot, self.profile)

        # Resolve mode: 'gamepad' or 'kbm'
        mapping = copy.deepcopy(mapping or {})
        if mode is None:
            if output or any(mapping.get(g, {}).get('action') in ('hold', 'shortcut', 'mouse_hold', 'mouse_click', 'wheel', 'launch') for g in ('short', 'long')):
                mode = 'kbm'
            else:
                from .studio_core import profile_mode
                mode = profile_mode(owner.config, self.profile)
        self.mode = mode

        title = '编辑手柄按键与原生宏' if self.mode == 'gamepad' else '编辑虚拟键鼠映射'
        self.setWindowTitle(title)
        self.setMinimumWidth(660)
        self.normalizer = InputNormalizer()
        self.capturing = False
        self.ready = False
        self.best = set()
        self.original_trigger = canonical_trigger(trigger)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        # Profile & Mode Header
        top_bar = QHBoxLayout()
        icon_char = '🎮' if self.mode == 'gamepad' else '⌨️'
        prof_title = QLabel(f"{icon_char} 当前配置：{self.profile}")
        prof_title.setStyleSheet(f"font-size: 13.5px; font-weight: 700; color: {TOKENS['accent'] if self.mode == 'gamepad' else TOKENS['cyan']};")
        top_bar.addWidget(prof_title, 1)

        badge_text = "手柄 ➔ 手柄 原生宏生产" if self.mode == 'gamepad' else "手柄 ➔ 虚拟键鼠 映射生产"
        macro_badge = QLabel(badge_text)
        macro_badge.setStyleSheet(f"background: {TOKENS['surface']}; color: {TOKENS['cyan'] if self.mode == 'gamepad' else TOKENS['accent']}; padding: 3px 8px; border-radius: 4px; font-weight: 600; font-size: 11px;")
        top_bar.addWidget(macro_badge)
        layout.addLayout(top_bar)

        # Trigger Input Selection
        source_group = QGroupBox("触发源 (物理手柄按键 / 组合键)")
        source_group.setStyleSheet(f"QGroupBox {{ font-weight: 700; color: {TOKENS['ink']}; }}")
        sg_l = QVBoxLayout(source_group)
        sg_l.setSpacing(8)

        source = QHBoxLayout()
        self.inputs = []
        parts = self.original_trigger.split('+')
        for i in range(4):
            combo = QComboBox()
            combo.addItem('—', '')
            for key in INPUTS:
                combo.addItem(trigger_label(key, self.family), key)
            combo.setCurrentIndex(combo.findData(parts[i] if i < len(parts) else ''))
            self.inputs.append(combo)
            source.addWidget(combo)
        sg_l.addLayout(source)

        capture_row = QHBoxLayout()
        self.capture_button = QPushButton('从手柄录入组合键')
        self.capture_button.clicked.connect(self.start_capture)
        capture_row.addWidget(self.capture_button)

        self.live = QLabel('')
        self.live.setStyleSheet(f"color: {TOKENS['ink_2']}; font-weight: 600;")
        capture_row.addWidget(self.live, 1)
        sg_l.addLayout(capture_row)
        layout.addWidget(source_group)

        if output:
            from .mapping_engine import legacy_action
            mapping['short'] = legacy_action(output)

        # Action Options
        GAMEPAD_UI_ACTIONS = [
            ('none', '保留原始输入 (Pass-through)'),
            ('gamepad_button', '映射为手柄按键 (Remap)'),
            ('gamepad_chord', '手柄多键组合宏 (一键出招)'),
            ('gamepad_turbo', '手柄高频连发 (Turbo)'),
            ('capture', '保存截图 (Screenshot)'),
            ('replay_record', '保存精彩瞬间 (回放录制)'),
            ('home', '打开控制中心 (Home)'),
            ('gallery', '打开截图资料库 (Gallery)'),
            ('suppress', '屏蔽此按键 (Suppress)'),
        ]

        KBM_UI_ACTIONS = [
            ('hold', '持续按住按键 (Hold Key)'),
            ('shortcut', '触发按键/组合键 (Shortcut)'),
            ('mouse_hold', '按住鼠标键 (Mouse Hold)'),
            ('mouse_click', '单击鼠标键 (Mouse Click)'),
            ('wheel', '鼠标滚轮滚动 (Mouse Wheel)'),
            ('none', '保留原始输入 (Pass-through)'),
            ('suppress', '屏蔽此按键 (Suppress)'),
            ('launch', '启动应用程序 (Launch App)'),
        ]

        self.action_combos = {}
        self.target_selectors = {}
        self.kbm_fields = {}
        self.mouse_combos = {}
        self.launch_paths = {}
        self.launch_args = {}

        for gesture, title in [('short', '短按 (SHORT PRESS)'), ('long', '长按 (LONG PRESS)')]:
            box = QGroupBox(title)
            box.setStyleSheet(f"QGroupBox {{ font-weight: 700; color: {TOKENS['ink']}; }}")
            b_l = QVBoxLayout(box)
            b_l.setSpacing(8)

            act_row = QHBoxLayout()
            act_label = QLabel('目标动作:')
            act_label.setStyleSheet(f"color: {TOKENS['ink_2']}; font-weight: 600;")
            act_row.addWidget(act_label)

            action = QComboBox()
            action.setMinimumWidth(240)
            binding = mapping.get(gesture, {})
            current_act = binding.get('action', 'none')

            options = GAMEPAD_UI_ACTIONS if self.mode == 'gamepad' else KBM_UI_ACTIONS
            for key, name in options:
                action.addItem(name, key)

            idx = action.findData(current_act)
            action.setCurrentIndex(max(0, idx))
            act_row.addWidget(action, 1)
            b_l.addLayout(act_row)

            if self.mode == 'gamepad':
                selector = GamepadTargetSelector(self.family)
                if current_act == 'gamepad_chord':
                    selector.set_mode('chord')
                    selector.set_value(binding.get('value', ''))
                elif current_act == 'gamepad_turbo':
                    selector.set_mode('turbo')
                    selector.set_value(binding.get('value', '0'))
                    if 'rate_hz' in binding:
                        selector.turbo_spin.setValue(int(binding['rate_hz']))
                elif current_act == 'gamepad_button':
                    selector.set_mode('single')
                    selector.set_value(binding.get('value', '0'))
                b_l.addWidget(selector)
                self.target_selectors[gesture] = selector

                def make_gamepad_updater(a=action, s=selector):
                    def update():
                        k = a.currentData()
                        if k in ('gamepad_button', 'gamepad_chord', 'gamepad_turbo'):
                            s.show()
                            if k == 'gamepad_button':
                                s.set_mode('single')
                            elif k == 'gamepad_chord':
                                s.set_mode('chord')
                            elif k == 'gamepad_turbo':
                                s.set_mode('turbo')
                        else:
                            s.hide()
                    return update
                updater = make_gamepad_updater(action, selector)
                action.currentIndexChanged.connect(updater)
                updater()
            else:
                # KBM Mode Controls
                kbm_row = QWidget()
                k_layout = QHBoxLayout(kbm_row)
                k_layout.setContentsMargins(0, 0, 0, 0)
                field = KeySequenceField(binding.get('value', ''))
                record_btn = QPushButton('录入键盘按键')
                record_btn.clicked.connect(field.start_recording)
                k_layout.addWidget(field, 1)
                k_layout.addWidget(record_btn)
                b_l.addWidget(kbm_row)
                self.kbm_fields[gesture] = field

                mouse_combo = QComboBox()
                for v, m_name in [('left', '鼠标左键'), ('right', '鼠标右键'), ('middle', '鼠标中键'), ('up', '滚轮向上'), ('down', '滚轮向下')]:
                    mouse_combo.addItem(m_name, v)
                m_idx = mouse_combo.findData(binding.get('value', 'left'))
                mouse_combo.setCurrentIndex(max(0, m_idx))
                b_l.addWidget(mouse_combo)
                self.mouse_combos[gesture] = mouse_combo

                launch_row = QWidget()
                l_layout = QHBoxLayout(launch_row)
                l_layout.setContentsMargins(0, 0, 0, 0)
                path_edit = QLineEdit(binding.get('executable', ''))
                path_edit.setPlaceholderText('应用程序路径 (.exe)')
                browse_btn = QPushButton('浏览...')
                browse_btn.clicked.connect(lambda chk=False, p=path_edit: self.browse_app(p))
                args_edit = QLineEdit(binding.get('arguments', ''))
                args_edit.setPlaceholderText('启动参数 (可选)')
                l_layout.addWidget(path_edit, 2)
                l_layout.addWidget(browse_btn)
                l_layout.addWidget(args_edit, 1)
                b_l.addWidget(launch_row)
                self.launch_paths[gesture] = path_edit
                self.launch_args[gesture] = args_edit

                def make_kbm_updater(a=action, kr=kbm_row, mc=mouse_combo, lr=launch_row):
                    def update():
                        k = a.currentData()
                        kr.setVisible(k in ('hold', 'shortcut'))
                        mc.setVisible(k in ('mouse_hold', 'mouse_click', 'wheel'))
                        lr.setVisible(k == 'launch')
                    return update
                updater = make_kbm_updater(action, kbm_row, mouse_combo, launch_row)
                action.currentIndexChanged.connect(updater)
                updater()

            self.action_combos[gesture] = action
            layout.addWidget(box)

        # Long press timing
        timing = QHBoxLayout()
        timing.addWidget(QLabel('长按识别阈值:'))
        self.long_press = QDoubleSpinBox()
        self.long_press.setRange(.15, 3.)
        self.long_press.setSingleStep(.05)
        self.long_press.setSuffix(' 秒')
        self.long_press.setValue(mapping.get('long_press', owner.config.get('long_press', .65)))
        timing.addWidget(self.long_press)
        timing.addStretch()
        layout.addLayout(timing)

        # Tip banner
        if self.mode == 'gamepad':
            tip_text = '💡 纯手柄原生宏：无需额外背键，通过原始手柄按键的短按、长按与组合键，即可直接触发全新手柄单键重映射或组合宏。'
        else:
            tip_text = '💡 虚拟键鼠模拟：将物理手柄输入无缝转换为真实键盘按键、鼠标点击、滚轮或应用快捷键。'
        tip_lbl = QLabel(tip_text)
        tip_lbl.setWordWrap(True)
        tip_lbl.setStyleSheet(f"color: {TOKENS['ink_3']}; font-size: 11px; padding: 4px 0;")
        layout.addWidget(tip_lbl)

        self.error = QLabel('')
        self.error.setStyleSheet(f"color: {TOKENS['amber']}; font-weight: 600;")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)

        controls = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        controls.button(QDialogButtonBox.Save).setText('保存映射')
        controls.button(QDialogButtonBox.Cancel).setText('取消')
        controls.accepted.connect(self.validate)
        controls.rejected.connect(self.reject)
        layout.addWidget(controls)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(16)

    def browse_app(self, field):
        from PySide6.QtWidgets import QFileDialog
        value, _ = QFileDialog.getOpenFileName(self, '选择应用', '', '程序 (*.exe);;所有文件 (*)')
        if value:
            field.setText(value)

    def start_capture(self):
        self.capturing = True
        self.ready = False
        self.best = set()
        self.capture_button.setText('请先松开按键，再同时按下目标组合键...')
        self.error.clear()

    def poll(self):
        state = self.owner.snapshot
        current = self.normalizer.update(state)
        current.discard('LS:outer')
        if not self.capturing:
            self.live.setText(' + '.join(trigger_label(k, self.family) for k in sorted(current)) if current else '等待输入' if state else '未连接 · 可手动在上方选择按键')
            return
        if not self.ready:
            self.ready = not current
            return
        if len(current) > len(self.best):
            self.best = current
        if current:
            self.live.setText(trigger_label('+'.join(current), self.family) if len(current) <= 4 else '最多支持 4 个按键')
        elif self.best:
            if len(self.best) > 4:
                self.error.setText('最多支持 4 个按键，请重新录入')
                self.best.clear()
                return
            parts = canonical_trigger('+'.join(self.best)).split('+')
            for i, combo in enumerate(self.inputs):
                combo.setCurrentIndex(combo.findData(parts[i] if i < len(parts) else ''))
            self.capturing = False
            self.capture_button.setText('重新录入组合键')

    def trigger(self):
        parts = [c.currentData() for c in self.inputs if c.currentData()]
        if not parts:
            parts = ['0']
        return canonical_trigger('+'.join(parts))

    def value(self):
        mapping = {}
        for g in ('short', 'long'):
            action_code = self.action_combos[g].currentData()
            binding = {'action': action_code}
            if self.mode == 'gamepad':
                if action_code == 'gamepad_button':
                    binding['value'] = self.target_selectors[g].get_selected_single()
                elif action_code == 'gamepad_chord':
                    binding['value'] = self.target_selectors[g].get_selected_chord()
                elif action_code == 'gamepad_turbo':
                    binding['value'] = self.target_selectors[g].get_selected_single()
                    binding['rate_hz'] = self.target_selectors[g].get_turbo_rate()
            else:
                if action_code in ('hold', 'shortcut'):
                    binding['value'] = self.kbm_fields[g].text().strip()
                elif action_code in ('mouse_hold', 'mouse_click', 'wheel'):
                    binding['value'] = self.mouse_combos[g].currentData()
                elif action_code == 'launch':
                    binding.update(executable=self.launch_paths[g].text().strip(), arguments=self.launch_args[g].text().strip())
            mapping[g] = binding
        mapping['long_press'] = self.long_press.value()
        return mapping

    def validate(self):
        try:
            if self.capturing:
                raise ValueError('请先完成手柄按键录入')
            val = self.value()
            trig = self.trigger()
            validate_mappings({trig: val})
            if self.new_binding or trig != self.original_trigger:
                existing = self.owner.config['profiles'].get(self.profile, {}).get(trig, {})
                if any(existing.get(g, {}).get('action', 'none') != 'none' for g in ('short', 'long')):
                    if QMessageBox.question(self, '替换映射', f'组合键 {trigger_label(trig, self.family)} 已有映射，是否替换？') != QMessageBox.Yes:
                        return
            self.accept()
        except ValueError as exc:
            self.error.setText(str(exc))


GamepadBindingDialog = BindingDialog
KbmBindingDialog = BindingDialog


class BindingList(QWidget):
    """Live list of active mappings for the current profile."""

    def __init__(self, owner, profile_getter=None, mode='gamepad', parent=None):
        from PySide6.QtWidgets import QSizePolicy
        from .glass import glyph
        from .i18n import tr

        super().__init__(parent)
        self.owner = owner
        self.profile_getter = profile_getter
        self.mode = mode
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        tools = QHBoxLayout()
        tools.setSpacing(8)
        header_text = '当前手柄宏映射列表' if mode == 'gamepad' else '当前虚拟键鼠映射列表'
        self.heading = QLabel(tr(header_text))
        self.heading.setObjectName('section')
        self.heading.setWordWrap(True)
        self.heading.setMinimumWidth(0)
        self.heading.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        tools.addWidget(self.heading, 1)
        for name, title, symbol, callback in [
            ('add_button', '添加映射', 'plus', self.add),
            ('edit_button', '编辑', 'edit', self.edit),
            ('clear_button', '清除', 'trash', self.clear),
        ]:
            btn = QPushButton(tr(title))
            btn.setObjectName('pill')
            btn.setCursor(Qt.PointingHandCursor)
            btn.setIcon(glyph(symbol, TOKENS['ink_2']))
            btn.setAccessibleName(tr(title))
            btn.setToolTip(tr(title))
            btn.clicked.connect(callback)
            tools.addWidget(btn)
            setattr(self, name, btn)
        layout.addLayout(tools)
        self.instruction = QLabel(tr('单击选择 · 双击编辑 · 实时按键高亮'))
        self.instruction.setObjectName('caption')
        self.instruction.setWordWrap(True)
        self.instruction.setMinimumWidth(0)
        self.instruction.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.instruction)
        self.list = QListWidget()
        self.list.setAccessibleName(tr(header_text))
        self.list.setAccessibleDescription(self.instruction.text())
        self.list.setMinimumWidth(0)
        self.list.setMinimumHeight(110)
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.itemSelectionChanged.connect(self._sync_controls)
        self.list.itemDoubleClicked.connect(lambda item: self.edit())
        layout.addWidget(self.list)
        self.rows = {}
        self.active = set()
        self._sync_controls()

    def _sync_controls(self):
        selected = bool(self.list.selectedItems())
        self.edit_button.setEnabled(selected)
        self.clear_button.setEnabled(selected)

    def get_profile_name(self):
        if self.profile_getter:
            return self.profile_getter()
        if hasattr(self.owner, 'current_gamepad_profile') and self.mode == 'gamepad':
            return self.owner.current_gamepad_profile()
        return self.owner.config.get('active_profile')

    def refresh(self):
        from .i18n import tr

        selected = self.list.currentItem().data(Qt.UserRole) if self.list.currentItem() else None
        self.list.clear()
        self.rows.clear()
        profile_name = self.get_profile_name()
        family = profile_family(self.owner.config, self.owner.snapshot, profile_name)
        for trigger, entry in effective_mappings(self.owner.config, self.owner.snapshot, profile_name).items():
            if all(entry.get(g, {}).get('action', 'none') == 'none' for g in ('short', 'long')):
                continue
            text = trigger_label(trigger, family) + '  →  ' + binding_label(entry.get('short', {}), family)
            if entry.get('long', {}).get('action', 'none') != 'none':
                text += '    ' + tr('长按：') + binding_label(entry['long'], family)
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, trigger)
            item.setData(Qt.AccessibleTextRole, text)
            item.setToolTip(text)
            item.setForeground(QColor(TOKENS['accent'] if trigger in self.active else TOKENS['ink']))
            self.list.addItem(item)
            self.rows[trigger] = item
            if trigger == selected:
                self.list.setCurrentItem(item)
        self._sync_controls()

    def add(self):
        profile_name = self.get_profile_name()
        self.owner.edit_mapping('0', new=True, profile=profile_name, mode=self.mode)

    def edit(self):
        if self.list.currentItem():
            profile_name = self.get_profile_name()
            self.owner.edit_mapping(self.list.currentItem().data(Qt.UserRole), profile=profile_name, mode=self.mode)

    def clear(self):
        if self.list.currentItem():
            profile_name = self.get_profile_name()
            self.owner.mapping_change({'op': 'unbind', 'trigger': self.list.currentItem().data(Qt.UserRole), 'profile': profile_name})

    def feedback(self, data):
        active = set(data.get('active', []))
        if active == self.active:
            return
        self.active = active
        for trigger, item in self.rows.items():
            item.setForeground(QColor(TOKENS['accent'] if trigger in active else TOKENS['ink']))

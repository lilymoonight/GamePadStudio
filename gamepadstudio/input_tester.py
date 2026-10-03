"""GamepadTester Diagnostic Dashboard — real-time hardware telemetry."""
from collections import deque
import math
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QWidget, QLabel, QVBoxLayout, QHBoxLayout,
                               QGridLayout, QPushButton, QFrame, QSizePolicy)

from .controller_catalog import CATALOG, axis_labels, button_labels, button_order
from .controller_schematic import ControllerSchematic
from .glass import GlassPanel, IconButton, glyph, TOKENS, token_color, tag_style, MONO_FONT_STACK
from .test_widgets import StickGauge, TriggerGauge
from .i18n import tr

INSTRUMENT_MONO_STACK = MONO_FONT_STACK if sys.platform == 'darwin' else '"Cascadia Code", Consolas'

class StickHistory:
    """Bounded motion trail and a full-deflection radial envelope in 36 sectors."""
    def __init__(self):
        self.points = deque(maxlen=600)
        self.radii = [None] * 36

    def clear(self):
        self.points.clear()
        self.radii = [None] * 36

    def add(self, x, y, sweep=False):
        if not self.points or math.dist(self.points[-1], (x, y)) >= .004:
            self.points.append((x, y))
        radius = math.hypot(x, y)
        if sweep and radius >= .6:
            sector = int((math.atan2(y, x) % math.tau) / math.tau * 36) % 36
            self.radii[sector] = max(radius, self.radii[sector] or 0.)

    @property
    def coverage(self):
        return sum(r is not None for r in self.radii) / 36

    @property
    def error(self):
        if self.coverage < 1:
            return None
        return sum(abs(r - 1.) for r in self.radii) / 36 * 100


def label(value='', size=13, bold=False):
    widget = QLabel(value)
    widget.setWordWrap(True)
    widget.setMinimumWidth(0)
    widget.setStyleSheet(
        f'color: {TOKENS["ink"] if bold else TOKENS["ink_2"]}; '
        f'font-size: {size}px; font-weight: {700 if bold else 500};'
    )
    return widget


def card_heading(title, symbol, color):
    row = QHBoxLayout()
    row.setSpacing(8)
    icon = QLabel()
    icon.setPixmap(glyph(symbol, color).pixmap(18, 18))
    row.addWidget(icon)
    heading = label(title, 14, True)
    heading.setStyleSheet(
        f'color: {TOKENS["ink"]}; font-size: 14px; font-weight: 700; letter-spacing: -0.2px;'
    )
    row.addWidget(heading)
    row.addStretch()
    return row


class InputTester(QWidget):
    """Real-time diagnostic dashboard inspired by GamepadTester.cn."""

    def __init__(self, show_events, send_rumble=None, measure_stick=None,
                 export_diagnostic=None):
        super().__init__()
        self.send_rumble = send_rumble
        self.identity = None
        self.state = None
        self.family = 'generic'
        self.axis_names = axis_labels('generic')
        self.positions = [None, None]
        self.histories = [StickHistory(), StickHistory()]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        # ── Top Toolbar ───────────────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(12)
        self._toolbar_layout = toolbar

        self.device_name = label(tr('未连接手柄'), 19, True)
        self.device_name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.device_name.setToolTip(self.device_name.text())
        self.device_name.setStyleSheet(
            f'color: {TOKENS["ink"]}; font-size: 19px; font-weight: 800; letter-spacing: -0.4px;'
        )
        toolbar.addWidget(self.device_name, 1)

        self.raw_tag = label(tr('  OFFLINE · 未连接  '), 11, True)
        self.raw_tag.setWordWrap(False)
        self.raw_tag.setStyleSheet(tag_style(TOKENS['ink_3'], 0.12, 0.25))
        self.raw_tag.setToolTip(tr('SDL 原始标准轴值；不应用软件死区。'))
        toolbar.addWidget(self.raw_tag, 0, Qt.AlignTop)

        self.events_button = QPushButton(tr('活动记录'))
        self.events_button.setObjectName('testAction')
        self.events_button.setFixedHeight(24)
        self.events_button.setStyleSheet('font-size: 11px; min-height: 20px; padding: 0 4px; border: none; background: transparent;')
        self.events_button.setAccessibleName(tr('活动记录'))
        self.events_button.clicked.connect(lambda: show_events())
        toolbar.addWidget(self.events_button, 0, Qt.AlignTop)

        self.export_diagnostic_button = QPushButton(tr('导出诊断'))
        self.export_diagnostic_button.setObjectName('testAction')
        self.export_diagnostic_button.setFixedHeight(24)
        self.export_diagnostic_button.setStyleSheet('font-size: 11px; min-height: 20px; padding: 0 4px; border: none; background: transparent;')
        self.export_diagnostic_button.setAccessibleName(tr('导出诊断'))
        self.export_diagnostic_button.setToolTip(tr('只导出脱敏的设备能力与错误摘要；不包含原始日志或配置。'))
        self.export_diagnostic_button.setEnabled(export_diagnostic is not None)
        if export_diagnostic is not None:
            self.export_diagnostic_button.clicked.connect(lambda: export_diagnostic())
        toolbar.addWidget(self.export_diagnostic_button, 0, Qt.AlignTop)

        self.diagnostic_note = label(tr('仅含脱敏摘要'), 10)
        self.diagnostic_note.setWordWrap(False)
        self.diagnostic_note.setToolTip(self.export_diagnostic_button.toolTip())
        toolbar.addWidget(self.diagnostic_note, 0, Qt.AlignTop)
        layout.addLayout(toolbar)

        # Headless tool state controllers (auto-active; hidden from UI to eliminate noise)
        self.protect = IconButton('shield', tr('测试保护'), size=32)
        self.protect.setCheckable(True)
        self.protect.setChecked(True)
        self.protect.hide()

        self.trace = IconButton('wave', tr('显示摇杆运动轨迹'), size=32)
        self.trace.setCheckable(True)
        self.trace.setChecked(True)
        self.trace.toggled.connect(self.toggle_trace)
        self.trace.hide()

        self.sweep = IconButton('circle', tr('圆周测试'), size=32)
        self.sweep.setCheckable(True)
        self.sweep.toggled.connect(self.toggle_sweep)
        self.sweep.hide()

        # ── Dashboard Split: High-Density Telemetry Workbench ──────────
        # Hidden schematic for backward compatibility and test assertions
        self.diagram = ControllerSchematic(self)
        self.diagram.setFixedSize(400, 300)
        self.diagram.hide()
        self.touch = label('', 11)
        self.touch.hide()

        dashboard = QHBoxLayout()
        dashboard.setSpacing(14)

        # ── Panel 1: Analog Sticks & Drift Telemetry (Left, 50%) ───────
        analog_card = GlassPanel()
        a_layout = QVBoxLayout(analog_card)
        a_layout.setContentsMargins(18, 16, 18, 16)
        a_layout.setSpacing(12)

        analog_heading = card_heading(tr('摇杆坐标与运动轨迹'), 'circle', TOKENS['accent'])
        self.measure_stick_button = QPushButton(tr('RS 静止测量'))
        self.measure_stick_button.setObjectName('testAction')
        self.measure_stick_button.setStyleSheet('font-size: 11px; min-height: 20px; padding: 0 4px; border: none; background: transparent;')
        self.measure_stick_button.setAccessibleName(tr('右摇杆静止测量'))
        self.measure_stick_button.setToolTip(tr('松开右摇杆，测量偏移并建议居中容错'))
        self.measure_stick_button.setEnabled(False)
        self.measure_stick_button.setVisible(measure_stick is not None)
        if measure_stick is not None:
            self.measure_stick_button.clicked.connect(measure_stick)
        analog_heading.addWidget(self.measure_stick_button)
        a_layout.addLayout(analog_heading)

        # Dual Stick Radars
        stick_row = QHBoxLayout()
        stick_row.setSpacing(16)
        self.stick_gauges = [StickGauge(), StickGauge()]
        self.axis_readings = [label('X —  Y —', 10) for _ in range(2)]
        for i in range(2):
            col = QVBoxLayout()
            col.setSpacing(4)
            col.addWidget(self.stick_gauges[i], 1)
            name = label(tr('左摇杆 (LS)') if i == 0 else tr('右摇杆 (RS)'), 11, True)
            name.setAlignment(Qt.AlignCenter)
            col.addWidget(name)
            reading = self.axis_readings[i]
            reading.setAlignment(Qt.AlignCenter)
            reading.setStyleSheet(f'font: 11px {INSTRUMENT_MONO_STACK}; color: {TOKENS["ink_2"]}; font-weight: 600;')
            col.addWidget(reading)
            stick_row.addLayout(col, 1)
        a_layout.addLayout(stick_row, 1)

        self.sweep_reading = label('', 10)
        self.sweep_reading.setStyleSheet(f'color: {TOKENS["orange"]}; font-size: 11px; font-weight: 700;')
        self.sweep_reading.hide()
        self.sweep_reading.setToolTip(tr('完整覆盖 36 个方向后显示最大半径相对单位圆的平均绝对径向误差。'))
        a_layout.addWidget(self.sweep_reading)

        # Hairline divider
        div1 = QFrame()
        div1.setFixedHeight(1)
        div1.setStyleSheet(f"background: {TOKENS['border']};")
        a_layout.addWidget(div1)

        # Drift Analysis Section
        a_layout.addLayout(card_heading(tr('摇杆回中与死区分析'), 'circle', TOKENS['orange']))

        drift_row = QHBoxLayout()
        drift_row.setSpacing(16)
        self.drift_gauge = StickGauge(drift=True)
        self.drift_gauge.setMinimumSize(85, 85)
        drift_row.addWidget(self.drift_gauge)

        drift_info = QVBoxLayout()
        drift_info.setSpacing(4)
        drift_info.setAlignment(Qt.AlignVCenter)
        self.drift_status = label(tr('● 离线'), 12, True)
        self.drift_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.drift_status.setStyleSheet(f'color: {TOKENS["ink_3"]}; font-size: 12px; font-weight: 700;')
        drift_info.addWidget(self.drift_status)

        self.drift_reading = label(tr('偏移: —'), 11)
        self.drift_reading.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.drift_reading.setStyleSheet(f'font: 11px {INSTRUMENT_MONO_STACK}; color: {TOKENS["ink"]}; font-weight: 600;')
        drift_info.addWidget(self.drift_reading)

        self.drift_spec = label(tr('静止偏移参考；软件容错可在操作手感中调整。'), 10.5)
        self.drift_spec.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.drift_spec.setStyleSheet(f'color: {TOKENS["ink_3"]};')
        self.drift_reading.setToolTip(self.drift_spec.text())
        drift_info.addWidget(self.drift_spec)
        drift_row.addLayout(drift_info, 1)

        a_layout.addLayout(drift_row)
        dashboard.addWidget(analog_card, 1)

        # ── Panel 2: Button Matrix & Actuator Feedback (Right, 50%) ─────
        digital_card = GlassPanel()
        d_layout = QVBoxLayout(digital_card)
        d_layout.setContentsMargins(18, 16, 18, 16)
        d_layout.setSpacing(12)

        d_layout.addLayout(card_heading(tr('按键响应与动力反馈'), 'grid', TOKENS['accent']))

        # Mechanical Button Matrix
        matrix_box = QVBoxLayout()
        matrix_box.setSpacing(6)
        matrix_hdr = label(tr('TACTILE MATRIX // 全键位物理矩阵'), 10, True)
        matrix_hdr.setStyleSheet(f"color: {TOKENS['ink_3']}; font-weight: 700; letter-spacing: 0.5px;")
        matrix_box.addWidget(matrix_hdr)

        self.btn_grid = QGridLayout()
        self.btn_grid.setSpacing(6)
        self.btn_tiles = {}

        # 16 slots in 2 rows of 8
        init_names = button_labels('generic')
        def clean_lbl(k):
            raw = init_names.get(k, str(k))
            return (raw.replace('方向键 ', '').replace('D-Pad ', '')
                    .replace('  交叉', '').replace('  Cross', '')
                    .replace('  圆圈', '').replace('  Circle', '')
                    .replace('  方块', '').replace('  Square', '')
                    .replace('  三角', '').replace('  Triangle', '')
                    .replace('左摇杆按下', 'L3').replace('右摇杆按下', 'R3')
                    .replace('Left Stick Click', 'L3').replace('Right Stick Click', 'R3')
                    .replace('左缓冲键', 'LB').replace('右缓冲键', 'RB')
                    .replace('Left Bumper', 'LB').replace('Right Bumper', 'RB')
                    .replace('触摸板按键', 'TP').replace('Touchpad Click', 'TP')).strip()

        button_keys_row1 = [0, 1, 2, 3, 9, 10, 7, 8]
        button_keys_row2 = [11, 12, 13, 14, 4, 6, 5, 15]
        for col_idx, k in enumerate(button_keys_row1):
            tile = QLabel(clean_lbl(k))
            tile.setAlignment(Qt.AlignCenter)
            tile.setFixedHeight(30)
            tile.setStyleSheet(f"background: {TOKENS['void']}; color: {TOKENS['ink_2']}; font: 11px {INSTRUMENT_MONO_STACK}; font-weight: 600; border: 1px solid {TOKENS['border']}; border-radius: {TOKENS['r_sm']}px;")
            self.btn_grid.addWidget(tile, 0, col_idx)
            self.btn_tiles[k] = tile

        for col_idx, k in enumerate(button_keys_row2):
            tile = QLabel(clean_lbl(k))
            tile.setAlignment(Qt.AlignCenter)
            tile.setFixedHeight(30)
            tile.setStyleSheet(f"background: {TOKENS['void']}; color: {TOKENS['ink_2']}; font: 11px {INSTRUMENT_MONO_STACK}; font-weight: 600; border: 1px solid {TOKENS['border']}; border-radius: {TOKENS['r_sm']}px;")
            self.btn_grid.addWidget(tile, 1, col_idx)
            self.btn_tiles[k] = tile

        matrix_box.addLayout(self.btn_grid)

        # Status Strip under Button Matrix
        activity = QHBoxLayout()
        activity.setSpacing(8)
        self.live_dot = QLabel('●')
        self.live_dot.setStyleSheet(f'color: {TOKENS["ink_dim"]}; font-size: 11px;')
        activity.addWidget(self.live_dot)

        status_tag = QLabel(tr('实时响应:'))
        status_tag.setStyleSheet(f'color: {TOKENS["ink_3"]}; font-size: 11.5px; font-weight: 600;')
        activity.addWidget(status_tag)

        self.pressed_reading = label(tr('等待手柄接入...'), 11.5, True)
        self.pressed_reading.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.pressed_reading.setToolTip(self.pressed_reading.text())
        self.pressed_reading.setStyleSheet(f'color: {TOKENS["accent"]}; font-size: 11.5px; font-weight: 700;')
        activity.addWidget(self.pressed_reading, 1)

        self.key_count = label('—', 11)
        self.key_count.setStyleSheet(f'color: {TOKENS["ink_3"]}; font-size: 11px;')
        activity.addWidget(self.key_count)
        matrix_box.addLayout(activity)
        d_layout.addLayout(matrix_box)

        # Hairline divider
        div2 = QFrame()
        div2.setFixedHeight(1)
        div2.setStyleSheet(f"background: {TOKENS['border']};")
        d_layout.addWidget(div2)

        # Actuators Split (Triggers on left, Rumble on right)
        actuators = QHBoxLayout()
        actuators.setSpacing(16)

        # Linear Triggers
        trig_col = QVBoxLayout()
        trig_col.setSpacing(8)
        trig_col.addLayout(card_heading(tr('线性扳机'), 'bolt', TOKENS['purple']))
        self.trigger_description = label(tr('霍尔 / 压感行程深度'), 11)
        self.trigger_description.setStyleSheet(f"color: {TOKENS['ink_3']}; font-weight: 500;")
        trig_col.addWidget(self.trigger_description)

        trig_row = QHBoxLayout()
        trig_row.setSpacing(12)
        self.trigger_gauges = [TriggerGauge('LT'), TriggerGauge('RT')]
        for gauge in self.trigger_gauges:
            gauge.setToolTip(self.trigger_description.text())
        self.trigger_names = [label('LT', 11, True), label('RT', 11, True)]
        for gauge, name in zip(self.trigger_gauges, self.trigger_names):
            c = QVBoxLayout()
            c.setSpacing(4)
            c.addWidget(gauge, 1)
            name.setAlignment(Qt.AlignCenter)
            c.addWidget(name)
            trig_row.addLayout(c, 1)
        trig_col.addLayout(trig_row, 1)
        self.trigger_reading = label('', 10)
        self.trigger_reading.hide()
        trig_col.addWidget(self.trigger_reading)
        actuators.addLayout(trig_col, 1)

        # Haptic Rumble
        rumble_col = QVBoxLayout()
        rumble_col.setSpacing(8)
        rumble_col.addLayout(card_heading(tr('触觉马达'), 'wave', TOKENS['green']))
        self.rumble_description = label(tr('双声道触觉脉冲发生器'), 11)
        self.rumble_description.setStyleSheet(f"color: {TOKENS['ink_3']}; font-weight: 500;")
        rumble_col.addWidget(self.rumble_description)

        rumble_grid = QGridLayout()
        rumble_grid.setSpacing(8)
        self.rumble_buttons = []
        rumble_presets = [
            (tr('重震'), .85, 1, tr('重度触觉脉冲 (85%)')),
            (tr('轻震'), .25, 1, tr('轻度触觉脉冲 (25%)')),
            (tr('爆发'), .7, 2, tr('爆发脉冲 (70% 双段)')),
            (tr('脉冲'), .4, 3, tr('连续脉冲 (40% 三段)')),
        ]
        for i, (title, strength, count, tip) in enumerate(rumble_presets):
            btn = QPushButton(title)
            btn.setObjectName('testAction')
            btn.setMinimumHeight(34)
            btn.setEnabled(False)
            btn.setAccessibleName(title)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda checked=False, s=strength, c=count: self.rumble_pattern(s, c))
            rumble_grid.addWidget(btn, i // 2, i % 2)
            self.rumble_buttons.append(btn)
        rumble_col.addLayout(rumble_grid)

        self.motor_status = label(tr('未连接马达'), 11)
        self.motor_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.motor_status.setStyleSheet(f"color: {TOKENS['ink_3']}; font-size: 11px;")
        self.motor_status.setToolTip(self.rumble_description.text())
        rumble_col.addWidget(self.motor_status)
        rumble_col.addStretch(1)
        actuators.addLayout(rumble_col, 1)

        d_layout.addLayout(actuators, 1)
        dashboard.addWidget(digital_card, 1)

        layout.addLayout(dashboard, 1)
        self._dashboard_layout = dashboard
        self._card_layouts = (a_layout, d_layout)
        self._compact_layout = None
        self._fit_dashboard(self.width())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_card_layouts'):
            self._fit_dashboard(event.size().width())

    def _fit_dashboard(self, width):
        # Keep all gauges visible at the supported 960px workspace width while
        # allowing the normal card spacing to return on wider screens.
        compact = width < 900
        if compact == self._compact_layout:
            return
        self._compact_layout = compact
        self.layout().setSpacing(6 if compact else 14)
        self._toolbar_layout.setSpacing(4 if compact else 12)
        self.diagnostic_note.setVisible(not compact)
        self.drift_spec.setVisible(not compact)
        self.trigger_description.setVisible(not compact)
        self.rumble_description.setVisible(not compact)
        # The gauges expand inside their cards. In a compact workspace their
        # 120px preferred heights must not force the whole page to scroll.
        gauge_policy = QSizePolicy.Ignored if compact else QSizePolicy.Expanding
        for gauge in (*self.stick_gauges, *self.trigger_gauges):
            gauge.setSizePolicy(QSizePolicy.Expanding, gauge_policy)
        self._dashboard_layout.setSpacing(8 if compact else 14)
        margins = (14, 8, 14, 8) if compact else (18, 16, 18, 16)
        for card_layout in self._card_layouts:
            card_layout.setContentsMargins(*margins)
            card_layout.setSpacing(6 if compact else 12)

    def toggle_trace(self, checked):
        self.diagram.trace = checked
        for gauge in self.stick_gauges:
            gauge.trace = checked
            gauge.update()

    def toggle_sweep(self, checked):
        self.diagram.sweep = checked
        if not checked:
            self.sweep_reading.hide()
            for h in self.histories:
                h.clear()
        else:
            self.sweep_reading.show()

    def reset(self):
        for h in self.histories:
            h.clear()
        self.sweep_reading.setText('')
        for gauge in self.stick_gauges:
            gauge.update()
        self.diagram.update()

    def rumble_pattern(self, strength, count=1):
        if not self.send_rumble or not (self.state and self.state.get('rumble')):
            return
        from .studio_core import profile_scope
        identity = (profile_scope(self.state), self.state.get('instance_id'))
        def pulse():
            current = self.state
            if (current and current.get('rumble') and
                    identity == (profile_scope(current), current.get('instance_id'))):
                self.send_rumble(strength)
        self.send_rumble(strength)
        for i in range(1, count):
            QTimer.singleShot(i * 180, pulse)

    def update_state(self, state, collect=True):
        self.state = state
        from .stick_calibration import supports_right_stick
        self.measure_stick_button.setEnabled(supports_right_stick(state))
        rumble_supported = bool(state and state.get('rumble'))
        for button in self.rumble_buttons:
            button.setEnabled(rumble_supported)

        if not state:
            self.family = 'generic'
            self.axis_names = axis_labels(self.family)
            for title, name in zip(self.trigger_names, self.axis_names[4:6]):
                title.setText(name)
            names = button_labels(self.family)
            self.positions = [None, None]
            for h in self.histories:
                h.clear()
            self.raw_tag.setText(tr('  OFFLINE · 未连接  '))
            self.raw_tag.setStyleSheet(tag_style(TOKENS['ink_3'], 0.12, 0.25))
            self.device_name.setText(tr('未连接手柄'))
            self.device_name.setToolTip(self.device_name.text())
            self.pressed_reading.setText(tr('等待手柄接入...'))
            self.pressed_reading.setToolTip(self.pressed_reading.text())
            self.live_dot.setStyleSheet(f'color: {TOKENS["ink_dim"]}; font-size: 11px;')
            self.key_count.setText('—')
            self.drift_status.setText(tr('● 离线'))
            self.drift_status.setStyleSheet(f"color: {TOKENS['ink_3']}; font-size: 11px;")
            self.drift_reading.setText(tr('偏移: —'))
            self.motor_status.setText(tr('未连接马达'))
            self.motor_status.setStyleSheet(f"color: {TOKENS['ink_3']}; font-size: 11px;")
            self.diagram.set_state(None, self.histories)
            self.drift_gauge.set_position(None)
            for reading in self.axis_readings:
                reading.setText('X —  Y —')
            for g in self.stick_gauges:
                g.set_position(None)
            for g in self.trigger_gauges:
                g.set_value('', None)
            for k, tile in self.btn_tiles.items():
                tile.setText(names.get(k, str(k + 1)).replace('方向键 ', '').replace('D-Pad ', '')
                             .replace('左摇杆按下', 'L3').replace('右摇杆按下', 'R3')
                             .replace('Left Stick Click', 'L3').replace('Right Stick Click', 'R3'))
                tile.setVisible(k < 15)
                tile.setStyleSheet(f"background: {TOKENS['void']}; color: {TOKENS['ink_3']}; font: 11px 'Cascadia Code', Consolas; font-weight: 600; border: 1px solid {TOKENS['border']}; border-radius: {TOKENS['r_sm']}px;")
            return

        new_family = state.get('family', 'generic')
        if new_family != self.family:
            for h in self.histories:
                h.clear()
        self.family = new_family
        self.axis_names = axis_labels(self.family)
        if len(self.axis_names) >= 6:
            self.trigger_names[0].setText(self.axis_names[4])
            self.trigger_names[1].setText(self.axis_names[5])

        dev_name = state.get('name', tr('游戏控制器'))
        self.device_name.setText(dev_name)
        self.device_name.setToolTip(dev_name)
        self.raw_tag.setText(tr('  ONLINE · 已连接  '))
        self.raw_tag.setStyleSheet(tag_style(TOKENS['green'], 0.16, 0.40))
        self.live_dot.setStyleSheet(f'color: {TOKENS["green"]}; font-size: 11px;')
        if rumble_supported:
            self.motor_status.setText(tr('双马达独立频段 · 支持触觉'))
            self.motor_status.setStyleSheet(f"color: {TOKENS['green']}; font-size: 11px; font-weight: 600;")
        else:
            self.motor_status.setText(tr('当前设备不支持震动'))
            self.motor_status.setStyleSheet(f"color: {TOKENS['ink_3']}; font-size: 11px;")

        # Axis updates
        axes = state.get('axes', [0.] * 6)
        if len(axes) >= 4:
            lx, ly = axes[0], axes[1]
            rx, ry = axes[2], axes[3]
            self.positions = [(lx, ly), (rx, ry)]
            for i, (x, y) in enumerate(self.positions):
                self.histories[i].add(x, y, self.sweep.isChecked())
                self.stick_gauges[i].set_position((x, y), self.histories[i], self.trace.isChecked())
                self.axis_readings[i].setText(f'X {x:+.3f}  Y {y:+.3f}')

            # Drift reading for left stick
            self.drift_gauge.set_position((lx, ly))
            drift_val = math.hypot(lx, ly)
            self.drift_reading.setText(f"{tr('偏移:')} {drift_val * 100:.1f}%  (X {lx:+.3f}, Y {ly:+.3f})")
            if drift_val > 0.15:
                self.drift_status.setText(tr('● 异常偏移'))
                self.drift_status.setStyleSheet(f'color: {TOKENS["orange"]}; font-size: 11px; font-weight: 700;')
            else:
                self.drift_status.setText(tr('● 回中良好'))
                self.drift_status.setStyleSheet(f'color: {TOKENS["green"]}; font-size: 11px; font-weight: 700;')

        # Triggers
        if len(axes) >= 6:
            self.trigger_gauges[0].set_value(self.axis_names[4], axes[4])
            self.trigger_gauges[1].set_value(self.axis_names[5], axes[5])

        self.diagram.set_state(state, self.histories)
        names = button_labels(self.family, state.get('controller_type', 0))
        pressed = self.diagram.pressed
        available = self.diagram.available

        # Update mechanical button matrix tiles
        for k, tile in self.btn_tiles.items():
            if k in names:
                raw = (names[k].replace('方向键 ', '').replace('D-Pad ', '')
                       .replace('  交叉', '').replace('  Cross', '')
                       .replace('  圆圈', '').replace('  Circle', '')
                       .replace('  方块', '').replace('  Square', '')
                       .replace('  三角', '').replace('  Triangle', '')
                       .replace('左摇杆按下', 'L3').replace('右摇杆按下', 'R3')
                       .replace('Left Stick Click', 'L3').replace('Right Stick Click', 'R3')
                       .replace('左缓冲键', 'LB').replace('右缓冲键', 'RB')
                       .replace('Left Bumper', 'LB').replace('Right Bumper', 'RB')
                       .replace('触摸板按键', 'TP').replace('Touchpad Click', 'TP'))
                tile.setText(raw.strip())
            is_avail = available is None or k in available
            tile.setVisible(is_avail)
            if k in pressed:
                tile.setStyleSheet(
                    f"background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ff6d3b, stop:1 #ff5722); "
                    f"color: #ffffff; font: 11px 'Cascadia Code', Consolas; font-weight: 700; "
                    f"border: 1px solid rgba(255, 255, 255, 0.45); border-radius: {TOKENS['r_sm']}px;"
                )
            else:
                tile.setStyleSheet(
                    f"background: {TOKENS['void']}; color: {TOKENS['ink_2']}; font: 11px 'Cascadia Code', Consolas; font-weight: 600; "
                    f"border: 1px solid {TOKENS['border']}; border-radius: {TOKENS['r_sm']}px;"
                )

        if pressed:
            pressed_names = [names[b] for b in sorted(pressed) if b < len(names)]
            self.pressed_reading.setText(tr('按下: ') + '、'.join(pressed_names))
            self.key_count.setText(f'{len(pressed)} / {len(self.diagram.available)}')
        else:
            self.pressed_reading.setText(tr('等待按键操作...'))
            self.key_count.setText(f'0 / {len(self.diagram.available)}' if self.diagram.available else '—')
        self.pressed_reading.setToolTip(self.pressed_reading.text())

    def snapshot(self):
        return {
            'family': self.family,
            'raw_positions': list(self.positions),
            'triggers': [g.value for g in self.trigger_gauges],
            'pressed': sorted(self.diagram.pressed),
        }

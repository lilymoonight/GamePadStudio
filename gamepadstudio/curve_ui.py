"""Device-scoped response curve editing, with no writes before Apply."""
from __future__ import annotations

import copy
import math

from PySide6.QtCore import QPointF, QRectF, Qt, QSignalBlocker, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractSpinBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                              QHBoxLayout, QLabel, QPushButton, QScrollArea,
                              QSlider, QVBoxLayout, QWidget)

from .glass import TOKENS, Toggle
from .i18n import tr
from .response_curves import (curve_capabilities, curve_preset, evaluate_curve,
                              normalize_curve)
from .studio_core import device_config, profile_scope


CURVE_KINDS = {
    'trigger': ('trigger_curves', '扳机输入曲线'),
    'rumble': ('rumble_curves', '双马达振动曲线'),
    'trigger_rumble': ('trigger_rumble_curves', '扳机振动曲线'),
}


def supports_curve(state, kind):
    capabilities = curve_capabilities(state)
    return bool(capabilities['trigger_axes']) if kind == 'trigger' else bool(capabilities.get(kind))


def _identity(state):
    if not state:
        return None
    return profile_scope(state), state.get('instance_id')


def _caption(text):
    widget = QLabel(text)
    widget.setObjectName('caption')
    widget.setWordWrap(True)
    return widget


class CurvePlot(QWidget):
    """Fixed input knots with monotonic draggable output values."""

    points_changed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.spec = normalize_curve()
        self.raw_value = 0.
        self.processed_value = 0.
        self.live = False
        self.selected_point = 2
        self.dragging = False
        self.setMinimumSize(230, 200)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(tr('响应曲线图'))
        self.setAccessibleDescription(tr('拖动三个控制点，或用左右键选择、上下键调整。'))
        self.setToolTip(tr('拖动三个控制点，或用左右键选择、上下键调整。'))

    def graph_rect(self):
        return QRectF(37., 15., max(1., self.width() - 55.), max(1., self.height() - 48.))

    def set_curve(self, spec):
        self.spec = normalize_curve(spec)
        self.processed_value = evaluate_curve(self.raw_value, self.spec)
        self.update()

    def set_live_value(self, value, available=True):
        self.live = available
        self.raw_value = max(0., min(1., float(value)))
        self.processed_value = evaluate_curve(self.raw_value, self.spec)
        self.update()

    def point_position(self, index):
        graph = self.graph_rect()
        x = self.spec['deadzone'] + index * .25 * (self.spec['saturation'] - self.spec['deadzone'])
        return QPointF(graph.left() + x * graph.width(),
                       graph.bottom() - self.spec['points'][index] * graph.height())

    def move_point(self, index, value):
        if index not in (1, 2, 3):
            return
        points = list(self.spec['points'])
        points[index] = max(points[index - 1], min(points[index + 1], float(value)))
        if points == self.spec['points']:
            return
        self.spec['points'] = points
        self.processed_value = evaluate_curve(self.raw_value, self.spec)
        self.points_changed.emit(list(points))
        self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        index = min((1, 2, 3), key=lambda i: (self.point_position(i) - event.position()).manhattanLength())
        if (self.point_position(index) - event.position()).manhattanLength() <= 24:
            self.selected_point = index
            self.dragging = True
            self.setFocus()
            self.update()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.dragging:
            graph = self.graph_rect()
            self.move_point(self.selected_point, (graph.bottom() - event.position().y()) / graph.height())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.dragging = False
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Left, Qt.Key_Right):
            self.selected_point = max(1, min(3, self.selected_point + (1 if event.key() == Qt.Key_Right else -1)))
            self.update()
        elif event.key() in (Qt.Key_Up, Qt.Key_Down):
            step = .05 if event.modifiers() & Qt.ShiftModifier else .01
            self.move_point(self.selected_point, self.spec['points'][self.selected_point] +
                            (step if event.key() == Qt.Key_Up else -step))
        else:
            return super().keyPressEvent(event)
        event.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        graph = self.graph_rect()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(TOKENS['void']))
        painter.drawRoundedRect(graph.adjusted(-1, -1, 1, 1), 9, 9)
        for step in range(5):
            fraction = step / 4.
            painter.setPen(QPen(QColor(TOKENS['border']), 1))
            x, y = graph.left() + fraction * graph.width(), graph.bottom() - fraction * graph.height()
            painter.drawLine(QPointF(x, graph.top()), QPointF(x, graph.bottom()))
            painter.drawLine(QPointF(graph.left(), y), QPointF(graph.right(), y))
            painter.setPen(QColor(TOKENS['ink_3']))
            painter.drawText(QRectF(x - 18, graph.bottom() + 7, 36, 17), Qt.AlignCenter, str(int(fraction * 100)))
            painter.drawText(QRectF(0, y - 9, 29, 18), Qt.AlignRight | Qt.AlignVCenter, str(int(fraction * 100)))
        painter.setPen(QPen(QColor(TOKENS['border_hi']), 1, Qt.DashLine))
        painter.drawLine(graph.bottomLeft(), graph.topRight())
        path = QPainterPath()
        for step in range(101):
            value = step / 100.
            point = QPointF(graph.left() + value * graph.width(),
                            graph.bottom() - evaluate_curve(value, self.spec) * graph.height())
            path.moveTo(point) if step == 0 else path.lineTo(point)
        painter.setPen(QPen(QColor(TOKENS['accent']), 2.6))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        for index in (1, 2, 3):
            point = self.point_position(index)
            if self.hasFocus() and index == self.selected_point:
                painter.setPen(QPen(QColor(TOKENS['ink_2']), 1))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(point, 10, 10)
            painter.setPen(QPen(QColor(TOKENS['surface']), 2))
            painter.setBrush(QColor(TOKENS['accent']))
            painter.drawEllipse(point, 5, 5)
        if self.live:
            point = QPointF(graph.left() + self.raw_value * graph.width(),
                            graph.bottom() - self.processed_value * graph.height())
            painter.setPen(QPen(QColor(TOKENS['surface']), 2))
            painter.setBrush(QColor(TOKENS['cyan']))
            painter.drawEllipse(point, 5, 5)


class CurveDialog(QDialog):
    def __init__(self, owner, kind='trigger'):
        super().__init__(owner)
        if kind not in CURVE_KINDS:
            raise ValueError(kind)
        self.owner, self.kind = owner, kind
        self.setting_key, title = CURVE_KINDS[kind]
        self.device_identity = _identity(owner.snapshot)
        self.invalidated = False
        settings = device_config(owner.config, owner.snapshot)
        stored = settings.get(self.setting_key, {})
        stored = stored if isinstance(stored, dict) else {}
        self.channels = self._channels(owner.snapshot)
        self.drafts = {channel: normalize_curve(stored.get(channel)) for channel in self.channels}
        self.active_channel = self.channels[0] if self.channels else None
        self.setWindowTitle(tr(title))
        self.setStyleSheet(f"""
            QDoubleSpinBox {{ background: {TOKENS['elevated']}; color: {TOKENS['ink']};
                border: 1px solid {TOKENS['border_hi']}; border-radius: 6px;
                padding: 5px 4px; min-height: 22px; }}
            QDoubleSpinBox:focus {{ border-color: {TOKENS['accent']}; }}
        """)
        self.resize(540, 655 if kind == 'trigger' else 690)
        self.setMinimumWidth(340)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        heading = QLabel(tr(title))
        heading.setObjectName('heading')
        layout.addWidget(heading)
        self.device_label = _caption((owner.snapshot or {}).get('name', tr('未连接手柄')))
        layout.addWidget(self.device_label)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        self.body = body
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(8)
        explanation = ('仅影响本应用的映射触发；原生游戏手柄输入保持原样。' if kind == 'trigger' else
                       '调整本应用发出的振动反馈；游戏自身的振动由游戏控制。')
        content.addWidget(_caption(tr(explanation)))
        if kind == 'trigger_rumble':
            content.addWidget(_caption(tr('这是扳机马达振动，不会改变扳机阻力。')))
            enable_row = QHBoxLayout()
            enable_row.addWidget(QLabel(tr('应用反馈使用扳机振动')), 1)
            self.enabled_box = Toggle(tr('启用'))
            self.enabled_box.setChecked(settings.get('trigger_rumble_enabled') is True)
            enable_row.addWidget(self.enabled_box)
            content.addLayout(enable_row)
        else:
            self.enabled_box = None
        channel_row = QHBoxLayout()
        channel_row.addWidget(QLabel(tr('通道')))
        self.channel_combo = QComboBox()
        for channel in self.channels:
            self.channel_combo.addItem(self._channel_label(channel, owner.snapshot), channel)
        channel_row.addWidget(self.channel_combo, 1)
        content.addLayout(channel_row)
        presets = QHBoxLayout()
        presets.setSpacing(6)
        for name, text in (('linear', '线性'), ('sensitive', '灵敏'), ('precise', '精细')):
            control = QPushButton(tr(text))
            control.setObjectName('pill')
            control.clicked.connect(lambda checked=False, name=name: self.set_preset(name))
            presets.addWidget(control, 1)
        content.addLayout(presets)
        self.plot = CurvePlot()
        self.plot.points_changed.connect(self._points_changed)
        content.addWidget(self.plot)
        self.live_label = _caption('')
        content.addWidget(self.live_label)
        content.addWidget(_caption(tr('横轴：输入 % · 纵轴：输出 %')))
        numeric = QHBoxLayout()
        numeric.setSpacing(10)
        self.point_fields = []
        self.point_labels = []
        for index in (1, 2, 3):
            column = QVBoxLayout()
            column.setSpacing(5)
            point_label = _caption(tr('{percent}% 输入', percent=index * 25))
            self.point_labels.append(point_label)
            column.addWidget(point_label)
            field = QDoubleSpinBox()
            field.setRange(0, 100)
            field.setDecimals(1)
            field.setSuffix('%')
            field.setSingleStep(1)
            field.setButtonSymbols(QAbstractSpinBox.NoButtons)
            field.setAccessibleName(tr('{percent}% 输入的输出值', percent=index * 25))
            field.valueChanged.connect(lambda value, index=index: self.plot.move_point(index, value / 100.))
            self.point_fields.append(field)
            column.addWidget(field)
            numeric.addLayout(column, 1)
        content.addLayout(numeric)
        self.travel_controls = QWidget()
        travel = QHBoxLayout(self.travel_controls)
        travel.setContentsMargins(0, 0, 0, 0)
        self.deadzone_field = self._travel_field('起始死区', travel)
        self.saturation_field = self._travel_field('满量程位置', travel)
        content.addWidget(self.travel_controls)
        self.travel_controls.setVisible(kind == 'trigger')
        self.deadzone_field.valueChanged.connect(self._travel_changed)
        self.saturation_field.valueChanged.connect(self._travel_changed)
        self.preview_slider = None
        self.test_button = None
        if kind != 'trigger':
            preview = QHBoxLayout()
            preview.addWidget(QLabel(tr('测试力度')))
            self.preview_slider = QSlider(Qt.Horizontal)
            self.preview_slider.setRange(1, 100)
            self.preview_slider.setValue(55)
            self.preview_slider.setAccessibleName(tr('测试力度'))
            preview.addWidget(self.preview_slider, 1)
            self.test_button = QPushButton(tr('预览此通道'))
            self.test_button.clicked.connect(self.preview)
            preview.addWidget(self.test_button)
            content.addLayout(preview)
            content.addWidget(_caption(tr('短振动预览当前草稿，不会保存设置。')))
            self.preview_slider.valueChanged.connect(self.refresh_live)
        content.addStretch(1)
        area.setWidget(body)
        layout.addWidget(area, 1)
        self.status = _caption('')
        self.status.hide()
        layout.addWidget(self.status)
        self.controls = QDialogButtonBox(QDialogButtonBox.Reset | QDialogButtonBox.Cancel |
                                        QDialogButtonBox.Apply | QDialogButtonBox.Save)
        self.controls.button(QDialogButtonBox.Reset).setText(tr('重置'))
        self.controls.button(QDialogButtonBox.Reset).setToolTip(tr('重置通道'))
        self.controls.button(QDialogButtonBox.Apply).setText(tr('应用'))
        self.controls.button(QDialogButtonBox.Save).setText(tr('保存'))
        self.controls.button(QDialogButtonBox.Save).setToolTip(tr('保存并关闭'))
        self.controls.button(QDialogButtonBox.Save).setObjectName('primary')
        self.controls.button(QDialogButtonBox.Cancel).setText(tr('关闭'))
        self.controls.rejected.connect(self.reject)
        self.controls.button(QDialogButtonBox.Reset).clicked.connect(lambda: self.set_preset('linear'))
        self.controls.button(QDialogButtonBox.Apply).clicked.connect(self.apply)
        self.controls.accepted.connect(self.save)
        layout.addWidget(self.controls)
        self.channel_combo.currentIndexChanged.connect(self._channel_changed)
        self._load_channel()
        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.refresh_live)
        self.timer.start()
        self.refresh_live()

    def _channels(self, state):
        if self.kind == 'rumble':
            return ['low', 'high']
        if self.kind == 'trigger':
            axes = curve_capabilities(state)['trigger_axes']
            return [channel for channel, axis in (('left', 4), ('right', 5)) if axis in axes]
        return ['left', 'right']

    def _channel_label(self, channel, state):
        if self.kind == 'rumble':
            return tr('低频马达') if channel == 'low' else tr('高频马达')
        ps = (state or {}).get('family') in ('dualshock4', 'dualsense')
        return ('L2' if ps else 'LT') if channel == 'left' else ('R2' if ps else 'RT')

    @staticmethod
    def _travel_field(text, row):
        column = QVBoxLayout()
        column.setSpacing(5)
        column.addWidget(_caption(tr(text)))
        field = QDoubleSpinBox()
        field.setRange(0, 100)
        field.setDecimals(1)
        field.setSuffix('%')
        field.setButtonSymbols(QAbstractSpinBox.NoButtons)
        field.setAccessibleName(tr(text))
        column.addWidget(field)
        row.addLayout(column, 1)
        return field

    def _channel_changed(self):
        self.active_channel = self.channel_combo.currentData()
        self._load_channel()

    def _load_channel(self):
        spec = self.drafts.get(self.active_channel, normalize_curve())
        self.plot.set_curve(spec)
        self._sync_fields(spec)
        self.refresh_live()

    def _sync_fields(self, spec):
        for index, field in enumerate(self.point_fields, 1):
            input_percent = (spec['deadzone'] + index * .25 * (spec['saturation'] - spec['deadzone'])) * 100.
            percent = f'{input_percent:g}'
            self.point_labels[index - 1].setText(tr('{percent}% 输入', percent=percent))
            field.setAccessibleName(tr('{percent}% 输入的输出值', percent=percent))
            with QSignalBlocker(field):
                field.setRange(spec['points'][index - 1] * 100., spec['points'][index + 1] * 100.)
                field.setValue(spec['points'][index] * 100.)
        with QSignalBlocker(self.deadzone_field), QSignalBlocker(self.saturation_field):
            self.deadzone_field.setRange(0., spec['saturation'] * 100. - 1.)
            self.saturation_field.setRange(spec['deadzone'] * 100. + 1., 100.)
            self.deadzone_field.setValue(spec['deadzone'] * 100.)
            self.saturation_field.setValue(spec['saturation'] * 100.)

    def _points_changed(self, points):
        if self.active_channel is None:
            return
        self.drafts[self.active_channel]['points'] = list(points)
        self._sync_fields(self.drafts[self.active_channel])
        self.refresh_live()

    def _travel_changed(self):
        if self.active_channel is None:
            return
        spec = dict(self.drafts[self.active_channel], deadzone=self.deadzone_field.value() / 100.,
                    saturation=self.saturation_field.value() / 100.)
        self.drafts[self.active_channel] = normalize_curve(spec)
        self._load_channel()

    def set_preset(self, name):
        if self.active_channel is not None:
            self.drafts[self.active_channel] = curve_preset(name)
            self._load_channel()

    def guard_device(self):
        if (self.invalidated or self.device_identity is None or
                _identity(self.owner.snapshot) != self.device_identity or
                not supports_curve(self.owner.snapshot, self.kind)):
            self.invalidated = True
            self.body.setEnabled(False)
            for role in (QDialogButtonBox.Save, QDialogButtonBox.Apply, QDialogButtonBox.Reset):
                self.controls.button(role).setEnabled(False)
            if self.test_button:
                self.test_button.setEnabled(False)
            self.plot.setEnabled(False)
            self.channel_combo.setEnabled(False)
            for field in self.point_fields + [self.deadzone_field, self.saturation_field]:
                field.setEnabled(False)
            if self.enabled_box:
                self.enabled_box.setEnabled(False)
            self.status.setText(tr('输入设备已改变，请重新打开曲线编辑。'))
            self.status.setStyleSheet(f"color: {TOKENS['orange']};")
            self.status.show()
            self.plot.set_live_value(0, False)
            return False
        return True

    def refresh_live(self):
        if not self.guard_device():
            return
        if self.kind == 'trigger':
            axis = 4 if self.active_channel == 'left' else 5
            axes = (self.owner.snapshot or {}).get('axes') or []
            available = isinstance(axes, (list, tuple)) and axis < len(axes)
            raw = axes[axis] if available else 0.
        else:
            available = True
            raw = self.preview_slider.value() / 100. if self.preview_slider else .55
        try:
            raw = float(raw)
            if not math.isfinite(raw):
                raise ValueError('nonfinite input')
            raw = max(0., min(1., raw))
        except (TypeError, ValueError):
            raw, available = 0., False
        self.plot.set_live_value(raw, available)
        self.live_label.setText(tr('输入 {input}% → 输出 {output}%', input=f'{raw * 100:.0f}',
                                   output=f'{self.plot.processed_value * 100:.0f}') if available else
                                tr('等待扳机输入'))

    def apply(self):
        if not self.guard_device():
            return False
        settings = device_config(self.owner.config, self.owner.snapshot)
        curves = copy.deepcopy(settings.get(self.setting_key, {}))
        curves = curves if isinstance(curves, dict) else {}
        curves.update({channel: normalize_curve(spec) for channel, spec in self.drafts.items()})
        # Opening, editing, presets and preview never mutate the persisted map.
        self.owner.setting(self.setting_key, curves)
        if self.enabled_box is not None:
            self.owner.setting('trigger_rumble_enabled', self.enabled_box.isChecked())
        if hasattr(self.owner, 'refresh_device_settings_ui'):
            self.owner.refresh_device_settings_ui(self.owner.snapshot)
        self.status.setStyleSheet(f"color: {TOKENS['green']};")
        self.status.setText(tr('已应用到此手柄'))
        self.status.show()
        return True

    def save(self):
        if self.apply():
            self.accept()

    def preview(self):
        if not self.guard_device() or self.active_channel is None:
            return
        preview = getattr(self.owner, 'preview_response_curve', None)
        if preview:
            result = preview(self.kind, self.active_channel,
                             normalize_curve(self.drafts[self.active_channel]),
                             self.preview_slider.value() / 100.)
            self.status.setText(tr('已预览此通道') if result is not False else tr('此设备暂时无法预览振动。'))
            self.status.setStyleSheet(f"color: {TOKENS['ink_2']};")
            self.status.show()

    def done(self, result):
        self.timer.stop()
        super().done(result)

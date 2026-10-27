"""Live SDL controls drawn over the unified standard XInput controller SVG (`reference_xbox.svg`)."""
import math
from pathlib import Path

from PySide6.QtCore import Qt, QPointF, QRectF, QSize, QByteArray
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget, QSizePolicy

from .controller_catalog import button_labels
from .controller_photo import tint_xbox, XINPUT_SVG_FILE, XINPUT_VIEWBOX
from .glass import TOKENS, token_color


ASSETS = Path(__file__).resolve().parent / 'assets'


def source_family(family):
    return 'xbox'


class ControllerSchematic(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.family = 'generic'
        self.names = button_labels('generic')
        self.available = set()
        self.pressed = set()
        self.axes = []
        self.histories = []
        self.trace = True
        self.sweep = False
        self._renderer_instance = None
        self.setMinimumSize(220, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAccessibleName('手柄按键和摇杆实时示意图')
        self.setToolTip('通用 XInput 键位示意')

    def set_state(self, state, histories):
        self.family = state.get('family', 'generic') if state else 'generic'
        self.names = button_labels(self.family, state.get('controller_type', 0) if state else 0)
        self.available = set(state.get('available_buttons', [])) if state else set()
        self.pressed = set(state.get('buttons', [])) & self.available if state else set()
        self.axes = state.get('axes', []) if state else []
        self.histories = histories
        description = '、'.join(self.names[i] for i in sorted(self.pressed))
        self.setAccessibleDescription(description or '没有按键按下')
        self.update()

    def sizeHint(self):
        return QSize(500, 390)

    def _renderer(self):
        if self._renderer_instance is None:
            raw = (ASSETS / XINPUT_SVG_FILE).read_bytes()
            tinted = tint_xbox(raw)
            self._renderer_instance = QSvgRenderer(QByteArray(tinted))
        return self._renderer_instance

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        vx, vy, vw, vh = XINPUT_VIEWBOX
        margin = 10
        scale = min((self.width() - 2 * margin) / vw, (self.height() - 2 * margin) / vh)
        if scale <= 0:
            return
        target = QRectF(0, 0, vw * scale, vh * scale)
        target.moveCenter(QRectF(self.rect()).center())

        # Ambient backdrop glow
        halo = QPainterPath()
        halo.addRoundedRect(target.adjusted(-8, -8, 8, 8), 16, 16)
        painter.fillPath(halo, token_color('accent', 12))

        painter.translate(target.topLeft())
        painter.scale(scale, scale)
        self._renderer().render(painter, QRectF(0, 0, vw, vh))
        painter.translate(-vx, -vy)
        self._xbox(painter)
        painter.end()

    def _glow(self, p, x, y, radius, color=TOKENS['accent']):
        c = QColor(color)
        p.setPen(QPen(c, max(1.4, radius * .16)))
        fill = QColor(c)
        fill.setAlpha(120)
        p.setBrush(fill)
        p.drawEllipse(QPointF(x, y), radius, radius)

    def _pill(self, p, x, y, width, height, color=TOKENS['accent']):
        c = QColor(color)
        c.setAlpha(175)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(x - width / 2, y - height / 2, width, height), height / 3, height / 3)

    def _label(self, p, text, x, y, size, color=TOKENS['ink_2'], bold=True):
        font = QFont('Arial')
        font.setPixelSize(size)
        font.setWeight(QFont.Bold if bold else QFont.Normal)
        p.setFont(font)
        p.setPen(QColor(color))
        p.drawText(QRectF(x - 17, y - size * .9, 34, size * 1.6), Qt.AlignCenter, text)

    def _stick(self, p, center, axis, button, history_index, radius):
        if button in self.pressed:
            self._glow(p, center.x(), center.y(), radius * .95)
        if self.trace and history_index < len(self.histories):
            points = list(self.histories[history_index].points)
            if len(points) > 1:
                trail = QPainterPath(center + QPointF(points[0][0] * radius * .34, points[0][1] * radius * .34))
                for x, y in points[1:]:
                    trail.lineTo(center + QPointF(x * radius * .34, y * radius * .34))
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(token_color('accent_lo', 135), max(1, radius * .055)))
                p.drawPath(trail)
        if self.sweep and history_index < len(self.histories):
            p.setPen(QPen(QColor(TOKENS['amber']), max(1, radius * .05)))
            for i, value in enumerate(self.histories[history_index].radii):
                if value is not None:
                    angle = (i + .5) * math.tau / 36
                    p.drawPoint(center + QPointF(value * radius * 1.3 * math.cos(angle),
                                                 value * radius * 1.3 * math.sin(angle)))
        x = self.axes[axis] if len(self.axes) > axis else 0
        y = self.axes[axis + 1] if len(self.axes) > axis + 1 else 0
        if abs(x) + abs(y) >= .015:
            dot = center + QPointF(max(-1, min(1, x)) * radius * .35,
                                   max(-1, min(1, y)) * radius * .35)
            p.setPen(QPen(QColor(TOKENS['ink']), max(1, radius * .08)))
            p.setBrush(QColor(TOKENS['accent_lo']))
            p.drawEllipse(dot, radius * .21, radius * .21)

    def _xbox(self, p):
        face = [(0, 330, 181, 'A', TOKENS['green']),
                (1, 348, 161, 'B', TOKENS['red']),
                (2, 310, 162, 'X', TOKENS['accent']),
                (3, 329, 140, 'Y', TOKENS['amber'])]
        for button, x, y, name, color in face:
            if button in self.pressed:
                self._glow(p, x, y, 12, color)
            self._label(p, name, x, y, 12, TOKENS['ink'] if button in self.pressed else TOKENS['ink_2'])
        for button, x, y, name in ((9, 138.5, 77, 'LB'), (10, 302.5, 77, 'RB')):
            if button in self.pressed:
                self._pill(p, x, y - 1, 42, 15)
                self._label(p, name, x, y, 10, TOKENS['ink'])
        for button, x, y, w, h in ((11, 166, 221, 14, 20), (12, 166, 254, 14, 20),
                                   (13, 149, 238, 20, 14), (14, 183, 238, 20, 14)):
            if button in self.pressed:
                self._pill(p, x, y, w, h)
        for button, x, y, r in ((4, 188, 162, 9), (5, 220.5, 125, 15),
                                (6, 253, 162, 9), (15, 220.5, 188, 9)):
            if button in self.pressed:
                self._glow(p, x, y, r)
        for axis, x in ((4, 138.5), (5, 302.5)):
            value = self.axes[axis] if len(self.axes) > axis else 0
            if value > .01:
                self._pill(p, x, 29, 31, 35 * min(1, value))
        self._stick(p, QPointF(113, 160), 0, 7, 0, 28)
        self._stick(p, QPointF(278, 238), 2, 8, 1, 28)

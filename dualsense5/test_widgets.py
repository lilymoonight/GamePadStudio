"""GamepadTester Live Instruments — Stick radar and trigger gauges."""
from PySide6.QtCore import Qt, QPointF, QRectF, QSize
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QLinearGradient
from PySide6.QtWidgets import QWidget, QSizePolicy
from .glass import TOKENS, token_color


class StickGauge(QWidget):
    """Circular stick coordinates radar matching GamepadTester.cn DriftTest."""
    def __init__(self, parent=None, drift=False):
        super().__init__(parent)
        self.position = None
        self.history = None
        self.trace = False
        self.drift = drift
        self.setMinimumSize(70, 70)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAccessibleName('摇杆回中图' if drift else '摇杆坐标图')

    def sizeHint(self):
        return QSize(120, 120)

    def set_position(self, position, history=None, trace=False):
        self.position = position
        self.history = history
        self.trace = trace
        self.setAccessibleDescription('X %.4f，Y %.4f' % position if position else '未连接')
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        radius = max(20, min(self.width(), self.height()) / 2 - 6)

        # Radar dish background (GamepadTester stone dish)
        p.setPen(QPen(QColor(TOKENS['border_hi']), 1.5))
        p.setBrush(QColor(TOKENS['base']))
        p.drawEllipse(center, radius, radius)

        # Concentric guide circle (50% boundary)
        p.setPen(QPen(QColor(TOKENS['overlay']), 1, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(center, radius * 0.5, radius * 0.5)

        # Subtle crosshairs
        p.setPen(QPen(QColor(TOKENS['border_hi']), 1))
        p.drawLine(QPointF(center.x() - radius, center.y()), QPointF(center.x() + radius, center.y()))
        p.drawLine(QPointF(center.x(), center.y() - radius), QPointF(center.x(), center.y() + radius))

        if self.drift:
            # Safe zone 12% drift boundary (GamepadTester green circle)
            p.setPen(QPen(QColor(TOKENS['green']), 1.2, Qt.DashLine))
            fill_green = QColor(TOKENS['green'])
            fill_green.setAlpha(20)
            p.setBrush(fill_green)
            p.drawEllipse(center, radius * .12, radius * .12)

        # Stick motion history trail
        if self.trace and self.history and len(self.history.points) > 1:
            points = list(self.history.points)
            path = QPainterPath(center + QPointF(points[0][0] * radius, points[0][1] * radius))
            for x, y in points[1:]:
                path.lineTo(center + QPointF(x * radius, y * radius))
            p.setBrush(Qt.NoBrush)
            trail_col = QColor(TOKENS['accent'])
            trail_col.setAlpha(170)
            p.setPen(QPen(trail_col, 1.8))
            p.drawPath(path)

        x, y = self.position if self.position else (0., 0.)
        dot = center + QPointF(max(-1, min(1, x)) * radius, max(-1, min(1, y)) * radius)

        # Outer glowing halo
        p.setPen(Qt.NoPen)
        halo = QColor(TOKENS['accent'])
        halo.setAlpha(55)
        p.setBrush(halo)
        p.drawEllipse(dot, 12, 12)

        # Middle ring
        p.setBrush(QColor(TOKENS['accent']))
        p.drawEllipse(dot, 6, 6)

        # Core bright white center
        p.setBrush(QColor('#ffffff'))
        p.drawEllipse(dot, 2.5, 2.5)


class TriggerGauge(QWidget):
    """Vertical analog pressure gauge matching GamepadTester.cn TriggerTest."""
    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.name = name
        self.value = None
        self.setMinimumSize(50, 80)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def sizeHint(self):
        return QSize(80, 120)

    def set_value(self, name, value):
        self.name = name
        self.value = value
        self.setAccessibleName(name + ' 原始扳机行程')
        self.setAccessibleDescription('%.4f' % value if value is not None else '未连接')
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = QRectF(self.width() / 2 - 20, 6, 40, max(36, self.height() - 32))

        # Track background (stone-800 with stone-700 border)
        p.setPen(QPen(QColor(TOKENS['border_hi']), 1.2))
        p.setBrush(QColor(TOKENS['elevated']))
        p.drawRoundedRect(track, 10, 10)

        # Value bar (GamepadTester violet/blue vertical fill)
        if self.value is not None and self.value > 0:
            amount = min(1, max(0, self.value)) * (track.height() - 4)
            bar_rect = QRectF(track.left() + 2, track.bottom() - 2 - amount, track.width() - 4, max(4, amount))
            grad = QLinearGradient(bar_rect.topLeft(), bar_rect.bottomLeft())
            grad.setColorAt(0, QColor(TOKENS['accent']))
            grad.setColorAt(1, QColor(TOKENS['amber']))
            p.setPen(Qt.NoPen)
            p.setBrush(grad)
            p.drawRoundedRect(bar_rect, 8, 8)

        # Tabular reading label (GamepadTester style percentage)
        p.setPen(QColor(TOKENS['ink']))
        font = p.font()
        font.setFamily('Cascadia Code, Cascadia Mono, Consolas, monospace')
        font.setPixelSize(11)
        font.setBold(True)
        p.setFont(font)
        display_val = f'{self.value * 100:.0f}%' if self.value is not None else '—'
        p.drawText(QRectF(0, self.height() - 22, self.width(), 18), Qt.AlignCenter, display_val)

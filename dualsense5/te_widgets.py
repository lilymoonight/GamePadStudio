"""Teenage Engineering & Dieter Rams inspired tactile hardware widgets.

Custom interactive controls:
- RotaryKnob: Physical knurled dial with pointer notch, tick scale, drag & wheel support.
- RockerSwitch: Mechanical dual-position rocker toggle with debossed I/O and LED pip.
- DotMatrixDisplay: Backlit retro LCD matrix readout for telemetry & device info.
- SpeakerGrille: Perforated acoustic / cooling vent dot matrix.
- RulerScale: Engineering millimeter callout rule.
"""
import math
from PySide6.QtCore import Qt, QPointF, QRectF, QSize, Signal
from PySide6.QtGui import (QColor, QFont, QLinearGradient, QPainter,
                           QPainterPath, QPen, QRadialGradient)
from PySide6.QtWidgets import QWidget, QSizePolicy

from .glass import TOKENS


class RotaryKnob(QWidget):
    """Tactile industrial rotary knob (Teenage Engineering / Synthesizer style).
    
    Click and drag vertically or use the mouse wheel to rotate.
    """
    valueChanged = Signal(float)

    def __init__(self, title="INTENSITY", lo=0.0, hi=1.0, value=0.5, unit="%",
                 color="#ff5722", size=72, parent=None):
        super().__init__(parent)
        self.title = title
        self.lo = lo
        self.hi = hi
        self._value = max(lo, min(hi, value))
        self.unit = unit
        self.knob_color = color
        self.knob_size = size
        self.setFixedSize(size + 24, size + 50)
        self.setCursor(Qt.SizeVerCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.drag_start_y = None
        self.drag_start_val = None

    def value(self):
        return self._value

    def setValue(self, val):
        new_val = max(self.lo, min(self.hi, val))
        if abs(new_val - self._value) > 1e-4:
            self._value = new_val
            self.valueChanged.emit(self._value)
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_start_y = event.position().y()
            self.drag_start_val = self._value

    def mouseMoveEvent(self, event):
        if self.drag_start_y is not None:
            dy = self.drag_start_y - event.position().y()
            span = self.hi - self.lo
            step = (span / 180.0) * dy
            self.setValue(self.drag_start_val + step)

    def mouseReleaseEvent(self, event):
        self.drag_start_y = None

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120.0
        step_size = (self.hi - self.lo) * 0.05
        self.setValue(self._value + steps * step_size)
        event.accept()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        center_x = w / 2.0
        center_y = self.knob_size / 2.0 + 8.0
        radius = self.knob_size / 2.0

        # Title (engraved uppercase label above knob)
        p.setFont(QFont("Arial", 8, QFont.Bold))
        p.setPen(QColor(TOKENS['ink_3']))
        p.drawText(QRectF(0, 0, w, 14), Qt.AlignCenter, self.title.upper())

        # Scale tick marks around dial (-135 deg to +135 deg, 270 deg total sweep)
        frac = (self._value - self.lo) / (self.hi - self.lo) if self.hi > self.lo else 0.0
        start_angle = 135.0
        sweep_total = 270.0

        tick_count = 11
        for i in range(tick_count):
            t_frac = i / (tick_count - 1)
            deg = start_angle + t_frac * sweep_total
            rad = math.radians(deg)
            r_inner = radius + 3.0
            r_outer = radius + 7.0
            is_active = t_frac <= frac + 0.01

            p.setPen(QPen(QColor(self.knob_color if is_active else TOKENS['border_hi']),
                          1.6 if is_active else 1.0))
            p.drawLine(QPointF(center_x + r_inner * math.cos(rad), center_y + r_inner * math.sin(rad)),
                       QPointF(center_x + r_outer * math.cos(rad), center_y + r_outer * math.sin(rad)))

        # Cylindrical Knob Body - Drop shadow
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 70))
        p.drawEllipse(QPointF(center_x, center_y + 2.5), radius, radius)

        # Cylindrical Knob Body - Outer milled rim
        rim_grad = QLinearGradient(center_x - radius, center_y - radius, center_x + radius, center_y + radius)
        rim_grad.setColorAt(0.0, QColor("#3a3633"))
        rim_grad.setColorAt(1.0, QColor("#1c1a18"))
        p.setBrush(rim_grad)
        p.setPen(QPen(QColor(TOKENS['border_hi']), 1.0))
        p.drawEllipse(QPointF(center_x, center_y), radius, radius)

        # Knurled knurling teeth around knob perimeter
        teeth_count = 24
        p.setPen(QPen(QColor(0, 0, 0, 80), 1.0))
        for t in range(teeth_count):
            ang = t * (math.tau / teeth_count)
            p.drawLine(QPointF(center_x + (radius - 3.5) * math.cos(ang), center_y + (radius - 3.5) * math.sin(ang)),
                       QPointF(center_x + (radius - 0.5) * math.cos(ang), center_y + (radius - 0.5) * math.sin(ang)))

        # Top Face of the Knob (tactile orange or chalk)
        top_radius = radius - 4.5
        top_grad = QRadialGradient(center_x - 4, center_y - 4, top_radius * 1.2)
        c_base = QColor(self.knob_color)
        c_hi = c_base.lighter(130)
        c_lo = c_base.darker(120)
        top_grad.setColorAt(0.0, c_hi)
        top_grad.setColorAt(0.7, c_base)
        top_grad.setColorAt(1.0, c_lo)

        p.setBrush(top_grad)
        p.setPen(QPen(c_lo, 1.0))
        p.drawEllipse(QPointF(center_x, center_y), top_radius, top_radius)

        # Inner pointer notch line
        cur_angle = start_angle + frac * sweep_total
        cur_rad = math.radians(cur_angle)
        needle_pen = QPen(QColor("#ffffff"), 2.2, Qt.SolidLine, Qt.RoundCap)
        p.setPen(needle_pen)
        p.drawLine(QPointF(center_x + (top_radius * 0.3) * math.cos(cur_rad),
                           center_y + (top_radius * 0.3) * math.sin(cur_rad)),
                   QPointF(center_x + (top_radius * 0.9) * math.cos(cur_rad),
                           center_y + (top_radius * 0.9) * math.sin(cur_rad)))

        # Numeric Readout Box below knob (Cascadia / Swiss mono style)
        val_str = f"{round(self._value * 100)}%" if self.unit == "%" else f"{self._value:.2f}{self.unit}"
        text_rect = QRectF(0, center_y + radius + 10, w, 18)

        p.setBrush(QColor(TOKENS['elevated']))
        p.setPen(QPen(QColor(TOKENS['border']), 1.0))
        p.drawRoundedRect(text_rect.adjusted(w * 0.15, 0, -w * 0.15, 0), 3, 3)

        p.setFont(QFont("Cascadia Mono, Consolas, Courier New", 9, QFont.Bold))
        p.setPen(QColor(TOKENS['ink']))
        p.drawText(text_rect, Qt.AlignCenter, val_str)


class RockerSwitch(QWidget):
    """Physical rectangular rocker switch (Teenage Engineering mechanical switch).
    
    Clicking flips the rocker switch with debossed [I / O] markings and active LED pip.
    """
    toggled = Signal(bool)

    def __init__(self, label="POWER", checked=False, parent=None):
        super().__init__(parent)
        self.label = label
        self._checked = checked
        self.setFixedSize(54, 86)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)

    def isChecked(self):
        return self._checked

    def setChecked(self, state):
        state = bool(state)
        if self._checked != state:
            self._checked = state
            self.toggled.emit(self._checked)
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setChecked(not self._checked)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        # Top Label
        p.setFont(QFont("Arial", 8, QFont.Bold))
        p.setPen(QColor(TOKENS['ink_3']))
        p.drawText(QRectF(0, 0, w, 14), Qt.AlignCenter, self.label.upper())

        # Recessed switch housing bezel
        housing = QRectF(10, 18, 34, 52)
        p.setPen(QPen(QColor(TOKENS['border_hi']), 1.2))
        p.setBrush(QColor("#181615"))
        p.drawRoundedRect(housing, 4, 4)

        # Rocker Actuator (tilts up when ON, tilts down when OFF)
        actuator = housing.adjusted(2.5, 2.5, -2.5, -2.5)
        top_half = QRectF(actuator.left(), actuator.top(), actuator.width(), actuator.height() / 2)
        bot_half = QRectF(actuator.left(), actuator.top() + actuator.height() / 2, actuator.width(), actuator.height() / 2)

        p.setPen(Qt.NoPen)
        if self._checked:
            # Pressed inward at bottom, raised at top
            p.setBrush(QColor("#3d3936"))
            p.drawRoundedRect(top_half, 3, 3)
            p.setBrush(QColor("#242220"))
            p.drawRoundedRect(bot_half, 3, 3)

            # Highlight edge on top
            p.setPen(QPen(QColor("#615c57"), 1.0))
            p.drawLine(top_half.topLeft() + QPointF(2, 0), top_half.topRight() - QPointF(2, 0))
        else:
            # Raised at bottom, pressed inward at top
            p.setBrush(QColor("#242220"))
            p.drawRoundedRect(top_half, 3, 3)
            p.setBrush(QColor("#3d3936"))
            p.drawRoundedRect(bot_half, 3, 3)

            # Highlight edge on bottom
            p.setPen(QPen(QColor("#615c57"), 1.0))
            p.drawLine(bot_half.bottomLeft() + QPointF(2, 0), bot_half.bottomRight() - QPointF(2, 0))

        # Debossed I and O symbols
        p.setFont(QFont("Cascadia Mono, Arial", 8, QFont.Bold))
        p.setPen(QColor(TOKENS['ink'] if self._checked else TOKENS['ink_dim']))
        p.drawText(top_half, Qt.AlignCenter, "I")
        p.setPen(QColor(TOKENS['ink'] if not self._checked else TOKENS['ink_dim']))
        p.drawText(bot_half, Qt.AlignCenter, "O")

        # Bottom LED Pip
        led_y = 76
        led_color = QColor(TOKENS['accent'] if self._checked else TOKENS['border_hi'])
        p.setPen(Qt.NoPen)
        p.setBrush(led_color)
        p.drawEllipse(QPointF(w / 2.0, led_y), 2.5, 2.5)

        if self._checked:
            glow = QRadialGradient(QPointF(w / 2.0, led_y), 6)
            glow.setColorAt(0, QColor(255, 87, 34, 120))
            glow.setColorAt(1, QColor(0, 0, 0, 0))
            p.setBrush(glow)
            p.drawEllipse(QPointF(w / 2.0, led_y), 6, 6)


class DotMatrixDisplay(QWidget):
    """Backlit retro dot-matrix LCD telemetry display.
    
    Shows stick coordinates, polling rates, latency, or device specs with grid raster.
    """

    def __init__(self, title="TELEMETRY MATRIX", lines=None, color="#22c55e", parent=None):
        super().__init__(parent)
        self.title = title
        self.lines = lines or [("STATUS", "OFFLINE"), ("LATENCY", "-- ms"), ("POLL", "0 Hz")]
        self.screen_color = QColor(color)
        self.setFixedHeight(116)
        self.setMinimumWidth(180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_lines(self, lines):
        self.lines = lines
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)

        # Outer milled frame
        p.setPen(QPen(QColor(TOKENS['border_hi']), 1.2))
        p.setBrush(QColor("#141211"))
        p.drawRoundedRect(rect, 4, 4)

        # Screen inner glass
        screen = rect.adjusted(6, 6, -6, -6)
        p.setPen(QPen(QColor("#242220"), 1.0))
        p.setBrush(QColor("#0e0d0c"))
        p.drawRoundedRect(screen, 3, 3)

        # Subtle LCD dot matrix texture overlay
        p.setPen(QPen(QColor(255, 255, 255, 6), 1.0))
        step = 4
        y_int = int(screen.top()) + 2
        while y_int < int(screen.bottom()) - 2:
            p.drawLine(screen.left() + 2, y_int, screen.right() - 2, y_int)
            y_int += step

        # Header tag
        p.setFont(QFont("Arial", 7, QFont.Bold))
        p.setPen(QColor(TOKENS['ink_3']))
        p.drawText(QRectF(screen.left() + 8, screen.top() + 4, screen.width() - 16, 12),
                   Qt.AlignLeft, self.title.upper())

        # Matrix lines
        p.setFont(QFont("Cascadia Mono, Consolas", 9, QFont.Bold))
        y_cursor = screen.top() + 20
        line_height = 18

        for k, v in self.lines:
            # Key in dim amber/green
            p.setPen(QColor(self.screen_color.darker(125)))
            p.drawText(QRectF(screen.left() + 8, y_cursor, 90, line_height), Qt.AlignLeft | Qt.AlignVCenter, str(k))

            # Value in bright backlit glowing text
            p.setPen(self.screen_color)
            p.drawText(QRectF(screen.left() + 100, y_cursor, screen.width() - 108, line_height),
                       Qt.AlignRight | Qt.AlignVCenter, str(v))
            y_cursor += line_height


class SpeakerGrille(QWidget):
    """Teenage Engineering OP-1 style perforated round acoustic vent holes."""

    def __init__(self, cols=6, rows=6, hole_r=2.2, spacing=7, parent=None):
        super().__init__(parent)
        self.cols = cols
        self.rows = rows
        self.hole_r = hole_r
        self.spacing = spacing
        w = round(cols * spacing + 10)
        h = round(rows * spacing + 10)
        self.setFixedSize(w, h)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        for r in range(self.rows):
            for c in range(self.cols):
                cx = 8 + c * self.spacing
                cy = 8 + r * self.spacing

                # Hole drop shadow (hole depth)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(0, 0, 0, 180))
                p.drawEllipse(QPointF(cx, cy), self.hole_r, self.hole_r)

                # Lower highlight rim (milled metal edge catching light)
                p.setPen(QPen(QColor(255, 255, 255, 22), 0.75))
                p.setBrush(Qt.NoBrush)
                p.drawArc(QRectF(cx - self.hole_r, cy - self.hole_r, self.hole_r * 2, self.hole_r * 2), 180 * 16, 180 * 16)

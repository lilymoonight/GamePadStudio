"""Live SDL controls drawn over the actual GamepadTester.cn controller SVGs.

The three source diagrams are stored offline in assets/reference_*.svg. Their
browser CSS is resolved to SVG styles so Qt can draw them without a web view.
"""
import math
from pathlib import Path

from PySide6.QtCore import Qt, QPointF, QRectF, QSize, QByteArray
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget, QSizePolicy

from .controller_catalog import button_labels
from .controller_photo import tint_playstation, tint_xbox, tint_switch
from .glass import TOKENS, token_color


ASSETS = Path(__file__).resolve().parent / 'assets'
SVG_FILES = {
    'xbox': 'reference_xbox.svg',
    'playstation': 'reference_playstation.svg',
    'switch': 'reference_switch.svg',
}
# The PlayStation source has generous empty margins in its 128 × 128 viewBox.
# Cropping only the viewBox keeps every original path and makes it readable.
VIEWBOX = {
    'xbox': (0, 0, 441, 383),
    'playstation': (0, 19, 128, 84),
    'switch': (0, 0, 400, 250),
}


def source_family(family):
    if family in ('dualsense', 'dualshock4'):
        return 'playstation'
    if family == 'switch':
        return 'switch'
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
        self._renderers = {}
        self.setMinimumSize(220, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAccessibleName('手柄按键和摇杆实时示意图')
        self.setToolTip('图形来源：GamepadTester.cn；Switch 图为 Joy-Con 示意')

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

    def _renderer(self, name):
        if name not in self._renderers:
            raw = (ASSETS / SVG_FILES[name]).read_bytes()
            if name == 'playstation':
                tinted = tint_playstation(raw)
            elif name == 'xbox':
                tinted = tint_xbox(raw)
            else:
                tinted = tint_switch(raw)
            self._renderers[name] = QSvgRenderer(QByteArray(tinted))
        return self._renderers[name]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        kind = source_family(self.family)
        vx, vy, vw, vh = VIEWBOX[kind]
        margin = 10
        scale = min((self.width()-2*margin)/vw, (self.height()-2*margin)/vh)
        if scale <= 0:
            return
        target = QRectF(0, 0, vw*scale, vh*scale)
        target.moveCenter(QRectF(self.rect()).center())

        # Ambient backdrop glow
        halo = QPainterPath()
        halo.addRoundedRect(target.adjusted(-8, -8, 8, 8), 16, 16)
        painter.fillPath(halo, token_color('accent', 12))

        painter.translate(target.topLeft())
        painter.scale(scale, scale)
        self._renderer(kind).render(painter, QRectF(0, 0, vw, vh))
        painter.translate(-vx, -vy)
        if kind == 'xbox':
            self._xbox(painter)
        elif kind == 'playstation':
            self._playstation(painter)
        else:
            self._switch(painter)
        painter.end()

    def _glow(self, p, x, y, radius, color=TOKENS['accent']):
        c = QColor(color)
        p.setPen(QPen(c, max(1.4, radius*.16)))
        fill = QColor(c); fill.setAlpha(120)
        p.setBrush(fill)
        p.drawEllipse(QPointF(x, y), radius, radius)

    def _pill(self, p, x, y, width, height, color=TOKENS['accent']):
        c = QColor(color)
        c.setAlpha(175)
        p.setPen(Qt.NoPen); p.setBrush(c)
        p.drawRoundedRect(QRectF(x-width/2, y-height/2, width, height), height/3, height/3)

    def _label(self, p, text, x, y, size, color=TOKENS['ink_2'], bold=True):
        font = QFont('Arial')
        font.setPixelSize(size)
        font.setWeight(QFont.Bold if bold else QFont.Normal)
        p.setFont(font)
        p.setPen(QColor(color))
        p.drawText(QRectF(x-17, y-size*.9, 34, size*1.6), Qt.AlignCenter, text)

    def _stick(self, p, center, axis, button, history_index, radius):
        if button in self.pressed:
            self._glow(p, center.x(), center.y(), radius*.95)
        if self.trace and history_index < len(self.histories):
            points = list(self.histories[history_index].points)
            if len(points) > 1:
                trail = QPainterPath(center+QPointF(points[0][0]*radius*.34, points[0][1]*radius*.34))
                for x, y in points[1:]:
                    trail.lineTo(center+QPointF(x*radius*.34, y*radius*.34))
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(token_color('accent_lo', 135), max(1, radius*.055)))
                p.drawPath(trail)
        if self.sweep and history_index < len(self.histories):
            p.setPen(QPen(QColor(TOKENS['amber']), max(1, radius*.05)))
            for i, value in enumerate(self.histories[history_index].radii):
                if value is not None:
                    angle = (i+.5)*math.tau/36
                    p.drawPoint(center+QPointF(value*radius*1.3*math.cos(angle),
                                               value*radius*1.3*math.sin(angle)))
        x = self.axes[axis] if len(self.axes) > axis else 0
        y = self.axes[axis+1] if len(self.axes) > axis+1 else 0
        if abs(x) + abs(y) >= .015:
            dot = center + QPointF(max(-1, min(1, x))*radius*.35,
                                   max(-1, min(1, y))*radius*.35)
            p.setPen(QPen(QColor(TOKENS['ink']), max(1, radius*.08)))
            p.setBrush(QColor(TOKENS['accent_lo']))
            p.drawEllipse(dot, radius*.21, radius*.21)

    def _xbox(self, p):
        # These positions are taken directly from the site's 441 × 383 SVG.
        face = [(0, 330, 181, 'A', TOKENS['green']),
                (1, 348, 161, 'B', TOKENS['red']),
                (2, 310, 162, 'X', TOKENS['accent']),
                (3, 329, 140, 'Y', TOKENS['amber'])]
        for button, x, y, name, color in face:
            if button in self.pressed:
                self._glow(p, x, y, 12, color)
            self._label(p, name, x, y, 12, TOKENS['ink'] if button in self.pressed else TOKENS['ink_2'])
        for button, x, y, name in ((9,138.5,77,'LB'),(10,302.5,77,'RB')):
            if button in self.pressed:
                self._pill(p,x,y-1,42,15)
            self._label(p,name,x,y,10,TOKENS['ink'] if button in self.pressed else TOKENS['ink_2'])
        for button, x, y, w, h in ((11,166,221,14,20),(12,166,254,14,20),
                                   (13,149,238,20,14),(14,183,238,20,14)):
            if button in self.pressed:
                self._pill(p,x,y,w,h)
        for button,x,y,r in ((4,188,162,9),(5,220.5,125,15),
                             (6,253,162,9),(15,220.5,188,9)):
            if button in self.pressed:
                self._glow(p,x,y,r)
        for axis,x in ((4,138.5),(5,302.5)):
            value = self.axes[axis] if len(self.axes)>axis else 0
            if value>.01:
                self._pill(p,x,29,31,35*min(1,value))
        self._stick(p,QPointF(113,160),0,7,0,28)
        self._stick(p,QPointF(278,238),2,8,1,28)

    def _playstation(self,p):
        # DualSense source viewBox is 128 × 128. The positions below match its
        # FaceButtons, Dpad and Sticks groups rather than a generic controller.
        for button,x,y,color in ((0,99,56,TOKENS.get('blue', '#3b82f6')),(1,107,48,TOKENS['red']),
                                  (2,91,48,TOKENS['purple']),(3,99,40,TOKENS['green'])):
            if button in self.pressed:
                self._glow(p,x,y,2.5,color)
        for button,x,y in ((11,29,41),(12,29,57),(13,21,49),(14,37,49)):
            if button in self.pressed:
                self._glow(p,x,y,3.7)
        if 20 in self.pressed:
            self._pill(p,64,40,43,20)
        for button,x in ((9,30),(10,98)):
            if button in self.pressed:
                self._pill(p,x,30,20,2.5)
        for axis,x in ((4,30),(5,98)):
            value = self.axes[axis] if len(self.axes)>axis else 0
            if value>.01:
                self._pill(p,x,27,19*min(1,value),2.5)
        for button,x,y in ((4,57,53),(5,64,69),(6,71,53),(15,64,65)):
            if button in self.pressed:
                self._glow(p,x,y,1.5)
        self._stick(p,QPointF(45.5,64.46),0,7,0,5)
        self._stick(p,QPointF(82.5,64.46),2,8,1,5)

    def _switch(self,p):
        # The reference site's Nintendo diagram depicts paired Joy-Con. SDL
        # south/east/west/north map to the physical B/A/Y/X positions.
        for button,x,y in ((0,275,102),(1,292,85),(2,258,85),(3,275,68)):
            if button in self.pressed:
                self._glow(p,x,y,7)
        for button,x,y in ((11,95,138),(12,95,172),(13,78,155),(14,112,155)):
            if button in self.pressed:
                self._glow(p,x,y,7)
        for button,x,y in ((4,108,48),(6,254,48),(5,260,200),(15,112,200),
                           (9,96,22),(10,274,22)):
            if button in self.pressed:
                self._glow(p,x,y,5,TOKENS['ink'])
        for axis,x in ((4,96),(5,274)):
            value = self.axes[axis] if len(self.axes)>axis else 0
            if value>.01:
                self._pill(p,x,24,22*min(1,value),5,TOKENS['ink'])
        self._stick(p,QPointF(95,85),0,7,0,14)
        self._stick(p,QPointF(275,155),2,8,1,14)

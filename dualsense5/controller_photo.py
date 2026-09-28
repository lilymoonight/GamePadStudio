"""Controller product art rendered using GamepadTester.cn SVG vector graphics.

Faithfully reproduces GamepadTester.cn's tactile hardware cards with
warm stone-800 body tones, crisp outlines, glowing blue DualSense lightbar,
and active button highlights.
"""
from functools import lru_cache
import json
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal, QByteArray
from PySide6.QtGui import (QColor, QPainter, QPainterPath, QPixmap, QPen,
                           QRadialGradient)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget, QSizePolicy

from .glass import TOKENS


ASSET_DIR = Path(__file__).resolve().parent / 'assets' / 'controllers'
ASSETS = Path(__file__).resolve().parent / 'assets'
PHOTOS = json.loads((ASSET_DIR / 'sources.json').read_text(encoding='utf-8'))

SVG_FILES = {
    'xbox': 'reference_xbox.svg',
    'playstation': 'reference_playstation.svg',
    'switch': 'reference_switch.svg',
}

VIEWBOX = {
    'xbox': (0, 0, 441, 383),
    'playstation': (0, 19, 128, 84),
    'switch': (0, 0, 400, 250),
}


def _svg_family(family):
    if family in ('dualsense', 'dualshock4', 'generic'):
        return 'playstation'
    if family == 'switch':
        return 'switch'
    return 'xbox'


def tint_playstation(svg_bytes, led_color=None):
    if led_color is None:
        led_color = TOKENS['accent']
    s = svg_bytes.decode('utf-8')
    s = s.replace('viewBox="0 0 128 128"', 'viewBox="0 19 128 84"', 1)

    # 1. DualSense outer chassis: stone body #2d2926 with visible stone-500 (#78716c) stroke
    s = s.replace(
        'style="fill:rgb(245, 245, 244);stroke:none;stroke-width:1px;',
        'style="fill:#2d2926;stroke:#78716c;stroke-width:1.2px;'
    )
    # 2. Touchpad & center: deep stone #161412 with glowing LED border!
    s = s.replace(
        'style="fill:rgb(28, 25, 23);stroke:none;stroke-width:1px;',
        f'style="fill:#161412;stroke:{led_color};stroke-width:1.0px;'
    )
    # 3. Bumpers & D-pad & button bases: #3e3a37 with stone-500 stroke
    s = s.replace(
        'style="fill:rgb(214, 211, 209);stroke:none;',
        'style="fill:#3e3a37;stroke:#68625d;stroke-width:0.6px;'
    )
    # 4. Stick wells & sticks
    s = s.replace(
        'style="fill:rgb(68, 64, 60);stroke:rgb(120, 113, 108);stroke-width:1px;',
        'style="fill:#1c1917;stroke:#57534e;stroke-width:1px;'
    )
    # 5. Crisp white button symbols
    s = s.replace('style="fill:rgb(168, 162, 158);', 'style="fill:#f5f5f4;')
    s = s.replace('stroke:rgb(168, 162, 158);', 'stroke:#f5f5f4;')
    return s.encode('utf-8')


def tint_xbox(svg_bytes):
    s = svg_bytes.decode('utf-8')
    # 1. Outer body: dark stone-800 with clean stroke-stone-700
    s = s.replace(
        'fill:rgb(245, 245, 244);stroke:rgb(214, 211, 209);stroke-width:3px',
        'fill:#292524;stroke:#57534e;stroke-width:3px'
    )
    # 2. Bumpers & triggers
    s = s.replace(
        'fill:rgb(231, 229, 228);stroke:rgb(214, 211, 209);stroke-width:2px',
        'fill:#23201e;stroke:#44403c;stroke-width:2px'
    )
    # 3. Stick wells & dishes
    s = s.replace(
        'fill:rgb(250, 250, 249);stroke:rgb(231, 229, 228);stroke-width:2px',
        'fill:#1c1917;stroke:#44403c;stroke-width:2px'
    )
    # 4. Stick heads
    s = s.replace(
        'fill:rgb(214, 211, 209);stroke:rgb(255, 255, 255);stroke-width:2px',
        'fill:#383533;stroke:#57534e;stroke-width:2px'
    )
    # 5. Buttons & D-pad cross
    s = s.replace('fill:rgb(231, 229, 228);', 'fill:#383533;')
    s = s.replace('stroke:rgb(168, 162, 158);', 'stroke:#a8a29e;')
    return s.encode('utf-8')


def tint_switch(svg_bytes):
    s = svg_bytes.decode('utf-8')
    # Background behind buttons:
    s = s.replace('fill:rgb(41, 37, 36)', 'fill:#1c1917;stroke:#383533;stroke-width:1px')
    s = s.replace('fill:rgb(214, 211, 209)', 'fill:#292524;stroke:#44403c;stroke-width:1px')
    s = s.replace('fill:rgb(87, 83, 78)', 'fill:#292524;stroke:#44403c;stroke-width:1px')
    # Joy-Con bodies: iconic vibrant neon cyan & coral red
    s = s.replace('fill:rgb(0, 195, 227)', 'fill:#00C3E3;stroke:#009ab3;stroke-width:1px')
    s = s.replace('fill:rgb(255, 60, 40)', 'fill:#FF3C28;stroke:#c92a18;stroke-width:1px')
    return s.encode('utf-8')


_renderer_cache = {}


def _get_renderer(kind, led_color=None):
    cache_key = (kind, led_color)
    if cache_key in _renderer_cache:
        return _renderer_cache[cache_key]

    raw = (ASSETS / SVG_FILES[kind]).read_bytes()
    if kind == 'playstation':
        tinted = tint_playstation(raw, led_color)
    elif kind == 'xbox':
        tinted = tint_xbox(raw)
    else:
        tinted = tint_switch(raw)

    renderer = QSvgRenderer(QByteArray(tinted))
    _renderer_cache[cache_key] = renderer
    return renderer


TE_ASSET_NAMES = {
    'dualsense': 'te_dualsense.png',
    'dualshock4': 'te_dualshock4.png',
    'xbox': 'te_xbox.png',
    'switch': 'te_switch.png',
    'generic': 'te_generic.png',
}

_te_pixmap_cache = {}


def get_te_controller_pixmap(family):
    if family not in _te_pixmap_cache:
        fname = TE_ASSET_NAMES.get(family)
        if fname:
            path = ASSET_DIR / fname
            if path.exists():
                _te_pixmap_cache[family] = QPixmap(str(path))
            else:
                _te_pixmap_cache[family] = None
        else:
            _te_pixmap_cache[family] = None
    return _te_pixmap_cache[family]


def draw_controller_svg(painter: QPainter, rect: QRectF, family: str, led_color: str = None):
    """Render the Teenage Engineering hardware asset or fallback to vector graphic."""
    if led_color is None:
        led_color = TOKENS['accent']

    te_pixmap = get_te_controller_pixmap(family)
    if te_pixmap and not te_pixmap.isNull():
        # Render high-resolution transparent controller hardware
        margin = 4
        available_w = rect.width() - 2 * margin
        available_h = rect.height() - 2 * margin
        if available_w <= 0 or available_h <= 0:
            return
        scale = min(available_w / te_pixmap.width(), available_h / te_pixmap.height())
        target_w = te_pixmap.width() * scale
        target_h = te_pixmap.height() * scale
        target = QRectF(0, 0, target_w, target_h)
        target.moveCenter(rect.center())

        # Smooth render of isolated transparent controller hardware (pure matte, no halo)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawPixmap(target.toRect(), te_pixmap)
        painter.restore()
        return

    kind = _svg_family(family)
    vx, vy, vw, vh = VIEWBOX[kind]
    margin = 8
    available_w = rect.width() - 2 * margin
    available_h = rect.height() - 2 * margin
    if available_w <= 0 or available_h <= 0:
        return
    scale = min(available_w / vw, available_h / vh)
    target = QRectF(0, 0, vw * scale, vh * scale)
    target.moveCenter(rect.center())

    # Render tinted SVG (pure matte, no halo)
    painter.save()
    painter.translate(target.topLeft())
    painter.scale(scale, scale)
    _get_renderer(kind, led_color).render(painter, QRectF(0, 0, vw, vh))
    painter.restore()


def draw_controller_contour(painter, rect, family, led_color=None):
    draw_controller_svg(painter, rect, family, led_color)


@lru_cache(maxsize=5)
def product_pixmap(family):
    pix = QPixmap(800, 500)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    rect = QRectF(20, 20, 760, 460)
    draw_controller_svg(painter, rect, family, TOKENS['accent'])
    painter.end()
    return pix


def photo_health():
    return {family: True for family in PHOTOS}


class ControllerPhoto(QWidget):
    """GamepadTester Controller Art Widget."""

    def __init__(self, family='dualsense', parent=None):
        super().__init__(parent)
        self.family = None
        self.led = TOKENS['accent']
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.set_family(family)

    def sizeHint(self):
        return QSize(320, 160)

    def set_family(self, family):
        family = family if family in PHOTOS else 'generic'
        if family == self.family:
            return
        self.family = family
        self.info = PHOTOS[family]
        self.setAccessibleName(self.info['caption'])
        self.setToolTip(self.info['caption'] + '\n图形来源：GamepadTester.cn')
        self.update()

    def set_led(self, color_hex):
        self.led = color_hex
        self.update()

    def product_rect(self):
        area = QRectF(self.rect()).adjusted(8, 4, -8, -4)
        kind = _svg_family(self.family)
        _, _, vw, vh = VIEWBOX[kind]
        scale = min(area.width() / vw, area.height() / vh)
        target = QRectF(0, 0, vw * scale, vh * scale)
        target.moveCenter(area.center())
        return target

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        stage = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        draw_controller_svg(painter, stage, self.family, self.led)


class ControllerInput(ControllerPhoto):
    """Live interactive controller diagram with clickable button anchors and glow nodes."""
    button_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.buttons = set()
        self.axes = [0.] * 6
        self.available = None
        self.selected = None

    def select_button(self, key):
        self.selected = key
        self.update()

    def anchors(self):
        if self.family in ('dualsense', 'dualshock4', 'generic'):
            return {
                0: (.773, .440),  # Cross
                1: (.845, .340),  # Circle
                2: (.700, .340),  # Square
                3: (.773, .240),  # Triangle
                4: (.290, .180),  # Create/Share
                5: (.500, .580),  # PS button
                6: (.710, .180),  # Options
                7: (.355, .535),  # L3 (left stick)
                8: (.645, .535),  # R3 (right stick)
                9: (.250, .070),  # L1
                10: (.750, .070), # R1
                11: (.227, .240), # Dpad Up
                12: (.227, .440), # Dpad Down
                13: (.155, .340), # Dpad Left
                14: (.300, .340), # Dpad Right
                15: (.500, .645), # Mic button
                20: (.500, .260), # Touchpad
            }
        if self.family == 'switch':
            return {
                0: (.66, .34), 1: (.78, .27), 2: (.62, .23), 3: (.73, .16),
                4: (.32, .18), 5: (.70, .52), 6: (.58, .14),
                7: (.23, .31), 8: (.49, .48), 9: (.30, .06), 10: (.81, .08),
                11: (.19, .46), 12: (.19, .58), 13: (.13, .52), 14: (.25, .52),
                15: (.32, .36)
            }
        # Xbox
        return {
            0: (round((840 - 45) / 1060, 4), round((520 - 272) / 680, 4)),
            1: (round((902 - 45) / 1060, 4), round((454 - 272) / 680, 4)),
            2: (round((776 - 45) / 1060, 4), round((452 - 272) / 680, 4)),
            3: (round((842 - 45) / 1060, 4), round((394 - 272) / 680, 4)),
            4: (round((532 - 45) / 1060, 4), round((454 - 272) / 680, 4)),
            5: (.500, .180),
            6: (round((668 - 45) / 1060, 4), round((454 - 272) / 680, 4)),
            7: (round((362 - 45) / 1060, 4), round((450 - 272) / 680, 4)),
            8: (round((721 - 45) / 1060, 4), round((593 - 272) / 680, 4)),
            9: (.280, .080), 10: (.720, .080),
            11: (.406, .400), 12: (.406, .490), 13: (.360, .445), 14: (.452, .445),
            15: (round((600 - 45) / 1060, 4), round((506 - 272) / 680, 4))
        }

    def update_state(self, state):
        if state:
            self.set_family(state.get('family', 'generic'))
        self.available = set(state.get('available_buttons', [])) if state else set()
        buttons = set(state['buttons']) if state else set()
        axes = state['axes'] if state else [0.] * 6
        if buttons != self.buttons or axes != self.axes:
            self.buttons = buttons
            self.axes = axes[:]
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        rect = self.product_rect()
        points = {k: QPointF(rect.left() + x * rect.width(), rect.top() + y * rect.height())
                  for k, (x, y) in self.anchors().items() if self.available is None or k in self.available}
        if not points:
            return
        key = min(points, key=lambda k: (points[k] - event.position()).manhattanLength())
        if (points[key] - event.position()).manhattanLength() < max(20, rect.width() * .065):
            self.button_clicked.emit(key)

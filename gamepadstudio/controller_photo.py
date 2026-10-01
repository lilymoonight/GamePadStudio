"""Controller product art rendered using GamepadTester.cn SVG vector graphics.

Faithfully reproduces GamepadTester.cn's tactile hardware cards with
warm stone-800 body tones, crisp outlines, glowing blue DualSense lightbar,
and active button highlights.
"""
from functools import lru_cache
import json
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal, QByteArray
from PySide6.QtGui import (QColor, QPainter, QPainterPath, QPixmap, QPen, QFont,
                           QRadialGradient, QImage)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget, QSizePolicy

from .glass import TOKENS
from .controller_catalog import axis_labels


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
    if family in ('dualsense', 'dualshock4'):
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

# Button centers in the source PNG pixels, independent of crop and stage size.
RASTER_BUTTON_CENTERS = {
    'dualsense': {
        0: (859, 400), 1: (920, 341), 2: (799, 341), 3: (859, 282),
        4: (406, 253), 5: (600, 453), 6: (794, 253),
        7: (463, 454), 8: (736, 454), 9: (344, 209), 10: (855, 209),
        11: (340, 296), 12: (340, 386), 13: (294, 341), 14: (383, 341),
        15: (600, 505), 20: (600, 290),
    },
    'dualshock4': {
        0: (843, 440), 1: (900, 383), 2: (786, 382), 3: (844, 327),
        4: (441, 308), 5: (600, 495), 6: (758, 308),
        7: (475, 485), 8: (725, 485), 9: (351, 269), 10: (846, 269),
        11: (355, 344), 12: (355, 420), 13: (316, 383), 14: (395, 383),
        15: (600, 549), 20: (600, 350),
    },
    'xbox': {
        0: (836, 399), 1: (900, 341), 2: (774, 338), 3: (836, 278),
        4: (533, 336), 5: (600, 244), 6: (667, 336),
        7: (361, 334), 8: (719, 479), 9: (374, 183), 10: (826, 183),
        11: (480, 435), 12: (480, 525), 13: (432, 479), 14: (528, 479),
        15: (600, 370),
    },
    'switch': {
        0: (864, 412), 1: (935, 344), 2: (790, 344), 3: (864, 280),
        4: (484, 276), 5: (675, 347), 6: (727, 276),
        7: (335, 347), 8: (729, 477), 9: (353, 174), 10: (842, 174),
        11: (458, 438), 12: (458, 527), 13: (411, 482), 14: (504, 482),
        15: (537, 347),
    },
    'generic': {
        0: (844, 424), 1: (903, 367), 2: (788, 368), 3: (845, 311),
        4: (534, 368), 5: (600, 368), 6: (669, 368),
        7: (359, 370), 8: (715, 501), 9: (357, 188), 10: (849, 188),
        11: (486, 455), 12: (486, 541), 13: (442, 499), 14: (528, 499),
        15: (844, 503),
    },
}

_te_pixmap_cache = {}


def get_te_controller_pixmap(family):
    if family == 'generic':
        return None
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


@lru_cache(maxsize=5)
def controller_source_rect(family):
    """Cache visible hardware bounds, excluding the raster's transparent canvas."""
    pixmap = get_te_controller_pixmap(family)
    if pixmap is None or pixmap.isNull():
        return QRectF()
    image = pixmap.toImage().convertToFormat(QImage.Format_Alpha8)
    # Ignore near-transparent export noise while retaining antialiased edges.
    threshold = bytes(0 if value <= 3 else 255 for value in range(256))
    alpha = bytes(image.constBits()).translate(threshold)
    width, height, stride = image.width(), image.height(), image.bytesPerLine()
    left, top, right, bottom = width, height, -1, -1
    for y in range(height):
        row = alpha[y * stride:y * stride + width]
        first = row.find(b'\xff')
        if first >= 0:
            left, right = min(left, first), max(right, row.rfind(b'\xff'))
            top, bottom = min(top, y), y
    if right < left:
        return QRectF()
    bounds = QRectF(left, top, right - left + 1, bottom - top + 1)
    return bounds.adjusted(-2, -2, 2, 2).intersected(QRectF(pixmap.rect()))


def controller_art_rect(rect, family):
    """Use the same proportional stage fit for the image and interactive anchors."""
    source = controller_source_rect(family)
    if source.isEmpty():
        _, _, width, height = VIEWBOX[_svg_family(family)]
    else:
        width, height = source.width(), source.height()
    margin = max(8., min(24., min(rect.width(), rect.height()) * .035))
    area = rect.adjusted(margin, margin, -margin, -margin)
    if area.width() <= 0 or area.height() <= 0:
        return QRectF()
    scale = min(area.width() / width, area.height() / height)
    target = QRectF(0, 0, width * scale, height * scale)
    target.moveCenter(rect.center())
    return target


def draw_controller_svg(painter: QPainter, rect: QRectF, family: str, led_color: str = None):
    """Render the Teenage Engineering hardware asset or fallback to vector graphic."""
    if led_color is None:
        led_color = TOKENS['accent']

    target = controller_art_rect(rect, family)
    if target.isEmpty():
        return
    te_pixmap = get_te_controller_pixmap(family)
    source = controller_source_rect(family)
    if te_pixmap is not None and not te_pixmap.isNull() and not source.isEmpty():

        # Smooth render of isolated transparent controller hardware (pure matte, no halo)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawPixmap(target, te_pixmap, source)
        painter.restore()
        return

    kind = _svg_family(family)

    # Render tinted SVG (pure matte, no halo)
    painter.save()
    _get_renderer(kind, led_color).render(painter, target)
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

    def __init__(self, family='generic', parent=None):
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
        self.info = dict(PHOTOS[family])
        if family == 'generic':
            self.info['caption'] = '通用 XInput 键位示意'
        self.setAccessibleName(self.info['caption'])
        self.setToolTip(self.info['caption'] + '\n图形来源：GamepadTester.cn')
        self.update()

    def set_led(self, color_hex):
        self.led = color_hex
        self.update()

    def product_rect(self):
        stage = self.product_stage()
        return controller_art_rect(stage, self.family)

    def product_stage(self):
        # Front views hide the triggers behind the shoulder buttons. One compact
        # label row keeps those inputs visible without covering the hardware.
        label_height = max(18., min(28., self.height() * .11))
        return QRectF(self.rect()).adjusted(1, label_height + 1, -1, -1)

    def trigger_anchors(self):
        rect = self.product_rect()
        if rect.isEmpty():
            return {}
        source = controller_source_rect(self.family)
        if not source.isEmpty() and self.family in RASTER_BUTTON_CENTERS:
            return {trigger: ((RASTER_BUTTON_CENTERS[self.family][button][0] - source.left()) / source.width(),
                              -12. / rect.height())
                    for trigger, button in (('LT', 9), ('RT', 10))}
        if _svg_family(self.family) == 'xbox':
            return {'LT': (138.5 / 441, 29 / 383), 'RT': (302.5 / 441, 29 / 383)}
        positions = (30 / 128, 98 / 128) if _svg_family(self.family) == 'playstation' else (96 / 400, 274 / 400)
        return {key: (x, -12. / rect.height()) for key, x in zip(('LT', 'RT'), positions)}

    def input_is_available(self, key):
        available = getattr(self, 'available', None)
        if key in ('LT', 'RT'):
            # New consumers pass every canonical source. Legacy consumers keep
            # integer buttons and report axes independently.
            if available is not None and (not available or any(isinstance(item, str) for item in available)):
                return key in available
            axes = getattr(self, 'available_axes', None)
            return axes is None or (4 if key == 'LT' else 5) in axes
        return available is None or key in available or str(key) in available

    def trigger_regions(self):
        rect = self.product_rect()
        return {key: QRectF(rect.left() + x * rect.width() - 23,
                           rect.top() + y * rect.height() - 11, 46, 22)
                for key, (x, y) in self.trigger_anchors().items()}

    def draw_trigger_labels(self, painter):
        labels = axis_labels(self.family)[-2:]
        font = QFont('Segoe UI')
        font.setPixelSize(12)
        font.setWeight(QFont.DemiBold)
        painter.setFont(font)
        selected = getattr(self, 'selected_buttons', set())
        axes = getattr(self, 'axes', [])
        for index, (key, region) in enumerate(self.trigger_regions().items()):
            if not self.input_is_available(key):
                continue
            amount = max(0., min(1., axes[index + 4])) if len(axes) > index + 4 else 0.
            active = key in selected or amount > .01
            painter.setPen(QColor(TOKENS['accent'] if active else TOKENS['ink_2']))
            painter.drawText(region, Qt.AlignCenter, labels[index])
            if active:
                width = 23 if key in selected else 23 * amount
                painter.setPen(QPen(QColor(TOKENS['accent']), 2.))
                painter.drawLine(QPointF(region.center().x() - width / 2, region.bottom()),
                                 QPointF(region.center().x() + width / 2, region.bottom()))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        stage = self.product_stage()
        draw_controller_svg(painter, stage, self.family, self.led)
        self.draw_trigger_labels(painter)


class ControllerInput(ControllerPhoto):
    """Live interactive controller diagram with clickable button anchors and glow nodes."""
    button_clicked = Signal(int)
    input_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.buttons = set()
        self.axes = [0.] * 6
        self.available = None
        self.available_axes = None
        self.selected = None
        self.selected_buttons = set()

    def select_button(self, key):
        self.select_buttons([] if key is None else [key])

    def select_buttons(self, keys):
        normalized = {int(key) if str(key).isdigit() else str(key) for key in keys}
        selected_buttons = {key for key in normalized if key in self.anchors()
                            and self.input_is_available(key)}
        if selected_buttons != self.selected_buttons:
            self.selected_buttons = selected_buttons
            self.selected = min(selected_buttons, key=lambda key: (1, key) if isinstance(key, str) else (0, key)) if selected_buttons else None
            self.update()

    def anchors(self):
        return {**self.button_anchors(), **self.trigger_anchors()}

    def button_anchors(self):
        if self.family == 'generic':
            centers = {0: (330,181), 1: (348,161), 2: (310,162), 3: (329,140),
                       4: (188,162), 5: (220.5,125), 6: (253,162),
                       7: (113,160), 8: (278,238), 9: (138.5,77), 10: (302.5,77),
                       11: (166,221), 12: (166,254), 13: (149,238), 14: (183,238)}
            return {key: (x / 441, y / 383) for key, (x, y) in centers.items()}
        source = controller_source_rect(self.family)
        if not source.isEmpty() and self.family in RASTER_BUTTON_CENTERS:
            return {key: ((x - source.left()) / source.width(), (y - source.top()) / source.height())
                    for key, (x, y) in RASTER_BUTTON_CENTERS[self.family].items()}
        if self.family in ('dualsense', 'dualshock4'):
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
        previous_available = self.available
        previous_axes = self.available_axes
        if state:
            self.set_family(state.get('family', 'generic'))
        if state:
            self.available = set(state.get('available_buttons', []))
            available_axes = state.get('available_axes')
            self.available_axes = set(available_axes) if available_axes is not None else set(range(len(state.get('axes', []))))
        buttons = set(state['buttons']) if state else set()
        axes = state['axes'] if state else [0.] * 6
        if (buttons != self.buttons or axes != self.axes
                or previous_available != self.available or previous_axes != self.available_axes):
            self.buttons = buttons
            self.axes = axes[:]
            self.update()
        if state:
            self.select_buttons(self.selected_buttons)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.selected_buttons:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.product_rect()
        radius = max(9., min(20., rect.width() * .026))
        stroke = QColor(TOKENS['accent'])
        fill = QColor(stroke)
        fill.setAlpha(38)
        painter.setPen(QPen(stroke, 2.))
        painter.setBrush(fill)
        for key, (x, y) in self.anchors().items():
            if isinstance(key, int) and key in self.selected_buttons and self.input_is_available(key):
                painter.drawEllipse(QPointF(rect.left() + x * rect.width(), rect.top() + y * rect.height()), radius, radius)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        for key, region in self.trigger_regions().items():
            if region.contains(event.position()):
                if self.input_is_available(key):
                    self.input_clicked.emit(key)
                return
        rect = self.product_rect()
        points = {k: QPointF(rect.left() + x * rect.width(), rect.top() + y * rect.height())
                  for k, (x, y) in self.button_anchors().items()}
        if not points:
            return
        def distance_squared(point):
            delta = point - event.position()
            return delta.x() ** 2 + delta.y() ** 2
        key = min(points, key=lambda k: distance_squared(points[k]))
        if self.input_is_available(key) and distance_squared(points[key]) < max(20, rect.width() * .075) ** 2:
            self.button_clicked.emit(key)
            self.input_clicked.emit(str(key))

"""Compact, borderless controller notation shared by mapping views.

Names follow the manufacturers' hardware diagrams. Geometric button marks are
drawn locally, so small labels do not depend on emoji or private font glyphs.
The input ID and family, rather than a translated label, select each mark.
"""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QLabel, QSizePolicy

from .controller_catalog import button_labels
from .i18n import get_language
from .mapping_engine import canonical_trigger, trigger_label

PLAYSTATION = ('dualsense', 'dualshock4')
MODIFIERS = {'4', '9', '10', 'LT', 'RT'}


def display_parts(trigger):
    parts = canonical_trigger(trigger).split('+')
    return tuple([p for p in parts if p in MODIFIERS] + [p for p in parts if p not in MODIFIERS])


def button_text(key, family='generic'):
    key = str(key)
    if key.startswith('TP:'):
        names = ({'tap': 'Tap', 'double_tap': '2×', 'hold': 'Hold', 'two_tap': '2-finger'}
                 if get_language() == 'en' else
                 {'tap': '轻触', 'double_tap': '双击', 'hold': '按住', 'two_tap': '双指'})
        return 'TP' + {**names,
                        'swipe_up': '↑', 'swipe_down': '↓', 'swipe_left': '←',
                        'swipe_right': '→', 'scroll_up': '2↑',
                        'scroll_down': '2↓'}.get(key[3:], '')
    if key in ('LT', 'RT'):
        return (('L2', 'R2') if family in PLAYSTATION else
                ('ZL', 'ZR') if family == 'switch' else ('LT', 'RT'))[key == 'RT']
    if key.startswith(('LS:', 'RS:')):
        stick, direction = key.split(':', 1)
        arrows = {'up': '↑', 'down': '↓', 'left': '←', 'right': '→'}
        if direction in arrows:
            return stick + arrows[direction]
        if direction in ('inner', 'outer'):
            suffix = ('轻推' if direction == 'inner' else '推满') if get_language() != 'en' else ('Soft' if direction == 'inner' else 'Full')
            return stick + ' ' + suffix
    if key in ('11', '12', '13', '14'):
        return {'11': '↑', '12': '↓', '13': '←', '14': '→'}[key]
    if key in ('7', '8') and family not in PLAYSTATION:
        return 'LS' if key == '7' else 'RS'
    if key.isdigit():
        return button_labels(family, lang='en').get(int(key), key).split('  ')[0]
    return key


def _mark(key, family):
    key = str(key)
    if key.startswith('TP:'):
        return 'touch_gesture'
    if family in PLAYSTATION and key in ('0', '1', '2', '3'):
        return ('cross', 'circle', 'square', 'triangle')[int(key)]
    if family == 'dualsense' and key == '4':
        return 'create'
    if (family == 'dualsense' and key == '6') or (family == 'xbox' and key == '6'):
        return 'menu'
    if family == 'xbox' and key == '4':
        return 'view'
    if family == 'xbox' and key == '15':
        return 'share'
    if family == 'switch' and key == '15':
        return 'capture'
    if family == 'switch' and key == '5':
        return 'home'
    if family in PLAYSTATION and key == '20':
        return 'touchpad'
    if family == 'dualsense' and key == '15':
        return 'mute'
    if key.startswith(('LS:', 'RS:')):
        return 'stick'
    return None


def token_advance(key, family, font):
    mark = _mark(key, family)
    size = max(8, font.pixelSize())
    if mark in ('stick', 'touch_gesture'):
        suffix = button_text(key, family)[2:]
        return size + math.ceil(QFontMetricsF(font).horizontalAdvance(suffix)) + 3
    if mark:
        return size + 2
    return max(5, math.ceil(QFontMetricsF(font).horizontalAdvance(button_text(key, family))) + 2)


def draw_token(painter, rect, key, family, font, color):
    """Draw a button mark or its printed name, without a decorative container."""
    rect = QRectF(rect)
    color = QColor(color)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)
    painter.setFont(font)
    painter.setPen(color)
    mark = _mark(key, family)
    if not mark:
        # This rectangle was already measured by token_advance. Eliding at the
        # rounded integer advance can replace even "L1" / "R2" with an ellipsis.
        painter.drawText(rect, Qt.AlignCenter, button_text(key, family))
        painter.restore()
        return
    size = min(max(8, font.pixelSize()), rect.height() - 2, rect.width() - 1)
    x = rect.x() + (rect.width() - token_advance(key, family, font)) / 2 if mark in ('stick', 'touch_gesture') else rect.center().x() - size / 2
    box = QRectF(x, rect.center().y() - size / 2, size, size)
    painter.setPen(QPen(color, max(1.0, size / 10), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)
    if mark in ('stick', 'touch_gesture'):
        if mark == 'stick':
            painter.drawEllipse(box.adjusted(1, 1, -1, -1))
            small = QFont(font)
            small.setPixelSize(max(6, int(size * .68)))
            painter.setFont(small)
            painter.drawText(box, Qt.AlignCenter, 'R' if str(key).startswith('RS') or str(key) == '8' else 'L')
            painter.setFont(font)
        else:
            pad = box.adjusted(0, size * .18, 0, -size * .18)
            painter.drawRoundedRect(pad, size * .12, size * .12)
            for fraction in (.3, .5, .7):
                painter.drawPoint(QPointF(pad.left() + pad.width() * fraction, pad.center().y()))
        suffix = button_text(key, family)[2:]
        painter.drawText(QRectF(box.right() + 2, rect.y(), max(0, rect.right() - box.right() - 2), rect.height()), Qt.AlignCenter, suffix)
    else:
        painter.translate(box.topLeft())
        painter.scale(size / 16, size / 16)
        painter.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if mark == 'cross':
            painter.drawLine(QPointF(3, 3), QPointF(13, 13))
            painter.drawLine(QPointF(13, 3), QPointF(3, 13))
        elif mark == 'circle':
            painter.drawEllipse(QRectF(2, 2, 12, 12))
        elif mark == 'square':
            painter.drawRect(QRectF(2.5, 2.5, 11, 11))
        elif mark == 'triangle':
            painter.drawPolygon(QPolygonF([QPointF(8, 2), QPointF(14, 13), QPointF(2, 13)]))
        elif mark == 'create':
            painter.drawLine(QPointF(8, 2), QPointF(8, 10))
            painter.drawLine(QPointF(2, 4), QPointF(5, 10))
            painter.drawLine(QPointF(14, 4), QPointF(11, 10))
        elif mark == 'menu':
            for y in (4, 8, 12):
                painter.drawLine(QPointF(2, y), QPointF(14, y))
        elif mark == 'view':
            painter.drawRect(QRectF(2, 2, 8, 8))
            painter.drawRect(QRectF(6, 6, 8, 8))
        elif mark == 'share':
            painter.drawLine(QPointF(8, 1), QPointF(8, 9))
            painter.drawPolyline(QPolygonF([QPointF(5, 4), QPointF(8, 1), QPointF(11, 4)]))
            painter.drawPolyline(QPolygonF([QPointF(2, 8), QPointF(2, 14), QPointF(14, 14), QPointF(14, 8)]))
        elif mark == 'capture':
            painter.drawRect(QRectF(2, 2, 12, 12))
            painter.drawEllipse(QRectF(5, 5, 6, 6))
        elif mark == 'home':
            painter.drawPolyline(QPolygonF([QPointF(2, 7), QPointF(8, 2), QPointF(14, 7)]))
            painter.drawPolyline(QPolygonF([QPointF(4, 6), QPointF(4, 14), QPointF(12, 14), QPointF(12, 6)]))
        elif mark == 'touchpad':
            painter.drawRoundedRect(QRectF(1, 3, 14, 10), 2, 2)
            painter.setPen(QPen(color, 1))
            for x in (5, 8, 11):
                painter.drawPoint(QPointF(x, 7))
                painter.drawPoint(QPointF(x, 10))
        elif mark == 'mute':
            painter.drawRoundedRect(QRectF(6, 2, 4, 7), 2, 2)
            painter.drawArc(QRectF(3, 4, 10, 8), 180 * 16, 180 * 16)
            painter.drawLine(QPointF(8, 12), QPointF(8, 15))
            painter.drawLine(QPointF(2, 2), QPointF(14, 14))
    painter.restore()


def make_token_label(key, family, parent=None, color='#cbd5e1', size=13):
    label = QLabel(parent)
    label.setAttribute(Qt.WA_TransparentForMouseEvents)
    label.setProperty('keyToken', str(key))
    label.setAlignment(Qt.AlignCenter)
    name = trigger_label(str(key), family)
    label.setToolTip(name)
    label.setAccessibleName(name)
    label.setStyleSheet(f'background: transparent; border: none; padding: 0; color: {color}; font-weight: 600;')
    label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    font = QFont('Segoe UI', -1)
    font.setFamilies(['Segoe UI', 'Microsoft YaHei', 'sans-serif'])
    font.setPixelSize(size)
    font.setBold(True)
    label.setFont(font)
    if _mark(str(key), family):
        width, height = token_advance(str(key), family, font) + 2, size + 6
        # Keep marks crisp on high-DPI displays as well as at narrow widths.
        ratio = max(1.0, label.devicePixelRatioF())
        pixmap = QPixmap(round(width * ratio), round(height * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        draw_token(painter, QRectF(0, 0, width, height), str(key), family, font, color)
        painter.end()
        label.setPixmap(pixmap)
    else:
        label.setText(button_text(str(key), family))
    return label

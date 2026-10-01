"""
Virtual KBM Studio UI Component (标准布局虚拟键鼠与外设捕获工作台)
1. 完整展示标准 ANSI 布局的高精细度虚拟键盘与虚拟鼠标面板
2. 键帽上直接清晰展示绑定的手柄按键，明确区分【短按 (短)】与【长按 (长)】
3. 键盘全画幅自适应缩放（46px ~ 76px 基准），铺满整个视口，不浪费页面空间
4. 目标驱动式捕获流：“点击虚拟键鼠按键 -> 触发捕获监听 -> 按下任意手柄/外设 -> 即刻绑定”
5. 悬停卡片详情与右键快速解绑，集成预设方案与设备隐身
"""

import copy
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Set, Union
from PySide6.QtCore import Qt, QSize, Signal, QUrl, QRect, QEvent, QTimer
from PySide6.QtGui import (
    QColor, QFont, QDesktopServices, QPainter, QPen, QBrush, QFontMetrics
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QComboBox, QLineEdit, QScrollArea,
    QFrame, QSlider, QInputDialog, QMessageBox, QSizePolicy, QCheckBox, QMenu, QLayout
)

from .glass import (
    TOKENS, GlassPanel, IconButton, Toggle,
    SquircleBadge, glyph
)
from .hidhide import HidHideClient, HIDHIDE_RELEASE_URL
from .i18n import tr, tr_profile


def label(text, kind=None, wrap=False):
    w = QLabel(text)
    if kind: w.setObjectName(kind)
    w.setWordWrap(wrap)
    return w


def button(text, callback, primary=False, icon=None, pill=False, danger=False):
    w = QPushButton(text); w.setCursor(Qt.PointingHandCursor)
    if primary: w.setObjectName('primary')
    elif danger: w.setObjectName('danger')
    elif pill: w.setObjectName('pill')
    if icon:
        if isinstance(icon, str):
            icon_color = '#ffffff' if primary else (TOKENS['red'] if danger else TOKENS['accent'])
            w.setIcon(glyph(icon, icon_color))
        else:
            w.setIcon(icon)
        w.setIconSize(QSize(15, 15))
    w.clicked.connect(callback)
    return w


def card(kind='card'):
    frame = GlassPanel(kind=kind)
    layout = QVBoxLayout(frame); layout.setContentsMargins(18, 16, 18, 16); layout.setSpacing(10)
    return frame, layout


def compact_trigger(raw_label: str) -> str:
    """精简手柄按键名称，便于在紧凑键帽徽章中清晰易读展示"""
    if not raw_label:
        return ""
    s = str(raw_label).strip()
    replacements = [
        ('  交叉', ''), ('  Cross', ''),
        ('  圆圈', ''), ('  Circle', ''),
        ('  方块', ''), ('  Square', ''),
        ('  三角', ''), ('  Triangle', ''),
        ('方向键 ', ''), ('D-Pad ', ''),
        ('左摇杆按下', 'L3'), ('右摇杆按下', 'R3'),
        ('Left Stick Click', 'L3'), ('Right Stick Click', 'R3'),
        ('左摇杆', 'LS:'), ('右摇杆', 'RS:'),
        ('按住', ''), ('（按住）', ''),
    ]
    for a, b in replacements:
        s = s.replace(a, b)
    parts = [p.strip() for p in s.split('+')]
    return "+".join(parts)


def trigger_tokens(raw_label: str) -> Tuple[str, ...]:
    """Display controller modifiers first without changing a chord's identity."""
    aliases = {'Options': 'OPT', 'Create': 'SHARE', '触摸板': 'TP', 'Touchpad': 'TP',
               '麦克风': 'MIC', 'Mic': 'MIC', 'LS:推满': 'LS MAX',
               'LS:↑': 'LS↑', 'LS:↓': 'LS↓', 'LS:←': 'LS←', 'LS:→': 'LS→',
               'RS:↑': 'RS↑', 'RS:↓': 'RS↓', 'RS:←': 'RS←', 'RS:→': 'RS→'}
    parts = [aliases.get(part.strip(), part.strip()) for part in compact_trigger(raw_label).split('+') if part.strip()]
    modifiers = {'L1', 'R1', 'L2', 'R2', 'LB', 'RB', 'LT', 'RT', 'Ctrl', 'Shift', 'Alt', 'Win'}
    return tuple([part for part in parts if part in modifiers] + [part for part in parts if part not in modifiers])


class KeyCap(QPushButton):
    """
    Apple 深色玻璃质感高级虚拟键帽：
    - 直接在键帽上展示绑定的手柄按键
    - 显著区分【短按 (短)】与【长按 (长)】文字标识及色彩光环
    - 支持自适应动态放大铺满屏幕
    - 鼠标悬停发光与详细卡片说明、左键编辑、右键快捷解绑
    """
    left_clicked = Signal(str, str)   # (key_id, display_name)
    right_clicked = Signal(str, str)  # (key_id, display_name)

    def __init__(self, key_id: str, display_name: str, width: Any = 1.0, height: Any = 1.0, is_special: bool = False, parent=None):
        super().__init__(parent)
        self.key_id = key_id
        self.display_name = display_name
        self.is_special = is_special

        # 兼容相对标准单位宽度 (1U = 1.0) 与历史像素宽度
        if isinstance(width, (int, float)) and width > 10:
            self.u_width = float(width) / 33.0
        else:
            self.u_width = float(width)

        if isinstance(height, (int, float)) and height > 10:
            self.u_height = float(height) / 34.0
        else:
            self.u_height = float(height)

        self.short_bindings: List[str] = []
        self.long_bindings: List[str] = []
        self.badges: List[str] = []
        self.is_capturing = False
        self.is_pressed = False
        self.is_hovered = False

        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(tr(display_name))
        self.setAttribute(Qt.WA_Hover, True)

        self.set_unit_size(52, 50, 4)
        self.update_tooltip()

    def set_unit_size(self, u_px: int, h_px: int, spacing: int = 4):
        """按全局自适应单位尺寸刷新实际像素大小"""
        w = max(24, int(round(self.u_width * u_px + (self.u_width - 1.0) * spacing)))
        h = max(24, int(round(self.u_height * h_px + (self.u_height - 1.0) * spacing)))
        self.setFixedSize(w, h)
        self.update()

    def enterEvent(self, event):
        self.is_hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.is_hovered = False
        self.update()
        super().leaveEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self.right_clicked.emit(self.key_id, self.display_name)
        elif event.button() == Qt.LeftButton:
            self.left_clicked.emit(self.key_id, self.display_name)
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.left_clicked.emit(self.key_id, self.display_name)
            event.accept()
            return
        super().keyPressEvent(event)

    def set_mapping_info(self, info: List[Dict[str, Any]], capturing: bool = False):
        """传入结构化映射信息，严格区分短按与长按"""
        self.short_bindings = []
        self.long_bindings = []
        self.badges = []
        self.is_capturing = capturing
        for item in info:
            trigger = item.get('trigger', '')
            gesture = item.get('gesture', 'short')
            if gesture == 'long':
                if trigger not in self.long_bindings:
                    self.long_bindings.append(trigger)
                self.badges.append(f"{trigger} 长按")
            else:
                if trigger not in self.short_bindings:
                    self.short_bindings.append(trigger)
                self.badges.append(f"{trigger} 短按")
        self.update_tooltip()
        self.update()

    def set_mapping_state(self, badges: List[Union[str, Dict[str, Any]]], capturing: bool = False):
        """兼容通用徽章输入"""
        self.short_bindings = []
        self.long_bindings = []
        self.badges = []
        self.is_capturing = capturing
        for item in badges:
            if isinstance(item, dict):
                trigger = item.get('trigger', '')
                gesture = item.get('gesture', 'short')
                if gesture == 'long':
                    if trigger not in self.long_bindings: self.long_bindings.append(trigger)
                    self.badges.append(f"{trigger} 长按")
                else:
                    if trigger not in self.short_bindings: self.short_bindings.append(trigger)
                    self.badges.append(f"{trigger} 短按")
            elif isinstance(item, str):
                self.badges.append(item)
                if '长按' in item or '长' in item:
                    t = item.replace(' 长按', '').replace('长按', '').replace('长', '').strip()
                    if t and t not in self.long_bindings: self.long_bindings.append(t)
                else:
                    t = item.replace(' 短按', '').replace('短按', '').replace('短', '').strip()
                    if t and t not in self.short_bindings: self.short_bindings.append(t)
        self.update_tooltip()
        self.update()

    def update_tooltip(self):
        desc = tr(KEY_DESCRIPTIONS.get(self.key_id, self.display_name))
        if self.is_pressed:
            self.setToolTip(f"【{desc}】正在触发物理输出...")
            return
        if self.is_capturing:
            self.setToolTip(f"【{desc}】正在等待外设输入...\n请在手柄、飞行摇杆或任意接入设备上按下按键或推轴")
            return
        lines = [f"虚拟按键：【{desc}】({self.key_id})"]
        if self.short_bindings or self.long_bindings:
            lines.append(f"已绑定手柄触发（{len(self.short_bindings) + len(self.long_bindings)} 项绑定）：")
            if self.short_bindings:
                lines.append(f"  • 短按 (Short Press)：{', '.join(self.short_bindings)}")
            if self.long_bindings:
                lines.append(f"  • 长按 (Long Press)：{', '.join(self.long_bindings)}")
            lines.append("\n💡 点击配置/修改 · 右键快速清除绑定")
        else:
            lines.append("未绑定外设触发\n\n💡 点击开始捕获手柄/外设按键")
        self.setToolTip("\n".join(lines))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        rect = self.rect()
        w, h = rect.width(), rect.height()
        radius = 6.0

        has_short = bool(self.short_bindings)
        has_long = bool(self.long_bindings)
        has_badges = has_short or has_long

        # 1. 背景与发光边框状态判决
        if self.is_pressed:
            # 物理按键触发输出高亮
            bg_brush = QBrush(QColor(255, 159, 10))
            border_pen = QPen(QColor(255, 200, 100), 1.5)
            text_color = QColor(18, 18, 20)
        elif self.is_capturing:
            # 捕获手柄输入脉冲
            bg_brush = QBrush(QColor(245, 158, 11, 40))
            border_pen = QPen(QColor(245, 158, 11), 1.8, Qt.DashLine)
            text_color = QColor(245, 158, 11)
        elif has_badges:
            # 已绑定：根据短按/长按提供高级辨识度光环
            if has_short and has_long:
                # 兼具短按与长按：深紫罗兰光环
                bg_color = QColor(36, 20, 56, 210 if not self.is_hovered else 240)
                border_color = QColor(168, 85, 247, 190 if not self.is_hovered else 255)
            elif has_long:
                # 仅长按：暖琥珀金光环
                bg_color = QColor(44, 26, 12, 210 if not self.is_hovered else 240)
                border_color = QColor(245, 158, 11, 190 if not self.is_hovered else 255)
            else:
                # 仅短按：电光青蓝光环
                bg_color = QColor(14, 32, 52, 210 if not self.is_hovered else 240)
                border_color = QColor(56, 189, 248, 190 if not self.is_hovered else 255)
            bg_brush = QBrush(bg_color)
            border_pen = QPen(border_color, 1.5 if not self.is_hovered else 2.0)
            text_color = QColor(255, 255, 255)
        else:
            # 未绑定默认深色质感
            bg_color = QColor(TOKENS['base'] if self.is_special else TOKENS['elevated'])
            if self.is_hovered:
                bg_color = QColor(TOKENS['overlay'])
            border_color = QColor(TOKENS['border_hi'] if self.is_hovered else TOKENS['border'])
            bg_brush = QBrush(bg_color)
            border_pen = QPen(border_color, 1.0)
            text_color = QColor(TOKENS['ink'] if self.is_hovered else TOKENS['ink_2'])

        if self.hasFocus():
            border_pen = QPen(QColor(TOKENS['accent']), 2.0)

        painter.setBrush(bg_brush)
        painter.setPen(border_pen)
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius, radius)

        # 2. 键名与手柄按键文字绘制
        main_font = QFont("Microsoft YaHei", -1)
        main_font.setFamilies(["Microsoft YaHei", "Segoe UI", "PingFang SC", "sans-serif"])
        if len(self.display_name) <= 2:
            main_font.setPixelSize(max(11, min(15, int(h * 0.30))))
        elif len(self.display_name) <= 5:
            main_font.setPixelSize(max(10, min(13, int(h * 0.25))))
        else:
            main_font.setPixelSize(max(9, min(11, int(h * 0.22))))
        main_font.setBold(True)
        stacked_chord = w < 48 and any(len(trigger_tokens(trigger)) > 1
                                     for trigger in self.short_bindings + self.long_bindings)
        if stacked_chord and h < 40:
            main_font.setPixelSize(min(9, main_font.pixelSize()))
        while main_font.pixelSize() > 7 and QFontMetrics(main_font).horizontalAdvance(self.display_name) > w - 6:
            main_font.setPixelSize(main_font.pixelSize() - 1)
        painter.setFont(main_font)
        painter.setPen(text_color)

        if not (self.is_capturing or has_badges):
            painter.drawText(rect, Qt.AlignCenter, self.display_name)
        else:
            # 键名和徽章组成紧凑内容组；双行高键帽也保持在正中。
            badge_h = max(18, min(24, int(h * 0.40)))
            if stacked_chord:
                badge_h = 22
            gap = 1 if stacked_chord and h < 40 else max(2, min(4, int(h * 0.05)))
            title_min = 8 if stacked_chord and h < 40 else 12
            title_h = min(QFontMetrics(main_font).height(), max(title_min, h - badge_h - gap - 4))
            content_h = title_h + gap + badge_h
            content_y = (h - content_h) // 2
            top_rect = QRect(3, content_y, w - 6, title_h)
            painter.drawText(top_rect, Qt.AlignCenter, self.display_name)

            # 底部空间绘制绑定的手柄按键与长按/短按标识
            badge_rect = QRect(3, content_y + title_h + gap, w - 6, badge_h)
            if self.is_capturing:
                cap_font = QFont("Microsoft YaHei", max(8, min(10, int(h * 0.20))))
                cap_font.setFamilies(["Microsoft YaHei", "Segoe UI", "PingFang SC", "sans-serif"])
                cap_font.setBold(True)
                painter.setFont(cap_font)
                painter.setPen(QColor(245, 158, 11))
                painter.drawText(badge_rect, Qt.AlignCenter, tr("⏳ 捕获中"))
            else:
                self._draw_badges(painter, badge_rect)

    def _draw_badges(self, painter, rect):
        plan = self.binding_display_plan(rect.width(), rect.height())
        if not plan:
            return
        total_w = sum(item['width'] for item in plan) + 8 * (len(plan) - 1)
        x = rect.x() + (rect.width() - total_w) // 2
        painter.save()
        painter.setClipRect(rect)
        for index, item in enumerate(plan):
            area = QRect(x, rect.y(), item['width'], rect.height())
            if item['kind'] == 'count':
                self._draw_binding_count(painter, area, item['count'], item['overflow'])
            else:
                self._draw_binding_chips(painter, area, item)
            x += item['width'] + 8
            if index < len(plan) - 1:
                painter.setPen(QPen(QColor(TOKENS['border_hi']), 1))
                painter.drawLine(x - 4, rect.center().y() - 4, x - 4, rect.center().y() + 4)
        painter.restore()

    @staticmethod
    def _chip_font(size):
        font = QFont('Segoe UI', -1)
        font.setFamilies(['Segoe UI', 'Microsoft YaHei', 'sans-serif'])
        font.setPixelSize(size)
        font.setBold(True)
        return font

    def binding_display_plan(self, width, height):
        """Keep chord members separate from the count of independent bindings."""
        items = [{'tokens': trigger_tokens(trigger), 'long': is_long}
                 for bindings, is_long in ((self.short_bindings, False), (self.long_bindings, True))
                 for trigger in bindings]
        if not items:
            return []
        minimum = 7 if width < 48 else 8
        candidates = []
        for size in range(12, minimum - 1, -1):
            fm = QFontMetrics(self._chip_font(size))
            measured = []
            for item in items:
                token_widths = [max(11, fm.horizontalAdvance(token) + 6) for token in item['tokens']]
                measured.append(dict(item, kind='binding', font=size, stacked=False,
                                     width=sum(token_widths) + 6 * (len(token_widths) - 1) + 6))
            candidates.append(measured)
            if sum(item['width'] for item in measured) + 8 * (len(measured) - 1) <= width:
                return measured
        if len(items) == 1:
            return [dict(items[0], kind='binding', font=7, stacked=len(items[0]['tokens']) > 1, width=width)]
        # A stack symbol labels binding counts; an ellipsis inside a chord only
        # folds its members. Neither is rendered as another chord separator.
        count_width = min(width, 17 + QFontMetrics(self._chip_font(8)).horizontalAdvance('+' + str(len(items))))
        shown = []
        for measured in candidates:
            candidate = []
            for item in measured:
                used = sum(entry['width'] for entry in candidate) + 8 * len(candidate)
                if used + item['width'] + 8 + count_width > width:
                    break
                candidate.append(item)
            if len(candidate) > len(shown):
                shown = candidate
        omitted = len(items) - len(shown)
        return shown + [{'kind': 'count', 'count': omitted, 'overflow': bool(shown), 'width': count_width}]

    def _draw_binding_chips(self, painter, rect, item):
        color = QColor('#fbbf24' if item['long'] else '#7dd3fc')
        if self.is_pressed:
            color = QColor('#ffffff')
        font = self._chip_font(item['font'])
        fm = QFontMetrics(font)
        tokens = item['tokens']
        if item['stacked']:
            row_h = max(8, (rect.height() - 1) // 2)
            prefix = ' + '.join(tokens[:-1])
            prefix = fm.elidedText(prefix, Qt.ElideRight, max(8, rect.width() - 7))
            self._draw_chip(painter, QRect(rect.x(), rect.y(), rect.width() - 4, row_h), prefix, font, color, True)
            painter.setFont(font)
            painter.setPen(QColor(TOKENS['ink_3']))
            painter.drawText(QRect(rect.right() - 4, rect.y(), 5, row_h), Qt.AlignCenter, '+')
            main_rect = QRect(rect.x(), rect.y() + row_h + 1, rect.width(), row_h)
            main_font = self._chip_font(item['font'] + 1)
            main_text = QFontMetrics(main_font).elidedText(tokens[-1], Qt.ElideRight, rect.width() - 4)
            self._draw_chip(painter, main_rect, main_text, main_font, color, False)
            return
        if len(tokens) == 1 and item['width'] == rect.width():
            token = fm.elidedText(tokens[0], Qt.ElideRight, max(1, rect.width() - 8))
            token_widths = [min(rect.width() - 6, max(11, fm.horizontalAdvance(token) + 6))]
            tokens = (token,)
        else:
            token_widths = [max(11, fm.horizontalAdvance(token) + 6) for token in tokens]
        pill_h = min(rect.height() - 2, 19)
        y = rect.y() + (rect.height() - pill_h) // 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        if item['long']:
            painter.drawRoundedRect(QRect(rect.x(), y + pill_h // 2 - 1, 4, 3), 1, 1)
        else:
            painter.drawEllipse(QRect(rect.x() + 1, y + pill_h // 2 - 1, 3, 3))
        x = rect.x() + 6
        for index, (token, token_w) in enumerate(zip(tokens, token_widths)):
            self._draw_chip(painter, QRect(x, y, token_w, pill_h), token, font, color, index < len(tokens) - 1)
            x += token_w
            if index < len(tokens) - 1:
                painter.setFont(font)
                painter.setPen(QColor(TOKENS['ink_3']))
                painter.drawText(QRect(x, y, 6, pill_h), Qt.AlignCenter, '+')
                x += 6

    def _draw_chip(self, painter, rect, text, font, color, prefix):
        fill = QColor(color)
        fill.setAlpha(14 if prefix else 36)
        edge = QColor(color)
        edge.setAlpha(70 if prefix else 155)
        if self.is_pressed:
            fill = QColor(20, 20, 24, 185)
        painter.setBrush(fill)
        painter.setPen(QPen(edge, 1))
        painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 3, 3)
        painter.setFont(font)
        painter.setPen(QColor(TOKENS['ink_2']) if prefix and not self.is_pressed else color)
        painter.drawText(rect, Qt.AlignCenter, text)

    def _draw_binding_count(self, painter, rect, count, overflow):
        painter.setPen(QPen(QColor(TOKENS['ink_3']), 1))
        painter.setBrush(Qt.NoBrush)
        y = rect.center().y() - 4
        painter.drawRoundedRect(QRect(rect.x() + 1, y - 2, 6, 6), 1, 1)
        painter.drawRoundedRect(QRect(rect.x() + 4, y + 1, 6, 6), 1, 1)
        painter.setFont(self._chip_font(8))
        painter.setPen(QColor(TOKENS['ink_2']))
        painter.drawText(QRect(rect.x() + 12, rect.y(), rect.width() - 12, rect.height()),
                         Qt.AlignCenter, ('+' if overflow else '') + str(count))


# 标准 108 键主打字区 (60% Typing Block + F-Row) - 6 行每行精准对齐至 15.00U
TYPING_BLOCK_ROWS = [
    # 行 0：Esc 与 F1~F12 功能键区 (1.0 + 0.66 + 4.0 + 0.67 + 4.0 + 0.67 + 4.0 = 15.00U)
    [
        ("Esc", "Esc", 1.0), ("__gap__", "", 0.66),
        ("F1", "F1", 1.0), ("F2", "F2", 1.0), ("F3", "F3", 1.0), ("F4", "F4", 1.0), ("__gap__", "", 0.67),
        ("F5", "F5", 1.0), ("F6", "F6", 1.0), ("F7", "F7", 1.0), ("F8", "F8", 1.0), ("__gap__", "", 0.67),
        ("F9", "F9", 1.0), ("F10", "F10", 1.0), ("F11", "F11", 1.0), ("F12", "F12", 1.0),
    ],
    # 行 1：数字与符号行 (13*1.0 + 2.0 = 15.00U)
    [
        ("`", "` ~", 1.0), ("1", "1", 1.0), ("2", "2", 1.0), ("3", "3", 1.0), ("4", "4", 1.0),
        ("5", "5", 1.0), ("6", "6", 1.0), ("7", "7", 1.0), ("8", "8", 1.0), ("9", "9", 1.0),
        ("0", "0", 1.0), ("-", "- _", 1.0), ("=", "= +", 1.0), ("Backspace", "Back", 2.0),
    ],
    # 行 2：QWERTY 字母行 (1.5 + 12*1.0 + 1.5 = 15.00U)
    [
        ("Tab", "Tab", 1.5), ("Q", "Q", 1.0), ("W", "W", 1.0), ("E", "E", 1.0), ("R", "R", 1.0),
        ("T", "T", 1.0), ("Y", "Y", 1.0), ("U", "U", 1.0), ("I", "I", 1.0), ("O", "O", 1.0),
        ("P", "P", 1.0), ("[", "[ {", 1.0), ("]", "] }", 1.0), ("\\", "\\ |", 1.5),
    ],
    # 行 3：ASDF 基准行 (1.75 + 11*1.0 + 2.25 = 15.00U)
    [
        ("Caps", "Caps", 1.75), ("A", "A", 1.0), ("S", "S", 1.0), ("D", "D", 1.0), ("F", "F", 1.0),
        ("G", "G", 1.0), ("H", "H", 1.0), ("J", "J", 1.0), ("K", "K", 1.0), ("L", "L", 1.0),
        (";", "; :", 1.0), ("'", "' \"", 1.0), ("Enter", "Enter", 2.25),
    ],
    # 行 4：ZXCV 行 (2.25 + 10*1.0 + 2.75 = 15.00U)
    [
        ("Shift", "Shift", 2.25), ("Z", "Z", 1.0), ("X", "X", 1.0), ("C", "C", 1.0), ("V", "V", 1.0),
        ("B", "B", 1.0), ("N", "N", 1.0), ("M", "M", 1.0), (",", ", <", 1.0), (".", ". >", 1.0),
        ("/", "/ ?", 1.0), ("RShift", "Shift", 2.75),
    ],
    # 行 5：控制修饰与空格 (1.25*3 + 6.25 + 1.25*4 = 15.00U)
    [
        ("Ctrl", "Ctrl", 1.25), ("Win", "Win", 1.25), ("Alt", "Alt", 1.25),
        ("Space", "Space (空格)", 6.25),
        ("RAlt", "Alt", 1.25), ("Fn", "Fn", 1.25), ("Menu", "Menu", 1.25), ("RCtrl", "Ctrl", 1.25),
    ],
]

# 独立导航与方向键区 (Navigation & Arrows Block) - 3 列 6 行，精准固定 3.00U
NAV_BLOCK_ROWS = [
    # 行 0：控制功能三键
    [("PrtScn", "PrtSc", 1.0), ("ScrLk", "ScrLk", 1.0), ("Pause", "Pause", 1.0)],
    # 行 1：编辑导航上三键
    [("Insert", "Ins", 1.0), ("Home", "Home", 1.0), ("PgUp", "PgUp", 1.0)],
    # 行 2：编辑导航下三键
    [("Delete", "Del", 1.0), ("End", "End", 1.0), ("PgDn", "PgDn", 1.0)],
    # 行 3：功能留白占位 (高度 1.0U，与 ASDF 基准行齐平)
    [("__empty__", "", 3.0)],
    # 行 4：上方向键居中 (空 1.0U + 上 1.0U + 空 1.0U)
    [("__empty__", "", 1.0), ("Up", "↑", 1.0), ("__empty__", "", 1.0)],
    # 行 5：下左右方向键 (左 1.0U + 下 1.0U + 右 1.0U)
    [("Left", "←", 1.0), ("Down", "↓", 1.0), ("Right", "→", 1.0)],
]

# 标准 108 键数字小键盘区 (Numpad Block) - 6 行 4 列网格布局，严格固定 4.00U
# 每个元组定义：(row, col, rowspan, colspan, key_id, display_name, u_width, u_height)
NUMPAD_BLOCK_GRID = [
    # 行 0：108 键专属顶部多媒体/快捷四键
    (0, 0, 1, 1, "Mute", "Mute", 1.0, 1.0),
    (0, 1, 1, 1, "Vol-", "Vol -", 1.0, 1.0),
    (0, 2, 1, 1, "Vol+", "Vol +", 1.0, 1.0),
    (0, 3, 1, 1, "Calc", "Calc", 1.0, 1.0),
    # 行 1：小键盘控制与基础四则运算
    (1, 0, 1, 1, "NumLock", "Num", 1.0, 1.0),
    (1, 1, 1, 1, "Num/", "/", 1.0, 1.0),
    (1, 2, 1, 1, "Num*", "*", 1.0, 1.0),
    (1, 3, 1, 1, "Num-", "-", 1.0, 1.0),
    # 行 2 & 3：7 8 9 与 2U 高度 + 加号
    (2, 0, 1, 1, "Num7", "7", 1.0, 1.0),
    (2, 1, 1, 1, "Num8", "8", 1.0, 1.0),
    (2, 2, 1, 1, "Num9", "9", 1.0, 1.0),
    (2, 3, 2, 1, "Num+", "+", 1.0, 2.0),
    (3, 0, 1, 1, "Num4", "4", 1.0, 1.0),
    (3, 1, 1, 1, "Num5", "5", 1.0, 1.0),
    (3, 2, 1, 1, "Num6", "6", 1.0, 1.0),
    # 行 4 & 5：1 2 3 与 2U 宽 0、. 以及 2U 高度 Enter
    (4, 0, 1, 1, "Num1", "1", 1.0, 1.0),
    (4, 1, 1, 1, "Num2", "2", 1.0, 1.0),
    (4, 2, 1, 1, "Num3", "3", 1.0, 1.0),
    (4, 3, 2, 1, "NumEnter", "Enter", 1.0, 2.0),
    (5, 0, 1, 2, "Num0", "0", 2.0, 1.0),
    (5, 2, 1, 1, "Num.", ".", 1.0, 1.0),
]

KEY_DESCRIPTIONS = {
    "Mute": "静音 (Mute)",
    "Vol-": "音量- (Vol -)",
    "Vol+": "音量+ (Vol +)",
    "Calc": "计算器 (Calc)",
    "NumLock": "数字键盘锁定 (NumLock)",
    "NumEnter": "小键盘回车 (Num Enter)",
    "Num+": "小键盘加号 (Num +)",
    "Num-": "小键盘减号 (Num -)",
    "Num*": "小键盘乘号 (Num *)",
    "Num/": "小键盘除号 (Num /)",
    "Num.": "小键盘点 (Num .)",
    "Num0": "小键盘 0",
    "Num1": "小键盘 1",
    "Num2": "小键盘 2",
    "Num3": "小键盘 3",
    "Num4": "小键盘 4",
    "Num5": "小键盘 5",
    "Num6": "小键盘 6",
    "Num7": "小键盘 7",
    "Num8": "小键盘 8",
    "Num9": "小键盘 9",
    "mouse:left": "鼠标左键 (普攻/快门/点击)",
    "mouse:right": "鼠标右键 (瞄准/蓄力/交互)",
    "mouse:middle": "鼠标中键",
    "mouse:wheel_up": "滚轮向上",
    "mouse:wheel_down": "滚轮向下",
    "action:capture": "快速截屏 (4K 无损)",
    "action:replay_record": "精彩回放 (回溯录像)",
    "action:record_toggle": "录屏开关",
}

KEYBOARD_LAYOUT = TYPING_BLOCK_ROWS

MOUSE_CONTROLS = [
    ("mouse:left", "左键", 2.4),
    ("mouse:right", "右键", 2.4),
    ("mouse:middle", "中键", 1.8),
    ("mouse:wheel_up", "滚轮 ↑", 1.8),
    ("mouse:wheel_down", "滚轮 ↓", 1.8),
]

ACTION_CONTROLS = [
    ("action:capture", "快速截屏", 3.8),
    ("action:replay_record", "精彩回放", 4.4),
    ("action:record_toggle", "录屏开关", 4.0),
]


class KeyboardCanvas(QWidget):
    """自适应全画幅键盘画布，监听视口缩放并自适应调整键帽基准像素"""
    def __init__(self, page, parent=None):
        super().__init__(parent)
        self.page = page
        self._last_u = 0

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self.page, 'area'):
            self.fit_width(max(0, self.page.area.viewport().width() - 32))

    def fit_width(self, width):
        # 按实际视口计算，避免固定键帽的最小尺寸阻止画布在窄窗口中缩小。
        target_u = max(28, min(100, int((width - 140) / 22.4)))
        if target_u != self._last_u:
            self._last_u = target_u
            target_h = max(34, int(round(target_u * 0.94)))
            self.page.recompute_key_sizes(target_u, target_h)


class VirtualKbmPage(QWidget):
    """标准布局虚拟键鼠与手柄按键全映射工作台"""
    def __init__(self, owner, store=None, parent=None):
        super().__init__(parent)
        from .mapping_ui import BindingList
        self.owner = owner
        self.store = store or owner.store
        self.keycaps = {}
        self.is_capturing = False
        self.device_state = None
        self._gaps = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        # 预设操作和输入选项在固定工具区分组，键盘与详情共用下方滚动区。
        toolbar, toolbar_layout = card()
        toolbar_layout.setContentsMargins(16, 14, 16, 14)
        toolbar_layout.setSpacing(12)
        self.preset_controls = QWidget()
        tools = QHBoxLayout(self.preset_controls)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(10)
        tools.addWidget(label(tr('键鼠预设'), 'section'))
        self.scheme_combo = QComboBox()
        self.scheme_combo.setMinimumWidth(180)
        self.scheme_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.scheme_combo.setAccessibleName(tr('键鼠预设'))
        self.scheme_combo.currentTextChanged.connect(self.on_scheme_changed)
        tools.addWidget(self.scheme_combo, 1)

        self.scheme_active_badge = QLabel(tr('✓ 已加载生效'))
        self.scheme_active_badge.setStyleSheet(f"background: rgba(16, 185, 129, 0.15); color: {TOKENS['green']}; border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 6px; padding: 3px 8px; font-weight: 700; font-size: 11px;")
        tools.addWidget(self.scheme_active_badge)

        self.scheme_activate_btn = button(tr('设为当前生效'), self.activate_current_scheme, primary=True)
        tools.addWidget(self.scheme_activate_btn)
        self.preset_actions = QWidget()
        actions = QHBoxLayout(self.preset_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        self.new_preset_btn = button(tr('新建预设'), lambda: owner.duplicate_profile(target_mode='kbm'))
        actions.addWidget(self.new_preset_btn)
        self.btn_toggle_bindings = button(tr('详细列表'), self.toggle_bindings_list)
        self.btn_toggle_bindings.setCheckable(True)
        actions.addWidget(self.btn_toggle_bindings)
        more = QPushButton(tr('更多设置'))
        more.setCursor(Qt.PointingHandCursor)
        more_menu = QMenu(more)
        more_menu.addAction(tr('后台设置'), lambda: owner.navigate(4))
        more_menu.addAction(tr('设备隐身'), self.open_cloaking)
        if hasattr(owner, 'reset_profile'):
            more_menu.addSeparator()
            more_menu.addAction(tr('恢复默认'), owner.reset_profile)
        more.setMenu(more_menu)
        actions.addWidget(more)
        self.toolbar_grid = QGridLayout()
        self.toolbar_grid.setContentsMargins(0, 0, 0, 0)
        self.toolbar_grid.setHorizontalSpacing(12)
        self.toolbar_grid.setVerticalSpacing(10)
        self.toolbar_grid.setColumnStretch(0, 1)
        self.toolbar_grid.addWidget(self.preset_controls, 0, 0)
        self.toolbar_grid.addWidget(self.preset_actions, 0, 1)
        self._toolbar_stacked = False
        toolbar_layout.addLayout(self.toolbar_grid)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {TOKENS['border']}; border: none;")
        toolbar_layout.addWidget(divider)

        options = QHBoxLayout()
        options.setSpacing(12)
        options.addWidget(label(tr('输入选项'), 'caption'))
        self.mouse_toggle = QCheckBox(tr('右摇杆控制鼠标'))
        self.mouse_toggle.toggled.connect(lambda enabled: owner.mapping_change({'op': 'options', 'profile': self.current_scheme(), 'options': {'right_stick_mouse': enabled}}))
        options.addWidget(self.mouse_toggle)
        self.mouse_settings_btn = button(tr('指针设置'), self.mouse_settings)
        options.addWidget(self.mouse_settings_btn)

        self.preview_toggle = QCheckBox(tr('安全试按'))
        self.preview_toggle.setChecked(False)  # 默认关闭安全试按，确保启动即可畅快操作
        self.preview_toggle.setToolTip(tr('勾选后，当前映射窗口有焦点时仅回显，不向系统发送键鼠；取消勾选或切至游戏即可正常输出。'))
        options.addWidget(self.preview_toggle)
        options.addStretch()
        toolbar_layout.addLayout(options)
        layout.addWidget(toolbar)

        feedback = QHBoxLayout()
        feedback.setContentsMargins(4, 0, 4, 0)
        feedback.setSpacing(10)
        feedback.addWidget(label(tr('实时反馈'), 'caption'))
        self.live = QLabel(tr('等待输入'))
        self.live.setObjectName('muted')
        self.live.setWordWrap(True)
        self.live.setMinimumWidth(0)
        self.live.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        feedback.addWidget(self.live, 1)
        layout.addLayout(feedback)

        # 键盘区域保持自然高度，空余空间留在组间，避免各行被拉开。
        content = QWidget()
        content.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(16)
        content_layout.setSizeConstraint(QLayout.SetNoConstraint)
        keyboard_card, keyboard_layout = card()
        keyboard_layout.setContentsMargins(16, 16, 16, 16)
        keyboard_layout.setSpacing(16)
        board_header = QHBoxLayout()
        board_header.setSpacing(16)
        heading = QVBoxLayout()
        heading.setSpacing(4)
        heading.addWidget(label(tr('键盘映射'), 'section'))
        heading.addWidget(label(tr('点击按键添加或编辑绑定，右键清除绑定。'), 'caption', wrap=True))
        board_header.addLayout(heading, 1)
        for title, color in (('短按', '#38bdf8'), ('长按', TOKENS['amber']), ('短按 + 长按', TOKENS['purple'])):
            legend = label(tr(title), 'caption')
            legend.setStyleSheet(f"color: {color}; font-size: 11px;")
            board_header.addWidget(legend)
        keyboard_layout.addLayout(board_header)
        self.canvas = KeyboardCanvas(self)
        # 画布宽度始终跟随视口，键帽的自然宽度不会锁住外层最小尺寸。
        self.canvas.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Maximum)
        canvas_layout = QVBoxLayout(self.canvas)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(0)

        # 键盘和下方输出按键作为一个紧凑内容组，在整个卡片中居中。
        # 固定组的自然宽度，使大屏留白落在两侧，避免被拉成零散列。
        self.key_group = QWidget()
        self.key_group.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Maximum)
        group_layout = QVBoxLayout(self.key_group)
        group_layout.setContentsMargins(0, 0, 0, 0)
        group_layout.setSpacing(20)

        blocks = QHBoxLayout()
        blocks.setContentsMargins(0, 0, 0, 0)
        blocks.setSpacing(18)
        blocks.addStretch(1)

        # 打字区与独立导航区
        for rows in (TYPING_BLOCK_ROWS, NAV_BLOCK_ROWS):
            block = QWidget()
            vertical = QVBoxLayout(block)
            vertical.setContentsMargins(0, 0, 0, 0)
            vertical.setSpacing(4)
            vertical.setAlignment(Qt.AlignTop)
            block.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            for row in rows:
                horizontal = QHBoxLayout()
                horizontal.setContentsMargins(0, 0, 0, 0)
                horizontal.setSpacing(4)
                for key, name, u_width in row:
                    if key.startswith('__'):
                        gap = QWidget()
                        horizontal.addWidget(gap)
                        self._gaps.append((gap, u_width))
                    else:
                        horizontal.addWidget(self.make_key(key, name, u_width, 1.0))
                vertical.addLayout(horizontal)
            blocks.addWidget(block)

        # 独立数字小键盘区
        numpad = QWidget()
        grid = QGridLayout(numpad)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        numpad.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        for row, col, rs, cs, key, name, u_w, u_h in NUMPAD_BLOCK_GRID:
            grid.addWidget(self.make_key(key, name, u_w, u_h), row, col, rs, cs)
        blocks.addWidget(numpad)
        blocks.addStretch(1)
        group_layout.addLayout(blocks)

        # 鼠标和录制动作有各自标题与清晰间隔，相关按键紧凑排列。
        outputs = QHBoxLayout()
        outputs.setContentsMargins(0, 0, 0, 0)
        outputs.setSpacing(20)
        for title, controls in (('鼠标按键', MOUSE_CONTROLS), ('快捷动作', ACTION_CONTROLS)):
            output_group = QVBoxLayout()
            output_group.setSpacing(8)
            output_group.addWidget(label(tr(title), 'caption'))
            output_keys = QHBoxLayout()
            output_keys.setContentsMargins(0, 0, 0, 0)
            output_keys.setSpacing(6)
            for key, name, u_w in controls:
                output_keys.addWidget(self.make_key(key, name, u_w, 1.0))
            output_group.addLayout(output_keys)
            outputs.addLayout(output_group)
        group_layout.addLayout(outputs)
        canvas_layout.addWidget(self.key_group, 0, Qt.AlignHCenter)
        keyboard_layout.addWidget(self.canvas)
        content_layout.addWidget(keyboard_card)

        self.area = QScrollArea()
        self.area.setWidget(content)
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.NoFrame)
        self.area.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }")
        self.area.viewport().installEventFilter(self)
        self.canvas.setStyleSheet("background: transparent;")
        layout.addWidget(self.area, 1)

        # 4. 可选折叠的绑定表格（默认折叠，不浪费主页面空间）
        self.bindings = BindingList(owner, profile_getter=lambda: self.current_scheme(), mode='kbm')
        self.bindings.hide()
        self.bindings.setMinimumHeight(240)
        content_layout.addWidget(self.bindings)
        content_layout.addStretch()

        self.refresh_display()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrange_toolbar()

    def _arrange_toolbar(self):
        if not hasattr(self, 'toolbar_grid'):
            return
        needed = self.preset_controls.minimumSizeHint().width() + self.preset_actions.sizeHint().width() + 12
        stacked = needed > self.width() - 32
        if stacked == self._toolbar_stacked:
            return
        self._toolbar_stacked = stacked
        self.toolbar_grid.removeWidget(self.preset_controls)
        self.toolbar_grid.removeWidget(self.preset_actions)
        self.toolbar_grid.addWidget(self.preset_controls, 0, 0, 1, 2 if stacked else 1)
        if stacked:
            self.toolbar_grid.addWidget(self.preset_actions, 1, 0, 1, 2, Qt.AlignRight)
        else:
            self.toolbar_grid.addWidget(self.preset_actions, 0, 1)

    def eventFilter(self, watched, event):
        if watched is self.area.viewport() and event.type() == QEvent.Resize:
            self.canvas.fit_width(max(0, event.size().width() - 32))
        return super().eventFilter(watched, event)

    def current_scheme(self):
        kbm_items = self.store.profiles_for(self.owner.snapshot, mode='kbm')
        txt = self.scheme_combo.currentText()
        if txt in kbm_items:
            return txt
        active = self.owner.config.get('active_profile', '')
        if active in kbm_items:
            return active
        previous = getattr(self, '_last_kbm_profile', '')
        if previous in kbm_items:
            return previous
        return next(iter(kbm_items), '')

    def on_scheme_changed(self, name):
        if not name:
            return
        self._last_kbm_profile = name
        self.owner.change_profile(name)

    def activate_current_scheme(self):
        scheme = self.current_scheme()
        if scheme:
            self.owner.change_profile(scheme)

    def toggle_bindings_list(self):
        """展开或折叠详细绑定表格"""
        if not self.bindings.isHidden():
            self.bindings.hide()
            self.btn_toggle_bindings.setText(tr('详细列表'))
            self.btn_toggle_bindings.setChecked(False)
        else:
            self.bindings.show()
            self.btn_toggle_bindings.setText(tr('收起列表'))
            self.btn_toggle_bindings.setChecked(True)
            QTimer.singleShot(0, lambda: self.area.ensureWidgetVisible(self.bindings, 0, 12))

    def recompute_key_sizes(self, u_px: int, h_px: int):
        """动态刷新全部键帽像素尺寸"""
        for cap in self.keycaps.values():
            cap.set_unit_size(u_px, h_px, 4)
        for gap, u_gap in self._gaps:
            gap_w = max(4, int(round(u_gap * u_px + (u_gap - 1.0) * 4)))
            gap.setFixedSize(gap_w, h_px)

    def make_key(self, key, name, u_width, u_height=1.0):
        cap = KeyCap(key, name, u_width, u_height)
        cap.left_clicked.connect(self.edit_output)
        cap.right_clicked.connect(self.handle_right_click)
        self.keycaps[key] = cap
        if key == 'Fn':
            cap.setEnabled(False)
            cap.setToolTip('Fn 由键盘硬件处理')
        return cap

    def handle_right_click(self, key, name):
        """右键点击按键：支持快速清除该键绑定的手柄输入"""
        from .mapping_engine import output_tokens, effective_mappings
        token = self.key_token(key)
        scheme = self.current_scheme()
        entries = effective_mappings(self.owner.config, self.owner.snapshot, scheme)
        matches = [trigger for trigger, entry in entries.items()
                   if any(token in output_tokens(entry.get(g, {})) for g in ('short', 'long'))]
        if matches:
            from PySide6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self, '清除绑定',
                f'确定要清除虚拟按键【{name}】的手柄绑定吗？\n涉及手柄按键：{", ".join(matches)}',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                for trigger in matches:
                    self.owner.mapping_change({'op': 'unbind', 'trigger': trigger, 'profile': scheme})
        else:
            self.edit_output(key, name)

    def mouse_settings(self):
        from PySide6.QtWidgets import QDialog, QFormLayout, QDoubleSpinBox, QDialogButtonBox
        import copy
        profile = self.current_scheme()
        settings = copy.deepcopy(self.owner.config.get('profile_options', {}).get(profile, {}).get('mouse', {}))
        dialog = QDialog(self); dialog.setWindowTitle('右摇杆指针'); form = QFormLayout(dialog)
        mode = QComboBox(); mode.addItem('游戏视角', 'game'); mode.addItem('桌面指针', 'desktop')
        mode.setCurrentIndex(max(0, mode.findData(settings.get('mode', 'game')))); form.addRow('用途', mode)
        fields = {}
        for key, title, low, high, default in [('sensitivity', '速度', 1, 100, 28), ('deadzone', '死区', .01, .5, .06), ('y_ratio', '垂直比例', .1, 2, .7), ('edge_boost', '推满加速', 1, 3, 1.7)]:
            field = QDoubleSpinBox(); field.setRange(low, high); field.setSingleStep(.01 if high <= 3 else 1); field.setValue(settings.get(key, default))
            form.addRow(title, field); fields[key] = field
        controls = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        controls.button(QDialogButtonBox.Save).setText('保存'); controls.button(QDialogButtonBox.Cancel).setText('取消')
        controls.accepted.connect(dialog.accept); controls.rejected.connect(dialog.reject); form.addRow(controls)
        if dialog.exec() == QDialog.Accepted:
            settings.update({key: field.value() for key, field in fields.items()}); settings['mode'] = mode.currentData()
            self.owner.mapping_change({'op': 'options', 'profile': profile, 'options': {'mouse': settings}})

    def edit_output(self, key, name):
        from .mapping_engine import output_tokens, effective_mappings
        token = self.key_token(key)
        scheme = self.current_scheme()
        entries = effective_mappings(self.owner.config, self.owner.snapshot, scheme)
        matches = [trigger for trigger, entry in entries.items()
                   if any(token in output_tokens(entry.get(g, {})) for g in ('short', 'long'))]
        if len(matches) == 1:
            self.owner.edit_mapping(matches[0], profile=scheme, mode='kbm')
        elif matches:
            from PySide6.QtWidgets import QInputDialog
            options = ['添加新的绑定'] + matches
            value, ok = QInputDialog.getItem(self, name, '选择绑定', options, 0, False)
            if ok:
                self.owner.edit_mapping(value if value != options[0] else '0', new=value == options[0],
                                        output=key if value == options[0] else None, profile=scheme, mode='kbm')
        else:
            self.owner.edit_mapping('0', new=True, output=key, profile=scheme, mode='kbm')

    @staticmethod
    def key_token(key):
        from .actions import parse_keys
        if key.startswith(('mouse:', 'action:')): return key
        try: return 'key:' + str(parse_keys(key)[0])
        except (ValueError, IndexError): return ''

    def refresh_display(self):
        from .mapping_engine import effective_mappings, output_tokens, trigger_label, profile_family
        from .studio_core import profile_mode
        config = self.owner.config
        kbm_items = self.store.profiles_for(self.owner.snapshot, mode='kbm')
        previous = self.current_scheme()

        self.scheme_combo.blockSignals(True)
        self.scheme_combo.clear()
        self.scheme_combo.setPlaceholderText(tr('选择键鼠预设...'))
        for p_name in kbm_items:
            self.scheme_combo.addItem(p_name)

        active = config.get('active_profile', '')
        if profile_mode(config, active) == 'kbm' and active in kbm_items:
            self.scheme_combo.setCurrentText(active)
            is_active = True
        else:
            selected = previous if previous in kbm_items else next(iter(kbm_items), '')
            if selected:
                self.scheme_combo.setCurrentText(selected)
            else:
                self.scheme_combo.setCurrentIndex(-1)
            is_active = False
        self.scheme_combo.blockSignals(False)

        selected = self.current_scheme()
        if selected:
            self._last_kbm_profile = selected

        if hasattr(self, 'scheme_active_badge'):
            if is_active:
                self.scheme_active_badge.setText(tr('✓ 已加载生效'))
                self.scheme_active_badge.setStyleSheet(f"background: rgba(16, 185, 129, 0.15); color: {TOKENS['green']}; border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 6px; padding: 3px 8px; font-weight: 700; font-size: 11px;")
                self.scheme_activate_btn.hide()
            else:
                self.scheme_active_badge.setText(tr('未激活'))
                self.scheme_active_badge.setStyleSheet(f"background: {TOKENS['surface']}; color: {TOKENS['ink_3']}; border: 1px solid {TOKENS['border']}; border-radius: 6px; padding: 3px 8px; font-weight: 600; font-size: 11px;")
                self.scheme_activate_btn.show()
            has_selection = self.scheme_combo.currentIndex() >= 0
            self.scheme_activate_btn.setEnabled(has_selection)
            for control, primary in ((self.scheme_activate_btn, has_selection), (self.new_preset_btn, not has_selection)):
                control.setObjectName('primary' if primary else '')
                control.style().unpolish(control)
                control.style().polish(control)
            self._arrange_toolbar()

        self.mouse_toggle.blockSignals(True)
        self.mouse_toggle.setChecked(config.get('profile_options', {}).get(selected, {}).get('right_stick_mouse', False))
        self.mouse_toggle.blockSignals(False)
        family = profile_family(config, self.owner.snapshot, selected)

        # 结构化抽取绑定信息，严格分离短按与长按
        badges = {}
        for trigger, entry in effective_mappings(config, self.owner.snapshot, selected).items():
            t_label = trigger_label(trigger, family)
            for gesture in ('short', 'long'):
                binding = entry.get(gesture, {})
                for token in output_tokens(binding):
                    badges.setdefault(token, []).append({
                        'trigger': t_label,
                        'gesture': gesture,
                    })

        for key, cap in self.keycaps.items():
            cap.set_mapping_info(badges.get(self.key_token(key), []), False)
        if hasattr(self, 'bindings'):
            self.bindings.refresh()

    def set_device_state(self, state):
        self.device_state = state

    def update_feedback(self, data, connected=False, suspended=False):
        from .mapping_engine import trigger_label
        pressed = set(data.get('outputs', [])) | set(data.get('recent_outputs', []))
        for key, cap in self.keycaps.items():
            active = self.key_token(key) in pressed
            if getattr(cap, 'is_pressed', False) != active:
                cap.is_pressed = active
                cap.update()
        events = data.get('events', [])
        state = self.device_state or {}
        family = state.get('family', 'dualsense')
        inputs = ' + '.join(trigger_label(k, family) for k in data.get('inputs', []))
        status = '编辑中 · 输出已暂停' if suspended else ('已连接' if connected else '未连接')
        if data.get('preview') and not suspended: status = '安全试按 · 不发送到游戏'
        last = events[-1] if events else None
        result = (trigger_label(last['trigger'], family) + ' ' + ('长按' if last['gesture'] == 'long' else '短按') + ' → ' + last['action']) if last and last.get('trigger') else ''
        self.live.setText(' · '.join(x for x in (status, inputs, result) if x))
        if hasattr(self, 'bindings'):
            self.bindings.feedback(data)

    def cancel_capture(self):
        self.is_capturing = False

    def handle_device_input(self, state):
        self.set_device_state(state)

    def get_current_device_info(self):
        state = self.device_state or self.owner.snapshot or {}
        return state.get('vendor'), state.get('product')

    def open_cloaking(self):
        from PySide6.QtWidgets import QDialog
        if not hasattr(self, 'hidhide'):
            self.hidhide = HidHideClient()
            self.cloaking_dialog = QDialog(self); self.cloaking_dialog.setWindowTitle('设备隐身')
            layout = QVBoxLayout(self.cloaking_dialog)
            self.cloaking_toggle = QCheckBox('向其他应用隐藏手柄原始输入')
            self.cloaking_status_label = QLabel(); self.cloaking_status_label.setWordWrap(True)
            self.btn_install_driver = QPushButton('打开 HidHide 驱动页面')
            self.btn_install_driver.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(HIDHIDE_RELEASE_URL)))
            layout.addWidget(self.cloaking_status_label); layout.addWidget(self.cloaking_toggle); layout.addWidget(self.btn_install_driver)
            self.cloaking_toggle.toggled.connect(self.toggle_cloaking)
        self.refresh_cloaking_status()
        self.cloaking_dialog.show()

    def refresh_cloaking_status(self):
        vendor, product = self.get_current_device_info()
        installed = self.hidhide.is_driver_installed()
        active = bool(installed and self.hidhide.is_active())
        self.cloaking_toggle.blockSignals(True); self.cloaking_toggle.setChecked(active)
        self.cloaking_toggle.setEnabled(bool(installed and vendor)); self.cloaking_toggle.blockSignals(False)
        self.btn_install_driver.setVisible(not installed)
        text = 'HidHide 未安装' if not installed else ('已隐身（物理设备已对外部应用彻底屏蔽）' if active else '原始输入可见（未开启屏蔽）')
        self.cloaking_status_label.setText(text)

    def toggle_cloaking(self, enabled):
        vendor, product = self.get_current_device_info()
        ok, text = self.hidhide.cloak_controller(vendor, product) if enabled else self.hidhide.uncloak_controller(vendor, product)
        if ok: self.owner.setting('device_cloaking_enabled', enabled)
        self.refresh_cloaking_status()
        self.cloaking_status_label.setText(text)

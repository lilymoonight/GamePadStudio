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
from PySide6.QtCore import Qt, QSize, Signal, QUrl, QRect
from PySide6.QtGui import (
    QColor, QFont, QDesktopServices, QPainter, QPen, QBrush, QFontMetrics
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QComboBox, QLineEdit, QScrollArea,
    QFrame, QSlider, QInputDialog, QMessageBox, QSizePolicy, QCheckBox
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
        self.setFocusPolicy(Qt.NoFocus)
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

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self.right_clicked.emit(self.key_id, self.display_name)
        elif event.button() == Qt.LeftButton:
            self.left_clicked.emit(self.key_id, self.display_name)
        super().mousePressEvent(event)

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
            lines.append("已绑定手柄触发：")
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
            if self.is_special:
                bg_color = QColor(18, 18, 22, 190 if not self.is_hovered else 230)
            else:
                bg_color = QColor(26, 26, 32, 210 if not self.is_hovered else 240)
            border_color = QColor(255, 255, 255, 28 if not self.is_hovered else 65)
            bg_brush = QBrush(bg_color)
            border_pen = QPen(border_color, 1.0)
            text_color = QColor(245, 245, 250) if self.is_hovered else QColor(160, 165, 175)

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
        painter.setFont(main_font)
        painter.setPen(text_color)

        if not (self.is_capturing or has_badges):
            painter.drawText(rect, Qt.AlignCenter, self.display_name)
        else:
            # 顶部空间居中绘制虚拟键名称
            top_h = int(h * 0.44)
            top_rect = QRect(2, 2, w - 4, top_h)
            painter.drawText(top_rect, Qt.AlignCenter, self.display_name)

            # 底部空间绘制绑定的手柄按键与长按/短按标识
            badge_rect = QRect(3, top_h, w - 6, h - top_h - 3)
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
        w, h = rect.width(), rect.height()
        items = []
        for t in self.short_bindings:
            items.append((f"{compact_trigger(t)} 短", False))
        for t in self.long_bindings:
            items.append((f"{compact_trigger(t)} 长", True))

        if not items:
            return

        badge_font = QFont("Microsoft YaHei", -1)
        badge_font.setFamilies(["Microsoft YaHei", "Segoe UI", "PingFang SC", "sans-serif"])
        font_size = max(7, min(10, int(h * 0.46)))
        badge_font.setPixelSize(font_size)
        badge_font.setBold(True)
        painter.setFont(badge_font)
        fm = QFontMetrics(badge_font)

        pill_h = max(13, min(20, h - 2))
        pill_y = rect.y() + (h - pill_h) // 2

        if len(items) == 1 or w < 72:
            # 单项或单键标准 1U 宽度：绘制单个徽章
            text, is_long = items[0]
            if len(items) > 1:
                t_short = [compact_trigger(x) for x in self.short_bindings]
                t_long = [compact_trigger(x) for x in self.long_bindings]
                if t_short and t_long:
                    text = f"{t_short[0]}短·{t_long[0]}长"
                    is_long = False
                elif len(t_short) > 1:
                    text = f"{t_short[0]}+{len(t_short)-1}短"
                    is_long = False
                elif len(t_long) > 1:
                    text = f"{t_long[0]}+{len(t_long)-1}长"
                    is_long = True

            text_w = fm.horizontalAdvance(text)
            pill_w = min(w - 2, text_w + 8)
            pill_x = rect.x() + (w - pill_w) // 2
            pill_rect = QRect(pill_x, pill_y, pill_w, pill_h)

            if self.is_pressed:
                b_bg = QBrush(QColor(20, 20, 24, 200))
                b_pen = QPen(QColor(40, 40, 48), 1.0)
                t_col = QColor(255, 255, 255)
            elif is_long:
                # 长按：暖琥珀金徽章
                b_bg = QBrush(QColor(245, 158, 11, 65))
                b_pen = QPen(QColor(251, 191, 36, 210), 1.0)
                t_col = QColor(251, 191, 36)
            else:
                # 短按：电光青蓝徽章
                b_bg = QBrush(QColor(14, 165, 233, 55))
                b_pen = QPen(QColor(56, 189, 248, 210), 1.0)
                t_col = QColor(56, 189, 248)

            painter.setBrush(b_bg)
            painter.setPen(b_pen)
            painter.drawRoundedRect(pill_rect, 4.0, 4.0)
            painter.setPen(t_col)
            painter.drawText(pill_rect, Qt.AlignCenter, text)

        else:
            # 宽键（Tab, Space, Enter, Shift, Backspace 等）：横向并排展示多枚徽章
            show_items = items[:3] if w >= 130 else items[:2]
            n = len(show_items)
            gap = 4
            avail_w = (w - (n - 1) * gap) // n
            cur_x = rect.x()
            for text, is_long in show_items:
                pill_rect = QRect(cur_x, pill_y, avail_w, pill_h)
                if self.is_pressed:
                    b_bg = QBrush(QColor(20, 20, 24, 200))
                    b_pen = QPen(QColor(40, 40, 48), 1.0)
                    t_col = QColor(255, 255, 255)
                elif is_long:
                    b_bg = QBrush(QColor(245, 158, 11, 65))
                    b_pen = QPen(QColor(251, 191, 36, 210), 1.0)
                    t_col = QColor(251, 191, 36)
                else:
                    b_bg = QBrush(QColor(14, 165, 233, 55))
                    b_pen = QPen(QColor(56, 189, 248, 210), 1.0)
                    t_col = QColor(56, 189, 248)

                painter.setBrush(b_bg)
                painter.setPen(b_pen)
                painter.drawRoundedRect(pill_rect, 4.0, 4.0)
                painter.setPen(t_col)
                painter.drawText(pill_rect, Qt.AlignCenter, text)
                cur_x += avail_w + gap


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
        ("0", "0", 1.0), ("-", "- _", 1.0), ("=", "= +", 1.0), ("Backspace", "⌫ Back", 2.0),
    ],
    # 行 2：QWERTY 字母行 (1.5 + 12*1.0 + 1.5 = 15.00U)
    [
        ("Tab", "Tab ⇥", 1.5), ("Q", "Q", 1.0), ("W", "W", 1.0), ("E", "E", 1.0), ("R", "R", 1.0),
        ("T", "T", 1.0), ("Y", "Y", 1.0), ("U", "U", 1.0), ("I", "I", 1.0), ("O", "O", 1.0),
        ("P", "P", 1.0), ("[", "[ {", 1.0), ("]", "] }", 1.0), ("\\", "\\ |", 1.5),
    ],
    # 行 3：ASDF 基准行 (1.75 + 11*1.0 + 2.25 = 15.00U)
    [
        ("Caps", "Caps", 1.75), ("A", "A", 1.0), ("S", "S", 1.0), ("D", "D", 1.0), ("F", "F", 1.0),
        ("G", "G", 1.0), ("H", "H", 1.0), ("J", "J", 1.0), ("K", "K", 1.0), ("L", "L", 1.0),
        (";", "; :", 1.0), ("'", "' \"", 1.0), ("Enter", "Enter ↵", 2.25),
    ],
    # 行 4：ZXCV 行 (2.25 + 10*1.0 + 2.75 = 15.00U)
    [
        ("Shift", "⇧ Shift", 2.25), ("Z", "Z", 1.0), ("X", "X", 1.0), ("C", "C", 1.0), ("V", "V", 1.0),
        ("B", "B", 1.0), ("N", "N", 1.0), ("M", "M", 1.0), (",", ", <", 1.0), (".", ". >", 1.0),
        ("/", "/ ?", 1.0), ("RShift", "Shift ⇧", 2.75),
    ],
    # 行 5：控制修饰与空格 (1.25*3 + 6.25 + 1.25*4 = 15.00U)
    [
        ("Ctrl", "Ctrl", 1.25), ("Win", "Win ⊞", 1.25), ("Alt", "Alt", 1.25),
        ("Space", "Space (空格)", 6.25),
        ("RAlt", "Alt", 1.25), ("Fn", "Fn", 1.25), ("Menu", "Menu ☰", 1.25), ("RCtrl", "Ctrl", 1.25),
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
    (0, 0, 1, 1, "Mute", "🔇", 1.0, 1.0),
    (0, 1, 1, 1, "Vol-", "🔉", 1.0, 1.0),
    (0, 2, 1, 1, "Vol+", "🔊", 1.0, 1.0),
    (0, 3, 1, 1, "Calc", "🧮", 1.0, 1.0),
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
    (4, 3, 2, 1, "NumEnter", "↵", 1.0, 2.0),
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
    ("mouse:left", "🖱️ 左键", 2.4),
    ("mouse:right", "🖱️ 右键", 2.4),
    ("mouse:middle", "🖱️ 中键", 1.8),
    ("mouse:wheel_up", "🖱️ 滚轮 ↑", 1.8),
    ("mouse:wheel_down", "🖱️ 滚轮 ↓", 1.8),
]

ACTION_CONTROLS = [
    ("action:capture", "📸 快速截屏", 3.8),
    ("action:replay_record", "🎬 精彩回放", 4.4),
    ("action:record_toggle", "🎥 录屏开关", 4.0),
]


class KeyboardCanvas(QWidget):
    """自适应全画幅键盘画布，监听视口缩放并自适应调整键帽基准像素"""
    def __init__(self, page, parent=None):
        super().__init__(parent)
        self.page = page
        self._last_u = 0

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w = event.size().width()
        # 键盘总宽约 23.6U (打字区 15U + 间隔 0.5U + 导航区 3U + 间隔 0.5U + 数字小键盘 4U + 边距)
        # 单键基准宽度自适应：在 46px 到 76px 之间平滑适配
        target_u = max(46, min(76, int((w - 56) / 23.6)))
        if target_u != self._last_u:
            self._last_u = target_u
            target_h = int(round(target_u * 0.94))
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
        layout.setSpacing(10)

        # 1. 顶部控制栏
        tools = QHBoxLayout()
        tools.setSpacing(10)
        tools.addWidget(label(tr('当前核心配置：'), 'muted'))
        self.scheme_combo = QComboBox()
        self.scheme_combo.currentTextChanged.connect(owner.change_profile)
        tools.addWidget(self.scheme_combo, 1)

        self.mouse_toggle = QCheckBox('右摇杆控制视角/鼠标')
        self.mouse_toggle.toggled.connect(lambda enabled: owner.mapping_change({'op': 'options', 'options': {'right_stick_mouse': enabled}}))
        tools.addWidget(self.mouse_toggle)
        tools.addWidget(button('指针设置', self.mouse_settings))

        self.preview_toggle = QCheckBox('安全试按')
        self.preview_toggle.setChecked(False)  # 默认关闭安全试按，确保启动即可畅快操作
        self.preview_toggle.setToolTip('勾选后，当前映射窗口有焦点时仅回显，不向系统发送键鼠；取消勾选或切至游戏即可正常输出。')
        tools.addWidget(self.preview_toggle)

        if hasattr(owner, 'reset_profile'):
            tools.addWidget(button('恢复默认', owner.reset_profile))
        tools.addWidget(button('新建预设', owner.duplicate_profile))
        tools.addWidget(button('后台设置', lambda: owner.navigate(4)))
        tools.addWidget(button('设备隐身', self.open_cloaking))

        self.btn_toggle_bindings = button('📋 详细列表', self.toggle_bindings_list)
        tools.addWidget(self.btn_toggle_bindings)
        layout.addLayout(tools)

        # 2. 实时输入反馈栏
        self.live = QLabel('等待输入')
        self.live.setObjectName('muted')
        self.live.setStyleSheet("padding: 2px 8px; font-size: 11px;")
        layout.addWidget(self.live)

        # 3. 自适应键盘与鼠标画布
        self.canvas = KeyboardCanvas(self)
        canvas_layout = QVBoxLayout(self.canvas)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(12)

        blocks = QHBoxLayout()
        blocks.setContentsMargins(0, 0, 0, 0)
        blocks.setSpacing(14)

        # 打字区与独立导航区
        for rows in (TYPING_BLOCK_ROWS, NAV_BLOCK_ROWS):
            block = QWidget()
            vertical = QVBoxLayout(block)
            vertical.setContentsMargins(0, 0, 0, 0)
            vertical.setSpacing(4)
            for row in rows:
                horizontal = QHBoxLayout()
                horizontal.setSpacing(4)
                for key, name, u_width in row:
                    if key.startswith('__'):
                        gap = QWidget()
                        horizontal.addWidget(gap)
                        self._gaps.append((gap, u_width))
                    else:
                        horizontal.addWidget(self.make_key(key, name, u_width, 1.0))
                horizontal.addStretch()
                vertical.addLayout(horizontal)
            blocks.addWidget(block)

        # 独立数字小键盘区
        numpad = QWidget()
        grid = QGridLayout(numpad)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        for row, col, rs, cs, key, name, u_w, u_h in NUMPAD_BLOCK_GRID:
            grid.addWidget(self.make_key(key, name, u_w, u_h), row, col, rs, cs)
        blocks.addWidget(numpad)
        blocks.addStretch()
        canvas_layout.addLayout(blocks)

        # 鼠标与动作控制栏 (总宽扩展至 23U，与上方键盘完美齐平对齐)
        outputs = QHBoxLayout()
        outputs.setSpacing(6)
        for key, name, u_w in MOUSE_CONTROLS + ACTION_CONTROLS:
            outputs.addWidget(self.make_key(key, name, u_w, 1.0))
        outputs.addStretch()
        canvas_layout.addLayout(outputs)

        # 主滚动区域：键盘占满页面核心区域
        area = QScrollArea()
        area.setWidget(self.canvas)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }")
        self.canvas.setStyleSheet("background: transparent;")
        layout.addWidget(area, 1)

        # 4. 可选折叠的绑定表格（默认折叠，不浪费主页面空间）
        self.bindings = BindingList(owner)
        self.bindings.hide()
        layout.addWidget(self.bindings)

        self.refresh_display()

    def toggle_bindings_list(self):
        """展开或折叠详细绑定表格"""
        if not self.bindings.isHidden():
            self.bindings.hide()
            self.btn_toggle_bindings.setText('📋 详细列表')
        else:
            self.bindings.show()
            self.btn_toggle_bindings.setText('📋 折叠列表')

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
        entries = effective_mappings(self.owner.config, self.owner.snapshot)
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
                    self.owner.mapping_change({'op': 'delete', 'trigger': trigger})
        else:
            self.edit_output(key, name)

    def mouse_settings(self):
        from PySide6.QtWidgets import QDialog, QFormLayout, QDoubleSpinBox, QDialogButtonBox
        import copy
        profile = self.owner.config['active_profile']
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
        entries = effective_mappings(self.owner.config, self.owner.snapshot)
        matches = [trigger for trigger, entry in entries.items()
                   if any(token in output_tokens(entry.get(g, {})) for g in ('short', 'long'))]
        if len(matches) == 1:
            self.owner.edit_mapping(matches[0])
        elif matches:
            from PySide6.QtWidgets import QInputDialog
            options = ['添加新的绑定'] + matches
            value, ok = QInputDialog.getItem(self, name, '选择绑定', options, 0, False)
            if ok: self.owner.edit_mapping(value if value != options[0] else '0', new=value == options[0], output=key if value == options[0] else None)
        else:
            self.owner.edit_mapping('0', new=True, output=key)

    @staticmethod
    def key_token(key):
        from .actions import parse_keys
        if key.startswith(('mouse:', 'action:')): return key
        try: return 'key:' + str(parse_keys(key)[0])
        except (ValueError, IndexError): return ''

    def refresh_display(self):
        from .mapping_engine import effective_mappings, output_tokens, trigger_label, profile_family
        config = self.owner.config
        self.scheme_combo.blockSignals(True); self.scheme_combo.clear()
        self.scheme_combo.addItems(self.store.profiles_for(self.owner.snapshot)); self.scheme_combo.setCurrentText(config['active_profile']); self.scheme_combo.blockSignals(False)
        self.mouse_toggle.blockSignals(True)
        self.mouse_toggle.setChecked(config.get('profile_options', {}).get(config['active_profile'], {}).get('right_stick_mouse', False))
        self.mouse_toggle.blockSignals(False)
        family = profile_family(config, self.owner.snapshot)

        # 结构化抽取绑定信息，严格分离短按与长按
        badges = {}
        for trigger, entry in effective_mappings(config, self.owner.snapshot).items():
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

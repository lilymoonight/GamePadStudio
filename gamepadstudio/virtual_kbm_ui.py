"""
Virtual KBM Studio UI Component (标准布局虚拟键鼠与外设捕获工作台)
1. 完整展示标准 ANSI 布局的高精细度虚拟键盘与虚拟鼠标面板
2. 目标驱动式捕获流：“点击虚拟键鼠按键 -> 触发捕获监听 -> 按下任意手柄/飞行外设 -> 即刻绑定”
3. 不限设备型号：无论是 DualSense、Xbox、Switch、还是 ECHO 飞行摇杆/原始外设，按键直通
4. 键帽实时渲染外设触发徽章、悬停光效与捕获动态脉冲
5. 支持右键快速解除绑定，集成 3D 动作与开放世界通用预设方案管理
"""

import copy
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional
from PySide6.QtCore import Qt, QSize, Signal, QUrl
from PySide6.QtGui import QColor, QFont, QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QComboBox, QLineEdit, QScrollArea,
    QFrame, QSlider, QInputDialog, QMessageBox, QSizePolicy
)

from .glass import (
    TOKENS, GlassPanel, IconButton, Toggle,
    SquircleBadge, glyph
)
from .virtual_kbm import (
    VirtualKbmEngine, get_trigger_label, get_inverted_mapping,
    NIKKI_PRESET_CONFIG, GENERAL_PRESET_CONFIG,
    NIKKI_SCHEME_NAME, GENERAL_SCHEME_NAME
)
from .hidhide import HidHideClient, HIDHIDE_RELEASE_URL


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


class KeyCap(QPushButton):
    """
    Apple 深色玻璃质感交互键帽
    支持显示字符、外设绑定发光徽章、点击激活捕获、右键快速解绑
    """
    left_clicked = Signal(str, str)   # (key_id, display_name)
    right_clicked = Signal(str, str)  # (key_id, display_name)

    def __init__(self, key_id: str, display_name: str, width: Any = 41, is_special: bool = False, parent=None):
        super().__init__(parent)
        self.key_id = key_id
        self.display_name = display_name
        self.is_special = is_special
        self.badges: List[str] = []
        self.is_capturing = False

        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)

        # 兼容固定像素宽度 (如 41, 64, 87, 98, 122, 281) 与系数 (如 1.0, 1.5, 2.0)
        if isinstance(width, (int, float)) and width > 10:
            self.fixed_w = int(width)
        else:
            w_factor = float(width)
            self.fixed_w = int(round(41 * w_factor + (w_factor - 1.0) * 5))

        self.setFixedSize(self.fixed_w, 42)
        self.update_style()

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self.right_clicked.emit(self.key_id, self.display_name)
        elif event.button() == Qt.LeftButton:
            self.left_clicked.emit(self.key_id, self.display_name)
        super().mousePressEvent(event)

    def set_mapping_state(self, badges: List[str], capturing: bool):
        self.badges = badges
        self.is_capturing = capturing
        self.update_style()

    def update_style(self):
        text_lines = [self.display_name]
        if self.is_capturing:
            text_lines.append("⏳ 捕获中")
        elif self.badges:
            # 针对 41px 等紧凑键位，避免多触发器并列导致文字换行溢出
            if self.fixed_w < 60 and len(self.badges) > 1:
                text_lines.append(f"· {self.badges[0]}⁺")
            else:
                text_lines.append("· " + " / ".join(self.badges[:2]))
        self.setText("\n".join(text_lines))

        if self.is_capturing:
            # 捕获中高能呼吸脉冲
            self.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255, 159, 10, 0.28);
                    border: 2px dashed {TOKENS['amber']};
                    border-radius: 7px;
                    color: {TOKENS['amber']};
                    font-size: 10px;
                    font-weight: 700;
                    text-align: center;
                    padding: 1px;
                }}
            """)
            self.setToolTip(f"【{self.display_name}】正在等待外设输入...\n请在手柄、飞行摇杆或任意接入设备上按下按键或推轴")
        elif self.badges:
            # 已映射外设按键 (优雅电光蓝高光徽章)
            self.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(41, 151, 255, 0.16);
                    border: 1.5px solid {TOKENS['blue']};
                    border-radius: 7px;
                    color: #ffffff;
                    font-size: 10px;
                    font-weight: 700;
                    text-align: center;
                    padding: 1px;
                }}
                QPushButton:hover {{
                    background: rgba(41, 151, 255, 0.28);
                    border-color: #64b5f6;
                }}
            """)
            self.setToolTip(f"虚拟按键：【{self.display_name}】\n已绑定外设触发：{', '.join(self.badges)}\n\n💡 点击重新捕获 · 右键快速清除绑定")
        else:
            # 默认静止状态
            bg = TOKENS['elevated'] if not self.is_special else 'rgba(255, 255, 255, 0.04)'
            self.setStyleSheet(f"""
                QPushButton {{
                    background: {bg};
                    border: 1px solid {TOKENS['border']};
                    border-radius: 7px;
                    color: {TOKENS['ink']};
                    font-size: 10px;
                    font-weight: 600;
                    text-align: center;
                    padding: 1px;
                }}
                QPushButton:hover {{
                    background: {TOKENS['overlay']};
                    border-color: {TOKENS['border_hi']};
                    color: #ffffff;
                }}
            """)
            self.setToolTip(f"虚拟按键：【{self.display_name}】\n未绑定外设触发\n\n💡 点击开始捕获手柄/外设按键")


# 标准 ANSI 87 键主打字区 (60% Typing Block) - 6 行每行精准对齐至 685px
TYPING_BLOCK_ROWS = [
    # 行 0：Esc 与 F1~F12 功能键区 (41 + 37 + 179 + 35 + 179 + 35 + 179 = 685px)
    [
        ("Esc", "Esc", 41), ("__gap__", "", 27),
        ("F1", "F1", 41), ("F2", "F2", 41), ("F3", "F3", 41), ("F4", "F4", 41), ("__gap__", "", 25),
        ("F5", "F5", 41), ("F6", "F6", 41), ("F7", "F7", 41), ("F8", "F8", 41), ("__gap__", "", 25),
        ("F9", "F9", 41), ("F10", "F10", 41), ("F11", "F11", 41), ("F12", "F12", 41),
    ],
    # 行 1：数字与符号行 (13*41 + 87 + 13*5 = 685px)
    [
        ("`", "` ~", 41), ("1", "1", 41), ("2", "2", 41), ("3", "3", 41), ("4", "4", 41),
        ("5", "5", 41), ("6", "6", 41), ("7", "7", 41), ("8", "8", 41), ("9", "9", 41),
        ("0", "0", 41), ("-", "- _", 41), ("=", "= +", 41), ("Backspace", "⌫ Back", 87),
    ],
    # 行 2：QWERTY 字母行 (64 + 12*41 + 64 + 13*5 = 685px)
    [
        ("Tab", "Tab ⇥", 64), ("Q", "Q", 41), ("W", "W", 41), ("E", "E", 41), ("R", "R", 41),
        ("T", "T", 41), ("Y", "Y", 41), ("U", "U", 41), ("I", "I", 41), ("O", "O", 41),
        ("P", "P", 41), ("[", "[ {", 41), ("]", "] }", 41), ("\\", "\\ |", 64),
    ],
    # 行 3：ASDF 基准行 (76 + 11*41 + 98 + 12*5 = 685px)
    [
        ("Caps", "Caps", 76), ("A", "A", 41), ("S", "S", 41), ("D", "D", 41), ("F", "F", 41),
        ("G", "G", 41), ("H", "H", 41), ("J", "J", 41), ("K", "K", 41), ("L", "L", 41),
        (";", "; :", 41), ("'", "' \"", 41), ("Enter", "Enter ↵", 98),
    ],
    # 行 4：ZXCV 行 (98 + 10*41 + 122 + 11*5 = 685px)
    [
        ("Shift", "⇧ Shift", 98), ("Z", "Z", 41), ("X", "X", 41), ("C", "C", 41), ("V", "V", 41),
        ("B", "B", 41), ("N", "N", 41), ("M", "M", 41), (",", ", <", 41), (".", ". >", 41),
        ("/", "/ ?", 41), ("RShift", "Shift ⇧", 122),
    ],
    # 行 5：控制修饰与空格 (53 + 52 + 53 + 281 + 53 + 52 + 53 + 53 + 7*5 = 685px)
    [
        ("Ctrl", "Ctrl", 53), ("Win", "Win ⊞", 52), ("Alt", "Alt", 53),
        ("Space", "Space (空格)", 281),
        ("RAlt", "Alt", 53), ("Fn", "Fn", 52), ("Menu", "Menu ☰", 53), ("RCtrl", "Ctrl", 53),
    ],
]

# 独立导航与方向键区 (Navigation & Arrows Block) - 3 列 6 行，精准固定宽 133px
NAV_BLOCK_ROWS = [
    # 行 0：控制功能三键
    [("PrtScn", "PrtSc", 41), ("ScrLk", "ScrLk", 41), ("Pause", "Pause", 41)],
    # 行 1：编辑导航上三键
    [("Insert", "Ins", 41), ("Home", "Home", 41), ("PgUp", "PgUp", 41)],
    # 行 2：编辑导航下三键
    [("Delete", "Del", 41), ("End", "End", 41), ("PgDn", "PgDn", 41)],
    # 行 3：功能留白占位 (高度 42px，与 ASDF 基准行完美垂直齐平)
    [("__empty__", "", 133)],
    # 行 4：上方向键居中 (空 41px + 上 41px + 空 41px)
    [("__empty__", "", 41), ("Up", "↑", 41), ("__empty__", "", 41)],
    # 行 5：下左右方向键 (左 41px + 下 41px + 右 41px)
    [("Left", "←", 41), ("Down", "↓", 41), ("Right", "→", 41)],
]

# 兼容历史引用
KEYBOARD_LAYOUT = TYPING_BLOCK_ROWS

MOUSE_CONTROLS = [
    ("mouse:left", "🖱️ 鼠标左键", 88),
    ("mouse:right", "🖱️ 鼠标右键", 88),
    ("mouse:middle", "🖱️ 鼠标中键", 80),
    ("mouse:wheel_up", "🔼 滚轮向上", 80),
    ("mouse:wheel_down", "🔽 滚轮向下", 80),
]

ACTION_CONTROLS = [
    ("action:capture", "📸 快速截屏", 110),
    ("action:replay_record", "🎬 精彩回放", 110),
    ("action:record_toggle", "🎥 录屏开关", 110),
]


class VirtualKbmPage(QWidget):
    """
    虚拟键鼠方案工作台（以标准虚拟键盘为核心交互媒介）
    1. 用户在虚拟键盘鼠标上点击任意目标按键
    2. 系统瞬间进入捕获状态，监听手柄/飞行摇杆/任意接入外设的操作
    3. 外设按压动作自动绑定为该键盘按键的硬件触发源
    """
    scheme_changed = Signal(dict)

    def __init__(self, engine: VirtualKbmEngine, store=None, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.store = store

        # 方案库初始化
        self.schemes = self._load_schemes()
        self.current_scheme_name = NIKKI_SCHEME_NAME if NIKKI_SCHEME_NAME in self.schemes else list(self.schemes.keys())[0]

        # 激活引擎
        self.engine.load_scheme(self.schemes[self.current_scheme_name])

        # 捕获状态跟踪
        self.is_capturing = False
        self.capturing_target: Optional[str] = None
        self.capturing_display_name: str = ""

        # 外设输入状态基准差分器
        self.last_buttons = set()
        self.last_rt = 0.0
        self.last_lt = 0.0
        self.last_lx = 0.0
        self.last_ly = 0.0

        # 所有可交互键帽字典 { key_id: KeyCap }
        self.keycaps: Dict[str, KeyCap] = {}

        # 硬件独占屏蔽客户端 (HidHide 内核驱动)
        self.hidhide = HidHideClient()

        self._build_ui()
        self.refresh_display()

    def _load_schemes(self) -> Dict[str, Any]:
        schemes = {
            NIKKI_SCHEME_NAME: copy.deepcopy(NIKKI_PRESET_CONFIG),
            GENERAL_SCHEME_NAME: copy.deepcopy(GENERAL_PRESET_CONFIG),
        }
        if self.store and hasattr(self.store, "data"):
            saved = self.store.data.get("virtual_kbm_schemes")
            if isinstance(saved, dict) and saved:
                for k, v in saved.items():
                    schemes[k] = v
        return schemes

    def _save_schemes(self):
        if self.store and hasattr(self.store, "data"):
            self.store.data["virtual_kbm_schemes"] = self.schemes
            self.store.save()
            try:
                from .ipc import request
                request(self.store.root, 'reload', timeout=150)
            except Exception:
                pass

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(12)

        # 1. 顶部操作工具栏 (方案切换、新建、克隆、总开关)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        toolbar.addWidget(label("当前键鼠方案：", "muted"))
        self.scheme_combo = QComboBox()
        self.scheme_combo.setMinimumWidth(210)
        for s in self.schemes.keys():
            self.scheme_combo.addItem(s)
        self.scheme_combo.setCurrentText(self.current_scheme_name)
        self.scheme_combo.currentTextChanged.connect(self.on_scheme_selected)
        toolbar.addWidget(self.scheme_combo)

        toolbar.addWidget(button("新建方案", self.create_new_scheme, icon="plus"))
        toolbar.addWidget(button("克隆方案", self.clone_scheme, icon="edit"))
        toolbar.addWidget(button("恢复预设", self.reset_to_preset, icon="refresh"))
        self.btn_del = button("删除方案", self.delete_scheme, icon="trash")
        toolbar.addWidget(self.btn_del)

        toolbar.addStretch()

        # 主控接管总开关
        self.master_toggle = Toggle("完全接管为虚拟键鼠")
        curr = self.schemes[self.current_scheme_name]
        self.master_toggle.setChecked(curr.get("enabled", True))
        self.master_toggle.toggled.connect(self.on_master_toggle)
        toolbar.addWidget(self.master_toggle)

        main_layout.addLayout(toolbar)

        # 2. 动态捕获操作提示悬浮横幅 (Capture HUD Banner)
        self.capture_banner, b_lay = card("card")
        b_lay.setContentsMargins(16, 12, 16, 12)
        b_inner = QHBoxLayout()
        self.capture_badge = SquircleBadge("keyboard", (TOKENS['blue'], TOKENS['blue']))
        b_inner.addWidget(self.capture_badge)

        t_box = QVBoxLayout()
        t_box.setSpacing(2)
        self.capture_title = label("🎯 点击下方任意虚拟键帽，即可捕获手柄/飞行摇杆操作并绑定为触发", "section")
        self.capture_title.setStyleSheet("font-weight: 700; font-size: 13px;")
        self.capture_sub = label("支持设备：DualSense / Xbox / Switch / ECHO 飞行手柄 / HOTAS 飞行摇杆 / 赛车踏板", "muted")
        t_box.addWidget(self.capture_title)
        t_box.addWidget(self.capture_sub)
        b_inner.addLayout(t_box, 1)

        self.btn_cancel_capture = button("✕ 取消捕获", self.cancel_capture)
        self.btn_cancel_capture.hide()
        b_inner.addWidget(self.btn_cancel_capture)

        self.btn_clear_current = button("🗑️ 清除该键绑定", self.clear_active_key_binding, danger=True)
        self.btn_clear_current.hide()
        b_inner.addWidget(self.btn_clear_current)

        b_lay.addLayout(b_inner)
        main_layout.addWidget(self.capture_banner)

        # 3. 滚动工作区
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        scroll_content = QWidget()
        c_layout = QVBoxLayout(scroll_content)
        c_layout.setContentsMargins(0, 0, 8, 0)
        c_layout.setSpacing(14)

        # 3a. 核心大卡片：标准 ANSI 虚拟键盘全景图
        card_keyboard, k_lay = card("card")
        k_lay.setContentsMargins(18, 16, 18, 16)
        k_lay.setSpacing(10)

        k_hdr = QHBoxLayout()
        k_hdr.addWidget(label("标准布局虚拟键盘 // VIRTUAL KEYBOARD", "eyebrow"))
        k_hdr.addStretch()
        k_hdr.addWidget(label("💡 左键点击捕获 · 右键快速解绑 · 发光徽章表示已绑定外设触发", "muted"))
        k_lay.addLayout(k_hdr)

        # 键盘双区块横向容器 (主键区 685px + 18px 间距 + 导航方向键区 133px)
        kb_body = QHBoxLayout()
        kb_body.setContentsMargins(0, 0, 0, 0)
        kb_body.setSpacing(18)
        kb_body.setAlignment(Qt.AlignLeft)

        # 左侧：60% 主键盘区 (严格 685px 宽)
        typing_block = QWidget()
        t_lay = QVBoxLayout(typing_block)
        t_lay.setContentsMargins(0, 0, 0, 0)
        t_lay.setSpacing(6)

        for row_data in TYPING_BLOCK_ROWS:
            row_box = QHBoxLayout()
            row_box.setContentsMargins(0, 0, 0, 0)
            row_box.setSpacing(5)
            row_box.setAlignment(Qt.AlignLeft)
            for item in row_data:
                kid, dname, w = item
                if kid == "__gap__":
                    sp = QWidget()
                    sp.setFixedSize(w, 42)
                    row_box.addWidget(sp)
                else:
                    kbtn = KeyCap(kid, dname, w, is_special=(len(kid) > 1 and not kid.startswith("F")))
                    kbtn.left_clicked.connect(self.on_keycap_clicked)
                    kbtn.right_clicked.connect(self.on_keycap_right_clicked)
                    self.keycaps[kid] = kbtn
                    row_box.addWidget(kbtn)
            t_lay.addLayout(row_box)

        kb_body.addWidget(typing_block)

        # 右侧：独立导航与方向键区 (严格 133px 宽，与左侧 6 行垂直 1:1 精准对齐)
        nav_block = QWidget()
        n_lay = QVBoxLayout(nav_block)
        n_lay.setContentsMargins(0, 0, 0, 0)
        n_lay.setSpacing(6)

        for row_data in NAV_BLOCK_ROWS:
            row_box = QHBoxLayout()
            row_box.setContentsMargins(0, 0, 0, 0)
            row_box.setSpacing(5)
            row_box.setAlignment(Qt.AlignLeft)
            for item in row_data:
                kid, dname, w = item
                if kid == "__empty__":
                    sp = QWidget()
                    sp.setFixedSize(w, 42)
                    row_box.addWidget(sp)
                else:
                    kbtn = KeyCap(kid, dname, w, is_special=True)
                    kbtn.left_clicked.connect(self.on_keycap_clicked)
                    kbtn.right_clicked.connect(self.on_keycap_right_clicked)
                    self.keycaps[kid] = kbtn
                    row_box.addWidget(kbtn)
            n_lay.addLayout(row_box)

        kb_body.addWidget(nav_block)
        k_lay.addLayout(kb_body)

        c_layout.addWidget(card_keyboard)

        # 3b. 底部两栏：左侧标准虚拟鼠标与动作板，右侧 300Hz 物理动力学参数
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(14)

        # 虚拟鼠标与快捷动作板
        card_mouse, m_lay = card("card")
        m_lay.setContentsMargins(18, 16, 18, 16)
        m_lay.setSpacing(10)

        m_hdr = QHBoxLayout()
        m_hdr.addWidget(label("虚拟鼠标与核心动作 // MOUSE & ACTIONS", "eyebrow"))
        m_hdr.addStretch()
        m_lay.addLayout(m_hdr)

        m_row = QHBoxLayout()
        m_row.setSpacing(6)
        for kid, dname, wf in MOUSE_CONTROLS:
            kbtn = KeyCap(kid, dname, wf, is_special=True)
            kbtn.left_clicked.connect(self.on_keycap_clicked)
            kbtn.right_clicked.connect(self.on_keycap_right_clicked)
            self.keycaps[kid] = kbtn
            m_row.addWidget(kbtn)
        m_lay.addLayout(m_row)

        a_row = QHBoxLayout()
        a_row.setSpacing(6)
        for kid, dname, wf in ACTION_CONTROLS:
            kbtn = KeyCap(kid, dname, wf, is_special=True)
            kbtn.left_clicked.connect(self.on_keycap_clicked)
            kbtn.right_clicked.connect(self.on_keycap_right_clicked)
            self.keycaps[kid] = kbtn
            a_row.addWidget(kbtn)
        m_lay.addLayout(a_row)

        bottom_row.addWidget(card_mouse, 3)

        # 右侧：300Hz 物理动力学视角微调卡片
        card_phys, p_lay = card("card")
        p_lay.setContentsMargins(18, 16, 18, 16)
        p_lay.setSpacing(10)

        p_hdr = QHBoxLayout()
        p_hdr.addWidget(SquircleBadge("wave", (TOKENS['amber'], TOKENS['amber'])))
        p_titles = QVBoxLayout()
        p_titles.setSpacing(1)
        p_titles.addWidget(label("300Hz 物理动力学视角", "section"))
        p_titles.addWidget(label("winmm 1ms · 二阶阻尼质点 · 亚像素扩散", "muted"))
        p_hdr.addLayout(p_titles, 1)
        p_lay.addLayout(p_hdr)

        sens_row = QHBoxLayout()
        sens_row.addWidget(label("右摇杆视角灵敏度：", "muted"))
        self.sens_val_label = label("28.0")
        self.sens_val_label.setStyleSheet(f"font-weight: 700; color: {TOKENS['amber']}; font-family: monospace;")
        sens_row.addWidget(self.sens_val_label)
        sens_row.addStretch()
        p_lay.addLayout(sens_row)

        self.sens_slider = QSlider(Qt.Horizontal)
        self.sens_slider.setRange(10, 60)
        curr_sens = curr.get("mouse_settings", {}).get("sensitivity", 28.0)
        self.sens_slider.setValue(int(curr_sens))
        self.sens_slider.valueChanged.connect(self.on_sens_changed)
        p_lay.addWidget(self.sens_slider)

        bottom_row.addWidget(card_phys, 2)

        c_layout.addLayout(bottom_row)

        # 3c. 硬件独占屏蔽卡片 (HidHide 内核过滤驱动 · 防游戏双重输入)
        card_cloaking, clk_lay = card("card")
        clk_lay.setContentsMargins(18, 14, 18, 14)
        clk_lay.setSpacing(10)

        clk_hdr = QHBoxLayout()
        clk_hdr.setSpacing(12)
        clk_badge = SquircleBadge("shield", (TOKENS['purple'], TOKENS['purple']))
        clk_hdr.addWidget(clk_badge)

        t_box = QVBoxLayout()
        t_box.setSpacing(2)
        t_box.addWidget(label("硬件独占隐身 // HARDWARE CLOAKING (防游戏双重输入)", "section"))
        t_box.addWidget(label("启用微软 WHQL 认证的 HidHide 驱动向系统与游戏隐藏物理手柄，仅输出纯净虚拟键鼠，彻底杜绝双重动作与 UI 狂闪", "muted"))
        clk_hdr.addLayout(t_box, 1)

        self.cloaking_status_label = label("检测中...", "muted")
        clk_hdr.addWidget(self.cloaking_status_label)

        self.btn_install_driver = button("📥 安装 HidHide 驱动 (官方 WHQL)", self.on_open_driver_installer, pill=True)
        clk_hdr.addWidget(self.btn_install_driver)

        self.cloaking_toggle = Toggle("开启独占屏蔽")
        self.cloaking_toggle.toggled.connect(self.on_cloaking_toggled)
        clk_hdr.addWidget(self.cloaking_toggle)

        clk_lay.addLayout(clk_hdr)
        c_layout.addWidget(card_cloaking)

        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll, 1)

    def refresh_display(self):
        """反向计算全量映射，并刷新所有虚拟键帽"""
        curr = self.schemes[self.current_scheme_name]
        self.master_toggle.blockSignals(True)
        self.master_toggle.setChecked(curr.get("enabled", True))
        self.master_toggle.blockSignals(False)

        # 提取当前方案的反向映射表 { key_id: [trigger1, trigger2, ...] }
        inv_map = get_inverted_mapping(curr)

        # 如果左摇杆处于默认 WASD 状态，也为 WASD 添加直观标识
        btn_map = curr.get("buttons", {})
        if "LS:Up" not in btn_map and "W" in self.keycaps:
            inv_map.setdefault("W", []).append("LS:Up")
        if "LS:Down" not in btn_map and "S" in self.keycaps:
            inv_map.setdefault("S", []).append("LS:Down")
        if "LS:Left" not in btn_map and "A" in self.keycaps:
            inv_map.setdefault("A", []).append("LS:Left")
        if "LS:Right" not in btn_map and "D" in self.keycaps:
            inv_map.setdefault("D", []).append("LS:Right")

        # 刷新所有键帽状态
        family = 'generic'
        if self.store and hasattr(self.store, 'snapshot') and self.store.snapshot:
            family = self.store.snapshot.get('family', 'generic')

        for kid, kbtn in self.keycaps.items():
            raw_triggers = inv_map.get(kid, [])
            badges = [get_trigger_label(t, family) for t in raw_triggers]
            is_cap = (self.is_capturing and kid == self.capturing_target)
            kbtn.set_mapping_state(badges, is_cap)

        # 刷新视角滑块
        ms = curr.get("mouse_settings", {})
        sens = ms.get("sensitivity", 28.0)
        self.sens_slider.blockSignals(True)
        self.sens_slider.setValue(int(sens))
        self.sens_slider.blockSignals(False)
        self.sens_val_label.setText(f"{sens:.1f}")

        # 刷新硬件独占隐身状态
        self.refresh_cloaking_status()

    def refresh_cloaking_status(self):
        """刷新 HidHide 硬件独占隐身状态"""
        installed = self.hidhide.is_driver_installed()
        saved_enabled = False
        if self.store and hasattr(self.store, 'data'):
            saved_enabled = self.store.data.get('device_cloaking_enabled', False)

        self.cloaking_toggle.blockSignals(True)
        if not installed:
            self.cloaking_status_label.setText("⚡ 驱动未安装 (可选)")
            self.cloaking_status_label.setStyleSheet(f"font-weight: 700; color: {TOKENS['amber']}; font-size: 12px;")
            self.btn_install_driver.show()
            self.cloaking_toggle.setChecked(False)
            self.cloaking_toggle.setEnabled(False)
            self.cloaking_toggle.setToolTip("需要先安装 HidHide 内核驱动后方可开启独占屏蔽")
        else:
            self.btn_install_driver.hide()
            self.cloaking_toggle.setEnabled(True)
            active = self.hidhide.is_active()
            is_on = (saved_enabled or active)
            self.cloaking_toggle.setChecked(is_on)
            if is_on:
                self.cloaking_status_label.setText("✓ 硬件已隐身 (独占接管中)")
                self.cloaking_status_label.setStyleSheet(f"font-weight: 700; color: {TOKENS['green']}; font-size: 12px;")
                self.cloaking_toggle.setToolTip("物理控制器已对系统与游戏完全隐藏，仅本程序能接收硬件信号并输出虚拟键鼠")
            else:
                self.cloaking_status_label.setText("○ 共享模式 (未隐身)")
                self.cloaking_status_label.setStyleSheet(f"font-weight: 600; color: {TOKENS['muted']}; font-size: 12px;")
                self.cloaking_toggle.setToolTip("物理控制器正常向系统广播输入，可能在同时支持手柄的游戏中产生双重输入")
        self.cloaking_toggle.blockSignals(False)

    def on_cloaking_toggled(self, checked: bool):
        """用户切换硬件独占隐身开关"""
        if not self.hidhide.is_driver_installed():
            self.cloaking_toggle.setChecked(False)
            self.on_open_driver_installer()
            return

        vendor, product = None, None
        if self.store and hasattr(self.store, 'snapshot') and self.store.snapshot:
            vendor = self.store.snapshot.get('vendor')
            product = self.store.snapshot.get('product')

        if checked:
            ok, msg = self.hidhide.cloak_controller(vendor, product)
            if ok:
                if self.store and hasattr(self.store, 'data'):
                    self.store.data['device_cloaking_enabled'] = True
                    self.store.save()
            else:
                QMessageBox.warning(self, "开启硬件独占隐身失败", f"{msg}\n\n提示：修改驱动白名单可能需要以管理员身份运行本程序。")
                self.cloaking_toggle.setChecked(False)
        else:
            self.hidhide.uncloak_controller(vendor, product)
            self.hidhide.set_active(False)
            if self.store and hasattr(self.store, 'data'):
                self.store.data['device_cloaking_enabled'] = False
                self.store.save()

        self.refresh_cloaking_status()

    def on_open_driver_installer(self):
        """弹出驱动安装说明并引导下载官方 WHQL 驱动"""
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("安装 HidHide 驱动 (官方 WHQL 认证)")
        msg_box.setText(
            "<b>HidHide 是什么？</b><br><br>"
            "HidHide 是一款经过<b>微软官方 WHQL 签名认证</b>的开源内核级过滤驱动。<br>"
            "开启后，它可以对 Windows 系统和所有游戏完全“隐藏”你的物理手柄/飞行摇杆，"
            "仅允许本工作台独占读取输入并转换为虚拟键鼠。<br><br>"
            "<b>优点：</b><br>"
            "• 彻底根治现代游戏同时支持手柄和键鼠时的【双重动作冲突】与【UI 提示狂闪】<br>"
            "• 安全合规：完全不注入游戏内存、不修改游戏代码，兼容各类主流反作弊系统。<br><br>"
            "点击“前往下载安装”将打开官方 GitHub 页面下载最新安装包（.msi），安装后即可开启。"
        )
        btn_open = msg_box.addButton("前往下载安装 (GitHub)", QMessageBox.AcceptRole)
        btn_cancel = msg_box.addButton("稍后安装", QMessageBox.RejectRole)
        msg_box.exec()
        if msg_box.clickedButton() == btn_open:
            QDesktopServices.openUrl(QUrl(HIDHIDE_RELEASE_URL))

    def on_keycap_clicked(self, key_id: str, display_name: str):
        """用户点击虚拟键盘按键 -> 进入按键捕获状态"""
        self.is_capturing = True
        self.capturing_target = key_id
        self.capturing_display_name = display_name

        # 激活捕获横幅
        self.capture_badge.set_accent(TOKENS['amber'])
        self.capture_title.setText(f"🎯 正在为虚拟按键【{display_name}】捕获外设触发操作...")
        self.capture_title.setStyleSheet(f"font-weight: 700; font-size: 13px; color: {TOKENS['amber']};")
        self.capture_sub.setText("👉 请在手柄、飞行摇杆或任意接入外设上按下按键、扳机或推动摇杆... (系统将自动完成绑定)")

        self.btn_cancel_capture.show()
        self.btn_clear_current.show()

        self.refresh_display()

    def on_keycap_right_clicked(self, key_id: str, display_name: str):
        """右键点击虚拟键帽 -> 快速清除该键绑定的所有外设触发"""
        self.clear_key_binding(key_id, display_name)

    def cancel_capture(self):
        """取消当前捕获状态"""
        self.is_capturing = False
        self.capturing_target = None
        self.capturing_display_name = ""

        self.capture_badge.set_accent(TOKENS['blue'])
        self.capture_title.setText("🎯 点击下方任意虚拟键帽，即可捕获手柄/飞行摇杆操作并绑定为触发")
        self.capture_title.setStyleSheet("font-weight: 700; font-size: 13px; color: #ffffff;")
        self.capture_sub.setText("支持设备：DualSense / Xbox / Switch / ECHO 飞行手柄 / HOTAS 飞行摇杆 / 赛车踏板")

        self.btn_cancel_capture.hide()
        self.btn_clear_current.hide()

        self.refresh_display()

    def clear_active_key_binding(self):
        """清除当前选中键帽的绑定"""
        if self.capturing_target:
            self.clear_key_binding(self.capturing_target, self.capturing_display_name)
            self.cancel_capture()

    def clear_key_binding(self, key_id: str, display_name: str = ""):
        """从当前方案中清除输出为 key_id 的所有触发"""
        curr = self.schemes[self.current_scheme_name]
        buttons = curr.get("buttons", {})
        chords = curr.get("chords", {})

        # 清除按钮
        to_del_btn = [tid for tid, act in buttons.items() if act == key_id]
        for tid in to_del_btn:
            del buttons[tid]

        # 清除组合换挡
        to_del_chord = [cid for cid, act in chords.items() if act == key_id]
        for cid in to_del_chord:
            del chords[cid]

        self.engine.load_scheme(curr)
        self._save_schemes()
        self.refresh_display()

        dname = display_name or key_id
        if hasattr(self, 'store') and hasattr(self.store, 'root'):
            self._play_feedback_sound()

    def handle_device_input(self, state: Dict[str, Any]):
        """
        接收设备轮询状态：当处于捕获模式时，自动比对差分并完成按键绑定
        """
        if not self.is_capturing or not self.capturing_target or not state:
            return

        buttons = set(state.get('buttons', []))
        axes = state.get('axes', [0.0] * 6)

        # 1. 差异检测：检测新按下按键
        new_buttons = buttons - self.last_buttons
        self.last_buttons = buttons

        captured_trigger: Optional[str] = None

        if new_buttons:
            btn = min(new_buttons)
            # 组合换挡识别：如果正按着 LB (9)，且按下面部四键
            if 9 in buttons and btn in (0, 1, 2, 3):
                captured_trigger = f"LB + {btn}"
            else:
                captured_trigger = str(btn)

        # 2. 差异检测：扳机触发 (RT / LT 下压)
        if not captured_trigger:
            rt_val = axes[5] if len(axes) > 5 else 0.0
            lt_val = axes[4] if len(axes) > 4 else 0.0

            if rt_val > 0.45 and self.last_rt <= 0.2:
                captured_trigger = "RT"
            elif lt_val > 0.45 and self.last_lt <= 0.2:
                captured_trigger = "LT"

            self.last_rt = rt_val
            self.last_lt = lt_val

        # 3. 差异检测：左摇杆推向特定方向
        if not captured_trigger:
            lx = axes[0] if len(axes) > 0 else 0.0
            ly = axes[1] if len(axes) > 1 else 0.0

            if ly < -0.70 and self.last_ly >= -0.30: captured_trigger = "LS:Up"
            elif ly > 0.70 and self.last_ly <= 0.30: captured_trigger = "LS:Down"
            elif lx < -0.70 and self.last_lx >= -0.30: captured_trigger = "LS:Left"
            elif lx > 0.70 and self.last_lx <= 0.30: captured_trigger = "LS:Right"

            self.last_lx = lx
            self.last_ly = ly

        # 4. 成功捕获到外设输入 -> 执行绑定
        if captured_trigger:
            self._apply_captured_binding(captured_trigger)

    def _apply_captured_binding(self, trigger_id: str):
        curr = self.schemes[self.current_scheme_name]
        target_key = self.capturing_target
        target_name = self.capturing_display_name

        family = 'generic'
        if self.store and hasattr(self.store, 'snapshot') and self.store.snapshot:
            family = self.store.snapshot.get('family', 'generic')

        trigger_name = get_trigger_label(trigger_id, family)

        if " + " in trigger_id:
            curr.setdefault("chords", {})[trigger_id] = target_key
        else:
            curr.setdefault("buttons", {})[trigger_id] = target_key

        self.engine.load_scheme(curr)
        self._save_schemes()
        self._play_feedback_sound()

        # 退出捕获状态并展示成功横幅
        self.cancel_capture()
        self.capture_title.setText(f"✅ 成功绑定：外设【{trigger_name}】 -> 虚拟按键【{target_name}】")
        self.capture_title.setStyleSheet(f"font-weight: 700; font-size: 13px; color: {TOKENS['green']};")
        self.capture_sub.setText("映射已即时生效并在后台驻留。点击其他按键可继续配置。")

    def _play_feedback_sound(self):
        """播放高质感机械按键反馈声音"""
        try:
            import winsound
            wav_path = Path(__file__).resolve().parent / 'assets' / 'shutter.wav'
            if wav_path.is_file():
                winsound.PlaySound(str(wav_path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            pass

    def on_scheme_selected(self, name: str):
        if name in self.schemes:
            self.current_scheme_name = name
            self.engine.load_scheme(self.schemes[name])
            self.refresh_display()
            self.scheme_changed.emit(self.schemes[name])

    def on_master_toggle(self, enabled: bool):
        self.schemes[self.current_scheme_name]["enabled"] = enabled
        self.engine.scheme["enabled"] = enabled
        self._save_schemes()

    def on_sens_changed(self, val: int):
        fval = float(val)
        self.sens_val_label.setText(f"{fval:.1f}")
        self.schemes[self.current_scheme_name]["mouse_settings"]["sensitivity"] = fval
        self.engine.mouse_thread.configure(self.schemes[self.current_scheme_name]["mouse_settings"])
        self._save_schemes()

    def create_new_scheme(self):
        name, ok = QInputDialog.getText(self, "新建键鼠方案", "请输入新方案名称：", QLineEdit.Normal, "自定义方案")
        if ok and name.strip():
            name = name.strip()
            if name in self.schemes:
                QMessageBox.warning(self, "提示", "同名方案已存在！")
                return
            new_s = copy.deepcopy(GENERAL_PRESET_CONFIG)
            new_s["name"] = name
            new_s["desc"] = "用户自定义虚拟键鼠方案"
            new_s["enabled"] = True
            self.schemes[name] = new_s
            self._save_schemes()

            self.scheme_combo.addItem(name)
            self.scheme_combo.setCurrentText(name)

    def clone_scheme(self):
        curr = self.schemes[self.current_scheme_name]
        default_name = f"{curr['name']} - 副本"
        name, ok = QInputDialog.getText(self, "克隆方案", "请输入克隆后的方案名称：", QLineEdit.Normal, default_name)
        if ok and name.strip():
            name = name.strip()
            if name in self.schemes:
                QMessageBox.warning(self, "提示", "同名方案已存在！")
                return
            new_s = copy.deepcopy(curr)
            new_s["name"] = name
            self.schemes[name] = new_s
            self._save_schemes()

            self.scheme_combo.addItem(name)
            self.scheme_combo.setCurrentText(name)

    def reset_to_preset(self):
        if self.current_scheme_name == NIKKI_SCHEME_NAME:
            self.schemes[NIKKI_SCHEME_NAME] = copy.deepcopy(NIKKI_PRESET_CONFIG)
        elif self.current_scheme_name == GENERAL_SCHEME_NAME:
            self.schemes[GENERAL_SCHEME_NAME] = copy.deepcopy(GENERAL_PRESET_CONFIG)
        else:
            QMessageBox.information(self, "提示", "仅内置预设方案支持一键恢复原厂设定。")
            return

        self.engine.load_scheme(self.schemes[self.current_scheme_name])
        self._save_schemes()
        self.refresh_display()

    def delete_scheme(self):
        if len(self.schemes) <= 1:
            QMessageBox.warning(self, "提示", "至少保留一个方案！")
            return
        if self.current_scheme_name in [NIKKI_SCHEME_NAME, GENERAL_SCHEME_NAME]:
            QMessageBox.warning(self, "提示", "内置预设方案不允许删除。")
            return

        ret = QMessageBox.question(self, "删除确认", f"确定要永久删除方案 [{self.current_scheme_name}] 吗？")
        if ret == QMessageBox.Yes:
            del self.schemes[self.current_scheme_name]
            self._save_schemes()
            self.scheme_combo.removeItem(self.scheme_combo.currentIndex())

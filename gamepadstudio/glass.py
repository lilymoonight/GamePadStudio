"""Shared visual system for GamePad Studio's desktop workspace.

Warm graphite surfaces, restrained orange actions, and clear control states.
"""
import ctypes
from ctypes import wintypes
from functools import lru_cache
from pathlib import Path
import sys

from PySide6.QtCore import QByteArray, QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QApplication, QCheckBox, QFrame, QLabel,
                               QMainWindow, QPushButton, QWidget,
                               QVBoxLayout, QHBoxLayout, QSizePolicy)

ASSETS = Path(__file__).resolve().parent / 'assets'
CHEVRON_DOWN_PATH = (ASSETS / 'chevron_down.svg').as_posix()

# ─── Teenage Engineering & Dieter Rams Design Tokens ────────────────
TOKENS = {
    # Warm dark graphite & matte metal surfaces (obsidian titanium chassis)
    'void':         '#121110',     # matte industrial chassis background
    'base':         '#171614',     # sub-panel level
    'surface_lo':   '#171614',     # low-contrast sub-surface level
    'surface':      '#1e1c1a',     # milled graphite module panel
    'elevated':     '#272421',     # tactile button & input blocks
    'overlay':      '#34302c',     # active / hovered control surface
    'border':       '#2f2b27',     # crisp 1px milled seam line
    'border_hi':    '#46413a',     # chamfered highlight border
    'border_subtle':'#211f1c',     # engraved divider groove

    # High-contrast technical typography
    'ink':          '#f7f4ed',     # chalk white crisp technical text
    'ink_2':        '#c8c2b4',     # laser-etched secondary readout
    'ink_3':        '#8e877a',     # dial scale & ruler markings
    'ink_dim':      '#5c554b',     # subtle placeholder / inactive
    'muted':        '#8e877a',     # secondary muted text alias

    # Teenage Engineering Iconic Accents
    'accent':       '#ff5722',     # TE Punchy Safety Orange
    'accent_hi':    '#ff7043',     # orange highlight
    'accent_lo':    '#e64a19',     # deep mechanical orange
    'accent_bg':    'rgba(255, 87, 34, 0.14)',
    'border_acc':   'rgba(255, 87, 34, 0.65)',
    'chalk':        '#f7f4ed',     # mechanical chalk white

    # Semantic hardware indicators
    'green':        '#10b981',     # status LED active emerald green
    'green_bg':     'rgba(16, 185, 129, 0.14)',
    'orange':       '#ff5722',     # rotary knob & primary action orange
    'amber':        '#f59e0b',     # alert / warning amber
    'red':          '#ef4444',     # emergency / disconnect red
    'cyan':         '#06b6d4',     # secondary oscilloscope cyan
    'purple':       '#8b5cf6',     # trigger / auxiliary purple
    'blue':         '#3b82f6',     # PlayStation cross blue
    'rose':         '#f43f5e',     # favorite pin rose

    # Soft squircle industrial radii (tactile, friendly, milled corners)
    'r_xs':         6,             # tight squircle for tags and badges
    'r_sm':         8,             # soft squircle for compact controls (buttons, inputs, combos)
    'r_md':         10,            # soft squircle for buttons, tiles, badges
    'r_lg':         12,            # workspace panels
    'r_pill':       18,            # tactile pill
}

INK = TOKENS['ink_3']
ACCENT = TOKENS['accent']

UI_FONT_STACK = ('"Helvetica Neue", "PingFang SC"' if sys.platform == 'darwin'
                 else '"Microsoft YaHei UI", "Segoe UI", "Microsoft YaHei", sans-serif')
MONO_FONT_STACK = ('"Menlo"' if sys.platform == 'darwin'
                   else '"Cascadia Code", "Cascadia Mono", Consolas, monospace')

# ─── Global Stylesheet ──────────────────────────────────────────────
# fmt: off
STYLE = f'''
/* ── Base ─────────────────────────────────────────────── */
QWidget {{
    color: {TOKENS['ink']};
    font-family: {UI_FONT_STACK};
    font-size: 13px;
    background: transparent;
}}
QMainWindow {{ background: transparent; }}
QDialog, QMessageBox {{
    background: {TOKENS['surface']};
    color: {TOKENS['ink']};
    border: 1px solid {TOKENS['border_hi']};
    border-radius: {TOKENS['r_lg']}px;
}}
QDialog QLabel, QMessageBox QLabel {{ color: {TOKENS['ink']}; }}

/* ── Typography (High Readability) ────────────────────── */
QLabel {{ background: transparent; border: none; }}
QLabel#muted {{ color: {TOKENS['ink_2']}; font-size: 12px; }}
QLabel#eyebrow {{
    color: {TOKENS['accent']};
    font-size: 11px;
    letter-spacing: 1.5px;
    font-weight: 700;
    text-transform: uppercase;
}}
QLabel#heading {{
    font-size: 22px;
    font-weight: 700;
    color: {TOKENS['ink']};
    letter-spacing: -0.3px;
}}
QLabel#section {{
    font-size: 14px;
    font-weight: 700;
    color: {TOKENS['ink']};
    letter-spacing: -0.2px;
}}
QLabel#productTitle {{
    font-size: 24px;
    font-weight: 700;
    color: {TOKENS['ink']};
    letter-spacing: -0.4px;
}}
QLabel#caption {{
    color: {TOKENS['ink_3']};
    font-size: 11px;
}}
QLabel#metric {{
    color: {TOKENS['ink']};
    font-family: {MONO_FONT_STACK};
    font-size: 12px;
    font-weight: 600;
}}
QLabel#brandTitle {{ color: {TOKENS['ink']}; font-size: 15px; font-weight: 700; }}
QLabel#brandCaption {{ color: {TOKENS['ink_3']}; font-size: 11px; }}
QLabel#navSection {{ color: {TOKENS['ink_3']}; font-size: 11px; font-weight: 600; }}
QLabel#pageTitle {{ color: {TOKENS['ink']}; font-size: 25px; font-weight: 700; }}
QLabel#pageSubtitle {{ color: {TOKENS['ink_2']}; font-size: 12px; }}
QLabel#footerStatus {{ color: {TOKENS['ink_3']}; font-size: 11px; }}

/* ── Buttons (GamepadTester Pill & Card Style) ────────── */
QPushButton {{
    color: {TOKENS['ink']};
    background: {TOKENS['elevated']};
    border: 1px solid {TOKENS['border_hi']};
    padding: 7px 16px;
    border-radius: {TOKENS['r_sm']}px;
    font-weight: 600;
    font-size: 13px;
    min-height: 20px;
}}
QPushButton:hover {{
    background: {TOKENS['overlay']};
    border-color: {TOKENS['ink_dim']};
}}
QPushButton:pressed {{ background: {TOKENS['base']}; border-color: {TOKENS['accent']}; }}
QPushButton:disabled {{
    color: {TOKENS['ink_dim']};
    background: rgba(30, 28, 26, 0.4);
    border-color: {TOKENS['border']};
}}
QPushButton:focus {{ border-color: {TOKENS['accent']}; }}

QPushButton#primary {{
    background: {TOKENS['accent']};
    color: #ffffff;
    border: 1px solid {TOKENS['accent']};
    border-radius: {TOKENS['r_sm']}px;
    font-weight: 700;
    padding: 8px 18px;
}}
QPushButton#primary:hover {{
    background: {TOKENS['accent_hi']};
    border-color: {TOKENS['accent_hi']};
}}
QPushButton#primary:pressed {{ background: #e64a19; }}
QPushButton#primary:focus {{ border-color: {TOKENS['chalk']}; }}
QPushButton#primary:disabled {{
    background: {TOKENS['elevated']}; border-color: {TOKENS['border']}; color: {TOKENS['ink_dim']};
}}

QPushButton#pill {{
    border-radius: {TOKENS['r_sm']}px;
    padding: 6px 16px;
    background: {TOKENS['elevated']};
    border: 1px solid {TOKENS['border_hi']};
    color: {TOKENS['ink']};
    font-weight: 600;
    font-size: 12px;
}}
QPushButton#pill:hover {{
    background: {TOKENS['overlay']};
    border-color: {TOKENS['accent']};
}}

QPushButton#danger {{
    border-radius: {TOKENS['r_sm']}px;
    padding: 6px 16px;
    background: rgba(239, 68, 68, 0.12);
    border: 1px solid rgba(239, 68, 68, 0.35);
    color: {TOKENS['red']};
    font-weight: 600;
    font-size: 12px;
}}
QPushButton#danger:hover {{
    background: rgba(239, 68, 68, 0.22);
    border-color: {TOKENS['red']};
    color: #ffffff;
}}
QPushButton#danger:pressed {{
    background: {TOKENS['red']};
    color: #ffffff;
}}

QPushButton#icon {{
    padding: 0;
    border-radius: {TOKENS['r_sm']}px;
    background: transparent;
    border: 1px solid transparent;
}}
QPushButton#icon:hover {{
    background: {TOKENS['elevated']};
    border-color: {TOKENS['border_hi']};
}}
QPushButton#icon:checked {{
    background: {TOKENS['accent_bg']};
    border: 1px solid {TOKENS['border_acc']};
}}

QPushButton#icon_danger {{
    padding: 0;
    border-radius: {TOKENS['r_sm']}px;
    background: transparent;
    border: 1px solid transparent;
}}
QPushButton#icon_danger:hover {{
    background: rgba(239, 68, 68, 0.18);
    border-color: rgba(239, 68, 68, 0.50);
}}

QPushButton#mappingTile {{
    padding: 0;
    min-height: 46px;
    max-height: 46px;
    text-align: left;
    border-radius: {TOKENS['r_sm']}px;
    background: {TOKENS['surface']};
    border: 1px solid {TOKENS['border']};
}}
QPushButton#mappingTile:checked {{
    background: rgba(255, 87, 34, 0.16);
    border: 1px solid {TOKENS['accent']};
}}
QPushButton#mappingTile:hover {{
    border-color: {TOKENS['border_hi']};
    background: {TOKENS['elevated']};
}}
QPushButton#mappingTile:checked:hover {{
    background: rgba(255, 87, 34, 0.20);
    border-color: {TOKENS['accent']};
}}

QPushButton#filter {{
    padding: 5px 12px;
    background: transparent;
    border: 1px solid transparent;
    border-radius: {TOKENS['r_sm']}px;
    font-size: 12px;
    font-weight: 600;
    color: {TOKENS['ink_3']};
}}
QPushButton#filter:hover {{
    color: {TOKENS['ink']};
    background: {TOKENS['elevated']};
}}
QPushButton#filter:checked {{
    background: {TOKENS['elevated']};
    color: {TOKENS['ink']};
    border: 1px solid {TOKENS['border_hi']};
}}

QPushButton#brand {{
    background: transparent;
    border: 1px solid transparent;
    padding: 0;
    border-radius: {TOKENS['r_md']}px;
}}
QPushButton#brand:hover {{
    background: {TOKENS['elevated']};
    border-color: {TOKENS['border_hi']};
}}

QPushButton#nav {{
    background: transparent;
    border: 1px solid transparent;
    padding: 0;
    border-radius: {TOKENS['r_md']}px;
}}
QPushButton#nav:hover {{
    background: {TOKENS['elevated']};
    border-color: {TOKENS['border_hi']};
}}
QPushButton#nav:checked {{
    background: rgba(255, 87, 34, 0.18);
    border: 1px solid rgba(255, 87, 34, 0.65);
}}
QPushButton#sidebarNav {{
    background: transparent;
    color: {TOKENS['ink_2']};
    border: 1px solid transparent;
    border-radius: {TOKENS['r_sm']}px;
    text-align: left;
    padding: 8px 12px;
    min-height: 26px;
    max-height: 26px;
    font-size: 13px;
    font-weight: 500;
}}
QPushButton#sidebarNav:hover {{
    color: {TOKENS['ink']};
    background: {TOKENS['elevated']};
    border-color: {TOKENS['border']};
}}
QPushButton#sidebarNav:checked {{
    color: {TOKENS['ink']};
    font-weight: 600;
    background: {TOKENS['accent_bg']};
    border-color: rgba(255, 87, 34, 0.35);
}}
QPushButton#sidebarNav:checked:hover {{
    background: rgba(255, 87, 34, 0.20);
    border-color: {TOKENS['border_acc']};
}}
QPushButton#sidebarNav:focus {{ border-color: {TOKENS['accent']}; }}
QPushButton#sidebarNav[compact="true"] {{ padding: 0; min-height: 42px; max-height: 42px; text-align: center; }}

QPushButton#window {{
    border: none;
    border-radius: {TOKENS['r_sm']}px;
    background: transparent;
    padding: 0;
    color: {TOKENS['ink_3']};
}}
QPushButton#window:hover {{ background: {TOKENS['elevated']}; color: {TOKENS['ink']}; }}
QPushButton#close {{
    border: none;
    border-radius: {TOKENS['r_sm']}px;
    background: transparent;
    padding: 0;
    color: {TOKENS['ink_3']};
}}
QPushButton#close:hover {{ background: {TOKENS['red']}; color: white; }}

QPushButton#chip {{
    padding: 6px 14px;
    border-radius: {TOKENS['r_sm']}px;
    background: {TOKENS['elevated']};
    border: 1px solid {TOKENS['border_hi']};
    color: {TOKENS['ink']};
    font-weight: 500;
}}
QPushButton#chip:hover {{ border-color: {TOKENS['accent']}; background: {TOKENS['overlay']}; }}

QPushButton#testAction {{
    background: {TOKENS['elevated']};
    border: 1px solid {TOKENS['border_hi']};
    padding: 7px 14px;
    font-size: 12px;
    font-weight: 600;
    border-radius: {TOKENS['r_sm']}px;
    color: {TOKENS['ink_2']};
}}
QPushButton#testAction:hover {{
    background: {TOKENS['accent']};
    border-color: {TOKENS['accent']};
    color: #ffffff;
}}
QPushButton[iconOnly="true"], QPushButton#primary[iconOnly="true"] {{
    min-height: 0; min-width: 0; padding: 0;
}}
QPushButton#icon:focus, QPushButton#icon_danger:focus,
QPushButton#nav:focus, QPushButton#brand:focus,
QPushButton#window:focus, QPushButton#close:focus {{
    border: 1px solid {TOKENS['accent']};
}}

/* ── Inputs & Dropdowns ────────────────────────────────── */
QLineEdit, QKeySequenceEdit {{
    background: {TOKENS['elevated']};
    border: 1px solid {TOKENS['border_hi']};
    border-radius: {TOKENS['r_sm']}px;
    padding: 6px 12px;
    min-height: 22px;
    color: {TOKENS['ink']};
    selection-background-color: {TOKENS['accent']};
    selection-color: #ffffff;
    font-weight: 500;
}}
QLineEdit:hover, QKeySequenceEdit:hover {{ border-color: {TOKENS['ink_dim']}; }}
QLineEdit:focus, QKeySequenceEdit:focus {{ border-color: {TOKENS['accent']}; }}

QComboBox {{
    background: {TOKENS['elevated']};
    border: 1px solid {TOKENS['border_hi']};
    border-radius: {TOKENS['r_sm']}px;
    padding: 0px 28px 0px 12px;
    min-height: 36px;
    color: {TOKENS['ink']};
    font-weight: 600;
    font-size: 12px;
}}
QComboBox:hover {{
    border-color: {TOKENS['ink_dim']};
    background: {TOKENS['overlay']};
}}
QComboBox:focus {{ border-color: {TOKENS['accent']}; }}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 28px;
    border-left: none;
}}
QComboBox::down-arrow {{
    image: url({CHEVRON_DOWN_PATH});
    width: 8px;
    height: 8px;
}}
QComboBox QAbstractItemView {{
    background: {TOKENS['surface']};
    border: 1px solid {TOKENS['border_hi']};
    selection-background-color: {TOKENS['accent']};
    selection-color: #ffffff;
    padding: 4px;
    border-radius: {TOKENS['r_sm']}px;
    color: {TOKENS['ink']};
    outline: none;
}}
QComboBox QAbstractItemView::item {{
    min-height: 28px;
    padding: 4px 8px;
    border-radius: 4px;
}}
QComboBox QAbstractItemView::item:hover {{
    background: {TOKENS['overlay']};
}}
QComboBox QAbstractItemView::item:selected {{
    background: {TOKENS['accent']};
    color: #ffffff;
}}
QLineEdit:disabled, QKeySequenceEdit:disabled, QComboBox:disabled {{
    color: {TOKENS['ink_dim']}; background: {TOKENS['surface']}; border-color: {TOKENS['border']};
}}
QComboBox QAbstractItemView QScrollBar:vertical {{
    background: transparent;
    width: 6px;
    border: none;
    margin: 2px 0;
}}
QComboBox QAbstractItemView QScrollBar::handle:vertical {{
    background: {TOKENS['border_hi']};
    border-radius: 3px;
    min-height: 20px;
}}

/* ── Scrollbars & Sliders ─────────────────────────────── */
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{
    background: transparent;
    width: 6px;
    border: none;
    margin: 4px 0;
}}
QScrollBar::handle:vertical {{
    background: {TOKENS['border_hi']};
    border-radius: 3px;
    min-height: 36px;
}}
QScrollBar::handle:vertical:hover {{ background: {TOKENS['ink_dim']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

QProgressBar {{
    border: none;
    border-radius: 4px;
    background: {TOKENS['elevated']};
    height: 6px;
    color: transparent;
}}
QProgressBar::chunk {{ background: {TOKENS['accent']}; border-radius: 4px; }}

QSlider::groove:horizontal {{
    height: 6px;
    background: {TOKENS['elevated']};
    border: 1px solid {TOKENS['border']};
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background: {TOKENS['accent']};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    width: 18px;
    margin: -6px 0;
    background: #ffffff;
    border: 1.5px solid {TOKENS['border_hi']};
    border-radius: 9px;
}}

/* ── Checkboxes ───────────────────────────────────────── */
QCheckBox {{ spacing: 10px; padding: 6px 0; color: {TOKENS['ink']}; font-weight: 500; }}
QCheckBox::indicator {{
    width: 36px;
    height: 22px;
    border: 1px solid {TOKENS['border_hi']};
    border-radius: 11px;
    background: {TOKENS['elevated']};
}}
QCheckBox::indicator:checked {{
    background: {TOKENS['green']};
    border-color: {TOKENS['green']};
}}

/* ── ToolTips & Menus ─────────────────────────────────── */
QToolTip {{
    background: {TOKENS['surface']};
    color: {TOKENS['ink']};
    border: 1px solid {TOKENS['border_hi']};
    border-radius: {TOKENS['r_sm']}px;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: 500;
}}
QMenu {{
    background: {TOKENS['surface']};
    border: 1px solid {TOKENS['border_hi']};
    border-radius: {TOKENS['r_md']}px;
    padding: 6px;
    color: {TOKENS['ink']};
}}
QMenu::item {{ padding: 7px 20px; border-radius: {TOKENS['r_sm']}px; }}
QMenu::item:selected {{ background: {TOKENS['accent']}; color: #ffffff; }}

/* ── Container Panels & Rails ─────────────────────────── */
QWidget#navRail {{
    background: {TOKENS['base']};
    border: 1px solid {TOKENS['border']};
    border-radius: {TOKENS['r_lg']}px;
}}
QWidget#statusCapsule {{
    background: {TOKENS['surface']};
    border: 1px solid {TOKENS['border_hi']};
    border-radius: {TOKENS['r_pill']}px;
}}
QListWidget {{
    background: {TOKENS['surface']};
    border: 1px solid {TOKENS['border']};
    border-radius: {TOKENS['r_md']}px;
    padding: 4px;
    color: {TOKENS['ink']};
    outline: none;
}}
QListWidget::item {{
    padding: 8px 12px;
    border-radius: {TOKENS['r_sm']}px;
    margin: 2px 0;
    border: 1px solid transparent;
}}
QListWidget::item:hover {{
    background: {TOKENS['elevated']};
    border-color: {TOKENS['border']};
}}
QListWidget::item:selected {{
    background: rgba(255, 87, 34, 0.16);
    border-color: {TOKENS['accent']};
    color: #ffffff;
}}
QListWidget::item:selected:hover {{ background: rgba(255, 87, 34, 0.20); }}
QListWidget:focus {{ border-color: {TOKENS['border_acc']}; }}
'''
# fmt: on


# ─── Helpers ─────────────────────────────────────────────────────────
def token(name):
    return TOKENS[name]


def token_color(name, alpha=None):
    c = QColor(TOKENS[name])
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def tag_style(color_hex, bg_alpha=0.15, border_alpha=0.35):
    """Badge pill styling with GamepadTester-style colored tag."""
    c = QColor(color_hex)
    return (
        f'background: rgba({c.red()},{c.green()},{c.blue()},{bg_alpha});'
        f'border: 1px solid rgba({c.red()},{c.green()},{c.blue()},{border_alpha});'
        f'border-radius: {TOKENS["r_sm"]}px; padding: 3px 10px; font-size: 11px;'
        f'color: {color_hex}; font-weight: 600;'
    )


# ─── SVG Glyphs (Lucide-grade) ──────────────────────────────────────
PATHS = {
    'controller':'<rect x="2" y="6" width="20" height="12" rx="4"/><path d="M6 12h4m-2-2v4"/><circle cx="16" cy="11" r="1"/><circle cx="18" cy="13" r="1"/>',
    'grid':'<rect x="3" y="3" width="7" height="7" rx="2.5"/><rect x="14" y="3" width="7" height="7" rx="2.5"/><rect x="3" y="14" width="7" height="7" rx="2.5"/><rect x="14" y="14" width="7" height="7" rx="2.5"/>',
    'mapping':'<circle cx="5" cy="5" r="2.5"/><circle cx="19" cy="5" r="2.5"/><circle cx="5" cy="19" r="2.5"/><circle cx="19" cy="19" r="2.5"/><path d="M8 5h8M8 19h8M5 8v8m14-8v8"/>',
    'photos':'<rect x="3" y="4" width="18" height="16" rx="4"/><circle cx="8" cy="9" r="1.5"/><path d="m4 17 6-6 5 5 3-3 3 3"/>',
    'wave':'<path d="M2 12h4l3-7 4 14 3-10 3 3h3"/>',
    'settings':'<path d="M3 5h4m5 0h9M3 12h10m5 0h3M3 19h4m5 0h9"/><circle cx="9.5" cy="5" r="2.5"/><circle cx="15.5" cy="12" r="2.5"/><circle cx="9.5" cy="19" r="2.5"/>',
    'camera':'<path d="m8 6 2-3h4l2 3h3a2 2 0 0 1 2 2v11H3V8a2 2 0 0 1 2-2Z"/><circle cx="12" cy="12" r="3.5"/>',
    'heart':'<path d="M12 20 3.5 12a5 5 0 0 1 8.5-7 5 5 0 0 1 8.5 7Z"/>',
    'connected':'<circle cx="12" cy="12" r="8"/><path d="m8 12 3 3 5-6"/>',
    'disconnected':'<circle cx="12" cy="12" r="8"/><path d="M8 12h8"/>',
    'battery':'<rect x="3" y="7" width="16" height="10" rx="3.5"/><path d="M22 10v4M6 10v4m4-4v4m4-4v4"/>',
    'battery_low':'<rect x="3" y="7" width="16" height="10" rx="3.5"/><path d="M22 10v4M6 10v4"/>',
    'battery_mid':'<rect x="3" y="7" width="16" height="10" rx="3.5"/><path d="M22 10v4M6 10v4m4-4v4"/>',
    'bolt':'<path d="m13 2-8 12h6l-1 8 9-13h-7Z"/>',
    'pause':'<path d="M8 5v14m8-14v14"/>',
    'play':'<path d="m7 4 13 8-13 8Z"/>',
    'power':'<path d="M12 2v10m-5-7a9 9 0 1 0 10 0"/>',
    'refresh':'<path d="M20 7A9 9 0 1 0 21 14M20 2v6h-6"/>',
    'arrow':'<path d="M4 12h15m-6-6 6 6-6 6"/>',
    'external':'<path d="M14 3h7v7m0-7L10 14M10 4H4v16h16v-6"/>',
    'info':'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-11v1"/>',
    'help':'<circle cx="12" cy="12" r="9"/><path d="M9 8a3 3 0 0 1 6 0c0 3-3 2-3 5m0 4v.1"/>',
    'edit':'<path d="m15 4 5 5M4 20l1-6L17 2l5 5L10 19Z"/>',
    'folder':'<path d="M3 19V5h7l2 3h9v11Z"/>',
    'trash':'<path d="M3 6h18m-2 0v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6m3 0V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2m-6 5v6m4-6v6"/>',
    'plus':'<path d="M12 4v16M4 12h16"/>',
    'search':'<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
    'minimize':'<path d="M6 12h12"/>',
    'maximize':'<rect x="6" y="6" width="12" height="12" rx="3.5"/>',
    'restore':'<path d="M9 7V4h11v11h-3"/><rect x="4" y="9" width="11" height="11" rx="2"/>',
    'close':'<path d="m6 6 12 12M18 6 6 18"/>',
    'check':'<path d="m5 12 5 5L20 6"/>',
    'usb':'<path d="M12 21V3m-3 3 3-3 3 3M12 15l-6-4V8m6 9 6-4V8"/><circle cx="6" cy="7" r="1"/><rect x="17" y="5" width="2" height="3"/>',
    'touch':'<rect x="3" y="5" width="18" height="14" rx="4"/><path d="M8 13h8m-4-4v8"/>',
    'keyboard':'<rect x="2" y="4" width="20" height="16" rx="3"/><path d="M6 8h.01M10 8h.01M14 8h.01M18 8h.01M6 12h.01M10 12h.01M14 12h.01M18 12h.01M7 16h10"/>',
    'mouse':'<rect x="6" y="3" width="12" height="18" rx="6"/><path d="M12 7v4"/>',
    'shield':'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8 12 3 3 5-6"/>',
    'circle':'<circle cx="12" cy="12" r="8"/><path d="M12 2v4m0 12v4M2 12h4m12 0h4"/>',
    'chevron_right':'<path d="m9 18 6-6-6-6"/>',
    'timer':'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>',
    'lightbulb':'<path d="M9 18h6m-5 3h4m-7-9a6 6 0 1 1 12 0c0 2-1 3-2 4H10c-1-1-2-2-2-4Z"/>',
    'cpu':'<rect x="5" y="5" width="14" height="14" rx="3.5"/><path d="M9 9h6v6H9ZM9 1v4m6-4v4M9 19v4m6-4v4M1 9h4m-4 6h4m14-6h4m-4 6h4"/>',
    'autostart':'<path d="M12 2v6m0 0a8 8 0 1 1-6 2.5"/>',
    'sliders':'<path d="M4 21v-7m0-4V3m8 21v-9m0-4V3m8 21v-5m0-4V3M1 14h6m2-7h6m2 8h6"/>',
    'sparkle':'<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z"/>',
    'touchpad':'<rect x="3" y="6" width="18" height="12" rx="4"/><path d="M12 6v5m-4 7h8"/>',
    'monitor':'<rect x="2" y="3" width="20" height="14" rx="3.5"/><path d="M8 21h8m-4-4v4"/>',
    'globe':'<circle cx="12" cy="12" r="9"/><path d="M3.6 9h16.8M3.6 15h16.8M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
}


@lru_cache(maxsize=180)
def glyph(name, color=INK):
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        f'<g fill="none" stroke="{color}" stroke-width="1.8" '
        f'stroke-linecap="round" stroke-linejoin="round">'
        f'{PATHS.get(name, PATHS["info"])}</g></svg>'
    )
    pix = QPixmap(96, 96)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    return QIcon(pix)


def app_icon():
    return QIcon(str(ASSETS / 'studio.ico'))


class LedSwatch(QPushButton):
    """Retina-crisp vector LED color swatch button with hardware tactile ring."""
    def __init__(self, color, callback=None, parent=None):
        super().__init__(parent)
        self.color = color
        self.selected = False
        self.setFixedSize(26, 26)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName('ledSwatch')
        self.setProperty('iconOnly', True)
        self.setStyleSheet('QPushButton#ledSwatch { background: transparent; border: none; padding: 0; margin: 0; }')
        if callback:
            self.clicked.connect(callback)

    def set_selected(self, selected: bool):
        if self.selected != selected:
            self.selected = selected
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = self.rect()
        w, h = rect.width(), rect.height()
        cx, cy = w / 2.0, h / 2.0

        opacity = 1.0 if self.isEnabled() else 0.42
        painter.setOpacity(opacity)

        radius = 10.0
        c = QColor(self.color)
        painter.setBrush(c)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPointF(cx, cy), radius, radius)

        if self.selected:
            pen = QPen(QColor('#ffffff'), 2.0)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QPointF(cx, cy), radius + 1.5, radius + 1.5)
        else:
            pen = QPen(QColor(255, 255, 255, 50), 1.0)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QPointF(cx, cy), radius, radius)

        painter.end()


# ─── GamepadTester Components ────────────────────────────────────────

class IconButton(QPushButton):
    """Icon-only button with tooltip, hover state, and active accent illumination."""

    def __init__(self, symbol, description, callback=None, size=38, parent=None):
        super().__init__(parent)
        self.setObjectName('icon')
        self.setProperty('iconOnly', True)
        self.setFixedSize(size, size)
        self.setIconSize(QSize(20, 20))
        self.setCursor(Qt.PointingHandCursor)
        self.symbol = symbol
        self._custom_color = None
        self.set_symbol(symbol)
        self.setText(description)
        if callback:
            self.clicked.connect(callback)

    def setText(self, text):
        self.setToolTip(text)
        self.setAccessibleName(text)

    def set_symbol(self, symbol, color=None):
        self.symbol = symbol
        self._custom_color = color
        self._update_icon()

    def setChecked(self, checked):
        super().setChecked(checked)
        self._update_icon()

    def nextCheckState(self):
        super().nextCheckState()
        self._update_icon()

    def _update_icon(self):
        col = self._custom_color
        if not col:
            if self.isCheckable() and self.isChecked() and self.objectName() == 'nav':
                col = TOKENS['accent']
            else:
                col = TOKENS['ink_2']
        self.setIcon(glyph(self.symbol, col))


class Indicator(QLabel):
    """Status pill with pulsing colored dot matching GamepadTester status."""

    def __init__(self, kind='connection', parent=None):
        super().__init__(parent)
        self.kind = kind
        self.current = None
        self.setFixedSize(30, 30)
        self.setAlignment(Qt.AlignCenter)

    def setText(self, text):
        if text == self.current:
            return
        self.current = text
        self.setToolTip(text)
        self.setAccessibleName(text)
        color = TOKENS['ink_dim']
        symbol = 'info'
        if self.kind == 'connection':
            good = any(k in text for k in ('已连接', '运行中', 'Connected', 'Running'))
            symbol = 'connected' if good else 'disconnected'
            color = TOKENS['green'] if good else TOKENS['ink_dim']
        elif self.kind == 'power':
            symbol = ('bolt' if any(k in text for k in ('外接', 'External')) else
                      'battery_low' if any(k in text for k in ('低', 'Low', 'Critical')) else
                      'battery_mid' if any(k in text for k in ('中等', 'Medium')) else 'battery')
            color = (TOKENS['amber'] if any(k in text for k in ('低', 'Low', 'Critical')) else
                     TOKENS['accent'] if any(k in text for k in ('外接', 'External')) else
                     TOKENS['green'])
            if any(k in text for k in ('未', '未知', 'Unknown', 'Disconnected')):
                symbol = 'info'
        self.symbol = symbol
        self.color = color
        self.setPixmap(glyph(symbol, color).pixmap(20, 20))


class Toggle(QCheckBox):
    """Modern iOS / GamepadTester switch toggle."""

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 56, 34)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.40)
        y = self.height() / 2
        track = QRectF(1, y - 11, 40, 22)
        painter.setPen(Qt.NoPen)
        track_color = QColor(TOKENS['green']) if self.isChecked() else QColor(TOKENS['elevated'])
        painter.setBrush(track_color)
        painter.drawRoundedRect(track, 11, 11)

        # Track border
        painter.setPen(QPen(QColor(TOKENS['border_hi']), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(track, 11, 11)

        # Thumb
        thumb_x = 29 if self.isChecked() else 11
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 40))
        painter.drawEllipse(QPointF(thumb_x, y + 1), 9, 9)
        painter.setBrush(QColor('#ffffff'))
        painter.drawEllipse(QPointF(thumb_x, y), 8.5, 8.5)

        # Text
        painter.setPen(QColor(TOKENS['ink'] if self.isEnabled() else TOKENS['ink_dim']))
        painter.drawText(QRectF(50, 0, self.width() - 50, self.height()), Qt.AlignVCenter, self.text())
        if self.hasFocus():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(TOKENS['accent']), 1.5))
            painter.drawRoundedRect(track.adjusted(-2, -2, 2, 2), 13, 13)


class GlassPanel(QFrame):
    """Quiet grouped surface with a consistent border and keyboard focus ring."""

    def __init__(self, parent=None, kind='card'):
        super().__init__(parent)
        self.setObjectName(kind)
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = TOKENS['r_lg']
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.setPen(Qt.NoPen)

        # Surface fill
        fill = QColor(TOKENS['surface'])
        hover = getattr(self, 'hover_amount', 0)
        painter.fillPath(path, fill)

        # Card border
        border_col = (QColor(TOKENS['accent']) if hover > 0.5
                      else QColor(TOKENS['border']))
        painter.setPen(QPen(border_col, 1.2))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

        if self.hasFocus():
            painter.setPen(QPen(QColor(TOKENS['accent']), 1.8))
            painter.drawPath(path)


class GlassCanvas(QWidget):
    """Matte workspace that meets screen edges cleanly when expanded."""

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        expanded = self.window().isFullScreen() or self.window().isMaximized()
        if expanded:
            painter.fillRect(rect, QColor(TOKENS['void']))
            return
        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), TOKENS['r_lg'], TOKENS['r_lg'])
        painter.setClipPath(path)

        # 1. Industrial matte dark graphite chassis
        painter.fillRect(rect, QColor(TOKENS['void']))

        painter.setClipping(False)
        painter.setPen(QPen(QColor(TOKENS['border_hi']), 1.2))
        painter.drawPath(path)


def transparency_enabled():
    if sys.platform != 'win32':
        return False
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize',
        ) as key:
            return bool(winreg.QueryValueEx(key, 'EnableTransparency')[0])
    except OSError:
        return True


class GlassWindow(QMainWindow):
    """Frameless window with fullscreen-aware resizing and desktop controls."""

    def __init__(self):
        super().__init__()
        self.native_glass = False
        self.glass_attempted = False
        self._fullscreen_restore_state = Qt.WindowNoState
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_window_chrome()
        if self.glass_attempted:
            return
        self.glass_attempted = True
        if (sys.platform != 'win32'
                or QApplication.platformName() != 'windows'
                or not transparency_enabled()):
            return
        if sys.getwindowsversion().build < 22621:
            return
        try:
            dwm = ctypes.WinDLL('dwmapi')
            hwnd = wintypes.HWND(int(self.winId()))
            dwm.DwmSetWindowAttribute.argtypes = [
                wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
            ]
            backdrop = ctypes.c_int(3)
            corner = ctypes.c_int(1 if self.isFullScreen() or self.isMaximized() else 2)
            dark = ctypes.c_int(1)
            dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), 4)
            dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), 4)
            result = dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(backdrop), 4)
            margins = (ctypes.c_int * 4)(-1, -1, -1, -1)
            dwm.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.c_void_p]
            extended = dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
            self.native_glass = result == 0 and extended == 0
            if self.centralWidget():
                self.centralWidget().update()
        except (OSError, AttributeError):
            pass

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            if self.isFullScreen() and not event.oldState() & Qt.WindowFullScreen:
                self._fullscreen_restore_state = event.oldState() & Qt.WindowMaximized
            self._sync_window_chrome()

    def _sync_window_chrome(self):
        if self.centralWidget():
            self.centralWidget().update()
        for titlebar in self.findChildren(TitleBar):
            titlebar.sync_window_state()
        if self.native_glass and sys.platform == 'win32':
            try:
                corner = ctypes.c_int(1 if self.isFullScreen() or self.isMaximized() else 2)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    wintypes.HWND(int(self.winId())), 33, ctypes.byref(corner), 4,
                )
            except (OSError, AttributeError):
                pass

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.leave_fullscreen()
        else:
            self.showFullScreen()

    def leave_fullscreen(self):
        if self.isFullScreen():
            if self._fullscreen_restore_state & Qt.WindowMaximized:
                self.showMaximized()
            else:
                self.showNormal()

    def nativeEvent(self, eventType, message):
        if sys.platform == 'win32':
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x84 and not (self.isMaximized() or self.isFullScreen()):
                rect = wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(
                    wintypes.HWND(int(self.winId())), ctypes.byref(rect),
                )
                x = ctypes.c_short(msg.lParam & 0xFFFF).value
                y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                edge = max(6, round(6 * self.devicePixelRatioF()))
                left = x < rect.left + edge
                right = x >= rect.right - edge
                top = y < rect.top + edge
                bottom = y >= rect.bottom - edge
                hit = (13 if top and left else
                       14 if top and right else
                       16 if bottom and left else
                       17 if bottom and right else
                       10 if left else
                       11 if right else
                       12 if top else
                       15 if bottom else 0)
                if hit:
                    return True, hit
        return super().nativeEvent(eventType, message)


class TitleBar(QWidget):
    """Desktop controls remain available in fullscreen and windowed modes."""

    def mousePressEvent(self, event):
        if (event.button() == Qt.LeftButton and not self.window().isFullScreen()
                and self.window().windowHandle()):
            self.window().windowHandle().startSystemMove()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle_maximized()

    def toggle_maximized(self):
        window = self.window()
        window.showNormal() if window.isFullScreen() or window.isMaximized() else window.showMaximized()

    def sync_window_state(self):
        expanded = self.window().isFullScreen() or self.window().isMaximized()
        for control in self.findChildren(IconButton):
            if control.symbol in ('maximize', 'restore'):
                control.set_symbol('restore' if expanded else 'maximize')


class SquircleBadge(QWidget):
    """Milled equipment status badge with laser-etched glyph."""

    def __init__(self, symbol, bg_gradient=None, size=32, icon_size=16, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.symbol = symbol
        self.accent = bg_gradient[0] if isinstance(bg_gradient, (list, tuple)) else (bg_gradient or TOKENS['accent'])
        self.icon_size = icon_size

    def set_accent(self, color):
        self.accent = color[0] if isinstance(color, (list, tuple)) else color
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        radius = round(self.width() * 0.28)
        path.addRoundedRect(r, radius, radius)

        # Milled recessed bay background
        painter.fillPath(path, QColor(TOKENS['void']))
        painter.setPen(QPen(QColor(TOKENS['border']), 1))
        painter.drawPath(path)

        # Crisp laser glyph in accent color
        icon = glyph(self.symbol, self.accent)
        pix = icon.pixmap(QSize(self.icon_size, self.icon_size))
        x = (self.width() - self.icon_size) / 2
        y = (self.height() - self.icon_size) / 2
        painter.drawPixmap(round(x), round(y), pix)


class AppleRow(QWidget):
    """GamepadTester setting / telemetry row: [Badge] [Title + Subtitle] [Spacer] [Control]."""

    def __init__(self, symbol, bg_gradient, title, subtitle='', control=None, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(48)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(12)

        self.badge = SquircleBadge(symbol, bg_gradient, size=30, icon_size=16)
        layout.addWidget(self.badge)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        self.title_label = QLabel(title)
        self.title_label.setWordWrap(True)
        self.title_label.setMinimumWidth(0)
        self.title_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.title_label.setStyleSheet(
            f'font-size: 13.5px; font-weight: 700; color: {TOKENS["ink"]};'
        )
        text_layout.addWidget(self.title_label)

        if subtitle:
            self.subtitle_label = QLabel(subtitle)
            self.subtitle_label.setWordWrap(True)
            self.subtitle_label.setMinimumWidth(0)
            self.subtitle_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            self.subtitle_label.setStyleSheet(
                f'font-size: 11.5px; color: {TOKENS["ink_3"]}; font-weight: 500;'
            )
            text_layout.addWidget(self.subtitle_label)
        else:
            self.subtitle_label = None

        layout.addLayout(text_layout, 1)

        self.control = control
        if control:
            layout.addWidget(control)


class AppleGroup(GlassPanel):
    """GamepadTester Inset Card with internal dividers."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(0, 4, 0, 4)
        self.vbox.setSpacing(0)
        self.row_count = 0

    def add_row(self, row_widget):
        divider = None
        if self.row_count > 0:
            divider = QFrame()
            divider.setFixedHeight(1)
            divider.setStyleSheet(
                f'background: {TOKENS["border"]}; margin-left: 12px; margin-right: 12px;'
            )
            self.vbox.addWidget(divider)
        self.vbox.addWidget(row_widget)
        if divider is not None:
            row_widget._associated_divider = divider
        self.row_count += 1

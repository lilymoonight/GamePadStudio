from __future__ import annotations

import argparse
import copy
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time

from PySide6.QtCore import Qt, QSize, QTimer, QThread, Signal, QUrl, QLockFile, QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QIcon, QPixmap, QPainter, QKeySequence, QDesktopServices, QAction, QPen, QPainterPath
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QGridLayout, QStackedWidget, QComboBox, QScrollArea, QLineEdit,
    QDialog, QFormLayout, QKeySequenceEdit, QFileDialog, QDialogButtonBox, QSlider, QCheckBox,
    QSystemTrayIcon, QMenu, QInputDialog, QListWidget, QListWidgetItem, QMessageBox, QSizePolicy, QSizeGrip)

from .ipc import AgentClient, RemoteDevice, LocalServer, request, spawn, default_root, autostart_enabled, set_autostart, cleanup_stale_agent, cleanup_stale_ui
from .studio_core import ConfigStore, GestureEngine, BUTTONS, ACTION_NAMES
from .device import Device
from .actions import WindowsActions, parse_keys, launch_command
from .mapping_engine import MappingRuntime, effective_mappings, binding_label
from .mapping_ui import BindingDialog, BindingList
from .virtual_kbm_ui import VirtualKbmPage
from .controller_photo import ControllerPhoto, ControllerInput, PHOTOS, photo_health
from .input_tester import InputTester
from .controller_catalog import CATALOG, button_labels, capture_button, button_order, controller_defaults
from .screenshot_service import take_screenshot, list_captures, set_favorite, delete_capture

from .glass import (TOKENS, tag_style, token, token_color, STYLE, GlassWindow, GlassCanvas, GlassPanel, TitleBar,
                    IconButton, Indicator, Toggle, glyph, app_icon,
                    SquircleBadge, AppleRow, AppleGroup, LedSwatch)
from .te_widgets import DotMatrixDisplay, SpeakerGrille, RotaryKnob, RockerSwitch
from .i18n import (tr, tr_button, tr_profile, get_language, set_language,
                   init_language, get_language_preference)



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


def scroll(widget):
    area = QScrollArea(); area.setWidgetResizable(True); area.setWidget(widget)
    return area


def navigation_icon(index):
    return glyph({0:'controller',1:'mapping',2:'photos',3:'wave',4:'settings',5:'grid'}[index])


class CaptureWorker(QThread):
    ready = Signal(str)
    failed = Signal(str)
    def __init__(self, folder, mode):
        super().__init__(); self.folder, self.mode = folder, mode
    def run(self):
        try: self.ready.emit(take_screenshot(self.folder, mode=self.mode))
        except Exception as exc: self.failed.emit(str(exc))


MappingDialog = BindingDialog


class Studio(GlassWindow):
    def __init__(self, root, standalone=False, lang=None):
        super().__init__()
        root=Path(root).resolve(); self.remote=not standalone; self.closed=False
        self.store=ConfigStore(root); self.config=self.store.data
        self.lang_pref = lang
        init_language(lang or self.config.get('language', 'auto'))
        self.setWindowTitle('GamePad Studio'); self.setWindowIcon(app_icon()); self.resize(1200,780); self.setMinimumSize(960,640)
        self.client=AgentClient(root,self) if self.remote else None
        self.device=RemoteDevice(self.client) if self.remote else Device()
        if not self.remote:self.device.preferred_key=self.config.get('preferred_controller','')
        self.actions=WindowsActions()
        self.engine=MappingRuntime(self.actions, self.dispatch, start_mouse=not self.remote)
        if self.remote:
            self.client.event.connect(self.agent_event)
            if request(root,'status',timeout=200) is None:
                cleanup_stale_agent(root)
                spawn(root,'--agent')
        self.snapshot=None; self.previous_connected=False; self.enabled=bool(self.config.get('mapping_enabled',True)); self.last_capture=0; self.worker=None
        self.device_identity=object();self.button_names=button_labels('dualsense');self.mapping_boxes={}
        self.last_touch=None; self.last_buttons=set(); self.quitting=False; self.learn=False
        self.log_rows=[]; self.nav={}; self.mapping_labels={}; self.recent_labels=[]
        main = GlassCanvas()
        self.setCentralWidget(main)
        outer = QHBoxLayout(main)
        outer.setContentsMargins(16, 16, 22, 14)
        outer.setSpacing(20)

        rail = QWidget()
        rail.setFixedWidth(58)
        side = QVBoxLayout(rail)
        side.setContentsMargins(0, 0, 0, 4)
        side.setSpacing(10)

        brand = QPushButton()
        brand.setObjectName('brand')
        brand.setFixedSize(44, 44)
        brand.setIcon(app_icon())
        brand.setIconSize(QSize(42, 42))
        brand.setCursor(Qt.PointingHandCursor)
        brand.setToolTip(tr('GamePad Studio · 手柄控制中心 (返回概览)'))
        brand.clicked.connect(lambda: self.navigate(0))
        side.addWidget(brand, 0, Qt.AlignHCenter)

        dock = QWidget()
        dock_layout = QVBoxLayout(dock)
        dock_layout.setContentsMargins(0, 0, 0, 0)
        dock_layout.setSpacing(8)

        for i, title in [(5, tr('控制器库')), (0, tr('设备概览')), (1, tr('按键配置')), (6, tr('虚拟键鼠')), (2, tr('截图图库')), (3, tr('硬件遥测'))]:
            b = IconButton({5: 'grid', 0: 'controller', 1: 'mapping', 6: 'keyboard', 2: 'photos', 3: 'wave'}[i],
                           title, lambda checked=False, j=i: self.navigate(j), 44)
            b.setObjectName('nav')
            b.setIconSize(QSize(22, 22))
            b.setCheckable(True)
            dock_layout.addWidget(b, 0, Qt.AlignHCenter)
            self.nav[i] = b

        side.addWidget(dock)
        side.addStretch()

        settings = IconButton('settings', tr('系统设置'), lambda: self.navigate(4), 44)
        settings.setObjectName('nav')
        settings.setCheckable(True)
        side.addWidget(settings, 0, Qt.AlignHCenter)
        self.nav[4] = settings

        help_button = IconButton('help', tr('使用指南'), self.show_help, 44)
        help_button.setObjectName('nav')
        side.addWidget(help_button, 0, Qt.AlignHCenter)

        self.side_status = QLabel()
        self.side_status.hide()
        outer.addWidget(rail)

        body = QWidget()
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(14)

        chrome = TitleBar()
        header = QHBoxLayout(chrome)
        header.setContentsMargins(2, 0, 0, 2)
        header.setSpacing(10)

        self.page_heading = label(tr('设备概览'), 'heading')
        self.page_heading.setAttribute(Qt.WA_TransparentForMouseEvents)
        header.addWidget(self.page_heading)
        header.addStretch()

        capsule = QWidget()
        capsule.setFixedHeight(38)
        capsule.setObjectName('pill')
        capsule_layout = QHBoxLayout(capsule)
        capsule_layout.setContentsMargins(10, 2, 8, 2)
        capsule_layout.setSpacing(8)

        self.status_badge = Indicator()
        self.status_badge.setText(tr('未连接'))
        capsule_layout.addWidget(self.status_badge)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.VLine)
        _border_hi = TOKENS['border_hi']
        sep1.setStyleSheet(f'color: {_border_hi}; max-height: 18px;')
        capsule_layout.addWidget(sep1)

        self.pause_button = IconButton('pause', tr('暂停手柄映射'), self.toggle_pause, 28)
        capsule_layout.addWidget(self.pause_button)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.VLine)
        sep2.setStyleSheet(f'color: {_border_hi}; max-height: 18px;')
        capsule_layout.addWidget(sep2)

        self.capture_button = IconButton('camera', tr('即时截屏 (Create / F12)'), self.capture, 28)
        self.capture_button.setObjectName('primary')
        self.capture_button.set_symbol('camera', '#ffffff')
        capsule_layout.addWidget(self.capture_button)
        header.addWidget(capsule)

        header.addSpacing(6)
        for symbol, title, action in [('minimize', tr('最小化'), self.showMinimized),
                                      ('maximize', tr('最大化 / 还原'), chrome.toggle_maximized),
                                      ('close', tr('关闭窗口'), self.close)]:
            control = IconButton(symbol, title, action, 28)
            control.setObjectName('close' if symbol == 'close' else 'window')
            control.setIconSize(QSize(14, 14))
            header.addWidget(control)
        content.addWidget(chrome)
        self.page_description=label('');self.page_description.hide()
        self.stack=QStackedWidget();content.addWidget(self.stack,1)
        self.build_home();self.build_mapping();self.build_gallery();self.build_test();self.build_settings()
        from .controller_gallery import ControllerGallery
        self.controllers=ControllerGallery(self.select_controller,lambda values:self.setting('controller_favorites',values),self.rescan_controllers,lambda:self.navigate(0),self.config['controller_favorites'])
        self.stack.addWidget(self.controllers)
        self.virtual_kbm_page = VirtualKbmPage(self, store=self.store)
        self.stack.addWidget(self.virtual_kbm_page)
        foot=QHBoxLayout();self.notice=label('','muted');foot.addWidget(self.notice,1);foot.addWidget(QSizeGrip(self));content.addLayout(foot)
        self.notice_timer=QTimer(self);self.notice_timer.setSingleShot(True);self.notice_timer.timeout.connect(lambda:self.notice.setText(''))
        outer.addWidget(body,1)
        self.tray=QSystemTrayIcon(app_icon(),self); self.tray.setToolTip('GamePad Studio')
        self.navigate(5); self.refresh_gallery(); self.refresh_mappings()
        self.timer=QTimer(self); self.timer.timeout.connect(self.poll); self.timer.start(16)
        self.scan_timer=QTimer(self); self.scan_timer.timeout.connect(self.scan); self.scan_timer.start(1000)
        self.gallery_timer=QTimer(self); self.gallery_timer.timeout.connect(self.refresh_gallery); self.gallery_timer.start(5000)
        self.scan()
        if self.store.warning: self.notify(self.store.warning)

    def build_home(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        row = QHBoxLayout()
        row.setSpacing(14)

        # 1. Left: Hero Showcase Panel with vector blueprint & telemetry matrix
        hero, h = card('hero')
        h.setContentsMargins(22, 18, 22, 18)
        top_deck = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        self.controller_eyebrow = label('HARDWARE WORKSTATION', 'eyebrow')
        title_box.addWidget(self.controller_eyebrow)
        self.controller_heading = label(CATALOG.get('dualsense', {}).get('name', 'DualSense 无线控制器'), 'productTitle')
        title_box.addWidget(self.controller_heading)

        # Clean pro workstation header (no redundant marketing text)
        self.controller_features = label('', 'caption')
        self.controller_features.hide()
        top_deck.addLayout(title_box)
        top_deck.addStretch()

        top_deck.addWidget(SpeakerGrille(7, 5, 2.0, 6.5))

        # Inline status readout (no nested pill box)
        pwr_layout = QHBoxLayout()
        pwr_layout.setContentsMargins(6, 4, 6, 4)
        pwr_layout.setSpacing(6)
        self.device_details = Indicator('power')
        pwr_layout.addWidget(self.device_details)
        self.power_label = label(tr('未连接'), 'muted')
        self.power_label.setObjectName('metric')
        pwr_layout.addWidget(self.power_label)
        top_deck.addLayout(pwr_layout)
        h.addLayout(top_deck)

        # Mid rack: CAD Blueprint & Telemetry Matrix
        mid_rack = QHBoxLayout()
        mid_rack.setSpacing(16)

        self.art = ControllerPhoto()
        self.art.setMinimumSize(340, 220)
        mid_rack.addWidget(self.art, 3)

        self.matrix = DotMatrixDisplay("FIELD TELEMETRY // HW-01", [
            ("LINK", "OFFLINE"), ("POWER", "STANDBY"), ("POLL", "1000 HZ"), ("STATUS", "WAITING")
        ], color=TOKENS['green'])
        self.matrix.setFixedWidth(200)
        mid_rack.addWidget(self.matrix, 1)

        h.addLayout(mid_rack, 1)
        self.home_input_feedback = label('等待输入', 'muted', True)
        h.addWidget(self.home_input_feedback)

        self.photo_caption = label(PHOTOS['dualsense']['caption'], 'muted')
        self.photo_caption.hide()
        row.addWidget(hero, 7)

        # 2. Right: Single Unified Operations Rack
        right_panel = AppleGroup()
        right_panel.setMinimumWidth(320)
        right_panel.setMaximumWidth(390)

        # Unified monochrome badge icon color (Dieter Rams & Teenage Engineering hardware tone)
        BADGE_COLOR = (TOKENS['ink_2'], TOKENS['ink_2'])

        # Section 1: Tuning & Profiles
        hdr1 = QLabel('  01 // CONTROL & PROFILES')
        hdr1.setObjectName('eyebrow')
        hdr1.setStyleSheet(f"color: {TOKENS['ink_3']}; font-size: 10px; font-weight: 700; letter-spacing: 0.8px; padding: 10px 16px 4px 16px;")
        right_panel.vbox.addWidget(hdr1)

        self.profile_combo = QComboBox()
        self.profile_combo.addItems(self.config['profiles'])
        self.profile_combo.setCurrentText(self.config['active_profile'])
        self.profile_combo.currentTextChanged.connect(self.change_profile)
        self.profile_combo.setFixedSize(112, 28)
        self.profile_combo.setMaxVisibleItems(10)
        row_profile = AppleRow('controller', BADGE_COLOR, tr('配置预设'), '', self.profile_combo)
        row_profile.setToolTip(tr('按键映射方案与预设配置切换'))
        right_panel.add_row(row_profile)

        self.rumble_button = button(tr('脉冲测试'), self.rumble, icon='wave', pill=True)
        self.rumble_button.setFixedSize(112, 28)
        row_rumble = AppleRow('wave', BADGE_COLOR, tr('触觉反馈'), '', self.rumble_button)
        row_rumble.setToolTip(tr('双马达触觉脉冲与响应测试'))
        right_panel.add_row(row_rumble)

        nav_mapping_btn = button(tr('编辑按键 ›'), lambda: self.navigate(1), pill=True)
        nav_mapping_btn.setFixedSize(112, 28)
        row_map = AppleRow('mapping', BADGE_COLOR, tr('按键映射'), '', nav_mapping_btn)
        row_map.setToolTip(tr('自定义按键键位与长按/短按宏映射'))
        right_panel.add_row(row_map)

        # Section 2: Shortcuts & Dispatch
        hdr2 = QLabel('  02 // SHORTCUT DISPATCH')
        hdr2.setObjectName('eyebrow')
        hdr2.setStyleSheet(f"color: {TOKENS['ink_3']}; font-size: 10px; font-weight: 700; letter-spacing: 0.8px; padding: 14px 16px 4px 16px;")
        right_panel.vbox.addWidget(hdr2)

        self.capture_action_btn = button(tr('查看图库 ›'), lambda: self.navigate(2), pill=True)
        self.capture_action_btn.setFixedSize(112, 28)
        self.row_capture = AppleRow('camera', BADGE_COLOR, tr('截图'), '', self.capture_action_btn)
        self.capture_heading = self.row_capture.title_label
        self.create_hint = QLabel()
        right_panel.add_row(self.row_capture)

        self.guide_action_btn = button(tr('系统菜单 ›'), lambda: self.navigate(1), pill=True)
        self.guide_action_btn.setFixedSize(112, 28)
        self.row_guide = AppleRow('controller', BADGE_COLOR, tr('Xbox 导航'), '', self.guide_action_btn)
        self.guide_heading = self.row_guide.title_label
        self.guide_hint = QLabel()
        right_panel.add_row(self.row_guide)

        self.touch_action_btn = button(tr('手势映射 ›'), lambda: self.navigate(1), pill=True)
        self.touch_action_btn.setFixedSize(112, 28)
        self.row_touchpad = AppleRow('touchpad', BADGE_COLOR, tr('触摸板'), '', self.touch_action_btn)
        right_panel.add_row(self.row_touchpad)

        row.addWidget(right_panel, 3)
        layout.addLayout(row, 1)

        # 3. Bottom: Recent Captures Strip (Direct flat tray, no nested card-in-card)
        bottom = QWidget()
        b = QVBoxLayout(bottom)
        b.setContentsMargins(4, 4, 4, 0)
        b.setSpacing(8)
        heading = QHBoxLayout()
        head_v = QVBoxLayout()
        head_v.setSpacing(2)
        head_v.addWidget(label('CAPTURES BUFFER // ' + tr('缓冲与最近保存'), 'eyebrow'))
        head_v.addWidget(label(tr('近期截图'), 'section'))
        heading.addLayout(head_v)
        heading.addStretch()
        all_btn = button(tr('查看完整图库 ›'), lambda: self.navigate(2))
        all_btn.setObjectName('nav')
        heading.addWidget(all_btn)
        b.addLayout(heading)

        self.recent_row = QHBoxLayout()
        self.recent_row.setSpacing(12)
        b.addLayout(self.recent_row)
        layout.addWidget(bottom)
        self.stack.addWidget(scroll(page))


    def build_mapping(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        tools = QHBoxLayout()
        tools.setSpacing(10)
        self.mapping_profile = label('主机体验', 'section')
        self.mapping_profile.hide()
        tools.addWidget(label(tr('当前核心配置：'), 'muted'))
        self.mapping_combo = QComboBox()
        self.mapping_combo.setMinimumWidth(170)
        self.mapping_combo.currentTextChanged.connect(self.change_profile)
        tools.addWidget(self.mapping_combo)
        tools.addStretch()
        self.learn_button = button(tr('识别按键'), self.start_learning, icon='controller')
        self.learn_button.setCheckable(True)
        tools.addWidget(self.learn_button)
        tools.addWidget(button(tr('恢复默认'), self.reset_profile, icon='refresh'))
        tools.addWidget(button(tr('新建'), self.duplicate_profile, icon='plus'))
        self.delete_profile_btn = button(tr('删除'), self.delete_profile, icon='trash')
        tools.addWidget(self.delete_profile_btn)
        layout.addLayout(tools)

        workspace = QWidget()
        workspace.setMinimumHeight(350)
        columns = QHBoxLayout(workspace)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(16)

        # Left Stage: Blueprint + Direct Telemetry Deck (No nested inspector card)
        stage, body = card('hero')
        body.setContentsMargins(22, 18, 22, 18)
        body.setSpacing(12)
        self.mapping_model = label('DualSense', 'productTitle')
        body.addWidget(self.mapping_model)
        self.mapping_art = ControllerInput()
        self.mapping_art.setMinimumSize(280, 100)
        self.mapping_art.button_clicked.connect(self.select_mapping)
        body.addWidget(self.mapping_art, 1)

        # Hairline divider within stage (replaces nested GlassPanel box)
        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet(f"background: {TOKENS['border']}; margin: 2px 0;")
        body.addWidget(div)

        detail = QHBoxLayout()
        self.selected_key = 0

        head_sel = QVBoxLayout()
        head_sel.setSpacing(2)
        head_sel.addWidget(label('SELECTED INPUT // ' + tr('当前按键'), 'eyebrow'))
        self.selected_label = label(self.button_names.get(0, '× 交叉'), 'section')
        self.selected_label.setObjectName('productTitle')
        head_sel.addWidget(self.selected_label)
        detail.addLayout(head_sel, 1)

        self.mapping_edit = button(tr('配置动作...'), lambda: self.edit_mapping(self.selected_key), primary=True, icon='edit', pill=True)
        detail.addWidget(self.mapping_edit)
        body.addLayout(detail)

        # Direct telemetry rows (replaces nested g_card boxes)
        gestures = QHBoxLayout()
        gestures.setSpacing(18)
        self.mapping_short = label('', 'muted', True)
        self.mapping_long = label('', 'muted', True)
        for title, summary in [('SHORT PRESS // ' + tr('短按'), self.mapping_short), ('LONG PRESS // ' + tr('长按'), self.mapping_long)]:
            g_col = QVBoxLayout()
            g_col.setSpacing(3)
            g_col.setContentsMargins(0, 2, 0, 2)
            g_col.addWidget(label(title, 'eyebrow'))
            g_col.addWidget(summary)
            gestures.addLayout(g_col, 1)
        body.addLayout(gestures)
        columns.addWidget(stage, 6)

        # Right Grid: Precision CNC Mechanical Keypad Tiles (No nested badge boxes)
        rows = QWidget()
        grid = QGridLayout(rows)
        self.mapping_grid = grid
        grid.setContentsMargins(0, 0, 4, 0)
        grid.setSpacing(8)
        for i, key in enumerate(button_order('dualsense')):
            box = QPushButton()
            box.setObjectName('mappingTile')
            box.setCheckable(True)
            box.setMinimumHeight(48)
            box.setCursor(Qt.PointingHandCursor)
            box.clicked.connect(lambda checked=False, k=key: self.select_mapping(k))
            b = QHBoxLayout(box)
            b.setContentsMargins(14, 8, 14, 8)
            b.setSpacing(10)

            t_col = QVBoxLayout()
            t_col.setContentsMargins(0, 0, 0, 0)
            t_col.setSpacing(2)
            name = label(self.button_names[key], 'section')
            name.setAttribute(Qt.WA_TransparentForMouseEvents)
            t_col.addWidget(name)

            info = label('', 'muted')
            info.setObjectName('caption')
            info.setAttribute(Qt.WA_TransparentForMouseEvents)
            t_col.addWidget(info)
            b.addLayout(t_col, 1)

            arrow = label('›', 'muted')
            arrow.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {TOKENS['ink_dim']};")
            arrow.setAttribute(Qt.WA_TransparentForMouseEvents)
            b.addWidget(arrow)

            self.mapping_boxes[key] = (box, name)
            self.mapping_labels[key] = info
            grid.addWidget(box, i // 2, i % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        inputs = scroll(rows)
        inputs.setMinimumWidth(300)
        columns.addWidget(inputs, 5)
        layout.addWidget(workspace, 3)
        self.mapping_feedback = label('等待输入', 'muted')
        layout.addWidget(self.mapping_feedback)
        self.binding_list = BindingList(self)
        layout.addWidget(self.binding_list, 1)
        self.stack.addWidget(scroll(page))

    def build_gallery(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.only_favorites = IconButton('heart', tr('仅看收藏'), size=32)
        self.only_favorites.setCheckable(True)
        self.only_favorites.toggled.connect(self.refresh_gallery)
        self.only_favorites.setStyleSheet(f'background: {TOKENS["elevated"]}; border: 1px solid {TOKENS["border_hi"]}; border-radius: {TOKENS["r_sm"]}px;')

        folder_btn = IconButton('folder', tr('打开截图文件夹'), self.open_capture_folder, 32)
        folder_btn.setStyleSheet(f'background: {TOKENS["elevated"]}; border: 1px solid {TOKENS["border_hi"]}; border-radius: {TOKENS["r_sm"]}px;')

        self.clean_captures_btn = IconButton('trash', tr('清理未收藏截图'), self.clean_unfavorited_captures, 32)
        self.clean_captures_btn.setObjectName('icon_danger')
        self.clean_captures_btn.setStyleSheet(f'background: {TOKENS["elevated"]}; border: 1px solid {TOKENS["border_hi"]}; border-radius: {TOKENS["r_sm"]}px;')

        bar.addWidget(self.only_favorites)
        bar.addWidget(folder_btn)
        bar.addWidget(self.clean_captures_btn)
        bar.addStretch()

        self.gallery_info = label('', 'muted')
        self.gallery_info.setStyleSheet(f'color: {TOKENS["ink_3"]}; font-size: 12px; font-weight: 600;')
        bar.addWidget(self.gallery_info)

        self.search = QLineEdit()
        self.search.setPlaceholderText(tr('搜索截图文件...'))
        self.search.setFixedWidth(240)
        self.search.setFixedHeight(32)
        self.search.setStyleSheet(
            f'background: {TOKENS["elevated"]}; border: 1px solid {TOKENS["border_hi"]}; '
            f'border-radius: {TOKENS["r_sm"]}px; padding: 0 12px; font-size: 12px;'
        )
        self.search.addAction(glyph('search', TOKENS['ink_3']), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self.refresh_gallery)
        bar.addWidget(self.search)
        layout.addLayout(bar)
        self.gallery_content = QWidget()
        self.gallery_grid = QGridLayout(self.gallery_content)
        self.gallery_grid.setContentsMargins(0, 10, 0, 10)
        self.gallery_grid.setSpacing(16)
        layout.addWidget(scroll(self.gallery_content), 1)
        self.stack.addWidget(page)

    def build_test(self):
        self.events = QListWidget()
        self.events.setWindowTitle(tr('活动记录'))
        self.events.resize(640, 400)
        self.events.setParent(self, Qt.Dialog)
        self.events.hide()
        self.tester = InputTester(self.events.show, self.test_rumble)
        self.stack.addWidget(scroll(self.tester))

    def build_settings(self):
        page = QWidget()
        grid = QGridLayout(page)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(16)

        # 1. 截图服务与存储
        cap = AppleGroup()
        hdr_cap = QLabel('  01 // CAPTURE ENGINE · ' + tr('截图服务与存储'))
        hdr_cap.setObjectName('eyebrow')
        hdr_cap.setStyleSheet(f"color: {TOKENS['accent']}; font-size: 10.5px; font-weight: 700; padding: 8px 16px 2px 16px;")
        cap.vbox.addWidget(hdr_cap)

        self.mode_combo = QComboBox()
        for name, value in [
            (tr('🎮 智能游戏屏幕 (自动探测锁定)'), 'game'),
            (tr('🖥️ 双屏全景全录制 (全部显示器 7680x2160)'), 'all'),
            (tr('🖥️ 显示器 1 (主屏幕)'), 'monitor_1'),
            (tr('🖥️ 显示器 2 (副屏幕)'), 'monitor_2'),
            (tr('🪟 当前活动独立窗口'), 'window'),
        ]:
            self.mode_combo.addItem(name, value)
        cur_mode = self.config.get('capture_mode', 'game')
        if cur_mode == 'monitor':
            cur_mode = 'game'
        idx = self.mode_combo.findData(cur_mode)
        self.mode_combo.setCurrentIndex(max(0, idx))
        self.mode_combo.currentIndexChanged.connect(lambda: self.setting('capture_mode', self.mode_combo.currentData()))
        self.mode_combo.setMinimumWidth(210)
        cap.add_row(AppleRow('camera', (TOKENS['accent'], TOKENS['accent_lo']), tr('捕获目标屏幕与范围'), tr('智能锁定游戏屏幕或双屏跨屏全景录制'), self.mode_combo))

        folder_row = QWidget()
        f_layout = QHBoxLayout(folder_row)
        f_layout.setContentsMargins(14, 8, 14, 8)
        f_layout.setSpacing(12)
        f_layout.addWidget(SquircleBadge('folder', (TOKENS['purple'], TOKENS['accent_lo'])))
        f_v = QVBoxLayout()
        f_v.setContentsMargins(0, 0, 0, 0)
        f_v.setSpacing(2)
        f_title = label(tr('截图存储路径'), 'section')
        f_v.addWidget(f_title)
        self.folder_label = label(self.config['save_dir'], 'muted')
        self.folder_label.setObjectName('metric')
        f_v.addWidget(self.folder_label)
        f_layout.addLayout(f_v, 1)
        self.folder_button = button(tr('更改目录...'), self.choose_folder, pill=True)
        f_layout.addWidget(self.folder_button)
        cap.add_row(folder_row)

        cool_row = QWidget()
        c_layout = QHBoxLayout(cool_row)
        c_layout.setContentsMargins(14, 8, 14, 8)
        c_layout.setSpacing(12)
        c_layout.addWidget(SquircleBadge('timer', (TOKENS['amber'], TOKENS['amber'])))
        c_tv = QVBoxLayout()
        c_tv.setContentsMargins(0, 0, 0, 0)
        c_tv.setSpacing(2)
        c_title = label(tr('防抖冷却间隔'), 'section')
        c_tv.addWidget(c_title)
        c_desc = label(tr('连击防误触时间阈值'), 'muted')
        c_desc.setObjectName('caption')
        c_tv.addWidget(c_desc)
        cool_val = label(f"{self.config['cooldown']:.2f} " + tr('秒'))
        cool_val.setStyleSheet(f"background: transparent; font: 12px 'Cascadia Code', monospace; font-weight: 700; color: {TOKENS['amber']}; padding: 0 2px;")
        cool_slider = QSlider(Qt.Horizontal)
        cool_slider.setRange(10, 200)
        cool_slider.setValue(round(self.config['cooldown'] * 100))
        c_tv.addWidget(cool_slider)
        c_layout.addLayout(c_tv, 1)

        cool_knob = RotaryKnob("COOLDOWN", 0.10, 2.00, self.config['cooldown'], "s", TOKENS['amber'], 46)
        def on_cool_change(v):
            sec = v / 100
            cool_val.setText(f"{sec:.2f} " + tr('秒'))
            cool_knob.setValue(sec)
            self.setting('cooldown', sec)
        cool_slider.valueChanged.connect(on_cool_change)
        cool_knob.valueChanged.connect(lambda v: (cool_slider.blockSignals(True), cool_slider.setValue(round(v * 100)), cool_slider.blockSignals(False), self.setting('cooldown', v)))
        c_layout.addWidget(cool_knob)
        cap.add_row(cool_row)

        # 机械快门声音反馈开关
        self.shutter_sound_box = Toggle(tr('启用'))
        self.shutter_sound_box.setChecked(bool(self.config.get('capture_sound_enabled', True)))
        self.shutter_sound_box.toggled.connect(lambda v: self.setting('capture_sound_enabled', v))
        cap.add_row(AppleRow('wave', (TOKENS['amber'], TOKENS['accent_lo']), tr('机械快门音效反馈'), tr('截图成功时通过系统播放清脆的高保真相机机械快门声'), self.shutter_sound_box))

        # 截图手柄触觉微脉冲开关
        self.shutter_haptics_box = Toggle(tr('启用'))
        self.shutter_haptics_box.setChecked(bool(self.config.get('capture_haptics_enabled', True)))
        self.shutter_haptics_box.toggled.connect(lambda v: self.setting('capture_haptics_enabled', v))
        cap.add_row(AppleRow('controller', (TOKENS['purple'], TOKENS['accent_lo']), tr('掌心触觉脉冲反馈'), tr('截图成功瞬间手柄给予 60ms 两段式物理快门轻触确认'), self.shutter_haptics_box))

        # 屏蔽 Windows 截图与 Game Bar 弹窗开关
        from .gamebar_shield import is_gamebar_shield_active
        self.gamebar_shield_box = Toggle(tr('启用'))
        init_shield = bool(self.config.get('gamebar_shield_enabled', False))
        self.gamebar_shield_box.setChecked(init_shield)
        self.gamebar_shield_box.toggled.connect(self.on_toggle_gamebar_shield)
        cap.add_row(AppleRow('shield', (TOKENS['purple'], TOKENS['accent_lo']), tr('屏蔽 Windows 截图与 Game Bar 弹窗'), tr('关闭 Windows 的手柄游戏栏与游戏录制响应'), self.gamebar_shield_box))

        grid.addWidget(cap, 0, 0)

        # 2. 硬件交互与反馈
        hardware = AppleGroup()
        hdr_hw = QLabel('  02 // HAPTICS & ILLUMINATION · ' + tr('硬件交互与反馈'))
        hdr_hw.setObjectName('eyebrow')
        hdr_hw.setStyleSheet(f"color: {TOKENS['amber']}; font-size: 10.5px; font-weight: 700; padding: 8px 16px 2px 16px;")
        hardware.vbox.addWidget(hdr_hw)

        led_row = QWidget()
        l_layout = QHBoxLayout(led_row)
        l_layout.setContentsMargins(14, 8, 14, 8)
        l_layout.setSpacing(12)
        l_layout.addWidget(SquircleBadge('lightbulb', (TOKENS['cyan'], TOKENS['accent_lo'])))
        l_v = QVBoxLayout()
        l_v.setContentsMargins(0, 0, 0, 0)
        l_v.setSpacing(2)
        l_title = label(tr('LED 状态光条'), 'section')
        l_v.addWidget(l_title)
        l_desc = label(tr('手柄呼吸光条发光色调'), 'muted')
        l_desc.setObjectName('caption')
        l_v.addWidget(l_desc)
        l_layout.addLayout(l_v, 1)

        colors = QHBoxLayout()
        colors.setSpacing(10)
        self.led_buttons = []
        cur_led = self.config.get('led', TOKENS['accent'])
        for color in [TOKENS['accent'], TOKENS['purple'], TOKENS['green'], TOKENS['red'], TOKENS['amber']]:
            b = LedSwatch(color, lambda checked=False, c=color: self.set_led(c))
            b.set_selected(color == cur_led)
            colors.addWidget(b)
            self.led_buttons.append(b)
        l_layout.addLayout(colors)
        hardware.add_row(led_row)

        rumble_row = QWidget()
        r_layout = QHBoxLayout(rumble_row)
        r_layout.setContentsMargins(14, 8, 14, 8)
        r_layout.setSpacing(12)
        r_layout.addWidget(SquircleBadge('wave', (TOKENS['purple'], TOKENS['purple'])))
        r_tv = QVBoxLayout()
        r_tv.setContentsMargins(0, 0, 0, 0)
        r_tv.setSpacing(2)
        r_title = label(tr('双马达振动强度'), 'section')
        r_tv.addWidget(r_title)
        r_desc = label(tr('触觉反馈马达输出力度'), 'muted')
        r_desc.setObjectName('caption')
        r_tv.addWidget(r_desc)
        rumble_val = label(f"{round(self.config['rumble'] * 100)}%")
        rumble_val.setStyleSheet(f"background: transparent; font: 12px 'Cascadia Code', monospace; font-weight: 700; color: {TOKENS['accent']}; padding: 0 2px;")
        rumble_slider = QSlider(Qt.Horizontal)
        rumble_slider.setRange(0, 100)
        rumble_slider.setValue(round(self.config['rumble'] * 100))
        r_tv.addWidget(rumble_slider)
        r_layout.addLayout(r_tv, 1)

        self.feedback_rumble = button(tr('脉冲测试'), self.rumble, primary=True, icon='wave', pill=True)
        r_layout.addWidget(self.feedback_rumble)

        rumble_knob = RotaryKnob("RUMBLE", 0.0, 1.0, self.config['rumble'], "%", TOKENS['accent'], 46)
        def on_rumble_change(v):
            pct = v / 100
            rumble_val.setText(f"{v}%")
            rumble_knob.setValue(pct)
            self.setting('rumble', pct)
        rumble_slider.valueChanged.connect(on_rumble_change)
        rumble_knob.valueChanged.connect(lambda v: (rumble_slider.blockSignals(True), rumble_slider.setValue(round(v * 100)), rumble_slider.blockSignals(False), self.setting('rumble', v)))
        r_layout.addWidget(rumble_knob)
        hardware.add_row(rumble_row)

        self.touch_mouse_box = Toggle(tr('启用'))
        self.touch_mouse_box.setChecked(self.config['touch_mouse'])
        self.touch_mouse_box.toggled.connect(lambda value: self.setting('touch_mouse', value))
        hardware.add_row(AppleRow('touchpad', (TOKENS['green'], TOKENS['green']), tr('触摸板手势扩展'), tr('双指轻扫模拟 Windows 鼠标指针'), self.touch_mouse_box))

        # 触觉拟真引擎与波形调校
        haptic_row = QWidget()
        h_layout = QHBoxLayout(haptic_row)
        h_layout.setContentsMargins(14, 8, 14, 8)
        h_layout.setSpacing(12)
        h_layout.addWidget(SquircleBadge('wave', (TOKENS['accent'], TOKENS['accent_lo'])))
        h_tv = QVBoxLayout()
        h_tv.setContentsMargins(0, 0, 0, 0)
        h_tv.setSpacing(2)
        h_title = label(tr('触觉拟真引擎 (Haptic Engine)'), 'section')
        h_tv.addWidget(h_title)
        h_desc = label(tr('微秒级双音圈多段触觉波形合成 (快门/棘轮/冲击/心跳)'), 'muted')
        h_desc.setObjectName('caption')
        h_tv.addWidget(h_desc)
        h_layout.addLayout(h_tv, 1)

        self.test_haptic_shutter = button(tr('快门触觉'), lambda: self.test_haptic_pattern('shutter'), pill=True)
        self.test_haptic_impact = button(tr('冲击阻尼'), lambda: self.test_haptic_pattern('impact'), pill=True)
        self.test_haptic_heart = button(tr('心跳律动'), lambda: self.test_haptic_pattern('heartbeat'), pill=True)
        h_layout.addWidget(self.test_haptic_shutter)
        h_layout.addWidget(self.test_haptic_impact)
        h_layout.addWidget(self.test_haptic_heart)
        hardware.add_row(haptic_row)

        grid.addWidget(hardware, 0, 1)

        # 3. 输入手势设定与文档
        general = AppleGroup()
        hdr_gen = QLabel('  03 // GESTURES & AUTOMATION · ' + tr('手势与高级设置'))
        hdr_gen.setObjectName('eyebrow')
        hdr_gen.setStyleSheet(f"color: {TOKENS['green']}; font-size: 10.5px; font-weight: 700; padding: 8px 16px 2px 16px;")
        general.vbox.addWidget(hdr_gen)

        lp_row = QWidget()
        lp_layout = QHBoxLayout(lp_row)
        lp_layout.setContentsMargins(14, 8, 14, 8)
        lp_layout.setSpacing(12)
        lp_layout.addWidget(SquircleBadge('timer', (TOKENS['amber'], TOKENS['amber'])))
        lp_tv = QVBoxLayout()
        lp_tv.setContentsMargins(0, 0, 0, 0)
        lp_tv.setSpacing(2)
        lp_title = label(tr('长按手势识别阈值'), 'section')
        lp_tv.addWidget(lp_title)
        lp_desc = label(tr('按住按键达到设定时长触发二次宏动作'), 'muted')
        lp_desc.setObjectName('caption')
        lp_tv.addWidget(lp_desc)
        lp_val = label(f"{self.config['long_press']:.2f} " + tr('秒'))
        lp_val.setStyleSheet(f"background: transparent; font: 12px 'Cascadia Code', monospace; font-weight: 700; color: {TOKENS['chalk']}; padding: 0 2px;")
        lp_slider = QSlider(Qt.Horizontal)
        lp_slider.setRange(30, 150)
        lp_slider.setValue(round(self.config['long_press'] * 100))
        lp_tv.addWidget(lp_slider)
        lp_layout.addLayout(lp_tv, 1)

        lp_knob = RotaryKnob("LONG PRESS", 0.30, 1.50, self.config['long_press'], "s", TOKENS['chalk'], 46)
        def on_lp_change(v):
            sec = v / 100
            lp_val.setText(f"{sec:.2f} " + tr('秒'))
            lp_knob.setValue(sec)
            self.setting('long_press', sec)
        lp_slider.valueChanged.connect(on_lp_change)
        lp_knob.valueChanged.connect(lambda v: (lp_slider.blockSignals(True), lp_slider.setValue(round(v * 100)), lp_slider.blockSignals(False), self.setting('long_press', v)))
        lp_layout.addWidget(lp_knob)
        general.add_row(lp_row)

        self.lang_combo = QComboBox()
        self.lang_combo.addItem('跟随系统 (System Default)', 'auto')
        self.lang_combo.addItem('English (US)', 'en')
        self.lang_combo.addItem('简体中文 (Simplified Chinese)', 'zh')
        cur_pref = self.lang_pref or self.config.get('language', 'auto')
        idx = self.lang_combo.findData(cur_pref)
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)
        def on_lang_change(index):
            val = self.lang_combo.currentData()
            self.setting('language', val)
            set_language(val)
            self.notify(tr('界面语言已更新，部分设置重启后生效'))
        self.lang_combo.currentIndexChanged.connect(on_lang_change)
        self.lang_combo.setMinimumWidth(180)
        general.add_row(AppleRow('globe', (TOKENS['purple'], TOKENS['accent_lo']), tr('界面语言 / Language'), tr('界面语言与国际化设置'), self.lang_combo))

        help_btn = button(tr('查阅指南 ›'), self.show_help, pill=True)
        general.add_row(AppleRow('help', (TOKENS['accent'], TOKENS['accent_lo']), tr('使用指南与硬件支持'), tr('查阅全型号支持与高级特性说明'), help_btn))
        grid.addWidget(general, 1, 0)

        # 4. 常驻服务与自启
        background = AppleGroup()
        hdr_bg = QLabel('  04 // RUNTIME & DAEMON · ' + tr('系统服务与开机启动'))
        hdr_bg.setObjectName('eyebrow')
        hdr_bg.setStyleSheet(f"color: {TOKENS['cyan']}; font-size: 10.5px; font-weight: 700; padding: 8px 16px 2px 16px;")
        background.vbox.addWidget(hdr_bg)

        ag_row = QWidget()
        ag_layout = QHBoxLayout(ag_row)
        ag_layout.setContentsMargins(14, 8, 14, 8)
        ag_layout.setSpacing(12)
        ag_layout.addWidget(SquircleBadge('cpu', (TOKENS['cyan'], TOKENS['accent_lo'])))
        ag_tv = QVBoxLayout()
        ag_tv.setContentsMargins(0, 0, 0, 0)
        ag_tv.setSpacing(2)
        ag_title = label(tr('常驻映射监听进程 (Agent)'), 'section')
        ag_tv.addWidget(ag_title)
        ag_desc = label(tr('后台超低延迟按键拦截与手势守护服务'), 'muted')
        ag_desc.setObjectName('caption')
        ag_tv.addWidget(ag_desc)
        ag_layout.addLayout(ag_tv, 1)

        self.agent_status = Indicator()
        self.agent_status.setText(tr('正在连接'))
        ag_layout.addWidget(self.agent_status)
        self.agent_toggle = button(tr('停止服务'), self.toggle_agent, pill=True)
        ag_layout.addWidget(self.agent_toggle)
        background.add_row(ag_row)

        self.autostart = Toggle(tr('启用'))
        self.autostart.setChecked(autostart_enabled())
        self.autostart.toggled.connect(self.toggle_autostart)
        background.add_row(AppleRow('autostart', (TOKENS['green'], TOKENS['green']), tr('系统开机自动启动'), tr('Windows 登录后在后台安静自启运行'), self.autostart))
        grid.addWidget(background, 1, 1)

        # 5. 4K 极清硬件加速回放录制 (HEVC / AV1 Replay Buffer)
        replay_group = AppleGroup()
        hdr_replay = QLabel('  05 // 4K INSTANT REPLAY BUFFER · ' + tr('HEVC / AV1 标杆极清即时回放'))
        hdr_replay.setObjectName('eyebrow')
        hdr_replay.setStyleSheet(f"color: {TOKENS['purple']}; font-size: 10.5px; font-weight: 700; padding: 8px 16px 2px 16px;")
        replay_group.vbox.addWidget(hdr_replay)

        self.replay_toggle = Toggle(tr('启用'))
        self.replay_toggle.setChecked(bool(self.config.get('replay_buffer_enabled', False)))
        def on_replay_toggle(enabled):
            self.setting('replay_buffer_enabled', enabled)
            self._update_replay_hud()
        self.replay_toggle.toggled.connect(on_replay_toggle)
        replay_group.add_row(AppleRow('wave', (TOKENS['purple'], TOKENS['accent_lo']), tr('4K 极清回放缓存'), tr('开启后长按 Create 键保存本地极清 MP4（若关闭则联动系统 Game Bar）'), self.replay_toggle))

        rep_min_row = QWidget()
        rm_layout = QHBoxLayout(rep_min_row)
        rm_layout.setContentsMargins(14, 8, 14, 8)
        rm_layout.setSpacing(12)
        rm_layout.addWidget(SquircleBadge('timer', (TOKENS['accent'], TOKENS['accent_lo'])))
        rm_tv = QVBoxLayout()
        rm_tv.setContentsMargins(0, 0, 0, 0)
        rm_tv.setSpacing(2)
        rm_title = label(tr('最大回看时间 (纯内存滑动窗口)'), 'section')
        rm_tv.addWidget(rm_title)
        rm_desc = label(tr('100% 纯内存环形缓冲区保留的最长历史片段（零磁盘写入损耗）'), 'muted')
        rm_desc.setObjectName('caption')
        rm_tv.addWidget(rm_desc)

        cur_min = int(self.config.get('replay_buffer_minutes', 5))
        self.replay_min_label = label(f"{cur_min} " + tr('分钟'))
        self.replay_min_label.setStyleSheet(f"background: transparent; font: 12px 'Cascadia Code', monospace; font-weight: 700; color: {TOKENS['accent']}; padding: 0 2px;")
        
        self.replay_slider = QSlider(Qt.Horizontal)
        self.replay_slider.setRange(1, 10)
        self.replay_slider.setValue(cur_min)
        rm_tv.addWidget(self.replay_slider)
        rm_layout.addLayout(rm_tv, 1)

        def on_min_change(v):
            self.replay_min_label.setText(f"{v} " + tr('分钟'))
            self.setting('replay_buffer_minutes', v)
            self._update_replay_hud()
        self.replay_slider.valueChanged.connect(on_min_change)
        replay_group.add_row(rep_min_row)

        codec_row = QWidget()
        cd_layout = QHBoxLayout(codec_row)
        cd_layout.setContentsMargins(14, 8, 14, 8)
        cd_layout.setSpacing(12)
        cd_layout.addWidget(SquircleBadge('cpu', (TOKENS['green'], TOKENS['accent_lo'])))
        cd_tv = QVBoxLayout()
        cd_tv.setContentsMargins(0, 0, 0, 0)
        cd_tv.setSpacing(2)
        cd_title = label(tr('硬件编码器与画质方案'), 'section')
        cd_tv.addWidget(cd_title)
        self.replay_codec_desc = label(tr('选择显卡硬件加速格式与码率'), 'muted')
        self.replay_codec_desc.setObjectName('caption')
        cd_tv.addWidget(self.replay_codec_desc)
        cd_layout.addLayout(cd_tv, 1)

        self.replay_codec_combo = QComboBox()
        self.replay_codec_combo.addItem(tr('HEVC 标杆极清 (推荐 · 50Mbps)'), 'hevc')
        self.replay_codec_combo.addItem(tr('AV1 次世代极清 (AMF/NVENC · 45Mbps)'), 'av1')
        self.replay_codec_combo.addItem(tr('H.264 兼容模式 (60Mbps)'), 'h264')
        cur_codec = self.config.get('replay_codec', 'hevc')
        self.replay_codec_combo.setCurrentIndex(max(0, self.replay_codec_combo.findData(cur_codec)))
        def on_codec_change():
            val = self.replay_codec_combo.currentData()
            self.setting('replay_codec', val)
            self._update_replay_hud()
        self.replay_codec_combo.currentIndexChanged.connect(on_codec_change)
        cd_layout.addWidget(self.replay_codec_combo)
        replay_group.add_row(codec_row)

        hud_row = QWidget()
        hud_layout = QHBoxLayout(hud_row)
        hud_layout.setContentsMargins(14, 8, 14, 8)
        hud_layout.setSpacing(12)
        hud_layout.addWidget(SquircleBadge('controller', (TOKENS['amber'], TOKENS['accent_lo'])))
        
        self.replay_hud_label = label(tr('正在探测硬件加速状态...'), 'muted')
        self.replay_hud_label.setStyleSheet(f"font: 11.5px 'Cascadia Code', monospace; color: {TOKENS['ink_2']}; font-weight: 600;")
        hud_layout.addWidget(self.replay_hud_label, 1)

        save_rep_btn = button(tr('立即保存当前回放'), self.trigger_manual_replay, pill=True)
        hud_layout.addWidget(save_rep_btn)
        replay_group.add_row(hud_row)

        grid.addWidget(replay_group, 2, 0, 1, 2)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)
        grid.setRowStretch(2, 1)
        self._update_replay_hud()
        self.stack.addWidget(scroll(page))

    def _update_replay_hud(self):
        try:
            from .replay_service import calculate_estimated_ram_gb
            minutes = int(self.config.get('replay_buffer_minutes', 5))
            codec = self.config.get('replay_codec', 'hevc')
            bitrate = int(self.config.get('replay_bitrate_mbps', 50))
            ram_gb = calculate_estimated_ram_gb(minutes, bitrate)
            replay_status = self.client.status.get('replay', {}) if self.remote else {}
            enc = replay_status.get('encoder') or ('待启动' if get_language() == 'zh' else 'Not started')
            enabled = self.config.get('replay_buffer_enabled', False)
            if not enabled:
                status_text = '回放已关闭' if get_language() == 'zh' else 'Replay disabled'
            elif replay_status.get('running'):
                status_text = '回放录制中' if get_language() == 'zh' else 'Replay recording'
            else:
                status_text = '等待录制启动' if get_language() == 'zh' else 'Waiting for recording'
            overhead_txt = f"{minutes} " + tr('分钟') + ("纯内存预估" if get_language() == 'zh' else " est. RAM")
            hw_core = "硬件核心" if get_language() == 'zh' else "GPU Engine"
            self.replay_hud_label.setText(
                f"{status_text}  |  {codec.upper()} {bitrate}Mbps  |  {overhead_txt}: ~{ram_gb:.2f} GB (0 磁盘损耗)  |  {hw_core}: {enc}"
            )
        except Exception:
            pass

    def trigger_manual_replay(self):
        if self.remote:
            self.client.send('save_replay')
            self.notify(tr("正在生成 4K 极清精彩回放录像..."))
        else:
            if hasattr(self, 'replay_engine'):
                if not self.replay_engine.is_running():
                    self.replay_engine.start()
                path = self.replay_engine.save_replay()
                if path:
                    self.notify(f"🎬 {tr('精彩回放已保存')}: {Path(path).name}")
                    return
            self.actions.shortcut('Win+Alt+G')
            self.notify(tr("已触发系统回放录制 (Win+Alt+G)"))

    def test_haptic_pattern(self, pattern: str):
        if self.remote:
            self.client.send('test_haptics', pattern=pattern)
        else:
            if hasattr(self, 'haptic_engine'):
                self.haptic_engine.trigger_feedback(pattern)
        names = {'shutter': '快门触觉微脉冲', 'impact': '重度撞击阻尼', 'heartbeat': '心跳仿真律动'}
        self.notify(tr('已触发触觉波形：') + tr(names.get(pattern, pattern)))

    def on_toggle_gamebar_shield(self, checked: bool):
        from .gamebar_shield import set_gamebar_shield
        if self.remote:
            result = request(self.store.root, 'set_gamebar_shield', enabled=checked, timeout=4000)
            if result and result.get('ok') and 'applied' in result:
                msg = result['message']
                latest = ConfigStore(self.store.root)
                self.store.data.clear(); self.store.data.update(latest.data)
                self.store._baseline = copy.deepcopy(latest.data)
            else:
                msg = tr('后台未能应用设置，请重启后台后重试')
        else:
            _, msg = set_gamebar_shield(self.store, checked)
        self.notify(msg)
        enabled = self.config.get('gamebar_shield_enabled', False)
        self.gamebar_shield_box.blockSignals(True)
        self.gamebar_shield_box.setChecked(enabled)
        self.gamebar_shield_box.blockSignals(False)

        # 保持虚拟键鼠工作台内的快捷开关联动同步
        if hasattr(self, 'virtual_kbm_page') and hasattr(self.virtual_kbm_page, 'gamebar_shield_toggle'):
            self.virtual_kbm_page.gamebar_shield_toggle.blockSignals(True)
            self.virtual_kbm_page.gamebar_shield_toggle.setChecked(self.config.get('gamebar_shield_enabled', False))
            self.virtual_kbm_page.gamebar_shield_toggle.blockSignals(False)

    def show_help(self):
        title = tr('使用指南')
        if get_language() == 'en':
            msg = (
                "Select a gamepad in the Controller Library. GamePad Studio maps one active controller at a time, "
                "releasing pressed keys upon switching or disconnecting.\n\n"
                "• PlayStation: Create / Share takes instant screenshots.\n"
                "• Xbox: Dedicated Share button captures screenshots; if absent, native input is preserved.\n"
                "• Switch: Capture button takes screenshots (or − minus if unmapped).\n\n"
                "Profiles are preserved per controller family. Click any controller button or key tile, then click Edit to configure.\n\n"
                "The background daemon runs on startup and mappings persist after closing this window. "
                "Virtual KBM and keyboard mappings do not block native input unless hardware cloaking (HidHide) is active."
            )
        else:
            msg = (
                "在手柄图库中选择设备。当前一次只为所选手柄执行映射，切换或断开时释放按键。\n\n"
                "PS：Create / Share 截图。Xbox：独立 Share 截图；未提供 Share 时保留原始按键，可自定义映射。Switch：优先 Capture，否则使用 −。\n\n"
                "预设按型号保存。点击映射页的手柄按键或按键列表，再点编辑。恢复默认只影响当前预设。\n\n"
                "后台随登录运行，关闭窗口不影响映射。键盘映射不屏蔽原始输入，Xbox 键的系统功能由 Windows 管理。游戏触觉和自适应扳机取决于游戏支持。\n\n"
                "产品图片来自品牌官网，通用手柄使用标注的示例机型。"
            )
        QMessageBox.information(self, title, msg)

    def select_controller(self,instance):
        try:
            self.end_learning()
            if self.remote:self.client.send('select_device',instance_id=instance)
            else:
                self.engine.reset();self.actions.release_all();self.device.select(instance);self.poll()
                if self.snapshot:self.setting('preferred_controller',self.snapshot['profile_key'])
        except (ValueError,RuntimeError) as exc:self.notify(str(exc))

    def rescan_controllers(self):
        if self.remote:self.client.send('scan')
        else:self.scan();self.poll()

    def update_controller_ui(self,state):
        family=(state.get('family','dualsense') if state else
                self.config.get('profile_families',{}).get(self.config['active_profile'],'dualsense'))
        if family not in CATALOG:family='generic'
        self.button_names=button_labels(family,state.get('controller_type',0) if state else 0)
        if state:
            self.controller_heading.setText(CATALOG[family]['name']);self.controller_heading.setToolTip(state['name'])
            if self.remote:
                latest = ConfigStore(self.store.root)
                self.store.data.clear(); self.store.data.update(latest.data)
                self.store._baseline = copy.deepcopy(latest.data)
            else:self.store.activate_controller(state);self.store.save()
        else:
            self.controller_heading.setText(CATALOG[family]['name'])
            self.controller_heading.setToolTip(tr('上次使用的型号 · 当前未连接'))
        self.art.set_family(family);self.mapping_art.set_family(family);self.photo_caption.setText(PHOTOS[family]['caption'])
        self.mapping_model.setText(CATALOG[family]['name']);self.mapping_model.setToolTip(state['name'] if state else tr('上次使用的型号 · 当前未连接'))
        for combo in (self.profile_combo,self.mapping_combo):
            combo.blockSignals(True);combo.clear();combo.addItems(self.store.profiles_for(state));combo.setCurrentText(self.config['active_profile']);combo.blockSignals(False)
        if state:
            available=set(state.get('available_buttons',BUTTONS))
        else:
            available=set(range(15))
            if family=='xbox' and '15' in self.store.mappings:available.add(15)
            elif family in ('dualsense','dualshock4'):available.update((15,20))
            elif family!='xbox':available.add(15)
        self.mapping_art.available=available
        position=0
        for i in range(self.mapping_grid.rowCount()):self.mapping_grid.setRowStretch(i,0)
        for key in button_order(family):
            box,title=self.mapping_boxes[key]
            self.mapping_grid.removeWidget(box);box.setVisible(key in available);title.setText(self.button_names[key])
            box.setAccessibleName(self.button_names[key]);box.setToolTip(self.button_names[key])
            if key in available:self.mapping_grid.addWidget(box,position//2,position%2);position+=1
        self.mapping_grid.setRowStretch((position+1)//2,1)
        self.mapping_edit.setEnabled(True);self.learn_button.setEnabled(True)
        self.select_mapping(self.selected_key if self.selected_key in available else next(iter(sorted(available)),0))
        for button in self.led_buttons:button.setEnabled(bool(state and state['led']));button.setToolTip(tr('灯条颜色') if state and state['led'] else tr('设备未提供灯条控制'))
        self.feedback_rumble.setEnabled(bool(state and state['rumble']))
        self.touch_mouse_box.setEnabled(bool(state and state.get('touchpad',False)))
        self.touch_mouse_box.setToolTip(tr('触摸板鼠标') if state and state.get('touchpad') else tr('设备未提供触摸板'))

        # Update Guide key and Touchpad row dynamically according to controller family
        if hasattr(self, 'guide_heading'):
            if family == 'xbox':
                self.guide_heading.setText(tr('Xbox 导航'))
                if hasattr(self, 'row_guide'):
                    self.row_guide.setToolTip(tr('Xbox 导航键：短按呼出 Game Bar，长按切换任务'))
            elif family == 'switch':
                self.guide_heading.setText(tr('Home 导航'))
                if hasattr(self, 'row_guide'):
                    self.row_guide.setToolTip(tr('Switch Home 键：短按返回主界面，长按快捷菜单'))
            elif family in ('dualsense', 'dualshock4'):
                self.guide_heading.setText(tr('PS 导航'))
                if hasattr(self, 'row_guide'):
                    self.row_guide.setToolTip(tr('PlayStation 系统键：呼出控制中心与多任务切换'))
            else:
                self.guide_heading.setText(tr('系统导航'))
                if hasattr(self, 'row_guide'):
                    self.row_guide.setToolTip(tr('通用手柄 Guide 键：呼出系统快捷主控'))

        # Update Eyebrow dynamically (concise serial header, zero marketing text)
        specs = {
            'dualsense': 'SONY PLAYSTATION® 5 // FIELD WORKSTATION',
            'dualshock4': 'SONY PLAYSTATION® 4 // FIELD WORKSTATION',
            'xbox': 'MICROSOFT XBOX® // FIELD WORKSTATION',
            'switch': 'NINTENDO SWITCH® // FIELD WORKSTATION',
            'generic': 'UNIVERSAL GAMEPAD // FIELD WORKSTATION'
        }
        if hasattr(self, 'controller_eyebrow'):
            self.controller_eyebrow.setText(specs.get(family, specs['generic']))
        if hasattr(self, 'controller_features'):
            self.controller_features.hide()
        if hasattr(self, 'row_touchpad'):
            has_touchpad = family in ('dualsense', 'dualshock4') or bool(state and state.get('touchpad', False))
            self.row_touchpad.setVisible(has_touchpad)
            if hasattr(self.row_touchpad, '_associated_divider') and self.row_touchpad._associated_divider:
                self.row_touchpad._associated_divider.setVisible(has_touchpad)

        self.refresh_mappings()

    def toggle_autostart(self,enabled):
        try:
            set_autostart(self.store.root,enabled)
            if enabled and self.remote and not self.client.connected:spawn(self.store.root,'--agent')
            self.notify(tr('登录启动已开启') if enabled else tr('登录启动已关闭'))
        except OSError as exc:
            self.autostart.blockSignals(True); self.autostart.setChecked(not enabled); self.autostart.blockSignals(False); self.notify(tr('设置失败：')+str(exc))

    def toggle_agent(self):
        if not self.remote:return
        if self.client.connected:self.client.send('stop')
        else:spawn(self.store.root,'--agent');self.notify(tr('后台启动中'))

    def agent_event(self,message):
        kind=message.get('type')
        if kind=='state' and hasattr(self,'replay_hud_label'):
            replay = message.get('replay', {})
            signature = (replay.get('running'), replay.get('encoder'))
            if signature != getattr(self, '_replay_hud_signature', None):
                self._replay_hud_signature = signature
                self._update_replay_hud()
        if kind=='notice' and hasattr(self,'notice'):self.notify(message['message'])
        elif kind=='capture':
            self.capture_button.setEnabled(True)
            if message.get('path'):self.captured(message['path'])
        elif kind=='replay_record':
            path = message.get('path')
            if path:
                self.notify(f"🎬 {tr('精彩回放已保存')}: {Path(path).name}")
            elif message.get("mode") == "system":
                self.notify(tr("已触发系统回放录制 (Win+Alt+G)"))
            else:
                self.notify(message.get("error") or "回放尚未就绪")
        elif kind=='buttons' and self.learn:
            valid=[b for b in message['buttons'] if b in BUTTONS]
            if valid:self.end_learning();QTimer.singleShot(0,lambda:self.edit_mapping(valid[0]))
        elif kind=='reply' and not message.get('ok',True):self.notify(message.get('error', tr('操作失败') if get_language() == 'zh' else 'Operation failed'))
        elif kind=='state' and hasattr(self, 'virtual_kbm_page') and self.virtual_kbm_page.is_capturing:
            dev = message.get('device')
            if dev:
                self.virtual_kbm_page.handle_device_input(dev)

    def add_slider(self, layout, title, key, lo, hi, scale, unit=''):
        text=label('','muted'); slider=QSlider(Qt.Horizontal); slider.setRange(lo,hi); slider.setValue(round(self.config[key]*scale))
        def display(value):
            amount=f'{round(value/scale*100)}%' if key in ('rumble','deadzone') else f'{value/scale:.2f} {unit}'
            return f'{title}   {amount}'
        def change(value):
            text.setText(display(value)); self.setting(key,value/scale)
        text.setText(display(slider.value())); slider.valueChanged.connect(change); layout.addWidget(text); layout.addWidget(slider)

    def navigate(self,index):
        headings = ['设备概览','按键配置','截图图库','硬件遥测','系统设置','控制器库','虚拟键鼠']
        self.stack.setCurrentIndex(index); self.page_heading.setText(tr(headings[index]) if index < len(headings) else 'GamePad Studio')
        for i,b in self.nav.items(): b.setChecked(i==index)
        if index==2: self.refresh_gallery()
        if hasattr(self, 'virtual_kbm_page'):
            if index == 6:
                self.virtual_kbm_page.refresh_display()
            elif self.virtual_kbm_page.is_capturing:
                self.virtual_kbm_page.cancel_capture()

    def show_home(self):
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized | Qt.WindowActive)
        self.show()
        self.raise_()
        self.activateWindow()

    def notify(self,text):
        if not text.startswith(('按下', 'Pressed', '已连接', 'Connected', '手柄已断开', 'Gamepad disconnected')):
            self.notice.setText(text);self.notice_timer.start(4500)
        self.events.insertItem(0, f'{datetime.now():%H:%M:%S}   {text}')
        while self.events.count()>100: self.events.takeItem(100)
        self.log_rows.append(dict(time=datetime.now().isoformat(),message=text))
        with (self.store.root/'events.jsonl').open('a',encoding='utf-8') as file: file.write(json.dumps(self.log_rows[-1],ensure_ascii=False)+'\n')

    def setting(self,key,value):
        self.config[key]=value; self.store.save()
        if self.remote:self.client.send('reload')
        if key=='long_press': self.engine.threshold=value
        if key=='touch_mouse': self.last_touch=None

    def scan(self):
        try: self.device.scan()
        except Exception as exc: self.notify(tr('设备扫描失败：')+str(exc))

    def testing_protected(self):
        return self.stack.currentIndex()==3 and self.isVisible() and not self.isMinimized() and self.tester.protect.isChecked()

    def preview_requested(self):
        return self.stack.currentIndex() in (1,6) and self.isActiveWindow() and self.virtual_kbm_page.preview_toggle.isChecked()

    def poll(self):
        try:
            state=self.device.read(); self.snapshot=state; connected=state is not None
            identity=(state.get('instance_id'),state.get('family')) if state else None
            profile_changed=self.remote and (self.client.status.get('profile',self.config['active_profile'])!=self.config['active_profile'] or self.client.status.get('mapping_revision',0)!=self.config.get('mapping_revision',0))
            if profile_changed:
                latest=ConfigStore(self.store.root);self.store.data.clear();self.store.data.update(latest.data);self.store._baseline=copy.deepcopy(latest.data)
            if identity!=self.device_identity or profile_changed:
                self.engine.reset();self.actions.release_all();self.last_buttons=set();self.device_identity=identity
                self.update_controller_ui(state)
            self.controllers.set_devices(self.device.available,state.get('instance_id') if state else None)
            if self.remote:
                online=self.client.connected; self.enabled=self.client.status.get('enabled',False)
                self.agent_status.setText(tr('运行中') if online else tr('已停止')); self.agent_toggle.setText(tr('停止后台') if online else tr('启动后台'))
                self.pause_button.setEnabled(online);self.pause_button.setText(tr('暂停映射') if self.enabled else tr('恢复映射'));self.pause_button.set_symbol('pause' if self.enabled else 'play')
                self.capture_button.setEnabled(online and not self.client.status.get('capturing',False))
                preview = self.preview_requested()
                now=time.monotonic()
                if online and (preview != getattr(self,'last_preview',False) or (preview and now-getattr(self,'preview_lease_time',0)>.25)):
                    self.client.send('preview',seconds=.75 if preview else 0)
                    self.preview_lease_time=now;self.last_preview=preview
                is_kbm_capturing = hasattr(self, 'virtual_kbm_page') and getattr(self.virtual_kbm_page, 'is_capturing', False)
                if online and (self.learn or is_kbm_capturing or QApplication.activeModalWidget() is not None or self.testing_protected()):
                    now=time.monotonic()
                    if now-getattr(self,'last_suspend',0)>.5:
                        self.client.send('suspend',seconds=2);self.last_suspend=now
            if connected != self.previous_connected:
                self.engine.reset(); self.actions.release_all(); self.last_touch=None
                self.previous_connected=connected
                self.notify(tr('已连接 ') + state['name'] if connected else tr('手柄已断开，等待重新连接'))
                if connected and state['led'] and not self.remote: self.device.led(self.config['led'])
            self.status_badge.setText(tr('●  已连接') if connected else tr('○  等待连接'))
            self.side_status.setText(tr('●  已连接') if connected else tr('○  未连接'))
            self.rumble_button.setEnabled(bool(state and state['rumble']))
            self.tester.update_state(state,collect=self.stack.currentIndex()==3 and self.isVisible() and not self.isMinimized())
            self.mapping_art.update_state(state)
            if hasattr(self, 'matrix'):
                if state:
                    p_simple = {-1:'未报告',0:'极低',1:'低',2:'中等',3:'充足',4:'外接'}.get(state.get('power'), '未知') if get_language() == 'zh' else {-1:'N/A',0:'CRIT',1:'LOW',2:'MED',3:'FULL',4:'EXT'}.get(state.get('power'), 'UNK')
                    self.matrix.set_lines([
                        ('DEVICE', state['name'][:12].upper()),
                        ('POWER', p_simple),
                        ('STATUS', 'ONLINE'),
                        ('INPUTS', f"{len(state.get('buttons', []))} ACTIVE")
                    ])
                else:
                    self.matrix.set_lines([
                        ('LINK', 'OFFLINE'),
                        ('POWER', 'STANDBY'),
                        ('POLL', '1000 HZ'),
                        ('STATUS', 'WAITING')
                    ])
            if self.remote:
                feedback = self.client.status.get('mapping', {}) if self.client.connected else {}
            else:
                feedback = self.engine.feedback() if state else {}
            self.virtual_kbm_page.set_device_state(state)
            self.virtual_kbm_page.update_feedback(feedback, bool(state), self.client.status.get('suspended',False) if self.remote else QApplication.activeModalWidget() is not None)
            self.binding_list.feedback(feedback)
            events = feedback.get('events',[])
            if events:
                from .mapping_engine import trigger_label
                last=events[-1]
                message=trigger_label(last['trigger'],(state or {}).get('family','dualsense'))+' '+('长按' if last.get('gesture')=='long' else '短按')+' → '+last.get('action','')
                message=('安全试按 · ' if feedback.get('preview') else '')+message
                self.mapping_feedback.setText(message); self.home_input_feedback.setText(message)
            elif not state:
                self.mapping_feedback.setText('等待输入'); self.home_input_feedback.setText('等待输入')
            if not state:
                self.engine.update(None, self.config, enabled=False)
                self.device_details.setText(tr('未连接')); self.power_label.setText(tr('未连接')); self.engine.reset(); self.last_buttons=set()
                return
            power={-1:tr('电量未报告'),0:tr('电量极低'),1:tr('电量低'),2:tr('电量中等'),3:tr('电量充足'),4:tr('外接供电')}
            p_text = power.get(state['power'], tr('电量未知'))
            self.device_details.setText(p_text); self.power_label.setText(p_text)
            buttons=set(state['buttons']); new=buttons-self.last_buttons; self.last_buttons=buttons
            if hasattr(self, 'virtual_kbm_page'):
                self.virtual_kbm_page.set_device_state(state)
            if hasattr(self, 'virtual_kbm_page') and self.virtual_kbm_page.is_capturing:
                self.virtual_kbm_page.handle_device_input(state)
                return
            if self.learn and new and not self.remote:
                self.end_learning();number=min(new)
                if number in BUTTONS: QTimer.singleShot(0,lambda:self.edit_mapping(number))
                return
            if not self.remote and self.enabled and not self.learn and not self.testing_protected() and QApplication.activeModalWidget() is None:
                self.engine.update(state, self.config, preview=self.preview_requested())
            else:
                self.engine.reset()
                self.last_touch=None
            if new:
                self.notify(tr('按下 ') + ' / '.join(self.button_names.get(k,str(k)) for k in sorted(new)))
        except Exception as exc:
            self.enabled=False
            try: self.engine.reset(); self.actions.release_all()
            except Exception: pass
            self.pause_button.setText(tr('恢复映射'));self.pause_button.set_symbol('play'); self.notify(tr('映射已暂停：')+str(exc))

    def dispatch(self,binding,down=True):
        action=binding.get('action','none')
        if action=='hold': self.actions.hold(binding.get('value',''),down); return
        if not down or action=='none': return
        if action=='capture': self.capture()
        elif action=='replay_record': self.trigger_manual_replay()
        elif action in ('gallery','home'):
            self.navigate(2 if action=='gallery' else 0); self.show_home()
        elif action=='shortcut': self.actions.shortcut(binding['value'])
        elif action=='launch': launch_command(binding['executable'],binding.get('arguments',''))
        else: self.actions.media(action)

    def toggle_pause(self):
        if self.remote:
            self.client.send('pause' if self.enabled else 'resume');return
        enable=not self.enabled
        self.enabled=False;self.config['mapping_enabled']=False;self.store.save()
        try:self.engine.reset()
        finally:self.actions.release_all()
        self.config['mapping_enabled']=enable;self.store.save();self.enabled=enable
        self.pause_button.setText(tr('暂停映射') if self.enabled else tr('恢复映射'));self.pause_button.set_symbol('pause' if self.enabled else 'play'); self.notify(tr('映射已恢复') if self.enabled else tr('映射已暂停 · 设备监测继续运行'))

    def change_profile(self,name):
        if not name or name not in self.config['profiles'] or name == self.config['active_profile']: return
        if self.mapping_change({'op': 'select', 'profile': name}):
            if not self.snapshot: self.update_controller_ui(None)

    def duplicate_profile(self):
        name,ok=QInputDialog.getText(self,tr('另存为预设'),tr('配置名称'))
        if ok and name.strip():
            name=name.strip()
            if name in self.config['profiles']: QMessageBox.warning(self,tr('名称重复'),tr('请使用不同的配置名称。')); return
            self.mapping_change({'op':'create', 'profile':name})

    def reset_profile(self):
        if not self.snapshot:return
        name=self.config['active_profile']
        msg = f"恢复“{name}”的默认映射？" if get_language() == 'zh' else f"Reset default mappings for '{tr_profile(name)}'?"
        if QMessageBox.question(self,tr('恢复默认'),msg)!=QMessageBox.Yes:return
        self.engine.reset();self.actions.release_all()
        self.mapping_change({'op':'reset', 'profile':name})

    def delete_profile(self, *args):
        name = self.config['active_profile']
        family_profiles = self.store.profiles_for(self.snapshot)
        if len(family_profiles) <= 1:
            QMessageBox.information(
                self, tr('无法删除配置'),
                tr('当前设备至少需要保留一个配置预设（当前为“{name}”）。\n\n如需重置按键设定，请点击“恢复默认配置”；如需建立新配置，请点击“另存为新预设”。', name=tr_profile(name))
            )
            return
        reply = QMessageBox.question(
            self, tr('删除配置预设'),
            tr('确定要永久删除配置预设“{name}”吗？\n删除后不可恢复。', name=tr_profile(name)),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        self.mapping_change({'op':'delete', 'profile':name})

    @staticmethod
    def compact_binding(action):
        kind=action.get('action','none')
        if kind in ('mouse_hold','mouse_click','wheel'):return binding_label(action)
        if kind in ('shortcut','hold'):return action.get('value','')
        base = {'none':'原始输入','capture':'截图','gallery':'图库','home':'控制中心',
                'replay_record':'回放录制','record_toggle':'录屏'}.get(kind,ACTION_NAMES.get(kind,'原始输入'))
        return tr(base)

    def select_mapping(self,key):
        self.selected_key=key
        for number,(box,_) in self.mapping_boxes.items():box.setChecked(number==key)
        self.mapping_art.select_button(key)
        self.selected_label.setText(self.button_names.get(key,str(key)))
        self.mapping_edit.setText(tr('编辑 ') + self.button_names.get(key,str(key)))
        entry=effective_mappings(self.config, self.snapshot).get(str(key),{})
        self.mapping_short.setText(self.compact_binding(entry.get('short',{})))
        self.mapping_long.setText(self.compact_binding(entry.get('long',{})))

    def refresh_mappings(self):
        self.mapping_profile.setText(tr_profile(self.config['active_profile']))
        for combo in (self.profile_combo,self.mapping_combo):
            combo.blockSignals(True);combo.clear();combo.addItems(self.store.profiles_for(self.snapshot));combo.setCurrentText(self.config['active_profile']);combo.blockSignals(False)
        if hasattr(self, 'delete_profile_btn'):
            can_del = len(self.store.profiles_for(self.snapshot)) > 1
            self.delete_profile_btn.setEnabled(True)
            del_tip = (tr('删除当前配置预设：') + tr_profile(self.config.get("active_profile", ""))) if can_del else (tr('当前控制器仅剩此一个预设，点击查看说明') if get_language() == 'zh' else 'Only one profile remaining for this controller; click for details')
            self.delete_profile_btn.setToolTip(del_tip)
        if hasattr(self, 'virtual_kbm_page'): self.virtual_kbm_page.refresh_display()
        self.binding_list.refresh()
        compact=self.compact_binding
        for key,info in self.mapping_labels.items():
            entry=effective_mappings(self.config, self.snapshot).get(str(key),{})
            short=compact(entry.get('short',{}));long=compact(entry.get('long',{}))
            original=short==long==tr('原始输入');info.setVisible(not original)
            info.setText('' if original else f'{short} / {long}')
            info.setToolTip(f"{tr('短按')}：{short}\n{tr('长按')}：{long}")
            self.mapping_boxes[key][0].setToolTip(self.button_names.get(key,str(key))+'\n'+info.toolTip())
        self.select_mapping(self.selected_key)
        if hasattr(self, 'row_guide'):
            guide=effective_mappings(self.config,self.snapshot).get('5',{})
            self.row_guide.setToolTip('短按：'+compact(guide.get('short',{}))+' / 长按：'+compact(guide.get('long',{})))
        family=(self.snapshot.get('family','dualsense') if self.snapshot else
                self.config.get('profile_families',{}).get(self.config['active_profile'],'dualsense'))
        available=(self.snapshot.get('available_buttons') if self.snapshot else
                   [key for key,(box,_) in self.mapping_boxes.items() if not box.isHidden()])
        key=capture_button(family,available)
        self.capture_heading.setText(self.button_names[key] if key is not None else tr('截图'))
        entry=effective_mappings(self.config, self.snapshot).get(str(key),{}) if key is not None else {}
        tip = f"{self.button_names.get(key, tr('截图'))}：{tr('短按')} {compact(entry.get('short',{}))} / {tr('长按')} {compact(entry.get('long',{}))}" if key is not None else (tr('驱动未提供 Share，可在映射中自定义截图按键') if get_language() == 'zh' else 'Share button unavailable in driver; custom shortcut configurable in mapping')
        if hasattr(self, 'row_capture'):
            self.row_capture.setToolTip(tip)
        if hasattr(self, 'create_hint'):
            self.create_hint.setText(compact(entry.get('short',{}))+'  /  '+compact(entry.get('long',{})) if key is not None else (tr('未设置') if get_language() == 'zh' else 'Not Configured'))
            self.create_hint.setToolTip(tip)
        if hasattr(self, 'capture_action_btn'):
            if key is not None:
                self.capture_action_btn.setText(tr('查看图库 ›'))
                try: self.capture_action_btn.clicked.disconnect()
                except Exception: pass
                self.capture_action_btn.clicked.connect(lambda: self.navigate(2))
            else:
                self.capture_action_btn.setText(tr('配置按键 ›'))
                try: self.capture_action_btn.clicked.disconnect()
                except Exception: pass
                self.capture_action_btn.clicked.connect(lambda: self.navigate(1))

    def start_learning(self):
        self.edit_mapping('0', new=True, capture=True)

    def end_learning(self):
        self.learn=False;self.learn_button.setChecked(False); self.learn_button.setText(tr('识别手柄按键'))

    def mapping_change(self, change):
        try:
            if self.remote:
                result = request(self.store.root, 'mapping_change', change=change)
                if not result or not result.get('ok', True) or 'config' not in result:
                    raise ValueError((result or {}).get('error', '后台未连接，修改尚未保存'))
                data = result['config']
                self.store.data.clear(); self.store.data.update(data)
                self.store._baseline = copy.deepcopy(data)
            else:
                self.engine.reset()
                self.store.apply_mapping_change(change, self.snapshot)
            self.refresh_mappings()
            self.notify('映射已保存')
            return True
        except (ValueError, OSError) as exc:
            self.notify(str(exc)); return False

    def edit_mapping(self, key, new=False, output=None, capture=False):
        if self.remote:self.client.send('suspend',seconds=2)
        self.engine.reset(); self.actions.release_all()
        mapping = {} if new else effective_mappings(self.config, self.snapshot).get(str(key), {})
        dialog = BindingDialog(self, key, mapping, output=output, new=new)
        if capture: dialog.start_capture()
        if dialog.exec() == QDialog.Accepted:
            self.mapping_change({'op':'binding', 'profile':dialog.profile,
                                 'trigger':dialog.trigger(), 'previous_trigger':None if new else str(key), 'mapping':dialog.value()})
        if self.remote:self.client.send('suspend',seconds=0)

    def capture(self):
        if self.remote:
            if not self.client.send('capture'):self.notify(tr('请先启动后台映射'))
            return
        now=time.monotonic()
        if (self.worker and self.worker.isRunning()) or now-self.last_capture<self.config['cooldown']: return
        self.last_capture=now; self.capture_button.setEnabled(False)
        self.worker=CaptureWorker(self.config['save_dir'],self.config['capture_mode']); self.worker.ready.connect(self.captured); self.worker.failed.connect(lambda error:self.notify(tr('截图失败：')+error)); self.worker.finished.connect(lambda:self.capture_button.setEnabled(True)); self.worker.start()

    def captured(self,path):
        self.notify(tr('截图已保存')); self.refresh_gallery()
        if not self.isActiveWindow(): self.tray.showMessage(tr('精彩瞬间已保存'),Path(path).name,QSystemTrayIcon.Information,1800)

    @staticmethod
    def clear_layout(layout):
        while layout.count():
            item=layout.takeAt(0)
            if item.widget():
                widget=item.widget(); widget.hide(); widget.setParent(None); widget.deleteLater()

    def thumbnail(self,row,compact=False):
        thumb_file = row.get('thumb_path', row['path'])
        pix = QPixmap(thumb_file) if Path(thumb_file).suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp') else QPixmap()
        if pix.isNull():
            pix = QPixmap(280, 150)
            pix.fill(QColor(TOKENS.get('surface_lo', TOKENS['base'])))
            painter = QPainter(pix)
            painter.setPen(QColor(TOKENS['accent']))
            font = painter.font()
            font.setPointSize(14)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(pix.rect(), Qt.AlignCenter, "🎬 4K 精彩回放" if row.get('is_video') else "🖼️ 截图")
            painter.end()

        if compact:
            box=GlassPanel(); layout=QHBoxLayout(box); layout.setContentsMargins(10,10,10,10); layout.setSpacing(12)
            image=QPushButton(); image.setFixedSize(112,64); image.setObjectName('icon')
            image.setIcon(QIcon(pix)); image.setIconSize(QSize(112,64)); image.clicked.connect(lambda:self.preview(row)); layout.addWidget(image)
            text=QVBoxLayout(); text.setSpacing(4); title=row['title']
            t_lbl = label(title, 'section'); text.addWidget(t_lbl)
            time_lbl = label(tr('拍摄于 ') + f"{row.get('created','')[11:19]}", 'muted'); time_lbl.setObjectName('caption'); text.addWidget(time_lbl)
            layout.addLayout(text,1)
            return box
        box, b = card()
        b.setContentsMargins(14, 14, 14, 14)
        b.setSpacing(10)
        image = QPushButton()
        image.setMinimumHeight(150)
        image.setObjectName('icon')
        image.setIcon(QIcon(pix))
        image.setIconSize(QSize(280, 150))
        image.clicked.connect(lambda: self.preview(row))
        b.addWidget(image)
        caption = QHBoxLayout()
        caption.addWidget(label(row['title'], 'section'), 1)
        tip = f"{row.get('created','')[:19].replace('T',' ')}"
        if row.get('is_video'):
            tip += f" · {row.get('size_mb', 0)} MB · 点击播放"
        else:
            tip += f" · {row.get('width','?')} × {row.get('height','?')}"
        image.setToolTip(tip)
        fav = IconButton('heart', tr('取消收藏') if row['favorite'] else tr('收藏'), lambda: self.favorite(row), 32)
        fav.setCheckable(True)
        fav.setChecked(row['favorite'])
        fav.setStyleSheet(f'background: {TOKENS["elevated"]}; border: 1px solid {TOKENS["border_hi"]}; border-radius: {TOKENS["r_sm"]}px;')
        caption.addWidget(fav)
        del_text = tr('删除视频') if row.get('is_video') else tr('删除截图')
        del_btn = IconButton('trash', del_text, lambda: self.delete_capture_confirm(row), 32)
        del_btn.setObjectName('icon_danger')
        del_btn.setStyleSheet(f'background: {TOKENS["elevated"]}; border: 1px solid {TOKENS["border_hi"]}; border-radius: {TOKENS["r_sm"]}px;')
        caption.addWidget(del_btn)
        b.addLayout(caption)
        return box

    def refresh_gallery(self,*args):
        try: rows=list_captures(self.config['save_dir'])
        except OSError as exc: self.notify(tr('无法读取截图目录：')+str(exc)); return
        columns=2 if self.width()<1150 else 3
        signature=(tuple((r['path'],r['favorite']) for r in rows),self.search.text(),self.only_favorites.isChecked(),columns)
        if getattr(self,'gallery_signature',None)==signature: return
        self.gallery_signature=signature
        for i in range(self.gallery_grid.rowCount()):self.gallery_grid.setRowStretch(i,0)
        for i in range(self.gallery_grid.columnCount()):self.gallery_grid.setColumnStretch(i,0)
        self.clear_layout(self.gallery_grid); self.clear_layout(self.recent_row)
        filtered=[r for r in rows if (not self.only_favorites.isChecked() or r['favorite']) and self.search.text().lower() in (r['title']+r['path']).lower()]
        self.gallery_info.setText(f"{len(rows)} " + tr('张'))
        for i,row in enumerate(filtered[:180]): self.gallery_grid.addWidget(self.thumbnail(row),i//columns,i%columns)
        if not filtered:
            empty = label(tr('暂无截图'), 'muted')
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(f"color: {TOKENS['ink_3']}; font-size: 15px; font-weight: 500;")
            self.gallery_grid.addWidget(empty, 0, 0, 1, columns, Qt.AlignCenter)
            for i in range(columns):
                self.gallery_grid.setColumnStretch(i, 1)
            self.gallery_grid.setRowStretch(0, 1)
        else:
            for i in range(columns):
                self.gallery_grid.setColumnStretch(i, 1)
            self.gallery_grid.setRowStretch((min(len(filtered), 180) + columns - 1) // columns, 1)
        for row in rows[:2]: self.recent_row.addWidget(self.thumbnail(row,True),1)
        if not rows:
            r_empty = label(tr('暂无截图'), 'muted')
            r_empty.setAlignment(Qt.AlignCenter)
            self.recent_row.addWidget(r_empty, 1)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'recent_row'):QTimer.singleShot(0,self.refresh_gallery)

    def favorite(self,row):
        try: set_favorite(row['path'],not row['favorite']); self.refresh_gallery()
        except OSError as exc: self.notify(tr('收藏失败：')+str(exc))

    def preview(self, row):
        if row.get('is_video'):
            QDesktopServices.openUrl(QUrl.fromLocalFile(row['path']))
            self.notify(f"🎬 {tr('已调用系统播放器播放精彩回放视频')}: {Path(row['path']).name}")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(tr('截图预览'))
        dialog.resize(960, 680)
        layout = QVBoxLayout(dialog)
        image = QLabel()
        image.setAlignment(Qt.AlignCenter)
        pix = QPixmap(row['path'])
        image.setPixmap(pix.scaled(920, 530, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        layout.addWidget(image, 1)
        layout.addWidget(label(row['title'], 'section'))
        layout.addWidget(label(Path(row['path']).name, 'muted'))
        bar = QHBoxLayout()
        bar.addWidget(button(tr('复制图像'), lambda: QApplication.clipboard().setPixmap(pix), pill=True))
        bar.addWidget(button(tr('打开原图'), lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(row['path'])), pill=True))
        del_btn = button(tr('删除截图'), lambda: self.delete_capture_confirm(row, on_deleted=dialog.accept), danger=True, icon='trash')
        bar.addWidget(del_btn)
        bar.addStretch()
        bar.addWidget(button(tr('关闭'), dialog.accept, pill=True))
        layout.addLayout(bar)
        dialog.exec()

    def delete_capture_confirm(self, row, on_deleted=None):
        if not isinstance(row, dict) or 'path' not in row:
            return
        name = Path(row['path']).name
        title = row.get('title', name)
        is_vid = bool(row.get('is_video'))
        item_type = tr('回放视频') if is_vid else tr('截图')
        reply = QMessageBox.question(
            self,
            tr('删除{type}', type=item_type),
            tr('确定要永久删除{type} "{title}" 吗？\n文件：{name}', type=item_type, title=title, name=name),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            try:
                if delete_capture(row['path']):
                    self.gallery_signature = None
                    self.refresh_gallery()
                    self.notify(tr('{type}已删除', type=item_type))
                    if on_deleted:
                        on_deleted()
                else:
                    self.notify(tr('截图文件不存在或已被删除'))
            except OSError as exc:
                self.notify(tr('删除失败：') + str(exc))

    def clean_unfavorited_captures(self, *args):
        try:
            rows = list_captures(self.config['save_dir'])
        except OSError as exc:
            self.notify(tr('无法读取截图目录：') + str(exc))
            return
        unfavorited = [r for r in rows if not r.get('favorite')]
        if not unfavorited:
            self.notify(tr('没有可清理的未收藏截图'))
            return
        reply = QMessageBox.question(
            self,
            tr('清理未收藏截图'),
            tr('确定要清理所有未收藏的截图吗？\n将永久删除 {count} 张截图，已收藏的 {retained} 张截图将被保留。', count=len(unfavorited), retained=len(rows) - len(unfavorited)),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            count = 0
            for r in unfavorited:
                try:
                    if delete_capture(r['path']):
                        count += 1
                except OSError:
                    pass
            self.gallery_signature = None
            self.refresh_gallery()
            self.notify(tr('已清理 {count} 张未收藏截图', count=count))

    def open_capture_folder(self):
        folder=Path(self.config['save_dir']); folder.mkdir(parents=True,exist_ok=True); QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def choose_folder(self):
        path=QFileDialog.getExistingDirectory(self,tr('截图保存位置'),self.config['save_dir'])
        if path: self.setting('save_dir',path); self.folder_label.setText(path);self.folder_button.setText(tr('截图位置：')+path); self.gallery_signature=None; self.refresh_gallery(); self.notify(tr('截图目录已更新'))

    def rumble(self):
        if self.remote:self.client.send('rumble',strength=self.config['rumble']);return
        self.notify(tr('已发送 350 ms 振动测试') if self.device.rumble(self.config['rumble']) else tr('当前设备暂不支持振动或尚未连接'))

    def test_rumble(self,strength):
        if self.remote:self.client.send('rumble',strength=strength)
        else:self.device.rumble(strength)

    def set_led(self,color):
        self.art.set_led(color); self.mapping_art.set_led(color)
        for b in getattr(self, 'led_buttons', []):
            if hasattr(b, 'set_selected'):
                b.set_selected(getattr(b, 'color', None) == color)
        if self.remote:
            self.setting('led',color);self.client.send('led',color=color);return
        if self.device.led(color):
            self.setting('led',color); self.notify(tr('灯条颜色已更新'))
        else: self.notify(tr('当前设备暂不支持灯条控制或尚未连接'))

    def closeEvent(self,event):
        close_to_tray = bool(self.config.get('close_to_tray', False)) and not getattr(self, 'quitting', False)
        if close_to_tray and self.tray.isVisible():
            self.hide()
            self.notify(tr('GamePad Studio 已最小化至系统托盘'))
            event.ignore()
            return
        self.cleanup();event.accept()

    def cleanup(self):
        if self.closed:return
        self.closed=True
        self.timer.stop(); self.scan_timer.stop(); self.gallery_timer.stop()
        self.notice_timer.stop();self.events.close()
        if self.remote:
            if self.client and self.client.connected:
                try: self.client.send('preview', seconds=0)
                except Exception: pass
                try: self.client.send('stop')
                except Exception: pass
            try:
                request(self.store.root, 'stop', timeout=600)
            except Exception:
                pass
        self.engine.reset(); self.actions.release_all()
        self.engine.close()
        self.device.close(); self.tray.hide()
        if self.worker and self.worker.isRunning(): self.worker.wait()

    def quit_app(self):
        self.quitting=True; self.close(); QApplication.quit()


def run():
    pages={'home':0,'mappings':1,'gallery':2,'input':3,'settings':4,'controllers':5,'keyboard':6}
    parser=argparse.ArgumentParser(description=tr('GamePad Studio 手柄管理软件'))
    parser.add_argument('--data-dir',type=Path,default=None)
    parser.add_argument('--cli',action='store_true',help='使用原命令行截图模式')
    parser.add_argument('--smoke-test',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--page',choices=list(pages),default='controllers')
    parser.add_argument('--lang',choices=['auto','zh','en'],default=None,help=tr('UI 界面语言选择 (auto/zh/en)'))
    args,extra=parser.parse_known_args()
    if args.cli or extra:
        from .cli import run as cli
        sys.argv=[sys.argv[0],*extra]; cli(); return
    root=(args.data_dir or default_root()).resolve()
    root.mkdir(parents=True,exist_ok=True)
    if sys.platform=='win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('GamePadStudio.ControllerManager')
    app=QApplication(sys.argv[:1]); app.setApplicationName('GamePad Studio'); app.setStyle('Fusion'); app.setStyleSheet(STYLE)
    lock=QLockFile(str(root/'studio.lock'))
    lock.setStaleLockTime(1500)
    if not lock.tryLock(100):
        # 尝试唤起已存在的前台界面
        if request(root,'show',role='ui',page=args.page,timeout=400) is not None:
            return
        # 若旧界面无响应或卡死，主动清理残留并接管
        cleanup_stale_ui(root)
        try:
            lock.removeStaleLockFile()
            (root/'studio.lock').unlink(missing_ok=True)
        except Exception:
            pass
        if not lock.tryLock(200):
            try:
                (root/'studio.lock').unlink(missing_ok=True)
            except Exception:
                pass
            lock.tryLock(300)
    window=Studio(root,standalone=args.smoke_test,lang=args.lang)
    def handle_ui(message):
        if message.get('command')=='exit':QTimer.singleShot(50,window.quit_app)
        elif message.get('command')=='show':window.navigate(pages.get(message.get('page'),0));window.show_home()
        else:raise ValueError('Unknown UI command')
        return {'pid':os.getpid(),'pages':window.stack.count(),'page':window.stack.currentIndex(),'native_glass':window.native_glass}
    ui_server=LocalServer(root,handle_ui,role='ui')
    window.navigate(pages[args.page])
    if args.smoke_test:
        window.enabled=False; window.tray.hide()
        def smoke_finish():
            photos=photo_health()
            result={'ok':all(photos.values()),'photos':photos,'device':window.snapshot,'pages':window.stack.count(),'frozen':bool(getattr(sys,'frozen',False)),'native_glass':window.native_glass,'icon':not app_icon().isNull()}
            result['input_tester']=window.tester.snapshot()
            result['mapping_ui']={'labels':window.button_names,'axes':window.tester.axis_names,
                'profile':window.config['active_profile'],'bindings':window.store.mappings,
                'visible_buttons':[key for key,(box,_) in window.mapping_boxes.items() if not box.isHidden()],
                'profile_options':[window.mapping_combo.itemText(i) for i in range(window.mapping_combo.count())]}
            (root/'smoke-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            window.grab().save(str(root/'smoke-window.png'))
            window.quit_app()
        QTimer.singleShot(1200,smoke_finish)
    window.show(); code=app.exec(); ui_server.close(); lock.unlock(); return code



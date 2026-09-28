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

from .ipc import AgentClient, RemoteDevice, LocalServer, request, spawn, default_root, autostart_enabled, set_autostart
from .studio_core import ConfigStore, GestureEngine, BUTTONS, ACTION_NAMES
from .device import Device
from .actions import WindowsActions, parse_keys, launch_command
from .kbm_mapper import NIKKI_PROFILE_NAME, NikkiKbmEngine
from .virtual_kbm import VirtualKbmEngine
from .virtual_kbm_ui import VirtualKbmPage
from .controller_photo import ControllerPhoto, ControllerInput, PHOTOS, photo_health
from .input_tester import InputTester
from .controller_catalog import CATALOG, button_labels, capture_button, button_order, controller_defaults
from .screenshot_service import take_screenshot, list_captures, set_favorite, delete_capture

from .glass import (TOKENS, tag_style, token, token_color, STYLE, GlassWindow, GlassCanvas, GlassPanel, TitleBar,
                    IconButton, Indicator, Toggle, glyph, app_icon,
                    SquircleBadge, AppleRow, AppleGroup, LedSwatch)
from .te_widgets import DotMatrixDisplay, SpeakerGrille, RotaryKnob, RockerSwitch



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


class MappingDialog(QDialog):
    def __init__(self, parent, number, mapping):
        names=getattr(parent,'button_names',BUTTONS)
        super().__init__(parent); self.setWindowTitle('编辑映射 · '+names[number]); self.setMinimumWidth(540)
        self.fields = {}
        layout=QVBoxLayout(self); layout.setContentsMargins(26,24,26,24); layout.setSpacing(16)
        layout.addWidget(label(names[number], 'heading'))
        
        for gesture, title in [('short','短按'),('long','长按')]:
            box, content=card(); content.addWidget(label(title,'section'))
            combo=QComboBox()
            for action,name in ACTION_NAMES.items():
                if gesture == 'long' and action == 'hold': continue
                combo.addItem(name,action)
            content.addWidget(combo)
            keys=QKeySequenceEdit(); keys.setMaximumSequenceLength(1); content.addWidget(keys)
            path=QLineEdit(); path.setPlaceholderText('选择应用'); content.addWidget(path)
            browse=button('选择程序…',lambda checked=False,p=path:self.browse(p)); content.addWidget(browse)
            arguments=QLineEdit(); arguments.setPlaceholderText('启动参数（可选）'); content.addWidget(arguments)
            binding=mapping.get(gesture, {'action':'none'})
            combo.setCurrentIndex(max(0,combo.findData(binding.get('action','none'))))
            keys.setKeySequence(QKeySequence(binding.get('value','')))
            path.setText(binding.get('executable','')); arguments.setText(binding.get('arguments',''))
            def update_fields(index=0,c=combo,k=keys,p=path,a=arguments,b=browse):
                k.setVisible(c.currentData() in ('shortcut','hold'))
                for widget in (p,a,b): widget.setVisible(c.currentData() == 'launch')
                self.adjustSize()
            combo.currentIndexChanged.connect(update_fields); update_fields()
            self.fields[gesture]=(combo,keys,path,arguments); layout.addWidget(box)
        self.setToolTip('键盘映射不会屏蔽游戏接收到的原始手柄输入。')
        controls=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel)
        controls.button(QDialogButtonBox.Save).setText('保存映射'); controls.button(QDialogButtonBox.Cancel).setText('取消')
        controls.accepted.connect(self.validate); controls.rejected.connect(self.reject); layout.addWidget(controls)

    def browse(self, field):
        path,_=QFileDialog.getOpenFileName(self,'选择程序','','程序 (*.exe);;所有文件 (*)')
        if path: field.setText(path)

    def validate(self):
        try:
            for combo,keys,path,args in self.fields.values():
                if combo.currentData() in ('shortcut','hold'):
                    parse_keys(keys.keySequence().toString(QKeySequence.PortableText))
                if combo.currentData() == 'launch' and not Path(path.text()).is_file():
                    raise ValueError('请选择存在的可执行文件')
            self.accept()
        except ValueError as exc: QMessageBox.warning(self,'检查映射',str(exc))

    def value(self):
        return {g:dict(action=c.currentData(),value=k.keySequence().toString(QKeySequence.PortableText),executable=p.text(),arguments=a.text()) for g,(c,k,p,a) in self.fields.items()}


class Studio(GlassWindow):
    def __init__(self, root, standalone=False):
        super().__init__()
        root=Path(root).resolve(); self.remote=not standalone; self.closed=False
        self.store=ConfigStore(root); self.config=self.store.data
        self.setWindowTitle('GamePad Studio'); self.setWindowIcon(app_icon()); self.resize(1200,780); self.setMinimumSize(960,640)
        self.client=AgentClient(root,self) if self.remote else None
        self.device=RemoteDevice(self.client) if self.remote else Device()
        if not self.remote:self.device.preferred_key=self.config.get('preferred_controller','')
        self.actions=WindowsActions(); self.engine=GestureEngine(self.dispatch,self.config['long_press'])
        self.nikki_engine=NikkiKbmEngine(self.actions,on_chord=self.notify)
        self.virtual_kbm_engine=VirtualKbmEngine(self.actions,on_notice=self.notify)
        if self.remote:
            self.client.event.connect(self.agent_event)
            if request(root,'status',timeout=150) is None: spawn(root,'--agent')
        self.snapshot=None; self.previous_connected=False; self.enabled=True; self.last_capture=0; self.worker=None
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
        brand.setToolTip('GamePad Studio · 手柄控制中心 (返回概览)')
        brand.clicked.connect(lambda: self.navigate(0))
        side.addWidget(brand, 0, Qt.AlignHCenter)

        dock = QWidget()
        dock_layout = QVBoxLayout(dock)
        dock_layout.setContentsMargins(0, 0, 0, 0)
        dock_layout.setSpacing(8)

        for i, title in [(5, '控制器库'), (0, '设备概览'), (6, '虚拟键鼠'), (1, '按键配置'), (2, '截图图库'), (3, '硬件遥测')]:
            b = IconButton({5: 'grid', 0: 'controller', 6: 'keyboard', 1: 'mapping', 2: 'photos', 3: 'wave'}[i],
                           title, lambda checked=False, j=i: self.navigate(j), 44)
            b.setObjectName('nav')
            b.setIconSize(QSize(22, 22))
            b.setCheckable(True)
            dock_layout.addWidget(b, 0, Qt.AlignHCenter)
            self.nav[i] = b

        side.addWidget(dock)
        side.addStretch()

        settings = IconButton('settings', '系统设置', lambda: self.navigate(4), 44)
        settings.setObjectName('nav')
        settings.setCheckable(True)
        side.addWidget(settings, 0, Qt.AlignHCenter)
        self.nav[4] = settings

        help_button = IconButton('help', '使用指南', self.show_help, 44)
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

        self.page_heading = label('设备概览', 'heading')
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
        self.status_badge.setText('未连接')
        capsule_layout.addWidget(self.status_badge)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.VLine)
        _border_hi = TOKENS['border_hi']
        sep1.setStyleSheet(f'color: {_border_hi}; max-height: 18px;')
        capsule_layout.addWidget(sep1)

        self.pause_button = IconButton('pause', '暂停手柄映射', self.toggle_pause, 28)
        capsule_layout.addWidget(self.pause_button)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.VLine)
        sep2.setStyleSheet(f'color: {_border_hi}; max-height: 18px;')
        capsule_layout.addWidget(sep2)

        self.capture_button = IconButton('camera', '即时截屏 (Create / F12)', self.capture, 28)
        self.capture_button.setObjectName('primary')
        self.capture_button.set_symbol('camera', '#ffffff')
        capsule_layout.addWidget(self.capture_button)
        header.addWidget(capsule)

        header.addSpacing(6)
        for symbol, title, action in [('minimize', '最小化', self.showMinimized),
                                      ('maximize', '最大化 / 还原', chrome.toggle_maximized),
                                      ('close', '关闭窗口', self.close)]:
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
        self.virtual_kbm_page = VirtualKbmPage(self.virtual_kbm_engine, store=self.store)
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
        self.controller_heading = label('DualSense 无线控制器', 'productTitle')
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
        self.power_label = label('未连接', 'muted')
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
        row_profile = AppleRow('controller', BADGE_COLOR, '配置预设', '', self.profile_combo)
        row_profile.setToolTip('按键映射方案与预设配置切换')
        right_panel.add_row(row_profile)

        self.rumble_button = button('脉冲测试', self.rumble, icon='wave', pill=True)
        self.rumble_button.setFixedSize(112, 28)
        row_rumble = AppleRow('wave', BADGE_COLOR, '触觉反馈', '', self.rumble_button)
        row_rumble.setToolTip('双马达触觉脉冲与响应测试')
        right_panel.add_row(row_rumble)

        nav_mapping_btn = button('编辑按键 ›', lambda: self.navigate(1), pill=True)
        nav_mapping_btn.setFixedSize(112, 28)
        row_map = AppleRow('mapping', BADGE_COLOR, '按键映射', '', nav_mapping_btn)
        row_map.setToolTip('自定义按键键位与长按/短按宏映射')
        right_panel.add_row(row_map)

        # Section 2: Shortcuts & Dispatch
        hdr2 = QLabel('  02 // SHORTCUT DISPATCH')
        hdr2.setObjectName('eyebrow')
        hdr2.setStyleSheet(f"color: {TOKENS['ink_3']}; font-size: 10px; font-weight: 700; letter-spacing: 0.8px; padding: 14px 16px 4px 16px;")
        right_panel.vbox.addWidget(hdr2)

        self.capture_action_btn = button('查看图库 ›', lambda: self.navigate(2), pill=True)
        self.capture_action_btn.setFixedSize(112, 28)
        self.row_capture = AppleRow('camera', BADGE_COLOR, '截图', '', self.capture_action_btn)
        self.capture_heading = self.row_capture.title_label
        self.create_hint = QLabel()
        right_panel.add_row(self.row_capture)

        self.guide_action_btn = button('系统菜单 ›', lambda: self.navigate(1), pill=True)
        self.guide_action_btn.setFixedSize(112, 28)
        self.row_guide = AppleRow('controller', BADGE_COLOR, 'Xbox 导航', '', self.guide_action_btn)
        self.guide_heading = self.row_guide.title_label
        self.guide_hint = QLabel()
        right_panel.add_row(self.row_guide)

        self.touch_action_btn = button('手势映射 ›', lambda: self.navigate(1), pill=True)
        self.touch_action_btn.setFixedSize(112, 28)
        self.row_touchpad = AppleRow('touchpad', BADGE_COLOR, '触摸板', '', self.touch_action_btn)
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
        head_v.addWidget(label('CAPTURES BUFFER // 缓冲与最近保存', 'eyebrow'))
        head_v.addWidget(label('近期截图', 'section'))
        heading.addLayout(head_v)
        heading.addStretch()
        all_btn = button('查看完整图库 ›', lambda: self.navigate(2))
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
        tools.addWidget(label('当前配置预设：', 'muted'))
        self.mapping_combo = QComboBox()
        self.mapping_combo.setMinimumWidth(210)
        self.mapping_combo.currentTextChanged.connect(self.change_profile)
        tools.addWidget(self.mapping_combo)
        tools.addStretch()
        self.learn_button = button('动态识别按键', self.start_learning, icon='controller')
        self.learn_button.setCheckable(True)
        tools.addWidget(self.learn_button)
        tools.addWidget(button('恢复默认配置', self.reset_profile, icon='refresh'))
        tools.addWidget(button('新建配置预设', self.duplicate_profile, icon='plus'))
        self.delete_profile_btn = button('删除配置预设', self.delete_profile, icon='trash')
        tools.addWidget(self.delete_profile_btn)
        layout.addLayout(tools)

        workspace = QWidget()
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
        self.mapping_art.setMinimumSize(280, 205)
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
        head_sel.addWidget(label('SELECTED INPUT // 当前按键', 'eyebrow'))
        self.selected_label = label('× 交叉', 'section')
        self.selected_label.setObjectName('productTitle')
        head_sel.addWidget(self.selected_label)
        detail.addLayout(head_sel, 1)

        self.mapping_edit = button('配置动作...', lambda: self.edit_mapping(self.selected_key), primary=True, icon='edit', pill=True)
        detail.addWidget(self.mapping_edit)
        body.addLayout(detail)

        # Direct telemetry rows (replaces nested g_card boxes)
        gestures = QHBoxLayout()
        gestures.setSpacing(18)
        self.mapping_short = label('', 'muted', True)
        self.mapping_long = label('', 'muted', True)
        for title, summary in [('SHORT PRESS // 短按', self.mapping_short), ('LONG PRESS // 长按', self.mapping_long)]:
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
        layout.addWidget(workspace, 1)
        self.stack.addWidget(page)

    def build_gallery(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.only_favorites = IconButton('heart', '仅看收藏', size=32)
        self.only_favorites.setCheckable(True)
        self.only_favorites.toggled.connect(self.refresh_gallery)
        self.only_favorites.setStyleSheet(f'background: {TOKENS["elevated"]}; border: 1px solid {TOKENS["border_hi"]}; border-radius: {TOKENS["r_sm"]}px;')

        folder_btn = IconButton('folder', '打开截图文件夹', self.open_capture_folder, 32)
        folder_btn.setStyleSheet(f'background: {TOKENS["elevated"]}; border: 1px solid {TOKENS["border_hi"]}; border-radius: {TOKENS["r_sm"]}px;')

        self.clean_captures_btn = IconButton('trash', '清理未收藏截图', self.clean_unfavorited_captures, 32)
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
        self.search.setPlaceholderText('搜索截图文件...')
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
        self.events.setWindowTitle('活动记录')
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
        hdr_cap = QLabel('  01 // CAPTURE ENGINE · 截图服务与存储')
        hdr_cap.setObjectName('eyebrow')
        hdr_cap.setStyleSheet(f"color: {TOKENS['accent']}; font-size: 10.5px; font-weight: 700; padding: 8px 16px 2px 16px;")
        cap.vbox.addWidget(hdr_cap)

        self.mode_combo = QComboBox()
        for name, value in [('当前活动显示器', 'monitor'), ('当前活动窗口', 'window'), ('全部连接显示器', 'all')]:
            self.mode_combo.addItem(name, value)
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(self.config['capture_mode'])))
        self.mode_combo.currentIndexChanged.connect(lambda: self.setting('capture_mode', self.mode_combo.currentData()))
        self.mode_combo.setMinimumWidth(150)
        cap.add_row(AppleRow('camera', (TOKENS['accent'], TOKENS['accent_lo']), '捕获目标范围', '设定活动显示器或独立活动窗口', self.mode_combo))

        folder_row = QWidget()
        f_layout = QHBoxLayout(folder_row)
        f_layout.setContentsMargins(14, 8, 14, 8)
        f_layout.setSpacing(12)
        f_layout.addWidget(SquircleBadge('folder', (TOKENS['purple'], TOKENS['accent_lo'])))
        f_v = QVBoxLayout()
        f_v.setContentsMargins(0, 0, 0, 0)
        f_v.setSpacing(2)
        f_title = label('截图存储路径', 'section')
        f_v.addWidget(f_title)
        self.folder_label = label(self.config['save_dir'], 'muted')
        self.folder_label.setObjectName('metric')
        f_v.addWidget(self.folder_label)
        f_layout.addLayout(f_v, 1)
        self.folder_button = button('更改目录...', self.choose_folder, pill=True)
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
        c_title = label('防抖冷却间隔', 'section')
        c_tv.addWidget(c_title)
        c_desc = label('连击防误触时间阈值', 'muted')
        c_desc.setObjectName('caption')
        c_tv.addWidget(c_desc)
        cool_val = label(f"{self.config['cooldown']:.2f} 秒")
        cool_val.setStyleSheet(f"background: transparent; font: 12px 'Cascadia Code', monospace; font-weight: 700; color: {TOKENS['amber']}; padding: 0 2px;")
        cool_slider = QSlider(Qt.Horizontal)
        cool_slider.setRange(10, 200)
        cool_slider.setValue(round(self.config['cooldown'] * 100))
        c_tv.addWidget(cool_slider)
        c_layout.addLayout(c_tv, 1)

        cool_knob = RotaryKnob("COOLDOWN", 0.10, 2.00, self.config['cooldown'], "s", TOKENS['amber'], 46)
        def on_cool_change(v):
            sec = v / 100
            cool_val.setText(f"{sec:.2f} 秒")
            cool_knob.setValue(sec)
            self.setting('cooldown', sec)
        cool_slider.valueChanged.connect(on_cool_change)
        cool_knob.valueChanged.connect(lambda v: (cool_slider.blockSignals(True), cool_slider.setValue(round(v * 100)), cool_slider.blockSignals(False), self.setting('cooldown', v)))
        c_layout.addWidget(cool_knob)
        cap.add_row(cool_row)

        # 机械快门声音反馈开关
        self.shutter_sound_box = Toggle('启用')
        self.shutter_sound_box.setChecked(bool(self.config.get('capture_sound_enabled', True)))
        self.shutter_sound_box.toggled.connect(lambda v: self.setting('capture_sound_enabled', v))
        cap.add_row(AppleRow('wave', (TOKENS['amber'], TOKENS['accent_lo']), '机械快门音效反馈', '截图成功时通过系统播放清脆的高保真相机机械快门声', self.shutter_sound_box))

        # 截图手柄触觉微脉冲开关
        self.shutter_haptics_box = Toggle('启用')
        self.shutter_haptics_box.setChecked(bool(self.config.get('capture_haptics_enabled', True)))
        self.shutter_haptics_box.toggled.connect(lambda v: self.setting('capture_haptics_enabled', v))
        cap.add_row(AppleRow('controller', (TOKENS['purple'], TOKENS['accent_lo']), '掌心触觉脉冲反馈', '截图成功瞬间手柄给予 60ms 两段式物理快门轻触确认', self.shutter_haptics_box))

        grid.addWidget(cap, 0, 0)

        # 2. 硬件交互与反馈
        hardware = AppleGroup()
        hdr_hw = QLabel('  02 // HAPTICS & ILLUMINATION · 硬件交互与反馈')
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
        l_title = label('LED 状态光条', 'section')
        l_v.addWidget(l_title)
        l_desc = label('手柄呼吸光条发光色调', 'muted')
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
        r_title = label('双马达振动强度', 'section')
        r_tv.addWidget(r_title)
        r_desc = label('触觉反馈马达输出力度', 'muted')
        r_desc.setObjectName('caption')
        r_tv.addWidget(r_desc)
        rumble_val = label(f"{round(self.config['rumble'] * 100)}%")
        rumble_val.setStyleSheet(f"background: transparent; font: 12px 'Cascadia Code', monospace; font-weight: 700; color: {TOKENS['accent']}; padding: 0 2px;")
        rumble_slider = QSlider(Qt.Horizontal)
        rumble_slider.setRange(0, 100)
        rumble_slider.setValue(round(self.config['rumble'] * 100))
        r_tv.addWidget(rumble_slider)
        r_layout.addLayout(r_tv, 1)

        self.feedback_rumble = button('脉冲测试', self.rumble, primary=True, icon='wave', pill=True)
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

        self.touch_mouse_box = Toggle('启用')
        self.touch_mouse_box.setChecked(self.config['touch_mouse'])
        self.touch_mouse_box.toggled.connect(lambda value: self.setting('touch_mouse', value))
        hardware.add_row(AppleRow('touchpad', (TOKENS['green'], TOKENS['green']), '触摸板手势扩展', '双指轻扫模拟 Windows 鼠标指针', self.touch_mouse_box))

        # 触觉拟真引擎与波形调校
        haptic_row = QWidget()
        h_layout = QHBoxLayout(haptic_row)
        h_layout.setContentsMargins(14, 8, 14, 8)
        h_layout.setSpacing(12)
        h_layout.addWidget(SquircleBadge('wave', (TOKENS['accent'], TOKENS['accent_lo'])))
        h_tv = QVBoxLayout()
        h_tv.setContentsMargins(0, 0, 0, 0)
        h_tv.setSpacing(2)
        h_title = label('触觉拟真引擎 (Haptic Engine)', 'section')
        h_tv.addWidget(h_title)
        h_desc = label('微秒级双音圈多段触觉波形合成 (快门/棘轮/冲击/心跳)', 'muted')
        h_desc.setObjectName('caption')
        h_tv.addWidget(h_desc)
        h_layout.addLayout(h_tv, 1)

        self.test_haptic_shutter = button('快门触觉', lambda: self.test_haptic_pattern('shutter'), pill=True)
        self.test_haptic_impact = button('冲击阻尼', lambda: self.test_haptic_pattern('impact'), pill=True)
        self.test_haptic_heart = button('心跳律动', lambda: self.test_haptic_pattern('heartbeat'), pill=True)
        h_layout.addWidget(self.test_haptic_shutter)
        h_layout.addWidget(self.test_haptic_impact)
        h_layout.addWidget(self.test_haptic_heart)
        hardware.add_row(haptic_row)

        grid.addWidget(hardware, 0, 1)

        # 3. 输入手势设定与文档
        general = AppleGroup()
        hdr_gen = QLabel('  03 // GESTURES & AUTOMATION · 手势与高级设置')
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
        lp_title = label('长按手势识别阈值', 'section')
        lp_tv.addWidget(lp_title)
        lp_desc = label('按住按键达到设定时长触发二次宏动作', 'muted')
        lp_desc.setObjectName('caption')
        lp_tv.addWidget(lp_desc)
        lp_val = label(f"{self.config['long_press']:.2f} 秒")
        lp_val.setStyleSheet(f"background: transparent; font: 12px 'Cascadia Code', monospace; font-weight: 700; color: {TOKENS['chalk']}; padding: 0 2px;")
        lp_slider = QSlider(Qt.Horizontal)
        lp_slider.setRange(30, 150)
        lp_slider.setValue(round(self.config['long_press'] * 100))
        lp_tv.addWidget(lp_slider)
        lp_layout.addLayout(lp_tv, 1)

        lp_knob = RotaryKnob("LONG PRESS", 0.30, 1.50, self.config['long_press'], "s", TOKENS['chalk'], 46)
        def on_lp_change(v):
            sec = v / 100
            lp_val.setText(f"{sec:.2f} 秒")
            lp_knob.setValue(sec)
            self.setting('long_press', sec)
        lp_slider.valueChanged.connect(on_lp_change)
        lp_knob.valueChanged.connect(lambda v: (lp_slider.blockSignals(True), lp_slider.setValue(round(v * 100)), lp_slider.blockSignals(False), self.setting('long_press', v)))
        lp_layout.addWidget(lp_knob)
        general.add_row(lp_row)

        help_btn = button('查阅指南 ›', self.show_help, pill=True)
        general.add_row(AppleRow('help', (TOKENS['accent'], TOKENS['accent_lo']), '使用指南与硬件支持', '查阅全型号支持与高级特性说明', help_btn))
        grid.addWidget(general, 1, 0)

        # 4. 常驻服务与自启
        background = AppleGroup()
        hdr_bg = QLabel('  04 // RUNTIME & DAEMON · 系统服务与开机启动')
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
        ag_title = label('常驻映射监听进程 (Agent)', 'section')
        ag_tv.addWidget(ag_title)
        ag_desc = label('后台超低延迟按键拦截与手势守护服务', 'muted')
        ag_desc.setObjectName('caption')
        ag_tv.addWidget(ag_desc)
        ag_layout.addLayout(ag_tv, 1)

        self.agent_status = Indicator()
        self.agent_status.setText('正在连接')
        ag_layout.addWidget(self.agent_status)
        self.agent_toggle = button('停止服务', self.toggle_agent, pill=True)
        ag_layout.addWidget(self.agent_toggle)
        background.add_row(ag_row)

        self.autostart = Toggle('启用')
        self.autostart.setChecked(autostart_enabled())
        self.autostart.toggled.connect(self.toggle_autostart)
        background.add_row(AppleRow('autostart', (TOKENS['green'], TOKENS['green']), '系统开机自动启动', 'Windows 登录后在后台安静自启运行', self.autostart))
        grid.addWidget(background, 1, 1)

        # 5. 4K 极清硬件加速回放录制 (HEVC / AV1 Replay Buffer)
        replay_group = AppleGroup()
        hdr_replay = QLabel('  05 // 4K INSTANT REPLAY BUFFER · HEVC / AV1 标杆极清即时回放')
        hdr_replay.setObjectName('eyebrow')
        hdr_replay.setStyleSheet(f"color: {TOKENS['purple']}; font-size: 10.5px; font-weight: 700; padding: 8px 16px 2px 16px;")
        replay_group.vbox.addWidget(hdr_replay)

        self.replay_toggle = Toggle('启用')
        self.replay_toggle.setChecked(bool(self.config.get('replay_buffer_enabled', False)))
        def on_replay_toggle(enabled):
            self.setting('replay_buffer_enabled', enabled)
            self._update_replay_hud()
        self.replay_toggle.toggled.connect(on_replay_toggle)
        replay_group.add_row(AppleRow('wave', (TOKENS['purple'], TOKENS['accent_lo']), '4K 极清回放缓存', '开启后长按 Create 键保存本地极清 MP4（若关闭则联动系统 Game Bar）', self.replay_toggle))

        rep_min_row = QWidget()
        rm_layout = QHBoxLayout(rep_min_row)
        rm_layout.setContentsMargins(14, 8, 14, 8)
        rm_layout.setSpacing(12)
        rm_layout.addWidget(SquircleBadge('timer', (TOKENS['accent'], TOKENS['accent_lo'])))
        rm_tv = QVBoxLayout()
        rm_tv.setContentsMargins(0, 0, 0, 0)
        rm_tv.setSpacing(2)
        rm_title = label('最大回看时间 (滑动窗口)', 'section')
        rm_tv.addWidget(rm_title)
        rm_desc = label('常驻内存/磁盘环形缓冲区保留的最长历史片段', 'muted')
        rm_desc.setObjectName('caption')
        rm_tv.addWidget(rm_desc)

        cur_min = int(self.config.get('replay_buffer_minutes', 5))
        self.replay_min_label = label(f"{cur_min} 分钟")
        self.replay_min_label.setStyleSheet(f"background: transparent; font: 12px 'Cascadia Code', monospace; font-weight: 700; color: {TOKENS['accent']}; padding: 0 2px;")
        
        self.replay_slider = QSlider(Qt.Horizontal)
        self.replay_slider.setRange(1, 10)
        self.replay_slider.setValue(cur_min)
        rm_tv.addWidget(self.replay_slider)
        rm_layout.addLayout(rm_tv, 1)

        def on_min_change(v):
            self.replay_min_label.setText(f"{v} 分钟")
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
        cd_title = label('硬件编码器与画质方案', 'section')
        cd_tv.addWidget(cd_title)
        self.replay_codec_desc = label('选择显卡硬件加速格式与码率', 'muted')
        self.replay_codec_desc.setObjectName('caption')
        cd_tv.addWidget(self.replay_codec_desc)
        cd_layout.addLayout(cd_tv, 1)

        self.replay_codec_combo = QComboBox()
        self.replay_codec_combo.addItem('HEVC 标杆极清 (推荐 · 50Mbps)', 'hevc')
        self.replay_codec_combo.addItem('AV1 次世代极清 (AMF/NVENC · 45Mbps)', 'av1')
        self.replay_codec_combo.addItem('H.264 兼容模式 (60Mbps)', 'h264')
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
        
        self.replay_hud_label = label('正在探测硬件加速状态...', 'muted')
        self.replay_hud_label.setStyleSheet(f"font: 11.5px 'Cascadia Code', monospace; color: {TOKENS['ink_2']}; font-weight: 600;")
        hud_layout.addWidget(self.replay_hud_label, 1)

        save_rep_btn = button('立即保存当前回放', self.trigger_manual_replay, pill=True)
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
            from .replay_service import calculate_estimated_ram_gb, detect_hardware_encoder
            minutes = int(self.config.get('replay_buffer_minutes', 5))
            codec = self.config.get('replay_codec', 'hevc')
            bitrate = int(self.config.get('replay_bitrate_mbps', 50))
            ram_gb = calculate_estimated_ram_gb(minutes, bitrate)
            enc = detect_hardware_encoder(codec)
            enabled = self.config.get('replay_buffer_enabled', False)
            status_text = "🟢 [已启用·实时录制中]" if enabled else "⚪ [未启用·回退系统 Game Bar]"
            self.replay_hud_label.setText(
                f"{status_text}  |  {codec.upper()} {bitrate}Mbps  |  {minutes}分钟预估开销: ~{ram_gb:.2f} GB  |  硬件核心: {enc}"
            )
        except Exception:
            pass

    def trigger_manual_replay(self):
        if self.remote:
            res = self.client.send('save_replay')
            if res and res.get('path'):
                self.notify(f"回放已保存: {res['path']}")
            else:
                self.notify("已触发系统回放录制 (Win+Alt+G)")
        else:
            self.actions.shortcut('Win+Alt+G')
            self.notify("已触发系统回放录制 (Win+Alt+G)")

    def test_haptic_pattern(self, pattern: str):
        if self.remote:
            self.client.send('test_haptics', pattern=pattern)
        else:
            if hasattr(self, 'haptic_engine'):
                self.haptic_engine.trigger_feedback(pattern)
        names = {'shutter': '快门触觉微脉冲', 'impact': '重度撞击阻尼', 'heartbeat': '心跳仿真律动'}
        self.notify(f"已触发触觉波形：{names.get(pattern, pattern)}")


    def show_help(self):
        QMessageBox.information(self,'帮助','在手柄图库中选择设备。当前一次只为所选手柄执行映射，切换或断开时释放按键。\n\nPS：Create / Share 截图。Xbox：独立 Share 截图；未提供 Share 时保留原始按键，可自定义映射。Switch：优先 Capture，否则使用 −。\n\n预设按型号保存。点击映射页的手柄按键或按键列表，再点编辑。恢复默认只影响当前预设。\n\n后台随登录运行，关闭窗口不影响映射。键盘映射不屏蔽原始输入，Xbox 键的系统功能由 Windows 管理。游戏触觉和自适应扳机取决于游戏支持。\n\n产品图片来自品牌官网，通用手柄使用标注的示例机型。')

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
                self.store=ConfigStore(self.store.root);self.config=self.store.data
            else:self.store.activate_controller(state);self.store.save()
        else:
            self.controller_heading.setText(CATALOG[family]['name'])
            self.controller_heading.setToolTip('上次使用的型号 · 当前未连接')
        self.art.set_family(family);self.mapping_art.set_family(family);self.photo_caption.setText(PHOTOS[family]['caption'])
        self.mapping_model.setText(CATALOG[family]['name']);self.mapping_model.setToolTip(state['name'] if state else '上次使用的型号 · 当前未连接')
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
        self.mapping_edit.setEnabled(bool(state));self.learn_button.setEnabled(bool(state))
        self.select_mapping(self.selected_key if self.selected_key in available else next(iter(sorted(available)),0))
        for button in self.led_buttons:button.setEnabled(bool(state and state['led']));button.setToolTip('灯条颜色' if state and state['led'] else '设备未提供灯条控制')
        self.feedback_rumble.setEnabled(bool(state and state['rumble']))
        self.touch_mouse_box.setEnabled(bool(state and state.get('touchpad',False)))
        self.touch_mouse_box.setToolTip('触摸板鼠标' if state and state.get('touchpad') else '设备未提供触摸板')

        # Update Guide key and Touchpad row dynamically according to controller family
        if hasattr(self, 'guide_heading'):
            if family == 'xbox':
                self.guide_heading.setText('Xbox 导航')
                if hasattr(self, 'row_guide'):
                    self.row_guide.setToolTip('Xbox 导航键：短按呼出 Game Bar，长按切换任务')
            elif family == 'switch':
                self.guide_heading.setText('Home 导航')
                if hasattr(self, 'row_guide'):
                    self.row_guide.setToolTip('Switch Home 键：短按返回主界面，长按快捷菜单')
            elif family in ('dualsense', 'dualshock4'):
                self.guide_heading.setText('PS 导航')
                if hasattr(self, 'row_guide'):
                    self.row_guide.setToolTip('PlayStation 系统键：呼出控制中心与多任务切换')
            else:
                self.guide_heading.setText('系统导航')
                if hasattr(self, 'row_guide'):
                    self.row_guide.setToolTip('通用手柄 Guide 键：呼出系统快捷主控')

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
            self.notify('登录启动已开启' if enabled else '登录启动已关闭')
        except OSError as exc:
            self.autostart.blockSignals(True); self.autostart.setChecked(not enabled); self.autostart.blockSignals(False); self.notify('设置失败：'+str(exc))

    def toggle_agent(self):
        if not self.remote:return
        if self.client.connected:self.client.send('stop')
        else:spawn(self.store.root,'--agent');self.notify('后台启动中')

    def agent_event(self,message):
        kind=message.get('type')
        if kind=='notice' and hasattr(self,'notice'):self.notify(message['message'])
        elif kind=='capture':
            self.capture_button.setEnabled(True)
            if message.get('path'):self.captured(message['path'])
        elif kind=='buttons' and self.learn:
            valid=[b for b in message['buttons'] if b in BUTTONS]
            if valid:self.end_learning();QTimer.singleShot(0,lambda:self.edit_mapping(valid[0]))
        elif kind=='reply' and not message.get('ok',True):self.notify(message.get('error','操作失败'))
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
        self.stack.setCurrentIndex(index); self.page_heading.setText(headings[index] if index < len(headings) else 'GamePad Studio')
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
        if not text.startswith(('按下','已连接','手柄已断开')):
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
        except Exception as exc: self.notify('设备扫描失败：'+str(exc))

    def testing_protected(self):
        return self.stack.currentIndex()==3 and self.isVisible() and not self.isMinimized() and self.tester.protect.isChecked()

    def poll(self):
        try:
            state=self.device.read(); self.snapshot=state; connected=state is not None
            identity=(state.get('instance_id'),state.get('family')) if state else None
            profile_changed=self.remote and self.client.status.get('profile',self.config['active_profile'])!=self.config['active_profile']
            if identity!=self.device_identity or profile_changed:
                self.engine.reset();self.actions.release_all();self.nikki_engine.reset();self.last_buttons=set();self.device_identity=identity
                self.update_controller_ui(state)
            self.controllers.set_devices(self.device.available,state.get('instance_id') if state else None)
            if self.remote:
                online=self.client.connected; self.enabled=self.client.status.get('enabled',False)
                self.agent_status.setText('运行中' if online else '已停止'); self.agent_toggle.setText('停止后台' if online else '启动后台')
                self.pause_button.setEnabled(online);self.pause_button.setText('暂停映射' if self.enabled else '恢复映射');self.pause_button.set_symbol('pause' if self.enabled else 'play')
                self.capture_button.setEnabled(online and not self.client.status.get('capturing',False))
                if online and (self.learn or QApplication.activeModalWidget() is not None or self.testing_protected()):
                    now=time.monotonic()
                    if now-getattr(self,'last_suspend',0)>.5:
                        self.client.send('suspend',seconds=2);self.last_suspend=now
            if connected != self.previous_connected:
                self.engine.reset(); self.actions.release_all(); self.nikki_engine.reset(); self.last_touch=None
                self.previous_connected=connected
                self.notify('已连接 '+state['name'] if connected else '手柄已断开，等待重新连接')
                if connected and state['led'] and not self.remote: self.device.led(self.config['led'])
            self.status_badge.setText('●  已连接' if connected else '○  等待连接')
            self.side_status.setText('●  已连接' if connected else '○  未连接')
            self.rumble_button.setEnabled(bool(state and state['rumble']))
            self.tester.update_state(state,collect=self.stack.currentIndex()==3 and self.isVisible() and not self.isMinimized())
            self.mapping_art.update_state(state)
            if hasattr(self, 'matrix'):
                if state:
                    p_simple = {-1:'未报告',0:'极低',1:'低',2:'中等',3:'充足',4:'外接'}.get(state.get('power'), '未知')
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
            if not state:
                self.device_details.setText('未连接'); self.power_label.setText('未连接'); self.engine.reset(); self.nikki_engine.reset(); self.last_buttons=set()
                return
            power={-1:'电量未报告',0:'电量极低',1:'电量低',2:'电量中等',3:'电量充足',4:'外接供电'}
            p_text = power.get(state['power'],'电量未知')
            self.device_details.setText(p_text); self.power_label.setText(p_text)
            buttons=set(state['buttons']); new=buttons-self.last_buttons; self.last_buttons=buttons
            if hasattr(self, 'virtual_kbm_page') and self.virtual_kbm_page.is_capturing:
                self.virtual_kbm_page.handle_device_input(state)
                return
            if self.learn and new and not self.remote:
                self.end_learning();number=min(new)
                if number in BUTTONS: QTimer.singleShot(0,lambda:self.edit_mapping(number))
                return
            if not self.remote and self.enabled and not self.learn and not self.testing_protected() and QApplication.activeModalWidget() is None:
                if hasattr(self, 'virtual_kbm_engine') and self.virtual_kbm_engine.scheme.get("enabled", True):
                    self.virtual_kbm_engine.update(state)
                elif self.config.get('active_profile') == NIKKI_PROFILE_NAME:
                    remaining = self.nikki_engine.update(state, self.store.mappings)
                    self.engine.update(remaining, self.store.mappings)
                else:
                    self.engine.update(buttons,self.store.mappings)
                    touch=state['touch']
                    if self.config['touch_mouse'] and touch and self.last_touch:
                        dx,dy=(touch[0]-self.last_touch[0])*1600,(touch[1]-self.last_touch[1])*900
                        if abs(dx)<250 and abs(dy)<250: self.actions.move_mouse(dx,dy)
                    self.last_touch=touch or None
            else:
                self.engine.reset(); self.nikki_engine.reset()
                if hasattr(self, 'virtual_kbm_engine'): self.virtual_kbm_engine.reset()
                self.last_touch=None
            if new:
                self.notify('按下 '+' / '.join(self.button_names.get(k,str(k)) for k in sorted(new)))
        except Exception as exc:
            self.enabled=False
            try: self.engine.reset(); self.actions.release_all(); self.nikki_engine.reset()
            except Exception: pass
            self.pause_button.setText('恢复映射');self.pause_button.set_symbol('play'); self.notify('映射已暂停：'+str(exc))

    def dispatch(self,binding,down=True):
        action=binding.get('action','none')
        if action=='hold': self.actions.hold(binding.get('value',''),down); return
        if not down or action=='none': return
        if action=='capture': self.capture()
        elif action in ('gallery','home'):
            self.navigate(2 if action=='gallery' else 0); self.show_home()
        elif action=='shortcut': self.actions.shortcut(binding['value'])
        elif action=='launch': launch_command(binding['executable'],binding.get('arguments',''))
        else: self.actions.media(action)

    def toggle_pause(self):
        if self.remote:
            self.client.send('pause' if self.enabled else 'resume');return
        self.engine.reset(); self.actions.release_all(); self.nikki_engine.reset(); self.enabled=not self.enabled
        self.pause_button.setText('暂停映射' if self.enabled else '恢复映射');self.pause_button.set_symbol('pause' if self.enabled else 'play'); self.notify('映射已恢复' if self.enabled else '映射已暂停 · 设备监测继续运行')

    def change_profile(self,name):
        if name not in self.config['profiles']: return
        self.engine.reset(); self.actions.release_all(); self.nikki_engine.reset(); self.store.remember_profile(self.snapshot,name); self.store.save(); self.refresh_mappings(); self.notify('已切换配置：'+name)
        if not self.snapshot:self.update_controller_ui(None)
        if self.remote:self.client.send('reload')

    def duplicate_profile(self):
        name,ok=QInputDialog.getText(self,'另存为预设','配置名称')
        if ok and name.strip():
            name=name.strip()
            if name in self.config['profiles']: QMessageBox.warning(self,'名称重复','请使用不同的配置名称。'); return
            self.config['profiles'][name]=copy.deepcopy(self.store.mappings)
            if self.snapshot:self.config['profile_families'][name]=self.snapshot['family']
            self.profile_combo.addItem(name);self.change_profile(name)

    def reset_profile(self):
        if not self.snapshot:return
        name=self.config['active_profile']
        if QMessageBox.question(self,'恢复默认',f'恢复“{name}”的默认映射？')!=QMessageBox.Yes:return
        self.engine.reset();self.actions.release_all()
        self.config['profiles'][name]=controller_defaults(self.snapshot['family'],self.snapshot.get('available_buttons'))
        self.store.save();self.refresh_mappings();self.notify('已恢复默认映射')
        if self.remote:self.client.send('reload')

    def delete_profile(self, *args):
        name = self.config['active_profile']
        family_profiles = self.store.profiles_for(self.snapshot)
        if len(family_profiles) <= 1:
            QMessageBox.information(
                self, '无法删除配置',
                f'当前设备至少需要保留一个配置预设（当前为“{name}”）。\n\n如需重置按键设定，请点击“恢复默认配置”；如需建立新配置，请点击“另存为新预设”。'
            )
            return
        reply = QMessageBox.question(
            self, '删除配置预设',
            f'确定要永久删除配置预设“{name}”吗？\n删除后不可恢复。',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        fallback = self.store.delete_profile(name, self.snapshot)
        if fallback:
            self.engine.reset()
            self.actions.release_all()
            self.refresh_mappings()
            self.notify(f'已删除配置预设：{name}')
            if self.remote:
                self.client.send('reload')

    @staticmethod
    def compact_binding(action):
        kind=action.get('action','none')
        if kind in ('shortcut','hold'):return action.get('value','')
        return {'none':'原始输入','capture':'截图','gallery':'图库','home':'控制中心',
                'replay_record':'回放录制','record_toggle':'录屏'}.get(kind,ACTION_NAMES.get(kind,'原始输入'))

    def select_mapping(self,key):
        self.selected_key=key
        for number,(box,_) in self.mapping_boxes.items():box.setChecked(number==key)
        self.mapping_art.select_button(key)
        self.selected_label.setText(self.button_names.get(key,str(key)))
        self.mapping_edit.setText('编辑 '+self.button_names.get(key,str(key)))
        entry=self.store.mappings.get(str(key),{})
        self.mapping_short.setText(self.compact_binding(entry.get('short',{})))
        self.mapping_long.setText(self.compact_binding(entry.get('long',{})))

    def refresh_mappings(self):
        self.mapping_profile.setText(self.config['active_profile'])
        for combo in (self.profile_combo,self.mapping_combo):
            combo.blockSignals(True);combo.clear();combo.addItems(self.store.profiles_for(self.snapshot));combo.setCurrentText(self.config['active_profile']);combo.blockSignals(False)
        if hasattr(self, 'delete_profile_btn'):
            can_del = len(self.store.profiles_for(self.snapshot)) > 1
            self.delete_profile_btn.setEnabled(True)
            self.delete_profile_btn.setToolTip(
                f'删除当前配置预设：{self.config.get("active_profile", "")}' if can_del
                else '当前控制器仅剩此一个预设，点击查看说明'
            )
        compact=self.compact_binding
        for key,info in self.mapping_labels.items():
            entry=self.store.mappings.get(str(key),{})
            short=compact(entry.get('short',{}));long=compact(entry.get('long',{}))
            original=short==long=='原始输入';info.setVisible(not original)
            info.setText('' if original else f'{short} / {long}')
            info.setToolTip(f'短按：{short}\n长按：{long}')
            self.mapping_boxes[key][0].setToolTip(self.button_names.get(key,str(key))+'\n'+info.toolTip())
        self.select_mapping(self.selected_key)
        family=(self.snapshot.get('family','dualsense') if self.snapshot else
                self.config.get('profile_families',{}).get(self.config['active_profile'],'dualsense'))
        available=(self.snapshot.get('available_buttons') if self.snapshot else
                   [key for key,(box,_) in self.mapping_boxes.items() if not box.isHidden()])
        key=capture_button(family,available)
        self.capture_heading.setText(self.button_names[key] if key is not None else '截图')
        entry=self.store.mappings.get(str(key),{}) if key is not None else {}
        tip = f"{self.button_names.get(key, '截图')}：短按 {compact(entry.get('short',{}))} / 长按 {compact(entry.get('long',{}))}" if key is not None else '驱动未提供 Share，可在映射中自定义截图按键'
        if hasattr(self, 'row_capture'):
            self.row_capture.setToolTip(tip)
        if hasattr(self, 'create_hint'):
            self.create_hint.setText(compact(entry.get('short',{}))+'  /  '+compact(entry.get('long',{})) if key is not None else '未设置')
            self.create_hint.setToolTip(tip)
        if hasattr(self, 'capture_action_btn'):
            if key is not None:
                self.capture_action_btn.setText('查看图库 ›')
                try: self.capture_action_btn.clicked.disconnect()
                except Exception: pass
                self.capture_action_btn.clicked.connect(lambda: self.navigate(2))
            else:
                self.capture_action_btn.setText('配置按键 ›')
                try: self.capture_action_btn.clicked.disconnect()
                except Exception: pass
                self.capture_action_btn.clicked.connect(lambda: self.navigate(1))

    def start_learning(self):
        self.engine.reset(); self.learn=True;self.learn_button.setChecked(True); self.learn_button.setText('请按手柄按键…'); self.notify('按下要配置的手柄按键（10 秒内）')
        QTimer.singleShot(10000,self.end_learning)

    def end_learning(self):
        self.learn=False;self.learn_button.setChecked(False); self.learn_button.setText('识别手柄按键')

    def edit_mapping(self,key):
        if self.remote:self.client.send('suspend',seconds=2)
        if key not in BUTTONS: return
        self.engine.reset(); self.actions.release_all()
        dialog=MappingDialog(self,key,self.store.mappings.get(str(key),{}))
        profile=self.config['active_profile']
        if dialog.exec()==QDialog.Accepted:
            self.config['profiles'][profile][str(key)]=dialog.value(); self.store.save(); self.refresh_mappings(); self.notify('映射已保存')
            if self.remote:self.client.send('reload')

    def capture(self):
        if self.remote:
            if not self.client.send('capture'):self.notify('请先启动后台映射')
            return
        now=time.monotonic()
        if (self.worker and self.worker.isRunning()) or now-self.last_capture<self.config['cooldown']: return
        self.last_capture=now; self.capture_button.setEnabled(False)
        self.worker=CaptureWorker(self.config['save_dir'],self.config['capture_mode']); self.worker.ready.connect(self.captured); self.worker.failed.connect(lambda error:self.notify('截图失败：'+error)); self.worker.finished.connect(lambda:self.capture_button.setEnabled(True)); self.worker.start()

    def captured(self,path):
        self.notify('截图已保存'); self.refresh_gallery()
        if not self.isActiveWindow(): self.tray.showMessage('精彩瞬间已保存',Path(path).name,QSystemTrayIcon.Information,1800)

    @staticmethod
    def clear_layout(layout):
        while layout.count():
            item=layout.takeAt(0)
            if item.widget():
                widget=item.widget(); widget.hide(); widget.setParent(None); widget.deleteLater()

    def thumbnail(self,row,compact=False):
        if compact:
            box=GlassPanel(); layout=QHBoxLayout(box); layout.setContentsMargins(10,10,10,10); layout.setSpacing(12)
            image=QPushButton(); image.setFixedSize(112,64); image.setObjectName('icon')
            pix = QPixmap(row['path'])
            image.setIcon(QIcon(pix)); image.setIconSize(QSize(112,64)); image.clicked.connect(lambda:self.preview(row)); layout.addWidget(image)
            text=QVBoxLayout(); text.setSpacing(4); title=row['title']
            t_lbl = label(title, 'section'); text.addWidget(t_lbl)
            time_lbl = label(f"拍摄于 {row.get('created','')[11:19]}", 'muted'); time_lbl.setObjectName('caption'); text.addWidget(time_lbl)
            layout.addLayout(text,1)
            return box
        box, b = card()
        b.setContentsMargins(14, 14, 14, 14)
        b.setSpacing(10)
        image = QPushButton()
        image.setMinimumHeight(150)
        image.setObjectName('icon')
        pix = QPixmap(row['path'])
        image.setIcon(QIcon(pix))
        image.setIconSize(QSize(280, 150))
        image.clicked.connect(lambda: self.preview(row))
        b.addWidget(image)
        caption = QHBoxLayout()
        caption.addWidget(label(row['title'], 'section'), 1)
        image.setToolTip(f"{row.get('created','')[:19].replace('T',' ')} · {row.get('width','?')} × {row.get('height','?')}")
        fav = IconButton('heart', '取消收藏' if row['favorite'] else '收藏', lambda: self.favorite(row), 32)
        fav.setCheckable(True)
        fav.setChecked(row['favorite'])
        fav.setStyleSheet(f'background: {TOKENS["elevated"]}; border: 1px solid {TOKENS["border_hi"]}; border-radius: {TOKENS["r_sm"]}px;')
        caption.addWidget(fav)
        del_btn = IconButton('trash', '删除截图', lambda: self.delete_capture_confirm(row), 32)
        del_btn.setObjectName('icon_danger')
        del_btn.setStyleSheet(f'background: {TOKENS["elevated"]}; border: 1px solid {TOKENS["border_hi"]}; border-radius: {TOKENS["r_sm"]}px;')
        caption.addWidget(del_btn)
        b.addLayout(caption)
        return box

    def refresh_gallery(self,*args):
        try: rows=list_captures(self.config['save_dir'])
        except OSError as exc: self.notify('无法读取截图目录：'+str(exc)); return
        columns=2 if self.width()<1150 else 3
        signature=(tuple((r['path'],r['favorite']) for r in rows),self.search.text(),self.only_favorites.isChecked(),columns)
        if getattr(self,'gallery_signature',None)==signature: return
        self.gallery_signature=signature
        for i in range(self.gallery_grid.rowCount()):self.gallery_grid.setRowStretch(i,0)
        for i in range(self.gallery_grid.columnCount()):self.gallery_grid.setColumnStretch(i,0)
        self.clear_layout(self.gallery_grid); self.clear_layout(self.recent_row)
        filtered=[r for r in rows if (not self.only_favorites.isChecked() or r['favorite']) and self.search.text().lower() in (r['title']+r['path']).lower()]
        self.gallery_info.setText(f"{len(rows)} 张")
        for i,row in enumerate(filtered[:180]): self.gallery_grid.addWidget(self.thumbnail(row),i//columns,i%columns)
        if not filtered:
            empty = label('暂无截图', 'muted')
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
            r_empty = label('暂无截图', 'muted')
            r_empty.setAlignment(Qt.AlignCenter)
            self.recent_row.addWidget(r_empty, 1)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'recent_row'):QTimer.singleShot(0,self.refresh_gallery)

    def favorite(self,row):
        try: set_favorite(row['path'],not row['favorite']); self.refresh_gallery()
        except OSError as exc: self.notify('收藏失败：'+str(exc))

    def preview(self, row):
        dialog = QDialog(self)
        dialog.setWindowTitle('截图预览')
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
        bar.addWidget(button('复制图像', lambda: QApplication.clipboard().setPixmap(pix), pill=True))
        bar.addWidget(button('打开原图', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(row['path'])), pill=True))
        del_btn = button('删除截图', lambda: self.delete_capture_confirm(row, on_deleted=dialog.accept), danger=True, icon='trash')
        bar.addWidget(del_btn)
        bar.addStretch()
        bar.addWidget(button('关闭', dialog.accept, pill=True))
        layout.addLayout(bar)
        dialog.exec()

    def delete_capture_confirm(self, row, on_deleted=None):
        if not isinstance(row, dict) or 'path' not in row:
            return
        name = Path(row['path']).name
        title = row.get('title', name)
        reply = QMessageBox.question(
            self,
            '删除截图',
            f'确定要永久删除截图 "{title}" 吗？\n文件：{name}',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            try:
                if delete_capture(row['path']):
                    self.gallery_signature = None
                    self.refresh_gallery()
                    self.notify('截图已删除')
                    if on_deleted:
                        on_deleted()
                else:
                    self.notify('截图文件不存在或已被删除')
            except OSError as exc:
                self.notify(f'删除失败：{exc}')

    def clean_unfavorited_captures(self, *args):
        try:
            rows = list_captures(self.config['save_dir'])
        except OSError as exc:
            self.notify(f'无法读取截图目录：{exc}')
            return
        unfavorited = [r for r in rows if not r.get('favorite')]
        if not unfavorited:
            self.notify('没有可清理的未收藏截图')
            return
        reply = QMessageBox.question(
            self,
            '清理未收藏截图',
            f'确定要清理所有未收藏的截图吗？\n将永久删除 {len(unfavorited)} 张截图，已收藏的 {len(rows) - len(unfavorited)} 张截图将被保留。',
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
            self.notify(f'已清理 {count} 张未收藏截图')

    def open_capture_folder(self):
        folder=Path(self.config['save_dir']); folder.mkdir(parents=True,exist_ok=True); QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def choose_folder(self):
        path=QFileDialog.getExistingDirectory(self,'截图保存位置',self.config['save_dir'])
        if path: self.setting('save_dir',path); self.folder_label.setText(path);self.folder_button.setText('截图位置：'+path); self.gallery_signature=None; self.refresh_gallery(); self.notify('截图目录已更新')

    def rumble(self):
        if self.remote:self.client.send('rumble',strength=self.config['rumble']);return
        self.notify('已发送 350 ms 振动测试' if self.device.rumble(self.config['rumble']) else '当前设备暂不支持振动或尚未连接')

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
            self.setting('led',color); self.notify('灯条颜色已更新')
        else: self.notify('当前设备暂不支持灯条控制或尚未连接')

    def closeEvent(self,event):
        self.cleanup();event.accept()

    def cleanup(self):
        if self.closed:return
        self.closed=True
        self.timer.stop(); self.scan_timer.stop(); self.gallery_timer.stop()
        self.notice_timer.stop();self.events.close()
        self.engine.reset(); self.actions.release_all(); self.nikki_engine.reset()
        if hasattr(self, 'virtual_kbm_engine'): self.virtual_kbm_engine.close()
        self.device.close(); self.tray.hide()
        if self.worker and self.worker.isRunning(): self.worker.wait()

    def quit_app(self):
        self.quitting=True; self.close(); QApplication.quit()


def run():
    pages={'home':0,'mappings':1,'gallery':2,'input':3,'settings':4,'controllers':5}
    parser=argparse.ArgumentParser(description='GamePad Studio 手柄管理软件')
    parser.add_argument('--data-dir',type=Path,default=None)
    parser.add_argument('--cli',action='store_true',help='使用原命令行截图模式')
    parser.add_argument('--smoke-test',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--page',choices=list(pages),default='controllers')
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
    lock.setStaleLockTime(2000)
    if not lock.tryLock(100):
        if request(root,'show',role='ui',page=args.page) is not None:
            return
        try:
            lock.removeStaleLockFile()
            (root/'studio.lock').unlink(missing_ok=True)
        except Exception:
            pass
        if not lock.tryLock(100):
            return
    window=Studio(root,standalone=args.smoke_test)
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


"""Connected input devices and the selected device's name/capability switcher.

The legacy per-model photo gallery, search bar, filter tabs, and favorite hearts
have been removed in favor of a lightweight connected-device name switcher.
"""
from PySide6.QtCore import Qt, QTimer, QVariantAnimation, QEasingCurve, QSize
from PySide6.QtWidgets import (QWidget, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout,
                               QScrollArea, QDialog, QSizePolicy, QPushButton)
from .controller_catalog import CATALOG, get_catalog_entry
from .glass import GlassPanel, IconButton, Indicator, SquircleBadge, glyph, TOKENS, tag_style
from .i18n import tr, get_language


def text(value, kind=None):
    widget = QLabel(value)
    if kind:
        widget.setObjectName(kind)
    return widget


def copy(zh, en):
    return en if get_language() == 'en' else zh


def action(value, callback, symbol=None, primary=False):
    button = QPushButton(value)
    button.setCursor(Qt.PointingHandCursor)
    button.setMinimumHeight(36)
    button.setAccessibleName(value)
    if primary:
        button.setObjectName('primary')
    if symbol:
        button.setIcon(glyph(symbol, TOKENS['ink']))
        button.setIconSize(QSize(17, 17))
    button.clicked.connect(callback)
    return button


def clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            widget = item.widget()
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()


class ProductCard(GlassPanel):
    """Connected device status card with smooth hover lift."""

    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.hover_amount = 0.
        self.hover_animation = QVariantAnimation(self)
        self.hover_animation.setDuration(160)
        self.hover_animation.setEasingCurve(QEasingCurve.OutCubic)
        self.hover_animation.valueChanged.connect(self.hover_changed)

    def hover_changed(self, value):
        self.hover_amount = value
        self.update()

    def animate_hover(self, value):
        self.hover_animation.stop()
        self.hover_animation.setStartValue(self.hover_amount)
        self.hover_animation.setEndValue(value)
        self.hover_animation.start()

    def enterEvent(self, event):
        self.animate_hover(1.)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.animate_hover(0.)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.callback()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.callback()
            event.accept()
        else:
            super().keyPressEvent(event)


class ControllerGallery(QWidget):
    """Connected device switcher that updates device name and reported capabilities on plug-in."""

    def __init__(self, on_select, on_favorite=None, on_scan=None, on_manage=None, favorites=(), parent=None):
        super().__init__(parent)
        self.on_select = on_select
        self.on_favorite = on_favorite or (lambda values: None)
        self.on_scan = on_scan or (lambda: None)
        self.on_manage = on_manage or (lambda: None)
        self.favorites = set(favorites)
        self.devices = []
        self.active = None
        self.active_state = None
        self.signature = None
        self.columns = 1
        self.empty_state = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        self.connections = GlassPanel()
        self.connection_rows = QVBoxLayout(self.connections)
        self.connection_rows.setContentsMargins(20, 16, 20, 16)
        self.connection_rows.setSpacing(12)
        layout.addWidget(self.connections)

        catalog_head = QHBoxLayout()
        catalog_head.setContentsMargins(2, 0, 2, 0)
        catalog_title = text(copy('当前输入设备', 'Current input device'), 'section')
        catalog_title.setMinimumHeight(24)
        catalog_head.addWidget(catalog_title)
        catalog_head.addStretch()
        self.result_count = text('', 'caption')
        self.result_count.setMinimumHeight(24)
        catalog_head.addWidget(self.result_count)
        layout.addLayout(catalog_head)

        self.content = QWidget()
        self.grid = QGridLayout(self.content)
        self.grid.setContentsMargins(0, 0, 6, 8)
        self.grid.setHorizontalSpacing(18)
        self.grid.setVerticalSpacing(18)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)

        self.set_devices([], None)

    def set_devices(self, devices, active, state=None):
        devices = list(devices)
        if state and state.get('instance_id') == active and not any(device.get('instance_id') == active for device in devices):
            devices.append(state)
        fields = ('device_key', 'name', 'family', 'supported', 'available_buttons', 'available_axes',
                  'num_axes', 'touchpad', 'led', 'rumble')
        selected = next((device for device in devices if device.get('instance_id') == active), None)
        actual = {**(selected or {}), **(state or {})} if state and state.get('instance_id') == active else selected
        signature = (tuple((d.get('instance_id'), d.get('name'), d.get('family'), d.get('supported', True)) for d in devices),
                     active, tuple((field, repr((actual or {}).get(field))) for field in fields))
        if signature == self.signature:
            return
        self.signature = signature
        self.devices = devices
        self.active = active
        self.active_state = actual
        clear(self.connection_rows)

        header = QWidget()
        top = QHBoxLayout(header)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(10)
        top.addWidget(text(copy('连接设备', 'Connected devices'), 'section'))
        if devices:
            count = text(str(len(devices)))
            count.setStyleSheet(tag_style(TOKENS['ink_3'], 0.14, 0.25))
            count.setAlignment(Qt.AlignCenter)
            count.setMinimumWidth(24)
            top.addWidget(count)
        top.addStretch()
        top.addWidget(action(tr('重新扫描设备'), self.on_scan, 'refresh'))
        self.connection_rows.addWidget(header)

        if not devices:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(12)
            indicator = Indicator()
            indicator.setText(tr('未连接'))
            line.addWidget(indicator)
            guidance = QVBoxLayout()
            guidance.setSpacing(4)
            guidance.addWidget(text(copy('连接手柄，开始配置', 'Connect a controller to get started'), 'section'))
            hint = text(copy('通过 USB 或蓝牙连接后，软件会自动切换显示当前手柄名称与键位能力。',
                             'Connect via USB or Bluetooth to automatically switch to the controller name and capabilities.'), 'muted')
            hint.setWordWrap(True)
            guidance.addWidget(hint)
            line.addLayout(guidance, 1)
            self.connection_rows.addWidget(row)

        for device in devices:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(12)
            current = device['instance_id'] == active
            indicator = Indicator()
            indicator.setText(tr('已连接'))
            line.addWidget(indicator)
            name = text(device['name'], 'section')
            name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            name.setToolTip(device['name'])
            line.addWidget(name, 1)
            if not device.get('supported', True):
                hint = text(tr('未识别，尝试 XInput 模式'), 'caption')
                hint.setStyleSheet(f'color: {TOKENS["amber"]}; font-size: 12px;')
                hint.setWordWrap(True)
                line.addWidget(hint)
            else:
                if current:
                    badge = text(copy('当前设备', 'Active device'))
                    badge.setStyleSheet(tag_style(TOKENS['green'], 0.14, 0.28))
                    line.addWidget(badge)
                choose = action(
                    tr('管理') if current else copy('切换', 'Switch'),
                    lambda checked=False, i=device['instance_id'], c=current: self.on_manage() if c else self.on_select(i),
                    'arrow', primary=current
                )
                choose.setToolTip(tr('管理') if current else tr('切换到此手柄'))
                line.addWidget(choose)
            self.connection_rows.addWidget(row)
        self.refresh()

    def open_family(self, family):
        if family != self.current_family():
            return
        if self.active_state and self.active_state.get('supported', True):
            self.on_manage()
        else:
            self.details(family)

    def current_family(self):
        family = (self.active_state or {}).get('family', 'generic')
        return family if family in CATALOG else 'generic'

    def device_info(self):
        family = self.current_family()
        if not self.active_state:
            return dict(name=copy('通用 XInput', 'Standard XInput'), brand='XINPUT',
                        subtitle=copy('未连接 · 标准按键预览', 'Disconnected · Standard button preview'),
                        note=copy('连接手柄后，显示该设备的型号与实际输入。',
                                  'Connect a controller to display its model and reported inputs.'))
        device = self.active_state
        info = get_catalog_entry(family)
        from .device_quirks import THIRD_PARTY_BRANDS, identify_controller_hardware
        hw_info = identify_controller_hardware(device, trigger_async=False)
        tp_brand = device.get('brand') or hw_info.get('brand', '')
        if tp_brand in THIRD_PARTY_BRANDS:
            info['brand'] = str(tp_brand).upper()
            info['name'] = device.get('model') or hw_info.get('display_name') or device.get('name') or info['name']
        else:
            info['name'] = device.get('name') or info['name']
        capabilities = []
        if 'available_buttons' in device:
            count = len(set(device['available_buttons']))
            capabilities.append(copy(f'{count} 个按键', f'{count} buttons'))
        paddles = int(device.get('back_paddles') or hw_info.get('back_paddles') or 0)
        if paddles > 0:
            capabilities.append(copy(f'{paddles} 个背键', f'{paddles} back paddles'))
        if 'available_axes' in device or 'num_axes' in device:
            count = len(set(device['available_axes'])) if 'available_axes' in device else device['num_axes']
            capabilities.append(copy(f'{count} 个轴', f'{count} axes'))
        for field, zh, en in [('rumble', '震动', 'Rumble'), ('led', '灯光', 'LED'), ('touchpad', '触摸板', 'Touchpad')]:
            if device.get(field):
                capabilities.append(copy(zh, en))
        info['subtitle'] = ' · '.join(capabilities) or copy('当前连接设备', 'Currently connected device')
        info['note'] = copy('配置只应用于当前输入设备；按键与功能以设备实际提供的能力为准。',
                            'Settings apply to this input device and its reported controls and features.')
        return info

    def refresh(self, *args):
        self.columns = self.column_count()
        clear(self.grid)
        for i in range(self.grid.rowCount()):
            self.grid.setRowStretch(i, 0)
        for i in range(self.grid.columnCount()):
            self.grid.setColumnStretch(i, 0)

        family = self.current_family()
        info = self.device_info()
        connected = bool(self.active_state)
        self.empty_state = False
        self.result_count.setText(copy('已连接 · 名称自动切换', 'Connected · Auto-switched name') if connected else
                                  copy('通用布局预览', 'Standard layout preview'))

        box = ProductCard(lambda k=family: self.open_family(k))
        box.setAccessibleName(info['name'])
        box.setMinimumHeight(140)
        box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        body = QVBoxLayout(box)
        body.setContentsMargins(20, 18, 20, 18)
        body.setSpacing(10)

        head = QHBoxLayout()
        brand = text(info['brand'], 'eyebrow')
        head.addWidget(brand)
        head.addStretch()
        if connected:
            conn_badge = QLabel(f"  {tr('已连接')}  ")
            conn_badge.setStyleSheet(tag_style(TOKENS['green'], 0.18, 0.40))
            head.addWidget(conn_badge)
        body.addLayout(head)

        foot = QHBoxLayout()
        foot.setSpacing(14)
        card_badge = SquircleBadge(
            'controller',
            TOKENS['accent'] if connected else TOKENS['ink_2'],
            size=42,
            icon_size=22,
        )
        foot.addWidget(card_badge)

        title_v = QVBoxLayout()
        title_v.setSpacing(4)
        title_lbl = text(info['name'], 'section')
        title_lbl.setStyleSheet(f'font-size: 18px; font-weight: 700; color: {TOKENS["ink"]};')
        title_lbl.setWordWrap(True)
        title_v.addWidget(title_lbl)

        sub_lbl = text(info.get('subtitle', ''), 'caption')
        sub_lbl.setWordWrap(True)
        title_v.addWidget(sub_lbl)

        note_lbl = text(info.get('note', ''), 'muted')
        note_lbl.setWordWrap(True)
        title_v.addWidget(note_lbl)
        foot.addLayout(title_v, 1)

        description = (tr('管理') if connected and self.active_state.get('supported', True)
                       else copy('按键预览', 'Button preview')) + ' · ' + info['name']
        details = IconButton('arrow', description, lambda checked=False, k=family: self.open_family(k), 36)
        details.setStyleSheet(
            f'background: {TOKENS["accent_bg"] if connected else TOKENS["elevated"]}; '
            f'border-radius: {TOKENS["r_sm"]}px; '
            f'border: 1px solid {TOKENS["border_acc"] if connected else TOKENS["border"]};'
        )
        foot.addWidget(details)
        body.addLayout(foot)

        box.setToolTip(info['note'])
        self.grid.addWidget(box, 0, 0, 1, max(1, self.columns))

        for i in range(self.columns):
            self.grid.setColumnStretch(i, 1)
        self.grid.setRowStretch(1, 1)

    def column_count(self):
        width = self.scroll.viewport().width() if hasattr(self, 'scroll') else self.width()
        if width >= 1600:
            return 5
        if width >= 1320:
            return 4
        if width >= 930:
            return 3
        return 2 if width >= 620 else 1

    def detail_dialog(self, family):
        if family != self.current_family():
            return None
        info = self.device_info()
        dialog = QDialog(self)
        dialog.setWindowTitle(info['name'])
        dialog.resize(520, 320)
        dialog.setMinimumWidth(420)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 22)
        layout.setSpacing(16)

        head = QHBoxLayout()
        title = text(info['name'], 'heading')
        title.setWordWrap(True)
        head.addWidget(title, 1)
        head.addStretch()
        head.addWidget(IconButton('close', tr('关闭'), dialog.accept))
        layout.addLayout(head)

        compatibility = GlassPanel()
        notes = QVBoxLayout(compatibility)
        notes.setContentsMargins(16, 12, 16, 12)
        notes.setSpacing(5)
        notes.addWidget(text(copy('设备信息', 'Device information'), 'section'))
        capabilities = text(info['subtitle'], 'caption')
        capabilities.setWordWrap(True)
        notes.addWidget(capabilities)
        note = text(info['note'], 'muted')
        note.setWordWrap(True)
        notes.addWidget(note)
        layout.addWidget(compatibility)

        footer = QHBoxLayout()
        footer.addWidget(text(info['brand'], 'muted'))
        footer.addStretch()
        layout.addLayout(footer)

        for device in self.devices:
            if device['instance_id'] == self.active and device.get('supported', True):
                def activate(checked=False, i=device['instance_id']):
                    self.on_select(i)
                    dialog.accept()
                    self.on_manage()
                row = QHBoxLayout()
                name = text(device['name'], 'section')
                name.setWordWrap(True)
                row.addWidget(name, 1)
                row.addStretch()
                row.addWidget(action(tr('管理'), activate, 'arrow', primary=True))
                layout.addLayout(row)
        if not self.active_state or not self.active_state.get('supported', True):
            hint = text(copy('连接手柄后，即可查看该设备的设置。',
                             'Connect a controller to access its settings.'), 'caption')
            hint.setWordWrap(True)
            layout.addWidget(hint)
        return dialog

    def details(self, family):
        dialog = self.detail_dialog(family)
        if dialog is None:
            return
        dialog.exec()
        dialog.deleteLater()

    def reflow(self):
        columns = self.column_count()
        if columns == self.columns:
            return
        widgets = []
        while self.grid.count():
            widgets.append(self.grid.takeAt(0).widget())
        for i in range(self.grid.rowCount()):
            self.grid.setRowStretch(i, 0)
        for i in range(self.grid.columnCount()):
            self.grid.setColumnStretch(i, 0)
        self.columns = columns
        if len(widgets) == 1:
            self.grid.addWidget(widgets[0], 0, 0, 1, max(1, columns))
        else:
            for i, widget in enumerate(widgets):
                self.grid.addWidget(widget, i // columns, i % columns)
        for i in range(columns):
            self.grid.setColumnStretch(i, 1)
        self.grid.setRowStretch((len(widgets) + columns - 1) // columns, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'grid'):
            QTimer.singleShot(0, self.reflow)

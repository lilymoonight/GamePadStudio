"""Connected input devices and the selected device's settings entry."""
from PySide6.QtCore import Qt, QTimer, QVariantAnimation, QEasingCurve, QSize
from PySide6.QtWidgets import (QWidget, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout,
                               QLineEdit, QComboBox, QScrollArea, QDialog, QSizePolicy,
                               QPushButton)
from .controller_photo import ControllerPhoto
from .controller_catalog import CATALOG, get_catalog_entry
from .glass import GlassPanel, IconButton, Indicator, glyph, TOKENS, tag_style
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
    """GamepadTester Product Card with smooth hover lift."""

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
    """Gallery of supported controllers matching GamepadTester.cn design."""

    def __init__(self, on_select, on_favorite, on_scan, on_manage, favorites=(), parent=None):
        super().__init__(parent)
        self.on_select = on_select
        self.on_favorite = on_favorite
        self.on_scan = on_scan
        self.on_manage = on_manage
        self.favorites = set(favorites)
        self.devices = []
        self.active = None
        self.active_state = None
        self.signature = None
        self.columns = 0
        self.empty_state = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        # Connection state and its next action stay together above the catalog.
        self.connections = GlassPanel()
        self.connection_rows = QVBoxLayout(self.connections)
        self.connection_rows.setContentsMargins(20, 16, 20, 16)
        self.connection_rows.setSpacing(12)
        layout.addWidget(self.connections)

        self.toolbar = GlassPanel()
        bar = QGridLayout(self.toolbar)
        self.toolbar_layout = bar
        bar.setContentsMargins(12, 10, 16, 10)
        bar.setSpacing(20)

        # Keep the selection model available to integrations and keyboard users.
        self.filter = QComboBox(self)
        self.filter.addItems([copy('当前设备', 'Current device'), tr('已连接'), tr('我的收藏')])
        self.filter.hide()
        self.filter_group = QWidget()
        filters = QHBoxLayout(self.filter_group)
        filters.setContentsMargins(0, 0, 0, 0)
        filters.setSpacing(4)
        self.filter_buttons = []
        for index in range(self.filter.count()):
            button = QPushButton(self.filter.itemText(index))
            button.setObjectName('filter')
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setFixedHeight(36)
            button.setCursor(Qt.PointingHandCursor)
            button.setAccessibleName(self.filter.itemText(index))
            button.clicked.connect(lambda checked=False, i=index: self.filter.setCurrentIndex(i))
            filters.addWidget(button)
            self.filter_buttons.append(button)
        bar.addWidget(self.filter_group, 0, 0)
        bar.setColumnStretch(1, 1)

        self.search = QLineEdit()
        self.search.setPlaceholderText(tr('搜索手柄型号...'))
        self.search.setAccessibleName(tr('搜索手柄型号...'))
        self.search.setMinimumWidth(190)
        self.search.setMaximumWidth(320)
        self.search.setFixedHeight(36)
        self.search.setClearButtonEnabled(True)
        self.search.setStyleSheet(
            f'background: {TOKENS["elevated"]}; border: 1px solid {TOKENS["border_hi"]}; '
            f'border-radius: {TOKENS["r_sm"]}px; padding: 0 10px; font-size: 12px;'
        )
        self.search.addAction(glyph('search', TOKENS['ink_3']), QLineEdit.LeadingPosition)
        bar.addWidget(self.search, 0, 1, Qt.AlignRight)
        layout.addWidget(self.toolbar)

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

        # Grid Content
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

        self.search.textChanged.connect(self.refresh)
        self.filter.currentIndexChanged.connect(self.refresh)
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
            hint = text(copy('通过 USB 或蓝牙连接；下方可先查看通用 XInput 按键布局。',
                             'Connect with USB or Bluetooth. A standard XInput layout is available below.'), 'muted')
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

    def toggle_favorite(self, family):
        if family in self.favorites:
            self.favorites.remove(family)
        else:
            self.favorites.add(family)
        self.on_favorite(sorted(self.favorites))
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
        info['name'] = device.get('name') or info['name']
        capabilities = []
        if 'available_buttons' in device:
            count = len(set(device['available_buttons']))
            capabilities.append(copy(f'{count} 个按键', f'{count} buttons'))
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
        for i, b in enumerate(self.filter_buttons):
            b.setChecked(i == self.filter.currentIndex())

        query = self.search.text().strip().casefold()
        family = self.current_family()
        info = self.device_info()
        connected = {family} if self.active_state else set()
        families = [family] if (query in ' '.join((info['name'], info['brand'], info['subtitle'])).casefold()
                               and (self.filter.currentIndex() != 1 or bool(connected))
                               and (self.filter.currentIndex() != 2 or family in self.favorites)) else []
        self.empty_state = not families
        self.result_count.setText(copy(f'{len(families)} 个设备', f'{len(families)} devices') if self.active_state else
                                  copy('通用布局预览', 'Standard layout preview'))

        for index, family in enumerate(families):
            info = self.device_info()
            box = ProductCard(lambda k=family: self.open_family(k))
            box.setAccessibleName(info['name'])
            box.setMinimumHeight(312)
            box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            body = QVBoxLayout(box)
            body.setContentsMargins(20, 18, 20, 18)
            body.setSpacing(10)

            # Top: Eyebrow Brand + Connected Pill + Favorite Heart
            head = QHBoxLayout()
            brand = text(info['brand'], 'eyebrow')
            head.addWidget(brand)
            head.addStretch()

            if family in connected:
                conn_badge = QLabel(f"  {tr('已连接')}  ")
                conn_badge.setStyleSheet(tag_style(TOKENS['green'], 0.18, 0.40))
                head.addWidget(conn_badge)

            is_fav = family in self.favorites
            favorite = IconButton(
                'heart', tr('取消收藏') if is_fav else tr('收藏'),
                lambda checked=False, k=family: self.toggle_favorite(k), 28
            )
            favorite.setCheckable(True)
            favorite.setChecked(is_fav)
            favorite.set_symbol('heart', TOKENS['rose'] if is_fav else TOKENS['ink_dim'])
            head.addWidget(favorite)
            body.addLayout(head)

            # Center: GamepadTester Vector Controller Graphic
            art = ControllerPhoto(family)
            art.setMinimumWidth(160)
            art.setFixedHeight(160)
            body.addWidget(art)

            # Bottom: Title + Arrow Action
            foot = QHBoxLayout()
            title_v = QVBoxLayout()
            title_v.setSpacing(2)
            title_lbl = text(info['name'], 'section')
            title_lbl.setStyleSheet(f'font-size: 16px; font-weight: 700; color: {TOKENS["ink"]};')
            title_lbl.setWordWrap(True)
            title_v.addWidget(title_lbl)

            sub_lbl = text(info.get('subtitle', ''), 'caption')
            sub_lbl.setWordWrap(True)
            title_v.addWidget(sub_lbl)
            foot.addLayout(title_v, 1)

            description = (tr('管理') if self.active_state and self.active_state.get('supported', True)
                           else copy('按键预览', 'Button preview')) + ' · ' + info['name']
            details = IconButton('arrow', description, lambda checked=False, k=family: self.open_family(k), 36)
            details.setStyleSheet(f'background: {TOKENS["accent_bg"] if family in connected else TOKENS["elevated"]}; border-radius: {TOKENS["r_sm"]}px; border: 1px solid {TOKENS["border_acc"] if family in connected else TOKENS["border"]};')
            foot.addWidget(details)
            body.addLayout(foot)

            box.setToolTip(info['note'])
            self.grid.addWidget(box, index // self.columns, index % self.columns)

        if not families:
            empty = GlassPanel()
            empty.setMinimumHeight(240)
            body = QVBoxLayout(empty)
            body.setContentsMargins(32, 32, 32, 32)
            body.setSpacing(10)
            body.addStretch()
            icon = text('')
            icon.setPixmap(glyph('search' if query else 'disconnected', TOKENS['ink_3']).pixmap(32, 32))
            icon.setAlignment(Qt.AlignCenter)
            body.addWidget(icon)
            heading = text(tr('未找到匹配的手柄设备'), 'section')
            heading.setAlignment(Qt.AlignCenter)
            body.addWidget(heading)
            if self.filter.currentIndex() == 1 and not connected:
                hint = copy('连接手柄后重新扫描，即可在这里管理设备。',
                            'Connect a controller and rescan to manage it here.')
                button = action(tr('重新扫描设备'), self.on_scan, 'refresh', primary=True)
            elif self.filter.currentIndex() == 2 and not self.favorites:
                hint = copy('点击型号卡片右上角的爱心，将常用手柄加入收藏。',
                            'Use the heart on a model card to add a favorite.')
                button = action(copy('查看当前设备', 'View current device'), self.reset_filters, 'arrow')
            else:
                hint = copy('尝试其他关键词，或清除筛选以查看当前设备。',
                            'Try another search or clear filters to view the current device.')
                button = action(copy('清除筛选', 'Clear filters'), self.reset_filters, 'refresh')
            caption = text(hint, 'muted')
            caption.setAlignment(Qt.AlignCenter)
            caption.setWordWrap(True)
            body.addWidget(caption)
            body.addWidget(button, 0, Qt.AlignHCenter)
            body.addStretch()
            self.grid.addWidget(empty, 0, 0, 1, self.columns)

        for i in range(self.columns):
            self.grid.setColumnStretch(i, 1)
        self.grid.setRowStretch((len(families) + self.columns - 1) // self.columns, 1)

    def reset_filters(self):
        self.search.clear()
        self.filter.setCurrentIndex(0)

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
        dialog.resize(640, 540)
        dialog.setMinimumWidth(520)
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

        art = ControllerPhoto(family)
        art.setMinimumSize(440, 220)
        layout.addWidget(art, 1)

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
        for i, widget in enumerate(widgets):
            if self.empty_state:
                self.grid.addWidget(widget, 0, 0, 1, columns)
            else:
                self.grid.addWidget(widget, i // columns, i % columns)
        for i in range(columns):
            self.grid.setColumnStretch(i, 1)
        self.grid.setRowStretch(0 if self.empty_state else (len(widgets) + columns - 1) // columns, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'toolbar_layout'):
            narrow = self.width() < 700
            self.toolbar_layout.removeWidget(self.search)
            if narrow:
                self.search.setMaximumWidth(16777215)
                self.toolbar_layout.addWidget(self.search, 1, 0, 1, 2)
            else:
                self.search.setMaximumWidth(320)
                self.toolbar_layout.addWidget(self.search, 0, 1, Qt.AlignRight)
        if hasattr(self, 'grid'):
            QTimer.singleShot(0, self.reflow)

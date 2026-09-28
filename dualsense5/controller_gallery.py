"""GamepadTester Controller Catalog — Clean product cards with live status."""
from PySide6.QtCore import Qt, QTimer, QUrl, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QWidget, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout,
                               QLineEdit, QComboBox, QScrollArea, QDialog, QSizePolicy)
from .controller_photo import ControllerPhoto, PHOTOS
from .controller_catalog import CATALOG
from .glass import GlassPanel, IconButton, Indicator, glyph, TOKENS, token_color, tag_style


def text(value, kind=None):
    widget = QLabel(value)
    if kind:
        widget.setObjectName(kind)
    return widget


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
        self.on_manage = on_manage
        self.favorites = set(favorites)
        self.devices = []
        self.active = None
        self.signature = None
        self.columns = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        # ── GamepadTester Top Filter Bar ──────────────────────────────────
        bar = QHBoxLayout()
        bar.setSpacing(10)

        self.filter = QComboBox(self)
        self.filter.addItems(['全部手柄', '已连接', '我的收藏'])
        self.filter.setFixedHeight(32)
        self.filter.setMinimumWidth(110)
        self.filter.setMaximumWidth(130)
        bar.addWidget(self.filter)
        self.filter_buttons = []
        bar.addStretch()

        # Search Bar
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索手柄型号...')
        self.search.setFixedWidth(240)
        self.search.setFixedHeight(32)
        self.search.setClearButtonEnabled(True)
        self.search.setStyleSheet(
            f'background: {TOKENS["elevated"]}; border: 1px solid {TOKENS["border_hi"]}; '
            f'border-radius: {TOKENS["r_sm"]}px; padding: 0 12px; font-size: 12px;'
        )
        self.search.addAction(glyph('search', TOKENS['ink_3']), QLineEdit.LeadingPosition)
        bar.addWidget(self.search)

        refresh_btn = IconButton('refresh', '重新扫描设备', on_scan, 32)
        refresh_btn.setStyleSheet(
            f'border-radius: {TOKENS["r_sm"]}px; background: {TOKENS["elevated"]}; border: 1px solid {TOKENS["border_hi"]};'
        )
        bar.addWidget(refresh_btn)
        layout.addLayout(bar)

        # Active Multi-Device switcher (if multiple plugged in)
        self.connections = GlassPanel()
        self.connection_rows = QVBoxLayout(self.connections)
        self.connection_rows.setContentsMargins(16, 8, 16, 8)
        layout.addWidget(self.connections)

        # Grid Content
        self.content = QWidget()
        self.grid = QGridLayout(self.content)
        self.grid.setContentsMargins(0, 4, 4, 0)
        self.grid.setSpacing(16)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)

        self.search.textChanged.connect(self.refresh)
        self.filter.currentIndexChanged.connect(self.refresh)
        self.set_devices([], None)

    def set_devices(self, devices, active):
        signature = (tuple((d['instance_id'], d['name'], d['family'], d['supported']) for d in devices), active)
        if signature == self.signature:
            return
        self.signature = signature
        self.devices = devices
        self.active = active
        clear(self.connection_rows)
        self.connections.setVisible(len(devices) > 1 or any(not d['supported'] for d in devices))
        for device in devices:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            current = device['instance_id'] == active
            indicator = Indicator()
            indicator.setText('已连接' if current else '可切换')
            line.addWidget(indicator)
            name = text(device['name'])
            name.setStyleSheet(f'font-weight: 600; color: {TOKENS["ink"]}; font-size: 13px;')
            name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            name.setToolTip(device['name'])
            line.addWidget(name, 1)
            if not device['supported']:
                hint = IconButton('info', '未识别，尝试 XInput 模式')
                line.addWidget(hint)
            else:
                choose = IconButton(
                    'arrow', '管理' if current else '切换到此手柄',
                    lambda checked=False, i=device['instance_id'], c=current: self.on_manage() if c else self.on_select(i)
                )
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
        connected = [d for d in self.devices if d['family'] == family and d['supported']]
        if len(connected) == 1:
            if connected[0]['instance_id'] != self.active:
                self.on_select(connected[0]['instance_id'])
            self.on_manage()
        else:
            self.details(family)

    def refresh(self, *args):
        self.columns = 3 if self.width() >= 940 else 2
        clear(self.grid)
        for i in range(self.grid.rowCount()):
            self.grid.setRowStretch(i, 0)
        for i in range(self.grid.columnCount()):
            self.grid.setColumnStretch(i, 0)
        for i, b in enumerate(self.filter_buttons):
            b.setChecked(i == self.filter.currentIndex())

        query = self.search.text().casefold()
        connected = {d['family'] for d in self.devices if d['supported']}
        families = [
            key for key, item in CATALOG.items()
            if query in (item['name'] + ' ' + item['brand'] + ' ' + item['subtitle']).casefold()
            and (self.filter.currentIndex() != 1 or key in connected)
            and (self.filter.currentIndex() != 2 or key in self.favorites)
        ]

        for index, family in enumerate(families):
            info = CATALOG[family]
            box = ProductCard(lambda k=family: self.open_family(k))
            box.setAccessibleName(info['name'])
            body = QVBoxLayout(box)
            body.setContentsMargins(22, 18, 22, 18)
            body.setSpacing(10)

            # Top: Eyebrow Brand + Connected Pill + Favorite Heart
            head = QHBoxLayout()
            brand = text(info['brand'], 'eyebrow')
            head.addWidget(brand)
            head.addStretch()

            if family in connected:
                conn_badge = QLabel('  已连接  ')
                conn_badge.setStyleSheet(tag_style(TOKENS['green'], 0.18, 0.40))
                head.addWidget(conn_badge)

            is_fav = family in self.favorites
            favorite = IconButton(
                'heart', '取消收藏' if is_fav else '收藏',
                lambda checked=False, k=family: self.toggle_favorite(k), 28
            )
            favorite.setCheckable(True)
            favorite.setChecked(is_fav)
            favorite.set_symbol('heart', TOKENS['rose'] if is_fav else TOKENS['ink_dim'])
            head.addWidget(favorite)
            body.addLayout(head)

            # Center: GamepadTester Vector Controller Graphic
            art = ControllerPhoto(family)
            art.setMinimumWidth(180)
            art.setFixedHeight(165)
            body.addWidget(art)

            # Bottom: Title + Arrow Action
            foot = QHBoxLayout()
            title_v = QVBoxLayout()
            title_v.setSpacing(2)
            title_lbl = text(info['name'], 'section')
            title_lbl.setStyleSheet(f'font-size: 15px; font-weight: 800; color: {TOKENS["ink"]};')
            title_v.addWidget(title_lbl)

            sub_lbl = text(info.get('subtitle', ''), 'caption')
            title_v.addWidget(sub_lbl)
            foot.addLayout(title_v, 1)

            details = IconButton('arrow', '进入配置 ' + info['name'], lambda checked=False, k=family: self.open_family(k), 32)
            details.setStyleSheet(f'background: {TOKENS["elevated"]}; border-radius: {TOKENS["r_sm"]}px; border: 1px solid {TOKENS["border"]};')
            foot.addWidget(details)
            body.addLayout(foot)

            if family == 'generic':
                example = text('8BitDo · 示例', 'caption')
                body.addWidget(example)

            box.setToolTip(info['note'])
            self.grid.addWidget(box, index // self.columns, index % self.columns)

        if not families:
            empty = text('未找到匹配的手柄设备', 'muted')
            empty.setAlignment(Qt.AlignCenter)
            self.grid.addWidget(empty, 0, 0, 1, self.columns)

        for i in range(self.columns):
            self.grid.setColumnStretch(i, 1)
        self.grid.setRowStretch((len(families) + self.columns - 1) // self.columns, 1)

    def detail_dialog(self, family):
        info = CATALOG[family]
        dialog = QDialog(self)
        dialog.setWindowTitle(info['name'])
        dialog.resize(580, 430)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 22)
        layout.setSpacing(16)

        head = QHBoxLayout()
        head.addWidget(text(info['name'], 'heading'))
        head.addStretch()
        head.addWidget(IconButton('close', '关闭', dialog.accept))
        layout.addLayout(head)

        art = ControllerPhoto(family)
        art.setMinimumSize(440, 240)
        layout.addWidget(art, 1)

        footer = QHBoxLayout()
        footer.addWidget(text('8BitDo Ultimate 2C · 示例' if family == 'generic' else info['brand'], 'muted'))
        footer.addStretch()
        footer.addWidget(IconButton('info', info['note']))
        footer.addWidget(IconButton('external', '官方产品页', lambda: QDesktopServices.openUrl(QUrl(PHOTOS[family]['page']))))
        layout.addLayout(footer)

        for device in self.devices:
            if device['family'] == family and device['supported']:
                def activate(checked=False, i=device['instance_id']):
                    self.on_select(i)
                    dialog.accept()
                    self.on_manage()
                row = QHBoxLayout()
                row.addWidget(text(device['name']))
                row.addStretch()
                row.addWidget(IconButton('arrow', '管理', activate))
                layout.addLayout(row)
        return dialog

    def details(self, family):
        dialog = self.detail_dialog(family)
        dialog.exec()
        dialog.deleteLater()

    def reflow(self):
        columns = 3 if self.width() >= 940 else 2
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
            self.grid.addWidget(widget, i // columns, i % columns)
        for i in range(columns):
            self.grid.setColumnStretch(i, 1)
        self.grid.setRowStretch((len(widgets) + columns - 1) // columns, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'grid') and (3 if self.width() >= 940 else 2) != self.columns:
            QTimer.singleShot(0, self.reflow)

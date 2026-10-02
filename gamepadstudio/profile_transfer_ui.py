"""Portable preset preview, with explicit device compatibility before import."""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHeaderView, QHBoxLayout,
                              QLabel, QLineEdit, QTreeWidget, QTreeWidgetItem,
                              QVBoxLayout, QStyledItemDelegate, QStyleOptionViewItem, QStyle)

from .i18n import tr
from .controller_glyphs import display_parts, draw_token, token_advance
from .glass import TOKENS
from .mapping_engine import trigger_label
from .profile_transfer import preview_profile_import
from .studio_core import profile_scope


def text_label(text, kind='caption'):
    widget = QLabel(text)
    widget.setObjectName(kind)
    widget.setWordWrap(True)
    return widget


def action_text(binding):
    kind, value = binding.get('action', 'none'), binding.get('value', '')
    if kind in ('hold', 'shortcut'):
        return value + (' · ' + tr('按住') if kind == 'hold' else '')
    if kind in ('mouse_hold', 'mouse_click'):
        return tr({'left': '鼠标左键', 'right': '鼠标右键', 'middle': '鼠标中键'}.get(value, value)) + (' · ' + tr('按住') if kind == 'mouse_hold' else '')
    if kind == 'wheel':
        return tr('滚轮') + (' ↑' if value == 'up' else ' ↓')
    if kind in ('gamepad_button', 'gamepad_chord', 'gamepad_turbo'):
        return tr('手柄') + ' ' + value + (f" · {binding.get('rate_hz', 15)} Hz" if kind == 'gamepad_turbo' else '')
    return tr({'none': '原始输入', 'suppress': '不触发', 'capture': '截图', 'gallery': '图库',
               'home': '控制中心', 'replay_record': '精彩回放', 'record_toggle': '录屏开关',
               'volume_mute': '静音', 'volume_up': '音量 +', 'volume_down': '音量 −',
               'media': '播放 / 暂停'}.get(kind, kind))


class InputGlyphDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        source = index.data(Qt.UserRole)
        if not source:
            return super().paint(painter, option, index)
        trigger, family = source
        base = QStyleOptionViewItem(option)
        self.initStyleOption(base, index)
        base.text = ''
        option.widget.style().drawControl(QStyle.CE_ItemViewItem, base, painter, option.widget)
        painter.save(); painter.setClipRect(option.rect)
        font = QFont('Segoe UI')
        font.setFamilies(['Segoe UI', 'Microsoft YaHei'])
        font.setPixelSize(12); font.setBold(True)
        parts = display_parts(trigger)
        available = max(0, option.rect.width() - 8)
        while font.pixelSize() > 8:
            total = sum(token_advance(part, family, font) for part in parts) + max(0, len(parts) - 1) * 12
            if total <= available:
                break
            font.setPixelSize(font.pixelSize() - 1)
        x = option.rect.x() + 4
        for position, part in enumerate(parts):
            if position:
                painter.setFont(font); painter.setPen(TOKENS['ink_3'])
                painter.drawText(QRectF(x, option.rect.y(), 12, option.rect.height()), Qt.AlignCenter, '+')
                x += 12
            width = token_advance(part, family, font)
            draw_token(painter, QRectF(x, option.rect.y(), width, option.rect.height()), part, family, font, TOKENS['ink_2'])
            x += width
        painter.restore()


class ProfileImportDialog(QDialog):
    def __init__(self, owner, package):
        super().__init__(owner)
        self.owner = owner
        self.package = copy.deepcopy(package)
        self.preview = preview_profile_import(self.package, owner.snapshot)
        self.identity = self.device_identity(owner.snapshot)
        self.invalidated = False
        self.setWindowTitle(tr('导入预设'))
        self.resize(610, 630)
        self.setMinimumSize(350, 420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(text_label(tr('导入预设'), 'heading'))
        layout.addWidget(text_label((owner.snapshot or {}).get('name', tr('未连接手柄'))))
        name_row = QHBoxLayout()
        name_row.addWidget(text_label(tr('预设名称')))
        self.name_edit = QLineEdit(self.preview['profile']['name'])
        self.name_edit.setMaxLength(80)
        self.name_edit.setMinimumWidth(0)
        self.name_edit.setAccessibleName(tr('预设名称'))
        self.name_edit.textChanged.connect(self.check_device)
        name_row.addWidget(self.name_edit, 1)
        layout.addLayout(name_row)
        self.summary = text_label(tr('可导入 {accepted} 条 · 跳过 {skipped} 条',
                                     accepted=len(self.preview['accepted']), skipped=len(self.preview['skipped'])), 'section')
        layout.addWidget(self.summary)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels([tr('来源'), tr('短按'), tr('长按')])
        self.tree.setAccessibleName(tr('导入绑定预览'))
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(False)
        self.tree.setIndentation(0)
        self.tree.setStyleSheet(f'''
            QTreeWidget {{ background: transparent; border: none; color: {TOKENS['ink_2']}; font-size: 12px; }}
            QTreeWidget::item {{ min-height: 28px; padding: 3px 0; border: none; }}
            QTreeWidget::item:selected {{ background: {TOKENS['overlay']}; }}
            QHeaderView::section {{ background: {TOKENS['surface']}; color: {TOKENS['ink_3']}; border: none; padding: 7px 4px; font-size: 11px; }}
        ''')
        self.tree.setItemDelegateForColumn(0, InputGlyphDelegate(self.tree))
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for column in range(3):
            self.tree.header().setSectionResizeMode(column, QHeaderView.Stretch)
        family = (owner.snapshot or {}).get('family', 'generic')
        source_family = self.package.get('source', {}).get('family', 'generic')
        for entry in self.preview['accepted']:
            mapping = entry['mapping']
            item = QTreeWidgetItem([trigger_label(entry['trigger'], family),
                                    action_text(mapping.get('short', {})), action_text(mapping.get('long', {}))])
            for column in range(3):
                item.setToolTip(column, item.text(column))
            item.setData(0, Qt.UserRole, (entry['trigger'], family))
            self.tree.addTopLevelItem(item)
        if self.preview['skipped']:
            skipped = QTreeWidgetItem([tr('因设备差异跳过'), '', ''])
            self.tree.addTopLevelItem(skipped)
            skipped.setFirstColumnSpanned(True)
            for entry in self.preview['skipped']:
                reason = '型号专属' if entry['reason'] == '型号专属按键不能跨手柄类型导入' else '不支持'
                item = QTreeWidgetItem([trigger_label(entry['trigger'], source_family), tr(reason), ''])
                item.setData(0, Qt.UserRole, (entry['trigger'], source_family))
                item.setToolTip(0, item.text(0)); item.setToolTip(1, tr(entry['reason']))
                self.tree.addTopLevelItem(item)
        layout.addWidget(self.tree, 1)
        hints = [tr('创建此手柄的新预设；保留当前生效方案。'), tr('重名时自动生成新名称，不覆盖已有预设。')]
        if any(str(entry['trigger']).startswith('TP:') for entry in self.preview['accepted']):
            hints.append(tr('触摸手势开关保持当前设备设置。'))
        if any(binding.get('action', '').startswith('gamepad_') for entry in self.preview['accepted']
               for binding in (entry['mapping'].get('short', {}), entry['mapping'].get('long', {}))):
            hints.append(tr('此文件含手柄输出动作，当前仅保留配置。'))
        layout.addWidget(text_label('\n'.join(hints)))
        self.message = text_label('')
        layout.addWidget(self.message)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText(tr('导入'))
        self.buttons.button(QDialogButtonBox.Cancel).setText(tr('取消'))
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_device)
        self.timer.start(250)
        self.check_device()

    @staticmethod
    def device_identity(state):
        return (profile_scope(state), state.get('instance_id')) if state else None

    def check_device(self):
        # textChanged can run while the dialog is still being constructed.
        if not hasattr(self, 'buttons'):
            return False
        if self.identity is None or self.device_identity(self.owner.snapshot) != self.identity:
            self.invalidated = True
        if not self.invalidated:
            try:
                current = preview_profile_import(self.package, self.owner.snapshot)
                self.invalidated = current['input_signature'] != self.preview['input_signature']
            except (ValueError, TypeError):
                self.invalidated = True
        if self.invalidated:
            self.message.setText(tr('输入设备已变化，请重新预览导入文件。'))
        elif not self.preview['accepted']:
            self.message.setText(tr('此手柄没有可导入的绑定。'))
        ready = not self.invalidated and bool(self.name_edit.text().strip()) and bool(self.preview['accepted'])
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(ready)
        return ready

    def save(self):
        if not self.check_device():
            return
        change = {'op': 'import_profile', 'package': self.package, 'name': self.name_edit.text().strip(),
                  'expected_inputs': self.preview['input_signature']}
        if self.owner.mapping_change(change):
            self.accept()

    def done(self, result):
        self.timer.stop()
        super().done(result)

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)

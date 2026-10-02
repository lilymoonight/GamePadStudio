"""A compact, device-bound editor for foreground application rules."""
from __future__ import annotations

import copy
import ntpath

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                              QHBoxLayout, QLabel, QPushButton, QScrollArea,
                              QSizePolicy, QVBoxLayout, QWidget)

from .glass import AppleGroup, AppleRow, TOKENS, Toggle
from .i18n import tr, tr_profile
from .studio_core import profile_scope


def caption(text, kind='caption'):
    widget = QLabel(text)
    widget.setObjectName(kind)
    widget.setWordWrap(True)
    return widget


class ApplicationRuleRow(QWidget):
    def __init__(self, rule, profiles, remove, parent=None):
        super().__init__(parent)
        self.executable = rule['executable']
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        top = QHBoxLayout()
        top.addWidget(caption(ntpath.basename(self.executable), 'section'), 1)
        self.remove_button = QPushButton(tr('移除'))
        self.remove_button.setObjectName('pill')
        self.remove_button.setCursor(Qt.PointingHandCursor)
        self.remove_button.setAccessibleName(tr('移除') + ' · ' + self.executable)
        self.remove_button.clicked.connect(lambda: remove(self))
        top.addWidget(self.remove_button)
        layout.addLayout(top)
        self.path_label = QLabel()
        self.path_label.setObjectName('caption')
        self.path_label.setMinimumWidth(0)
        self.path_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.path_label.setToolTip(self.executable)
        self.path_label.setAccessibleName(self.executable)
        layout.addWidget(self.path_label)
        bottom = QHBoxLayout()
        bottom.addWidget(caption(tr('使用预设')))
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(0)
        self.profile_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.profile_combo.setMinimumContentsLength(8)
        self.profile_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.profile_combo.setAccessibleName(tr('使用预设') + ' · ' + ntpath.basename(self.executable))
        for name in profiles:
            self.profile_combo.addItem(tr_profile(name), name)
        self.profile_combo.setCurrentIndex(self.profile_combo.findData(rule['profile']))
        self.profile_combo.currentTextChanged.connect(self.profile_combo.setToolTip)
        self.profile_combo.setToolTip(self.profile_combo.currentText())
        bottom.addWidget(self.profile_combo, 1)
        layout.addLayout(bottom)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.path_label.setText(self.path_label.fontMetrics().elidedText(
            self.executable, Qt.ElideMiddle, self.path_label.width()))

    def value(self):
        return {'executable': self.executable, 'profile': self.profile_combo.currentData()}


class ApplicationProfilesDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        state = owner.snapshot
        self.identity = (profile_scope(state), state.get('instance_id')) if state else None
        self.profiles = owner.store.profiles_for(state)
        self.rows = []
        self.invalidated = False
        self.setWindowTitle(tr('应用关联'))
        self.resize(560, 590)
        self.setMinimumSize(350, 380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(caption(tr('应用关联'), 'heading'))
        layout.addWidget(caption((state or {}).get('name', tr('未连接手柄'))))
        settings = owner.store.application_settings(state)
        self.enabled_box = Toggle(tr('启用'))
        self.enabled_box.setChecked(settings['enabled'])
        group = AppleGroup()
        group.add_row(AppleRow('controller', (TOKENS['green'], TOKENS['green']),
                              tr('自动切换预设'), tr('进入关联应用时生效，离开后恢复手动预设'),
                              self.enabled_box))
        layout.addWidget(group)
        header = QHBoxLayout()
        header.addWidget(caption(tr('关联应用'), 'section'), 1)
        self.add_button = QPushButton(tr('添加应用'))
        self.add_button.setObjectName('pill')
        self.add_button.setCursor(Qt.PointingHandCursor)
        self.add_button.clicked.connect(self.choose_application)
        header.addWidget(self.add_button)
        layout.addLayout(header)
        area = QScrollArea()
        area.setFrameShape(QScrollArea.NoFrame)
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        self.rule_layout = QVBoxLayout(body)
        self.rule_layout.setContentsMargins(0, 0, 0, 0)
        self.rule_layout.setSpacing(8)
        self.empty_label = caption(tr('添加游戏实际运行的程序，再选择此手柄的预设。'))
        self.rule_layout.addWidget(self.empty_label)
        self.rule_layout.addStretch()
        area.setWidget(body)
        layout.addWidget(area, 1)
        for rule in settings['rules']:
            self.add_rule(copy.deepcopy(rule))
        self.message = caption(tr('仅对当前手柄生效；编辑时保持当前预设。'))
        layout.addWidget(self.message)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Save).setText(tr('保存'))
        self.buttons.button(QDialogButtonBox.Cancel).setText(tr('取消'))
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_device)
        self.timer.start(200)
        self.check_device()

    def add_rule(self, rule):
        row = ApplicationRuleRow(rule, self.profiles, self.remove_rule)
        self.rows.append(row)
        self.rule_layout.insertWidget(self.rule_layout.count() - 1, row)
        self.empty_label.hide()

    def remove_rule(self, row):
        self.rows.remove(row)
        self.rule_layout.removeWidget(row)
        row.hide()
        row.deleteLater()
        self.empty_label.setVisible(not self.rows)

    def choose_application(self):
        path, _ = QFileDialog.getOpenFileName(self, tr('选择应用'), '', tr('应用程序 (*.exe)'))
        if not path or not self.check_device():
            return
        path = ntpath.normpath(path)
        if any(ntpath.normcase(row.executable) == ntpath.normcase(path) for row in self.rows):
            self.message.setText(tr('此应用已关联，请修改已有条目的预设。'))
            return
        active = self.owner.config.get('active_profile')
        profile = active if active in self.profiles else self.profiles[0]
        self.add_rule({'executable': path, 'profile': profile})

    def check_device(self):
        state = self.owner.snapshot
        identity = (profile_scope(state), state.get('instance_id')) if state else None
        if self.identity is None or identity != self.identity:
            self.invalidated = True
            self.message.setText(tr('输入设备已变化，请重新打开当前设备设置'))
        self.buttons.button(QDialogButtonBox.Save).setEnabled(not self.invalidated)
        self.add_button.setEnabled(not self.invalidated)
        self.enabled_box.setEnabled(not self.invalidated)
        for row in self.rows:
            row.setEnabled(not self.invalidated)
        return not self.invalidated

    def save(self):
        if not self.check_device():
            return
        settings = {'enabled': self.enabled_box.isChecked(), 'rules': [row.value() for row in self.rows]}
        if self.owner.mapping_change({'op': 'application_profiles', 'settings': settings}):
            self.accept()

    def done(self, result):
        self.timer.stop()
        super().done(result)

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)

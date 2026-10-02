"""A draft editor for the application's physical-keyboard pause shortcut."""
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QVBoxLayout

from .emergency_hotkey import DEFAULT_SHORTCUT, normalize_hotkey_settings
from .glass import TOKENS, Toggle
from .i18n import tr


class EmergencyHotkeyDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        from .studio import label
        self.owner = owner
        self.setWindowTitle(tr('紧急暂停快捷键'))
        self.setMinimumWidth(350)
        self.resize(470, 270)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(label(tr('紧急暂停快捷键'), 'section'))
        layout.addWidget(label(tr('在游戏前台按下物理键盘快捷键，立即暂停映射；请在工作台手动恢复。'), 'caption', True))
        settings = normalize_hotkey_settings(owner.config.get('emergency_hotkey'))
        form = QFormLayout()
        form.setVerticalSpacing(12)
        self.enabled = Toggle(tr('启用'))
        self.enabled.setChecked(settings['enabled'])
        self.shortcut = QComboBox()
        self.shortcut.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.shortcut.setMinimumContentsLength(12)
        for value in (DEFAULT_SHORTCUT, 'Ctrl+Shift+F10', 'Alt+Shift+F10'):
            self.shortcut.addItem(value, value)
        if self.shortcut.findData(settings['shortcut']) < 0:
            self.shortcut.addItem(settings['shortcut'], settings['shortcut'])
        self.shortcut.setCurrentIndex(self.shortcut.findData(settings['shortcut']))
        self.shortcut.setEnabled(self.enabled.isChecked())
        self.enabled.toggled.connect(self.shortcut.setEnabled)
        form.addRow(tr('紧急暂停'), self.enabled)
        form.addRow(tr('键盘快捷键'), self.shortcut)
        layout.addLayout(form)
        self.error = label('', wrap=True)
        self.error.setStyleSheet(f"color: {TOKENS['amber']};")
        self.error.hide()
        layout.addWidget(self.error)
        layout.addStretch(1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Save).setText(tr('保存'))
        self.buttons.button(QDialogButtonBox.Cancel).setText(tr('取消'))
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def save(self):
        settings = {'enabled': self.enabled.isChecked(), 'shortcut': self.shortcut.currentData()}
        try:
            self.owner.setting('emergency_hotkey', settings)
        except (OSError, ValueError, RuntimeError) as exc:
            self.error.setText(tr(str(exc)))
            self.error.show()
            return
        self.accept()

"""macOS physical-keyboard screenshot entry using an exclusive Carbon hotkey."""
from __future__ import annotations

import sys

from PySide6.QtCore import QCoreApplication, QObject, QThread

from .emergency_hotkey import MOD_COMMAND, MOD_SHIFT, parse_shortcut


DEFAULT_SHORTCUT = 'Ctrl+Alt+Shift+S'
_SYSTEM_CAPTURE_KEYS = {ord(key) for key in '3456'}


def normalize_screenshot_hotkey_settings(value, strict=False):
    """Keep existing Mac installs enabled while respecting an explicit opt-out."""
    defaults = {'enabled': sys.platform == 'darwin', 'shortcut': DEFAULT_SHORTCUT}
    try:
        if not isinstance(value, dict) or set(value) != {'enabled', 'shortcut'}:
            raise ValueError('截图快捷键设置格式错误')
        if type(value['enabled']) is not bool:
            raise ValueError('截图快捷键只能设为开启或关闭')
        canonical, modifiers, vk = parse_shortcut(value['shortcut'], platform='darwin')
        if modifiers & (MOD_COMMAND | MOD_SHIFT) == MOD_COMMAND | MOD_SHIFT and vk in _SYSTEM_CAPTURE_KEYS:
            raise ValueError('该快捷键由 macOS 系统截图使用，请选择其他按键')
        return {'enabled': value['enabled'], 'shortcut': canonical}
    except ValueError:
        if strict:
            raise
        return defaults


class ScreenshotHotkey(QObject):
    """Own one Mac shortcut on the Qt main thread; never polls unrelated keys."""
    def __init__(self, callback, parent=None):
        super().__init__(parent)
        if not callable(callback):
            raise TypeError('ScreenshotHotkey callback must be callable')
        self._callback = callback
        self._settings = normalize_screenshot_hotkey_settings(None)
        self._configured = None
        self._registered = False
        self._error = ''
        self._closed = False
        self._mac_hotkey = None
        self._app = None
        if parent is not None:
            parent.destroyed.connect(self.close)

    def status(self):
        return dict(self._settings, registered=self._registered, error=self._error)

    def _invoke(self):
        if self._closed or not self._registered:
            return
        try:
            self._callback()
        except Exception:
            self._error = '截图失败，请使用窗口中的截图按钮'

    def _failed(self, message):
        self._registered = False
        self._error = message

    def _unregister(self):
        self._registered = False
        if self._mac_hotkey is None or self._mac_hotkey.unregister():
            return True
        self._error = '旧截图快捷键未能释放，请重启软件后再试'
        return False

    def configure(self, settings):
        settings = normalize_screenshot_hotkey_settings(settings)
        if self._closed:
            self._settings = dict(settings, enabled=False)
            self._error = '截图快捷键已关闭'
            return self.status()
        app = QCoreApplication.instance()
        if app is not None and QThread.currentThread() != app.thread():
            self._error = '请在软件主线程设置截图快捷键'
            return self.status()
        if settings == self._configured and (self._registered or not settings['enabled']):
            return self.status()
        self._settings = settings
        self._configured = dict(settings)
        self._error = ''
        if not self._unregister() or not settings['enabled']:
            return self.status()
        if sys.platform != 'darwin':
            self._error = '当前系统不支持此截图快捷键'
            return self.status()
        if app is None:
            self._error = '软件主线程尚未就绪，无法注册截图快捷键'
            return self.status()
        if self._app is None:
            self._app = app
            app.aboutToQuit.connect(self.close)
        try:
            from .macos_hotkey import MacHotkey
            if self._mac_hotkey is None:
                self._mac_hotkey = MacHotkey(
                    self, failure_message='截图快捷键监听失败，请使用窗口中的截图按钮并重新设置快捷键')
                self._mac_hotkey.triggered.connect(self._invoke)
                self._mac_hotkey.failed.connect(self._failed)
            _, modifiers, vk = parse_shortcut(settings['shortcut'], platform='darwin')
            self._mac_hotkey.register(vk, modifiers)
            self._registered = True
        except (OSError, AttributeError, TypeError, RuntimeError, ValueError) as exc:
            self._error = ('该快捷键已被其他功能或软件占用，请更换截图快捷键'
                           if getattr(exc, 'code', None) == -9878
                           else '截图快捷键注册失败，请更换快捷键后再试')
            self._unregister()
        return self.status()

    def close(self, *_):
        if self._closed and self._mac_hotkey is None:
            return
        if self._app is not None and QThread.currentThread() != self._app.thread():
            raise RuntimeError('ScreenshotHotkey must close on the Qt main thread')
        self._closed = True
        self._settings['enabled'] = False
        if self._unregister():
            self._mac_hotkey = None
            if self._app is not None:
                try:
                    self._app.aboutToQuit.disconnect(self.close)
                except (TypeError, RuntimeError):
                    pass
            self._app = None
            self._callback = None

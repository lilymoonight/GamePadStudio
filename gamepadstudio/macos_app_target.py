"""Locate explicit chat composers in already running macOS applications.

This backend never launches an app, synthesizes keys, or submits a message.
Accessibility is checked without prompting. Native calls are injectable so
tests never need to inspect or operate Codex or Antigravity themselves.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes as C
from dataclasses import dataclass, field
import re
import sys
import time


TARGETS = {'com.openai.codex': 'Codex', 'com.google.antigravity': 'Google Antigravity'}
_TEXT_ROLES = {'AXTextArea', 'AXTextField'}
_LABELS = ('AXIdentifier', 'AXDescription', 'AXTitle', 'AXHelp', 'AXPlaceholderValue')
_COMPOSER = re.compile(r'(?:^|[^a-z])(composer|prompt|chat|message)(?:$|[^a-z])', re.I)
_NON_COMPOSER = re.compile(r'(?:^|[^a-z])(search|filter|terminal|editor|filename|password|commit|rename|command)(?:$|[^a-z])', re.I)
_VOICE_START = {'start dictation', 'start voice input', '开始听写', '开始语音输入'}
_VOICE_STOP = {'stop dictation', 'finish dictation', 'stop voice input', '停止听写', '停止语音输入'}
_VOICE_MEMO = {'record voice memo'}
_SUBMIT = re.compile(r'(?:^|[^a-z])(send|submit|run)(?:$|[^a-z])|发送|提交', re.I)
_ACTIVATION_TIMEOUT = .8
_FOCUS_TIMEOUT = .6
_CONFIRM_INTERVAL = .025


def _semantic_label(value):
    return re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', value).strip().casefold()


class TargetError(RuntimeError):
    def __init__(self, code, reason):
        self.code, self.reason = code, reason
        super().__init__(reason)


@dataclass
class ComposerTarget:
    bundle_id: str
    pid: int
    generation: float
    name: str
    window_title: str
    draft: str
    _owner: object = field(repr=False)
    _app: object = field(repr=False)
    _window: object = field(repr=False)
    _composer: object = field(repr=False)
    _closed: bool = field(default=False, repr=False)

    def close(self):
        if not self._closed:
            self._closed = True
            for element in (self._composer, self._window, self._app):
                self._owner.native.release(element)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        try:
            self.close()
        except (AttributeError, RuntimeError, OSError):
            pass


class CFRange(C.Structure):
    _fields_ = [('location', C.c_long), ('length', C.c_long)]


class _AXRef:
    def __init__(self, native, pointer):
        self.native, self.pointer = native, pointer

    def close(self):
        if self.pointer:
            self.native.cf.CFRelease(self.pointer)
            self.pointer = None

    def __del__(self):
        self.close()


class _MacNative:
    """Fixed-arity Objective-C and public AX/CF bindings, with owned AX refs."""
    def __init__(self):
        if sys.platform != 'darwin':
            raise TargetError('unsupported_platform', '应用输入定位目前仅支持 macOS。')
        self.appkit = C.CDLL('/System/Library/Frameworks/AppKit.framework/AppKit')
        self.objc = C.CDLL('/usr/lib/libobjc.A.dylib')
        self.ax = C.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.objc.objc_getClass.argtypes, self.objc.objc_getClass.restype = [C.c_char_p], C.c_void_p
        self.objc.sel_registerName.argtypes, self.objc.sel_registerName.restype = [C.c_char_p], C.c_void_p
        address = C.cast(self.objc.objc_msgSend, C.c_void_p).value
        self._object = C.CFUNCTYPE(C.c_void_p, C.c_void_p, C.c_void_p)(address)
        self._object_at = C.CFUNCTYPE(C.c_void_p, C.c_void_p, C.c_void_p, C.c_ulong)(address)
        self._integer = C.CFUNCTYPE(C.c_int32, C.c_void_p, C.c_void_p)(address)
        self._count = C.CFUNCTYPE(C.c_ulong, C.c_void_p, C.c_void_p)(address)
        self._double = C.CFUNCTYPE(C.c_double, C.c_void_p, C.c_void_p)(address)
        self._text = C.CFUNCTYPE(C.c_char_p, C.c_void_p, C.c_void_p)(address)
        self._bool = C.CFUNCTYPE(C.c_bool, C.c_void_p, C.c_void_p)(address)
        self._activate = C.CFUNCTYPE(C.c_bool, C.c_void_p, C.c_void_p, C.c_ulong)(address)
        self._void = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p)(address)
        ax_signatures = {
            'AXIsProcessTrusted': ([], C.c_bool),
            'AXUIElementCreateApplication': ([C.c_int32], C.c_void_p),
            'AXUIElementGetTypeID': ([], C.c_ulong),
            'AXUIElementCopyAttributeValue': ([C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p)], C.c_int32),
            'AXUIElementIsAttributeSettable': ([C.c_void_p, C.c_void_p, C.POINTER(C.c_ubyte)], C.c_int32),
            'AXUIElementSetAttributeValue': ([C.c_void_p, C.c_void_p, C.c_void_p], C.c_int32),
            'AXUIElementPerformAction': ([C.c_void_p, C.c_void_p], C.c_int32),
            'AXUIElementSetMessagingTimeout': ([C.c_void_p, C.c_float], C.c_int32),
            'AXValueCreate': ([C.c_uint32, C.c_void_p], C.c_void_p),
            'AXValueGetTypeID': ([], C.c_ulong),
            'AXValueGetType': ([C.c_void_p], C.c_uint32),
            'AXValueGetValue': ([C.c_void_p, C.c_uint32, C.c_void_p], C.c_ubyte),
        }
        cf_signatures = {
            'CFRetain': ([C.c_void_p], C.c_void_p), 'CFRelease': ([C.c_void_p], None),
            'CFEqual': ([C.c_void_p, C.c_void_p], C.c_ubyte), 'CFHash': ([C.c_void_p], C.c_ulong),
            'CFGetTypeID': ([C.c_void_p], C.c_ulong),
            'CFStringGetTypeID': ([], C.c_ulong), 'CFArrayGetTypeID': ([], C.c_ulong),
            'CFBooleanGetTypeID': ([], C.c_ulong),
            'CFBooleanGetValue': ([C.c_void_p], C.c_ubyte),
            'CFStringCreateWithBytes': ([C.c_void_p, C.c_void_p, C.c_long, C.c_uint32, C.c_ubyte], C.c_void_p),
            'CFStringGetLength': ([C.c_void_p], C.c_long),
            'CFStringGetCharacters': ([C.c_void_p, CFRange, C.POINTER(C.c_uint16)], None),
            'CFArrayGetCount': ([C.c_void_p], C.c_long),
            'CFArrayGetValueAtIndex': ([C.c_void_p, C.c_long], C.c_void_p),
            'CFRunLoopRunInMode': ([C.c_void_p, C.c_double, C.c_ubyte], C.c_int32),
        }
        for library, signatures in ((self.ax, ax_signatures), (self.cf, cf_signatures)):
            for name, (arguments, result) in signatures.items():
                method = getattr(library, name)
                method.argtypes, method.restype = arguments, result
        self._true = C.c_void_p.in_dll(self.cf, 'kCFBooleanTrue').value
        self._false = C.c_void_p.in_dll(self.cf, 'kCFBooleanFalse').value
        self._run_mode = C.c_void_p.in_dll(self.cf, 'kCFRunLoopDefaultMode').value

    def _selector(self, name):
        return self.objc.sel_registerName(name.encode('ascii'))

    def _message(self, obj, name):
        return self._object(obj, self._selector(name)) if obj else None

    def _string(self, obj):
        data = self._text(obj, self._selector('UTF8String')) if obj else None
        return data.decode('utf-8') if data else ''

    @contextmanager
    def _pool(self):
        pool = self._message(self.objc.objc_getClass(b'NSAutoreleasePool'), 'alloc')
        pool = self._message(pool, 'init')
        try:
            yield
        finally:
            if pool:
                self._void(pool, self._selector('drain'))

    def _workspace(self):
        return self._message(self.objc.objc_getClass(b'NSWorkspace'), 'sharedWorkspace')

    def _record(self, app):
        bundle_id = self._string(self._message(app, 'bundleIdentifier'))
        launch = self._message(app, 'launchDate')
        return dict(bundle_id=bundle_id, pid=self._integer(app, self._selector('processIdentifier')),
                    name=self._string(self._message(app, 'localizedName')),
                    generation=self._double(launch, self._selector('timeIntervalSince1970')) if launch else None)

    def running_apps(self):
        with self._pool():
            apps = self._message(self._workspace(), 'runningApplications')
            result = []
            for index in range(self._count(apps, self._selector('count')) if apps else 0):
                app = self._object_at(apps, self._selector('objectAtIndex:'), index)
                if not self._bool(app, self._selector('isTerminated')):
                    record = self._record(app)
                    if record['bundle_id'] in TARGETS:
                        result.append(record)
            return result

    def frontmost(self):
        with self._pool():
            app = self._message(self._workspace(), 'frontmostApplication')
            return self._record(app) if app else None

    def activate(self, expected):
        with self._pool():
            apps = self._message(self._workspace(), 'runningApplications')
            for index in range(self._count(apps, self._selector('count')) if apps else 0):
                app = self._object_at(apps, self._selector('objectAtIndex:'), index)
                if self._record(app) == expected:
                    # No launch API is used. Modern macOS activates through the
                    # documented NSRunningApplication method with no options.
                    return bool(self._activate(app, self._selector('activateWithOptions:'), 0))
        return False

    def trusted(self):
        return bool(self.ax.AXIsProcessTrusted())

    def wait(self, duration):
        # NSWorkspace's time-varying properties need the calling run loop to
        # advance. This is only used for short read-only confirmation waits.
        self.cf.CFRunLoopRunInMode(self._run_mode, duration, False)

    def _wrap(self, pointer):
        if not pointer:
            raise TargetError('ax_unavailable', '应用没有提供可访问的窗口。')
        self.ax.AXUIElementSetMessagingTimeout(pointer, .3)
        return _AXRef(self, pointer)

    def application(self, pid):
        return self._wrap(self.ax.AXUIElementCreateApplication(pid))

    def retain(self, element):
        return self._wrap(self.cf.CFRetain(element.pointer))

    @staticmethod
    def release(element):
        if element is not None:
            element.close()

    def equal(self, first, second):
        return bool(first and second and first.pointer and second.pointer
                    and self.cf.CFEqual(first.pointer, second.pointer))

    def identity(self, element):
        return self.cf.CFHash(element.pointer)

    def _cfstring(self, text):
        encoded = text.encode('utf-8')
        buffer = C.create_string_buffer(encoded)
        pointer = self.cf.CFStringCreateWithBytes(None, buffer, len(encoded), 0x08000100, False)
        if not pointer:
            raise TargetError('text_encoding', '无法编码输入文字。')
        return pointer

    def _check(self, code):
        if code:
            if code == -25211:
                raise TargetError('permission_required', '请在系统设置的辅助功能中授权 Gamepad Studio。')
            raise TargetError('ax_error', f'应用辅助功能接口不可用（{code}），请重新定位输入框。')

    def _convert(self, pointer):
        kind = self.cf.CFGetTypeID(pointer)
        if kind == self.ax.AXUIElementGetTypeID():
            return self._wrap(self.cf.CFRetain(pointer))
        if kind == self.cf.CFStringGetTypeID():
            length = self.cf.CFStringGetLength(pointer)
            if length < 0 or length > 1_000_000:
                raise TargetError('value_too_large', '应用文字超出读取上限。')
            buffer = (C.c_uint16 * length)()
            self.cf.CFStringGetCharacters(pointer, CFRange(0, length), buffer)
            return bytes(buffer).decode('utf-16-le')
        if kind == self.cf.CFBooleanGetTypeID():
            return bool(self.cf.CFBooleanGetValue(pointer))
        if kind == self.cf.CFArrayGetTypeID():
            count = self.cf.CFArrayGetCount(pointer)
            if count < 0 or count > 4000:
                raise TargetError('tree_limit', '应用可访问性树过大，无法明确选择输入框。')
            result = []
            try:
                for index in range(count):
                    result.append(self._convert(self.cf.CFArrayGetValueAtIndex(pointer, index)))
            except Exception:
                for value in result:
                    if isinstance(value, _AXRef):
                        value.close()
                raise
            return result
        if kind == self.ax.AXValueGetTypeID() and self.ax.AXValueGetType(pointer) == 4:
            value = CFRange()
            if self.ax.AXValueGetValue(pointer, 4, C.byref(value)):
                return value.location, value.length
        return None

    def get(self, element, attribute):
        name, output = self._cfstring(attribute), C.c_void_p()
        try:
            code = self.ax.AXUIElementCopyAttributeValue(element.pointer, name, C.byref(output))
            if code in (-25205, -25212):  # Unsupported optional attribute / no value.
                return None
            self._check(code)
            return self._convert(output.value) if output.value else None
        finally:
            self.cf.CFRelease(name)
            if output.value:
                self.cf.CFRelease(output.value)

    def settable(self, element, attribute):
        name, value = self._cfstring(attribute), C.c_ubyte()
        try:
            code = self.ax.AXUIElementIsAttributeSettable(element.pointer, name, C.byref(value))
            if code in (-25205, -25212):
                return False
            self._check(code)
            return bool(value.value)
        finally:
            self.cf.CFRelease(name)

    def set(self, element, attribute, value):
        name, owned = self._cfstring(attribute), None
        try:
            if type(value) is bool:
                pointer = self._true if value else self._false
            elif isinstance(value, str):
                pointer = owned = self._cfstring(value)
            elif isinstance(value, tuple) and len(value) == 2:
                native_range = CFRange(*value)
                pointer = owned = self.ax.AXValueCreate(4, C.byref(native_range))
                if not pointer:
                    raise TargetError('ax_error', '无法设置输入光标位置。')
            else:
                raise TypeError('Unsupported Accessibility attribute value')
            self._check(self.ax.AXUIElementSetAttributeValue(element.pointer, name, pointer))
        finally:
            self.cf.CFRelease(name)
            if owned:
                self.cf.CFRelease(owned)

    def action(self, element, name):
        value = self._cfstring(name)
        try:
            self._check(self.ax.AXUIElementPerformAction(element.pointer, value))
        finally:
            self.cf.CFRelease(value)


class MacAppTarget:
    def __init__(self, native=None, *, clock=None, wait=None):
        self._native = native
        self._clock = clock or time.monotonic
        self._wait = wait

    @property
    def native(self):
        if self._native is None:
            self._native = _MacNative()
        return self._native

    def list_running_targets(self):
        return [dict(item, name=TARGETS[item['bundle_id']]) for item in self.native.running_apps()
                if item.get('bundle_id') in TARGETS]

    def _running(self, bundle_id):
        if bundle_id not in TARGETS:
            raise TargetError('unsupported_target', '只能选择已经运行的 Codex 或 Google Antigravity。')
        matches = [item for item in self.native.running_apps() if item.get('bundle_id') == bundle_id]
        if not matches:
            raise TargetError('not_running', '目标应用尚未运行，请先手动打开应用。')
        if len(matches) != 1:
            raise TargetError('ambiguous_application', '发现多个目标应用进程，请明确保留一个。')
        app = matches[0]
        if not app.get('pid') or app.get('generation') is None:
            raise TargetError('generation_unavailable', '无法确认目标进程代次，已停止输入。')
        return app

    def _trusted(self):
        if not self.native.trusted():
            raise TargetError('permission_required', '请在系统设置的辅助功能中授权 Gamepad Studio；授权后重新定位输入框。')

    @staticmethod
    def _process_key(app):
        return app.get('bundle_id'), app.get('pid'), app.get('generation')

    def _wait_once(self, remaining):
        wait = self._wait or getattr(self.native, 'wait', time.sleep)
        wait(min(_CONFIRM_INTERVAL, max(0, remaining)))

    def _confirm_frontmost(self, expected):
        deadline = self._clock() + _ACTIVATION_TIMEOUT
        while True:
            current = self._running(expected['bundle_id'])
            if self._process_key(current) != self._process_key(expected):
                raise TargetError('stale_target', '目标应用已经重启，请重新定位输入框。')
            frontmost = self.native.frontmost()
            if frontmost and self._process_key(frontmost) == self._process_key(expected):
                return
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise TargetError('activation_pending', '目标应用尚未确认前台激活，请手动切换后重试。')
            self._wait_once(remaining)

    def _wake_process(self, app):
        current = self._running(app['bundle_id'])
        if self._process_key(current) != self._process_key(app):
            raise TargetError('stale_target', '目标应用已经重启，请重新定位输入框。')
        frontmost = self.native.frontmost()
        if not frontmost or self._process_key(frontmost) != self._process_key(app):
            if not self.native.activate(app):
                raise TargetError('activation_failed', '目标应用未能激活，请手动切换到目标窗口。')
        self._confirm_frontmost(app)
        return dict(app, name=TARGETS[app['bundle_id']], activation_requested=True)

    def wake(self, bundle_id):
        return self._wake_process(self._running(bundle_id))

    def _labels(self, element):
        return [value for attribute in _LABELS
                if isinstance(value := self.native.get(element, attribute), str) and value]

    def _composer(self, element):
        if self.native.get(element, 'AXRole') not in _TEXT_ROLES:
            return False
        if self.native.get(element, 'AXSubrole') == 'AXSecureTextField':
            return False
        if self.native.get(element, 'AXEnabled') is not True or self.native.get(element, 'AXHidden') is True:
            return False
        labels = [_semantic_label(label) for label in self._labels(element)]
        if any(_NON_COMPOSER.search(label) for label in labels):
            return False
        return any(_COMPOSER.search(label) or any(token in label for token in ('消息输入', '聊天输入', '提示词输入'))
                   for label in labels)

    def _walk(self, window):
        stack = [(self.native.retain(window), 0)]
        owned, seen, count = [], {}, 0
        deadline = time.monotonic() + 4
        try:
            while stack:
                element, depth = stack.pop()
                owned.append(element)
                identity = self.native.identity(element)
                if any(self.native.equal(element, previous) for previous in seen.get(identity, [])):
                    continue
                seen.setdefault(identity, []).append(element)
                count += 1
                if count > 1200 or depth > 36 or time.monotonic() > deadline:
                    raise TargetError('tree_limit', '应用可访问性树未能完整检查，无法明确选择输入框。')
                if self.native.get(element, 'AXHidden') is True:
                    continue
                yield element
                if self.native.get(element, 'AXRole') in _TEXT_ROLES | {'AXStaticText'}:
                    continue
                children = self.native.get(element, 'AXChildren') or []
                if not isinstance(children, list):
                    raise TargetError('ax_error', '应用子控件格式异常，无法确认输入框。')
                stack.extend((child, depth + 1) for child in children)
        finally:
            for element in owned:
                self.native.release(element)
            for element, _ in stack:
                self.native.release(element)

    def _select_window(self, app):
        window = self.native.get(app, 'AXFocusedWindow')
        if window is None:
            window = self.native.get(app, 'AXMainWindow')
        if window is None:
            windows = self.native.get(app, 'AXWindows') or []
            try:
                if len(windows) != 1:
                    raise TargetError('ambiguous_window', '无法明确选择目标窗口，请先手动切换到聊天窗口。')
                window = self.native.retain(windows[0])
            finally:
                for candidate in windows:
                    self.native.release(candidate)
        try:
            if (self.native.get(window, 'AXRole') != 'AXWindow'
                    or self.native.get(window, 'AXMinimized') is True
                    or self.native.get(window, 'AXModal') is True):
                raise TargetError('window_unavailable', '目标窗口处于最小化或对话框状态，请先打开聊天窗口。')
        except Exception:
            self.native.release(window)
            raise
        return window

    def locate(self, bundle_id):
        running = self._running(bundle_id)
        self._trusted()
        app, window, candidates = None, None, []
        try:
            app = self.native.application(running['pid'])
            window = self._select_window(app)
            for element in self._walk(window):
                if self._composer(element):
                    candidates.append(self.native.retain(element))
            if len(candidates) != 1:
                raise TargetError('ambiguous_composer' if candidates else 'composer_not_found',
                                  '聊天输入框不明确，请先手动打开目标聊天窗口。')
            composer = candidates[0]
            draft = self.native.get(composer, 'AXValue')
            if not isinstance(draft, str):
                raise TargetError('draft_unavailable', '无法读取已有草稿，已停止输入。')
            return ComposerTarget(bundle_id, running['pid'], running['generation'], TARGETS[bundle_id],
                                  self.native.get(window, 'AXTitle') or '', draft, self, app, window, composer)
        except Exception:
            for element in candidates + [window, app]:
                self.native.release(element)
            raise

    def _validate(self, target, require_focus=False, require_window=False, check_composer=True):
        if not isinstance(target, ComposerTarget) or target._owner is not self or target._closed:
            raise TargetError('stale_target', '输入目标已关闭或失效，请重新定位。')
        app = self._running(target.bundle_id)
        if (app['pid'], app['generation']) != (target.pid, target.generation):
            raise TargetError('stale_target', '目标应用已经重启，请重新定位输入框。')
        self._trusted()
        if self.native.get(target._window, 'AXRole') != 'AXWindow':
            raise TargetError('window_unavailable', '原目标窗口已关闭，请重新定位。')
        if check_composer and not self._composer(target._composer):
            raise TargetError('composer_changed', '聊天输入控件已改变，请重新定位。')
        if require_focus or require_window:
            frontmost = self.native.frontmost()
            if not frontmost or (frontmost.get('bundle_id'), frontmost.get('pid'), frontmost.get('generation')) != (
                    target.bundle_id, target.pid, target.generation):
                raise TargetError('focus_changed', '前台应用已经改变，已停止输入。')
            window, focused = None, None
            try:
                window = self.native.get(target._app, 'AXFocusedWindow')
                focused = self.native.get(target._app, 'AXFocusedUIElement') if require_focus else None
                if (not self.native.equal(window, target._window)
                        or require_focus and not self.native.equal(focused, target._composer)):
                    raise TargetError('focus_changed', '目标窗口或输入焦点已改变，已停止输入。')
            finally:
                self.native.release(window)
                self.native.release(focused)
        return app

    def focus(self, target):
        expected = self._validate(target)
        self._confirm_frontmost(expected)
        self.native.action(target._window, 'AXRaise')
        if not self.native.settable(target._composer, 'AXFocused'):
            raise TargetError('focus_unsupported', '该聊天输入框不支持辅助功能聚焦，请手动点击输入框。')
        self.native.set(target._composer, 'AXFocused', True)
        self._confirm_window(target, expected, require_focus=True)
        return self.inspect(target)

    def _confirm_window(self, target, expected, require_focus=False):
        deadline = self._clock() + _FOCUS_TIMEOUT
        while True:
            try:
                self._validate(target, require_focus=require_focus, require_window=True,
                               check_composer=require_focus)
                break
            except TargetError as exc:
                # Only a pending window/element focus may settle. A stale
                # process, removed composer or permission failure is final.
                if exc.code != 'focus_changed':
                    raise
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise
                frontmost = self.native.frontmost()
                if not frontmost or self._process_key(frontmost) != self._process_key(expected):
                    raise
                self._wait_once(remaining)

    def activate(self, target):
        expected = self._validate(target)
        self._wake_process(expected)
        return self.focus(target)

    def activate_window(self, target):
        """Recover an owned recording window even when its composer is hidden."""
        expected = self._validate(target, check_composer=False)
        self._wake_process(expected)
        self._validate(target, check_composer=False)
        self.native.action(target._window, 'AXRaise')
        self._confirm_window(target, expected)
        return self.inspect(target)

    def inspect(self, target):
        result = dict(running=False, frontmost=False, input_focused=False, target_available=False,
                      generation=getattr(target, 'generation', None), draft='', reason='')
        try:
            self._validate(target, check_composer=False)
            result['running'] = True
            frontmost = self.native.frontmost()
            result['frontmost'] = bool(frontmost and (frontmost.get('bundle_id'), frontmost.get('pid'),
                                                    frontmost.get('generation')) == (
                target.bundle_id, target.pid, target.generation))
            self._validate(target)
            result['target_available'] = True
            try:
                self._validate(target, require_focus=True)
                result['input_focused'] = True
            except TargetError as exc:
                if exc.code != 'focus_changed':
                    raise
            draft = self.native.get(target._composer, 'AXValue')
            if not isinstance(draft, str):
                raise TargetError('draft_unavailable', '无法读取已有草稿。')
            result['draft'] = draft
        except TargetError as exc:
            result['reason'], result['code'] = exc.reason, exc.code
        return result

    def append_text(self, target, text, separator='\n'):
        if not isinstance(text, str) or not text.strip() or '\x00' in text or len(text) > 100_000:
            raise TargetError('invalid_text', '待填入文字为空、包含无效字符或过长。')
        if not isinstance(separator, str):
            raise TypeError('Text separator must be a string')
        self._validate(target, require_focus=True)
        draft = self.native.get(target._composer, 'AXValue')
        if not isinstance(draft, str):
            raise TargetError('draft_unavailable', '无法读取已有草稿，已停止输入。')
        addition = (separator if draft and not draft[-1].isspace() and not text[0].isspace() else '') + text
        selected_text = all(self.native.settable(target._composer, name)
                            for name in ('AXSelectedTextRange', 'AXSelectedText'))
        if selected_text:
            # Prefer insertion into a zero-length selection at the end.
            end = len(draft.encode('utf-16-le')) // 2
            self.native.set(target._composer, 'AXSelectedTextRange', (end, 0))
            if (self.native.get(target._composer, 'AXValue') != draft
                    or self.native.get(target._composer, 'AXSelectedTextRange') != (end, 0)):
                raise TargetError('draft_changed', '草稿或选区已经改变，已停止输入以保留现有内容。')
            self._validate(target, require_focus=True)
            if (self.native.get(target._composer, 'AXValue') != draft
                    or self.native.get(target._composer, 'AXSelectedTextRange') != (end, 0)):
                raise TargetError('draft_changed', '草稿或选区已经改变，已停止输入以保留现有内容。')
            self.native.set(target._composer, 'AXSelectedText', addition)
        else:
            if not self.native.settable(target._composer, 'AXValue'):
                raise TargetError('append_unsupported', '该输入框不支持追加文字，请手动粘贴。')
            # Chromium may expose only writable AXValue. Preserve the latest
            # draft and recheck it after the final process/window/focus checks.
            # A failed write never falls back or retries another input method.
            self._validate(target, require_focus=True)
            if self.native.get(target._composer, 'AXValue') != draft:
                raise TargetError('draft_changed', '草稿已经改变，已停止输入以保留现有内容。')
            self.native.set(target._composer, 'AXValue', draft + addition)
        after = self.native.get(target._composer, 'AXValue')
        if after != draft + addition:
            raise TargetError('append_unconfirmed', '应用未确认完整追加，请检查输入框；没有自动发送。')
        target.draft = after
        return after

    def _voice_controls(self, target):
        controls = []
        try:
            for element in self._walk(target._window):
                if (self.native.get(element, 'AXRole') != 'AXButton'
                        or self.native.get(element, 'AXEnabled') is not True):
                    continue
                labels = {_semantic_label(label) for label in self._labels(element)}
                if any(_SUBMIT.search(label) for label in labels):
                    continue
                for state, accepted in (('idle', _VOICE_START), ('recording', _VOICE_STOP),
                                        ('native_voice_memo', _VOICE_MEMO)):
                    if labels & accepted:
                        controls.append((state, self.native.retain(element)))
            return controls
        except Exception:
            for _, element in controls:
                self.native.release(element)
            raise

    def dictation_status(self, target):
        controls = []
        try:
            self._validate(target, check_composer=False)
            controls = self._voice_controls(target)
            if len(controls) == 1:
                if controls[0][0] == 'native_voice_memo':
                    return dict(supported=False, state='unknown', mode='native_voice_memo',
                                reason='当前入口标为「Record voice memo」（语音备忘录）；尚未确认文字听写模式与停止控件，不能自动启停。')
                return dict(supported=True, state=controls[0][0], reason='')
            return dict(supported=False, state='unknown', reason='未找到唯一且状态明确的语音输入控件。')
        except TargetError as exc:
            return dict(supported=False, state='unknown', reason=exc.reason)
        finally:
            for _, element in controls:
                self.native.release(element)

    def _dictation_action(self, target, expected):
        start = expected == 'idle'
        attempted = False
        try:
            self._validate(target, require_focus=start, require_window=True, check_composer=start)
            controls = self._voice_controls(target)
            try:
                if len(controls) != 1 or controls[0][0] != expected:
                    raise TargetError('dictation_unknown', '语音输入状态未知或已经改变，已停止操作。')
                self._validate(target, require_focus=start, require_window=True, check_composer=start)
                current_labels = {_semantic_label(label) for label in self._labels(controls[0][1])}
                accepted = _VOICE_START if expected == 'idle' else _VOICE_STOP
                if (not current_labels & accepted or any(_SUBMIT.search(label) for label in current_labels)
                        or self.native.get(controls[0][1], 'AXEnabled') is not True):
                    raise TargetError('dictation_unknown', '语音控件已经改变，已停止操作。')
                # A timeout may arrive after the application acted. Mark the
                # attempt before crossing into AX, without claiming success.
                attempted = True
                self.native.action(controls[0][1], 'AXPress')
            finally:
                for _, element in controls:
                    self.native.release(element)
        except Exception as exc:
            exc.action_requested = attempted
            raise
        # AXPress has already succeeded. Electron may update the accessible
        # label later, so return the observation without inviting a retry of
        # a Stop control that could now toggle recording back on.
        try:
            status = self.dictation_status(target)
        except Exception as exc:
            status = dict(supported=False, state='unknown',
                          reason=exc.reason if isinstance(exc, TargetError) else str(exc))
        destination = 'recording' if expected == 'idle' else 'idle'
        return dict(status, action_requested=True,
                    pending=not status.get('supported', False) or status.get('state') != destination)

    def start_dictation(self, target):
        return self._dictation_action(target, 'idle')

    def stop_dictation(self, target):
        return self._dictation_action(target, 'recording')

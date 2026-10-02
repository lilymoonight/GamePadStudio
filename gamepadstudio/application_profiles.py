"""Device-owned application rules and read-only Windows foreground detection."""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import ntpath
import re
import sys


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PATH_ERROR = '请选择 Windows 应用的完整 .exe 路径'


def normalize_executable(path):
    """Accept complete Win32 executable paths without requiring installation."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError(_PATH_ERROR)
    path = path.strip().replace('/', '\\')
    # QueryFullProcessImageNameW normally returns Win32 paths. Treat extended
    # spelling of the same Win32 path identically if an imported rule uses it.
    if path[:8].casefold() == '\\\\?\\unc\\':
        path = '\\\\' + path[8:]
    elif path.startswith('\\\\?\\'):
        path = path[4:]
    drive, tail = ntpath.splitdrive(path)
    drive_path = bool(re.fullmatch(r'[A-Za-z]:', drive))
    unc_path = drive.startswith('\\\\') and len(drive[2:].split('\\')) == 2
    if not (drive_path or unc_path) or not tail.startswith('\\') or tail == '\\':
        raise ValueError(_PATH_ERROR)
    if any(ord(char) < 32 or char in '<>"|?*' for char in path):
        raise ValueError(_PATH_ERROR)
    if ':' in tail or (unc_path and ':' in drive):
        raise ValueError(_PATH_ERROR)
    if any(part.endswith((' ', '.')) for part in path.split('\\') if part):
        raise ValueError(_PATH_ERROR)
    path = ntpath.normpath(path)
    if ntpath.splitext(path)[1].casefold() != '.exe':
        raise ValueError(_PATH_ERROR)
    return path


def canonical_executable(path):
    """Case-insensitive full-path identity; equal basenames never imply a match."""
    return normalize_executable(path).casefold()


def normalize_application_profiles(value, config, state):
    """Validate one device's settings before a single authoritative write."""
    from .studio_core import profile_scope

    if value is None:
        return {'enabled': False, 'rules': []}
    if not isinstance(value, dict) or set(value) - {'enabled', 'rules'}:
        raise ValueError('应用关联设置格式错误')
    enabled, rules = value.get('enabled', False), value.get('rules', [])
    if not isinstance(enabled, bool) or not isinstance(rules, list):
        raise ValueError('应用关联设置格式错误')
    if state is None and (enabled or rules):
        raise ValueError('请先连接手柄，再设置应用关联')
    scope = profile_scope(state)
    owners = config.get('profile_devices', {})
    profiles = config.get('profiles', {})
    allowed = {name for name in profiles if owners.get(name) == scope}
    normalized, seen = [], set()
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {'executable', 'profile'}:
            raise ValueError('应用关联规则格式错误')
        executable = normalize_executable(rule['executable'])
        canonical = canonical_executable(executable)
        if canonical in seen:
            raise ValueError('同一个应用只能关联一个预设')
        profile = rule['profile']
        if not isinstance(profile, str) or profile not in allowed:
            raise ValueError('该预设不属于当前输入设备')
        seen.add(canonical)
        normalized.append({'executable': executable, 'profile': profile})
    return {'enabled': enabled, 'rules': normalized}


class ForegroundApplicationReader:
    """Query only the foreground process with ordinary limited access rights."""
    def __init__(self, user32=None, kernel32=None):
        self.user32 = user32 or C.WinDLL('user32', use_last_error=True)
        self.kernel32 = kernel32 or C.WinDLL('kernel32', use_last_error=True)
        specs = (
            (self.user32, 'GetForegroundWindow', [], W.HWND),
            (self.user32, 'GetWindowThreadProcessId', [W.HWND, C.POINTER(W.DWORD)], W.DWORD),
            (self.kernel32, 'OpenProcess', [W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
            (self.kernel32, 'QueryFullProcessImageNameW',
             [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)], W.BOOL),
            (self.kernel32, 'CloseHandle', [W.HANDLE], W.BOOL),
        )
        for library, name, args, result in specs:
            function = getattr(library, name)
            function.argtypes, function.restype = args, result

    def read(self):
        result = {'pid': 0, 'hwnd': 0, 'executable': ''}
        handle = None
        try:
            hwnd = self.user32.GetForegroundWindow()
            if not hwnd:
                return result
            result['hwnd'] = int(hwnd)
            pid = W.DWORD()
            thread = self.user32.GetWindowThreadProcessId(hwnd, C.byref(pid))
            if not thread or not pid.value:
                return result
            result['pid'] = int(pid.value)
            handle = self.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
            if not handle:
                return result
            buffer = C.create_unicode_buffer(32768)
            length = W.DWORD(len(buffer))
            if self.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, C.byref(length)):
                if 0 < length.value < len(buffer):
                    result['executable'] = normalize_executable(buffer[:length.value])
            # A process can disappear or lose focus while its image is queried.
            # Never apply its rule to a different foreground window.
            if self.user32.GetForegroundWindow() != hwnd:
                return {'pid': 0, 'hwnd': 0, 'executable': ''}
        except (OSError, ValueError, TypeError, AttributeError):
            result['executable'] = ''
        finally:
            if handle:
                try:
                    self.kernel32.CloseHandle(handle)
                except (OSError, ValueError, TypeError):
                    pass
        return result


_foreground_reader = None


def foreground_application():
    """Safe process metadata for the current foreground window, never a scan."""
    global _foreground_reader
    if sys.platform != 'win32':
        return {'pid': 0, 'hwnd': 0, 'executable': ''}
    try:
        if _foreground_reader is None:
            _foreground_reader = ForegroundApplicationReader()
        return _foreground_reader.read()
    except (OSError, ValueError, TypeError, AttributeError):
        return {'pid': 0, 'hwnd': 0, 'executable': ''}


def _foreground_identity(foreground):
    foreground = foreground if isinstance(foreground, dict) else {}
    try:
        executable = canonical_executable(foreground.get('executable', ''))
    except ValueError:
        executable = ''
    return (foreground.get('hwnd', 0), foreground.get('pid', 0), executable)


class ApplicationProfileResolver:
    """Choose profiles without changing configuration or sending input."""
    def __init__(self, protected_pids=None):
        self.protected_pids = set(protected_pids or ())
        self._protected_pids = set(self.protected_pids)
        self._scope = None
        self._last_identity = None
        self._manual_profile = None
        self._manual_identity = None
        self._status = {'automatic': False, 'executable': '', 'profile': ''}

    def manual_selection(self, profile, foreground=None):
        self._manual_profile = profile
        frozen = (isinstance(foreground, dict)
                  and foreground.get('pid') in self._protected_pids)
        self._manual_identity = (_foreground_identity(foreground)
                                 if foreground is not None and not frozen else self._last_identity)
        self._status = dict(self._status, automatic=False, profile=profile)

    def resolve(self, config, state, foreground, now, editing=False, preview=False,
                protected_pids=None):
        from .studio_core import profile_scope

        self._protected_pids = self.protected_pids | set(protected_pids or ())
        scope = profile_scope(state)
        if scope != self._scope:
            previous_scope = self._scope
            self._scope = scope
            self._last_identity = None
            if previous_scope is not None:
                self._manual_profile = self._manual_identity = None
            self._status = {'automatic': False, 'executable': '', 'profile': ''}
        profiles = config.get('profiles', {})
        profiles = profiles if isinstance(profiles, dict) else {}
        owners = config.get('profile_devices', {})
        owners = owners if isinstance(owners, dict) else {}
        allowed = [name for name in profiles if owners.get(name) == scope]
        current = config.get('active_profile', '')
        remembered = config.get('controller_profiles', {})
        remembered = remembered if isinstance(remembered, dict) else {}
        baseline = remembered.get(scope, '')
        if baseline not in allowed:
            baseline = current if current in allowed else next(iter(allowed), '')
        application_profiles = config.get('application_profiles', {})
        application_profiles = application_profiles if isinstance(application_profiles, dict) else {}
        settings = application_profiles.get(scope, {})
        settings = settings if isinstance(settings, dict) else {}
        if not state or settings.get('enabled') is not True:
            self._manual_profile = self._manual_identity = None
            self._last_identity = None
            self._status = {'automatic': False, 'executable': '', 'profile': baseline}
            return dict(self._status)
        foreground = foreground if isinstance(foreground, dict) else {}
        if editing or preview or foreground.get('pid') in self._protected_pids:
            if self._status['automatic']:
                previous_profile = self._status['profile']
                previous_path = (self._last_identity or (0, 0, ''))[2]
                rules = settings.get('rules', [])
                valid = False
                for rule in rules if isinstance(rules, list) else []:
                    if not isinstance(rule, dict) or rule.get('profile') != previous_profile:
                        continue
                    try:
                        valid = (previous_profile in allowed and previous_path
                                 and canonical_executable(rule.get('executable', '')) == previous_path)
                    except ValueError:
                        pass
                    if valid:
                        break
                if not valid:
                    self._status = dict(self._status, automatic=False, profile=baseline)
                    return dict(self._status)
                if current != previous_profile:
                    self._status['automatic'] = False
            self._status['profile'] = current if current in allowed else baseline
            return dict(self._status)
        identity = _foreground_identity(foreground)
        self._last_identity = identity
        if self._manual_profile is not None:
            if self._manual_identity is None:
                self._manual_identity = identity
            if identity == self._manual_identity and self._manual_profile in allowed:
                self._status = {'automatic': False, 'executable': foreground.get('executable', '')
                                if identity[2] else '', 'profile': self._manual_profile}
                return dict(self._status)
            self._manual_profile = self._manual_identity = None
        target = baseline
        automatic = False
        rules = settings.get('rules', [])
        if identity[2] and isinstance(rules, list):
            for rule in rules:
                if not isinstance(rule, dict) or rule.get('profile') not in allowed:
                    continue
                try:
                    matches = canonical_executable(rule.get('executable', '')) == identity[2]
                except ValueError:
                    continue
                if matches:
                    target, automatic = rule['profile'], True
                    break
        self._status = {'automatic': automatic, 'executable': foreground.get('executable', '')
                        if identity[2] else '', 'profile': target}
        return dict(self._status)

"""Read physical display timing/color state without changing Windows settings.

``enumerate_displays`` returns native display rows: Windows desktop pixels,
macOS CG points with explicit physical-pixel dimensions and Retina scale.
``match_monitors`` associates them with MSS's monitor indices by geometry.
``capture_display_info`` aggregates the screens touched by a capture rectangle.
Unknown refresh/HDR values are None; unknown rates are never replaced by 60 Hz.

Win32 layouts and semantics are based on Microsoft Learn and the public SDK:
https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-querydisplayconfig
https://learn.microsoft.com/en-us/windows/win32/api/wingdi/ns-wingdi-displayconfig_sdr_white_level
https://raw.githubusercontent.com/microsoft/win32metadata/main/generation/WinSDK/RecompiledIdlHeaders/um/wingdi.h
"""
from __future__ import annotations

import ctypes as C
import math
import sys
from collections.abc import Mapping, Sequence


_UINT = C.c_uint32
_INT = C.c_int32
_WORD = C.c_uint16
_WCHAR = C.c_uint16  # Win32 UTF-16 remains two bytes on every test platform.
_SUCCESS = 0
_INSUFFICIENT_BUFFER = 122
_INVALID_PARAMETER = 87
_QDC_ACTIVE = 2
_VIRTUAL_MODE = 0x10
_VIRTUAL_REFRESH = 0x40
_PATH_VIRTUAL = 8


class _LUID(C.Structure):
    _fields_ = [('LowPart', _UINT), ('HighPart', _INT)]


class _POINT(C.Structure):
    _fields_ = [('x', _INT), ('y', _INT)]


class _RECT(C.Structure):
    _fields_ = [('left', _INT), ('top', _INT), ('right', _INT), ('bottom', _INT)]


class _RATIONAL(C.Structure):
    _fields_ = [('numerator', _UINT), ('denominator', _UINT)]


class _REGION(C.Structure):
    _fields_ = [('cx', _UINT), ('cy', _UINT)]


class _SOURCE_INFO(C.Structure):
    _fields_ = [('adapterId', _LUID), ('id', _UINT), ('modeInfoIdx', _UINT), ('statusFlags', _UINT)]


class _TARGET_INFO(C.Structure):
    _fields_ = [('adapterId', _LUID), ('id', _UINT), ('modeInfoIdx', _UINT),
                ('outputTechnology', _UINT), ('rotation', _UINT), ('scaling', _UINT),
                ('refreshRate', _RATIONAL), ('scanLineOrdering', _UINT),
                ('targetAvailable', _INT), ('statusFlags', _UINT)]


class _PATH_INFO(C.Structure):
    _fields_ = [('sourceInfo', _SOURCE_INFO), ('targetInfo', _TARGET_INFO), ('flags', _UINT)]


class _VIDEO_SIGNAL(C.Structure):
    _fields_ = [('pixelRate', C.c_uint64), ('hSyncFreq', _RATIONAL), ('vSyncFreq', _RATIONAL),
                ('activeSize', _REGION), ('totalSize', _REGION),
                ('videoStandard', _UINT), ('scanLineOrdering', _UINT)]


class _SOURCE_MODE(C.Structure):
    _fields_ = [('width', _UINT), ('height', _UINT), ('pixelFormat', _UINT), ('position', _POINT)]


class _MODE_UNION(C.Union):
    _fields_ = [('targetMode', _VIDEO_SIGNAL), ('sourceMode', _SOURCE_MODE)]


class _MODE_INFO(C.Structure):
    _fields_ = [('infoType', _UINT), ('id', _UINT), ('adapterId', _LUID), ('mode', _MODE_UNION)]


class _HEADER(C.Structure):
    _fields_ = [('type', _UINT), ('size', _UINT), ('adapterId', _LUID), ('id', _UINT)]


class _SOURCE_NAME(C.Structure):
    _fields_ = [('header', _HEADER), ('viewGdiDeviceName', _WCHAR * 32)]


class _TARGET_NAME(C.Structure):
    _fields_ = [('header', _HEADER), ('flags', _UINT), ('outputTechnology', _UINT),
                ('edidManufactureId', _WORD), ('edidProductCodeId', _WORD),
                ('connectorInstance', _UINT), ('monitorFriendlyDeviceName', _WCHAR * 64),
                ('monitorDevicePath', _WCHAR * 128)]


class _ADVANCED_COLOR(C.Structure):
    _fields_ = [('header', _HEADER), ('value', _UINT), ('colorEncoding', _UINT), ('bitsPerColorChannel', _UINT)]


class _ADVANCED_COLOR_2(C.Structure):
    _fields_ = _ADVANCED_COLOR._fields_ + [('activeColorMode', _UINT)]


class _SDR_WHITE(C.Structure):
    _fields_ = [('header', _HEADER), ('SDRWhiteLevel', _UINT)]


class _MONITOR_INFO(C.Structure):
    _fields_ = [('cbSize', _UINT), ('rcMonitor', _RECT), ('rcWork', _RECT),
                ('dwFlags', _UINT), ('szDevice', _WCHAR * 32)]


class _DEVMODE(C.Structure):
    # The display arm of DEVMODEW's 16-byte printer/display union.
    _fields_ = [('dmDeviceName', _WCHAR * 32), ('dmSpecVersion', _WORD),
                ('dmDriverVersion', _WORD), ('dmSize', _WORD), ('dmDriverExtra', _WORD),
                ('dmFields', _UINT), ('dmPosition', _POINT), ('dmDisplayOrientation', _UINT),
                ('dmDisplayFixedOutput', _UINT), ('dmColor', C.c_int16), ('dmDuplex', C.c_int16),
                ('dmYResolution', C.c_int16), ('dmTTOption', C.c_int16), ('dmCollate', C.c_int16),
                ('dmFormName', _WCHAR * 32), ('dmLogPixels', _WORD), ('dmBitsPerPel', _UINT),
                ('dmPelsWidth', _UINT), ('dmPelsHeight', _UINT), ('dmDisplayFlags', _UINT),
                ('dmDisplayFrequency', _UINT), ('dmICMMethod', _UINT), ('dmICMIntent', _UINT),
                ('dmMediaType', _UINT), ('dmDitherType', _UINT), ('dmReserved1', _UINT),
                ('dmReserved2', _UINT), ('dmPanningWidth', _UINT), ('dmPanningHeight', _UINT)]


def _wide_text(array):
    return bytes(array).decode('utf-16-le', errors='replace').split('\0', 1)[0]


def refresh_hz(numerator, denominator):
    """Convert a valid Windows rational without replacing unknown rates by 60."""
    try:
        if isinstance(numerator, bool) or isinstance(denominator, bool):
            return None
        numerator, denominator = float(numerator), float(denominator)
        rate = numerator / denominator
        return rate if math.isfinite(rate) and rate > 0 else None
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None


def format_refresh(value):
    value = refresh_hz(value, 1)
    if value is None:
        return '刷新率未知'
    return f'{value:.3f}'.rstrip('0').rstrip('.') + ' Hz'


def decode_color_info(value, *, version=1, active_color_mode=None, bits_per_channel=None):
    """Legacy advanced color includes WCG; enabled never implies HDR on its own."""
    result = {'hdr_enabled': None, 'hdr_supported': None, 'hdr_user_enabled': None,
              'advanced_color_enabled': bool(value & 2), 'advanced_color_supported': bool(value & 1),
              'bits_per_channel': bits_per_channel, 'color_mode': None}
    if version == 2:
        result['hdr_supported'] = bool(value & 0x10)
        result['hdr_user_enabled'] = bool(value & 0x20)
        result['color_mode'] = {0: 'sdr', 1: 'wcg', 2: 'hdr'}.get(active_color_mode)
        if result['color_mode'] is not None:
            result['hdr_enabled'] = active_color_mode == 2
    elif not result['advanced_color_enabled']:
        result['hdr_enabled'], result['color_mode'] = False, 'sdr'
    elif value & 4:
        # The SDK explicitly describes wideColorEnforced as WCG enabled,
        # but the legacy packet has no active HDR mode discriminator.
        result['color_mode'] = 'advanced'
    return result


def _request(user32, packet_type, request_type, adapter, identifier):
    packet = packet_type()
    packet.header = _HEADER(request_type, C.sizeof(packet), adapter, identifier)
    return packet if user32.DisplayConfigGetDeviceInfo(C.byref(packet.header)) == _SUCCESS else None


def _color_state(user32, target):
    fields = {'hdr_enabled': None, 'hdr_supported': None, 'hdr_user_enabled': None,
              'advanced_color_enabled': None, 'advanced_color_supported': None,
              'bits_per_channel': None, 'color_mode': None, 'color_source': None,
              'sdr_white_nits': None}
    packet = _request(user32, _ADVANCED_COLOR_2, 15, target.adapterId, target.id)
    if packet is not None:
        fields.update(decode_color_info(packet.value, version=2, active_color_mode=packet.activeColorMode,
                                        bits_per_channel=packet.bitsPerColorChannel))
        fields['color_source'] = 'DisplayConfigAdvancedColorInfo2'
    else:
        packet = _request(user32, _ADVANCED_COLOR, 9, target.adapterId, target.id)
        if packet is not None:
            fields.update(decode_color_info(packet.value, bits_per_channel=packet.bitsPerColorChannel))
            fields['color_source'] = 'DisplayConfigAdvancedColorInfo'
    white = _request(user32, _SDR_WHITE, 11, target.adapterId, target.id)
    if white is not None and white.SDRWhiteLevel > 0:
        fields['sdr_white_nits'] = white.SDRWhiteLevel / 1000.0 * 80.0
    return fields


def _query_paths(user32):
    for flags in (_QDC_ACTIVE | _VIRTUAL_MODE | _VIRTUAL_REFRESH, _QDC_ACTIVE | _VIRTUAL_MODE, _QDC_ACTIVE):
        for _ in range(3):
            path_count, mode_count = _UINT(), _UINT()
            status = user32.GetDisplayConfigBufferSizes(flags, C.byref(path_count), C.byref(mode_count))
            if status != _SUCCESS:
                break
            if path_count.value > 512 or mode_count.value > 4096:
                return []
            paths = (_PATH_INFO * max(1, path_count.value))()
            modes = (_MODE_INFO * max(1, mode_count.value))()
            status = user32.QueryDisplayConfig(flags, C.byref(path_count), paths,
                                               C.byref(mode_count), modes, None)
            if status == _INSUFFICIENT_BUFFER:
                continue
            if status != _SUCCESS:
                break
            rows = []
            for path in paths[:path_count.value]:
                if not (path.flags & 1) or not path.targetInfo.targetAvailable:
                    continue
                source = _request(user32, _SOURCE_NAME, 1, path.sourceInfo.adapterId, path.sourceInfo.id)
                target = _request(user32, _TARGET_NAME, 2, path.targetInfo.adapterId, path.targetInfo.id)
                if source is None:
                    continue
                row = {'device_name': _wide_text(source.viewGdiDeviceName),
                       'friendly_name': _wide_text(target.monitorFriendlyDeviceName) if target else '',
                       'monitor_path': _wide_text(target.monitorDevicePath) if target else '',
                       'target_id': path.targetInfo.id,
                       'adapter_id': [path.targetInfo.adapterId.LowPart, path.targetInfo.adapterId.HighPart]}
                virtual = bool(flags & _VIRTUAL_MODE and path.flags & _PATH_VIRTUAL)
                source_idx = path.sourceInfo.modeInfoIdx >> 16 if virtual else path.sourceInfo.modeInfoIdx
                target_idx = path.targetInfo.modeInfoIdx >> 16 if virtual else path.targetInfo.modeInfoIdx
                if source_idx < mode_count.value and modes[source_idx].infoType == 1:
                    mode = modes[source_idx].mode.sourceMode
                    row.update(left=mode.position.x, top=mode.position.y, width=mode.width, height=mode.height)
                rational = path.targetInfo.refreshRate
                row['desktop_refresh_hz'] = refresh_hz(rational.numerator, rational.denominator)
                if target_idx < mode_count.value and modes[target_idx].infoType == 2:
                    signal = modes[target_idx].mode.targetMode
                    if refresh_hz(signal.vSyncFreq.numerator, signal.vSyncFreq.denominator) is not None:
                        rational = signal.vSyncFreq
                row.update(refresh_numerator=rational.numerator, refresh_denominator=rational.denominator,
                           refresh_hz=refresh_hz(rational.numerator, rational.denominator),
                           refresh_source='QueryDisplayConfig')
                row.update(_color_state(user32, path.targetInfo))
                rows.append(row)
            return rows
    return []


def _enumerate_monitors(user32):
    callback_type = C.WINFUNCTYPE(_INT, C.c_void_p, C.c_void_p, C.POINTER(_RECT), C.c_ssize_t)
    user32.EnumDisplayMonitors.argtypes = [C.c_void_p, C.c_void_p, callback_type, C.c_ssize_t]
    user32.EnumDisplayMonitors.restype = _INT
    user32.GetMonitorInfoW.argtypes = [C.c_void_p, C.POINTER(_MONITOR_INFO)]
    user32.GetMonitorInfoW.restype = _INT
    rows = []

    @callback_type
    def receive(handle, _dc, _rect, _data):
        packet = _MONITOR_INFO()
        packet.cbSize = C.sizeof(packet)
        if user32.GetMonitorInfoW(handle, C.byref(packet)):
            rect = packet.rcMonitor
            rows.append({'device_name': _wide_text(packet.szDevice), 'primary': bool(packet.dwFlags & 1),
                         'left': rect.left, 'top': rect.top,
                         'width': rect.right - rect.left, 'height': rect.bottom - rect.top})
        return 1

    user32.EnumDisplayMonitors(None, None, receive, 0)
    return rows


def _fallback_mode(user32, device_name):
    mode = _DEVMODE()
    mode.dmSize = C.sizeof(mode)
    if not user32.EnumDisplaySettingsExW(device_name, 0xFFFFFFFF, C.byref(mode), 0):
        return {}
    result = {}
    if mode.dmFields & 0x20 and mode.dmFields & 0x80000 and mode.dmFields & 0x100000:
        result.update(left=mode.dmPosition.x, top=mode.dmPosition.y,
                      width=mode.dmPelsWidth, height=mode.dmPelsHeight)
    # 0/1 are documented hardware-default values, not measured 0/1 Hz.
    if mode.dmFields & 0x400000 and mode.dmDisplayFrequency > 1:
        result.update(refresh_hz=refresh_hz(mode.dmDisplayFrequency, 1),
                      refresh_numerator=mode.dmDisplayFrequency, refresh_denominator=1,
                      refresh_source='EnumDisplaySettingsEx')
    return result


def enumerate_displays():
    """Return active native monitor information; unsupported/error -> no guessed rows."""
    if sys.platform == 'darwin':
        from .macos_display import enumerate_displays as mac_displays
        try:
            return mac_displays()
        except (OSError, RuntimeError, ValueError):
            return []
    if sys.platform != 'win32':
        return []
    try:
        user32 = C.WinDLL('user32', use_last_error=True)
        user32.GetDisplayConfigBufferSizes.argtypes = [_UINT, C.POINTER(_UINT), C.POINTER(_UINT)]
        user32.GetDisplayConfigBufferSizes.restype = _INT
        user32.QueryDisplayConfig.argtypes = [_UINT, C.POINTER(_UINT), C.POINTER(_PATH_INFO),
                                              C.POINTER(_UINT), C.POINTER(_MODE_INFO), C.c_void_p]
        user32.QueryDisplayConfig.restype = _INT
        user32.DisplayConfigGetDeviceInfo.argtypes = [C.POINTER(_HEADER)]
        user32.DisplayConfigGetDeviceInfo.restype = _INT
        user32.EnumDisplaySettingsExW.argtypes = [C.c_wchar_p, _UINT, C.POINTER(_DEVMODE), _UINT]
        user32.EnumDisplaySettingsExW.restype = _INT
        paths = _query_paths(user32)
        rows = []
        for monitor in _enumerate_monitors(user32):
            matches = [path for path in paths if path['device_name'].casefold() == monitor['device_name'].casefold()]
            row = dict(monitor)
            row.update(_fallback_mode(user32, row['device_name']))
            if len(matches) == 1:
                row.update(matches[0])
            elif matches:
                row.update(aggregate_displays(matches))
                row['targets'] = matches
            row.setdefault('friendly_name', row['device_name'])
            for key in ('refresh_hz', 'refresh_numerator', 'refresh_denominator', 'refresh_source',
                        'hdr_enabled', 'hdr_supported', 'sdr_white_nits', 'advanced_color_enabled', 'color_source'):
                row.setdefault(key, None)
            rows.append(row)
        return rows
    except (OSError, AttributeError, ValueError, C.ArgumentError):
        return []


def _bounds(rectangle):
    if isinstance(rectangle, Mapping):
        return (int(rectangle['left']), int(rectangle['top']),
                int(rectangle['left']) + int(rectangle['width']), int(rectangle['top']) + int(rectangle['height']))
    if isinstance(rectangle, Sequence) and len(rectangle) == 4:
        return tuple(int(value) for value in rectangle)
    raise ValueError('Capture rectangle must be an MSS rectangle or (left, top, right, bottom)')


def _tri_state(values):
    values = list(values)
    if any(value is True for value in values):
        return True
    return False if values and all(value is False for value in values) else None


def aggregate_displays(displays):
    """Pure summary: measured unequal refresh rates disable combined recording."""
    rows = [target for row in displays for target in row.get('targets', [row])]
    rates = [refresh_hz(row.get('refresh_hz'), 1) for row in rows]
    known = [rate for rate in rates if rate is not None]
    mixed = any(not math.isclose(first, second, abs_tol=.01, rel_tol=0)
                for first in known for second in known)
    mixed = True if mixed else (False if rows and len(known) == len(rows) else None)
    white = [row.get('sdr_white_nits') for row in rows]
    uniform_white = bool(white) and all(isinstance(value, (int, float)) and math.isfinite(value) and value > 0
                                       for value in white) and max(white) - min(white) <= .1
    reason = ''
    if mixed:
        measured = '、'.join(format_refresh(rate) for rate in dict.fromkeys(known))
        reason = f'显示器刷新率不同（{measured}），请选择单个屏幕录制'
    elif len(rows) > 1 and mixed is None:
        reason = '无法确认所有显示器的刷新率，请选择单个屏幕录制'
    return {'display_count': len(rows), 'mixed_refresh_rate': mixed,
            'refresh_hz': known[0] if mixed is False else None,
            'refresh_rates': rates,
            'hdr_enabled': _tri_state(row.get('hdr_enabled') for row in rows),
            'hdr_supported': _tri_state(row.get('hdr_supported') for row in rows),
            'advanced_color_enabled': _tri_state(row.get('advanced_color_enabled') for row in rows),
            'mixed_hdr': len({row.get('hdr_enabled') for row in rows if row.get('hdr_enabled') is not None}) > 1,
            'sdr_white_nits': white[0] if uniform_white else None,
            'recording_enabled': not bool(reason), 'recording_reason': reason}


def capture_display_info(bbox, displays):
    """Aggregate every display with positive area inside a capture rectangle."""
    left, top, right, bottom = _bounds(bbox)
    touched = []
    for display in displays:
        dl, dt, dr, db = _bounds(display)
        if min(right, dr) > max(left, dl) and min(bottom, db) > max(top, dt):
            touched.append(display)
    return dict(aggregate_displays(touched), displays=touched)


def match_monitors(monitors, displays):
    """Keep MSS order/index 0; attach color/timing only to exact pixel matches."""
    result = []
    for index, monitor in enumerate(monitors):
        row = dict(monitor, index=index)
        if index == 0:
            row.update(aggregate_displays(displays))
            row['displays'] = list(displays)
        else:
            matches = [display for display in displays if _bounds(display) == _bounds(monitor)]
            if matches:
                row.update(matches[0] if len(matches) == 1 else aggregate_displays(matches))
                row.update(aggregate_displays(matches))
                row['displays'] = matches
            else:
                row.update(aggregate_displays([]))
                row.update(device_name=None, friendly_name=None, displays=[])
        result.append(row)
    if result and len(result) > 1:
        # Include unmatched MSS monitors as explicitly unknown. A failed
        # native probe must not make an unknown multi-monitor setup appear
        # to have a single verified refresh rate.
        result[0].update(aggregate_displays(result[1:]))
        result[0]['displays'] = [display for row in result[1:] for display in row.get('displays', [])]
    return result

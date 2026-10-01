"""Display policy tests use measured-style packets rather than host assumptions."""
import ctypes as C

import pytest

from gamepadstudio import display_info as displays


def display(name, left, rate, *, hdr=False, white=80):
    return {'device_name': name, 'left': left, 'top': 0, 'width': 1920, 'height': 1080,
            'refresh_hz': rate, 'hdr_enabled': hdr, 'hdr_supported': hdr,
            'advanced_color_enabled': hdr, 'sdr_white_nits': white}


@pytest.mark.parametrize('name,size', [
    ('_LUID', 8), ('_SOURCE_INFO', 20), ('_TARGET_INFO', 48), ('_PATH_INFO', 72),
    ('_MODE_INFO', 64), ('_HEADER', 20), ('_SOURCE_NAME', 84), ('_TARGET_NAME', 420),
    ('_ADVANCED_COLOR', 32), ('_ADVANCED_COLOR_2', 36), ('_SDR_WHITE', 24),
    ('_MONITOR_INFO', 104), ('_DEVMODE', 220),
])
def test_native_structures_follow_win32_sdk_sizes(name, size):
    assert C.sizeof(getattr(displays, name)) == size


@pytest.mark.parametrize('num,den,expected', [(60000, 1001, 59.94005994005994), (120000, 1000, 120),
                                           (0, 0, None), (60, 0, None), (None, 1, None),
                                           ('x', 1, None), (-60, 1, None), (float('nan'), 1, None)])
def test_rational_refresh_retains_real_fractional_rate(num, den, expected):
    assert displays.refresh_hz(num, den) == expected


def test_legacy_advanced_color_never_guesses_hdr_from_enabled_or_ten_bit():
    state = displays.decode_color_info(3, bits_per_channel=10)
    assert state['advanced_color_enabled'] is True
    assert state['hdr_enabled'] is None and state['hdr_supported'] is None
    assert displays.decode_color_info(1)['hdr_enabled'] is False
    assert displays.decode_color_info(7)['hdr_enabled'] is None


def test_new_packet_distinguishes_active_hdr_from_user_preference_and_wcg():
    assert displays.decode_color_info(0x33, version=2, active_color_mode=2)['hdr_enabled'] is True
    wcg = displays.decode_color_info(0x33, version=2, active_color_mode=1)
    assert wcg['hdr_enabled'] is False and wcg['hdr_user_enabled'] is True
    assert wcg['color_mode'] == 'wcg'
    assert displays.decode_color_info(0x33, version=2, active_color_mode=99)['hdr_enabled'] is None


def test_combined_recording_is_disabled_for_actual_unequal_refresh_rates():
    state = displays.aggregate_displays([display('first', 0, 60), display('second', 1920, 144)])
    assert state['mixed_refresh_rate'] is True and state['recording_enabled'] is False
    assert '60 Hz' in state['recording_reason'] and '144 Hz' in state['recording_reason']
    assert '单个屏幕' in state['recording_reason']
    fractional = displays.aggregate_displays([display('first', 0, 60), display('second', 1920, 60000 / 1001)])
    assert fractional['mixed_refresh_rate'] is True
    assert '59.94 Hz' in fractional['recording_reason']


def test_uniform_timings_do_not_assume_uniform_hdr_or_white_point():
    state = displays.aggregate_displays([display('sdr', 0, 60), display('hdr', 1920, 60, hdr=True, white=240)])
    assert state['recording_enabled'] is True and state['refresh_hz'] == 60
    assert state['hdr_enabled'] is True and state['mixed_hdr'] is True
    assert state['sdr_white_nits'] is None
    assert displays.aggregate_displays([display('hdr', 0, 60, hdr=True, white=240)])['sdr_white_nits'] == 240


def test_unknown_refresh_is_explicit_and_never_reported_as_sixty_hz():
    state = displays.aggregate_displays([display('known', 0, 60), display('unknown', 1920, None, hdr=None)])
    assert state['mixed_refresh_rate'] is None and state['refresh_hz'] is None
    assert state['hdr_enabled'] is None
    assert state['recording_enabled'] is False and '无法确认' in state['recording_reason']
    empty = displays.aggregate_displays([])
    assert empty['hdr_enabled'] is None and empty['refresh_hz'] is None


def test_mss_indices_are_matched_by_pixels_not_native_list_order():
    native = [display('right', 0, 144, hdr=True, white=240), display('left', -1920, 60)]
    monitors = [{'left': -1920, 'top': 0, 'width': 3840, 'height': 1080},
                {'left': -1920, 'top': 0, 'width': 1920, 'height': 1080},
                {'left': 0, 'top': 0, 'width': 1920, 'height': 1080}]
    rows = displays.match_monitors(monitors, native)
    assert rows[1]['device_name'] == 'left' and rows[1]['refresh_hz'] == 60
    assert rows[2]['device_name'] == 'right' and rows[2]['hdr_enabled'] is True
    assert rows[2]['sdr_white_nits'] == 240
    assert rows[0]['index'] == 0 and rows[0]['recording_enabled'] is False


def test_mss_monitor_missing_from_native_probe_remains_unknown_and_blocks_all():
    native = [display('first', 0, 60)]
    monitors = [{'left': 0, 'top': 0, 'width': 3840, 'height': 1080},
                {'left': 0, 'top': 0, 'width': 1920, 'height': 1080},
                {'left': 1920, 'top': 0, 'width': 1920, 'height': 1080}]
    rows = displays.match_monitors(monitors, native)
    assert rows[2]['hdr_enabled'] is None and rows[2]['refresh_hz'] is None
    assert rows[0]['display_count'] == 2 and rows[0]['mixed_refresh_rate'] is None
    assert rows[0]['recording_enabled'] is False


def test_window_match_includes_only_positive_intersections():
    native = [display('left', -1920, 60), display('right', 0, 144, hdr=True, white=240)]
    left = displays.capture_display_info((-1920, 0, 0, 1080), native)
    assert left['display_count'] == 1 and left['hdr_enabled'] is False
    assert left['recording_enabled'] is True
    spanning = displays.capture_display_info((-100, 0, 100, 1080), native)
    assert spanning['display_count'] == 2 and spanning['mixed_refresh_rate'] is True
    assert spanning['recording_enabled'] is False
    empty = displays.capture_display_info((9999, 0, 10000, 10), native)
    assert empty['display_count'] == 0 and empty['hdr_enabled'] is None


def test_mirrored_outputs_with_different_timings_are_kept_in_policy():
    mirrored = display('clone', 0, None)
    mirrored['targets'] = [display('one', 0, 60), display('two', 0, 120)]
    aggregate = displays.aggregate_displays([mirrored])
    assert aggregate['display_count'] == 2
    assert aggregate['mixed_refresh_rate'] is True and aggregate['recording_enabled'] is False


def test_non_windows_probe_returns_unknown_instead_of_hdr_off(monkeypatch):
    monkeypatch.setattr(displays.sys, 'platform', 'linux')
    assert displays.enumerate_displays() == []
    assert displays.capture_display_info((0, 0, 1, 1), [])['hdr_enabled'] is None


class DisplayConfigAPI:
    def __init__(self):
        self.queries = 0
        self.requests = []

    def GetDisplayConfigBufferSizes(self, flags, paths, modes):
        C.cast(paths, C.POINTER(displays._UINT))[0] = 1
        C.cast(modes, C.POINTER(displays._UINT))[0] = 2
        return 0

    def QueryDisplayConfig(self, flags, path_count, paths, mode_count, modes, topology):
        self.queries += 1
        if self.queries == 1:
            return 122  # Hotplug between buffer sizing and querying.
        assert topology is None
        paths[0].flags = 1 | 8
        paths[0].targetInfo.targetAvailable = 1
        paths[0].sourceInfo.modeInfoIdx = (1 << 16) | 0xFFFF
        paths[0].targetInfo.modeInfoIdx = 0xFFFF
        paths[0].targetInfo.refreshRate = displays._RATIONAL(60, 1)
        modes[0].infoType = 2
        modes[0].mode.targetMode.vSyncFreq = displays._RATIONAL(120000, 1000)
        modes[1].infoType = 1
        modes[1].mode.sourceMode.width = 1920
        modes[1].mode.sourceMode.height = 1080
        modes[1].mode.sourceMode.position.x = -1920
        return 0

    def DisplayConfigGetDeviceInfo(self, header):
        kind = C.cast(header, C.POINTER(displays._HEADER)).contents.type
        self.requests.append(kind)
        if kind == 1:
            packet = C.cast(header, C.POINTER(displays._SOURCE_NAME)).contents
            for index, char in enumerate('\\\\.\\DISPLAY2'):
                packet.viewGdiDeviceName[index] = ord(char)
        elif kind == 2:
            packet = C.cast(header, C.POINTER(displays._TARGET_NAME)).contents
            for index, char in enumerate('Native HDR Panel'):
                packet.monitorFriendlyDeviceName[index] = ord(char)
        elif kind == 15:
            packet = C.cast(header, C.POINTER(displays._ADVANCED_COLOR_2)).contents
            packet.value, packet.activeColorMode, packet.bitsPerColorChannel = 0x33, 2, 10
        elif kind == 11:
            packet = C.cast(header, C.POINTER(displays._SDR_WHITE)).contents
            packet.SDRWhiteLevel = 3000
        return 0


def test_query_retries_hotplug_reads_virtual_indices_and_prefers_physical_rational():
    api = DisplayConfigAPI()
    rows = displays._query_paths(api)
    assert api.queries == 2 and len(rows) == 1
    assert rows[0]['device_name'] == '\\\\.\\DISPLAY2' and rows[0]['left'] == -1920
    assert rows[0]['desktop_refresh_hz'] == 60
    assert rows[0]['refresh_hz'] == 120 and rows[0]['refresh_numerator'] == 120000
    assert rows[0]['hdr_enabled'] is True and rows[0]['sdr_white_nits'] == 240
    assert api.requests == [1, 2, 15, 11]


def test_fallback_enum_frequency_does_not_guess_hdr_or_default_refresh():
    class API:
        def EnumDisplaySettingsExW(self, name, index, packet, flags):
            assert index == 0xFFFFFFFF
            mode = C.cast(packet, C.POINTER(displays._DEVMODE)).contents
            assert mode.dmSize == 220
            mode.dmFields = 0x400000
            mode.dmDisplayFrequency = 1
            return 1

    assert displays._fallback_mode(API(), '\\\\.\\DISPLAY1') == {}

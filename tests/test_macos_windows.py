"""Window metadata/geometry with fake windows; never inspect user windows."""
import copy
import ctypes as C
import os
import plistlib
import sys

import pytest

from gamepadstudio import macos_windows as windows


def row(window_id=11, pid=100, title='Synthetic game', bounds=None):
    return dict(kCGWindowNumber=window_id, kCGWindowOwnerPID=pid, kCGWindowName=title,
                kCGWindowOwnerName='Synthetic Game', kCGWindowLayer=0, kCGWindowIsOnscreen=True,
                kCGWindowAlpha=1.0, kCGWindowBounds=bounds or dict(X=0, Y=0, Width=1200, Height=800))


class FakeWindowNative:
    def __init__(self):
        self.rows = [row()]
        self.processes = {
            100: dict(pid=100, executable='/Applications/Synthetic Game.app/Contents/MacOS/Game',
                      bundle_id='test.game', name='Synthetic Game', generation=(1000, 123)),
            200: dict(pid=200, executable='/project/.venv/bin/python', bundle_id='', name='Python', generation=(2000, 2)),
        }
        self.front = dict(self.processes[100])
        self.front_sequence = []
        self.calls = []
        self.screens = [dict(left=0, top=0, width=1440, height=900, display_id=1),
                        dict(left=-1920, top=0, width=1920, height=1080, display_id=2),
                        dict(left=0, top=-1200, width=1600, height=1200, display_id=3)]

    def windows(self, window_id=None):
        self.calls.append(('windows', window_id))
        return copy.deepcopy([item for item in self.rows
                              if window_id is None or item['kCGWindowNumber'] == window_id])

    def process(self, pid):
        self.calls.append(('process', pid))
        return dict(self.processes.get(pid, dict(pid=pid, generation=None, executable='')))

    def frontmost(self):
        self.calls.append(('frontmost',))
        return self.front_sequence.pop(0) if self.front_sequence else dict(self.front)

    def displays(self):
        self.calls.append(('displays',))
        return copy.deepcopy(self.screens)


@pytest.fixture(autouse=True)
def clear_exclusions():
    windows.configure_window_exclusions([])
    yield
    windows.configure_window_exclusions([])


def test_default_backend_constructor_does_not_enumerate_or_load_native(monkeypatch):
    monkeypatch.setattr(windows, 'MacWindowNative', lambda: pytest.fail('Native must stay lazy'))
    windows.MacWindowBackend()


def test_frontmost_window_is_first_matching_process_not_largest_background_window():
    native = FakeWindowNative()
    native.rows = [row(20, 200, 'Background', dict(X=0, Y=0, Width=3000, Height=2000)), row()]
    selected = windows.MacWindowBackend(native).foreground_window()
    assert selected.window_id == 11 and selected.pid == 100
    assert selected.process_name == 'Game' and selected.title == 'Synthetic game'
    assert selected.generation == (1000, 123)


def test_frontmost_process_change_never_returns_stale_window():
    native = FakeWindowNative()
    native.front_sequence = [dict(native.processes[100]), dict(native.processes[200])]
    assert windows.MacWindowBackend(native).foreground_window() is None


def test_process_start_time_is_checked_even_if_frontmost_pid_is_reused():
    native = FakeWindowNative()
    replacement = dict(native.processes[100], generation=(3000, 4))
    native.front_sequence = [dict(native.processes[100]), replacement]
    assert windows.MacWindowBackend(native).foreground_window() is None


def test_registered_ui_pid_is_excluded_when_capture_runs_in_another_python_process():
    native = FakeWindowNative()
    native.front = dict(native.processes[200])
    native.rows = [row(20, 200, 'GamePad Studio', dict(X=0, Y=0, Width=3000, Height=2000)), row()]
    windows.configure_window_exclusions([200])
    assert windows.MacWindowBackend(native).smart_window().window_id == 11


def test_bundle_identity_excludes_packaged_studio_without_title_matching():
    native = FakeWindowNative()
    native.processes[200]['bundle_id'] = windows.STUDIO_BUNDLE_ID
    native.front = dict(native.processes[200])
    native.rows = [row(20, 200, '设置', dict(X=0, Y=0, Width=3000, Height=2000)), row()]
    assert windows.MacWindowBackend(native).smart_window().window_id == 11


def test_other_python_apps_and_games_named_gamepad_are_not_misidentified_as_studio():
    native = FakeWindowNative()
    native.front = dict(native.processes[200])
    native.rows = [row(20, 200, 'Gamepad Simulator')]
    assert windows.MacWindowBackend(native).smart_window().window_id == 20


def test_smart_desktop_fallback_uses_large_visible_application_window():
    native = FakeWindowNative()
    native.processes[200]['bundle_id'] = 'com.apple.finder'
    native.front = dict(native.processes[200])
    native.rows = [row(20, 200, 'Desktop', dict(X=0, Y=0, Width=4000, Height=3000)),
                   row(11, 100, 'Small', dict(X=0, Y=0, Width=639, Height=800)),
                   row(12, 100, 'Game', dict(X=-1000, Y=0, Width=1600, Height=900))]
    assert windows.MacWindowBackend(native).smart_window().window_id == 12


@pytest.mark.parametrize('changed', [dict(kCGWindowLayer=3), dict(kCGWindowIsOnscreen=False),
                                    dict(kCGWindowAlpha=0), dict(kCGWindowBounds={'X': 0}),
                                    dict(kCGWindowBounds=dict(X=float('nan'), Y=0, Width=800, Height=600)),
                                    dict(kCGWindowBounds=dict(X=0, Y=0, Width=-1, Height=600))])
def test_nonvisible_overlay_or_invalid_geometry_is_not_a_game_window(changed):
    native = FakeWindowNative()
    native.rows[0].update(changed)
    assert windows.MacWindowBackend(native).foreground_window() is None


def test_untitled_fullscreen_application_still_uses_owner_name():
    native = FakeWindowNative()
    native.rows[0].pop('kCGWindowName')
    assert windows.MacWindowBackend(native).foreground_window().title == 'Synthetic Game'


@pytest.mark.parametrize('change', ['pid', 'generation', 'path', 'closed', 'unknown_generation'])
def test_window_capture_identity_verification_rejects_reuse_and_missing_identity(change):
    native = FakeWindowNative()
    backend = windows.MacWindowBackend(native)
    if change == 'unknown_generation':
        native.processes[100]['generation'] = None
    expected = backend.window(11)
    if change == 'pid':
        native.rows[0]['kCGWindowOwnerPID'] = 200
    elif change == 'generation':
        native.processes[100]['generation'] = (4000, 999)
    elif change == 'path':
        native.processes[100]['executable'] = '/tmp/Another Game'
    elif change == 'closed':
        native.rows = []
    with pytest.raises(RuntimeError, match='身份变化'):
        backend.verify(expected)


def test_monitor_selection_uses_global_points_and_keeps_retina_pixel_metadata():
    native = FakeWindowNative()
    native.rows[0]['kCGWindowBounds'] = dict(X=-1400, Y=-20, Width=1200, Height=900)
    native.screens[1].update(pixel_width=1920, pixel_height=1080, scale=1.0)
    native.screens[0].update(pixel_width=2880, pixel_height=1800, scale=2.0)
    backend = windows.MacWindowBackend(native)
    selected = backend.monitor(backend.smart_window(), native.screens)
    assert selected['display_id'] == 2 and selected['left'] == -1920
    assert selected['pixel_width'] == 1920 and selected['scale'] == 1.0


def test_monitor_above_primary_and_nearest_offscreen_window_are_supported():
    native = FakeWindowNative()
    assert windows.monitor_for_bounds(dict(left=200, top=-1000, width=800, height=600), native.screens)['display_id'] == 3
    assert windows.monitor_for_bounds(dict(left=-4000, top=0, width=800, height=600), native.screens)['display_id'] == 2


@pytest.mark.skipif(sys.platform != 'darwin', reason='Offline CF/ABI check requires Apple frameworks')
def test_native_window_plist_roundtrip_uses_only_synthetic_in_memory_cf_values(monkeypatch):
    native = windows.MacWindowNative()
    assert C.sizeof(windows.CGRect) == 32 and C.sizeof(windows.ProcBSDInfo) == 136
    assert windows.ProcBSDInfo.pbi_start_tvsec.offset == 120
    rows = [row(title='合成窗口😀', bounds=dict(X=-1920, Y=-100, Width=1280, Height=720))]
    data = plistlib.dumps(rows, fmt=plistlib.FMT_BINARY)
    native.cf.CFDataCreate.argtypes = [C.c_void_p, C.c_void_p, C.c_long]
    native.cf.CFDataCreate.restype = C.c_void_p
    native.cf.CFPropertyListCreateWithData.argtypes = [C.c_void_p, C.c_void_p, C.c_ulong, C.c_void_p, C.c_void_p]
    native.cf.CFPropertyListCreateWithData.restype = C.c_void_p
    blob = C.create_string_buffer(data)
    cf_data = native.cf.CFDataCreate(None, blob, len(data))
    value = native.cf.CFPropertyListCreateWithData(None, cf_data, 0, None, None)
    assert value

    def synthetic_windows(options, relative):
        assert (options, relative) == (17, 0)
        return value

    monkeypatch.setattr(native.cg, 'CGWindowListCopyWindowInfo', synthetic_windows)
    try:
        assert native.windows() == rows  # The method releases ownership of value.
    finally:
        native.cf.CFRelease(cf_data)


@pytest.mark.skipif(sys.platform != 'darwin', reason='Public libproc ABI check requires macOS')
def test_native_process_identity_queries_only_this_test_process():
    native = windows.MacWindowNative()
    process = native.process(os.getpid())
    assert process['pid'] == os.getpid()
    assert process['generation'] and process['generation'][0] > 0
    assert process['executable'] and process['executable'].startswith('/')

"""Mac screenshot routes capture generated RGB images, never user pixels."""
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from gamepadstudio import screenshot_service as screenshots
from gamepadstudio.macos_windows import MacWindowBackend
from tests.test_macos_windows import FakeWindowNative


class SyntheticCapture:
    monitors = [dict(left=-1920, top=-1200, width=3520, height=2280),
                dict(left=0, top=0, width=1440, height=900, pixel_width=2880, pixel_height=1800, scale=2.0, display_id=1),
                dict(left=-1920, top=0, width=1920, height=1080, pixel_width=1920, pixel_height=1080, scale=1.0, display_id=2),
                dict(left=0, top=-1200, width=1600, height=1200, pixel_width=1600, pixel_height=1200, scale=1.0, display_id=3)]

    def __init__(self):
        self.calls = []
        self.after_window = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.calls.append(('close',))

    def capture_window(self, window_id):
        self.calls.append(('window', window_id))
        image = Image.new('RGB', (48, 32), (200, 30, 40))
        if self.after_window:
            self.after_window()
        return image

    def capture_display(self, display_id):
        self.calls.append(('display', display_id))
        return Image.new('RGB', (96, 64), (30, 200, 40))

    def capture_all(self):
        self.calls.append(('all',))
        return Image.new('RGB', (192, 128), (30, 40, 200))


@pytest.fixture
def route(monkeypatch):
    native, capture = FakeWindowNative(), SyntheticCapture()
    backend = MacWindowBackend(native)
    monkeypatch.setattr(screenshots, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(screenshots, 'get_mac_window_backend', lambda: backend)
    monkeypatch.setattr(screenshots, 'create_mac_capture', lambda: capture)
    monkeypatch.setattr('gamepadstudio.macos_permissions.screen_capture_permission_status',
                        lambda: dict(granted=True, reason=''))
    return native, backend, capture


@pytest.mark.parametrize('mode,expected', [('game', ('display', 1)), ('smart', ('display', 1)),
                                        ('monitor', ('display', 1)), ('window', ('window', 11)),
                                        ('all', ('all',)), ('monitor_1', ('display', 1)),
                                        ('monitor_2', ('display', 2)), ('monitor_3', ('display', 3))])
def test_capture_options_keep_their_meaning_and_record_actual_pixel_size(tmp_path, route, mode, expected):
    _, _, capture = route
    path = Path(screenshots.take_screenshot(tmp_path, mode=mode))
    assert capture.calls == [expected, ('close',)]
    with Image.open(path) as image:
        size = image.size
        assert image.mode == 'RGB'
    metadata = screenshots.extract_png_metadata(path)
    assert (metadata['width'], metadata['height']) == size
    assert metadata['mode'] == mode and metadata['capture_method'] == 'ScreenCaptureKit'
    assert metadata['title'] == 'Synthetic game'
    assert not path.with_suffix('.json').exists()
    if mode == 'window':
        assert metadata['window_id'] == 11 and metadata['pid'] == 100
        assert metadata['process_path'].endswith('/MacOS/Game')


def test_game_mode_follows_window_onto_display_with_negative_coordinates(tmp_path, route):
    native, _, capture = route
    native.rows[0]['kCGWindowBounds'] = dict(X=-1400, Y=0, Width=1200, Height=900)
    path = screenshots.take_screenshot(tmp_path)
    assert capture.calls[0] == ('display', 2)
    assert screenshots.extract_png_metadata(path)['bounds_points']['left'] == -1920
    selected = screenshots.get_target_monitor_bbox(capture, 'game')
    assert selected['display_id'] == 2 and selected['pixel_width'] == 1920


@pytest.mark.parametrize('mode', ['monitor_0', 'monitor_4', 'monitor_99'])
def test_disconnected_explicit_monitor_never_silently_captures_other_screen(tmp_path, route, mode):
    _, _, capture = route
    with pytest.raises(RuntimeError, match='显示器已断开'):
        screenshots.take_screenshot(tmp_path, mode=mode)
    assert capture.calls == [('close',)]
    assert not list(tmp_path.glob('*.png'))
    with pytest.raises(RuntimeError, match='显示器已断开'):
        screenshots.get_target_monitor_bbox(capture, mode)


def test_window_identity_change_after_capture_discards_image_before_saving(tmp_path, route):
    native, _, capture = route
    capture.after_window = lambda: native.processes[100].update(generation=(9000, 123))
    with pytest.raises(RuntimeError, match='身份变化'):
        screenshots.take_screenshot(tmp_path, mode='window')
    assert capture.calls == [('window', 11), ('close',)]
    assert not list(tmp_path.glob('*.png'))


def test_window_mode_missing_window_does_not_capture_the_desktop_instead(tmp_path, route):
    native, _, capture = route
    native.rows = []
    with pytest.raises(RuntimeError, match='未找到可截图'):
        screenshots.take_screenshot(tmp_path, mode='window')
    assert capture.calls == [('close',)]


def test_capture_denied_permission_prevents_window_or_image_access(tmp_path, route, monkeypatch):
    native, _, capture = route
    monkeypatch.setattr('gamepadstudio.macos_permissions.screen_capture_permission_status',
                        lambda: dict(granted=False, reason='请授权屏幕录制'))
    with pytest.raises(PermissionError, match='请授权'):
        screenshots.take_screenshot(tmp_path, mode='all')
    assert native.calls == capture.calls == []


def test_foreground_and_process_compatibility_functions_use_mac_window_ids(route):
    assert screenshots.foreground_info() == (11, 'Synthetic game')
    assert screenshots.smart_foreground_info() == (11, 'Synthetic game')
    assert screenshots.get_window_process_name(11) == 'Game'

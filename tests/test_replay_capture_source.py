"""HDR source errors never degrade to incorrect 8-bit capture."""
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gamepadstudio.replay_capture import ReplayCapture, _HDRSource


def test_hdr_still_frames_keep_elapsed_time_and_reuse_color_conversion(monkeypatch):
    source = _HDRSource.__new__(_HDRSource)
    source.condition = threading.Condition()
    source.failure = None
    source.closed = False
    source.latest = (0.0, b'fp16', 100.)
    source.converted = None
    source.last_delivered_time = source.last_delivered_key = None
    source.plan = SimpleNamespace(convert_frame=Mock(return_value=b'rgb'))
    clock = [100.]
    monkeypatch.setattr('gamepadstudio.replay_capture.time.monotonic', lambda: clock[0])
    first = source.grab()
    clock[0] = 102.
    second = source.grab()
    assert first.timestamp == 100 and second.timestamp == 102
    source.plan.convert_frame.assert_called_once_with(b'fp16')
    # A new captured frame's source clock cannot move presentation backward
    # after an unchanged desktop was displayed using real elapsed time.
    source.latest = (1.9, b'new', 101.9)
    monkeypatch.setattr('gamepadstudio.replay_capture.time.monotonic', lambda: 102.1)
    third = source.grab()
    assert third.timestamp >= second.timestamp
    assert source.plan.convert_frame.call_count == 2


def test_hdr_failure_does_not_return_a_stale_frame():
    source = _HDRSource.__new__(_HDRSource)
    source.condition = threading.Condition()
    source.closed = False
    source.latest = (0., b'old', 0.)
    source.failure = 'FP16 capture failed'
    with pytest.raises(RuntimeError, match='FP16'):
        source.grab()


def test_unknown_color_state_refuses_before_using_gdi(monkeypatch):
    gdi = SimpleNamespace(grab=Mock())
    capture = ReplayCapture(gdi)
    monkeypatch.setattr(capture, '_display_info', lambda bbox: dict(recording_enabled=True,
                        hdr_enabled=None, recording_reason='', displays=[]))
    with pytest.raises(RuntimeError, match='HDR 状态'):
        capture.configure(dict(left=0, top=0, width=640, height=480))
    gdi.grab.assert_not_called()


def test_hdr_panorama_refuses_before_launching_capture(monkeypatch):
    capture = ReplayCapture(SimpleNamespace(grab=Mock()))
    monkeypatch.setattr(capture, '_display_info', lambda bbox: dict(recording_enabled=True,
                        hdr_enabled=True, recording_reason='', displays=[{}, {}]))
    with pytest.raises(RuntimeError, match='单个屏幕'):
        capture.configure(dict(left=0, top=0, width=640, height=480))


def test_display_color_or_refresh_change_stops_old_capture(monkeypatch):
    gdi = SimpleNamespace(grab=Mock())
    capture = ReplayCapture(gdi)
    capture.bbox = dict(left=0, top=0, width=640, height=480)
    capture.display_signature = ('old display',)
    capture.last_display_check = 0
    monkeypatch.setattr(capture, '_display_info', lambda bbox: {'displays': [
        dict(device_name='new display', hdr_enabled=True)]})
    monkeypatch.setattr('gamepadstudio.replay_capture.time.monotonic', lambda: 10.)
    with pytest.raises(RuntimeError, match='状态已改变'):
        capture.grab(capture.bbox)
    gdi.grab.assert_not_called()

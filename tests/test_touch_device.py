import ctypes as C

import pytest

from gamepadstudio.device import Device, TouchpadEvent


class TouchSDL:
    def __init__(self):
        self.samples = {(0, 0): (1, .25, .40, 1), (0, 1): (1, .75, .60, 1)}
        self.failures = {}
        self.events = []
        self.attached = True

    def SDL_GameControllerGetTouchpadFinger(self, handle, pad, finger, down, x, y, pressure):
        failure = self.failures.get((pad, finger))
        if isinstance(failure, Exception):
            raise failure
        if failure is not None:
            return failure
        values = self.samples.get((pad, finger), (0, 0, 0, 0))
        for pointer, kind, value in zip((down, x, y, pressure),
                                        (C.c_uint8, C.c_float, C.c_float, C.c_float), values):
            C.cast(pointer, C.POINTER(kind)).contents.value = value
        return 0

    def SDL_PumpEvents(self):
        pass

    def SDL_PollEvent(self, buffer):
        if not self.events:
            return 0
        event = self.events.pop(0)
        C.memmove(buffer, C.byref(event), C.sizeof(event))
        return 1

    def SDL_GameControllerGetAttached(self, handle):
        return self.attached

    def SDL_GameControllerName(self, handle):
        return b'DualSense'

    def SDL_GameControllerGetButton(self, handle, button):
        return 0

    def SDL_GameControllerGetAxis(self, handle, axis):
        return 0

    def SDL_GameControllerGetJoystick(self, handle):
        return handle

    def SDL_JoystickCurrentPowerLevel(self, handle):
        return 4

    def SDL_GameControllerGetVendor(self, handle):
        return 0x054c

    def SDL_GameControllerGetProduct(self, handle):
        return 0x0ce6

    def SDL_GameControllerHasLED(self, handle):
        return True

    def SDL_GameControllerHasRumble(self, handle):
        return True

    def SDL_GameControllerRumble(self, handle, low, high, duration):
        return 0

    def SDL_GameControllerClose(self, handle):
        pass


def device_with_touch():
    device = Device.__new__(Device)
    device.lib = TouchSDL()
    device.handle = 11
    device.instance_id = 7
    device.is_raw_joystick = False
    device.event = C.create_string_buffer(128)
    device.metadata = {'touchpad': True, 'touchpad_count': 1, 'touch_finger_counts': [2],
                       'touchpad_fingers': 2, 'family': 'dualsense', 'instance_id': 7}
    device._touch_contacts = {}
    device._touch_contact_serial = 0
    return device


def test_device_samples_both_finger_slots_and_preserves_legacy_coordinate():
    device = device_with_touch()
    state = device.read()
    assert state['touch_valid'] and not state['touch_read_error']
    assert state['touchpad_fingers'] == 2
    assert len(state['touch_fingers']) == 2
    assert [(item['pad'], item['finger']) for item in state['touch_fingers']] == [(0, 0), (0, 1)]
    assert state['touch'] == pytest.approx([.25, .40])
    assert state['touch_fingers'][1]['x'] == pytest.approx(.75)
    assert state['touch_fingers'][1]['pressure'] == 1
    assert device.read()['touch_fingers'] == state['touch_fingers']


def test_slot_generation_changes_only_after_an_observed_release():
    device = device_with_touch()
    original = device.read()['touch_fingers']
    device.lib.samples[(0, 0)] = (0, .25, .4, 0)
    only_second = device.read()['touch_fingers']
    assert len(only_second) == 1
    assert only_second[0]['contact'] == original[1]['contact']
    device.lib.samples[(0, 0)] = (1, .25, .4, 1)
    returned = device.read()['touch_fingers']
    assert returned[0]['contact'] != original[0]['contact']
    assert returned[1]['contact'] == original[1]['contact']


def test_sdl_release_and_retouch_events_preserve_reuse_between_snapshots():
    device = device_with_touch()
    original = device.read()['touch_fingers'][0]['contact']
    device.lib.events = [TouchpadEvent(type=0x658, which=7, pad=0, finger=0),
                         TouchpadEvent(type=0x656, which=7, pad=0, finger=0)]
    assert device.read()['touch_fingers'][0]['contact'] != original


def test_touch_events_for_another_device_do_not_change_selected_contacts():
    device = device_with_touch()
    original = device.read()['touch_fingers']
    device.lib.events = [TouchpadEvent(type=0x658, which=99, pad=0, finger=0),
                         TouchpadEvent(type=0x656, which=99, pad=0, finger=0)]
    assert device.read()['touch_fingers'] == original


@pytest.mark.parametrize('failure', [-1, RuntimeError('unavailable')])
def test_any_finger_read_failure_marks_entire_touch_snapshot_invalid(failure):
    device = device_with_touch()
    device.lib.failures[(0, 1)] = failure
    state = device.read()
    assert state['touch_valid'] is False
    assert state['touch_read_error'] is True
    assert len(state['touch_fingers']) == 1


@pytest.mark.parametrize('bad_index', [1, 2, 3])
def test_nonfinite_contact_value_is_a_failed_sample(bad_index):
    device = device_with_touch()
    sample = [1, .25, .4, 1]
    sample[bad_index] = float('nan')
    device.lib.samples[(0, 0)] = tuple(sample)
    assert device.read()['touch_valid'] is False


def test_sampling_multiple_touchpads_preserves_pad_and_finger_indices():
    device = device_with_touch()
    device.metadata.update(touchpad_count=2, touch_finger_counts=[2, 1])
    device.lib.samples[(1, 0)] = (1, .1, .2, 1)
    fingers, failed = device._read_touch_fingers(device.handle)
    assert not failed
    assert [(item['pad'], item['finger']) for item in fingers] == [(0, 0), (0, 1), (1, 0)]


def test_device_without_touchpad_does_not_read_touch_api():
    device = device_with_touch()
    device.metadata['touchpad'] = False
    device.lib.failures[(0, 0)] = RuntimeError('must not be called')
    assert device._read_touch_fingers(device.handle) == ([], False)


def test_disconnected_device_returns_none_before_sampling_forced_release():
    device = device_with_touch()
    device.read()
    device.lib.attached = False
    device.lib.events = [TouchpadEvent(type=0x658, which=7, pad=0, finger=0)]
    assert device.read() is None


def test_close_clears_contacts_before_another_device_uses_same_slot():
    device = device_with_touch()
    original = device.read()['touch_fingers'][0]['contact']
    device.close_handle()
    assert device._touch_contacts == {}
    device.handle = 12
    device.instance_id = 8
    device.metadata = {'touchpad': True, 'touch_finger_counts': [2]}
    assert device.read()['touch_fingers'][0]['contact'] != original

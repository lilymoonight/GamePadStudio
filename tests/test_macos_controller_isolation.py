"""Only fake HID operations and read-only binding/version checks; no hardware."""
import ctypes as C
import sys
import threading
from collections import deque
from types import SimpleNamespace

import pytest

from gamepadstudio.device import Device, load_sdl_library
from gamepadstudio.macos_controller_isolation import (
    ControllerIsolation, IsolationNative, IsolationUnavailable, NOT_OPEN, SDLVersion)


class FakeSDL:
    def __init__(self):
        self.version = (2, 28, 4)
        self.rows = [(7, b'Bluetooth_054c_0ce6_0x100000', 0x054c, 0x0ce6),
                     (8, b'USB_045e_0b13_0x200000', 0x045e, 0x0b13)]
        self.handles = {11: 7, 12: 8}
        self.attached = True
        self.path_override = None
        self.lock_depth = 0
        self.calls = []

    def SDL_GetVersion(self, output):
        target = C.cast(output, C.POINTER(SDLVersion)).contents
        target.major, target.minor, target.patch = self.version

    def SDL_LockJoysticks(self):
        self.lock_depth += 1

    def SDL_UnlockJoysticks(self):
        self.lock_depth -= 1

    def SDL_GameControllerGetJoystick(self, handle):
        return handle

    def SDL_JoystickGetAttached(self, handle):
        return self.attached and handle in self.handles

    def SDL_JoystickInstanceID(self, handle):
        return self.handles.get(handle, -1)

    def SDL_NumJoysticks(self):
        return len(self.rows)

    def SDL_JoystickGetDeviceInstanceID(self, index):
        return self.rows[index][0]

    def SDL_JoystickPathForIndex(self, index):
        assert self.lock_depth > 0
        return self.rows[index][1]

    def SDL_JoystickPath(self, handle):
        assert self.lock_depth > 0
        if self.path_override is not None:
            return self.path_override
        instance = self.SDL_JoystickInstanceID(handle)
        return next((row[1] for row in self.rows if row[0] == instance), None)

    def SDL_JoystickGetDeviceVendor(self, index):
        return self.rows[index][2]

    def SDL_JoystickGetDeviceProduct(self, index):
        return self.rows[index][3]

    def SDL_GameControllerRumble(self, handle, *values):
        self.calls.append(('rumble', handle))
        return 0

    def SDL_GameControllerClose(self, handle):
        self.calls.append(('sdl_close', handle))
        self.handles.pop(handle, None)

    SDL_JoystickClose = SDL_GameControllerClose

    def SDL_QuitSubSystem(self, flags):
        self.calls.append(('quit', flags))

    def SDL_JoystickOpen(self, index):
        handle = index + 11
        self.handles[handle] = self.rows[index][0]
        self.calls.append(('sdl_open', handle))
        return handle

    def SDL_JoystickNumButtons(self, handle):
        return 16

    def SDL_JoystickNumAxes(self, handle):
        return 4

    def SDL_JoystickNumHats(self, handle):
        return 1

    def SDL_JoystickHasRumble(self, handle):
        return 1


class FakeNative:
    def __init__(self, sdl):
        self.sdl = sdl
        self.calls = []
        self.close_results = deque()
        self.open_results = deque()
        self.validation_error = None
        self.on_open = None

    def _call(self, call):
        assert self.sdl.lock_depth > 0
        self.calls.append(call)

    def retain(self, reference):
        self._call(('retain', reference))

    def release(self, reference):
        # Final release after SDL closes its handle need not take the SDL lock.
        self.calls.append(('release', reference))

    def validate(self, reference, vendor, product):
        self._call(('validate', reference, vendor, product))
        if self.validation_error:
            raise self.validation_error

    def close(self, reference):
        self._call(('close', reference))
        self.sdl.calls.append(('hid_close', reference))
        result = self.close_results.popleft() if self.close_results else 0
        if isinstance(result, Exception):
            raise result
        return result

    def open(self, reference, options):
        self._call(('open', reference, options))
        self.sdl.calls.append(('hid_open', reference, options))
        if self.on_open:
            self.on_open(options)
        result = self.open_results.popleft() if self.open_results else 0
        if isinstance(result, Exception):
            raise result
        return result


def fake_device():
    device = Device.__new__(Device)
    device._io_lock = threading.RLock()
    device.lib = FakeSDL()
    device.handle, device.instance_id = 11, 7
    device.is_raw_joystick = False
    device.metadata = {'vendor': 0x054c, 'product': 0x0ce6,
                       'device_path': 'USB_dead_beef_0xf00000'}
    device._touch_contacts = {}
    device.native = FakeNative(device.lib)
    device._controller_isolation = ControllerIsolation(device, device.native)
    return device


def test_constructor_and_status_never_open_or_enumerate_hid():
    device = fake_device()
    assert device._controller_isolation.status() == dict(
        supported=True, enabled=False, active=False, status='off', reason='', restore_pending=False)
    assert device.native.calls == []
    assert device.lib.calls == []


def test_same_sdl_object_is_closed_shared_then_seized_and_restored_once():
    device = fake_device()
    backend = device._controller_isolation
    assert backend.set_enabled(True)['active']
    assert device.native.calls == [('retain', 0x100000), ('validate', 0x100000, 0x054c, 0x0ce6),
                                   ('close', 0x100000), ('open', 0x100000, 1)]
    assert backend.set_enabled(True)['active']
    assert len(device.native.calls) == 4
    assert backend.set_enabled(False)['status'] == 'off'
    assert device.native.calls[-3:] == [('close', 0x100000), ('open', 0x100000, 0), ('release', 0x100000)]
    backend.set_enabled(False)
    assert device.native.calls.count(('release', 0x100000)) == 1
    assert device.lib.lock_depth == 0


@pytest.mark.parametrize('version', [(2, 28, 3), (2, 30, 0), (3, 0, 0)])
def test_unknown_sdl_versions_fail_closed_without_pointer_access(version):
    device = fake_device()
    device.lib.version = version
    backend = ControllerIsolation(device, device.native)
    status = backend.set_enabled(True)
    assert not status['supported'] and not status['active']
    assert '2.28.4' in status['reason']
    assert device.native.calls == []


@pytest.mark.parametrize('path', [None, b'0x100000', b'USB_054c_0ce6_0x000000',
    b'USB_054c_0ce6_0x100003', b'USB_045e_0ce6_0x100000',
    b'USB_054c_0ce6_0x100000_extra', b'12345678', b'IOService:/gamepad',
    b'SPI_054c_0ce6_0x100000', b'Bluetooth_054c_0ce6_0x100000\n'])
def test_non_hidapi_or_untrusted_path_is_never_dereferenced(path):
    device = fake_device()
    row = device.lib.rows[0]
    device.lib.rows[0] = (row[0], path, row[2], row[3])
    status = device._controller_isolation.set_enabled(True)
    assert not status['active'] and status['reason']
    assert device.native.calls == []


@pytest.mark.parametrize('change', ['detached', 'index_reused', 'handle_instance', 'handle_path', 'no_handle'])
def test_hotplug_identity_changes_fail_before_native_operations(change):
    device = fake_device()
    if change == 'detached':
        device.lib.attached = False
    elif change == 'index_reused':
        device.lib.rows = [device.lib.rows[1]]
    elif change == 'handle_instance':
        device.lib.handles[11] = 8
    elif change == 'handle_path':
        device.lib.path_override = b'Bluetooth_054c_0ce6_0x300000'
    else:
        device.handle = None
    assert not device._controller_isolation.set_enabled(True)['active']
    assert device.native.calls == []


def test_current_matching_instance_is_found_after_index_reordering():
    device = fake_device()
    device.lib.rows.reverse()
    assert device._controller_isolation.set_enabled(True)['active']
    assert device.native.calls[0] == ('retain', 0x100000)


def test_native_type_or_usage_validation_failure_balances_retain_without_closing_shared():
    device = fake_device()
    device.native.validation_error = IsolationUnavailable('键盘接口')
    assert not device._controller_isolation.set_enabled(True)['active']
    assert device.native.calls[-1] == ('release', 0x100000)
    assert all(call[0] not in ('open', 'close') for call in device.native.calls)


@pytest.mark.parametrize('failure', [0xE00002C5, OSError('exclusive unavailable')])
def test_failed_seize_attempt_closes_before_rollback_and_preserves_sdl_reader(failure):
    device = fake_device()
    device.native.open_results.extend([failure, 0])
    result = device._controller_isolation.set_enabled(True)
    assert not result['active'] and not result['restore_pending']
    assert device.native.calls[-3:] == [('close', 0x100000), ('open', 0x100000, 0), ('release', 0x100000)]
    assert device.handle == 11 and device.lib.SDL_JoystickGetAttached(11)


def test_failed_rollback_is_visible_and_disable_can_retry_without_seizing_again():
    device = fake_device()
    device.native.open_results.extend([0xE00002C5, 0xE00002C0])
    result = device._controller_isolation.set_enabled(True)
    assert not result['active'] and result['restore_pending'] and result['status'] == 'error'
    assert '恢复' in result['reason']
    assert device._controller_isolation.set_enabled(True)['restore_pending']
    assert device.native.calls.count(('open', 0x100000, 1)) == 1
    assert device._controller_isolation.set_enabled(False)['status'] == 'off'
    assert device.native.calls.count(('release', 0x100000)) == 1


def test_failed_release_keeps_reference_for_retry_and_does_not_report_active():
    device = fake_device()
    device._controller_isolation.set_enabled(True)
    device.native.close_results.append(0xE00002BC)
    result = device._controller_isolation.set_enabled(False)
    assert result['restore_pending'] and not result['active']
    assert ('release', 0x100000) not in device.native.calls
    assert device._controller_isolation.set_enabled(False)['status'] == 'off'
    assert device.native.calls.count(('release', 0x100000)) == 1


def test_failed_attachment_query_keeps_uncertain_exclusive_access_for_cleanup(monkeypatch):
    device = fake_device()
    device._controller_isolation.set_enabled(True)
    original = device.lib.SDL_JoystickGetAttached
    def failing_query(handle):
        raise OSError('SDL query failed')
    monkeypatch.setattr(device.lib, 'SDL_JoystickGetAttached', failing_query)
    status = device._controller_isolation.status()
    assert not status['active'] and status['restore_pending'] and status['status'] == 'error'
    assert ('release', 0x100000) not in device.native.calls
    monkeypatch.setattr(device.lib, 'SDL_JoystickGetAttached', original)
    assert device._controller_isolation.set_enabled(False)['status'] == 'off'


def test_already_closed_link_can_reopen_shared_on_recovery():
    device = fake_device()
    device._controller_isolation.set_enabled(True)
    device.native.close_results.append(NOT_OPEN)
    assert device._controller_isolation.set_enabled(False)['status'] == 'off'


@pytest.mark.parametrize('query', ['disable', 'status', 'close'])
def test_unplugged_controller_is_only_released_never_closed_or_reopened(query):
    device = fake_device()
    device._controller_isolation.set_enabled(True)
    device.native.calls.clear()
    device.lib.attached = False
    if query == 'disable':
        device._controller_isolation.set_enabled(False)
    elif query == 'status':
        assert not device._controller_isolation.status()['active']
    else:
        device.close()
    assert device.native.calls == [('release', 0x100000)]


def test_disconnect_during_seize_never_restores_an_unplugged_device():
    device = fake_device()
    device.native.on_open = lambda flags: setattr(device.lib, 'attached', False)
    status = device._controller_isolation.set_enabled(True)
    assert not status['active']
    assert device.native.calls[-1] == ('release', 0x100000)
    assert device.native.calls.count(('close', 0x100000)) == 1


def test_close_restores_shared_before_sdl_close_then_quits():
    device = fake_device()
    device._controller_isolation.set_enabled(True)
    device.lib.calls.clear()
    device.close()
    assert device.lib.calls == [('hid_close', 0x100000), ('hid_open', 0x100000, 0),
                                ('rumble', 11), ('sdl_close', 11), ('quit', 0x2200)]
    assert device.native.calls.count(('release', 0x100000)) == 1
    assert device.handle is None


def test_close_failure_restoring_shared_still_closes_sdl_and_releases_retained_reference():
    device = fake_device()
    device._controller_isolation.set_enabled(True)
    device.native.open_results.append(0xE00002C0)
    device.close()
    assert ('sdl_close', 11) in device.lib.calls
    assert device.native.calls.count(('release', 0x100000)) == 1
    assert '恢复' in device.access_warning
    assert device._controller_isolation.status()['status'] == 'error'


def test_select_restores_old_device_before_closing_and_never_auto_seizes_new_selection():
    device = fake_device()
    device._controller_isolation.set_enabled(True)
    device.enumerate_devices = lambda: [dict(instance_id=8, index=1, is_gamecontroller=False,
                                             profile_key='second', vendor=0x045e, product=0x0b13)]
    device.lib.calls.clear()
    device.select(8)
    assert device.lib.calls.index(('hid_open', 0x100000, 0)) < device.lib.calls.index(('sdl_close', 11))
    assert device.handle == 12 and device.instance_id == 8
    assert not device._controller_isolation.status()['active']
    assert ('open', 0x200000, 1) not in device.native.calls


def test_device_public_api_windows_does_not_touch_mac_backend(monkeypatch):
    monkeypatch.setattr('gamepadstudio.device.sys.platform', 'win32')
    device = fake_device()
    assert not device.set_controller_isolation(True)['supported']
    assert not device.controller_isolation_status()['supported']
    assert device.native.calls == []


def test_device_public_api_mac_explicit_opt_in_only(monkeypatch):
    monkeypatch.setattr('gamepadstudio.device.sys.platform', 'darwin')
    device = fake_device()
    assert not device.controller_isolation_status()['enabled']
    assert device.native.calls == []
    assert device.set_controller_isolation(True)['active']
    assert not device.set_controller_isolation(False)['active']


class PropertyCF:
    def __init__(self, properties):
        self.properties = properties
        self.releases = []

    def CFGetTypeID(self, reference):
        return 10 if reference == 0x100000 else 30 if reference in ('synthetic_true', 'synthetic_false') else 20

    def CFNumberGetTypeID(self):
        return 20

    def CFBooleanGetTypeID(self):
        return 30

    def CFBooleanGetValue(self, value):
        return value == 'synthetic_true'

    def CFStringCreateWithCString(self, allocator, name, encoding):
        return name.decode()

    def CFNumberGetValue(self, value, kind, output):
        assert kind == 3
        output._obj.value = value
        return True

    def CFRelease(self, reference):
        self.releases.append(reference)


@pytest.mark.parametrize('properties,composite,rejected', [
    ({}, False, False), ({'VendorID': 0x045e}, False, True),
    ({'PrimaryUsage': 6}, False, True), ({'PrimaryUsagePage': 0xff00}, False, True),
    ({}, True, True),
    ({'GCSyntheticDevice': 'synthetic_true'}, False, True),
    ({'GCSyntheticDevice': 'synthetic_false'}, False, False),
    ({'GCSyntheticDevice': 123}, False, True),
])
def test_real_native_validator_rejects_other_identity_and_keyboard_mouse_collections(properties, composite, rejected):
    values = {'VendorID': 0x054c, 'ProductID': 0x0ce6, 'PrimaryUsagePage': 1, 'PrimaryUsage': 5, **properties}
    native = IsolationNative.__new__(IsolationNative)
    native.cf = PropertyCF(values)
    native.iokit = SimpleNamespace(IOHIDDeviceGetTypeID=lambda: 10,
        IOHIDDeviceGetProperty=lambda reference, key: values.get(key),
        IOHIDDeviceConformsTo=lambda reference, page, usage: composite and usage == 2)
    if rejected:
        with pytest.raises(IsolationUnavailable):
            native.validate(0x100000, 0x054c, 0x0ce6)
    else:
        native.validate(0x100000, 0x054c, 0x0ce6)
    assert native.cf.releases


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS read-only bindings')
def test_native_bindings_and_exact_pygame_sdl_version_without_opening_or_enumerating_hardware():
    native = IsolationNative()
    assert native.iokit.IOHIDDeviceGetTypeID() > 0
    assert native.iokit.IOHIDDeviceOpen.argtypes == [C.c_void_p, C.c_uint32]
    assert native.iokit.IOHIDDeviceOpen.restype is C.c_int32
    assert native.cf.CFRetain.restype is C.c_void_p
    assert C.sizeof(SDLVersion) == 3
    backend = ControllerIsolation(SimpleNamespace(lib=load_sdl_library()))
    # Construction only reads SDL_GetVersion. No SDL_Init/enumeration/open.
    assert backend.supported
    assert not backend.enabled and not backend.active

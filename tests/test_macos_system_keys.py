"""Public system-key bindings: fake IOKit writes and unposted local events."""
import ctypes as C
import sys

import pytest

from gamepadstudio.macos_system_keys import MediaEventNative, ModifierLockNative, NSPoint


class FakeIOKit:
    def __init__(self, *, matching=17, service=18, connection=19, open_error=0, set_error=0):
        self.calls = []
        self.matching, self.service, self.connection = matching, service, connection
        self.open_error, self.set_error = open_error, set_error

    def IOServiceMatching(self, name):
        self.calls.append(('matching', name))
        return self.matching

    def IOServiceGetMatchingService(self, port, matching):
        self.calls.append(('service', port, matching))
        return self.service

    def IOServiceOpen(self, service, task, kind, output):
        self.calls.append(('open', service, task, kind))
        output._obj.value = self.connection
        return self.open_error

    def IOObjectRelease(self, service):
        self.calls.append(('release', service))
        return 0

    def IOHIDSetModifierLockState(self, connection, selector, enabled):
        self.calls.append(('set', connection, selector, enabled))
        return self.set_error

    def IOServiceClose(self, connection):
        self.calls.append(('close', connection))
        return 0


def fake_locks(**kwargs):
    native = ModifierLockNative.__new__(ModifierLockNative)
    native.iokit, native.task = FakeIOKit(**kwargs), 20
    return native


@pytest.mark.parametrize('enabled', [False, True])
def test_caps_connection_is_parameter_only_and_always_releases_service_and_connection(enabled):
    native = fake_locks()
    native.set_caps_lock(enabled)
    assert native.iokit.calls == [
        ('matching', b'IOHIDSystem'), ('service', 0, 17), ('open', 18, 20, 1),
        ('release', 18), ('set', 19, 1, enabled), ('close', 19)]


@pytest.mark.parametrize('failure,expected_closes', [
    ({'matching': None}, []), ({'service': 0}, []),
    ({'open_error': -1, 'connection': 0}, []),
    ({'open_error': -1}, [('close', 19)]),
    ({'connection': 0}, []), ({'set_error': -1}, [('close', 19)]),
])
def test_caps_failures_are_reported_and_do_not_leak_connections(failure, expected_closes):
    native = fake_locks(**failure)
    with pytest.raises(OSError, match='macOS'):
        native.set_caps_lock(True)
    assert [call for call in native.iokit.calls if call[0] == 'close'] == expected_closes
    if native.iokit.service and native.iokit.matching:
        assert native.iokit.calls.count(('release', 18)) == 1


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS native ABI only')
def test_system_key_native_abi_and_local_media_roundtrip_never_posts_or_sets_locks():
    # Framework binding and construction of local retained NSEvent/CGEvent
    # values only. No IOServiceOpen, lock setter, CGEventPost or permissions.
    media, locks = MediaEventNative(), ModifierLockNative()
    assert C.sizeof(NSPoint) == 16
    assert locks.iokit.IOHIDSetModifierLockState.argtypes == [C.c_uint32, C.c_int, C.c_bool]
    assert locks.iokit.IOServiceOpen.argtypes[-1] == C.POINTER(C.c_uint32)
    address = C.cast(media.objc.objc_msgSend, C.c_void_p).value
    object_arg = C.CFUNCTYPE(C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p)(address)
    integer = C.CFUNCTYPE(C.c_long, C.c_void_p, C.c_void_p)(address)
    pool_class = media.objc.objc_getClass(b'NSAutoreleasePool')
    pool = media._object(pool_class, media._selector(b'alloc'))
    pool = media._object(pool, media._selector(b'init'))
    try:
        for key_type in (0, 1, 7, 16):
            for down in (False, True):
                quartz = media.create_event(key_type, down, 1 << 20)
                try:
                    event = object_arg(media.objc.objc_getClass(b'NSEvent'),
                                       media._selector(b'eventWithCGEvent:'), quartz)
                    assert event
                    assert integer(event, media._selector(b'type')) == 14
                    assert integer(event, media._selector(b'subtype')) == 8
                    assert integer(event, media._selector(b'data1')) == (key_type << 16) | ((10 if down else 11) << 8)
                    assert integer(event, media._selector(b'data2')) == -1
                    assert integer(event, media._selector(b'modifierFlags')) & (1 << 20)
                finally:
                    media.cf.CFRelease(quartz)
    finally:
        media._void(pool, media._selector(b'drain'))

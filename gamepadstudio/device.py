"""SDL2's official GameController API; no raw button-number assumptions."""
import ctypes as C
import os
import hashlib
import sys
import uuid
import threading
from contextlib import nullcontext
from functools import wraps
from pathlib import Path
from .controller_catalog import family_for
from .response_curves import (curve_capabilities, evaluate_curve,
                              normalize_curve_channels, CURVE_CHANNELS)

os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
import pygame


def load_sdl_library():
    """Use pygame's SDL instance so its events and controller state stay shared."""
    pygame_dir = Path(pygame.__file__).resolve().parent
    if sys.platform == 'win32':
        return C.CDLL(str(pygame_dir / 'SDL2.dll'))
    # dlopen/dlsym search an extension's linked dependencies. Loading pygame's
    # already imported base extension therefore resolves the exact SDL library
    # it uses, including wheels with private .dylibs and frozen applications.
    # Finding a separate system SDL can silently create a second event queue.
    library = C.CDLL(str(Path(pygame.base.__file__).resolve()))
    if not hasattr(library, 'SDL_Init'):
        raise RuntimeError('pygame 的 SDL2 库未公开控制器接口，请重新安装 pygame。')
    return library


class GUID(C.Structure):
    _fields_=[('data',C.c_uint8*16)]


class TouchpadEvent(C.Structure):
    _fields_ = [('type', C.c_uint32), ('timestamp', C.c_uint32),
                ('which', C.c_int32), ('pad', C.c_int32), ('finger', C.c_int32),
                ('x', C.c_float), ('y', C.c_float), ('pressure', C.c_float)]


class AxisBindHat(C.Structure):
    _fields_ = [('hat', C.c_int), ('hat_mask', C.c_int)]


class AxisBindValue(C.Union):
    _fields_ = [('button', C.c_int), ('axis', C.c_int), ('hat', AxisBindHat)]


class AxisBind(C.Structure):
    _fields_ = [('bind_type', C.c_int), ('value', AxisBindValue)]


def input_backend_for_guid(guid):
    """SDL records the backend and XInput subtype in the final GUID bytes."""
    try:
        raw = bytes.fromhex(guid) if isinstance(guid, str) else bytes(guid)
    except (TypeError, ValueError):
        return ''
    return 'xinput' if len(raw) == 16 and raw[14] == ord('x') and raw[15] == 1 else ''


def _serialized_device_call(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with getattr(self, '_io_lock', None) or nullcontext():
            return method(self, *args, **kwargs)
    return wrapped


class Device:
    def __init__(self):
        self._io_lock = threading.RLock()
        self.lib = load_sdl_library()
        self.handle = None
        self.index = -1
        self.error = ''
        self.access_warning = ''
        self.instance_id = None
        self.available = []
        self.metadata = {}
        self._touch_contacts = {}
        self._touch_contact_serial = 0
        self.set_response_curves({})
        self.preferred_key=''
        self.is_raw_joystick = False
        self.session_id = uuid.uuid4().hex
        specs = {
            'SDL_SetHint': ([C.c_char_p, C.c_char_p], C.c_int),
            'SDL_SetHintWithPriority': ([C.c_char_p, C.c_char_p,C.c_int], C.c_int),
            'SDL_Init': ([C.c_uint32], C.c_int), 'SDL_QuitSubSystem': ([C.c_uint32], None),
            'SDL_PumpEvents': ([], None), 'SDL_PollEvent': ([C.c_void_p], C.c_int),
            'SDL_JoystickUpdate': ([], None), 'SDL_GameControllerUpdate': ([], None),
            'SDL_NumJoysticks': ([], C.c_int), 'SDL_IsGameController': ([C.c_int], C.c_int),
            'SDL_JoystickGetDeviceVendor': ([C.c_int], C.c_uint16),
            'SDL_JoystickGetDeviceProduct': ([C.c_int], C.c_uint16),
            'SDL_JoystickNameForIndex': ([C.c_int], C.c_char_p),
            'SDL_JoystickGetDeviceInstanceID': ([C.c_int], C.c_int32),
            'SDL_JoystickInstanceID': ([C.c_void_p], C.c_int32),
            'SDL_JoystickGetDeviceGUID': ([C.c_int], GUID),
            'SDL_JoystickOpen': ([C.c_int], C.c_void_p),
            'SDL_JoystickClose': ([C.c_void_p], None),
            'SDL_JoystickGetAttached': ([C.c_void_p], C.c_int),
            'SDL_JoystickNumButtons': ([C.c_void_p], C.c_int),
            'SDL_JoystickGetButton': ([C.c_void_p, C.c_int], C.c_uint8),
            'SDL_JoystickNumAxes': ([C.c_void_p], C.c_int),
            'SDL_JoystickGetAxis': ([C.c_void_p, C.c_int], C.c_int16),
            'SDL_JoystickNumHats': ([C.c_void_p], C.c_int),
            'SDL_JoystickGetHat': ([C.c_void_p, C.c_int], C.c_uint8),
            'SDL_JoystickName': ([C.c_void_p], C.c_char_p),
            'SDL_JoystickRumble': ([C.c_void_p, C.c_uint16, C.c_uint16, C.c_uint32], C.c_int),
            'SDL_GameControllerTypeForIndex': ([C.c_int], C.c_int),
            'SDL_GameControllerHasButton': ([C.c_void_p,C.c_int], C.c_int),
            'SDL_GameControllerGetNumTouchpads': ([C.c_void_p], C.c_int),
            'SDL_GameControllerOpen': ([C.c_int], C.c_void_p),
            'SDL_GameControllerAddMapping': ([C.c_char_p], C.c_int),
            'SDL_GameControllerMapping': ([C.c_void_p], C.c_void_p),
            'SDL_free': ([C.c_void_p], None),
            'SDL_GameControllerClose': ([C.c_void_p], None),
            'SDL_GameControllerGetAttached': ([C.c_void_p], C.c_int),
            'SDL_GameControllerName': ([C.c_void_p], C.c_char_p),
            'SDL_GameControllerGetAxis': ([C.c_void_p, C.c_int], C.c_int16),
            'SDL_GameControllerGetButton': ([C.c_void_p, C.c_int], C.c_uint8),
            'SDL_GameControllerGetJoystick': ([C.c_void_p], C.c_void_p),
            'SDL_JoystickCurrentPowerLevel': ([C.c_void_p], C.c_int),
            'SDL_GameControllerGetVendor': ([C.c_void_p], C.c_uint16),
            'SDL_GameControllerGetProduct': ([C.c_void_p], C.c_uint16),
            'SDL_GameControllerHasLED': ([C.c_void_p], C.c_int),
            'SDL_GameControllerHasRumble': ([C.c_void_p], C.c_int),
            'SDL_GameControllerSetLED': ([C.c_void_p, C.c_uint8, C.c_uint8, C.c_uint8], C.c_int),
            'SDL_GameControllerRumble': ([C.c_void_p, C.c_uint16, C.c_uint16, C.c_uint32], C.c_int),
            'SDL_GameControllerGetTouchpadFinger': ([C.c_void_p, C.c_int, C.c_int, C.POINTER(C.c_uint8), C.POINTER(C.c_float), C.POINTER(C.c_float), C.POINTER(C.c_float)], C.c_int),
            'SDL_GetError': ([], C.c_char_p),
        }
        for name, (args, result) in specs.items():
            fn = getattr(self.lib, name)
            fn.argtypes, fn.restype = args, result
        for name, args, result in [
            ('SDL_GameControllerHasAxis', [C.c_void_p, C.c_int], C.c_int),
            ('SDL_GameControllerGetNumTouchpadFingers', [C.c_void_p, C.c_int], C.c_int),
            ('SDL_GameControllerGetBindForAxis', [C.c_void_p, C.c_int], AxisBind),
            ('SDL_GameControllerHasRumbleTriggers', [C.c_void_p], C.c_int),
            ('SDL_GameControllerRumbleTriggers', [C.c_void_p, C.c_uint16, C.c_uint16, C.c_uint32], C.c_int),
            ('SDL_JoystickHasRumble', [C.c_void_p], C.c_int),
            ('SDL_JoystickGetSerial', [C.c_void_p], C.c_char_p),
            ('SDL_GameControllerGetSerial', [C.c_void_p], C.c_char_p),
            ('SDL_JoystickPathForIndex', [C.c_int], C.c_char_p),
        ]:
            fn = getattr(self.lib, name, None)
            if fn is not None:
                fn.argtypes, fn.restype = args, result
        for name in [b'SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS', b'SDL_JOYSTICK_HIDAPI',
                     b'SDL_JOYSTICK_HIDAPI_PS4_RUMBLE', b'SDL_JOYSTICK_HIDAPI_PS5',
                     b'SDL_JOYSTICK_HIDAPI_PS5_RUMBLE']:
            self.lib.SDL_SetHint(name, b'1')
        # Set RAWINPUT to 0 so HIDAPI / DirectInput operates under HidHide application whitelist.
        # This completely hides physical hardware from games while allowing GamePad Studio exclusive access.
        self.lib.SDL_SetHint(b'SDL_JOYSTICK_RAWINPUT', b'0')
        self.lib.SDL_SetHintWithPriority(b'SDL_GAMECONTROLLER_USE_BUTTON_LABELS', b'0', 2)
        # Active HidHide filtering must allow this executable before SDL creates
        # its first device list, including a newly built packaged application.
        if sys.platform == 'win32':
            from .hidhide import ensure_current_app_input_access
            access_ok, access_message = ensure_current_app_input_access()
            if not access_ok:
                self.access_warning = access_message
                self.error = access_message
        if self.lib.SDL_Init(0x2200) != 0:
            raise RuntimeError(self.lib.SDL_GetError().decode())
        self.event = C.create_string_buffer(128)

    def enumerate_devices(self):
        try:
            self.lib.SDL_JoystickUpdate()
            self.lib.SDL_GameControllerUpdate()
        except Exception:
            pass
        self.lib.SDL_PumpEvents()
        rows=[]
        for i in range(self.lib.SDL_NumJoysticks()):
            instance=self.lib.SDL_JoystickGetDeviceInstanceID(i)
            if instance<0:continue
            is_gamecontroller=bool(self.lib.SDL_IsGameController(i))
            vendor=self.lib.SDL_JoystickGetDeviceVendor(i);product=self.lib.SDL_JoystickGetDeviceProduct(i)
            controller_type=self.lib.SDL_GameControllerTypeForIndex(i) if is_gamecontroller else 0
            name=(self.lib.SDL_JoystickNameForIndex(i) or b'Controller').decode('utf-8','replace')
            guid=bytes(self.lib.SDL_JoystickGetDeviceGUID(i).data).hex()
            family=family_for(controller_type,vendor,product,name)
            model_key=f'{family}:{vendor:04x}:{product:04x}:{guid}'
            path_api=getattr(self.lib, 'SDL_JoystickPathForIndex', None)
            path=path_api(i) if path_api else None
            device_key=self._device_key(model_key, instance, path=path)
            if instance == self.instance_id and self.metadata.get('device_key'):
                device_key=self.metadata['device_key']
            rows.append(dict(instance_id=instance,index=i,name=name,vendor=vendor,product=product,
                             controller_type=controller_type,family=family,supported=True,
                             is_gamecontroller=is_gamecontroller,
                             input_backend=input_backend_for_guid(guid) if is_gamecontroller else '',
                             profile_key=model_key,model_key=model_key,device_key=device_key,
                             device_path=path.decode('utf-8', 'replace') if path else ''))
        self.available=rows
        return rows

    def scan(self):
        self.enumerate_devices()
        current_ids = {r['instance_id'] for r in self.available}
        is_attached = False
        if self.handle and self.instance_id in current_ids:
            if getattr(self, 'is_raw_joystick', False):
                is_attached = bool(self.lib.SDL_JoystickGetAttached(self.handle))
            else:
                is_attached = bool(self.lib.SDL_GameControllerGetAttached(self.handle))
        if self.handle and not is_attached:
            self.close_handle()
        if not self.handle:
            for row in sorted(self.available,key=lambda r:self.preferred_key not in (r['profile_key'], r.get('device_key'))):
                if row.get('supported', True):
                    try:self.select(row['instance_id']);break
                    except RuntimeError as exc:self.error=str(exc)

    @_serialized_device_call
    def select(self,instance_id):
        row=next((r for r in self.enumerate_devices() if r['instance_id']==instance_id),None)
        if not row:raise ValueError('设备已断开或尚未识别')
        if self.instance_id==instance_id and self.handle:return
        if row.get('is_gamecontroller', True):
            handle=self.lib.SDL_GameControllerOpen(row['index'])
            if not handle:raise RuntimeError(self.lib.SDL_GetError().decode('utf-8','replace'))
            self._complete_xbox_share_mapping(handle, row)
            actual=self.lib.SDL_JoystickInstanceID(self.lib.SDL_GameControllerGetJoystick(handle))
            if actual!=instance_id:
                self.lib.SDL_GameControllerClose(handle);raise RuntimeError('设备列表已变化，请重试')
            self.close_handle()
            self.handle=handle;self.index=row['index'];self.instance_id=instance_id;self.is_raw_joystick=False
            self.metadata={k:v for k,v in row.items() if k!='index'}
            self.metadata.update(available_buttons=[i for i in range(21) if self.lib.SDL_GameControllerHasButton(handle,i)],
                                 touchpad=self.lib.SDL_GameControllerGetNumTouchpads(handle)>0,
                                 led=bool(self.lib.SDL_GameControllerHasLED(handle)),
                                 rumble=bool(self.lib.SDL_GameControllerHasRumble(handle)))
            touchpad_count = max(0, min(4, self.lib.SDL_GameControllerGetNumTouchpads(handle)))
            finger_api = getattr(self.lib, 'SDL_GameControllerGetNumTouchpadFingers', None)
            finger_counts = [max(0, min(10, finger_api(handle, pad))) if finger_api else 1
                             for pad in range(touchpad_count)]
            self.metadata.update(touchpad_count=touchpad_count, touch_finger_counts=finger_counts,
                                 touchpad_fingers=max(finger_counts, default=0))
            axis_api=getattr(self.lib, 'SDL_GameControllerHasAxis', None)
            if axis_api:
                self.metadata['available_axes']=[i for i in range(6) if axis_api(handle, i)]
            bind_api = getattr(self.lib, 'SDL_GameControllerGetBindForAxis', None)
            if bind_api:
                self.metadata['trigger_axis_bindings'] = [axis for axis in (4, 5)
                                                          if bind_api(handle, axis).bind_type == 2]
            self.metadata['analog_trigger_axes'] = curve_capabilities(self.metadata)['trigger_axes']
            trigger_api = getattr(self.lib, 'SDL_GameControllerHasRumbleTriggers', None)
            self.metadata['trigger_rumble'] = bool(trigger_api and
                getattr(self.lib, 'SDL_GameControllerRumbleTriggers', None) and trigger_api(handle))
            serial_api=getattr(self.lib, 'SDL_GameControllerGetSerial', None)
            serial=serial_api(handle) if serial_api else None
            if serial:
                self.metadata['device_key']=self._device_key(row['profile_key'], instance_id, serial=serial)
        else:
            handle=self.lib.SDL_JoystickOpen(row['index'])
            if not handle:raise RuntimeError(self.lib.SDL_GetError().decode('utf-8','replace'))
            actual=self.lib.SDL_JoystickInstanceID(handle)
            if actual!=instance_id:
                self.lib.SDL_JoystickClose(handle);raise RuntimeError('设备列表已变化，请重试')
            self.close_handle()
            self.handle=handle;self.index=row['index'];self.instance_id=instance_id;self.is_raw_joystick=True
            num_buttons=self.lib.SDL_JoystickNumButtons(handle)
            num_axes=self.lib.SDL_JoystickNumAxes(handle)
            num_hats=self.lib.SDL_JoystickNumHats(handle)
            self.metadata={k:v for k,v in row.items() if k!='index'}
            available_buttons=set(range(min(64, num_buttons)))
            if num_hats:
                available_buttons.update(range(11, 15))
            rumble_api=getattr(self.lib, 'SDL_JoystickHasRumble', None)
            self.metadata.update(available_buttons=sorted(available_buttons),available_axes=list(range(min(6, num_axes))),
                                 num_axes=num_axes, num_hats=num_hats,
                                 touchpad=False, led=False, rumble=bool(rumble_api(handle)) if rumble_api else False)
            self.metadata.update(analog_trigger_axes=[], trigger_rumble=False)
            self.metadata.update(touchpad_count=0, touch_finger_counts=[], touchpad_fingers=0)
            serial_api=getattr(self.lib, 'SDL_JoystickGetSerial', None)
            serial=serial_api(handle) if serial_api else None
            if serial:
                self.metadata['device_key']=self._device_key(row['profile_key'], instance_id, serial=serial)

    def _device_key(self, model_key, instance_id, serial=None, path=None):
        """Prefer persistent hardware IDs; unnamed devices stay session scoped."""
        value=serial or path
        if value:
            raw=value if isinstance(value, bytes) else str(value).encode('utf-8')
            return model_key + (':serial:' if serial else ':path:') + hashlib.sha256(raw).hexdigest()[:24]
        return f'{model_key}:session:{self.session_id}:{instance_id}'

    def _complete_xbox_share_mapping(self, handle, row):
        # SDL2's generic Windows Raw Input mapping omits b11 (Share) on
        # Xbox Series Bluetooth HID 045e:0b13. Do not guess for other pads.
        guid = row.get('profile_key', '').rsplit(':', 1)[-1]
        if not (row.get('vendor') == 0x045e and row.get('product') == 0x0b13
                and guid.endswith('7200')):
            return
        joystick = self.lib.SDL_GameControllerGetJoystick(handle)
        if self.lib.SDL_JoystickNumButtons(joystick) < 12 or self.lib.SDL_GameControllerHasButton(handle, 15):
            return
        ptr = self.lib.SDL_GameControllerMapping(handle)
        if not ptr:
            return
        try:
            mapping = C.string_at(ptr).decode('utf-8')
        finally:
            self.lib.SDL_free(ptr)
        if 'misc1:' not in mapping:
            self.lib.SDL_GameControllerAddMapping((mapping.rstrip(',') + ',misc1:b11,').encode('utf-8'))

    @_serialized_device_call
    def read(self):
        self.lib.SDL_PumpEvents()
        while self.lib.SDL_PollEvent(self.event):
            event = C.cast(self.event, C.POINTER(TouchpadEvent)).contents
            if event.type in (0x656, 0x658) and event.which == self.instance_id:
                slot = (event.pad, event.finger)
                if event.type == 0x656:
                    self._new_touch_contact(slot)
                else:
                    getattr(self, '_touch_contacts', {}).pop(slot, None)
        if not self.handle:
            return None
        if getattr(self, 'is_raw_joystick', False):
            if not self.lib.SDL_JoystickGetAttached(self.handle):
                return None
            h = self.handle
            num_buttons = self.lib.SDL_JoystickNumButtons(h)
            num_axes = self.lib.SDL_JoystickNumAxes(h)
            num_hats = self.lib.SDL_JoystickNumHats(h)
            buttons = [i for i in range(min(64, num_buttons)) if self.lib.SDL_JoystickGetButton(h, i)]
            axes = [self.lib.SDL_JoystickGetAxis(h, i) / 32768.0 for i in range(num_axes)]
            for hat_idx in range(min(1, num_hats)):
                hat_val = self.lib.SDL_JoystickGetHat(h, hat_idx)
                if hat_val & 0x01: buttons.append(11) # Up
                if hat_val & 0x04: buttons.append(12) # Down
                if hat_val & 0x08: buttons.append(13) # Left
                if hat_val & 0x02: buttons.append(14) # Right
            name = (self.lib.SDL_JoystickName(h) or b'Joystick').decode('utf-8', 'replace')
            return {**self.metadata, 'name': name, 'buttons': sorted(set(buttons)),
                    'axes': axes, 'power': self.lib.SDL_JoystickCurrentPowerLevel(h),
                    'vendor': self.metadata.get('vendor', 0), 'product': self.metadata.get('product', 0),
                    'led': False, 'rumble': self.metadata.get('rumble', False),
                    'touch': [], 'touch_fingers': [], 'touch_read_error': False, 'touch_valid': True}

        if not self.lib.SDL_GameControllerGetAttached(self.handle):
            return None
        h = self.handle
        touch_fingers, touch_error = self._read_touch_fingers(h)
        fingers = [touch_fingers[0]['x'], touch_fingers[0]['y']] if touch_fingers else []
        return {**self.metadata,'name': (self.lib.SDL_GameControllerName(h) or b'Controller').decode('utf-8', 'replace'),
                'buttons': [i for i in range(21) if self.lib.SDL_GameControllerGetButton(h, i)],
                'axes': [self.lib.SDL_GameControllerGetAxis(h, i) / 32768 for i in range(6)],
                'power': self.lib.SDL_JoystickCurrentPowerLevel(self.lib.SDL_GameControllerGetJoystick(h)),
                'vendor': self.lib.SDL_GameControllerGetVendor(h), 'product': self.lib.SDL_GameControllerGetProduct(h),
                'led': bool(self.lib.SDL_GameControllerHasLED(h)), 'rumble': bool(self.lib.SDL_GameControllerHasRumble(h)),
                'touch': fingers, 'touch_fingers': touch_fingers, 'touch_read_error': touch_error,
                'touch_valid': not touch_error}

    def _new_touch_contact(self, slot):
        self._touch_contact_serial = getattr(self, '_touch_contact_serial', 0) + 1
        if not hasattr(self, '_touch_contacts'):
            self._touch_contacts = {}
        self._touch_contacts[slot] = self._touch_contact_serial
        return self._touch_contact_serial

    def _read_touch_fingers(self, handle):
        """Track SDL slots; generations distinguish observed release/reuse edges."""
        import math
        if not self.metadata.get('touchpad'):
            return [], False
        counts = self.metadata.get('touch_finger_counts', [1])
        contacts = getattr(self, '_touch_contacts', {})
        fingers, failed = [], False
        for pad, count in enumerate(counts):
            for finger in range(count):
                down, x, y, pressure = C.c_uint8(), C.c_float(), C.c_float(), C.c_float()
                try:
                    result = self.lib.SDL_GameControllerGetTouchpadFinger(
                        handle, pad, finger, C.byref(down), C.byref(x), C.byref(y), C.byref(pressure))
                except Exception:
                    failed = True
                    continue
                slot = (pad, finger)
                if result != 0:
                    failed = True
                    continue
                if not down.value:
                    contacts.pop(slot, None)
                    continue
                if not all(math.isfinite(value) for value in (x.value, y.value, pressure.value)):
                    failed = True
                    continue
                contact = contacts.get(slot)
                if contact is None:
                    contact = self._new_touch_contact(slot)
                    contacts = self._touch_contacts
                fingers.append({'pad': pad, 'finger': finger, 'contact': contact,
                                'x': max(0., min(1., x.value)), 'y': max(0., min(1., y.value)),
                                'pressure': max(0., min(1., pressure.value))})
        return fingers, failed

    def rumble(self, strength):
        try:
            return self.rumble_ext(strength, float(strength) * .65, 350)
        except (TypeError, ValueError, OverflowError):
            return False

    @_serialized_device_call
    def set_response_curves(self, settings=None):
        self.response_curves = {key: normalize_curve_channels(key, settings) for key in CURVE_CHANNELS}

    @_serialized_device_call
    def rumble_ext(self, low_strength: float, high_strength: float, duration_ms: int):
        if not self.handle:
            return False
        curves = getattr(self, 'response_curves', {}).get('rumble_curves', {})
        low = int(evaluate_curve(low_strength, curves.get('low')) * 65535)
        high = int(evaluate_curve(high_strength, curves.get('high')) * 65535)
        try:
            dur = max(10, min(5000, int(duration_ms)))
        except (TypeError, ValueError, OverflowError):
            return False
        if getattr(self, 'is_raw_joystick', False):
            return self.lib.SDL_JoystickRumble(self.handle, low, high, dur) == 0
        return self.lib.SDL_GameControllerRumble(self.handle, low, high, dur) == 0

    @_serialized_device_call
    def rumble_triggers(self, left_strength: float, right_strength: float, duration_ms: int):
        if (not self.handle or not hasattr(self.lib, 'SDL_GameControllerRumbleTriggers')
                or getattr(self, 'is_raw_joystick', False)
                or not self.metadata.get('trigger_rumble', False)):
            return False
        try:
            curves = getattr(self, 'response_curves', {}).get('trigger_rumble_curves', {})
            l = int(evaluate_curve(left_strength, curves.get('left')) * 65535)
            r = int(evaluate_curve(right_strength, curves.get('right')) * 65535)
            dur = max(10, min(5000, int(duration_ms)))
            return self.lib.SDL_GameControllerRumbleTriggers(self.handle, l, r, dur) == 0
        except Exception:
            return False


    def led(self, color):
        if getattr(self, 'is_raw_joystick', False): return False
        return bool(self.handle) and self.lib.SDL_GameControllerSetLED(self.handle, *bytes.fromhex(color.lstrip('#'))) == 0

    def _controller_isolation_backend(self):
        backend = getattr(self, '_controller_isolation', None)
        if backend is None:
            from .macos_controller_isolation import ControllerIsolation
            backend = self._controller_isolation = ControllerIsolation(self)
        return backend

    @_serialized_device_call
    def set_controller_isolation(self, enabled):
        """Explicit macOS opt-in; Windows isolation continues through HidHide."""
        if sys.platform != 'darwin':
            return dict(supported=False, enabled=False, active=False, status='unavailable',
                        reason='此接口仅适用于 macOS；Windows 请使用 HidHide', restore_pending=False)
        return self._controller_isolation_backend().set_enabled(enabled)

    @_serialized_device_call
    def controller_isolation_status(self):
        if sys.platform != 'darwin':
            return dict(supported=False, enabled=False, active=False, status='unavailable',
                        reason='此接口仅适用于 macOS；Windows 请使用 HidHide', restore_pending=False)
        return self._controller_isolation_backend().status()

    @_serialized_device_call
    def close_handle(self):
        isolation = getattr(self, '_controller_isolation', None)
        if isolation is not None:
            result = isolation.set_enabled(False)
            if result.get('restore_pending'):
                self.access_warning = result.get('reason', '手柄共享访问恢复失败')
        if self.handle:
            if getattr(self, 'is_raw_joystick', False):
                self.lib.SDL_JoystickClose(self.handle)
            else:
                self.lib.SDL_GameControllerRumble(self.handle, 0, 0, 0)
                if self.metadata.get('trigger_rumble'):
                    self.lib.SDL_GameControllerRumbleTriggers(self.handle, 0, 0, 0)
                self.lib.SDL_GameControllerClose(self.handle)
            self.handle = None
        if isolation is not None:
            isolation.forget_closed_handle()
        self.instance_id=None;self.metadata={};self.is_raw_joystick=False
        self._touch_contacts = {}
        self.set_response_curves({})

    def close(self):
        self.close_handle()
        self.lib.SDL_QuitSubSystem(0x2200)

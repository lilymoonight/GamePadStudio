"""SDL2's official GameController API; no raw button-number assumptions."""
import ctypes as C
import os
from pathlib import Path
from .controller_catalog import family_for

os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
import pygame


class GUID(C.Structure):
    _fields_=[('data',C.c_uint8*16)]


class Device:
    def __init__(self):
        self.lib = C.CDLL(str(Path(pygame.__file__).parent / 'SDL2.dll'))
        self.handle = None
        self.index = -1
        self.error = ''
        self.instance_id = None
        self.available = []
        self.metadata = {}
        self.preferred_key=''
        self.is_raw_joystick = False
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
        for name in [b'SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS', b'SDL_JOYSTICK_HIDAPI', b'SDL_JOYSTICK_HIDAPI_PS5', b'SDL_JOYSTICK_HIDAPI_PS5_RUMBLE']:
            self.lib.SDL_SetHint(name, b'1')
        # Set RAWINPUT to 0 so HIDAPI / DirectInput operates under HidHide application whitelist.
        # This completely hides physical hardware from games while allowing GamePad Studio exclusive access.
        self.lib.SDL_SetHint(b'SDL_JOYSTICK_RAWINPUT', b'0')
        self.lib.SDL_SetHintWithPriority(b'SDL_GAMECONTROLLER_USE_BUTTON_LABELS', b'0', 2)
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
            rows.append(dict(instance_id=instance,index=i,name=name,vendor=vendor,product=product,
                             controller_type=controller_type,family=family,supported=True,
                             is_gamecontroller=is_gamecontroller,
                             profile_key=f'{family}:{vendor:04x}:{product:04x}:{guid}'))
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
            for row in sorted(self.available,key=lambda r:r['profile_key']!=self.preferred_key):
                if row.get('supported', True):
                    try:self.select(row['instance_id']);break
                    except RuntimeError as exc:self.error=str(exc)

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
            self.metadata.update(available_buttons=list(range(num_buttons)),
                                 num_axes=num_axes, num_hats=num_hats,
                                 touchpad=False, led=False, rumble=True)

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

    def read(self):
        self.lib.SDL_PumpEvents()
        while self.lib.SDL_PollEvent(self.event):
            pass
        if not self.handle:
            return None
        if getattr(self, 'is_raw_joystick', False):
            if not self.lib.SDL_JoystickGetAttached(self.handle):
                return None
            h = self.handle
            num_buttons = self.lib.SDL_JoystickNumButtons(h)
            num_axes = self.lib.SDL_JoystickNumAxes(h)
            num_hats = self.lib.SDL_JoystickNumHats(h)
            buttons = [i for i in range(num_buttons) if self.lib.SDL_JoystickGetButton(h, i)]
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
                    'led': False, 'rumble': True, 'touch': []}

        if not self.lib.SDL_GameControllerGetAttached(self.handle):
            return None
        h = self.handle
        fingers = []
        state, x, y, pressure = C.c_uint8(), C.c_float(), C.c_float(), C.c_float()
        if self.metadata.get('touchpad') and self.lib.SDL_GameControllerGetTouchpadFinger(h, 0, 0, C.byref(state), C.byref(x), C.byref(y), C.byref(pressure)) == 0 and state.value:
            fingers = [x.value, y.value]
        return {**self.metadata,'name': (self.lib.SDL_GameControllerName(h) or b'Controller').decode('utf-8', 'replace'),
                'buttons': [i for i in range(21) if self.lib.SDL_GameControllerGetButton(h, i)],
                'axes': [self.lib.SDL_GameControllerGetAxis(h, i) / 32768 for i in range(6)],
                'power': self.lib.SDL_JoystickCurrentPowerLevel(self.lib.SDL_GameControllerGetJoystick(h)),
                'vendor': self.lib.SDL_GameControllerGetVendor(h), 'product': self.lib.SDL_GameControllerGetProduct(h),
                'led': bool(self.lib.SDL_GameControllerHasLED(h)), 'rumble': bool(self.lib.SDL_GameControllerHasRumble(h)),
                'touch': fingers}

    def rumble(self, strength):
        if not self.handle: return False
        if getattr(self, 'is_raw_joystick', False):
            return self.lib.SDL_JoystickRumble(self.handle, int(strength*65535), int(strength*.65*65535), 350) == 0
        return self.lib.SDL_GameControllerRumble(self.handle, int(strength*65535), int(strength*.65*65535), 350) == 0

    def rumble_ext(self, low_strength: float, high_strength: float, duration_ms: int):
        if not self.handle:
            return False
        low = int(max(0.0, min(1.0, float(low_strength))) * 65535)
        high = int(max(0.0, min(1.0, float(high_strength))) * 65535)
        dur = max(10, min(5000, int(duration_ms)))
        if getattr(self, 'is_raw_joystick', False):
            return self.lib.SDL_JoystickRumble(self.handle, low, high, dur) == 0
        return self.lib.SDL_GameControllerRumble(self.handle, low, high, dur) == 0

    def rumble_triggers(self, left_strength: float, right_strength: float, duration_ms: int):
        if not self.handle or not hasattr(self.lib, 'SDL_GameControllerRumbleTriggers') or getattr(self, 'is_raw_joystick', False):
            return False
        try:
            l = int(max(0.0, min(1.0, float(left_strength))) * 65535)
            r = int(max(0.0, min(1.0, float(right_strength))) * 65535)
            return self.lib.SDL_GameControllerRumbleTriggers(self.handle, l, r, int(duration_ms)) == 0
        except Exception:
            return False


    def led(self, color):
        if getattr(self, 'is_raw_joystick', False): return False
        return bool(self.handle) and self.lib.SDL_GameControllerSetLED(self.handle, *bytes.fromhex(color.lstrip('#'))) == 0

    def close_handle(self):
        if self.handle:
            if getattr(self, 'is_raw_joystick', False):
                self.lib.SDL_JoystickClose(self.handle)
            else:
                self.lib.SDL_GameControllerRumble(self.handle, 0, 0, 0)
                self.lib.SDL_GameControllerClose(self.handle)
            self.handle = None
        self.instance_id=None;self.metadata={};self.is_raw_joystick=False

    def close(self):
        self.close_handle()
        self.lib.SDL_QuitSubSystem(0x2200)

import ctypes as C
import pytest
from dualsense5.controller_catalog import family_for,button_labels,controller_defaults,desktop_defaults,axis_labels
from dualsense5.studio_core import ConfigStore
from dualsense5.kbm_mapper import NIKKI_PROFILE_NAME
from dualsense5.device import Device


@pytest.mark.parametrize('kind,family',[(1,'xbox'),(2,'xbox'),(4,'dualshock4'),(5,'switch'),(7,'dualsense'),(0,'generic')])
def test_family_uses_sdl_type(kind,family):
    assert family_for(kind)==family


def test_face_positions_and_capture_fallback():
    assert [button_labels('switch')[i] for i in range(4)]==['B','A','Y','X']
    assert button_labels('dualshock4')[4]=='Share'
    assert button_labels('xbox',1)[4]=='Back'
    assert '15' in controller_defaults('xbox',range(16))
    assert '4' not in controller_defaults('xbox',range(16))
    assert controller_defaults('xbox',range(15))=={}
    assert '5' not in controller_defaults('xbox',range(16))
    assert axis_labels('xbox')[-2:]==['LT','RT']
    assert button_labels('xbox')[7]=='LS'
    assert desktop_defaults('xbox',range(16))['15']['short']['action']=='capture'
    assert '4' in controller_defaults('dualsense',range(21))
    assert controller_defaults('generic',[])=={}


def test_physical_button_order_is_not_changed_by_external_sdl_settings(monkeypatch):
    monkeypatch.setenv('SDL_GAMECONTROLLER_USE_BUTTON_LABELS','1')
    device=Device()
    try:
        getter=device.lib.SDL_GetHint;getter.argtypes=[C.c_char_p];getter.restype=C.c_char_p
        assert getter(b'SDL_GAMECONTROLLER_USE_BUTTON_LABELS')==b'0'
    finally:device.close()


def test_devices_remember_profiles_and_preserve_old_customization(tmp_path):
    store=ConfigStore(tmp_path);store.data['profiles']['我的 PS 配置']={'4':{'short':{'action':'shortcut','value':'F12'}}}
    store.data['active_profile']='我的 PS 配置';store.save()
    store=ConfigStore(tmp_path)
    ps=dict(family='dualsense',profile_key='ps');xbox=dict(family='xbox',profile_key='xbox',available_buttons=list(range(16)))
    store.activate_controller(ps);assert store.data['active_profile']=='我的 PS 配置'
    store.activate_controller(xbox);assert store.mappings['15']['short']['action']=='capture'
    xbox_profile=store.data['active_profile'];store.mappings['0']={'short':{'action':'hold','value':'Enter'}}
    store.save();store=ConfigStore(tmp_path);store.activate_controller(ps)
    assert store.mappings['4']['short']['value']=='F12'
    store.activate_controller(xbox);assert store.data['active_profile']==xbox_profile and store.mappings['0']['short']['value']=='Enter'


def test_xbox_legacy_default_migration_and_profile_isolation(tmp_path):
    store=ConfigStore(tmp_path)
    original_ps=store.data['profiles']['主机体验'].copy()
    legacy={'4':{'short':{'action':'capture'},'long':{'action':'gallery'}},'5':{'short':{'action':'home'},'long':{'action':'none'}}}
    store.data['profiles']['Xbox · 默认']=legacy
    store.data['controller_profiles']['xbox:driver']='Xbox · 默认';store.save()
    store=ConfigStore(tmp_path)
    state=dict(family='xbox',profile_key='xbox:driver',available_buttons=list(range(15)))
    store.activate_controller(state)
    assert store.mappings=={} and store.data['profiles']['主机体验']==original_ps
    assert set(store.profiles_for(state)) == {NIKKI_PROFILE_NAME, 'Xbox · 默认', 'Xbox · 桌面'}
    desktop=store.data['profiles']['Xbox · 桌面']
    assert desktop['0']['short']['value']=='Enter' and '4' not in desktop and '15' not in desktop
    store.mappings['4']={'short':{'action':'shortcut','value':'F12'}};store.save()
    again=ConfigStore(tmp_path);again.activate_controller(state)
    assert again.mappings['4']['short']['value']=='F12'


def test_xbox_custom_legacy_profile_is_never_rewritten(tmp_path):
    store=ConfigStore(tmp_path)
    custom={'4':{'short':{'action':'capture'},'long':{'action':'gallery'}},'5':{'short':{'action':'none'},'long':{'action':'home'}}}
    store.data['profiles']['Xbox · 默认']=custom
    store.data['controller_profiles']['xbox:custom']='Xbox · 默认'
    store.activate_controller(dict(family='xbox',profile_key='xbox:custom',available_buttons=list(range(16))))
    assert store.mappings==custom


def test_xbox_migration_does_not_rewrite_another_devices_default(tmp_path):
    store=ConfigStore(tmp_path)
    series={'15':{'short':{'action':'capture'},'long':{'action':'replay_record'}},'5':{'short':{'action':'home'},'long':{'action':'none'}}}
    store.data['profiles']['Xbox · 默认']=series.copy()
    store.data['controller_profiles']['xbox:series']='Xbox · 默认'
    store.activate_controller(dict(family='xbox',profile_key='xbox:one',available_buttons=list(range(15))))
    assert store.mappings=={} and store.data['profiles']['Xbox · 默认']==series
    store.activate_controller(dict(family='xbox',profile_key='xbox:series',available_buttons=list(range(16))))
    assert store.mappings=={'15':series['15']}


def test_sdl_virtual_devices_switch_read_and_disconnect():
    # Process-local SDL virtual devices exercise the real ctypes/SDL path, not physical hardware.
    device=Device();lib=device.lib
    specs={'SDL_JoystickAttachVirtual':([C.c_int,C.c_int,C.c_int,C.c_int],C.c_int),
           'SDL_JoystickDetachVirtual':([C.c_int],C.c_int),
           'SDL_JoystickSetVirtualButton':([C.c_void_p,C.c_int,C.c_uint8],C.c_int),
           'SDL_JoystickSetVirtualAxis':([C.c_void_p,C.c_int,C.c_int16],C.c_int)}
    for name,(params,returns) in specs.items():fn=getattr(lib,name);fn.argtypes=params;fn.restype=returns
    instances=[]
    try:
        for _ in range(2):
            index=lib.SDL_JoystickAttachVirtual(1,6,21,0);assert index>=0
            instances.append(lib.SDL_JoystickGetDeviceInstanceID(index))
        device.scan();assert set(instances)<=set(d['instance_id'] for d in device.available)
        device.select(instances[0]);joystick=lib.SDL_GameControllerGetJoystick(device.handle)
        assert lib.SDL_JoystickSetVirtualButton(joystick,0,1)==0
        assert lib.SDL_JoystickSetVirtualAxis(joystick,0,16384)==0
        state=device.read();assert 0 in state['buttons'] and state['axes'][0]==pytest.approx(.5)
        device.select(instances[1]);assert device.read()['instance_id']==instances[1]
        assert 0 not in device.read()['buttons']
        old=device.instance_id
        with pytest.raises(ValueError):device.select(999999)
        assert device.instance_id==old
        index=next(d['index'] for d in device.enumerate_devices() if d['instance_id']==instances[1])
        assert lib.SDL_JoystickDetachVirtual(index)==0
        device.scan();assert device.instance_id!=instances[1]
    finally:
        device.close_handle()
        for instance in reversed(instances):
            row=next((r for r in device.enumerate_devices() if r['instance_id']==instance),None)
            if row:lib.SDL_JoystickDetachVirtual(row['index'])
        device.close()


def test_raw_joystick_flight_stick_mode():
    device = Device()
    lib = device.lib
    specs = {
        'SDL_JoystickAttachVirtual': ([C.c_int, C.c_int, C.c_int, C.c_int], C.c_int),
        'SDL_JoystickDetachVirtual': ([C.c_int], C.c_int),
        'SDL_JoystickSetVirtualButton': ([C.c_void_p, C.c_int, C.c_uint8], C.c_int),
        'SDL_JoystickSetVirtualAxis': ([C.c_void_p, C.c_int, C.c_int16], C.c_int),
        'SDL_JoystickSetVirtualHat': ([C.c_void_p, C.c_int, C.c_uint8], C.c_int),
    }
    for name, (params, returns) in specs.items():
        fn = getattr(lib, name)
        fn.argtypes, fn.restype = params, returns

    idx = lib.SDL_JoystickAttachVirtual(2, 4, 16, 1)  # Raw flight stick / wheel
    assert idx >= 0
    try:
        assert lib.SDL_IsGameController(idx) == 0
        rows = device.enumerate_devices()
        row = next(r for r in rows if r['index'] == idx)
        assert row['supported'] is True
        assert row['is_gamecontroller'] is False

        device.select(row['instance_id'])
        assert device.is_raw_joystick is True

        # Test setting button and hat
        handle = device.handle
        assert lib.SDL_JoystickSetVirtualButton(handle, 3, 1) == 0
        assert lib.SDL_JoystickSetVirtualHat(handle, 0, 0x01) == 0  # Hat Up -> Button 11

        state = device.read()
        assert state is not None
        assert 3 in state['buttons']
        assert 11 in state['buttons']  # Hat Up mapped to D-Pad Up
    finally:
        device.close_handle()
        lib.SDL_JoystickDetachVirtual(idx)
        device.close()

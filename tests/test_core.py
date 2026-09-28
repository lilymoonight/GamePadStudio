import json
from pathlib import Path
import pytest
from dualsense5.studio_core import GestureEngine, ConfigStore, radial_deadzone
from dualsense5.actions import parse_keys, launch_command, WindowsActions
from dualsense5.screenshot_service import list_captures, set_favorite


def test_short_release_and_long_are_mutually_exclusive():
    calls=[]; engine=GestureEngine(lambda binding,down:calls.append((binding['action'],down)))
    maps={'4':{'short':{'action':'capture'},'long':{'action':'gallery'}}}
    engine.update({4},maps,0); engine.update(set(),maps,.1)
    assert calls==[('capture',True)]
    calls.clear(); engine.update({4},maps,1); engine.update({4},maps,1.7); engine.update({4},maps,2); engine.update(set(),maps,3)
    assert calls==[('gallery',True)]


def test_disconnect_releases_held_key_without_short_action():
    calls=[]; engine=GestureEngine(lambda binding,down:calls.append((binding['action'],down)))
    maps={'0':{'short':{'action':'hold','value':'Enter'}},'4':{'short':{'action':'capture'}}}
    engine.update({0,4},maps,0); engine.reset()
    assert calls==[('hold',True),('hold',False)]
    assert not engine.pressed


def test_mapping_is_snapshotted_until_release():
    calls=[]; engine=GestureEngine(lambda b,down:calls.append(b['value']))
    maps={'0':{'short':{'action':'hold','value':'Enter'}}}
    engine.update({0},maps,0); maps['0']['short']['value']='Esc'; engine.update(set(),maps,1)
    assert calls==['Enter','Enter']


def test_config_round_trip_and_corrupt_backup(tmp_path):
    config=ConfigStore(tmp_path); config.data['profiles']['我的配置']={}; config.data['active_profile']='我的配置'; config.save()
    assert ConfigStore(tmp_path).data['active_profile']=='我的配置'
    config.path.write_text('{bad',encoding='utf-8'); restored=ConfigStore(tmp_path)
    assert restored.warning and list(tmp_path.glob('studio.invalid-*.json'))
    assert restored.data['active_profile']=='主机体验'


def test_invalid_mapping_recovers(tmp_path):
    config=ConfigStore(tmp_path); config.data['profiles']['主机体验']['4']={'short':{'action':'oops'}}; config.save()
    assert ConfigStore(tmp_path).warning


def test_delete_profile_and_protection(tmp_path):
    config = ConfigStore(tmp_path)
    state = {'family': 'xbox', 'profile_key': 'xbox:1', 'available_buttons': {}}
    config.activate_controller(state)
    # Duplicate / add a new profile
    config.data['profiles']['Xbox · 自定义'] = dict(config.data['profiles']['Xbox · 默认'])
    config.data['profile_families']['Xbox · 自定义'] = 'xbox'
    config.remember_profile(state, 'Xbox · 自定义')
    config.save()
    assert 'Xbox · 自定义' in config.profiles_for(state)
    assert config.data['active_profile'] == 'Xbox · 自定义'

    # Delete the custom profile
    fallback = config.delete_profile('Xbox · 自定义', state)
    assert fallback is not None
    assert 'Xbox · 自定义' not in config.data['profiles']
    assert config.data['active_profile'] == fallback

    # Deleting nonexistent returns None
    assert config.delete_profile('不存在的配置', state) is None

    # Deleting until only 1 remains: verify last profile cannot be deleted
    remaining = config.profiles_for(state)
    while len(remaining) > 1:
        config.delete_profile(remaining[0], state)
        remaining = config.profiles_for(state)
    assert len(remaining) == 1
    assert config.delete_profile(remaining[0], state) is None
    assert remaining[0] in config.data['profiles']


def test_radial_deadzone_preserves_direction():
    assert radial_deadzone(.03,-.04,.1)==(0.,0.)
    x,y=radial_deadzone(.8,.8,.1)
    assert x==y and x*x+y*y==pytest.approx(1)


def test_key_validation():
    assert parse_keys('Ctrl+Shift+S')==[17,16,83]
    assert parse_keys('F12')==[123]
    with pytest.raises(ValueError): parse_keys('Ctrl+')


def test_gallery_import_favorite_preserves_metadata(tmp_path):
    image=tmp_path/'sample.png'; image.write_bytes(b'PNG')
    image.with_suffix('.json').write_text(json.dumps({'title':'测试窗口','width':1920}),encoding='utf-8')
    set_favorite(str(image),True)
    row=list_captures(tmp_path)[0]
    assert row['favorite'] and row['title']=='测试窗口' and row['width']==1920


def test_hold_key_shared_by_two_buttons_uses_reference_counts():
    action=WindowsActions(); sent=[]; action._send=lambda keys,down:sent.append((keys,down))
    action.hold('Ctrl',True); action.hold('Ctrl',True); action.hold('Ctrl',False)
    assert action.held=={17:1}
    action.hold('Ctrl',False)
    assert sent==[([17],True),([],True),([],False),([17],False)]
    assert not action.held


def test_launch_is_argument_vector_without_shell(monkeypatch):
    received=[]; monkeypatch.setattr('dualsense5.actions.subprocess.Popen',lambda *a,**k:received.append((a,k)))
    launch_command('C:/Apps/app.exe','"hello world" --flag')
    assert received==[((['C:/Apps/app.exe','hello world','--flag'],),{'shell':False})]

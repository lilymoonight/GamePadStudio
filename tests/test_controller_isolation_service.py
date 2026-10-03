from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gamepadstudio import controller_isolation_service as module
from gamepadstudio.studio_core import ConfigStore, device_config


@pytest.fixture
def session(tmp_path):
    state = dict(vendor=0x54c, product=0xce6, buttons=[], axes=[0]*6,
                 family='ps5', instance_id=5, device_key='test-pad')
    store = ConfigStore(tmp_path)
    current = dict(supported=True, enabled=False, active=False, restore_pending=False, status='off', reason='')
    def set_enabled(enabled):
        current.update(enabled=enabled, active=enabled, status='active' if enabled else 'off')
        return current.copy()
    device = SimpleNamespace(controller_isolation_status=lambda:current.copy(), set_controller_isolation=Mock(side_effect=set_enabled))
    return store, device, state, Mock(), current


def test_windows_cloaking_default_never_automatically_seizes_mac(session):
    store, _, state, _, _ = session
    assert device_config(store.data,state)['device_cloaking_enabled'] is True
    assert not module.isolation_requested(store.data,state)


def test_success_is_persisted_for_this_controller_only(session):
    store, device, state, release, _ = session
    result = module.set_controller_isolation(store,device,state,True,release)
    assert result['applied'] and result['enabled']
    release.assert_called_once()
    assert module.isolation_requested(ConfigStore(store.root).data,state)
    assert not module.isolation_requested(store.data,dict(state,device_key='another-pad'))
    result = module.set_controller_isolation(store,device,state,False,release)
    assert result['applied'] and not result['enabled']
    assert not module.isolation_requested(ConfigStore(store.root).data,state)


@pytest.mark.parametrize('state', [{'buttons':[1]}, {'buttons':[],'axes':[.3]},
                                  {'buttons':[],'axes':[float('nan')]}, {'buttons':[],'axes':['bad']}])
def test_active_or_invalid_input_does_not_open_hid(session,state):
    store, device, _, release, _ = session
    result = module.set_controller_isolation(store,device,state,True,release)
    assert not result['applied']
    device.set_controller_isolation.assert_not_called()
    release.assert_not_called()
    assert not store.path.exists()


def test_no_selected_controller_does_not_open_or_change_settings(session):
    store, device, _, release, _ = session
    with pytest.raises(ValueError,match='请先连接'):
        module.set_controller_isolation(store,device,None,True,release)
    device.set_controller_isolation.assert_not_called()


def test_native_failure_is_not_saved_or_called_successful(session):
    store, device, state, release, current = session
    current.update(status='unavailable', reason='version unsupported')
    device.set_controller_isolation.side_effect = lambda _: current.copy()
    result = module.set_controller_isolation(store,device,state,True,release)
    assert not result['applied'] and result['message'] == 'version unsupported'
    assert not module.isolation_requested(store.data,state)


def test_restore_pending_cannot_look_disabled(session):
    store, device, state, release, current = session
    current.update(restore_pending=True, status='error', reason='restore failed')
    device.set_controller_isolation.side_effect = lambda _:current.copy()
    result = module.set_controller_isolation(store,device,state,False,release)
    assert not result['applied'] and result['isolation']['restore_pending']
    assert not store.path.exists()


def test_save_failure_restores_native_state_and_in_memory_config(session,monkeypatch):
    store, device, state, release, current = session
    import copy
    previous = copy.deepcopy(store.data)
    monkeypatch.setattr(store,'save',Mock(side_effect=OSError('read only')))
    result = module.set_controller_isolation(store,device,state,True,release)
    assert not result['applied'] and not result['enabled']
    assert '保存' in result['message']
    assert device.set_controller_isolation.call_args_list[0].args == (True,)
    assert device.set_controller_isolation.call_args_list[1].args == (False,)
    assert store.data == previous and not current['active']


def test_release_failure_does_not_change_access_or_config(session):
    store, device, state, release, _ = session
    release.side_effect = OSError('release failed')
    with pytest.raises(OSError): module.set_controller_isolation(store,device,state,True,release)
    device.set_controller_isolation.assert_not_called()
    assert not store.path.exists()


@pytest.mark.parametrize('enabled',[1,None,'true'])
def test_enable_requires_boolean(session,enabled):
    store, device, state, release, _ = session
    with pytest.raises(ValueError): module.set_controller_isolation(store,device,state,enabled,release)
    device.set_controller_isolation.assert_not_called()


def test_old_remote_backend_reports_unavailable_without_actions():
    result = module.isolation_status(SimpleNamespace())
    assert not result['supported'] and not result['active']

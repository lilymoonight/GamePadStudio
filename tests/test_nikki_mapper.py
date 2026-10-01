import pytest
from gamepadstudio.kbm_mapper import NIKKI_PROFILE_NAME, CHORD_MAPPINGS
from gamepadstudio.studio_core import ConfigStore, is_nikki_profile, profile_scope
from gamepadstudio.mapping_engine import canonical_trigger, MappingRuntime
from tests.test_unified_mapping import Actions, frame


def test_nikki_profile_in_all_controller_families(tmp_path):
    store=ConfigStore(tmp_path)
    for family in ('dualsense','dualshock4','xbox','switch','generic'):
        state = {'family': family, 'device_key': f'{family}:test-device', 'available_buttons': list(range(21))}
        store.activate_controller(state)
        profiles = store.profiles_for(state, 'kbm')
        assert len(profiles) == 1 and is_nikki_profile(store.data, profiles[0])
        assert profiles[0] != NIKKI_PROFILE_NAME
        assert store.data['profile_devices'][profiles[0]] == profile_scope(state)


@pytest.mark.parametrize('parts,value',list(CHORD_MAPPINGS.items()))
def test_game_preset_chords_use_unified_engine(tmp_path,parts,value):
    store=ConfigStore(tmp_path)
    state = {'family': 'dualsense', 'device_key': 'dualsense:test-device', 'available_buttons': list(range(21))}
    store.activate_controller(state)
    store.remember_profile(state, store.profiles_for(state, 'kbm')[0])
    key=canonical_trigger('+'.join(map(str,parts)))
    assert store.mappings[key]['short']['value']==value
    a=Actions();r=MappingRuntime(a,lambda *args:None,start_mouse=False)
    def sample(keys):
        buttons=[k for k in keys if isinstance(k,int)]
        axes=[0.]*6
        if 'LT' in keys: axes[4]=1
        if 'RT' in keys: axes[5]=1
        return dict(state, **frame(buttons,axes))
    r.update(sample(parts[:1]),store.data,now=0)
    r.update(sample(parts),store.data,now=.04)
    r.update(sample(()),store.data,now=.1)
    assert ('key',value,True) in a.calls
    assert all(call[1]==value for call in a.calls)
    r.update(sample(()),store.data,now=.2)
    assert not any(a.keys.values())

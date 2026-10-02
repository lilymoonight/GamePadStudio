"""Behavioral tests: no controller, injected keyboard input, or wall-clock sleeps."""
import copy
import json
import pytest
from gamepadstudio.mapping_engine import (GestureEngine, MappingRuntime, InputNormalizer,
    canonical_trigger, validate_mappings, convert_scheme, effective_mappings)
from gamepadstudio.studio_core import ConfigStore, profile_mode, is_nikki_profile
from gamepadstudio.kbm_mapper import NIKKI_PROFILE_NAME, LEGACY_NIKKI_PROFILE_NAME, infinity_nikki_defaults
from gamepadstudio.actions import parse_keys


def entry(short='none', value='', long='none', long_value=''):
    return {'short': {'action': short, 'value': value}, 'long': {'action': long, 'value': long_value}}


def frame(buttons=(), axes=None):
    return {'buttons': list(buttons), 'axes': axes or [0.] * 6}


class Actions:
    def __init__(self):
        self.calls = []; self.keys = {}; self.mouse = set()
    def hold(self, value, down):
        self.calls.append(('key', value, down))
        for key in parse_keys(value): self.keys[key] = max(0, self.keys.get(key, 0) + (1 if down else -1))
    def mouse_button(self, button, down):
        self.calls.append(('mouse', button, down))
        self.mouse.add(button) if down else self.mouse.discard(button)
    def scroll(self, n): self.calls.append(('wheel', n))
    def move_mouse(self, dx, dy): self.calls.append(('move', dx, dy))
    def shortcut(self, value):
        self.hold(value, True)
        self.hold(value, False)
    def release_all(self): self.keys.clear(); self.mouse.clear()


def runtime(mappings):
    actions = Actions(); events = []
    engine = MappingRuntime(actions, lambda b, d: events.append((b['action'], d)), start_mouse=False)
    config = {'active_profile': 'test', 'profiles': {'test': mappings}, 'long_press': .65}
    return engine, config, actions, events


@pytest.mark.parametrize('key', ['0', '5', '9', '15', 'LT', 'RT', 'LS:up', '0+9', '0+9+RT'])
def test_every_component_short_hold_and_long_hold_obeys_same_rules(key):
    calls=[]; engine=GestureEngine(lambda b,d: calls.append((b['value'],d)))
    maps={key:entry('hold','Ctrl+S','hold','Shift+W')}; pressed=set(key.split('+'))
    engine.update(pressed,maps,0); engine.update(pressed,maps,.3)
    assert calls==[]  # no short leakage while long is undecided
    engine.update(pressed,maps,.7); engine.update(pressed,maps,1.5)
    engine.update(set(),maps,1.6)
    assert calls==[('Shift+W',True),('Shift+W',False)]
    calls.clear(); engine.update(pressed,maps,2); engine.update(set(),maps,2.1)
    engine.update(set(),maps,2.2)
    assert calls==[('Ctrl+S',True),('Ctrl+S',False)]


@pytest.mark.parametrize('first,second', [('0','9'),('9','0'),('0','LT'),('LT','0')])
def test_chords_order_independent_without_component_leak(first,second):
    calls=[]; engine=GestureEngine(lambda b,d:calls.append((b.get('value'),d)))
    key=canonical_trigger(first+'+'+second)
    maps={first:entry('hold','Space'),second:entry('hold','E'),key:entry('shortcut','Ctrl+1')}
    engine.update({first},maps,0); engine.update({first,second},maps,.04)
    engine.update({first},maps,.1); engine.update(set(),maps,.2)
    assert calls==[('Ctrl+1',True)]


def test_late_chord_releases_single_hold_and_does_not_restore_it():
    engine,cfg,actions,_=runtime({'9':entry('hold','E'),'0+9':entry('hold','Ctrl+W')})
    engine.update(frame([9]),cfg,now=0);engine.update(frame([9]),cfg,now=.1)
    assert actions.keys[ord('E')]==1
    engine.update(frame([9,0]),cfg,now=.2)
    assert actions.keys[ord('E')]==0 and actions.keys[ord('W')]==1
    engine.update(frame([9]),cfg,now=.3);engine.update(frame([9]),cfg,now=.8)
    assert all(n==0 for n in actions.keys.values())
    engine.update(frame([]),cfg,now=.9);engine.update(frame([9]),cfg,now=1);engine.update(frame([9]),cfg,now=1.1)
    assert actions.keys[ord('E')]==1


def test_shared_modifiers_and_mouse_are_owned_until_last_binding_releases():
    engine,cfg,a,_=runtime({'0':entry('hold','Ctrl+W'),'1':entry('hold','Ctrl+S'),
                           '2':entry('mouse_hold','left'),'3':entry('mouse_hold','left')})
    engine.update(frame([0,1,2,3]),cfg,now=0)
    assert engine.feedback()['outputs']==['key:17','key:83','key:87','mouse:left']
    engine.update(frame([1,3]),cfg,now=.1)
    assert a.keys[17]==1 and a.mouse=={'left'}
    assert ('mouse','left',False) not in a.calls
    engine.update(frame([]),cfg,now=.2)
    assert a.keys[17]==0 and not a.mouse and not engine.feedback()['outputs']


def test_shortcuts_are_nonblocking_pulses_and_keep_other_modifier_owner():
    engine,cfg,a,_=runtime({'0':entry('hold','Ctrl'),'1':entry('shortcut','Ctrl+S')})
    engine.update(frame([0,1]),cfg,now=0);engine.update(frame([0]),cfg,now=.01)
    assert a.keys[17]==2 and a.keys[83]==1
    engine.update(frame([0]),cfg,now=.061)
    assert a.keys[17]==1 and a.keys[83]==0
    assert 'key:83' in engine.feedback()['recent_outputs']
    engine.reset(); assert not any(a.keys.values())


@pytest.mark.parametrize('interrupt', ['pause','disconnect','profile','edit'])
def test_interrupt_releases_everything_and_never_replays_old_input(interrupt):
    engine,cfg,a,_=runtime({'0':entry('hold','W'),'RT':entry('mouse_hold','left')})
    down=frame([0],[0,0,0,0,0,1]);engine.update(down,cfg,now=0)
    assert a.mouse and a.keys[87]
    if interrupt=='pause': engine.update(down,cfg,enabled=False,now=.1)
    elif interrupt=='disconnect': engine.update(None,cfg,now=.1)
    elif interrupt=='profile':
        cfg['profiles']['other']={'0':entry('hold','X')};cfg['active_profile']='other';engine.update(down,cfg,now=.1)
    else:
        cfg['profiles']['test']['0']=entry('hold','X');engine.update(down,cfg,now=.1)
    assert not a.mouse and not any(a.keys.values())
    if interrupt != 'disconnect':
        engine.update(down,cfg,now=.2); assert not any(a.keys.values())
    engine.update(frame(),cfg,now=.3);engine.update(down,cfg,now=.4)
    assert any(a.keys.values())


def test_trigger_and_stick_hysteresis_is_shared_and_no_axis_chatter():
    n=InputNormalizer()
    assert n.update(frame([], [0,.6,0,0,.6,0]))=={'LS:down','LT'}
    assert n.update(frame([], [0,.4,0,0,.4,0]))=={'LS:down','LT'}
    assert not n.update(frame([], [0,.3,0,0,.3,0]))
    assert canonical_trigger('LT + LB + A')=='0+9+LT'
    assert canonical_trigger('LS:Up')=='LS:up'


def test_three_button_chord_beats_two_button_chord_and_leaves_no_release_action():
    calls=[];e=GestureEngine(lambda b,d:calls.append((b['value'],d)))
    maps={'0+9':entry('hold','A'),'0+9+10':entry('hold','B')}
    e.update({'0','9'},maps,0); e.update({'0','9','10'},maps,.03)
    e.update({'0','9'},maps,.1);e.update(set(),maps,.2)
    assert calls==[('B',True),('B',False)]


@pytest.mark.parametrize('active', ['桌面导航', '主机体验'])
@pytest.mark.parametrize('mapping_version', [0, 2])
def test_keyboard_cleanup_preserves_dedicated_edits_and_native_profiles(tmp_path, active, mapping_version):
    store = ConfigStore(tmp_path)
    old = copy.deepcopy(store.data)
    old['mapping_version'] = mapping_version
    old.pop('keyboard_profile_cleanup_version')
    old['mapping_revision'] = 17
    old['profiles'][NIKKI_PROFILE_NAME] = {'0': entry('hold', 'Ctrl+S'),
                                          '9': entry('shortcut', 'Tab', 'hold', 'Ctrl+W')}
    old['profile_options'][NIKKI_PROFILE_NAME] = {'right_stick_mouse': True, 'mouse': {'sensitivity': 34, 'omega': 32}}
    discarded = ['桌面导航', 'Xbox · 桌面', '3D 动作通用预设', '全能桌面与游戏通用', LEGACY_NIKKI_PROFILE_NAME]
    for name in discarded:
        old['profiles'][name] = {'0': entry('hold', 'Enter')}
        old['profile_families'][name] = 'all'
        old['profile_options'][name] = {'right_stick_mouse': True}
    # Explicit modes distinguish native profiles that have optional shortcuts.
    old['profiles']['Native custom'] = {'0': entry('shortcut', 'F12')}
    old['profile_modes']['Native custom'] = 'gamepad'
    old['profile_families']['Native custom'] = 'xbox'
    for field in ('profile_options', 'profile_modes', 'profile_families'):
        old[field]['already deleted'] = {} if field == 'profile_options' else 'kbm'
    old['active_profile'] = active
    old['controller_profiles'] = {'ps': '桌面导航', 'xbox': 'Native custom', 'gone': 'already deleted'}
    old['legacy_dualsense_profile'] = LEGACY_NIKKI_PROFILE_NAME
    old['virtual_kbm_schemes'] = {'custom': {'enabled': True, 'buttons': {'0': 'Ctrl+S'}}}
    old['active_virtual_kbm_scheme'] = 'custom'
    original = json.dumps(old, ensure_ascii=False).encode('utf-8')
    store.path.write_bytes(original)

    migrated = ConfigStore(tmp_path)
    assert migrated.data['profiles'] == {name: profile for name, profile in old['profiles'].items() if name not in discarded}
    assert migrated.data['profile_options'][NIKKI_PROFILE_NAME] == old['profile_options'][NIKKI_PROFILE_NAME]
    assert migrated.data['active_profile'] == (NIKKI_PROFILE_NAME if active == '桌面导航' else active)
    assert migrated.data['controller_profiles'] == {'ps': NIKKI_PROFILE_NAME, 'xbox': 'Native custom'}
    assert migrated.data['legacy_dualsense_profile'] == NIKKI_PROFILE_NAME
    assert migrated.data['mapping_revision'] == 18
    assert migrated.data['mapping_version'] == 2
    assert migrated.data['keyboard_profile_cleanup_version'] == 1
    assert 'virtual_kbm_schemes' not in migrated.data and 'active_virtual_kbm_scheme' not in migrated.data
    for field in ('profile_options', 'profile_modes', 'profile_families'):
        assert set(migrated.data[field]) <= set(migrated.data['profiles'])
    backup = tmp_path / 'studio.before-keyboard-profile-cleanup-v1.json'
    assert backup.read_bytes() == original
    assert ConfigStore(tmp_path).data == migrated.data
    assert backup.read_bytes() == original
    if mapping_version == 0:
        assert (tmp_path / 'studio.before-unified-mapping-v2.json').read_bytes() == original


def test_keyboard_cleanup_renames_old_nikki_without_losing_edits(tmp_path):
    store = ConfigStore(tmp_path)
    old = copy.deepcopy(store.data)
    old.pop('keyboard_profile_cleanup_version')
    old['profiles'].pop(NIKKI_PROFILE_NAME)
    old['profile_options'].pop(NIKKI_PROFILE_NAME)
    old['profiles'][LEGACY_NIKKI_PROFILE_NAME] = {'RT': entry('mouse_hold', 'middle')}
    old['profile_options'][LEGACY_NIKKI_PROFILE_NAME] = {'right_stick_mouse': False, 'mouse': {'sensitivity': 9}}
    old['active_profile'] = LEGACY_NIKKI_PROFILE_NAME
    store.path.write_text(json.dumps(old), encoding='utf-8')
    migrated = ConfigStore(tmp_path)
    assert migrated.data['active_profile'] == NIKKI_PROFILE_NAME
    assert migrated.mappings == old['profiles'][LEGACY_NIKKI_PROFILE_NAME]
    assert migrated.data['profile_options'][NIKKI_PROFILE_NAME] == old['profile_options'][LEGACY_NIKKI_PROFILE_NAME]
    assert LEGACY_NIKKI_PROFILE_NAME not in migrated.data['profiles']


@pytest.mark.parametrize('metadata', [None, ['invalid'], {NIKKI_PROFILE_NAME: None}])
def test_cleanup_accepts_null_profile_metadata_without_resetting_bindings(tmp_path, metadata):
    store = ConfigStore(tmp_path)
    old = copy.deepcopy(store.data)
    old.pop('keyboard_profile_cleanup_version')
    old['profile_modes'] = metadata
    old['profile_options'] = metadata
    old['profiles'][NIKKI_PROFILE_NAME] = {'RT': entry('mouse_hold', 'middle')}
    old['profiles']['Native custom'] = {'0': entry('gamepad_button', '2')}
    old['active_profile'] = 'Native custom'
    old['legacy_dualsense_profile'] = 'deleted preset'
    store.path.write_text(json.dumps(old), encoding='utf-8')
    migrated = ConfigStore(tmp_path)
    assert not migrated.warning
    assert migrated.data['profiles'] == old['profiles']
    assert migrated.data['active_profile'] == 'Native custom'
    assert migrated.data['legacy_dualsense_profile'] == NIKKI_PROFILE_NAME
    assert isinstance(migrated.data['profile_modes'], dict)
    assert isinstance(migrated.data['profile_options'], dict)
    assert all(isinstance(value, dict) for value in migrated.data['profile_options'].values())


def test_new_installs_only_include_dedicated_keyboard_preset(tmp_path):
    store = ConfigStore(tmp_path)
    assert store.data['mapping_version'] == 2
    assert store.profiles_for(None, 'kbm') == [NIKKI_PROFILE_NAME]
    for family in ('xbox', 'dualshock4', 'switch', 'generic'):
        state = {'family': family, 'profile_key': family, 'available_buttons': list(range(16))}
        store.activate_controller(state)
        keyboard = store.profiles_for(state, 'kbm')
        assert len(keyboard) == 1 and is_nikki_profile(store.data, keyboard[0])
        assert keyboard[0] != NIKKI_PROFILE_NAME
    store.save()
    assert ConfigStore(tmp_path).profiles_for(None, 'kbm') == [NIKKI_PROFILE_NAME]


def test_custom_keyboard_profiles_created_after_cleanup_survive_reload(tmp_path):
    store = ConfigStore(tmp_path)
    store.apply_mapping_change({'op': 'create', 'profile': 'My new keyboard preset', 'mode': 'kbm', 'source': NIKKI_PROFILE_NAME})
    loaded = ConfigStore(tmp_path)
    assert loaded.data['active_profile'] == 'My new keyboard preset'
    assert set(loaded.profiles_for(None, 'kbm')) == {NIKKI_PROFILE_NAME, 'My new keyboard preset'}


def test_stale_writer_cannot_resurrect_cleaned_keyboard_profiles(tmp_path):
    store = ConfigStore(tmp_path)
    old = copy.deepcopy(store.data)
    old.pop('keyboard_profile_cleanup_version')
    old['profiles']['桌面导航'] = {'0': entry('hold', 'Enter')}
    old['profile_modes']['桌面导航'] = 'kbm'
    old['profile_options']['桌面导航'] = {'right_stick_mouse': True}
    old['active_profile'] = '桌面导航'
    store.path.write_text(json.dumps(old), encoding='utf-8')
    # Recreate a settings process that was already open before cleanup.
    store.data = copy.deepcopy(old)
    store._baseline = copy.deepcopy(old)
    cleaned = ConfigStore(tmp_path)
    cleaned.apply_mapping_change({'op': 'create', 'profile': 'New keyboard preset', 'mode': 'kbm', 'source': NIKKI_PROFILE_NAME})
    store.data['profiles']['桌面导航']['0'] = entry('hold', 'Esc')
    store.data['profile_options']['桌面导航']['mouse'] = {'sensitivity': 3}
    store.data['rumble'] = .2
    store.save()
    loaded = ConfigStore(tmp_path)
    assert loaded.data['rumble'] == .2
    assert '桌面导航' not in loaded.data['profiles'] and '桌面导航' not in loaded.data['profile_options']
    assert loaded.data['active_profile'] == 'New keyboard preset'
    assert loaded.data['mapping_revision'] == cleaned.data['mapping_revision']


def test_resetting_keyboard_profile_offline_preserves_native_profile(tmp_path):
    store = ConfigStore(tmp_path)
    store.activate_controller(None)
    native = copy.deepcopy(store.mappings)
    active = store.data['active_profile']
    store.data['profiles'][NIKKI_PROFILE_NAME]['0'] = entry('hold', 'Ctrl+S')
    store.data['profile_options'][NIKKI_PROFILE_NAME]['mouse']['sensitivity'] = 3
    store.apply_mapping_change({'op': 'reset', 'profile': NIKKI_PROFILE_NAME})
    assert store.data['active_profile'] == active and store.mappings == native
    assert store.data['profiles'][NIKKI_PROFILE_NAME] == infinity_nikki_defaults('xbox', range(15))
    assert store.data['profile_options'][NIKKI_PROFILE_NAME]['mouse']['sensitivity'] == 24
    with pytest.raises(ValueError, match='不属于当前输入设备'):
        store.apply_mapping_change({'op': 'reset', 'profile': '主机体验'})


def test_stale_settings_writer_cannot_overwrite_mapping_or_active_profile(tmp_path):
    ui=ConfigStore(tmp_path); ui.activate_controller(None); ui.save()
    agent=ConfigStore(tmp_path); agent.activate_controller(None)
    native = agent.data['active_profile']
    agent.apply_mapping_change({'op':'binding','trigger':'LB+0','mapping':entry('shortcut','Ctrl+1')})
    agent.apply_mapping_change({'op':'select','profile':NIKKI_PROFILE_NAME})
    ui.data['rumble']=.2; ui.save()
    fresh=ConfigStore(tmp_path)
    assert fresh.data['rumble']==.2 and fresh.data['active_profile']==NIKKI_PROFILE_NAME
    assert fresh.data['profiles'][native]['0+9']['short']['value']=='Ctrl+1'


def test_validation_rejects_ambiguous_and_empty_sequences():
    for key,value in [('0+0',entry()),('0',entry('hold','')),('0',entry('shortcut','Ctrl+')),('0',entry('mouse_hold','up'))]:
        with pytest.raises(ValueError): validate_mappings({key:value})


def test_legacy_shoulder_prefix_stays_silent_when_held_then_used_in_chord():
    maps=convert_scheme({'timing_window_s':.18, 'buttons':{'9':{'short':'Tab','long':'none'}},'chords':{'LB+0':'1'}})
    assert maps['9']['long']['action']=='suppress' and maps['9']['long_press']==.18
    calls=[];e=GestureEngine(lambda b,d:calls.append((b.get('value'),d)))
    e.update({'9'},maps,0);e.update({'9'},maps,.2);e.update({'9'},maps,1)
    assert not calls
    e.update({'9','0'},maps,1.1);e.update({'9'},maps,1.2);e.update(set(),maps,1.3)
    assert calls==[('1',True)]
    calls.clear();e.update({'9'},maps,2);e.update(set(),maps,2.08)
    assert calls==[('Tab',True)]
    calls.clear();e.update({'9'},maps,3);e.update({'9'},maps,3.3);e.update(set(),maps,3.4)
    assert not calls


def test_old_string_shoulder_migration_keeps_silent_prefix():
    maps=convert_scheme({'timing_window_s':.18,'buttons':{'9':'Tab'},'chords':{'LB+0':'1'}})
    assert maps['9']=={'short':{'action':'shortcut','value':'Tab'},'long':{'action':'suppress'},'long_press':.18}


def test_safe_preview_uses_same_gestures_without_os_output_and_requires_fresh_press():
    e,cfg,a,events=runtime({'0':entry('hold','Ctrl+S'),'1':entry('capture'),'2':entry('mouse_hold','left')})
    e.update(frame([0,1,2]),cfg,now=0,preview=True)
    assert e.feedback()['preview'] and e.feedback()['outputs']==['key:17','key:83','mouse:left']
    e.update(frame([0,2]),cfg,now=.1,preview=True)
    assert not a.calls and not events
    assert e.feedback()['events'][-1]['action']=='保存截图'
    e.update(frame([0,2]),cfg,now=.2,preview=False)
    assert not a.calls and not e.feedback()['outputs']
    e.update(frame(),cfg,now=.3);e.update(frame([0,2]),cfg,now=.4)
    assert a.keys[17] and a.mouse=={'left'}
    e.update(frame([0,2]),cfg,now=.5,preview=True)
    assert not a.mouse and not any(a.keys.values())


def test_modifier_sequential_combo_chaining_and_order_independent_release():
    calls = []
    e = GestureEngine(lambda b, d: calls.append((b.get('value'), d)), chord_window=0.045)
    maps = {
        '0': entry('hold', 'Space'),
        '1': entry('hold', 'Shift'),
        '9': {'short': {'action': 'shortcut', 'value': 'Tab'}, 'long': {'action': 'suppress'}, 'long_press': 0.18},
        '0+9': entry('shortcut', '1'),
        '1+9': entry('shortcut', '2'),
    }
    # 1. Hold modifier 9, press combo 0 -> 0+9 fires immediately ('1')
    e.update({'9'}, maps, now=1.0)
    e.update({'9', '0'}, maps, now=1.05)
    assert calls == [('1', True)]
    calls.clear()

    # 2. Press combo 1 during overlap with 0 -> 1+9 fires immediately ('2')
    e.update({'9', '0', '1'}, maps, now=1.10)
    assert calls == [('2', True)]
    calls.clear()

    # 3. Release 0 while 9 and 1 are still held -> clean, zero output
    e.update({'9', '1'}, maps, now=1.15)
    assert not calls

    # 4. Release modifier 9 before combo key 1 -> clean, 1 is locked in chord lock and does NOT leak Shift!
    e.update({'1'}, maps, now=1.20)
    assert not calls

    # 5. Release combo key 1 -> lock dissolves at last key release, zero leak!
    e.update(set(), maps, now=1.25)
    assert not calls


def test_rapid_shortcut_retrigger_cleans_pulse_and_sends_distinct_events():
    a = Actions()
    runtime = MappingRuntime(a, lambda b, d: None, start_mouse=False)
    binding = {'action': 'shortcut', 'value': '1'}

    # First trigger at t=1.0s
    runtime.now = 1.0
    runtime._dispatch(binding, True)
    assert a.calls == [('key', '1', True)]
    assert len(runtime.pulses) == 1

    # Rapid second trigger at t=1.02s before 50ms pulse finishes:
    # Must immediately release previous pulse and trigger fresh key-down!
    runtime.now = 1.02
    runtime._dispatch(binding, True)
    assert a.calls == [('key', '1', True), ('key', '1', False), ('key', '1', True)]
    assert len(runtime.pulses) == 1
    assert runtime.pulses[0][0] == 1.07

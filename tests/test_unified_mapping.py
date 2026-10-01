"""Behavioral tests: no controller, injected keyboard input, or wall-clock sleeps."""
import copy
import json
import pytest
from gamepadstudio.mapping_engine import (GestureEngine, MappingRuntime, InputNormalizer,
    canonical_trigger, validate_mappings, convert_scheme, effective_mappings)
from gamepadstudio.studio_core import ConfigStore
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


def test_migration_preserves_custom_active_scheme_and_keeps_backup_once(tmp_path):
    store=ConfigStore(tmp_path); old=copy.deepcopy(store.data)
    old.pop('mapping_version');old['virtual_kbm_schemes']={'custom':{'enabled':True,
        'buttons':{'0':{'short':'Ctrl+S','long':'action:capture'},'RT':'mouse:left'},
        'chords':{'LB + A':'Ctrl+1'},'mouse':{'sensitivity':34}}}
    old['active_virtual_kbm_scheme']='custom'
    store.path.write_text(json.dumps(old),encoding='utf-8')
    migrated=ConfigStore(tmp_path)
    assert migrated.mappings['0']['short']['value']=='Ctrl+S'
    assert migrated.mappings['0']['long']['action']=='capture'
    assert migrated.mappings['0+9']['short']['value']=='Ctrl+1'
    assert migrated.data['profile_options'][migrated.data['active_profile']]['mouse']['sensitivity']==34
    assert 'virtual_kbm_schemes' not in migrated.data
    backup=(tmp_path/'studio.before-unified-mapping-v2.json').read_bytes()
    assert json.loads(backup)['active_virtual_kbm_scheme']=='custom'
    assert ConfigStore(tmp_path).data==migrated.data
    assert (tmp_path/'studio.before-unified-mapping-v2.json').read_bytes()==backup


def test_stale_settings_writer_cannot_overwrite_mapping_or_active_profile(tmp_path):
    ui=ConfigStore(tmp_path); agent=ConfigStore(tmp_path)
    agent.apply_mapping_change({'op':'binding','trigger':'LB+0','mapping':entry('shortcut','Ctrl+1')})
    agent.apply_mapping_change({'op':'select','profile':'桌面导航'})
    ui.data['rumble']=.2; ui.save()
    fresh=ConfigStore(tmp_path)
    assert fresh.data['rumble']==.2 and fresh.data['active_profile']=='桌面导航'
    assert fresh.data['profiles']['主机体验']['0+9']['short']['value']=='Ctrl+1'


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


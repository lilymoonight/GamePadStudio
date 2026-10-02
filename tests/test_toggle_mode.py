"""Toggle output owns a held key or mouse button until the next source press."""
from __future__ import annotations

import copy

import pytest

from gamepadstudio.actions import parse_keys
from gamepadstudio.mapping_engine import MappingRuntime, validate_mappings


def binding(action='hold', value='W', mode='toggle'):
    return {'action': action, 'value': value, 'mode': mode}


def entry(short=None, long=None):
    return {'short': short or {'action': 'none'}, 'long': long or {'action': 'none'}}


def frame(*buttons):
    return {'buttons': list(buttons), 'axes': [0.] * 6}


class Actions:
    def __init__(self):
        self.keys = {}
        self.mouse = set()
        self.calls = []
        self.refuse_key_up = None

    def hold(self, value, down):
        for key in parse_keys(value):
            count = self.keys.get(key, 0)
            if down:
                if not count:
                    self.calls.append(('key', key, True))
                self.keys[key] = count + 1
            elif count:
                if count == 1 and key == self.refuse_key_up:
                    raise OSError('key up refused')
                if count == 1:
                    self.calls.append(('key', key, False))
                self.keys[key] = count - 1

    def mouse_button(self, button, down):
        self.calls.append(('mouse', button, down))
        if down:
            self.mouse.add(button)
        else:
            self.mouse.discard(button)

    def scroll(self, _steps):
        raise AssertionError('unexpected scroll')


def runtime(mappings):
    actions = Actions()
    engine = MappingRuntime(actions, lambda *_: None, start_mouse=False)
    config = {'active_profile': 'test', 'profiles': {'test': mappings}, 'long_press': .65}
    return engine, config, actions


def test_keyboard_toggle_stays_held_after_release_and_second_press_turns_it_off():
    engine, config, actions = runtime({'0': entry(binding())})
    engine.update(frame(0), config, now=0)
    engine.update(frame(0), config, now=.2)
    engine.update(frame(), config, now=.3)
    assert actions.calls == [('key', ord('W'), True)]
    assert engine.feedback()['outputs'] == ['key:87']
    engine.update(frame(0), config, now=.4)
    engine.update(frame(), config, now=.5)
    assert actions.calls == [('key', ord('W'), True), ('key', ord('W'), False)]
    assert engine.feedback()['outputs'] == []
    assert engine.toggle_latches == {}


def test_mouse_toggle_uses_the_shared_button_ownership_count():
    engine, config, actions = runtime({
        '0': entry(binding('mouse_hold', 'left')),
        '1': entry({'action': 'mouse_hold', 'value': 'left'}),
    })
    engine.update(frame(0), config, now=0)
    engine.update(frame(), config, now=.1)
    engine.update(frame(1), config, now=.2)
    engine.update(frame(0, 1), config, now=.3)
    engine.update(frame(1), config, now=.4)
    assert actions.calls == [('mouse', 'left', True)]
    assert engine.feedback()['outputs'] == ['mouse:left']
    engine.update(frame(), config, now=.5)
    assert actions.calls[-1] == ('mouse', 'left', False)
    assert not actions.mouse and engine.feedback()['outputs'] == []


def test_two_toggle_sources_own_the_same_target_independently():
    engine, config, actions = runtime({
        '0': entry(binding()), '1': entry(binding()),
    })
    engine.update(frame(0), config, now=0)
    engine.update(frame(), config, now=.1)
    engine.update(frame(1), config, now=.2)
    engine.update(frame(), config, now=.3)
    assert actions.keys[ord('W')] == 2
    assert actions.calls == [('key', ord('W'), True)]
    engine.update(frame(0), config, now=.4)
    engine.update(frame(), config, now=.5)
    assert actions.keys[ord('W')] == 1
    assert actions.calls == [('key', ord('W'), True)]
    engine.update(frame(1), config, now=.6)
    assert actions.calls[-1] == ('key', ord('W'), False)


def test_long_gesture_toggles_only_after_threshold():
    engine, config, actions = runtime({'0': entry(long=binding())})
    engine.update(frame(0), config, now=0)
    engine.update(frame(0), config, now=.6)
    assert not actions.calls
    engine.update(frame(0), config, now=.7)
    engine.update(frame(), config, now=.8)
    assert actions.calls == [('key', ord('W'), True)]
    engine.update(frame(0), config, now=.9)
    engine.update(frame(0), config, now=1.6)
    assert actions.calls[-1] == ('key', ord('W'), False)


def test_short_and_long_gestures_on_one_source_keep_separate_latches():
    engine, config, actions = runtime({'0': entry(binding(value='W'), binding(value='S'))})
    engine.update(frame(0), config, now=0)
    engine.update(frame(), config, now=.1)
    engine.update(frame(), config, now=.2)
    assert actions.keys[ord('W')] == 1 and actions.keys.get(ord('S'), 0) == 0
    engine.update(frame(0), config, now=.3)
    engine.update(frame(0), config, now=1.0)
    engine.update(frame(), config, now=1.1)
    assert actions.keys[ord('W')] == 1 and actions.keys[ord('S')] == 1
    engine.update(frame(0), config, now=1.2)
    engine.update(frame(), config, now=1.3)
    assert actions.keys[ord('W')] == 0 and actions.keys[ord('S')] == 1
    engine.reset()
    assert not any(actions.keys.values())


@pytest.mark.parametrize('interrupt', ['pause', 'disconnect', 'edit', 'profile', 'reset', 'close'])
def test_interrupt_releases_latched_output(interrupt):
    engine, config, actions = runtime({'0': entry(binding())})
    engine.update(frame(0), config, now=0)
    engine.update(frame(), config, now=.1)
    assert actions.keys[ord('W')] == 1
    if interrupt == 'pause':
        engine.update(frame(), config, enabled=False, now=.2)
    elif interrupt == 'disconnect':
        engine.update(None, config, now=.2)
    elif interrupt == 'edit':
        config['profiles']['test']['0'] = entry(binding(value='X'))
        engine.update(frame(), config, now=.2)
    elif interrupt == 'profile':
        config['profiles']['other'] = {'0': entry(binding(value='X'))}
        config['active_profile'] = 'other'
        engine.update(frame(), config, now=.2)
    elif interrupt == 'close':
        engine.close()
    else:
        engine.reset()
    assert actions.keys[ord('W')] == 0
    assert engine.toggle_latches == {}
    assert engine.feedback()['outputs'] == []


def test_config_change_releases_toggle_and_blocks_still_pressed_source():
    engine, config, actions = runtime({'0': entry(binding())})
    engine.update(frame(0), config, now=0)
    config['profiles']['test']['0'] = entry(binding(value='X'))
    engine.update(frame(0), config, now=.1)
    engine.update(frame(0), config, now=.2)
    assert actions.calls == [('key', ord('W'), True), ('key', ord('W'), False)]
    engine.update(frame(), config, now=.3)
    engine.update(frame(0), config, now=.4)
    assert actions.calls[-1] == ('key', ord('X'), True)


def test_preview_transition_releases_live_toggle_and_preview_never_posts_output():
    engine, config, actions = runtime({'0': entry(binding())})
    engine.update(frame(0), config, now=0)
    engine.update(frame(), config, now=.1)
    engine.update(frame(), config, preview=True, now=.2)
    assert actions.calls == [('key', ord('W'), True), ('key', ord('W'), False)]
    engine.update(frame(0), config, preview=True, now=.3)
    engine.update(frame(), config, preview=True, now=.4)
    assert engine.feedback()['outputs'] == ['key:87']
    assert actions.calls == [('key', ord('W'), True), ('key', ord('W'), False)]
    engine.update(frame(), config, preview=False, now=.5)
    assert engine.feedback()['outputs'] == []
    engine.update(frame(0), config, now=.6)
    assert actions.calls[-1] == ('key', ord('W'), True)


def test_reset_releases_other_latches_even_when_one_key_up_fails_then_can_retry():
    engine, config, actions = runtime({
        '0': entry(binding(value='W')), '1': entry(binding(value='S')),
    })
    engine.update(frame(0, 1), config, now=0)
    engine.update(frame(), config, now=.1)
    actions.refuse_key_up = ord('W')
    with pytest.raises(OSError, match='未能释放'):
        engine.reset()
    assert actions.keys[ord('S')] == 0
    assert actions.keys[ord('W')] == 1
    assert list(engine.toggle_latches) == [('0', 'short')]
    actions.refuse_key_up = None
    engine.reset()
    assert actions.keys[ord('W')] == 0 and not engine.toggle_latches


@pytest.mark.parametrize('mode', [None, True, 1, '', 'turbo', 'TOGGLE', [], {}])
def test_invalid_modes_are_rejected(mode):
    with pytest.raises(ValueError, match='模式'):
        validate_mappings({'0': entry(binding(mode=mode))})


def test_toggle_mode_is_only_valid_for_held_kbm_actions_and_physical_sources():
    with pytest.raises(ValueError, match='模式'):
        validate_mappings({'0': entry({'action': 'shortcut', 'value': 'W', 'mode': 'toggle'})})
    with pytest.raises(ValueError, match='触摸板手势'):
        validate_mappings({'TP:tap': entry(binding())})
    old = entry({'action': 'hold', 'value': 'W'})
    assert validate_mappings({'0': old})['0'] == copy.deepcopy(old)
    assert validate_mappings({'0': entry(binding(mode='hold'))})['0']['short']['mode'] == 'hold'


@pytest.mark.parametrize('value', ['Caps', 'CapsLock', 'NumLock', 'ScrollLock', 'Ctrl+CapsLock'])
def test_toggle_rejects_os_lock_keys_that_latch_on_their_own(value):
    with pytest.raises(ValueError, match='不能使用切换保持'):
        validate_mappings({'0': entry(binding(value=value))})

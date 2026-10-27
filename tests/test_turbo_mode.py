"""Tests for Virtual KBM Hold-to-Repeat Turbo mode (validation, clock-driven pulsing, and safety)."""
from __future__ import annotations

import copy
import pytest

from gamepadstudio.actions import parse_keys
from gamepadstudio.mapping_engine import (
    MappingRuntime, validate_binding, validate_mappings, binding_label
)
from gamepadstudio.profile_transfer import (
    PROFILE_FORMAT, preview_profile_import, export_profile,
)


def pt_state(family='dualsense', touch=True, fingers=2, buttons=None, **extra):
    result = dict(family=family, device_key='physical:private', instance_id=11,
                  is_gamecontroller=True, available_buttons=list(range(21)) if buttons is None else buttons,
                  available_axes=list(range(6)), touchpad=touch, touchpad_fingers=fingers,
                  model_key='dualsense:054c:0ce6:01234567890123456789012345678901')
    result.update(extra)
    return result


def pt_mapping(action='hold', value='Space', **extra):
    b = {'action': action}
    if value is not None:
        b['value'] = value
    b.update(extra)
    return {'short': b, 'long': {'action': 'none'}}


def pt_package(mappings=None, family='dualsense', **source):
    return {'format': PROFILE_FORMAT, 'version': 1,
            'source': dict(family=family, input_kind='sdl_gamecontroller', **source),
            'profile': {'name': '我的预设', 'mode': 'kbm',
                        'mappings': {'0': pt_mapping()} if mappings is None else mappings,
                        'options': {'right_stick_mouse': True,
                                    'input': {'stick_press': .24, 'stick_release': .17,
                                              'walk_press': .62, 'walk_release': .72,
                                              'chord_window': .055},
                                    'mouse': {'mode': 'game', 'sensitivity': 24., 'deadzone': .09,
                                              'y_ratio': .7, 'edge_boost': 1.45}}}}


def binding(action='hold', value='Space', mode='turbo', rate_hz=15):
    b = {'action': action, 'value': value, 'mode': mode}
    if rate_hz is not None:
        b['rate_hz'] = rate_hz
    return b


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


# --- 1. Validation Tests ---

@pytest.mark.parametrize('hz', [5, 10, 15, 20, 25, 30])
def test_validate_binding_turbo_accepts_valid_rates(hz):
    # Keyboard hold
    b_k = validate_binding({'action': 'hold', 'value': 'Space', 'mode': 'turbo', 'rate_hz': hz})
    assert b_k['mode'] == 'turbo' and b_k['rate_hz'] == hz

    # Mouse hold
    b_m = validate_binding({'action': 'mouse_hold', 'value': 'left', 'mode': 'turbo', 'rate_hz': hz})
    assert b_m['mode'] == 'turbo' and b_m['rate_hz'] == hz


def test_validate_binding_turbo_defaults_rate_hz():
    b = validate_binding({'action': 'hold', 'value': 'Space', 'mode': 'turbo'})
    assert b['mode'] == 'turbo'
    assert b['rate_hz'] == 15


@pytest.mark.parametrize('bad_hz', [True, False])
def test_validate_binding_turbo_rejects_booleans(bad_hz):
    with pytest.raises(ValueError, match='连发速率'):
        validate_binding({'action': 'hold', 'value': 'Space', 'mode': 'turbo', 'rate_hz': bad_hz})


@pytest.mark.parametrize('bad_hz', ['15', 'turbo', '', 15.0, 15.5, None, [], {}])
def test_validate_binding_turbo_rejects_non_integers(bad_hz):
    with pytest.raises(ValueError, match='连发速率'):
        validate_binding({'action': 'hold', 'value': 'Space', 'mode': 'turbo', 'rate_hz': bad_hz})


@pytest.mark.parametrize('bad_hz', [4, 1, 0, -1, -5, 31, 50, 100])
def test_validate_binding_turbo_rejects_out_of_range(bad_hz):
    with pytest.raises(ValueError, match='连发速率应在 5 至 30 Hz 之间'):
        validate_binding({'action': 'hold', 'value': 'Space', 'mode': 'turbo', 'rate_hz': bad_hz})


@pytest.mark.parametrize('lock_key', ['CapsLock', 'NumLock', 'ScrollLock', 'Ctrl+CapsLock'])
def test_validate_binding_turbo_rejects_lock_keys(lock_key):
    with pytest.raises(ValueError, match='不能使用连发'):
        validate_binding({'action': 'hold', 'value': lock_key, 'mode': 'turbo', 'rate_hz': 15})


@pytest.mark.parametrize('action', ['shortcut', 'mouse_click', 'wheel', 'launch', 'gamepad_button'])
def test_validate_binding_turbo_rejects_non_hold_actions(action):
    with pytest.raises(ValueError, match='模式'):
        validate_binding({'action': action, 'value': 'W', 'mode': 'turbo'})


@pytest.mark.parametrize('tp_key', ['TP:tap', 'TP:swipe_left', 'TP:swipe_right'])
def test_validate_mappings_turbo_rejects_touchpad_gestures(tp_key):
    with pytest.raises(ValueError, match='触摸板手势不能使用切换保持或连发'):
        validate_mappings({tp_key: {'short': {'action': 'hold', 'value': 'A', 'mode': 'turbo'}}})


def test_binding_label_formats_turbo():
    assert binding_label({'action': 'hold', 'value': 'Space', 'mode': 'turbo', 'rate_hz': 20}) == 'Space（连发 20Hz）'
    assert binding_label({'action': 'hold', 'value': 'Space', 'mode': 'turbo'}) == 'Space（连发 15Hz）'
    assert binding_label({'action': 'mouse_hold', 'value': 'left', 'mode': 'turbo', 'rate_hz': 10}) == '鼠标左键（连发 10Hz）'
    assert binding_label({'action': 'mouse_hold', 'value': 'right', 'mode': 'turbo', 'rate_hz': 30}) == '鼠标右键（连发 30Hz）'
    assert binding_label({'action': 'mouse_hold', 'value': 'middle', 'mode': 'turbo'}) == '鼠标中键（连发 15Hz）'


# --- 2. Deterministic Pulsing Tests ---

def test_turbo_deterministic_cadence_10hz():
    # 10Hz -> period = 0.1s, half_period = 0.05s
    engine, config, actions = runtime({'0': entry(binding('hold', 'A', 'turbo', 10))})
    key_a = ord('A')

    # t=0.00: Down
    engine.update(frame(0), config, now=0.00)
    assert actions.calls == [('key', key_a, True)]
    assert engine.output_counts[f'key:{key_a}'] == 1

    # t=0.02: In first half of cycle, remains down
    engine.update(frame(0), config, now=0.02)
    assert len(actions.calls) == 1

    # t=0.05: Half-period reached, releases key
    engine.update(frame(0), config, now=0.05)
    assert actions.calls == [('key', key_a, True), ('key', key_a, False)]
    assert engine.output_counts[f'key:{key_a}'] == 0

    # t=0.08: In second half of cycle, remains up
    engine.update(frame(0), config, now=0.08)
    assert len(actions.calls) == 2

    # t=0.10: Full period reached, presses down again
    engine.update(frame(0), config, now=0.10)
    assert actions.calls == [('key', key_a, True), ('key', key_a, False), ('key', key_a, True)]
    assert engine.output_counts[f'key:{key_a}'] == 1

    # t=0.15: Second cycle release
    engine.update(frame(0), config, now=0.15)
    assert actions.calls[-1] == ('key', key_a, False)

    # t=0.20: Third cycle press
    engine.update(frame(0), config, now=0.20)
    assert actions.calls[-1] == ('key', key_a, True)


def test_turbo_deterministic_cadence_20hz():
    # 20Hz -> period = 0.05s, half_period = 0.025s
    engine, config, actions = runtime({'0': entry(binding('hold', 'A', 'turbo', 20))})
    key_a = ord('A')

    engine.update(frame(0), config, now=0.000)
    assert actions.calls == [('key', key_a, True)]

    engine.update(frame(0), config, now=0.025)
    assert actions.calls == [('key', key_a, True), ('key', key_a, False)]

    engine.update(frame(0), config, now=0.050)
    assert actions.calls == [('key', key_a, True), ('key', key_a, False), ('key', key_a, True)]

    engine.update(frame(0), config, now=0.075)
    assert actions.calls[-1] == ('key', key_a, False)

    engine.update(frame(0), config, now=0.100)
    assert actions.calls[-1] == ('key', key_a, True)


def test_turbo_boundary_frequencies_5hz_and_30hz():
    # 5Hz -> period = 0.20s, half = 0.10s
    engine5, config5, actions5 = runtime({'0': entry(binding('hold', 'A', 'turbo', 5))})
    key_a = ord('A')
    engine5.update(frame(0), config5, now=0.00)
    assert actions5.calls == [('key', key_a, True)]
    engine5.update(frame(0), config5, now=0.09)
    assert len(actions5.calls) == 1
    engine5.update(frame(0), config5, now=0.10)
    assert actions5.calls == [('key', key_a, True), ('key', key_a, False)]

    # 30Hz -> period = 1/30s, half = 1/60s
    engine30, config30, actions30 = runtime({'0': entry(binding('hold', 'A', 'turbo', 30))})
    engine30.update(frame(0), config30, now=0.00)
    assert actions30.calls == [('key', key_a, True)]
    engine30.update(frame(0), config30, now=1.0 / 60.0)
    assert actions30.calls == [('key', key_a, True), ('key', key_a, False)]


def test_turbo_mouse_button_pulsing():
    engine, config, actions = runtime({'0': entry(binding('mouse_hold', 'left', 'turbo', 10))})
    engine.update(frame(0), config, now=0.00)
    assert actions.calls == [('mouse', 'left', True)]

    engine.update(frame(0), config, now=0.05)
    assert actions.calls == [('mouse', 'left', True), ('mouse', 'left', False)]

    engine.update(frame(0), config, now=0.10)
    assert actions.calls[-1] == ('mouse', 'left', True)


def test_turbo_multiple_simultaneous_buttons():
    # Button 0 -> A at 10Hz (half=0.05s), Button 1 -> B at 20Hz (half=0.025s)
    engine, config, actions = runtime({
        '0': entry(binding('hold', 'A', 'turbo', 10)),
        '1': entry(binding('hold', 'B', 'turbo', 20)),
    })
    key_a, key_b = ord('A'), ord('B')

    engine.update(frame(0, 1), config, now=0.000)
    assert ('key', key_a, True) in actions.calls
    assert ('key', key_b, True) in actions.calls

    # At t=0.025: B releases, A remains down
    engine.update(frame(0, 1), config, now=0.025)
    assert ('key', key_b, False) in actions.calls
    assert actions.keys[key_a] == 1 and actions.keys[key_b] == 0

    # At t=0.050: B presses down, A releases
    engine.update(frame(0, 1), config, now=0.050)
    assert actions.keys[key_a] == 0 and actions.keys[key_b] == 1


# --- 3. Safety Release Tests ---

def test_turbo_release_during_down_phase_guarantees_immediate_key_up():
    engine, config, actions = runtime({'0': entry(binding('hold', 'A', 'turbo', 10))})
    key_a = ord('A')
    engine.update(frame(0), config, now=0.00)
    assert actions.calls == [('key', key_a, True)]

    # Release physical button at t=0.03 while currently held down
    engine.update(frame(), config, now=0.03)
    assert actions.calls == [('key', key_a, True), ('key', key_a, False)]
    assert actions.keys[key_a] == 0
    assert engine.output_counts[f'key:{key_a}'] == 0
    assert not getattr(engine, 'turbo_bindings', {})

    # Advancing time to normal pulse moments produces NO further events
    engine.update(frame(), config, now=0.05)
    engine.update(frame(), config, now=0.10)
    assert len(actions.calls) == 2


def test_turbo_release_during_up_phase_cleans_up_without_duplicate_key_up():
    engine, config, actions = runtime({'0': entry(binding('hold', 'A', 'turbo', 10))})
    key_a = ord('A')
    engine.update(frame(0), config, now=0.00)
    engine.update(frame(0), config, now=0.05)  # now UP
    assert actions.calls == [('key', key_a, True), ('key', key_a, False)]

    # Release physical button at t=0.07 while in UP phase
    engine.update(frame(), config, now=0.07)
    # Must NOT emit a redundant key-up
    assert len(actions.calls) == 2
    assert actions.keys[key_a] == 0
    assert not getattr(engine, 'turbo_bindings', {})

    # Advancing time produces no further pulses
    engine.update(frame(), config, now=0.10)
    assert len(actions.calls) == 2


def test_turbo_gesture_cancel_releases_key():
    # Chord 0+1 cancels short press of 0 if configured
    engine, config, actions = runtime({
        '0': entry(binding('hold', 'A', 'turbo', 10)),
        '0+1': entry(binding('hold', 'C', 'hold'))
    })
    key_a = ord('A')
    # Press button 0 and advance past chord window (0.08s) so 'short' fires
    engine.update(frame(0), config, now=0.00)
    engine.update(frame(0), config, now=0.09)
    assert actions.calls == [('key', key_a, True)]

    # Now press 1 -> chord 0+1 takes over and cancels 0
    engine.update(frame(0, 1), config, now=0.11)
    assert ('key', key_a, False) in actions.calls
    assert actions.keys[key_a] == 0


@pytest.mark.parametrize('interrupt', ['pause', 'disconnect', 'edit', 'profile', 'reset', 'close'])
def test_turbo_interrupt_releases_held_key(interrupt):
    engine, config, actions = runtime({'0': entry(binding('hold', 'A', 'turbo', 10))})
    key_a = ord('A')
    engine.update(frame(0), config, now=0.00)
    assert actions.keys[key_a] == 1

    if interrupt == 'pause':
        engine.update(frame(0), config, enabled=False, now=0.02)
    elif interrupt == 'disconnect':
        engine.update(None, config, now=0.02)
    elif interrupt == 'edit':
        config['profiles']['test']['0'] = entry(binding('hold', 'X', 'turbo', 10))
        engine.update(frame(0), config, now=0.02)
    elif interrupt == 'profile':
        config['profiles']['other'] = {'0': entry(binding('hold', 'X', 'turbo', 10))}
        config['active_profile'] = 'other'
        engine.update(frame(0), config, now=0.02)
    elif interrupt == 'close':
        engine.close()
    else:
        engine.reset()

    assert actions.keys[key_a] == 0
    assert not getattr(engine, 'turbo_bindings', {})
    assert engine.output_counts[f'key:{key_a}'] == 0


def test_turbo_reset_refuse_key_up_error_handling():
    engine, config, actions = runtime({
        '0': entry(binding('hold', 'A', 'turbo', 10)),
        '1': entry(binding('hold', 'B', 'turbo', 10)),
    })
    key_a, key_b = ord('A'), ord('B')
    engine.update(frame(0, 1), config, now=0.00)
    assert actions.keys[key_a] == 1 and actions.keys[key_b] == 1

    actions.refuse_key_up = key_a
    with pytest.raises(OSError, match='未能释放'):
        engine.reset()

    # B was released despite A failing
    assert actions.keys[key_b] == 0
    assert actions.keys[key_a] == 1

    # Clearing refusal allows second reset attempt to succeed
    actions.refuse_key_up = None
    engine.reset()
    assert actions.keys[key_a] == 0
    assert not getattr(engine, 'turbo_bindings', {})


def test_turbo_preview_mode_never_posts_output():
    engine, config, actions = runtime({'0': entry(binding('hold', 'A', 'turbo', 10))})
    key_a = ord('A')
    engine.update(frame(0), config, preview=True, now=0.00)
    # Output actions are never called
    assert not actions.calls
    assert actions.keys.get(key_a, 0) == 0
    # Feedback shows active output token visually
    assert engine.feedback()['outputs'] == [f'key:{key_a}']


# --- 4. Profile Transfer Tests for Turbo ---

def test_portable_turbo_mode_round_trip():
    original = pt_package({
        '0': pt_mapping('hold', 'Space', mode='turbo', rate_hz=25),
        '1': pt_mapping('mouse_hold', 'left', mode='turbo', rate_hz=10),
    })
    result = preview_profile_import(original, pt_state())
    m = result['profile']['mappings']
    assert m['0']['short']['mode'] == 'turbo'
    assert m['0']['short']['rate_hz'] == 25
    assert m['1']['short']['mode'] == 'turbo'
    assert m['1']['short']['rate_hz'] == 10


def test_portable_turbo_mode_defaults_rate_hz():
    original = pt_package({
        '0': pt_mapping('hold', 'Space', mode='turbo'),
    })
    result = preview_profile_import(original, pt_state())
    assert result['profile']['mappings']['0']['short']['rate_hz'] == 15


@pytest.mark.parametrize('bad_hz', [True, False, '15', 4, 31, 0, None])
def test_portable_turbo_mode_rejects_invalid_rates(bad_hz):
    orig = pt_package({'0': pt_mapping('hold', 'Space', mode='turbo', rate_hz=bad_hz)})
    with pytest.raises(ValueError, match='连发速率'):
        preview_profile_import(orig, pt_state())


def test_portable_rate_hz_on_non_turbo_mode_is_rejected():
    orig = pt_package({'0': pt_mapping('hold', 'Space', mode='hold', rate_hz=15)})
    with pytest.raises(ValueError):
        preview_profile_import(orig, pt_state())

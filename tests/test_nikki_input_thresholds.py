"""Profile input tuning must retain release guarantees and precise game aiming."""
from types import SimpleNamespace

import pytest

from gamepadstudio.mapping_engine import (InputNormalizer, input_thresholds, INPUT_THRESHOLD_DEFAULTS,
                                         canonical_trigger, trigger_label, validate_mappings)
from gamepadstudio import virtual_kbm
from tests.test_unified_mapping import frame, entry, runtime


NIKKI_INPUT = {'trigger_press': .30, 'trigger_release': .20,
               'stick_press': .24, 'stick_release': .17}


def test_profile_thresholds_enable_light_diagonal_movement_with_hysteresis():
    normalizer = InputNormalizer()
    assert not normalizer.update(frame(axes=[.23, -.23, 0, 0, .29, .29]), NIKKI_INPUT)
    assert normalizer.update(frame(axes=[.25, -.25, 0, 0, .31, .31]), NIKKI_INPUT) == {
        'LS:right', 'LS:up', 'LT', 'RT'}
    assert normalizer.update(frame(axes=[.18, -.18, 0, 0, .21, .21]), NIKKI_INPUT) == {
        'LS:right', 'LS:up', 'LT', 'RT'}
    assert not normalizer.update(frame(axes=[.16, -.16, 0, 0, .19, .19]), NIKKI_INPUT)


def test_direction_reversal_releases_old_key_without_opposing_outputs():
    engine, config, actions, _ = runtime({'LS:left': entry('hold', 'A'), 'LS:right': entry('hold', 'D')})
    config['profile_options'] = {'test': {'input': NIKKI_INPUT}}
    engine.update(frame(axes=[.3, 0, 0, 0, 0, 0]), config, now=0)
    assert actions.keys[ord('D')] == 1
    engine.update(frame(axes=[-.3, 0, 0, 0, 0, 0]), config, now=.1)
    assert actions.keys[ord('D')] == 0 and actions.keys[ord('A')] == 1
    engine.update(frame(), config, now=.2)
    assert not any(actions.keys.values())


def test_outer_threshold_is_optional_and_uses_its_own_release_band():
    normalizer = InputNormalizer()
    settings = {'outer_press': .92, 'outer_release': .80}
    assert 'LS:outer' not in normalizer.update(frame(axes=[.90, 0, 0, 0, 0, 0]), settings)
    assert 'LS:outer' in normalizer.update(frame(axes=[.93, 0, 0, 0, 0, 0]), settings)
    assert 'LS:outer' in normalizer.update(frame(axes=[.81, 0, 0, 0, 0, 0]), settings)
    assert 'LS:outer' not in normalizer.update(frame(axes=[.79, 0, 0, 0, 0, 0]), settings)
    assert not normalizer.update(frame(), settings)


def test_light_push_walk_band_avoids_chatter_and_releases_at_neutral():
    normalizer = InputNormalizer()
    settings = dict(NIKKI_INPUT, walk_press=.62, walk_release=.72)
    for magnitude, walking in [(.63, False), (.61, True), (.65, True), (.71, True),
                               (.73, False), (.71, False), (.63, False), (.62, True)]:
        inputs = normalizer.update(frame(axes=[magnitude, 0, 0, 0, 0, 0]), settings)
        assert ('LS:inner' in inputs) is walking
        assert 'LS:right' in inputs
    assert not normalizer.update(frame(axes=[.16, 0, 0, 0, 0, 0]), settings)
    assert not normalizer.update(frame(), settings)
    # A deflection below the movement threshold cannot press Ctrl alone.
    assert not normalizer.update(frame(axes=[.20, 0, 0, 0, 0, 0]), settings)


def test_light_push_walk_modifier_tracks_direction_reversal_and_full_push():
    engine, config, actions, _ = runtime({'LS:left': entry('hold', 'A'),
        'LS:right': entry('hold', 'D'), 'LS:inner': entry('hold', 'Ctrl')})
    config['profile_options'] = {'test': {'input': dict(NIKKI_INPUT, walk_press=.62, walk_release=.72)}}
    engine.update(frame(axes=[.5, 0, 0, 0, 0, 0]), config, now=0)
    assert actions.keys[17] == 1 and actions.keys[ord('D')] == 1
    engine.update(frame(axes=[-.5, 0, 0, 0, 0, 0]), config, now=.1)
    assert actions.keys[17] == 1 and actions.keys[ord('A')] == 1 and actions.keys[ord('D')] == 0
    engine.update(frame(axes=[-.9, 0, 0, 0, 0, 0]), config, now=.2)
    assert actions.keys[17] == 0 and actions.keys[ord('A')] == 1
    engine.update(frame(), config, now=.3)
    assert not any(actions.keys.values())


def test_old_profiles_disable_walk_even_after_using_a_walk_profile():
    normalizer = InputNormalizer()
    held = frame(axes=[.5, 0, 0, 0, 0, 0])
    assert 'LS:inner' in normalizer.update(held, dict(NIKKI_INPUT, walk_press=.62, walk_release=.72))
    assert 'LS:inner' not in normalizer.update(held)
    assert 'LS:inner' not in normalizer.update(held, NIKKI_INPUT)
    assert canonical_trigger('LS:INNER') == 'LS:inner'
    assert trigger_label('LS:inner') == '左摇杆轻推'
    assert validate_mappings({'LS:inner': entry('hold', 'Ctrl')})['LS:inner']['short']['value'] == 'Ctrl'


@pytest.mark.parametrize('settings', [{'walk_press': .62}, {'walk_release': .72},
    {'walk_press': 0, 'walk_release': .72}, {'walk_press': .72, 'walk_release': .62},
    {'walk_press': .62, 'walk_release': .62}, {'walk_press': .62, 'walk_release': 1.1},
    {'walk_press': float('nan'), 'walk_release': .72},
    {'walk_press': .62, 'walk_release': float('inf')}, {'walk_press': True, 'walk_release': .72}])
def test_incomplete_or_invalid_walk_settings_never_create_ctrl_only_input(settings):
    normalizer = InputNormalizer()
    assert 'LS:inner' not in normalizer.update(frame(axes=[.5, 0, 0, 0, 0, 0]), settings)
    assert not normalizer.update(frame(), settings)


@pytest.mark.parametrize('settings', [None, [], 'invalid',
    {'trigger_press': float('nan')}, {'trigger_release': float('inf')},
    {'trigger_press': None}, {'trigger_release': 'invalid'},
    {'trigger_press': True}, {'trigger_press': 1.1}, {'trigger_release': -.1},
    {'trigger_release': 0}, {'trigger_press': .3, 'trigger_release': .3},
    {'trigger_press': .2, 'trigger_release': .3},
    {'stick_release': 0}, {'outer_release': 0}])
def test_invalid_threshold_pairs_fall_back_and_neutral_always_releases(settings):
    assert input_thresholds(settings) == INPUT_THRESHOLD_DEFAULTS
    normalizer = InputNormalizer()
    assert normalizer.update(frame(axes=[1, 0, 0, 0, 1, 1]), settings) == {
        'LS:right', 'LS:outer', 'LT', 'RT'}
    assert not normalizer.update(frame(), settings)


def test_lowering_threshold_does_not_emit_for_an_already_pressed_trigger():
    engine, config, actions, _ = runtime({'LT': entry('mouse_hold', 'left')})
    lightly_pressed = frame(axes=[0, 0, 0, 0, .4, 0])
    engine.update(lightly_pressed, config, now=0)
    assert not actions.mouse
    config['profile_options'] = {'test': {'input': NIKKI_INPUT}}
    engine.update(lightly_pressed, config, now=.1)
    assert engine.feedback()['inputs'] == ['LT']
    assert not actions.calls
    engine.update(lightly_pressed, config, now=.2)
    assert not actions.calls
    engine.update(frame(), config, now=.3)
    engine.update(lightly_pressed, config, now=.4)
    assert actions.mouse == {'left'}
    engine.update(frame(), config, now=.5)
    assert not actions.mouse


def test_threshold_change_releases_held_output_and_requires_a_new_press():
    engine, config, actions, _ = runtime({'LT': entry('mouse_hold', 'left')})
    config['profile_options'] = {'test': {'input': dict(NIKKI_INPUT)}}
    held = frame(axes=[0, 0, 0, 0, .6, 0])
    engine.update(held, config, now=0)
    assert actions.mouse == {'left'}
    config['profile_options']['test']['input'] = {'trigger_press': .55, 'trigger_release': .35}
    engine.update(held, config, now=.1)
    assert not actions.mouse
    engine.update(held, config, now=.2)
    assert not actions.mouse
    engine.update(frame(), config, now=.3)
    engine.update(held, config, now=.4)
    assert actions.mouse == {'left'}


@pytest.mark.parametrize(('settings', 'expected_window'),
    [({}, .08), ({'chord_window': .055}, .055), ({'chord_window': .019}, .08),
     ({'chord_window': .201}, .08), ({'chord_window': 'invalid'}, .08),
     ({'chord_window': '0.055'}, .08), ({'chord_window': None}, .08),
     ({'chord_window': True}, .08), ({'chord_window': float('nan')}, .08),
     ({'chord_window': float('inf')}, .08), ({'chord_window': 10 ** 400}, .08)])
def test_profile_chord_window_reduces_core_button_delay_and_keeps_default_fallback(settings, expected_window):
    engine, config, actions, _ = runtime({'0': entry('hold', 'Space'), '0+9': entry('shortcut', '1')})
    config['profile_options'] = {'test': {'input': settings}}
    engine.update(frame([0]), config, now=0)
    engine.update(frame([0]), config, now=.054)
    assert not actions.calls
    engine.update(frame([0]), config, now=.056)
    assert bool(actions.calls) is (expected_window == .055)
    assert engine.engine.chord_window == expected_window
    engine.update(frame([0]), config, now=.081)
    assert actions.keys[32] == 1
    engine.update(frame(), config, now=.1)
    assert not any(actions.keys.values())


@pytest.mark.parametrize(('is_desktop', 'stick', 'moves_expected'),
                         [(False, .20, True), (True, .20, False),
                          (True, .45, True), (False, .04, False)])
def test_clicking_preserves_game_micro_aim_but_filters_desktop_jitter(
        monkeypatch, is_desktop, stick, moves_expected):
    actions = SimpleNamespace(moves=[])
    actions.move_mouse = lambda dx, dy: actions.moves.append((dx, dy))
    mouse = virtual_kbm.VirtualMouseThread(actions)
    mouse.update_stick(stick, 0, is_desktop=is_desktop)
    mouse.set_click_lock(True)
    elapsed = 0.

    def advance_clock():
        nonlocal elapsed
        elapsed += .003
        if elapsed >= .15:
            mouse.running = False
        return elapsed

    monkeypatch.setattr(virtual_kbm, 'time', SimpleNamespace(perf_counter=advance_clock, sleep=lambda _: None))
    monkeypatch.setattr(virtual_kbm, 'set_system_timer_resolution', lambda _: None)
    monkeypatch.setattr('gamepadstudio.actions.attach_to_default_desktop', lambda: None)
    mouse.run()
    assert bool(actions.moves) is moves_expected
    assert all(dx > 0 and dy == 0 for dx, dy in actions.moves)

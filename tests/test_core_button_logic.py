"""Verification of Universal Button Logic as Core and Loaded Profile as Single Source of Truth."""
import pytest
from gamepadstudio.mapping_engine import (
    GestureEngine, MappingRuntime, canonical_trigger, convert_scheme, effective_mappings
)
from gamepadstudio.studio_core import ConfigStore
from gamepadstudio.kbm_mapper import NIKKI_PROFILE_NAME, CHORD_MAPPINGS
from tests.test_unified_mapping import Actions, frame, entry


def test_radial_menu_no_leak_when_holding_shoulder_or_trigger(tmp_path):
    """Holding LB to press LB+A must NEVER emit Tab (no radial wheel popup in game)."""
    store = ConfigStore(tmp_path)
    store.data['active_profile'] = NIKKI_PROFILE_NAME
    actions = Actions()
    runtime = MappingRuntime(actions, lambda *args: None, start_mouse=False)

    # 1. Player presses LB (button 9) alone at t=0
    runtime.update(frame([9]), store.data, now=0.0)
    # Player holds LB while deciding/coordinating for 200ms
    runtime.update(frame([9]), store.data, now=0.1)
    runtime.update(frame([9]), store.data, now=0.2)
    # Assert: Tab was NEVER fired! No radial wheel popup!
    assert not actions.calls, f"Expected no button leakage during hold, got {actions.calls}"

    # 2. Player presses A (button 0) to form chord LB+A (Outfit 1) at t=0.25
    runtime.update(frame([9, 0]), store.data, now=0.25)
    # Outfit 1 (key '1') must be fired immediately!
    assert any(call == ('key', '1', True) for call in actions.calls)
    assert not any('Tab' in str(call) for call in actions.calls), "Tab must NOT leak when chord triggers!"

    # 3. Player releases A, still holding LB
    runtime.update(frame([9]), store.data, now=0.35)
    # 4. Player releases LB
    runtime.update(frame([]), store.data, now=0.50)
    # Tab must NOT fire on release because LB was consumed by the chord!
    assert not any('Tab' in str(call) for call in actions.calls)


def test_tap_shoulder_button_alone_opens_radial_menu_on_release(tmp_path):
    """Tapping LB quickly (< 250ms) without chord fires Tab on release to toggle radial wheel."""
    store = ConfigStore(tmp_path)
    store.data['active_profile'] = NIKKI_PROFILE_NAME
    actions = Actions()
    runtime = MappingRuntime(actions, lambda *args: None, start_mouse=False)

    # Tap LB down at t=0
    runtime.update(frame([9]), store.data, now=0.0)
    assert not actions.calls  # Doesn't fire immediately on down

    # Release LB at t=0.08 (quick tap)
    runtime.update(frame([]), store.data, now=0.08)
    assert ('key', 'Tab', True) in actions.calls  # Fires Tab on release!


def test_long_hold_shoulder_button_alone_stays_silent(tmp_path):
    """Holding LB for > 250ms alone without chord stays silent and does NOT fire Tab on release."""
    store = ConfigStore(tmp_path)
    store.data['active_profile'] = NIKKI_PROFILE_NAME
    actions = Actions()
    runtime = MappingRuntime(actions, lambda *args: None, start_mouse=False)

    # Press LB down at t=0
    runtime.update(frame([9]), store.data, now=0.0)
    # Hold for 1 second
    runtime.update(frame([9]), store.data, now=0.5)
    runtime.update(frame([9]), store.data, now=1.0)
    assert not actions.calls

    # Release LB at t=1.1
    runtime.update(frame([]), store.data, now=1.1)
    # Since it was held past threshold, it was a long hold, not a short tap; stays silent!
    assert not actions.calls


@pytest.mark.parametrize('btn,chord_partner,chord_key,chord_out,single_out', [
    ('9', '0', '0+9', '1', 'Tab'),         # LB + A -> 1 (Outfit 1)
    ('10', '0', '0+10', 'M', 'V'),        # RB + A -> M (Map)
    ('LT', '0', '0+LT', '5', 'right'),    # LT + A -> 5 (Outfit 5)
    ('RT', '0', '0+RT', 'O', 'left'),     # RT + A -> O (Resonance)
])
def test_universal_button_logic_for_all_modifier_keys(btn, chord_partner, chord_key, chord_out, single_out):
    """Verify universal button logic applies identically to any modifier button without hacks."""
    calls = []
    engine = GestureEngine(lambda b, d: calls.append((b.get('value'), d)))
    maps = {
        btn: {'short': {'action': 'shortcut', 'value': single_out}, 'long': {'action': 'suppress'}, 'long_press': 0.20},
        chord_partner: entry('hold', 'Space'),
        chord_key: entry('shortcut', chord_out)
    }

    # 1. Hold modifier alone past 200ms -> stays silent
    engine.update({btn}, maps, 0.0)
    engine.update({btn}, maps, 0.25)
    assert not calls

    # 2. Press chord partner -> chord fires immediately, modifier suppressed
    engine.update({btn, chord_partner}, maps, 0.30)
    assert (chord_out, True) in calls
    assert not any(c[0] == single_out for c in calls)

    # 3. Release both -> no single_out leaked
    engine.update(set(), maps, 0.40)
    assert not any(c[0] == single_out for c in calls)


def test_active_profile_is_single_source_of_truth_for_all_mappings(tmp_path):
    """Verify controller bindings and virtual KBM are 100% unified under active_profile."""
    store = ConfigStore(tmp_path)
    # 1. Default loaded profile has full 40 entries for Nikki
    assert NIKKI_PROFILE_NAME in store.data['profiles']
    nikki = store.data['profiles'][NIKKI_PROFILE_NAME]
    assert len(nikki) >= 30
    assert '0+9' in nikki
    assert '0+LT' in nikki
    assert 'LS:up' in nikki
    assert 'LS:outer' in nikki

    # 2. Setting options on active profile is recognized
    options = store.data['profile_options'][NIKKI_PROFILE_NAME]
    assert options.get('right_stick_mouse') is True

    # 3. Runtime execution is directly driven by the active profile
    store.data['active_profile'] = NIKKI_PROFILE_NAME
    actions = Actions()
    runtime = MappingRuntime(actions, lambda *args: None, start_mouse=False)

    # In-game controller input acts as trigger stimulus to the loaded profile
    # Left stick up stimulates 'W'
    runtime.update(frame([], [0, -0.9, 0, 0, 0, 0]), store.data, now=0.0)
    assert actions.keys[ord('W')] == 1

    # Left stick outer stimulates 'Shift'
    runtime.update(frame([], [0, -0.95, 0, 0, 0, 0]), store.data, now=0.05)
    assert actions.keys[16] == 1  # VK_SHIFT = 16


def test_chord_locks_independent_of_release_order():
    """Universal rule:
    Press modifier, then press combo button -> emit immediately.
    Before both buttons are released, NEITHER button fires any single action.
    Evaluation ends only when the LAST button is released, regardless of release order.
    """
    maps = {
        '9': {'short': {'action': 'shortcut', 'value': 'Tab'}, 'long': {'action': 'suppress'}, 'long_press': 0.20},
        '0': {'short': {'action': 'hold', 'value': 'Space'}, 'long': {'action': 'none'}},
        '0+9': {'short': {'action': 'shortcut', 'value': '1'}, 'long': {'action': 'none'}}
    }

    # Case 1: Release modifier (9) first at t=0.20, then release combo button (0) at t=0.30
    calls = []
    engine = GestureEngine(lambda b, d: calls.append((b.get('value'), d)))
    engine.update({'9'}, maps, now=0.0)      # LB pressed alone
    engine.update({'9', '0'}, maps, now=0.10) # LB+A pressed -> 1 emitted
    assert calls == [('1', True)]

    engine.update({'0'}, maps, now=0.20)      # LB released first, A still held
    # A MUST NOT fire Space! Lock must hold!
    assert not any(c[0] == 'Space' for c in calls)
    assert not any(c[0] == 'Tab' for c in calls)

    engine.update(set(), maps, now=0.30)      # A released second (last button released)
    # Both released, evaluation ends cleanly without leaking Space or Tab
    assert calls == [('1', True)]

    # Case 2: Release combo button (0) first at t=0.20, then release modifier (9) at t=0.30
    calls.clear()
    engine = GestureEngine(lambda b, d: calls.append((b.get('value'), d)))
    engine.update({'9'}, maps, now=0.0)      # LB pressed alone
    engine.update({'9', '0'}, maps, now=0.10) # LB+A pressed -> 1 emitted
    assert calls == [('1', True)]

    engine.update({'9'}, maps, now=0.20)      # A released first, LB still held
    # LB MUST NOT fire Tab! Lock must hold!
    assert not any(c[0] == 'Tab' for c in calls)
    assert not any(c[0] == 'Space' for c in calls)

    engine.update(set(), maps, now=0.30)      # LB released second (last button released)
    assert calls == [('1', True)]


def test_chord_retrigger_while_holding_modifier():
    """Holding modifier allows repeatedly tapping combo buttons to trigger chords without single-key leaks."""
    maps = {
        '9': {'short': {'action': 'shortcut', 'value': 'Tab'}, 'long': {'action': 'suppress'}, 'long_press': 0.20},
        '0': {'short': {'action': 'hold', 'value': 'Space'}, 'long': {'action': 'none'}},
        '1': {'short': {'action': 'hold', 'value': 'Shift'}, 'long': {'action': 'none'}},
        '0+9': {'short': {'action': 'shortcut', 'value': '1'}, 'long': {'action': 'none'}},
        '1+9': {'short': {'action': 'shortcut', 'value': '2'}, 'long': {'action': 'none'}}
    }
    calls = []
    engine = GestureEngine(lambda b, d: calls.append((b.get('value'), d)))
    engine.update({'9'}, maps, now=0.0)
    engine.update({'9', '0'}, maps, now=0.10) # LB+A -> 1
    assert calls == [('1', True)]

    engine.update({'9'}, maps, now=0.20)      # release A, still hold LB
    engine.update({'9', '1'}, maps, now=0.30) # LB+B -> 2
    assert calls == [('1', True), ('2', True)]

    engine.update({'9'}, maps, now=0.40)      # release B, still hold LB
    engine.update(set(), maps, now=0.50)      # release LB
    assert calls == [('1', True), ('2', True)]
    assert not any(c[0] in ('Space', 'Shift', 'Tab') for c in calls)


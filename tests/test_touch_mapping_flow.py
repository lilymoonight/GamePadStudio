"""Recognized touch scrolling is a real, device-scoped mapping source."""
import copy

import pytest

from gamepadstudio.mapping_engine import (
    MappingRuntime, canonical_trigger, effective_mappings, input_sources,
)
from gamepadstudio.studio_core import ConfigStore, profile_scope
from gamepadstudio.touch_gestures import TOUCH_INPUTS, touch_sources
from tests.test_touch_runtime import TouchActions, ds5, finger, runtime
from tests.test_unified_mapping import entry


def scroll_frame(y, **values):
    return ds5(fingers=[finger(x=.3, y=y),
                        finger(x=.6, y=y, index=1, contact=2)], **values)


def scroll(engine, config, direction='up', *, preview=False):
    engine.update(scroll_frame(.5), config, now=.1, preview=preview)
    # At default sensitivity, 0.16 normalized travel spans three scroll steps.
    engine.update(scroll_frame(.34 if direction == 'up' else .66), config,
                  now=.2, preview=preview)


@pytest.mark.parametrize('direction', ['up', 'down'])
def test_scroll_source_is_canonical_only_on_a_two_finger_device(direction):
    token = f'TP:scroll_{direction}'
    assert token in TOUCH_INPUTS
    assert canonical_trigger(token.upper()) == token
    assert token in input_sources(ds5())
    assert token in touch_sources(ds5())
    assert token not in input_sources(None)
    assert token not in input_sources(ds5(touchpad_fingers=1, touch_finger_counts=[1]))
    with pytest.raises(ValueError):
        canonical_trigger(token + '+20')


@pytest.mark.parametrize('direction', ['up', 'down'])
def test_custom_scroll_action_runs_per_step_without_builtin_wheel(direction):
    token = f'TP:scroll_{direction}'
    engine, config, actions, dispatched = runtime(
        {token: entry('capture')}, {'touch_scroll': True})
    scroll(engine, config, direction)
    assert dispatched == [(entry('capture')['short'], True)] * 3
    assert not actions.calls
    feedback = engine.feedback()
    assert token in feedback['inputs']
    assert len(feedback['events']) == 3
    assert all(event['trigger'] == token and event['gesture'] == 'scroll'
               for event in feedback['events'])
    engine.update(ds5(), config, now=.3)
    engine.update(ds5(), config, now=.8)
    assert len(dispatched) == 3


def test_scroll_shortcut_has_distinct_presses_and_balanced_releases():
    engine, config, actions, dispatched = runtime(
        {'TP:scroll_up': entry('shortcut', 'F7')})
    scroll(engine, config)
    assert actions.calls == [('key', 'F7', True), ('key', 'F7', False)] * 2 + [
        ('key', 'F7', True)]
    assert not dispatched
    engine.update(ds5(), config, now=.26)
    assert actions.calls == [('key', 'F7', True), ('key', 'F7', False)] * 3
    assert not any(actions.keys.values())
    assert not engine.feedback()['outputs']


@pytest.mark.parametrize('binding', [None, entry('none')])
@pytest.mark.parametrize('builtin', [False, True])
def test_unmapped_scroll_uses_only_enabled_builtin_fallback(binding, builtin):
    mappings = {'TP:scroll_up': binding} if binding is not None else {}
    engine, config, actions, dispatched = runtime(mappings, {'touch_scroll': builtin})
    scroll(engine, config)
    assert actions.calls == ([('wheel', 1)] * 3 if builtin else [])
    assert not dispatched
    if builtin:
        assert [event['trigger'] for event in engine.feedback()['events']] == [
            'TP:scroll_up'] * 3


def test_suppressed_scroll_does_not_fall_through_to_builtin_wheel():
    engine, config, actions, dispatched = runtime(
        {'TP:scroll_up': entry('suppress')}, {'touch_scroll': True})
    scroll(engine, config)
    assert not actions.calls and not dispatched
    assert not engine.feedback()['events']


def test_master_switch_disables_custom_scroll_but_builtin_stays_independent():
    engine, config, actions, dispatched = runtime(
        {'TP:scroll_up': entry('capture')},
        {'touch_scroll': True, 'touch_gestures_enabled': False})
    scroll(engine, config)
    assert actions.calls == [('wheel', 1)] * 3
    assert not dispatched
    assert all(event['trigger'] == 'TP:scroll_up'
               for event in engine.feedback()['events'])


def test_each_scroll_direction_has_its_own_custom_or_builtin_behavior():
    engine, config, actions, dispatched = runtime(
        {'TP:scroll_up': entry('capture')}, {'touch_scroll': True})
    engine.update(scroll_frame(.5), config, now=.1)
    engine.update(scroll_frame(.34), config, now=.2)
    engine.update(scroll_frame(.5), config, now=.3)
    assert dispatched == [(entry('capture')['short'], True)] * 3
    assert actions.calls == [('wheel', -1)] * 3
    assert [event['trigger'] for event in engine.feedback()['events']] == [
        'TP:scroll_up'] * 3 + ['TP:scroll_down'] * 3


def test_scroll_preview_records_custom_mapping_without_emitting_os_actions():
    engine, config, actions, dispatched = runtime(
        {'TP:scroll_up': entry('shortcut', 'Ctrl+S')},
        {'touch_scroll': True, 'touch_mouse': True}, preview=True)
    scroll(engine, config, preview=True)
    assert not actions.calls and not dispatched
    assert [event['trigger'] for event in engine.feedback()['events']] == [
        'TP:scroll_up'] * 3
    assert 'key:83' in engine.feedback()['outputs']
    engine.update(ds5(), config, now=.26, preview=True)
    assert not engine.feedback()['outputs']


def test_saved_scroll_binding_runs_after_reload_only_for_its_active_device_profile(tmp_path):
    first, second = ds5(), ds5(unit=2)
    store = ConfigStore(tmp_path)
    store.activate_controller(first)
    source = store.profiles_for(first, 'kbm')[0]
    mapping = entry('shortcut', 'Ctrl+S')
    store.apply_mapping_change({'op': 'binding', 'profile': source,
                               'trigger': 'TP:scroll_up', 'mapping': mapping}, first)
    store.set_setting('touch_gestures_enabled', True, first)
    store.set_setting('touch_gesture_sensitivity', .5, first)
    store.remember_profile(first, source)
    store.save()
    loaded = ConfigStore(tmp_path)
    loaded.activate_controller(first)
    assert loaded.data['active_profile'] == source
    assert loaded.data['profile_devices'][source] == profile_scope(first)
    assert effective_mappings(loaded.data, first)['TP:scroll_up'] == mapping
    actions, dispatched = TouchActions(), []
    engine = MappingRuntime(actions, lambda *args: dispatched.append(args), start_mouse=False)
    engine.update(first, loaded.data, now=0.)
    scroll(engine, loaded.data)
    assert len([call for call in actions.calls if call == ('key', 'Ctrl+S', True)]) == 3
    engine.update(first, loaded.data, now=.3)
    assert not any(actions.keys.values())
    assert not dispatched
    # Another unit of the same controller cannot inherit this binding or output.
    other = copy.deepcopy(loaded.data)
    assert not effective_mappings(other, second, source)
    before = list(actions.calls)
    engine.update(second, other, now=.4)
    engine.update(scroll_frame(.5, instance_id=2, device_key=second['device_key']),
                  other, now=.5)
    engine.update(scroll_frame(.34, instance_id=2, device_key=second['device_key']),
                  other, now=.6)
    assert actions.calls == before


@pytest.mark.parametrize('interruption', ['pause', 'disconnect', 'device', 'capability', 'physical_click'])
def test_scroll_interruption_never_creates_a_late_custom_action(interruption):
    engine, config, actions, dispatched = runtime({'TP:scroll_up': entry('capture')})
    engine.update(scroll_frame(.5), config, now=.1)
    state, options = scroll_frame(.34), {}
    if interruption == 'pause':
        options['enabled'] = False
    elif interruption == 'disconnect':
        state = None
    elif interruption == 'device':
        state.update(instance_id=2, device_key=ds5(unit=2)['device_key'])
    elif interruption == 'capability':
        state.update(touchpad_fingers=1, touch_finger_counts=[1])
    else:
        state['buttons'] = [20]
    engine.update(state, config, now=.2, **options)
    engine.update(scroll_frame(.2), config, now=.3)
    engine.update(ds5(), config, now=.4)
    assert not actions.calls and not dispatched
    assert not engine.feedback()['events']

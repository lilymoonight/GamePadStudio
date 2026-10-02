"""Measured RS deadzones patch one owned KBM preference transactionally."""
import copy
import json
import math

import pytest

from gamepadstudio.studio_core import ConfigStore, pointer_input_signature, profile_scope
from gamepadstudio.virtual_kbm import VirtualMouseThread
from tests.test_application_profile_backend import GAME_A, controller, foreground, settings
from tests.test_battery_backend import battery_backend
from tests.test_profile_transfer_ipc import app, request_over_socket
from tests.test_unified_mapping import entry


@pytest.fixture
def pointer_store(tmp_path):
    store, state = ConfigStore(tmp_path), controller()
    store.activate_controller(state)
    keyboard = store.profiles_for(state, 'kbm')[0]
    native = store.data['active_profile']
    store.apply_mapping_change({'op': 'options', 'profile': keyboard, 'options': {
        'mouse': {'mode': 'game', 'deadzone': .09, 'sensitivity': 31., 'y_ratio': .65,
                  'edge_boost': 1.45, 'invert_y': True},
        'input': {'stick_press': .22, 'stick_release': .17, 'chord_window': .055}}}, state)
    store.save()
    return store, state, keyboard, native


def change(state, profile, value=.07):
    return {'op': 'pointer_deadzone', 'profile': profile, 'deadzone': value,
            'expected_input_signature': pointer_input_signature(state)}


def request(context, profile, value=.07):
    return {'command': 'mapping_change', 'device_scope': profile_scope(context.device.state),
            'instance_id': context.device.state['instance_id'],
            'change': change(context.device.state, profile, value)}


def test_inactive_keyboard_preset_changes_only_mouse_deadzone_and_revision(pointer_store):
    store, state, keyboard, native = pointer_store
    before = copy.deepcopy(store.data)
    assert keyboard != native
    store.apply_mapping_change(change(state, keyboard), state)
    expected = copy.deepcopy(before)
    expected['profile_options'][keyboard]['mouse']['deadzone'] = .07
    expected['mapping_revision'] += 1
    assert store.data == expected
    assert store.data['active_profile'] == native
    assert ConfigStore(store.root).data['profile_options'][keyboard] == expected['profile_options'][keyboard]


def test_stale_store_patches_latest_settings_without_overwriting_concurrent_edits(pointer_store):
    stale, state, keyboard, _ = pointer_store
    editor = ConfigStore(stale.root)
    editor.data['profile_options'][keyboard]['mouse'].update(sensitivity=43., invert_y=False)
    editor.data['profile_options'][keyboard]['input']['chord_window'] = .11
    editor.data['profiles'][keyboard]['0'] = entry('hold', 'R')
    editor.set_setting('battery_notifications_enabled', False, state)
    editor.set_setting('cooldown', 1.2)
    editor.save()
    before_latest = copy.deepcopy(editor.data)
    stale.apply_mapping_change(change(state, keyboard, .06), state)
    expected = copy.deepcopy(before_latest)
    expected['profile_options'][keyboard]['mouse']['deadzone'] = .06
    expected['mapping_revision'] += 1
    assert stale.data == expected
    assert ConfigStore(stale.root).data['profile_options'][keyboard]['mouse']['sensitivity'] == 43.


@pytest.mark.parametrize('value', [True, None, '0.07', math.nan, math.inf, -math.inf, -.01, 0., .501])
def test_invalid_recommendation_cannot_mutate_memory_or_file(pointer_store, value):
    store, state, keyboard, _ = pointer_store
    before, before_file = copy.deepcopy(store.data), store.path.read_bytes()
    with pytest.raises(ValueError, match='有限数值'):
        store.apply_mapping_change(change(state, keyboard, value), state)
    assert store.data == before
    assert store.path.read_bytes() == before_file


@pytest.mark.parametrize('invalid_state', ['disconnect', 'missing_axes', 'one_axis', 'nan', 'boolean', 'outside_unit', 'no_identity'])
def test_only_reported_finite_rs_axes_allow_applying_a_result(pointer_store, invalid_state):
    store, state, keyboard, _ = pointer_store
    payload = change(state, keyboard)
    before = copy.deepcopy(store.data)
    if invalid_state == 'disconnect':
        state = None
    elif invalid_state == 'missing_axes':
        state.pop('available_axes')
    elif invalid_state == 'one_axis':
        state['available_axes'] = [0, 1, 2, 4, 5]
    elif invalid_state == 'nan':
        state['axes'][3] = math.nan
    elif invalid_state == 'boolean':
        state['axes'][2] = True
    elif invalid_state == 'outside_unit':
        state['axes'][2] = 1.01
    else:
        state.pop('instance_id')
    with pytest.raises(ValueError, match='右摇杆双轴'):
        store.apply_mapping_change(payload, state)
    assert store.data == before


@pytest.mark.parametrize('capability', ['buttons', 'family', 'raw_model'])
def test_capability_signature_changed_since_sampling_requires_fresh_measurement(pointer_store, capability):
    store, state, keyboard, _ = pointer_store
    payload = change(state, keyboard)
    before = copy.deepcopy(store.data)
    if capability == 'buttons':
        state['available_buttons'].append(15)
    elif capability == 'family':
        state['family'] = 'dualsense'
    else:
        state.update(is_gamecontroller=False, model_key='raw:fixture-model')
    with pytest.raises(ValueError, match='重新测量'):
        store.apply_mapping_change(payload, state)
    assert store.data == before


@pytest.mark.parametrize('wrong_profile', ['native', 'foreign', 'deleted'])
def test_only_current_device_keyboard_preset_can_receive_recommendation(pointer_store, wrong_profile):
    store, state, keyboard, native = pointer_store
    if wrong_profile == 'native':
        name = native
    elif wrong_profile == 'foreign':
        second = controller('pad:second', 2)
        store.activate_controller(second)
        name = store.profiles_for(second, 'kbm')[0]
        store.activate_controller(state)
        store.save()
    else:
        name = 'Deleted preset'
    before, before_file = copy.deepcopy(store.data), store.path.read_bytes()
    with pytest.raises(ValueError):
        store.apply_mapping_change(change(state, name), state)
    assert store.data == before
    assert store.path.read_bytes() == before_file


def test_target_deleted_from_latest_file_cannot_be_resurrected(pointer_store):
    stale, state, keyboard, _ = pointer_store
    editor = ConfigStore(stale.root)
    editor.apply_mapping_change({'op': 'delete', 'profile': keyboard}, state)
    before, before_file = copy.deepcopy(stale.data), stale.path.read_bytes()
    with pytest.raises(ValueError, match='已删除'):
        stale.apply_mapping_change(change(state, keyboard), state)
    assert stale.data == before
    assert stale.path.read_bytes() == before_file


def test_actual_atomic_replace_failure_rolls_back_memory_and_baseline(pointer_store, monkeypatch):
    store, state, keyboard, _ = pointer_store
    before, baseline, before_file = copy.deepcopy(store.data), copy.deepcopy(store._baseline), store.path.read_bytes()
    original = __import__('os').replace

    def fail_replace(source, target):
        if target == store.path:
            raise OSError('Configuration replacement is unavailable')
        return original(source, target)

    monkeypatch.setattr('gamepadstudio.studio_core.os.replace', fail_replace)
    with pytest.raises(OSError):
        store.apply_mapping_change(change(state, keyboard), state)
    assert store.data == before and store._baseline == baseline
    assert store.path.read_bytes() == before_file


@pytest.mark.parametrize('guard', ['missing_scope', 'wrong_scope', 'missing_instance', 'wrong_instance', 'boolean_instance'])
def test_agent_requires_exact_scope_and_instance_for_measured_result(battery_backend, guard):
    context = battery_backend
    keyboard = context.agent.store.profiles_for(context.device.state, 'kbm')[0]
    message = request(context, keyboard)
    if guard == 'missing_scope':
        message.pop('device_scope')
    elif guard == 'wrong_scope':
        message['device_scope'] = 'pad:another'
    elif guard == 'missing_instance':
        message.pop('instance_id')
    elif guard == 'wrong_instance':
        message['instance_id'] += 1
    else:
        message['instance_id'] = True
    before, before_file = copy.deepcopy(context.agent.config), context.agent.store.path.read_bytes()
    with pytest.raises(ValueError):
        context.agent.handle(message)
    assert context.agent.config == before
    assert context.agent.store.path.read_bytes() == before_file


def test_agent_applying_inactive_preset_releases_hold_and_keeps_selection_and_pause(battery_backend):
    context = battery_backend
    native = context.agent.config['active_profile']
    keyboard = context.agent.store.profiles_for(context.device.state, 'kbm')[0]
    context.agent.store.apply_mapping_change({'op': 'binding', 'profile': native,
                                             'trigger': '0', 'mapping': entry('hold', 'E')}, context.device.state)
    context.device.state['buttons'] = [0]
    context.agent.engine.update(context.device.state, context.agent.config, enabled=True, now=1.)
    context.agent.engine.update(context.device.state, context.agent.config, enabled=True, now=1.2)
    assert context.actions.keys[ord('E')] == 1
    before = copy.deepcopy(context.agent.config)
    automatic = copy.deepcopy(context.agent.application_profile)
    enabled = context.agent.enabled
    result = context.agent.handle(request(context, keyboard))
    assert result['config']['profile_options'][keyboard]['mouse']['deadzone'] == .07
    assert context.agent.config['active_profile'] == native
    assert context.agent.config['controller_profiles'] == before['controller_profiles']
    assert context.agent.config['application_profiles'] == before['application_profiles']
    assert context.agent.application_profile == automatic
    assert context.agent.enabled == enabled
    assert not any(context.actions.keys.values())
    context.agent.engine.update(context.device.state, context.agent.config, enabled=True, now=2.)
    assert not any(context.actions.keys.values())
    context.device.state['buttons'] = []
    context.agent.engine.update(context.device.state, context.agent.config, enabled=True, now=2.2)
    context.device.state['buttons'] = [0]
    context.agent.engine.update(context.device.state, context.agent.config, enabled=True, now=3.)
    context.agent.engine.update(context.device.state, context.agent.config, enabled=True, now=3.2)
    assert context.actions.keys[ord('E')] == 1


def test_active_preset_result_reaches_real_mouse_runtime_without_resuming_pause(battery_backend):
    context = battery_backend
    keyboard = context.agent.store.profiles_for(context.device.state, 'kbm')[0]
    context.agent.handle({'command': 'mapping_change', 'device_scope': profile_scope(context.device.state),
                          'change': {'op': 'select', 'profile': keyboard}})
    context.agent.set_enabled(False)
    thread = VirtualMouseThread(context.actions)
    context.agent.engine.mouse_thread = thread
    try:
        context.agent.engine.update(context.device.state, context.agent.config, enabled=False, now=1.)
        original = thread.deadzone
        assert original != .07
        context.agent.handle(request(context, keyboard))
        context.agent.engine.update(context.device.state, context.agent.config, enabled=False, now=2.)
        assert thread.deadzone == .07
        assert not context.agent.enabled
        assert context.agent.config['mapping_enabled'] is False
        assert not context.actions.calls
    finally:
        context.agent.engine.mouse_thread = None
        thread.stop()


def test_real_socket_applies_nonactive_preset_without_displacing_automatic_profile(app, battery_backend, monkeypatch):
    context = battery_backend
    agent, state = context.agent, context.device.state
    automatic = agent.store.profiles_for(state, 'kbm')[0]
    target = 'Measured alternate'
    agent.store.apply_mapping_change({'op': 'create', 'profile': target,
                                     'source': automatic, 'mode': 'kbm'}, state)
    agent.store.apply_mapping_change({'op': 'application_profiles',
                                     'settings': settings(automatic)}, state)
    monkeypatch.setattr('gamepadstudio.agent.foreground_application',
                        lambda: foreground(GAME_A, pid=912345, hwnd=45678))
    assert agent.update_application_profile(force=True)
    assert agent.config['active_profile'] == automatic != target
    assert agent.status()['application_profile']['automatic'] is True
    agent.set_enabled(False)
    before, status = copy.deepcopy(agent.config), copy.deepcopy(agent.status())

    result = request_over_socket(app, agent.root, 'mapping_change',
                                 device_scope=profile_scope(state), instance_id=state['instance_id'],
                                 change=change(state, target, .08))

    assert result['ok'] is True
    expected = copy.deepcopy(before)
    expected['profile_options'][target].setdefault('mouse', {})['deadzone'] = .08
    expected['mapping_revision'] += 1
    assert result['config'] == expected
    assert agent.config == expected
    assert json.loads(agent.store.path.read_text(encoding='utf-8')) == expected
    reopened = ConfigStore(agent.root).data
    assert reopened['profile_options'][target] == expected['profile_options'][target]
    assert reopened['active_profile'] == automatic
    assert agent.status()['application_profile'] == status['application_profile']
    assert agent.config['controller_profiles'][profile_scope(state)] == target
    assert not agent.enabled and agent.config['mapping_enabled'] is False
    assert not context.actions.calls


def test_real_socket_rejects_old_measurement_after_same_device_reconnect(app, battery_backend):
    context = battery_backend
    agent, measured_state = context.agent, copy.deepcopy(context.device.state)
    target = agent.store.profiles_for(measured_state, 'kbm')[0]
    payload = change(measured_state, target)
    current_state = copy.deepcopy(measured_state)
    current_state['instance_id'] += 1
    context.device.state = agent.state = current_state
    assert profile_scope(current_state) == profile_scope(measured_state)
    before, before_file = copy.deepcopy(agent.config), agent.store.path.read_bytes()
    automatic = copy.deepcopy(agent.application_profile)

    result = request_over_socket(app, agent.root, 'mapping_change',
                                 device_scope=profile_scope(measured_state),
                                 instance_id=measured_state['instance_id'], change=payload)

    assert result['ok'] is False and '重新测量' in result['error']
    assert agent.config == before
    assert agent.store.path.read_bytes() == before_file
    assert agent.application_profile == automatic
    assert not context.actions.calls

"""Battery observations use current device settings, independent of mapping."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from gamepadstudio.agent import Agent
from gamepadstudio.mapping_engine import MappingRuntime
from gamepadstudio.profile_transfer import export_profile
from gamepadstudio.studio_core import ConfigStore, device_config, profile_scope
from tests.test_application_profile_backend import controller
from tests.test_unified_mapping import Actions


@pytest.fixture
def battery_backend(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    clock = {'now': 0.}
    monkeypatch.setattr('gamepadstudio.agent.time', SimpleNamespace(monotonic=lambda: clock['now']))
    monkeypatch.setattr('gamepadstudio.agent.foreground_application', lambda: None)
    monkeypatch.setattr(Agent, 'apply_device_cloaking', lambda self: None)
    monkeypatch.setattr(Agent, 'apply_gamebar_shield', lambda self: None)

    class Device:
        available = []
        def __init__(self): self.state = controller()
        def scan(self): pass
        def read(self): return self.state
        def close(self): pass

    device, actions = Device(), Actions()
    agent = Agent(tmp_path, device, actions)
    agent.timer.stop(); agent.scan_timer.stop(); agent.broadcast_timer.stop()
    agent.engine.close()
    agent.engine = MappingRuntime(actions, agent.dispatch, start_mouse=False)
    agent._user32 = SimpleNamespace(GetAsyncKeyState=lambda _: 0)
    agent._last_prtsc_down = False
    events = []
    monkeypatch.setattr(agent.server, 'broadcast', lambda event: events.append(copy.deepcopy(event)))
    agent.poll()
    yield SimpleNamespace(agent=agent, device=device, actions=actions, clock=clock, events=events)
    agent.close()


def sample(context, power, now):
    context.device.state['power'] = power
    context.clock['now'] = now
    context.agent.poll()


def battery_events(context):
    return [event for event in context.events if event.get('type') == 'battery']


def stabilize_low(context):
    sample(context, 1, 1.)
    sample(context, 1, 4.1)
    assert len(battery_events(context)) == 1
    return battery_events(context)[0]


def test_device_setting_defaults_isolated_and_stays_out_of_portable_file(tmp_path):
    store = ConfigStore(tmp_path)
    first, second = controller(), controller('pad:two', 2)
    store.activate_controller(first)
    assert store.settings_for(first)['battery_notifications_enabled'] is True
    store.set_setting('battery_notifications_enabled', False, first)
    store.save()
    store.activate_controller(second)
    assert store.settings_for(second)['battery_notifications_enabled'] is True
    assert store.settings_for(first)['battery_notifications_enabled'] is False
    loaded = ConfigStore(tmp_path)
    assert loaded.settings_for(first)['battery_notifications_enabled'] is False
    assert loaded.settings_for(second)['battery_notifications_enabled'] is True
    name = loaded.profiles_for(second, 'kbm')[0]
    assert 'battery_notifications_enabled' not in json.dumps(export_profile(loaded.data, second, name))


@pytest.mark.parametrize('bad', [None, 0, 1, 'false', [], {}])
def test_invalid_setting_is_rejected_without_mutating_any_device(tmp_path, bad):
    store = ConfigStore(tmp_path)
    state = controller()
    store.activate_controller(state)
    before = copy.deepcopy(store.data)
    with pytest.raises(ValueError, match='开启或关闭'):
        store.set_setting('battery_notifications_enabled', bad, state)
    assert store.data == before


@pytest.mark.parametrize('bad', [None, 0, 1, 'false', [], {}])
def test_corrupt_stored_setting_is_read_as_default_boolean(tmp_path, bad):
    store = ConfigStore(tmp_path)
    state = controller()
    store.activate_controller(state)
    store.data['device_settings'][profile_scope(state)]['battery_notifications_enabled'] = bad
    assert device_config(store.data, state)['battery_notifications_enabled'] is True
    assert store.settings_for(state)['battery_notifications_enabled'] is True


def test_low_event_broadcast_once_and_status_sync_does_not_replay_notice(battery_backend):
    context = battery_backend
    event = stabilize_low(context)
    assert set(event) == {'type', 'id', 'device_scope', 'instance_id', 'level', 'name'}
    assert event['type'] == 'battery' and event['level'] == 1
    assert event['device_scope'] == profile_scope(context.device.state)
    assert event['instance_id'] == context.device.state['instance_id']
    assert event['id'] and event['name'] == context.device.state['name']
    for time in (5., 8., 30.):
        sample(context, 1, time)
        assert context.agent.status()['battery_warning'] == event
        assert context.agent.handle({'command': 'status'})['battery_warning'] == event
    assert len(battery_events(context)) == 1
    log = [json.loads(row) for row in (context.agent.root / 'agent-events.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [row['id'] for row in log if row.get('type') == 'battery'] == [event['id']]
    assert not any(row.get('type') == 'notice' and '电量' in row.get('message', '') for row in context.events)


def test_battery_history_write_failure_does_not_pause_mapping_or_repeat_event(battery_backend, monkeypatch):
    context = battery_backend
    sample(context, 1, 1.)
    history = context.agent.root / 'agent-events.jsonl'
    original_open = Path.open

    def blocked_history(path, *args, **kwargs):
        if path == history:
            raise OSError('History file is unavailable')
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', blocked_history)
    enabled = context.agent.enabled
    sample(context, 1, 4.1)
    assert context.agent.enabled == enabled
    assert context.agent.config['mapping_enabled'] is True
    assert len(battery_events(context)) == 1
    sample(context, 1, 10.)
    assert len(battery_events(context)) == 1


@pytest.mark.parametrize('guard', ['paused', 'editing', 'preview'])
def test_mapping_protection_does_not_silence_hardware_warning(battery_backend, guard):
    context = battery_backend
    if guard == 'paused':
        context.agent.handle({'command': 'pause'})
    elif guard == 'editing':
        context.agent.suspended_until = 100.
    else:
        context.agent.preview_until = 100.
    enabled = context.agent.enabled
    event = stabilize_low(context)
    assert context.agent.enabled == enabled
    assert context.agent.status()['battery_warning'] == event
    assert not context.actions.calls
    if guard == 'paused':
        assert context.agent.config['mapping_enabled'] is False
        assert ConfigStore(context.agent.root).data['mapping_enabled'] is False


@pytest.mark.parametrize('power', [-1, None, True, 1.0, '1', 4])
def test_unknown_invalid_or_external_power_clears_status_without_new_notice(battery_backend, power):
    context = battery_backend
    stabilize_low(context)
    sample(context, power, 5.)
    assert context.agent.status()['battery_warning'] is None
    sample(context, power, 20.)
    assert context.agent.status()['battery_warning'] is None
    assert len(battery_events(context)) == 1


def test_disabled_setting_cancels_pending_then_enable_requires_new_stability(battery_backend):
    context = battery_backend
    sample(context, 1, 1.)
    context.agent.store.set_setting('battery_notifications_enabled', False, context.device.state)
    context.agent.store.save()
    sample(context, 1, 10.)
    assert battery_events(context) == []
    assert context.agent.status()['battery_warning'] is None
    context.agent.store.set_setting('battery_notifications_enabled', True, context.device.state)
    context.agent.store.save()
    sample(context, 1, 11.)
    sample(context, 1, 13.)
    assert battery_events(context) == []
    sample(context, 1, 14.1)
    assert len(battery_events(context)) == 1


def test_setting_reload_hides_warning_immediately_without_replaying_on_enable(battery_backend):
    context = battery_backend
    event = stabilize_low(context)
    editor = ConfigStore(context.agent.root)
    editor.set_setting('battery_notifications_enabled', False, context.device.state)
    editor.save()
    context.agent.handle({'command': 'reload'})
    assert context.agent.status()['battery_warning'] is None
    sample(context, 1, 10.)
    assert len(battery_events(context)) == 1
    editor = ConfigStore(context.agent.root)
    editor.set_setting('battery_notifications_enabled', True, context.device.state)
    editor.save()
    context.agent.handle({'command': 'reload'})
    sample(context, 1, 14.)
    assert context.agent.status()['battery_warning']['id'] == event['id']
    assert len(battery_events(context)) == 1


def test_current_physical_device_owns_its_setting_and_warning(battery_backend):
    context = battery_backend
    first = copy.deepcopy(context.device.state)
    context.agent.store.set_setting('battery_notifications_enabled', False, first)
    context.agent.store.save()
    sample(context, 1, 1.)
    sample(context, 1, 5.)
    assert battery_events(context) == []
    second = controller('pad:two', 2)
    second['name'] = 'Second fixture'
    context.device.state = second
    sample(context, 1, 6.)
    sample(context, 1, 9.1)
    assert len(battery_events(context)) == 1
    event = battery_events(context)[0]
    assert event['device_scope'] == profile_scope(second)
    assert event['instance_id'] == 2 and event['name'] == 'Second fixture'
    context.device.state = first
    sample(context, 1, 10.)
    assert context.agent.status()['battery_warning'] is None
    assert len(battery_events(context)) == 1


def test_disconnect_clears_pending_and_same_device_reconnect_does_not_duplicate_episode(battery_backend):
    context = battery_backend
    original = copy.deepcopy(context.device.state)
    sample(context, 1, 1.)
    context.device.state = None
    context.clock['now'] = 2.
    context.agent.poll()
    assert context.agent.status()['battery_warning'] is None
    context.device.state = copy.deepcopy(original)
    context.device.state['instance_id'] += 1
    sample(context, 1, 3.)
    sample(context, 1, 5.)
    assert battery_events(context) == []
    sample(context, 1, 6.1)
    assert len(battery_events(context)) == 1
    context.device.state = None
    context.clock['now'] = 7.
    context.agent.poll()
    assert context.agent.status()['battery_warning'] is None
    context.device.state = copy.deepcopy(original)
    context.device.state['instance_id'] += 2
    sample(context, 1, 8.)
    sample(context, 1, 12.)
    assert len(battery_events(context)) == 1
    warning = context.agent.status()['battery_warning']
    assert warning['device_scope'] == profile_scope(original)
    assert warning['instance_id'] == context.device.state['instance_id']


def test_critical_upgrade_and_sustained_recovery_each_allow_one_new_event(battery_backend):
    context = battery_backend
    stabilize_low(context)
    sample(context, 0, 5.)
    sample(context, 0, 6.1)
    assert [event['level'] for event in battery_events(context)] == [1, 0]
    sample(context, 1, 10.)
    assert len(battery_events(context)) == 2
    sample(context, 2, 11.)
    sample(context, 2, 21.1)
    assert context.agent.status()['battery_warning'] is None
    sample(context, 1, 22.)
    sample(context, 1, 25.1)
    assert [event['level'] for event in battery_events(context)] == [1, 0, 1]
    assert len({event['id'] for event in battery_events(context)}) == 3

"""Reported battery levels debounce, deduplicate and recover per physical pad."""
import uuid

import pytest

from gamepadstudio.battery_monitor import (
    BatteryMonitor, MAX_EPISODES, battery_reported, normalize_power,
)
from gamepadstudio.studio_core import profile_scope


def pad(power=1, key='pad:one', instance=1, name='DualSense'):
    return {'device_key': key, 'instance_id': instance, 'name': name,
            'family': 'ds5', 'power': power}


@pytest.mark.parametrize('power', range(5))
def test_only_reported_integer_levels_are_valid(power):
    assert normalize_power(pad(power)) == power
    assert battery_reported(pad(power)) is (power < 4)


@pytest.mark.parametrize('power', [None, -1, 5, 99, True, False, 0., 1.,
                                  float('nan'), float('inf'), '1', [], {}])
def test_unknown_invented_percentages_and_coerced_levels_are_not_battery_data(power):
    assert normalize_power(pad(power)) is None
    assert battery_reported(pad(power)) is False


@pytest.mark.parametrize('state', [None, {}, [], 1, 'pad'])
def test_absent_or_invalid_state_has_no_reported_battery(state):
    assert normalize_power(state) is None
    assert battery_reported(state) is False
    monitor = BatteryMonitor()
    assert monitor.sample(state, 0.) is None
    assert monitor.warning(state) is None


def test_low_level_needs_three_continuous_seconds_and_has_stable_warning_id():
    monitor, state = BatteryMonitor(), pad()
    assert monitor.sample(state, 0.) is None
    assert monitor.sample(state, 2.999) is None
    assert monitor.warning(state) is None
    event = monitor.sample(state, 3.)
    assert event == {'type': 'battery', 'id': event['id'],
                     'device_scope': profile_scope(state), 'instance_id': 1,
                     'level': 1, 'name': 'DualSense'}
    uuid.UUID(event['id'].split(':')[0])
    assert event['id'].endswith(':1')
    assert monitor.warning(state) == event
    event['level'] = 0
    assert monitor.warning(state)['level'] == 1
    for now in (3.1, 10., 100.):
        assert monitor.sample(state, now) is None


def test_critical_needs_one_second_and_does_not_later_replay_low_notice():
    monitor, state = BatteryMonitor(), pad(0)
    assert monitor.sample(state, 0.) is None
    assert monitor.sample(state, .999) is None
    assert monitor.warning(state) is None
    event = monitor.sample(state, 1.)
    assert event['level'] == 0
    assert monitor.warning(state) == event
    state['power'] = 1
    for now in (2., 5., 10.):
        assert monitor.sample(state, now) is None
    assert monitor.warning(state) == dict(event, level=1)


def test_low_to_critical_warning_waits_for_stable_upgrade_and_notifies_only_once():
    monitor, state = BatteryMonitor(), pad()
    monitor.sample(state, 0.)
    low = monitor.sample(state, 3.)
    state['power'] = 0
    assert monitor.warning(state) is None
    assert monitor.sample(state, 3.1) is None
    assert monitor.sample(state, 4.099) is None
    assert monitor.warning(state) is None
    critical = monitor.sample(state, 4.1)
    assert critical['level'] == 0
    assert critical['id'] != low['id']
    assert critical['id'].endswith(':2')
    assert monitor.warning(state) == critical
    for now, power in ((5., 1), (8., 1), (9., 0), (10., 0), (20., 1)):
        state['power'] = power
        assert monitor.sample(state, now) is None
        assert monitor.warning(state)['id'] == critical['id']


def test_unstable_low_critical_jitter_cannot_borrow_other_levels_timer():
    monitor, state = BatteryMonitor(), pad()
    for now, power in ((0., 1), (2.9, 0), (3.8, 1), (6.7, 0), (7.6, 1)):
        state['power'] = power
        assert monitor.sample(state, now) is None
        assert monitor.warning(state) is None
    low = monitor.sample(state, 10.6)
    assert low['level'] == 1
    for now, power in ((11., 0), (11.9, 1), (12., 0), (12.9, 1)):
        state['power'] = power
        assert monitor.sample(state, now) is None
    state['power'] = 0
    assert monitor.sample(state, 13.) is None
    assert monitor.sample(state, 14.)['level'] == 0


@pytest.mark.parametrize('interruption', [None, {}, pad(None), pad(4), pad(2)])
def test_missing_or_non_low_sample_breaks_pending_confirmation(interruption):
    monitor, state = BatteryMonitor(), pad()
    monitor.sample(state, 0.)
    assert monitor.sample(interruption, 2.9) is None
    assert monitor.sample(state, 3.) is None
    assert monitor.sample(state, 5.999) is None
    assert monitor.sample(state, 6.)['level'] == 1


def test_replacement_connection_instance_cannot_inherit_pending_timer():
    monitor = BatteryMonitor()
    monitor.sample(pad(0), 0.)
    replacement = pad(0, instance=2)
    assert monitor.sample(replacement, .9) is None
    assert monitor.sample(replacement, 1.899) is None
    event = monitor.sample(replacement, 1.9)
    assert event['instance_id'] == 2 and event['level'] == 0


def test_reconnecting_same_physical_device_keeps_dedup_and_current_warning_metadata():
    monitor, state = BatteryMonitor(), pad()
    monitor.sample(state, 0.)
    event = monitor.sample(state, 3.)
    monitor.sample(None, 4.)
    assert monitor.warning(None) is None
    replacement = pad(instance=9, name='重新连接的手柄')
    assert monitor.sample(replacement, 5.) is None
    assert monitor.sample(replacement, 8.) is None
    assert monitor.warning(replacement) == dict(event, instance_id=9, name='重新连接的手柄')


def test_two_devices_have_independent_episodes_and_switch_clears_pending():
    monitor, first, second = BatteryMonitor(), pad(), pad(key='pad:two', instance=2)
    monitor.sample(first, 0.)
    monitor.sample(second, 2.)
    assert monitor.sample(first, 2.9) is None
    assert monitor.sample(first, 5.899) is None
    first_event = monitor.sample(first, 5.9)
    assert first_event['device_scope'] == 'pad:one'
    assert monitor.warning(second) is None
    monitor.sample(second, 6.)
    second_event = monitor.sample(second, 9.)
    assert second_event['device_scope'] == 'pad:two'
    assert second_event['id'] != first_event['id']
    assert monitor.sample(first, 10.) is None
    assert monitor.warning(first) == first_event


@pytest.mark.parametrize('disabled', [False, 0, 1, None, 'true'])
def test_disabled_or_non_boolean_enabled_clears_pending_without_forgetting_notice(disabled):
    monitor, state = BatteryMonitor(), pad()
    monitor.sample(state, 0.)
    assert monitor.sample(state, 2.9, enabled=disabled) is None
    assert monitor.warning(state, enabled=disabled) is None
    assert monitor.sample(state, 3.) is None
    assert monitor.sample(state, 5.999) is None
    event = monitor.sample(state, 6.)
    monitor.sample(state, 7., enabled=disabled)
    assert monitor.warning(state, enabled=disabled) is None
    assert monitor.sample(state, 10.) is None
    assert monitor.sample(state, 20.) is None
    assert monitor.warning(state) == event


def test_ten_continuous_seconds_of_healthy_or_external_power_rearms_episode():
    monitor, state = BatteryMonitor(), pad(0)
    monitor.sample(state, 0.)
    first = monitor.sample(state, 1.)
    for now, power in ((2., 2), (5., 3), (11.999, 4), (12., 4)):
        state['power'] = power
        assert monitor.sample(state, now) is None
        assert monitor.warning(state) is None
    state['power'] = 1
    assert monitor.sample(state, 13.) is None
    second = monitor.sample(state, 16.)
    assert second['level'] == 1 and second['id'] != first['id']


def test_less_than_ten_seconds_of_recovery_does_not_rearm():
    monitor, state = BatteryMonitor(), pad(0)
    monitor.sample(state, 0.)
    event = monitor.sample(state, 1.)
    state['power'] = 4
    monitor.sample(state, 2.)
    monitor.sample(state, 11.999)
    state['power'] = 0
    assert monitor.sample(state, 12.) is None
    assert monitor.sample(state, 13.) is None
    assert monitor.warning(state) == event


@pytest.mark.parametrize('kind', ['unknown', 'disconnect', 'instance', 'device', 'disabled'])
def test_interrupted_recovery_cannot_rearm_old_alerted_episode(kind):
    monitor, state = BatteryMonitor(), pad(0)
    monitor.sample(state, 0.)
    event = monitor.sample(state, 1.)
    healthy = dict(state, power=3)
    monitor.sample(healthy, 2.)
    if kind == 'unknown':
        monitor.sample(dict(state, power=None), 11.)
    elif kind == 'disconnect':
        monitor.sample(None, 11.)
    elif kind == 'instance':
        healthy['instance_id'] = state['instance_id'] = 2
        monitor.sample(healthy, 11.)
    elif kind == 'device':
        monitor.sample(pad(3, key='pad:two'), 11.)
    else:
        monitor.sample(healthy, 11., enabled=False)
    monitor.sample(healthy, 12.)
    state['power'] = 0
    assert monitor.sample(state, 13.) is None
    assert monitor.sample(state, 14.) is None
    assert monitor.warning(state)['id'] == event['id']


@pytest.mark.parametrize('power', [None, True, False, -1, 2, 3, 4, 5, 1., '0'])
def test_warning_disappears_as_soon_as_current_level_is_unavailable_or_not_low(power):
    monitor, state = BatteryMonitor(), pad(0)
    monitor.sample(state, 0.)
    monitor.sample(state, 1.)
    state['power'] = power
    assert monitor.warning(state) is None


@pytest.mark.parametrize('now', [None, True, False, '3', float('nan'), float('inf'),
                                float('-inf'), 10 ** 500])
def test_invalid_clock_cannot_confirm_warning_and_breaks_pending(now):
    monitor, state = BatteryMonitor(), pad()
    monitor.sample(state, 0.)
    assert monitor.sample(state, now) is None
    assert monitor.sample(state, 3.) is None
    assert monitor.sample(state, 6.)['level'] == 1


def test_backward_clock_restarts_confirmation_and_preserves_dedup():
    monitor, state = BatteryMonitor(), pad()
    monitor.sample(state, 10.)
    assert monitor.sample(state, 9.) is None
    assert monitor.sample(state, 11.999) is None
    event = monitor.sample(state, 12.)
    assert event['level'] == 1
    assert monitor.sample(state, 0.) is None
    assert monitor.sample(state, 3.) is None
    assert monitor.warning(state) == event


@pytest.mark.parametrize('name', [None, '', '  ', 1, False])
def test_missing_display_name_uses_controller_label(name):
    monitor, state = BatteryMonitor(), pad(0, name=name)
    monitor.sample(state, 0.)
    assert monitor.sample(state, 1.)['name'] == '手柄'


def test_event_ids_are_unique_across_monitor_sessions():
    first, second, state = BatteryMonitor(), BatteryMonitor(), pad(0)
    first.sample(state, 0.)
    second.sample(state, 0.)
    assert first.sample(state, 1.)['id'] != second.sample(state, 1.)['id']


def test_lru_is_bounded_and_preserves_recent_device_episode():
    monitor, state = BatteryMonitor(), pad(0)
    monitor.sample(state, 0.)
    recent = monitor.sample(state, 1.)
    for number in range(MAX_EPISODES):
        monitor.sample(pad(3, key=f'pad:{number + 2}'), float(number + 2))
        # Keep this physically selected pad's episode recent in the bounded cache.
        monitor.sample(state, float(number + 2) + .1)
    assert len(monitor._episodes) == MAX_EPISODES
    assert monitor.warning(state) == recent
    assert 'pad:2' not in monitor._episodes
    evicted = pad(0, key='pad:2')
    monitor.sample(evicted, 200.)
    assert monitor.sample(evicted, 201.)['level'] == 0

"""Numerical and time boundaries of fresh right-stick rest measurements."""
import math

import pytest

from gamepadstudio.stick_calibration import (
    DEADZONE_MARGIN, MAX_DEADZONE, MAX_SAMPLES, MIN_DEADZONE,
    StickCalibration, supports_right_stick,
)


def pad(x=0., y=0., key='pad:one', instance=1):
    return {'device_key': key, 'instance_id': instance,
            'available_axes': list(range(6)), 'axes': [0., 0., x, y, 0., 0.]}


def measure(points=None, *, rate=100):
    points = points or (lambda index: (0., 0.))
    session = StickCalibration()
    session.start(pad(), 0.)
    for index in range(1, int(4. * rate) + 1):
        x, y = points(index)
        result = session.sample(pad(x, y), index / rate)
        if result['phase'] in ('complete', 'failed'):
            return session, result
    raise AssertionError('measurement should have reached a terminal result')


@pytest.mark.parametrize('available', [[2, 3], [0, 1, 2, 3, 4, 5], (2, 3), {2, 3}])
def test_capability_requires_real_both_right_stick_axis_declarations(available):
    state = pad(.01, -.02)
    state['available_axes'] = available
    assert supports_right_stick(state) is True


@pytest.mark.parametrize('available', [None, [], [0, 1], [2], [3], [2., 3.],
                                      ['2', '3'], [True, 3], '2,3', {2: True, 3: True}])
def test_capability_never_infers_axes_from_zero_padding_or_coerced_indices(available):
    state = pad()
    if available is None:
        state.pop('available_axes')
    else:
        state['available_axes'] = available
    assert supports_right_stick(state) is False


@pytest.mark.parametrize('value', [None, True, False, '0', float('nan'), float('inf'),
                                  float('-inf'), 1.001, -1.001, 10 ** 500])
@pytest.mark.parametrize('axis', [2, 3])
def test_capability_rejects_non_finite_non_numeric_or_out_of_range_axis(axis, value):
    state = pad()
    state['axes'][axis] = value
    assert supports_right_stick(state) is False


@pytest.mark.parametrize('instance', [None, True, False, 1., '1', -1])
def test_capability_requires_current_integer_connection_instance(instance):
    state = pad()
    state['instance_id'] = instance
    assert supports_right_stick(state) is False


@pytest.mark.parametrize('state', [None, {}, [], {'axes': [0.] * 6},
                                  dict(pad(), axes=[0., 0., 0.]),
                                  dict(pad(), axes={'2': 0., '3': 0.})])
def test_missing_state_or_short_axis_array_is_unavailable(state):
    assert supports_right_stick(state) is False
    status = StickCalibration().start(state, 0.)
    assert status['phase'] == 'failed' and status['result'] is None


def test_unrelated_axis_values_do_not_disable_actual_right_stick():
    state = pad(-1., 1., instance=0)
    state['axes'][0] = float('nan')
    state['is_gamecontroller'] = False
    assert supports_right_stick(state) is True


def test_idle_session_requires_explicit_start_and_start_is_not_measurement_data():
    session = StickCalibration()
    assert session.status() == {'phase': 'idle', 'progress': 0., 'samples': 0,
                                'result': None, 'error': ''}
    assert session.sample(pad(), 10.) == session.tick(10.) == session.status()
    status = session.start(pad(), 0.)
    assert status['phase'] == 'settling' and status['samples'] == 0
    assert session.sample(pad(), 0.)['samples'] == 0


def test_settling_allows_release_and_only_counts_post_settle_fresh_samples():
    session = StickCalibration()
    session.start(pad(.9, 0.), 0.)
    for index in range(1, 75):
        status = session.sample(pad(.9 - .9 * index / 74, 0.), index / 100)
        assert status['phase'] == 'settling' and status['samples'] == 0
    status = session.sample(pad(), .75)
    assert status['phase'] == 'sampling' and status['samples'] == 1
    assert status['progress'] == pytest.approx(.2)
    for index in range(76, 376):
        status = session.sample(pad(), index / 100)
    assert status['phase'] == 'complete'
    assert status['samples'] == 301


@pytest.mark.parametrize('point', [(0., 0.), (.03, .04), (-.03, -.04), (.09, -.12)])
def test_constant_rest_offset_has_exact_radial_metrics_and_conservative_percent_deadzone(point):
    _, status = measure(lambda _: point)
    result = status['result']
    radius = math.hypot(*point)
    assert status['phase'] == 'complete' and status['error'] == ''
    assert result['device_scope'] == 'pad:one' and result['instance_id'] == 1
    assert result['offset_x'] == pytest.approx(point[0])
    assert result['offset_y'] == pytest.approx(point[1])
    assert result['offset'] == pytest.approx(radius)
    assert result['peak'] == pytest.approx(radius)
    assert result['noise'] == pytest.approx(0., abs=1e-15)
    recommendation = result['recommended_deadzone']
    assert MIN_DEADZONE <= recommendation <= MAX_DEADZONE
    assert recommendation * 100 == pytest.approx(round(recommendation * 100))
    assert recommendation + 1e-12 >= radius + DEADZONE_MARGIN


def test_deadzone_uses_radial_peak_with_margin_and_rounds_up_rather_than_down():
    _, status = measure(lambda index: (.03 + (.003 if index % 2 else -.003),
                                       .04 + (.003 if index % 2 else -.003)))
    result = status['result']
    assert result['peak'] == pytest.approx(math.hypot(.033, .043))
    assert .004 < result['noise'] < .005
    assert result['recommended_deadzone'] == .08
    assert result['recommended_deadzone'] > result['offset'] + DEADZONE_MARGIN


@pytest.mark.parametrize('rate', [25, 50, 100, 250])
def test_recommendation_does_not_change_with_valid_sampling_frequency(rate):
    _, status = measure(lambda _: (.026, -.033), rate=rate)
    assert status['phase'] == 'complete'
    assert status['samples'] >= 60
    assert status['result']['recommended_deadzone'] == .07


@pytest.mark.parametrize(('count', 'phase'), [(59, 'failed'), (60, 'complete')])
def test_exact_minimum_sample_count_is_enforced_after_full_measurement_duration(count, phase):
    session = StickCalibration()
    session.start(pad(), 0.)
    for index in range(1, 15):
        session.sample(pad(), index / 20)
    for index in range(count):
        status = session.sample(pad(), .75 + 3. * index / (count - 1))
    assert status['phase'] == phase and status['samples'] == count
    if phase == 'failed':
        assert status['result'] is None and '样本不足' in status['error']


def test_duplicate_sample_times_do_not_manufacture_required_sample_count():
    session = StickCalibration()
    session.start(pad(), 0.)
    for index in range(1, 15):
        session.sample(pad(), index / 20)
    for index in range(31):
        for _ in range(10):
            status = session.sample(pad(), .75 + index / 10)
    assert status['phase'] == 'failed' and status['samples'] == 31
    assert '样本不足' in status['error']


def test_tick_never_adds_samples_or_completes_even_when_duration_is_reached():
    session = StickCalibration()
    session.start(pad(), 0.)
    for index in range(1, 75):
        session.sample(pad(), index / 20)
    before = session.status()
    status = session.tick(3.75)
    assert status['phase'] == 'sampling' and status['progress'] == 1.
    assert status['samples'] == before['samples'] and status['result'] is None
    assert session.sample(pad(), 3.75)['phase'] == 'complete'


def test_exact_maximum_gap_is_allowed_but_tick_detects_input_stall():
    session = StickCalibration()
    session.start(pad(), 0.)
    assert session.tick(.15)['phase'] == 'settling'
    assert session.sample(pad(), .15)['phase'] == 'settling'
    assert session.tick(.300000001)['phase'] == 'settling'
    status = session.tick(.301)
    assert status['phase'] == 'failed' and '新数据' in status['error']


def test_late_sample_cannot_repair_a_gap_without_prior_tick():
    session = StickCalibration()
    session.start(pad(), 0.)
    status = session.sample(pad(), .151)
    assert status['phase'] == 'failed' and status['result'] is None


@pytest.mark.parametrize('replacement', [None, {}, pad(key='pad:two'), pad(instance=2),
                                        dict(pad(), available_axes=[0, 1]),
                                        pad(float('nan'), 0.), dict(pad(), instance_id=True)])
def test_disconnect_identity_change_or_invalid_axis_permanently_invalidates_measurement(replacement):
    session = StickCalibration()
    session.start(pad(), 0.)
    status = session.sample(replacement, .01)
    assert status['phase'] == 'failed' and status['result'] is None and status['error']
    for index in range(2, 401):
        assert session.sample(pad(), index / 100) == status


@pytest.mark.parametrize('point', [(.36, 0.), (0., -.36), (.3, .2)])
def test_obvious_deflection_is_rejected_as_soon_as_measurement_starts(point):
    _, status = measure(lambda _: point)
    assert status['phase'] == 'failed' and '移动' in status['error']
    assert status['result'] is None


def test_moving_from_initial_measured_position_is_rejected_without_using_new_position_as_center():
    _, status = measure(lambda index: (.01 if index < 120 else .071, 0.))
    assert status['phase'] == 'failed' and '移动' in status['error']


@pytest.mark.parametrize('offset', [.151, .25])
def test_large_stable_offset_remains_diagnostic_but_has_no_applicable_deadzone(offset):
    _, status = measure(lambda _: (offset, 0.))
    assert status['phase'] == 'complete' and '偏移过大' in status['error']
    assert status['result']['offset'] == pytest.approx(offset)
    assert status['result']['noise'] == pytest.approx(0., abs=1e-15)
    assert status['result']['recommended_deadzone'] is None


def test_small_mean_but_excessive_noise_has_no_recommendation():
    _, status = measure(lambda index: (.02 + (.018 if index % 2 else -.018), 0.))
    assert status['phase'] == 'complete' and '波动较大' in status['error']
    assert status['result']['offset'] == pytest.approx(.02, abs=.0001)
    assert status['result']['noise'] > .017
    assert status['result']['recommended_deadzone'] is None


def test_required_deadzone_over_safe_limit_is_rejected_instead_of_clamped():
    _, status = measure(lambda index: (.199 if index == 120 else .149, 0.))
    assert status['phase'] == 'complete'
    assert status['result']['offset'] < .15
    assert status['result']['peak'] == .199
    assert status['result']['recommended_deadzone'] is None


@pytest.mark.parametrize('now', [None, True, '0', float('nan'), float('inf'), 10 ** 500])
def test_invalid_start_clock_cannot_create_an_active_measurement(now):
    status = StickCalibration().start(pad(), now)
    assert status['phase'] == 'failed' and '时间异常' in status['error']


@pytest.mark.parametrize('method', ['sample', 'tick'])
@pytest.mark.parametrize('now', [-.001, None, True, float('nan'), float('inf')])
def test_invalid_or_backward_live_clock_is_terminal(method, now):
    session = StickCalibration()
    session.start(pad(), 0.)
    status = session.sample(pad(), now) if method == 'sample' else session.tick(now)
    assert status['phase'] == 'failed' and '时间异常' in status['error']


def test_excessive_sampling_is_bounded_without_silently_dropping_measured_data():
    session = StickCalibration()
    session.start(pad(), 0.)
    for index in range(1, 75):
        session.sample(pad(), index / 100)
    for index in range(MAX_SAMPLES + 1):
        status = session.sample(pad(), .75 + index / 2000)
    assert status['phase'] == 'failed' and '频率异常' in status['error']
    assert status['samples'] == MAX_SAMPLES and status['result'] is None


def test_successful_result_is_immutable_for_later_samples_ticks_and_returned_dictionary_edits():
    session, status = measure(lambda _: (.03, .04))
    saved = session.status()
    status['result']['recommended_deadzone'] = .5
    session.result['offset_x'] = 1.
    for state, now in ((None, 100.), (pad(key='pad:two'), 101.), (pad(.8, .8), 102.)):
        assert session.sample(state, now) == session.tick(now) == saved
    assert session.status() == saved


def test_restart_clears_terminal_result_and_binds_new_device():
    session, _ = measure()
    other = pad(.02, 0., key='pad:two', instance=9)
    assert session.start(other, 100.) == {
        'phase': 'settling', 'progress': 0., 'samples': 0, 'result': None, 'error': ''}
    for index in range(1, 376):
        status = session.sample(other, 100. + index / 100)
    assert status['phase'] == 'complete'
    assert status['result']['device_scope'] == 'pad:two'
    assert status['result']['instance_id'] == 9

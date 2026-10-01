"""Curve behavior, hardware capabilities and safe changes to held inputs."""
from types import SimpleNamespace

import pytest

from gamepadstudio.device import Device, input_backend_for_guid
from gamepadstudio.mapping_engine import InputNormalizer
from gamepadstudio.response_curves import (curve_capabilities, curve_preset,
    evaluate_curve, normalize_curve)
from gamepadstudio.studio_core import ConfigStore, device_config
from tests.test_unified_mapping import entry, frame, runtime


@pytest.mark.parametrize('name', ['linear', 'sensitive', 'precise'])
def test_presets_are_monotonic_with_silent_zero_and_full_scale(name):
    spec = curve_preset(name)
    values = [evaluate_curve(index / 100, spec) for index in range(101)]
    assert values[0] == 0 and values[-1] == 1
    assert values == sorted(values)
    assert evaluate_curve(.5, spec) == spec['points'][2]


@pytest.mark.parametrize('spec', [None, [], 'broken', {'points': [1]},
    {'points': [float('nan'), float('inf'), 'bad', None, True]},
    {'points': [-1, .8, .2, .3, 2], 'deadzone': .8, 'saturation': .5},
    {'deadzone': float('nan'), 'saturation': False},
    {'deadzone': .99, 'saturation': .995}])
def test_invalid_curve_input_stays_finite_monotonic_and_cannot_rumble_at_zero(spec):
    curve = normalize_curve(spec)
    assert curve['points'][0] == 0 and curve['points'][-1] == 1
    assert curve['points'] == sorted(curve['points'])
    assert 0 <= curve['deadzone'] < curve['saturation'] <= 1
    assert evaluate_curve(0, curve) == 0
    assert evaluate_curve(1, curve) == 1
    for invalid in (None, 'bad', float('nan'), float('inf'), 10 ** 400, True):
        assert evaluate_curve(invalid, curve) == 0


def test_deadzone_saturation_and_interpolation_have_known_output_values():
    spec = {'points': [0, .1, .4, .7, 1], 'deadzone': .1, 'saturation': .9}
    assert evaluate_curve(.1, spec) == 0
    assert evaluate_curve(.3, spec) == pytest.approx(.1)
    assert evaluate_curve(.4, spec) == pytest.approx(.25)
    assert evaluate_curve(.9, spec) == 1
    assert evaluate_curve(-2, spec) == 0
    assert evaluate_curve(2, spec) == 1


@pytest.mark.parametrize(('state', 'axes'), [
    (None, []), ({'family': 'generic', 'available_axes': list(range(6))}, []),
    ({'family': 'switch', 'available_axes': list(range(6))}, []),
    ({'family': 'xbox', 'is_gamecontroller': False, 'num_axes': 6}, []),
    ({'family': 'xbox', 'available_axes': [0, 1, 4]}, [4]),
    ({'family': 'dualshock4', 'available_axes': list(range(6))}, [4, 5]),
    ({'family': 'dualsense', 'axes': [0] * 5}, [4]),
    ({'family': 'generic', 'analog_trigger_axes': [4, 5], 'available_axes': [5]}, [5]),
    ({'family': 'generic', 'input_backend': 'xinput', 'available_axes': [4, 5]}, [4, 5]),
    ({'family': 'switch', 'input_backend': 'xinput', 'available_axes': [4, 5]}, [4, 5]),
    ({'family': 'switch', 'input_backend': 'hidapi', 'available_axes': [4, 5]}, []),
    ({'family': 'switch', 'input_backend': 'xinput', 'available_axes': [4, 5],
      'trigger_axis_bindings': [4]}, [4]),
])
def test_trigger_curves_need_analog_evidence_and_available_axes(state, axes):
    assert curve_capabilities(state)['trigger_axes'] == axes


@pytest.mark.parametrize(('guid', 'backend'), [
    ('00' * 14 + '7801', 'xinput'), ('00' * 14 + '7802', ''),
    ('00' * 14 + '6801', ''), ('broken', ''), ('00' * 17, ''),
    (bytes(14) + b'x\x01', 'xinput')])
def test_only_sdl_xinput_gamepad_driver_signature_proves_backend(guid, backend):
    assert input_backend_for_guid(guid) == backend


def test_rumble_capabilities_are_independently_reported_and_offline_disabled():
    assert curve_capabilities({'rumble': True})['rumble'] is True
    assert curve_capabilities({'rumble': True})['trigger_rumble'] is False
    assert curve_capabilities({'rumble': False, 'trigger_rumble': True})['trigger_rumble'] is True
    assert curve_capabilities({'is_gamecontroller': False, 'trigger_rumble': True})['trigger_rumble'] is False
    assert not any(curve_capabilities({'connected': False, 'rumble': True}).values())


def fake_device(raw=False):
    device = Device.__new__(Device)
    device.handle = 1
    device.instance_id = 1
    device.metadata = {'rumble': True, 'trigger_rumble': True}
    device.is_raw_joystick = raw
    calls = []
    device.lib = SimpleNamespace(
        SDL_GameControllerRumble=lambda *args: calls.append(('motors', args)) or 0,
        SDL_JoystickRumble=lambda *args: calls.append(('raw', args)) or 0,
        SDL_GameControllerRumbleTriggers=lambda *args: calls.append(('triggers', args)) or 0)
    device.set_response_curves({})
    return device, calls


@pytest.mark.parametrize('raw', [False, True])
def test_device_applies_independent_motor_curves_once_to_all_entry_points(raw):
    device, calls = fake_device(raw)
    device.set_response_curves({'rumble_curves': {'low': curve_preset('sensitive'),
                                               'high': curve_preset('precise')}})
    assert device.rumble_ext(.5, .5, 250)
    assert calls[-1][1][1:3] == (int(.68 * 65535), int(.32 * 65535))
    assert device.rumble(.5)
    assert calls[-1][1][1] == int(.68 * 65535)
    assert calls[-1][1][2] == int(evaluate_curve(.325, curve_preset('precise')) * 65535)
    assert device.rumble_ext(0, 0, 250)
    assert calls[-1][1][1:3] == (0, 0)


def test_device_trigger_outputs_require_capability_and_use_channel_curves():
    device, calls = fake_device()
    device.set_response_curves({'trigger_rumble_curves': {'left': curve_preset('precise')}})
    assert device.rumble_triggers(.5, .5, 250)
    assert calls[-1][1] == (1, int(.32 * 65535), int(.5 * 65535), 250)
    device.metadata['trigger_rumble'] = False
    assert device.rumble_triggers(.5, .5, 250) is False
    assert len(calls) == 1
    device.metadata['trigger_rumble'] = True
    device.is_raw_joystick = True
    assert device.rumble_triggers(.5, .5, 250) is False


def test_replacing_device_settings_resets_missing_curve_channels():
    device, calls = fake_device()
    device.set_response_curves({'rumble_curves': {'low': curve_preset('sensitive')}})
    device.rumble_ext(.5, .5, 250)
    assert calls[-1][1][1] == int(.68 * 65535)
    device.set_response_curves({})
    device.rumble_ext(.5, .5, 250)
    assert calls[-1][1][1] == int(.5 * 65535)


def test_input_curve_precedes_trigger_press_and_release_thresholds():
    normalizer = InputNormalizer()
    settings = {'trigger_press': .55, 'trigger_release': .35,
                'trigger_curves': {'left': curve_preset('sensitive'), 'right': curve_preset('precise')}}
    assert normalizer.update(dict(frame(axes=[0, 0, 0, 0, .5, .5]), family='xbox'), settings) == {'LT'}
    assert normalizer.update(dict(frame(axes=[0, 0, 0, 0, .25, .5]), family='xbox'), settings) == {'LT'}
    assert not normalizer.update(dict(frame(axes=[0, 0, 0, 0, .2, .5]), family='xbox'), settings)
    assert not normalizer.update(dict(frame(), family='xbox'), settings)


@pytest.mark.parametrize('state', [{'family': 'switch'}, {'family': 'generic'},
                                  {'family': 'xbox', 'is_gamecontroller': False}])
def test_input_normalizer_ignores_analog_curves_without_hardware_evidence(state):
    normalizer = InputNormalizer()
    settings = {'trigger_curves': {'left': curve_preset('sensitive')}}
    assert 'LT' not in normalizer.update(dict(frame(axes=[0, 0, 0, 0, .5, 0]), **state), settings)


def xbox_frame(value=0., identity='pad:A'):
    return dict(frame(axes=[0, 0, 0, 0, value, 0]), family='xbox',
                device_key=identity, available_axes=list(range(6)))


def test_curve_edit_releases_held_output_and_blocks_until_raw_neutral():
    engine, config, actions, _ = runtime({'LT': entry('mouse_hold', 'left')})
    config['device_settings'] = {'pad:A': {'trigger_curves': {'left': curve_preset('sensitive')}}}
    engine.update(xbox_frame(.5), config, now=0)
    assert actions.mouse == {'left'}
    config['device_settings']['pad:A']['trigger_curves']['left'] = curve_preset('precise')
    engine.update(xbox_frame(.5), config, now=.1)
    assert not actions.mouse
    # Deflection dropped below the new threshold; increasing the same held
    # trigger must not become a fresh press before its physical release.
    engine.update(xbox_frame(.95), config, now=.2)
    assert not actions.mouse
    engine.update(xbox_frame(), config, now=.3)
    engine.update(xbox_frame(.95), config, now=.4)
    assert actions.mouse == {'left'}


def test_lower_curve_threshold_does_not_fire_an_existing_partial_press():
    engine, config, actions, _ = runtime({'LT': entry('mouse_hold', 'left')})
    engine.update(xbox_frame(.5), config, now=0)
    assert not actions.mouse
    config['device_settings'] = {'pad:A': {'trigger_curves': {'left': curve_preset('sensitive')}}}
    engine.update(xbox_frame(.5), config, now=.1)
    assert not actions.mouse
    engine.update(xbox_frame(), config, now=.2)
    engine.update(xbox_frame(.5), config, now=.3)
    assert actions.mouse == {'left'}


@pytest.mark.parametrize('family', ['generic', 'switch'])
def test_runtime_ignores_saved_analog_curves_for_digital_or_unknown_devices(family):
    engine, config, actions, _ = runtime({'LT': entry('mouse_hold', 'left')})
    config['device_settings'] = {'pad:A': {'trigger_curves': {'left': curve_preset('sensitive')}}}
    state = xbox_frame(.5)
    state['family'] = family
    engine.update(state, config, now=0)
    assert not actions.mouse


def test_curve_settings_stay_device_scoped_and_survive_save(tmp_path):
    store = ConfigStore(tmp_path)
    first, second = xbox_frame(identity='pad:A'), xbox_frame(identity='pad:B')
    store.set_setting('trigger_curves', {'left': curve_preset('sensitive')}, first)
    store.set_setting('rumble_curves', {'high': curve_preset('precise')}, first)
    store.set_setting('trigger_rumble_enabled', True, first)
    store.save()
    store = ConfigStore(tmp_path)
    assert device_config(store.data, first)['trigger_curves']['left'] == curve_preset('sensitive')
    assert device_config(store.data, first)['trigger_rumble_enabled'] is True
    assert device_config(store.data, second)['trigger_curves']['left'] == curve_preset('linear')
    assert device_config(store.data, second)['rumble_curves']['high'] == curve_preset('linear')
    assert device_config(store.data, second)['trigger_rumble_enabled'] is False
    assert device_config(store.data, None)['trigger_curves']['left'] == curve_preset('linear')


def test_corrupt_device_curves_are_normalized_when_read():
    settings = device_config({'device_settings': {'pad:A': {'trigger_curves': 'bad',
        'rumble_curves': {'low': {'points': [0, .7, .2, .4, 1]}},
        'trigger_rumble_enabled': 'false'}}}, xbox_frame())
    assert settings['trigger_curves']['left'] == curve_preset('linear')
    assert settings['rumble_curves']['low']['points'] == [0, .7, .7, .7, 1]
    assert settings['trigger_rumble_enabled'] is False

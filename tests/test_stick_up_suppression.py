import pytest
from gamepadstudio.mapping_engine import input_thresholds, InputNormalizer


def test_input_thresholds_suppress_ls_up_options():
    # Default is off
    assert input_thresholds({})['suppress_ls_up'] == 'off'

    # Valid values
    assert input_thresholds({'suppress_ls_up': 'filter'})['suppress_ls_up'] == 'filter'
    assert input_thresholds({'suppress_ls_up': 'disable'})['suppress_ls_up'] == 'disable'
    assert input_thresholds({'suppress_ls_up': 'off'})['suppress_ls_up'] == 'off'

    # True maps to filter
    assert input_thresholds({'suppress_ls_up': True})['suppress_ls_up'] == 'filter'

    # Invalid values fallback to off
    assert input_thresholds({'suppress_ls_up': 'unknown'})['suppress_ls_up'] == 'off'
    assert input_thresholds({'suppress_ls_up': 123})['suppress_ls_up'] == 'off'


def test_input_normalizer_suppress_ls_up_behavior():
    normalizer = InputNormalizer()
    fake_state = {'connected': True, 'buttons': [], 'axes': [0.6, -0.6, 0.0, 0.0, 0.0, 0.0]}

    # 1. Normal mode (off): diagonal forward-up triggers both LS:right and LS:up
    active = normalizer.update(fake_state, {'suppress_ls_up': 'off'})
    assert 'LS:right' in active
    assert 'LS:up' in active

    normalizer.reset()

    # 2. Filter mode: rolling motion with horizontal deflection (abs(x) > 0.25) suppresses LS:up
    active = normalizer.update(fake_state, {'suppress_ls_up': 'filter'})
    assert 'LS:right' in active
    assert 'LS:up' not in active

    normalizer.reset()

    # 3. Filter mode: straight up without horizontal deflection (abs(x) <= 0.25) triggers LS:up
    straight_up_state = {'connected': True, 'buttons': [], 'axes': [0.05, -0.6, 0.0, 0.0, 0.0, 0.0]}
    active = normalizer.update(straight_up_state, {'suppress_ls_up': 'filter'})
    assert 'LS:up' in active

    normalizer.reset()

    # 4. Filter mode: deep deliberate diagonal (y <= -0.85) triggers LS:up
    deep_diagonal_state = {'connected': True, 'buttons': [], 'axes': [0.6, -0.88, 0.0, 0.0, 0.0, 0.0]}
    active = normalizer.update(deep_diagonal_state, {'suppress_ls_up': 'filter'})
    assert 'LS:up' in active

    normalizer.reset()

    # 5. Disable mode: straight full up never triggers LS:up
    full_up_state = {'connected': True, 'buttons': [], 'axes': [0.0, -1.0, 0.0, 0.0, 0.0, 0.0]}
    active = normalizer.update(full_up_state, {'suppress_ls_up': 'disable'})
    assert 'LS:up' not in active

"""Declarative schema constants for GamePad Studio mapping, feel, and profile contracts."""

HOLD_MODES = ('hold', 'toggle', 'turbo', 'double_tap_hold')
TURBO_RATE_RANGE = (5, 30)
DOUBLE_TAP_HOLD_TAP_S = 0.045

THRESHOLD_GROUPS = (
    'stick',
    'trigger',
    'outer',
    'walk',
    'outer_up',
    'outer_down',
    'outer_left',
    'outer_right',
)

SUPPRESS_LS_UP_MODES = ('off', 'filter', 'disable')

INPUT_NUMERIC_FIELDS = (
    {f'{group}_press' for group in THRESHOLD_GROUPS}
    | {f'{group}_release' for group in THRESHOLD_GROUPS}
    | {'chord_window'}
)
INPUT_OPTION_FIELDS = INPUT_NUMERIC_FIELDS | {'suppress_ls_up'}

MOUSE_MODES = ('game', 'desktop')
STICK_SOURCES = ('right', 'left')
GYRO_TRIGGERS = (
    'LT', 'RT', 'LB', 'RB', 'L3', 'R3',
    'L1', 'R1', 'L2', 'R2',
    '9', '10', '7', '8',
    'always', '',
)

DEFAULT_GYRO_SENSITIVITY = 18.0
DEFAULT_GYRO_TRIGGER = 'LT'
DEFAULT_STICK_SOURCE = 'right'
DEFAULT_TURBO_RATE = 15

MOUSE_NUMERIC_RANGES = {
    'sensitivity': (1.0, 100.0),
    'deadzone': (0.01, 0.50),
    'y_ratio': (0.1, 2.0),
    'edge_boost': (1.0, 3.0),
    'gyro_sensitivity': (1.0, 100.0),
}
MOUSE_BOOL_FIELDS = {'invert_y', 'gyro_enabled', 'gyro_invert_y'}
MOUSE_OPTION_FIELDS = (
    set(MOUSE_NUMERIC_RANGES)
    | MOUSE_BOOL_FIELDS
    | {'mode', 'stick_source', 'gyro_trigger'}
)

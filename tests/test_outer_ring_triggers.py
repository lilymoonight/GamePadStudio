"""Automated unit and UI tests for Milestone 3 (Requirement R4):
Left-stick per-direction outer-ring triggers and configurable directional thresholds.
"""
from __future__ import annotations

import copy
import math
import os
import pytest

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from gamepadstudio.controller_glyphs import button_text, _mark, token_advance
from gamepadstudio.i18n import init_language, get_language_preference
from gamepadstudio.mapping_engine import (
    INPUTS,
    INPUT_THRESHOLD_DEFAULTS,
    InputNormalizer,
    canonical_trigger,
    input_sources,
    input_thresholds,
    trigger_label,
)
from gamepadstudio.mapping_ui import BindingDialog
from gamepadstudio.virtual_kbm_ui import KbmFeelDialog, compact_trigger, trigger_tokens
from tests.mapping_fixtures import MappingOwner
from tests.test_unified_mapping import entry, frame, runtime


# ---------------------------------------------------------------------------
# Test Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def owner(tmp_path):
    app = QApplication.instance() or QApplication([])
    from gamepadstudio import i18n
    prev_pref = i18n._CURRENT_PREFERENCE
    prev_lang = i18n._CURRENT_LANG
    init_language('zh')
    val = MappingOwner(tmp_path)
    val.snapshot = {
        'family': 'generic', 'device_key': 'test:unit', 'instance_id': 1,
        'touchpad': False, 'touchpad_count': 0, 'touchpad_fingers': 0,
        'available_buttons': list(range(16)), 'available_axes': list(range(6)),
        'buttons': [], 'axes': [0.] * 6, 'touch_fingers': []
    }
    val.store.activate_controller(val.snapshot)
    yield val
    val.close()
    i18n._CURRENT_PREFERENCE = prev_pref
    i18n._CURRENT_LANG = prev_lang


# ---------------------------------------------------------------------------
# 1. Constants, Canonicalization, Discovery & Glyphs
# ---------------------------------------------------------------------------

def test_directional_outer_constants_and_canonical_triggers():
    """All 4 directional outer triggers must be registered in INPUTS and canonicalized cleanly."""
    directional_outer = ('LS:up:outer', 'LS:down:outer', 'LS:left:outer', 'LS:right:outer')
    for trig in directional_outer:
        assert trig in INPUTS
        assert canonical_trigger(trig) == trig
        assert canonical_trigger(trig.lower()) == trig
        assert canonical_trigger(trig.upper()) == trig

    # Chord composition with buttons
    assert canonical_trigger('0+LS:up:outer') == '0+LS:up:outer'
    assert canonical_trigger('LS:down:outer+RT') == 'RT+LS:down:outer'


def test_input_sources_detection_when_axes_present():
    """input_sources() must include directional outer triggers only when left stick axes exist."""
    state_with_ls = {'available_axes': [0, 1, 2, 3, 4, 5], 'axes': [0.] * 6}
    sources = set(input_sources(state_with_ls))
    for expected in ('LS:up:outer', 'LS:down:outer', 'LS:left:outer', 'LS:right:outer', 'LS:outer', 'LS:inner'):
        assert expected in sources

    # When left stick axes are absent (e.g. only triggers available)
    state_without_ls = {'available_axes': [4, 5], 'axes': [0., 0.], 'is_gamecontroller': False}
    sources_no_ls = set(input_sources(state_without_ls))
    for not_expected in ('LS:up:outer', 'LS:down:outer', 'LS:left:outer', 'LS:right:outer', 'LS:outer', 'LS:inner'):
        assert not_expected not in sources_no_ls


def test_directional_outer_trigger_labels_and_glyphs():
    """trigger_label() and controller glyph notation must render compact, descriptive labels."""
    app = QApplication.instance() or QApplication([])
    expected_labels = {
        'LS:up:outer': '左摇杆上推满',
        'LS:down:outer': '左摇杆下推满',
        'LS:left:outer': '左摇杆左推满',
        'LS:right:outer': '左摇杆右推满',
    }
    for trig, expected_text in expected_labels.items():
        assert trigger_label(trig) == expected_text
        assert trigger_label(trig, family='dualsense') == expected_text
        assert trigger_label(trig, family='xbox') == expected_text

    # Glyphs in Chinese
    assert button_text('LS:up:outer') == 'LS↑推满'
    assert button_text('LS:down:outer') == 'LS↓推满'
    assert button_text('LS:left:outer') == 'LS←推满'
    assert button_text('LS:right:outer') == 'LS→推满'
    assert _mark('LS:up:outer', 'generic') == 'stick'
    font = QFont('sans-serif', 12)
    assert token_advance('LS:up:outer', 'generic', font) > 0

    # Compact triggers and tokens in virtual_kbm_ui
    assert compact_trigger('左摇杆上推满') == 'LS↑推满'
    assert trigger_tokens('左摇杆上推满 + R1') == ('R1', 'LS↑推满')


# ---------------------------------------------------------------------------
# 2. Threshold Normalization & Atomic Pair Fallback
# ---------------------------------------------------------------------------

def test_input_thresholds_defaults_and_directional_inheritance():
    """input_thresholds() directional outer pairs must default to (0.85, 0.75) or inherit outer_press/release."""
    t_default = input_thresholds({})
    assert t_default['outer_press'] == 0.85
    assert t_default['outer_release'] == 0.75
    for d in ('up', 'down', 'left', 'right'):
        assert t_default[f'outer_{d}_press'] == 0.85
        assert t_default[f'outer_{d}_release'] == 0.75

    # Global override inherits down to all 4 directions when not explicitly overridden
    t_global = input_thresholds({'outer_press': 0.90, 'outer_release': 0.80})
    for d in ('up', 'down', 'left', 'right'):
        assert t_global[f'outer_{d}_press'] == 0.90
        assert t_global[f'outer_{d}_release'] == 0.80


def test_input_thresholds_independent_directional_overrides():
    """Specific directional threshold pairs must override defaults independently."""
    custom = {
        'outer_press': 0.85, 'outer_release': 0.75,
        'outer_up_press': 0.95, 'outer_up_release': 0.88,
        'outer_down_press': 0.78, 'outer_down_release': 0.65,
    }
    t = input_thresholds(custom)
    assert t['outer_up_press'] == 0.95 and t['outer_up_release'] == 0.88
    assert t['outer_down_press'] == 0.78 and t['outer_down_release'] == 0.65
    # Left and right inherit base outer
    assert t['outer_left_press'] == 0.85 and t['outer_left_release'] == 0.75
    assert t['outer_right_press'] == 0.85 and t['outer_right_release'] == 0.75


@pytest.mark.parametrize('bad_settings', [
    # release >= press
    {'outer_up_press': 0.80, 'outer_up_release': 0.80},
    {'outer_up_press': 0.75, 'outer_up_release': 0.85},
    # out of bounds (<= 0 or > 1)
    {'outer_up_press': 1.10, 'outer_up_release': 0.75},
    {'outer_up_press': 0.85, 'outer_up_release': 0.0},
    {'outer_up_press': 0.85, 'outer_up_release': -0.1},
    # boolean rejection
    {'outer_up_press': True, 'outer_up_release': 0.75},
    {'outer_up_press': 0.85, 'outer_up_release': False},
    # nan / inf / string
    {'outer_up_press': float('nan'), 'outer_up_release': 0.75},
    {'outer_up_press': 0.85, 'outer_up_release': float('inf')},
    {'outer_up_press': 'invalid', 'outer_up_release': 0.75},
    # missing one key of pair (incomplete pair)
    {'outer_up_press': 0.95},
    {'outer_up_release': 0.70},
])
def test_input_thresholds_atomic_pair_fallback(bad_settings):
    """If either member of a directional pair is invalid or omitted, both fall back together to base outer."""
    settings = dict({'outer_press': 0.90, 'outer_release': 0.80}, **bad_settings)
    t = input_thresholds(settings)
    # Must atomically fall back to the base outer pair
    assert t['outer_up_press'] == 0.90
    assert t['outer_up_release'] == 0.80


# ---------------------------------------------------------------------------
# 3. InputNormalizer Hysteresis & Diagonal Deflection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('direction, axis_index, sign', [
    ('up', 1, -1.0),
    ('down', 1, 1.0),
    ('left', 0, -1.0),
    ('right', 0, 1.0),
])
def test_normalizer_directional_outer_hysteresis_all_directions(direction, axis_index, sign):
    """InputNormalizer activates directional outer at press, holds until release, and releases below."""
    normalizer = InputNormalizer()
    key = f'LS:{direction}:outer'
    base_key = f'LS:{direction}'

    def make_frame(mag):
        axes = [0.0] * 6
        axes[axis_index] = sign * mag
        return frame(axes=axes)

    # 1. Neutral
    assert key not in normalizer.update(make_frame(0.0))

    # 2. Deflection just below press threshold (0.84 < 0.85)
    inputs = normalizer.update(make_frame(0.84))
    assert base_key in inputs
    assert key not in inputs

    # 3. Deflection reaching press threshold (0.86 >= 0.85)
    inputs = normalizer.update(make_frame(0.86))
    assert base_key in inputs
    assert key in inputs

    # 4. Deflection in hysteresis release band (0.80: between 0.75 and 0.85)
    inputs = normalizer.update(make_frame(0.80))
    assert key in inputs

    # 5. Deflection dropping below release threshold (0.74 < 0.75)
    inputs = normalizer.update(make_frame(0.74))
    assert key not in inputs
    assert base_key in inputs  # base direction stays active until 0.35

    # 6. Full return to neutral
    inputs = normalizer.update(make_frame(0.0))
    assert not inputs


def test_normalizer_asymmetric_directional_thresholds():
    """Different thresholds per direction must behave independently."""
    normalizer = InputNormalizer()
    settings = {
        'outer_up_press': 0.95, 'outer_up_release': 0.88,
        'outer_down_press': 0.75, 'outer_down_release': 0.65,
    }
    # Down stick at 0.78 triggers Down outer
    inputs = normalizer.update(frame(axes=[0.0, 0.78, 0, 0, 0, 0]), settings)
    assert 'LS:down:outer' in inputs

    # Up stick at -0.78 does NOT trigger Up outer (needs 0.95)
    normalizer.reset()
    inputs = normalizer.update(frame(axes=[0.0, -0.78, 0, 0, 0, 0]), settings)
    assert 'LS:up:outer' not in inputs
    assert 'LS:up' in inputs

    # Up stick at -0.96 triggers Up outer
    inputs = normalizer.update(frame(axes=[0.0, -0.96, 0, 0, 0, 0]), settings)
    assert 'LS:up:outer' in inputs


def test_normalizer_diagonal_deflection_activates_both_directional_outers():
    """Deep diagonal stick deflection activates both directional outer triggers simultaneously."""
    normalizer = InputNormalizer()
    # Upper-right diagonal deflection
    diag_state = frame(axes=[0.90, -0.90, 0, 0, 0, 0])
    inputs = normalizer.update(diag_state)
    assert 'LS:right' in inputs
    assert 'LS:up' in inputs
    assert 'LS:right:outer' in inputs
    assert 'LS:up:outer' in inputs
    assert 'LS:outer' in inputs

    # Relax X below release while holding Y
    asym_state = frame(axes=[0.70, -0.90, 0, 0, 0, 0])
    inputs = normalizer.update(asym_state)
    assert 'LS:right:outer' not in inputs
    assert 'LS:right' in inputs
    assert 'LS:up:outer' in inputs


def test_normalizer_coexistence_with_radial_outer_and_inner_walk():
    """Radial LS:outer, walking LS:inner, and directional LS:<dir>:outer operate independently."""
    normalizer = InputNormalizer()
    settings = {'stick_press': 0.24, 'stick_release': 0.17, 'walk_press': 0.62, 'walk_release': 0.72}

    # Case A: Diagonal where magnitude >= 0.85 but individual components < 0.85
    # hypot(0.65, 0.65) = 0.9192 >= 0.85
    inputs = normalizer.update(frame(axes=[0.65, 0.65, 0, 0, 0, 0]), settings)
    assert 'LS:outer' in inputs
    assert 'LS:right:outer' not in inputs
    assert 'LS:down:outer' not in inputs

    # Case B: Walking band
    normalizer.reset()
    inputs = normalizer.update(frame(axes=[0.40, 0.0, 0, 0, 0, 0]), settings)
    assert 'LS:right' in inputs
    assert 'LS:inner' in inputs
    assert 'LS:outer' not in inputs
    assert 'LS:right:outer' not in inputs

    # Case C: Pushing through walk band into full outer
    inputs = normalizer.update(frame(axes=[0.90, 0.0, 0, 0, 0, 0]), settings)
    assert 'LS:right' in inputs
    assert 'LS:inner' not in inputs
    assert 'LS:outer' in inputs
    assert 'LS:right:outer' in inputs


# ---------------------------------------------------------------------------
# 4. suppress_ls_up Integration
# ---------------------------------------------------------------------------

def test_directional_outer_up_respects_suppress_ls_up():
    """LS:up:outer must be suppressed whenever suppress_ls_up suppresses LS:up."""
    normalizer = InputNormalizer()

    # 1. Mode 'off': roll motion with horizontal deflection triggers both
    roll_state = frame(axes=[0.60, -0.90, 0, 0, 0, 0])
    inputs = normalizer.update(roll_state, {'suppress_ls_up': 'off'})
    assert 'LS:up' in inputs
    assert 'LS:up:outer' in inputs

    # 2. Mode 'filter': rolling motion (abs(x) > 0.25) with shallow/moderate y suppresses both
    normalizer.reset()
    shallow_roll = frame(axes=[0.60, -0.75, 0, 0, 0, 0])
    inputs = normalizer.update(shallow_roll, {'suppress_ls_up': 'filter'})
    assert 'LS:up' not in inputs
    assert 'LS:up:outer' not in inputs

    # 3. Mode 'filter': straight up without horizontal deflection triggers both
    normalizer.reset()
    straight_up = frame(axes=[0.05, -0.90, 0, 0, 0, 0])
    inputs = normalizer.update(straight_up, {'suppress_ls_up': 'filter'})
    assert 'LS:up' in inputs
    assert 'LS:up:outer' in inputs

    # 4. Mode 'filter': deep intentional diagonal (y <= -0.88) triggers both
    normalizer.reset()
    deep_diag = frame(axes=[0.60, -0.92, 0, 0, 0, 0])
    inputs = normalizer.update(deep_diag, {'suppress_ls_up': 'filter'})
    assert 'LS:up' in inputs
    assert 'LS:up:outer' in inputs

    # 5. Mode 'disable': full upward deflection never triggers LS:up or LS:up:outer
    normalizer.reset()
    full_up = frame(axes=[0.0, -1.0, 0, 0, 0, 0])
    inputs = normalizer.update(full_up, {'suppress_ls_up': 'disable'})
    assert 'LS:up' not in inputs
    assert 'LS:up:outer' not in inputs


# ---------------------------------------------------------------------------
# 5. MappingRuntime End-to-End Execution
# ---------------------------------------------------------------------------

def test_mapping_runtime_dispatch_directional_outer():
    """MappingRuntime dispatches bindings mapped to directional outer triggers with clean hysteresis."""
    # LS:up -> 'W' (run forward), LS:up:outer -> 'Shift' (sprint)
    mappings = {
        'LS:up': entry('hold', 'W'),
        'LS:up:outer': entry('hold', 'Shift'),
    }
    engine, config, actions, _ = runtime(mappings)

    # 1. Half stick push: runs forward
    engine.update(frame(axes=[0.0, -0.60, 0, 0, 0, 0]), config, now=0.0)
    assert actions.keys[ord('W')] == 1
    assert actions.keys.get(16, 0) == 0  # Shift not pressed

    # 2. Push full forward: triggers sprint
    engine.update(frame(axes=[0.0, -0.90, 0, 0, 0, 0]), config, now=0.1)
    assert actions.keys[ord('W')] == 1
    assert actions.keys[16] == 1  # Shift pressed

    # 3. Relax slightly to 0.80: sprint stays active (hysteresis)
    engine.update(frame(axes=[0.0, -0.80, 0, 0, 0, 0]), config, now=0.2)
    assert actions.keys[ord('W')] == 1
    assert actions.keys[16] == 1

    # 4. Relax below 0.75 to 0.70: sprint releases, run stays active
    engine.update(frame(axes=[0.0, -0.70, 0, 0, 0, 0]), config, now=0.3)
    assert actions.keys[ord('W')] == 1
    assert actions.keys[16] == 0

    # 5. Release to neutral: all keys release
    engine.update(frame(), config, now=0.4)
    assert not any(actions.keys.values())


# ---------------------------------------------------------------------------
# 6. UI Tests: BindingDialog & KbmFeelDialog
# ---------------------------------------------------------------------------

def test_binding_dialog_poll_excludes_directional_outer_triggers(owner):
    """BindingDialog.poll() must exclude directional outer triggers to prevent chord contamination."""
    dialog = BindingDialog(owner, '0', {'short': {'action': 'none'}}, mode='kbm')
    dialog.timer.stop()
    try:
        dialog.start_capture()
        owner.snapshot['axes'] = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        dialog.poll()
        # Deflect stick to upper outer limit
        owner.snapshot['axes'] = [0.0, -0.95, 0.0, 0.0, 0.0, 0.0]
        dialog.poll()

        # Capture best must record 'LS:up', NOT 'LS:up:outer' or chord
        assert dialog.best == {'LS:up'}
    finally:
        dialog.close()


def test_kbm_feel_dialog_outer_thresholds_editing_and_persistence(owner):
    """KbmFeelDialog must allow editing outer thresholds and persist them to profile options."""
    profile_name = owner.config['active_profile']
    dialog = KbmFeelDialog(owner, profile_name)
    try:
        dialog.show()

        # Check existence of global outer controls
        assert 'outer_press' in dialog.input_fields
        assert 'outer_release' in dialog.input_fields
        assert dialog.input_fields['outer_press'].value() == 85
        assert dialog.input_fields['outer_release'].value() == 75

        # Modify global outer thresholds
        dialog.input_fields['outer_press'].setValue(90)
        dialog.input_fields['outer_release'].setValue(80)

        # Enable directional overrides if toggle is available
        if hasattr(dialog, 'directional_outer_toggle'):
            dialog.directional_outer_toggle.setChecked(True)
            if 'outer_up_press' in dialog.input_fields:
                dialog.input_fields['outer_up_press'].setValue(95)
                dialog.input_fields['outer_up_release'].setValue(85)

        # Save dialog
        dialog.validate()
        assert dialog.result() == QDialog.DialogCode.Accepted

        # Check persisted options
        saved_opts = dialog.options()['input']
        assert saved_opts['outer_press'] == 0.90
        assert saved_opts['outer_release'] == 0.80
        if hasattr(dialog, 'directional_outer_toggle') and dialog.directional_outer_toggle.isChecked():
            assert saved_opts.get('outer_up_press') == 0.95
            assert saved_opts.get('outer_up_release') == 0.85
    finally:
        dialog.close()


def test_kbm_feel_dialog_rejects_invalid_outer_thresholds(owner):
    """KbmFeelDialog must reject outer_release >= outer_press with an error message."""
    profile_name = owner.config['active_profile']
    dialog = KbmFeelDialog(owner, profile_name)
    try:
        dialog.show()

        # Invert global thresholds: release >= press
        dialog.input_fields['outer_press'].setValue(70)
        dialog.input_fields['outer_release'].setValue(75)

        QTest.mouseClick(dialog.controls.button(QDialogButtonBox.Save), Qt.LeftButton)

        # Dialog must reject and display error
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert not dialog.error.isHidden()
        assert '松开位置' in dialog.error.text() or '外圈推满' in dialog.error.text()
    finally:
        dialog.close()

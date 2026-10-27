"""Unit tests for virtual key definitions, parse_keys parsing, and platform action backends.

Validates Milestone 1 (R2):
- Calc (0xB7 = 183) and Calculator (0xB7 = 183) vs. F24 (0x87 = 135) separation in KEYS.
- parse_keys('Calc') != parse_keys('F24') and distinct chord generation.
- WindowsActions._send() extended key flags (0xB7 included in extended set, 0x87 excluded).
- MacActions.supports_key() and MacActions.key_capability() distinct rejection reasons.
"""
import pytest
from gamepadstudio import actions
from gamepadstudio.actions import KEYS, parse_keys, WindowsActions
from gamepadstudio.macos_actions import MacActions, UNSUPPORTED_KEY_REASONS


# ============================================================================
# 1. Keycode Mappings & Parsing Tests
# ============================================================================

def test_calc_and_f24_keycodes_in_keys_dict():
    """Verify virtual-key dictionary mappings for Calc, Calculator, and F24."""
    # Calc / Calculator must map to Windows VK_LAUNCH_APP2 (0xB7 = 183)
    assert KEYS['Calc'] == 0xB7
    assert KEYS['Calculator'] == 0xB7
    assert KEYS['Calc'] == 183
    assert KEYS['Calculator'] == 183

    # F24 must map to VK_F24 (0x87 = 135)
    assert KEYS['F24'] == 0x87
    assert KEYS['F24'] == 135

    # Calc and F24 must be strictly distinct
    assert KEYS['Calc'] != KEYS['F24']
    assert KEYS['Calculator'] != KEYS['F24']


def test_parse_keys_calc_vs_f24_separation():
    """Verify parse_keys returns distinct single-key virtual key lists."""
    calc = parse_keys('Calc')
    calculator = parse_keys('Calculator')
    f24 = parse_keys('F24')

    assert calc == [0xB7]
    assert calculator == [0xB7]
    assert f24 == [0x87]

    assert calc != f24
    assert calculator != f24


@pytest.mark.parametrize('alias,expected', [
    ('Calc', [0xB7]),
    ('calc', [0xB7]),
    ('CALC', [0xB7]),
    ('Calculator', [0xB7]),
    ('calculator', [0xB7]),
    ('CALCULATOR', [0xB7]),
    ('F24', [0x87]),
    ('f24', [0x87]),
])
def test_parse_keys_case_insensitivity_and_aliases(alias, expected):
    """Verify parse_keys handles various case representations for Calc and F24."""
    assert parse_keys(alias) == expected


def test_parse_keys_chords_with_calc_and_f24():
    """Verify modifier chords containing Calc and F24 parse distinctly."""
    # Ctrl = 17 (0x11), Alt = 18 (0x12), Shift = 16 (0x10)
    assert parse_keys('Ctrl+Calc') == [17, 0xB7]
    assert parse_keys('Ctrl+F24') == [17, 0x87]
    assert parse_keys('Ctrl+Calc') != parse_keys('Ctrl+F24')

    assert parse_keys('Ctrl+Alt+Calc') == [17, 18, 0xB7]
    assert parse_keys('Ctrl+Alt+F24') == [17, 18, 0x87]
    assert parse_keys('Ctrl+Alt+Calc') != parse_keys('Ctrl+Alt+F24')


# ============================================================================
# 2. WindowsActions Extended Key Set Verification
# ============================================================================

def _create_mock_windows_actions():
    """Create a WindowsActions instance safely on any OS with a mock user32."""
    action = WindowsActions.__new__(WindowsActions)
    action.held = {}
    action.held_mouse = set()
    action.held_gamepad_buttons = set()
    action.held_turbo = {}

    sent_events = []

    class MockUser32:
        def MapVirtualKeyW(self, k, mode):
            # Return a non-zero dummy scan code to activate the scan code flag (0x0008)
            return 0x20

        def SendInput(self, count, array, size):
            for i in range(count):
                ki = array[i].u.ki
                sent_events.append({
                    'vk': ki.wVk,
                    'scan': ki.wScan,
                    'flags': ki.dwFlags,
                    'extended': bool(ki.dwFlags & 0x0001),
                    'keyup': bool(ki.dwFlags & 0x0002),
                    'scancode': bool(ki.dwFlags & 0x0008),
                })
            return count

    action.user32 = MockUser32()
    return action, sent_events


def test_windows_actions_send_calc_extended_flag():
    """Verify 0xB7 (VK_LAUNCH_APP2) emits KEYEVENTF_EXTENDEDKEY (0x0001) in _send."""
    action, sent = _create_mock_windows_actions()

    # Key down
    action._send([0xB7], down=True)
    assert len(sent) == 1
    assert sent[0]['vk'] == 0xB7
    assert sent[0]['extended'] is True
    assert sent[0]['keyup'] is False

    # Key up
    sent.clear()
    action._send([0xB7], down=False)
    assert len(sent) == 1
    assert sent[0]['vk'] == 0xB7
    assert sent[0]['extended'] is True
    assert sent[0]['keyup'] is True


def test_windows_actions_send_f24_not_extended():
    """Verify 0x87 (VK_F24) does NOT emit KEYEVENTF_EXTENDEDKEY (0x0001) in _send."""
    action, sent = _create_mock_windows_actions()

    # Key down
    action._send([0x87], down=True)
    assert len(sent) == 1
    assert sent[0]['vk'] == 0x87
    assert sent[0]['extended'] is False
    assert sent[0]['keyup'] is False

    # Key up
    sent.clear()
    action._send([0x87], down=False)
    assert len(sent) == 1
    assert sent[0]['vk'] == 0x87
    assert sent[0]['extended'] is False
    assert sent[0]['keyup'] is True


def test_windows_actions_send_batch_calc_and_f24():
    """Verify a batch send correctly separates extended flags between Calc and F24."""
    action, sent = _create_mock_windows_actions()

    action._send([0x87, 0xB7], down=True)
    assert len(sent) == 2
    # First is F24: non-extended
    assert sent[0]['vk'] == 0x87
    assert sent[0]['extended'] is False
    # Second is Calc: extended
    assert sent[1]['vk'] == 0xB7
    assert sent[1]['extended'] is True


def test_windows_actions_hold_calc_and_f24_lifecycle():
    """Verify WindowsActions.hold() tracks ownership and emits proper flags for Calc vs F24."""
    action, sent = _create_mock_windows_actions()

    action.hold('Calc', down=True)
    assert action.held == {0xB7: 1}
    assert sent[-1]['vk'] == 0xB7
    assert sent[-1]['extended'] is True

    action.hold('F24', down=True)
    assert action.held == {0xB7: 1, 0x87: 1}
    assert sent[-1]['vk'] == 0x87
    assert sent[-1]['extended'] is False

    action.hold('Calc', down=False)
    assert action.held == {0x87: 1}
    assert sent[-1]['vk'] == 0xB7
    assert sent[-1]['extended'] is True
    assert sent[-1]['keyup'] is True

    action.hold('F24', down=False)
    assert not action.held
    assert sent[-1]['vk'] == 0x87
    assert sent[-1]['extended'] is False
    assert sent[-1]['keyup'] is True


# ============================================================================
# 3. MacActions Rejection & Capability Explanation Tests
# ============================================================================

def test_macos_actions_supports_key_calc_and_f24_both_false():
    """Verify MacActions.supports_key returns False for Calc, Calculator, and F24."""
    assert MacActions.supports_key('Calc') is False
    assert MacActions.supports_key('Calculator') is False
    assert MacActions.supports_key('calc') is False
    assert MacActions.supports_key('F24') is False
    assert MacActions.supports_key('f24') is False
    assert MacActions.supports_key('Ctrl+Calc') is False
    assert MacActions.supports_key('Cmd+F24') is False


def test_macos_actions_key_capability_distinct_reasons():
    """Verify MacActions.key_capability returns distinct, actionable reasons."""
    cap_calc = MacActions.key_capability('Calc')
    cap_calculator = MacActions.key_capability('Calculator')
    cap_f24 = MacActions.key_capability('F24')

    assert cap_calc['supported'] is False
    assert cap_calculator['supported'] is False
    assert cap_f24['supported'] is False

    # Calc and Calculator must have identical explanations
    assert cap_calc['reason'] == cap_calculator['reason']

    # Calc and F24 must have distinct explanations
    assert cap_calc['reason'] != cap_f24['reason']

    # Calc explanation directs user to the 'launch' action
    assert 'launch' in cap_calc['reason']
    assert '多媒体' in cap_calc['reason']
    assert '同一键值' not in cap_calc['reason']
    assert 'F21–F24' not in cap_calc['reason']

    # F24 explanation cites AppKit function key limitations
    assert 'F21–F24' in cap_f24['reason']
    assert 'launch' not in cap_f24['reason']
    assert '同一键值' not in cap_f24['reason']


def test_macos_actions_unsupported_key_reasons_table():
    """Verify UNSUPPORTED_KEY_REASONS contains direct entries for 0xB7 and 135."""
    assert 0xB7 in UNSUPPORTED_KEY_REASONS
    assert 135 in UNSUPPORTED_KEY_REASONS

    reason_0xb7 = UNSUPPORTED_KEY_REASONS[0xB7]
    reason_135 = UNSUPPORTED_KEY_REASONS[135]

    assert reason_0xb7 != reason_135
    assert 'launch' in reason_0xb7
    assert 'F21–F24' in reason_135


def test_macos_actions_hold_raises_distinct_value_errors():
    """Verify MacActions._hold_keys raises ValueError with distinct messages."""
    mac = MacActions.__new__(MacActions)

    with pytest.raises(ValueError) as exc_calc:
        mac._hold_keys([0xB7], down=True)
    assert 'launch' in str(exc_calc.value)

    with pytest.raises(ValueError) as exc_f24:
        mac._hold_keys([0x87], down=True)
    assert 'F21–F24' in str(exc_f24.value)

    assert str(exc_calc.value) != str(exc_f24.value)


# ============================================================================
# 4. Cross-Platform Module-Level functions
# ============================================================================

def test_actions_supports_key_cross_platform(monkeypatch):
    """Verify actions.supports_key behavior under simulated Windows and macOS."""
    # Under Darwin: both unsupported
    monkeypatch.setattr(actions.sys, 'platform', 'darwin')
    assert actions.supports_key('Calc') is False
    assert actions.supports_key('F24') is False

    # Under Win32: both supported
    monkeypatch.setattr(actions.sys, 'platform', 'win32')
    assert actions.supports_key('Calc') is True
    assert actions.supports_key('F24') is True

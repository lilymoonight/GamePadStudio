from pathlib import Path
from gamepadstudio.studio_core import ConfigStore
from gamepadstudio.kbm_mapper import (
    NIKKI_PROFILE_NAME,
    NikkiKbmEngine,
    CHORD_MAPPINGS,
    stick_to_wasd,
    stick_to_mouse,
    infinity_nikki_defaults,
)


class MockActions:
    def __init__(self):
        self.shortcuts = []
        self.holds = []
        self.held_keys = set()
        self.mouse_buttons = []
        self.held_mouse = set()
        self.mouse_moves = []

    def shortcut(self, val):
        self.shortcuts.append(val)

    def hold(self, val, down):
        self.holds.append((val, down))
        if down:
            self.held_keys.add(val)
        else:
            self.held_keys.discard(val)

    def mouse_button(self, btn, down):
        self.mouse_buttons.append((btn, down))
        if down:
            self.held_mouse.add(btn)
        else:
            self.held_mouse.discard(btn)

    def move_mouse(self, dx, dy):
        self.mouse_moves.append((dx, dy))


def test_nikki_profile_in_all_controller_families(tmp_path):
    store = ConfigStore(tmp_path)
    assert NIKKI_PROFILE_NAME in store.data['profiles']
    assert store.data['profile_families'].get(NIKKI_PROFILE_NAME) == 'all'

    for fam in ['dualsense', 'dualshock4', 'xbox', 'switch', 'generic']:
        profiles = store.profiles_for({'family': fam})
        assert NIKKI_PROFILE_NAME in profiles, f'{NIKKI_PROFILE_NAME} must be available in {fam}'


def test_nikki_chords_outfits_1_to_8():
    mock = MockActions()
    engine = NikkiKbmEngine(mock)

    # LB + (A, X, Y, B) -> 1, 2, 3, 4
    # Frame 1: Hold LB (9)
    engine.update({'axes': [0] * 6, 'buttons': [9]})
    # Frame 2: Press A (0)
    engine.update({'axes': [0] * 6, 'buttons': [9, 0]})
    assert mock.shortcuts[-1] == '1'

    # Frame 3: Release A, press X (2)
    engine.update({'axes': [0] * 6, 'buttons': [9, 2]})
    assert mock.shortcuts[-1] == '2'

    # Frame 4: Release X, press Y (3)
    engine.update({'axes': [0] * 6, 'buttons': [9, 3]})
    assert mock.shortcuts[-1] == '3'

    # Frame 5: Release Y, press B (1)
    engine.update({'axes': [0] * 6, 'buttons': [9, 1]})
    assert mock.shortcuts[-1] == '4'

    # LT + (A, X, Y, B) -> 5, 6, 7, 8
    # Frame 6: Release LB, pull LT (axis 4 = 0.8)
    engine.update({'axes': [0, 0, 0, 0, 0.8, 0], 'buttons': []})
    engine.update({'axes': [0, 0, 0, 0, 0.8, 0], 'buttons': [0]})
    assert mock.shortcuts[-1] == '5'

    engine.update({'axes': [0, 0, 0, 0, 0.8, 0], 'buttons': [2]})
    assert mock.shortcuts[-1] == '6'

    engine.update({'axes': [0, 0, 0, 0, 0.8, 0], 'buttons': [3]})
    assert mock.shortcuts[-1] == '7'

    engine.update({'axes': [0, 0, 0, 0, 0.8, 0], 'buttons': [1]})
    assert mock.shortcuts[-1] == '8'


def test_nikki_chords_panels():
    mock = MockActions()
    engine = NikkiKbmEngine(mock)

    # RB + (A, X, Y, B) -> M, P, C, U
    engine.update({'axes': [0] * 6, 'buttons': [10, 0]})
    assert mock.shortcuts[-1] == 'M'
    engine.update({'axes': [0] * 6, 'buttons': [10, 2]})
    assert mock.shortcuts[-1] == 'P'
    engine.update({'axes': [0] * 6, 'buttons': [10, 3]})
    assert mock.shortcuts[-1] == 'C'
    engine.update({'axes': [0] * 6, 'buttons': [10, 1]})
    assert mock.shortcuts[-1] == 'U'

    # RT + (A, X, Y, B) -> O, I, Y, K
    engine.update({'axes': [0, 0, 0, 0, 0, 0.8], 'buttons': [0]})
    assert mock.shortcuts[-1] == 'O'
    engine.update({'axes': [0, 0, 0, 0, 0, 0.8], 'buttons': [2]})
    assert mock.shortcuts[-1] == 'I'
    engine.update({'axes': [0, 0, 0, 0, 0, 0.8], 'buttons': [3]})
    assert mock.shortcuts[-1] == 'Y'
    engine.update({'axes': [0, 0, 0, 0, 0, 0.8], 'buttons': [1]})
    assert mock.shortcuts[-1] == 'K'


def test_nikki_single_tap_and_mouse_triggers():
    mock = MockActions()
    engine = NikkiKbmEngine(mock)

    # Tap LB alone -> Tab
    engine.update({'axes': [0] * 6, 'buttons': [9]})
    engine.update({'axes': [0] * 6, 'buttons': []})
    assert mock.shortcuts[-1] == 'Tab'

    # Tap RB alone -> V
    engine.update({'axes': [0] * 6, 'buttons': [10]})
    engine.update({'axes': [0] * 6, 'buttons': []})
    assert mock.shortcuts[-1] == 'V'

    # Pull LT alone -> right click hold & release (after 80ms chord grace window)
    import time
    engine.update({'axes': [0, 0, 0, 0, 0.7, 0], 'buttons': []})
    time.sleep(0.12)
    engine.update({'axes': [0, 0, 0, 0, 0.7, 0], 'buttons': []})
    assert ('right', True) in mock.mouse_buttons
    engine.update({'axes': [0, 0, 0, 0, 0.1, 0], 'buttons': []})
    assert ('right', False) in mock.mouse_buttons

    # Pull RT alone -> left click hold & release
    engine.update({'axes': [0, 0, 0, 0, 0, 0.7], 'buttons': []})
    time.sleep(0.12)
    engine.update({'axes': [0, 0, 0, 0, 0, 0.7], 'buttons': []})
    assert ('left', True) in mock.mouse_buttons
    engine.update({'axes': [0, 0, 0, 0, 0, 0.1], 'buttons': []})
    assert ('left', False) in mock.mouse_buttons


def test_nikki_left_stick_wasd():
    mock = MockActions()
    engine = NikkiKbmEngine(mock)

    # Up slight -> W
    engine.update({'axes': [0.0, -0.5, 0, 0, 0, 0], 'buttons': []})
    assert mock.held_keys == {'W'}

    # Up full -> W + Shift
    engine.update({'axes': [0.0, -1.0, 0, 0, 0, 0], 'buttons': []})
    assert mock.held_keys == {'W', 'Shift'}

    # Neutral -> release all
    engine.update({'axes': [0.0, 0.0, 0, 0, 0, 0], 'buttons': []})
    assert len(mock.held_keys) == 0


def test_nikki_base_face_buttons():
    mock = MockActions()
    engine = NikkiKbmEngine(mock)

    # Press A alone -> Space hold & release
    engine.update({'axes': [0] * 6, 'buttons': [0]})
    assert ('Space', True) in mock.holds
    engine.update({'axes': [0] * 6, 'buttons': []})
    assert ('Space', False) in mock.holds

    # Remaining buttons return non-face buttons for GestureEngine
    rem = engine.update({'axes': [0] * 6, 'buttons': [0, 4, 6, 9]})
    assert rem == {4, 6}

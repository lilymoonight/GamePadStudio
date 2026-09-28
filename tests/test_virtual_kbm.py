import time
from gamepadstudio.virtual_kbm import (
    VirtualKbmEngine,
    NIKKI_PRESET_CONFIG,
    GENERAL_PRESET_CONFIG,
    NIKKI_SCHEME_NAME,
    format_action_display,
)


class MockActions:
    def __init__(self):
        self.shortcuts = []
        self.holds = []
        self.held_keys = set()
        self.mouse_buttons = []
        self.held_mouse = set()
        self.mouse_moves = []
        self.game_focused = True

    def is_nikki_game_focused(self):
        return self.game_focused

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


def test_virtual_kbm_preset_loading():
    mock = MockActions()
    engine = VirtualKbmEngine(mock)
    try:
        assert engine.scheme["name"] == NIKKI_SCHEME_NAME
        assert engine.scheme["buttons"]["RT"] == "mouse:left"
        assert engine.scheme["buttons"]["LT"] == "mouse:right"
        assert engine.scheme["buttons"]["0"] == "Space"
        assert engine.scheme["chords"]["LB + 0"] == "1"
    finally:
        engine.close()


def test_virtual_kbm_instant_mouse_clicks():
    mock = MockActions()
    engine = VirtualKbmEngine(mock)
    engine.scheme["enabled"] = True
    try:
        # Pull RT (axis 5 = 0.8) -> Immediate mouse:left down
        engine.update({'axes': [0, 0, 0, 0, 0, 0.8], 'buttons': []})
        assert ('left', True) in mock.mouse_buttons
        assert 'left' in engine.active_mouse_buttons

        # Release RT (axis 5 = 0.0) -> Immediate mouse:left up
        engine.update({'axes': [0, 0, 0, 0, 0, 0.0], 'buttons': []})
        assert ('left', False) in mock.mouse_buttons
        assert 'left' not in engine.active_mouse_buttons

        # Pull LT (axis 4 = 0.8) -> Immediate mouse:right down
        engine.update({'axes': [0, 0, 0, 0, 0.8, 0], 'buttons': []})
        assert ('right', True) in mock.mouse_buttons
        assert 'right' in engine.active_mouse_buttons

        # Release LT -> Immediate mouse:right up
        engine.update({'axes': [0, 0, 0, 0, 0.0, 0], 'buttons': []})
        assert ('right', False) in mock.mouse_buttons
        assert 'right' not in engine.active_mouse_buttons
    finally:
        engine.close()


def test_virtual_kbm_nikki_chords_switching():
    mock = MockActions()
    engine = VirtualKbmEngine(mock)
    engine.scheme["enabled"] = True
    try:
        # Hold LB (button 9) and press A (button 0) -> suit 1
        engine.update({'axes': [0]*6, 'buttons': [9]})
        engine.update({'axes': [0]*6, 'buttons': [9, 0]})
        assert mock.shortcuts[-1] == '1'

        # Release A, press B (1) -> suit 2
        engine.update({'axes': [0]*6, 'buttons': [9, 1]})
        assert mock.shortcuts[-1] == '2'

        # Release B, press X (2) -> suit 3
        engine.update({'axes': [0]*6, 'buttons': [9, 2]})
        assert mock.shortcuts[-1] == '3'

        # Release X, press Y (3) -> suit 4
        engine.update({'axes': [0]*6, 'buttons': [9, 3]})
        assert mock.shortcuts[-1] == '4'
    finally:
        engine.close()


def test_virtual_kbm_wasd_movement():
    mock = MockActions()
    engine = VirtualKbmEngine(mock)
    engine.scheme["enabled"] = True
    try:
        # Push Left Stick Up slight -> W
        engine.update({'axes': [0.0, -0.6, 0, 0, 0, 0], 'buttons': []})
        assert mock.held_keys == {'W'}

        # Push Left Stick Up full -> W + Shift (sprint)
        engine.update({'axes': [0.0, -1.0, 0, 0, 0, 0], 'buttons': []})
        assert mock.held_keys == {'W', 'Shift'}

        # Neutral -> Release all
        engine.update({'axes': [0.0, 0.0, 0, 0, 0, 0], 'buttons': []})
        assert len(mock.held_keys) == 0
    finally:
        engine.close()


def test_format_action_display():
    assert "鼠标左键" in format_action_display("mouse:left")
    assert "鼠标右键" in format_action_display("mouse:right")
    assert "Space" in format_action_display("Space")
    assert format_action_display("") == "未绑定"


def test_virtual_kbm_continuous_mouse_physics():
    mock = MockActions()
    engine = VirtualKbmEngine(mock)
    engine.scheme["enabled"] = True
    try:
        # Push Right Stick right (axis 2 = 1.0) and hold for 100ms
        for _ in range(6):
            engine.update({'axes': [0, 0, 1.0, 0, 0, 0], 'buttons': []})
            time.sleep(0.016)

        # Ensure continuous mouse movement deltas were generated
        assert len(mock.mouse_moves) > 0
        total_dx = sum(dx for dx, dy in mock.mouse_moves)
        assert total_dx > 50, f"Expected substantial continuous dx, got {total_dx}"
    finally:
        engine.close()


def test_virtual_kbm_lt_aim_and_chord_interplay():
    mock = MockActions()
    engine = VirtualKbmEngine(mock)
    engine.scheme["enabled"] = True
    try:
        # 1. Pull LT without face buttons -> aim (mouse:right down)
        engine.update({'axes': [0, 0, 0, 0, 0.9, 0], 'buttons': []})
        assert ('right', True) in mock.mouse_buttons

        # 2. While holding LT, press A (0) -> fire Outfit 5 (chord LT+0 -> 5)
        # Should cleanly release mouse:right and fire shortcut 5
        engine.update({'axes': [0, 0, 0, 0, 0.9, 0], 'buttons': [0]})
        assert ('right', False) in mock.mouse_buttons
        assert '5' in mock.shortcuts

        # 3. Release A (0) while still holding LT -> chord consumed, no re-aiming
        engine.update({'axes': [0, 0, 0, 0, 0.9, 0], 'buttons': []})

        # 4. Release LT completely
        engine.update({'axes': [0, 0, 0, 0, 0.0, 0], 'buttons': []})
        assert 'LT' not in engine.consumed_modifiers
    finally:
        engine.close()


def test_virtual_kbm_smart_launcher_click():
    mock = MockActions()
    engine = VirtualKbmEngine(mock)
    engine.scheme["enabled"] = True
    try:
        # 1. Launcher / Desktop context: is_nikki_game_focused = False
        mock.game_focused = False
        
        # Press A (button 0) on launcher -> triggers mouse:left (click) instead of Space
        engine.update({'axes': [0]*6, 'buttons': [0]})
        assert ('left', True) in mock.mouse_buttons
        assert 'left' in engine.active_mouse_buttons
        assert ('Space', True) not in mock.holds

        # Release A on launcher -> releases mouse:left
        engine.update({'axes': [0]*6, 'buttons': []})
        assert ('left', False) in mock.mouse_buttons
        assert 'left' not in engine.active_mouse_buttons

        # RT also works as mouse:left on launcher
        engine.update({'axes': [0, 0, 0, 0, 0, 0.8], 'buttons': []})
        assert ('left', True) in mock.mouse_buttons
        engine.update({'axes': [0, 0, 0, 0, 0, 0.0], 'buttons': []})
        assert ('left', False) in mock.mouse_buttons

        # 2. In-Game context: is_nikki_game_focused = True
        mock.game_focused = True
        mock.mouse_buttons.clear()
        mock.holds.clear()

        # Press A (button 0) in game -> triggers Space (Jump)
        engine.update({'axes': [0]*6, 'buttons': [0]})
        assert ('Space', True) in mock.holds
        assert ('left', True) not in mock.mouse_buttons

        # Release A in game -> releases Space
        engine.update({'axes': [0]*6, 'buttons': []})
        assert ('Space', False) in mock.holds

        # Pull RT in game -> triggers mouse:left (Attack)
        engine.update({'axes': [0, 0, 0, 0, 0, 0.8], 'buttons': []})
        assert ('left', True) in mock.mouse_buttons
        engine.update({'axes': [0, 0, 0, 0, 0, 0.0], 'buttons': []})
        assert ('left', False) in mock.mouse_buttons
    finally:
        engine.close()


def test_general_preset_desktop_clicking():
    mock = MockActions()
    engine = VirtualKbmEngine(mock)
    engine.load_scheme(GENERAL_PRESET_CONFIG)
    engine.scheme["enabled"] = True
    try:
        # A button (0) is directly mapped to mouse:left
        engine.update({'axes': [0]*6, 'buttons': [0]})
        assert ('left', True) in mock.mouse_buttons
        engine.update({'axes': [0]*6, 'buttons': []})
        assert ('left', False) in mock.mouse_buttons

        # B button (1) is directly mapped to mouse:right
        engine.update({'axes': [0]*6, 'buttons': [1]})
        assert ('right', True) in mock.mouse_buttons
        engine.update({'axes': [0]*6, 'buttons': []})
        assert ('right', False) in mock.mouse_buttons
    finally:
        engine.close()


def test_trigger_label_and_inverted_mapping():
    from gamepadstudio.virtual_kbm import get_trigger_label, get_inverted_mapping
    # PS family
    assert get_trigger_label("0", "dualsense") == "×"
    assert get_trigger_label("RT", "dualsense") == "R2"
    # Xbox family
    assert get_trigger_label("0", "xbox") == "A"
    assert get_trigger_label("RT", "xbox") == "RT"
    # Flight stick / Generic
    assert get_trigger_label("LS:Up", "generic") == "摇杆↑"
    assert get_trigger_label("25", "generic") == "键26"
    assert get_trigger_label("LB + 0", "xbox") == "LB+A"

    scheme = {
        "buttons": {"0": "Space", "RT": "mouse:left", "1": "Shift"},
        "chords": {"LB + 0": "1"}
    }
    inv = get_inverted_mapping(scheme)
    assert inv["Space"] == ["0"]
    assert inv["mouse:left"] == ["RT"]
    assert inv["1"] == ["LB + 0"]


def test_virtual_kbm_ui_layout_and_interactive_capture(tmp_path):
    from PySide6.QtWidgets import QApplication
    from gamepadstudio.virtual_kbm_ui import VirtualKbmPage
    from gamepadstudio.studio_core import ConfigStore

    app = QApplication.instance() or QApplication([])
    mock = MockActions()
    engine = VirtualKbmEngine(mock)
    store = ConfigStore(tmp_path)

    page = VirtualKbmPage(engine, store=store)
    try:
        # Check standard keycaps exist
        assert "Space" in page.keycaps
        assert "W" in page.keycaps
        assert "mouse:left" in page.keycaps
        assert "Esc" in page.keycaps
        assert "F12" in page.keycaps

        # 1. User clicks 'Space' -> enters capturing state
        page.on_keycap_clicked("Space", "Space (空格)")
        assert page.is_capturing is True
        assert page.capturing_target == "Space"
        assert page.keycaps["Space"].is_capturing is True

        # 2. Controller input arrives: button 0 (A / Cross) pressed
        page.handle_device_input({'buttons': [0], 'axes': [0.0]*6})
        # Capture should complete and exit
        assert page.is_capturing is False
        assert page.schemes[page.current_scheme_name]["buttons"]["0"] == "Space"
        assert "0" in page.keycaps["Space"].badges or any("A" in b or "×" in b for b in page.keycaps["Space"].badges)

        # 3. User clicks 'mouse:left' -> pulls RT (axes[5] = 0.8)
        page.on_keycap_clicked("mouse:left", "🖱️ 鼠标左键")
        assert page.is_capturing is True
        page.handle_device_input({'buttons': [], 'axes': [0, 0, 0, 0, 0, 0.85]})
        assert page.is_capturing is False
        assert page.schemes[page.current_scheme_name]["buttons"]["RT"] == "mouse:left"

        # 4. Right click on 'Space' keycap -> clears binding
        page.on_keycap_right_clicked("Space", "Space (空格)")
        assert "0" not in page.schemes[page.current_scheme_name]["buttons"]

    finally:
        engine.close()


from unittest.mock import MagicMock
from PySide6.QtWidgets import QApplication
import pytest
from gamepadstudio.response_curves import curve_capabilities


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def test_trigger_capabilities_for_dinput_vs_xinput():
    # Raw joystick / Switch / DInput without analog trigger axes
    dinput_state = {
        'connected': True,
        'name': 'BETOP Beitong Gamepad',
        'is_gamecontroller': False,
        'family': 'generic',
        'num_axes': 4,
        'analog_trigger_axes': [],
    }
    caps = curve_capabilities(dinput_state)
    assert caps['trigger_axes'] == []

    # Standard XInput with analog triggers
    xinput_state = {
        'connected': True,
        'name': 'Xbox Compatible Gamepad',
        'is_gamecontroller': True,
        'family': 'xbox',
        'input_backend': 'xinput',
        'available_axes': [0, 1, 2, 3, 4, 5],
        'analog_trigger_axes': [4, 5],
    }
    x_caps = curve_capabilities(xinput_state)
    assert x_caps['trigger_axes'] == [4, 5]

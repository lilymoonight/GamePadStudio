import pytest
from PySide6.QtWidgets import QApplication

from gamepadstudio.virtual_kbm_ui import VirtualKbmPage
from tests.mapping_fixtures import MappingOwner


@pytest.fixture
def keyboard_curves(tmp_path):
    app = QApplication.instance() or QApplication([])
    owner = MappingOwner(tmp_path)
    opened = []
    owner.open_curve_editor = opened.append
    page = VirtualKbmPage(owner)
    yield owner, page, opened
    page.close()
    owner.close()


@pytest.mark.parametrize('family, analog, rumble, trigger_rumble, expected', [
    ('dualsense', [4, 5], True, False, {'trigger', 'rumble'}),
    ('xbox', [4, 5], True, True, {'trigger', 'rumble', 'trigger_rumble'}),
    ('switch', [], True, False, {'rumble'}),
    ('generic', [], False, False, set()),
])
def test_keyboard_curve_menu_exposes_only_device_capabilities(
        keyboard_curves, family, analog, rumble, trigger_rumble, expected):
    owner, page, opened = keyboard_curves
    owner.snapshot = {'family': family, 'axes': [0.] * 6, 'available_axes': list(range(6)),
                      'analog_trigger_axes': analog, 'rumble': rumble,
                      'trigger_rumble': trigger_rumble}
    page.set_device_state(owner.snapshot)

    assert {kind for kind, action in page.curve_actions.items() if action.isVisible()} == expected
    assert page.curves_btn.isHidden() is (not expected)
    for kind in expected:
        page.curve_actions[kind].trigger()
    assert set(opened) == expected


def test_keyboard_curve_action_rechecks_device_after_menu_opens(keyboard_curves):
    owner, page, opened = keyboard_curves
    owner.snapshot = {'family': 'dualsense', 'axes': [0.] * 6, 'rumble': True}
    page.set_device_state(owner.snapshot)
    assert page.curve_actions['trigger'].isEnabled()

    owner.snapshot = None
    page.curve_actions['trigger'].trigger()

    assert not opened
    assert page.curves_btn.isHidden()
    assert all(not action.isEnabled() for action in page.curve_actions.values())

"""Live capability updates must reach curve controls without a reconnect."""
from gamepadstudio.response_curves import curve_capabilities
from tests.test_device_scope_ui import device, workspace


def test_same_device_poll_refreshes_curve_controls_when_capabilities_change(workspace):
    window, app = workspace
    state = device(41, 'dualsense', rumble=False, axes=[])
    state.update(is_gamecontroller=True, analog_trigger_axes=[], trigger_axis_bindings=[])
    window.device.read = lambda: dict(state)
    window.device.available = [dict(state)]

    window.poll()
    app.processEvents()
    assert window.snapshot['instance_id'] == 41
    assert all(row.isHidden() for row in window.curve_rows.values())

    state.update(available_axes=list(range(6)), analog_trigger_axes=[4, 5],
                 trigger_axis_bindings=[4, 5], rumble=True)
    window.poll()
    app.processEvents()
    assert curve_capabilities(window.snapshot)['trigger_axes'] == [4, 5]
    assert not window.curve_rows['trigger'].isHidden()
    assert window.curve_rows['trigger'].control.isEnabled()
    assert not window.curve_rows['rumble'].isHidden()
    assert window.curve_rows['rumble'].control.isEnabled()
    assert window.curve_rows['trigger_rumble'].isHidden()

    state.update(available_axes=[], analog_trigger_axes=[], trigger_axis_bindings=[], rumble=False)
    window.poll()
    app.processEvents()
    assert all(row.isHidden() for row in window.curve_rows.values())
    assert all(not row.control.isEnabled() for row in window.curve_rows.values())

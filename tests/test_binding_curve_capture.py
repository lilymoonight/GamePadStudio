"""The mapping recorder must use the same trigger feel as execution."""
import pytest
from PySide6.QtWidgets import QApplication

from gamepadstudio.mapping_ui import BindingDialog
from gamepadstudio.response_curves import curve_preset
from tests.mapping_fixtures import MappingOwner


@pytest.mark.parametrize('preset,expected', [('linear', set()), ('sensitive', {'LT'}),
                                            ('precise', set())])
def test_binding_recorder_uses_current_device_left_trigger_curve(tmp_path, preset, expected):
    app = QApplication.instance() or QApplication([])
    owner = MappingOwner(tmp_path)
    owner.snapshot = {'device_key': 'pad:A', 'instance_id': 1, 'family': 'xbox',
                      'available_axes': list(range(6)), 'available_buttons': list(range(15)),
                      'axes': [0.] * 6, 'buttons': []}
    owner.store.activate_controller(owner.snapshot)
    owner.store.set_setting('trigger_curves', {'left': curve_preset(preset)}, owner.snapshot)
    dialog = BindingDialog(owner, mode='kbm')
    dialog.timer.stop()
    try:
        dialog.start_capture()
        dialog.poll()
        assert dialog.ready
        owner.snapshot['axes'][4:6] = [.4, .4]
        dialog.poll()
        assert dialog.best == expected
        owner.snapshot['axes'][4:6] = [0., 0.]
        dialog.poll()
        if expected:
            assert dialog.trigger() == 'LT'
        assert owner.config['device_settings']['pad:A']['trigger_curves']['right']['points'] == [0., .25, .5, .75, 1.]
    finally:
        dialog.close()
        owner.close()

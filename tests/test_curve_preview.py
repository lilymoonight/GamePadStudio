"""Preview drafts on one motor without changing persisted device settings."""
import copy
import threading
from types import SimpleNamespace

import pytest

from gamepadstudio.curve_preview import preview_response_curve
from gamepadstudio.response_curves import curve_preset, evaluate_curve


class PreviewDevice:
    def __init__(self):
        self.metadata = {'device_key': 'pad:A'}
        self.instance_id = 1
        self.settings = {}
        self.outputs = []
        self.reject = False

    def set_response_curves(self, settings):
        self.settings = copy.deepcopy(settings)

    def _output(self, kind, first, second, duration):
        if duration and self.reject:
            raise OSError('device disconnected')
        names = ('low', 'high') if kind == 'rumble' else ('left', 'right')
        curves = self.settings.get(kind + '_curves', {})
        values = [evaluate_curve(value, curves.get(name)) for name, value in zip(names, (first, second))]
        self.outputs.append((kind, *values, duration))
        return True

    def rumble_ext(self, first, second, duration):
        return self._output('rumble', first, second, duration)

    def rumble_triggers(self, first, second, duration):
        return self._output('trigger_rumble', first, second, duration)


def state(**changes):
    return {'device_key': 'pad:A', 'instance_id': 1, 'family': 'xbox',
            'rumble': True, 'trigger_rumble': True, **changes}


@pytest.mark.parametrize('kind,channel', [('rumble', 'low'), ('rumble', 'high'),
                                        ('trigger_rumble', 'left'), ('trigger_rumble', 'right')])
def test_preview_auditions_only_selected_channel_and_restores_settings(kind, channel):
    device = PreviewDevice()
    settings = {kind + '_curves': {channel: curve_preset('precise')}, 'trigger_rumble_enabled': False}
    original = copy.deepcopy(settings)
    device.set_response_curves(settings)
    assert preview_response_curve(device, settings, state(), kind, channel, curve_preset('sensitive'), .5)
    pulse = device.outputs[-1]
    expected = (.68, 0.) if channel in ('low', 'left') else (0., .68)
    assert pulse == (kind, *expected, 250)
    assert device.settings == settings == original


def test_preview_restores_settings_when_output_fails():
    device = PreviewDevice()
    settings = {'rumble_curves': {'low': curve_preset('precise')}}
    device.set_response_curves(settings)
    device.reject = True
    with pytest.raises(OSError):
        preview_response_curve(device, settings, state(), 'rumble', 'low', curve_preset('sensitive'))
    assert device.settings == settings


@pytest.mark.parametrize('replacement', [None, state(rumble=False, trigger_rumble=False)])
def test_unsupported_preview_sends_nothing(replacement):
    device = PreviewDevice()
    assert not preview_response_curve(device, {}, replacement, 'rumble', 'low', {})
    assert device.outputs == []


@pytest.mark.parametrize('replacement', [state(device_key='pad:B'), state(instance_id=2)])
def test_stale_device_snapshot_cannot_preview_on_new_controller(replacement):
    device = PreviewDevice()
    with pytest.raises(ValueError, match='输入设备已变化'):
        preview_response_curve(device, {}, replacement, 'rumble', 'low', {})
    assert device.outputs == []


def test_preview_cancels_delayed_feedback_without_closing_engine():
    device = PreviewDevice()
    cancellation = threading.Event()
    engine = SimpleNamespace(_state_lock=threading.RLock(), _pattern_cancel=cancellation, _closed=False)
    assert preview_response_curve(device, {}, state(), 'rumble', 'low', {}, haptic_engine=engine)
    assert cancellation.is_set()
    assert engine._pattern_cancel is not cancellation and not engine._pattern_cancel.is_set()


@pytest.mark.parametrize('kind,channel,strength', [('input', 'left', .5), ('rumble', 'left', .5),
                                                ('rumble', 'low', float('nan')),
                                                ('rumble', 'low', -1), ('rumble', 'low', True)])
def test_invalid_preview_requests_send_nothing(kind, channel, strength):
    device = PreviewDevice()
    with pytest.raises(ValueError):
        preview_response_curve(device, {}, state(), kind, channel, {}, strength)
    assert device.outputs == []

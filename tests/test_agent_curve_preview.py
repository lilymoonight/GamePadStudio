"""Background previews respect device identity and leave saved curves alone."""
import copy

import pytest
from PySide6.QtWidgets import QApplication

from gamepadstudio.agent import Agent
from gamepadstudio.response_curves import curve_preset
from gamepadstudio.studio_core import ConfigStore
from tests.test_agent import ActionsStub, DeviceStub
from tests.test_curve_preview import PreviewDevice, state


class AgentPreviewDevice(PreviewDevice, DeviceStub):
    def __init__(self):
        PreviewDevice.__init__(self)
        self.state = state(buttons=[], touch=[], led=False, axes=[0.] * 6)


@pytest.fixture
def running_agent(tmp_path):
    app = QApplication.instance() or QApplication([])
    device = AgentPreviewDevice()
    agent = Agent(tmp_path, device, ActionsStub())
    agent.timer.stop(); agent.scan_timer.stop(); agent.broadcast_timer.stop()
    agent.poll()
    yield agent, device
    agent.close()


def test_agent_draft_preview_does_not_persist_or_replace_config(running_agent):
    agent, device = running_agent
    agent.store.set_setting('rumble_curves', {'low': curve_preset('precise')}, agent.state)
    agent.store.save()
    agent.apply_device_settings()
    config = copy.deepcopy(agent.config)
    disk = agent.store.path.read_bytes()
    result = agent.handle({'command': 'preview_curve', 'kind': 'rumble', 'channel': 'high',
                           'curve': curve_preset('sensitive'), 'strength': .5,
                           'device_scope': 'pad:A', 'instance_id': 1})
    assert result == {'supported': True}
    assert device.outputs[-1] == ('rumble', 0., .68, 250)
    assert agent.config == config and agent.store.path.read_bytes() == disk
    assert ConfigStore(agent.root).data['device_settings'] == config['device_settings']


@pytest.mark.parametrize('scope,instance', [('pad:B', 1), ('pad:A', 2)])
def test_agent_rejects_stale_curve_preview_before_output(running_agent, scope, instance):
    agent, device = running_agent
    with pytest.raises(ValueError, match='输入设备已变化'):
        agent.handle({'command': 'preview_curve', 'kind': 'rumble', 'channel': 'low',
                      'curve': {}, 'device_scope': scope, 'instance_id': instance})
    assert device.outputs == []


def test_reconnect_with_trigger_already_pulled_requires_release(running_agent):
    agent, device = running_agent
    agent.store.mappings['LT'] = {'short': {'action': 'hold', 'value': 'Alt'},
                                 'long': {'action': 'none'}}
    agent.store.set_setting('trigger_curves', {'left': curve_preset('sensitive')}, agent.state)
    agent.store.save()
    agent.apply_device_settings()
    agent.poll()
    device.state = None
    agent.poll()
    device.state = state(buttons=[], touch=[], led=False, axes=[0., 0., 0., 0., .4, 0.])
    agent.poll()
    assert not agent.actions.held
    device.state['axes'][4] = 0.
    agent.poll()
    device.state['axes'][4] = .4
    agent.poll()
    assert agent.actions.held == {'Alt'}

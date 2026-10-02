"""Imported Windows keys must not shut down otherwise valid Mac mappings."""
import json
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication

from gamepadstudio.agent import Agent
from gamepadstudio.mapping_engine import MappingRuntime
from gamepadstudio.studio_core import ConfigStore
from tests.test_unified_mapping import Actions, entry, frame


class CapabilityActions(Actions):
    def __init__(self, fail_output=False):
        super().__init__()
        self.fail_output = fail_output

    @staticmethod
    def supports_key(value):
        return value not in ('PrintScreen', 'F24')

    @staticmethod
    def key_capability(value):
        return {'supported': False, 'reason': f'{value} 在此平台没有等效键盘输出'}

    def hold(self, value, down):
        if not self.supports_key(value):
            raise AssertionError('Unsupported output reached the native boundary')
        if self.fail_output and down:
            raise OSError('native post failed')
        super().hold(value, down)


class DeviceStub:
    available = []

    def __init__(self):
        self.state = dict(instance_id=8, family='dualsense', controller_type=0,
                          name='fixture', profile_key='dualsense:fixture',
                          device_key='dualsense:fixture:one', vendor=0, product=0,
                          buttons=[], axes=[0.] * 6, touch=[], touchpad=False,
                          led=False, rumble=False, power=-1,
                          available_buttons=list(range(16)))

    def scan(self):
        pass

    def read(self):
        return self.state

    def close(self):
        pass


def test_unsupported_output_keeps_other_binding_and_original_profile_intact():
    output = CapabilityActions()
    runtime = MappingRuntime(output, lambda *_: None, start_mouse=False)
    config = {'active_profile': 'imported', 'profiles': {'imported': {
        '0': entry('hold', 'PrintScreen'), '1': entry('hold', 'W')}}}
    runtime.update(frame(), config, now=0)
    assert runtime.feedback()['unsupported_bindings'] == [
        {'trigger': '0', 'gesture': 'short', 'value': 'PrintScreen',
         'reason': 'PrintScreen 在此平台没有等效键盘输出'}]
    runtime.update(frame([0, 1]), config, now=.1)
    assert output.keys[ord('W')] == 1
    assert not any(call[1] == 'PrintScreen' for call in output.calls)
    runtime.update(frame(), config, now=.2)
    assert output.keys[ord('W')] == 0
    assert config['profiles']['imported']['0']['short']['value'] == 'PrintScreen'
    config['profiles']['imported']['0'] = entry('hold', 'A')
    runtime.update(frame(), config, now=.3)
    assert runtime.feedback()['unsupported_bindings'] == []
    runtime.update(frame([0]), config, now=.4)
    assert output.keys[ord('A')] == 1


def test_unsupported_chord_still_consumes_its_components():
    output = CapabilityActions()
    runtime = MappingRuntime(output, lambda *_: None, start_mouse=False)
    config = {'active_profile': 'imported', 'profiles': {'imported': {
        '0': entry('hold', 'A'), '1': entry('hold', 'W'),
        '0+1': entry('shortcut', 'F24')}}}
    runtime.update(frame(), config, now=0)
    runtime.update(frame([0, 1]), config, now=.01)
    runtime.update(frame([0, 1]), config, now=.2)
    assert not output.calls
    runtime.update(frame(), config, now=.3)
    runtime.update(frame([1]), config, now=.4)
    runtime.update(frame([1]), config, now=.5)
    assert output.keys[ord('W')] == 1


def test_imported_unsupported_binding_notifies_once_and_agent_keeps_mapping(tmp_path):
    QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path)
    store.mappings.clear()
    store.mappings.update({'0': entry('hold', 'PrintScreen'), '1': entry('hold', 'W')})
    store.save()
    device, output = DeviceStub(), CapabilityActions()
    agent = Agent(tmp_path, device, output)
    agent.timer.stop(); agent.scan_timer.stop(); agent.broadcast_timer.stop()
    try:
        agent.poll()
        device.state['buttons'] = [0, 1]
        agent.poll()
        assert agent.enabled and agent.config['mapping_enabled']
        assert ConfigStore(tmp_path).data['mapping_enabled']
        assert output.keys[ord('W')] == 1
        assert agent.status()['mapping']['unsupported_bindings'][0]['value'] == 'PrintScreen'
        rows = [json.loads(line)['message'] for line in (tmp_path/'agent-events.jsonl').read_text().splitlines()]
        notices = [row for row in rows if '已跳过对应动作' in row]
        assert len(notices) == 1 and 'PrintScreen' in notices[0]
        agent.poll()
        rows = [json.loads(line)['message'] for line in (tmp_path/'agent-events.jsonl').read_text().splitlines()]
        assert sum('已跳过对应动作' in row for row in rows) == 1
    finally:
        agent.close()


def test_real_native_output_failure_still_pauses_mapping(tmp_path):
    QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path)
    store.mappings.clear()
    store.mappings['1'] = entry('hold', 'W')
    store.save()
    device = DeviceStub()
    output = CapabilityActions(fail_output=True)
    agent = Agent(tmp_path, device, output)
    agent.timer.stop(); agent.scan_timer.stop(); agent.broadcast_timer.stop()
    try:
        agent.poll()
        device.state['buttons'] = [1]
        agent.poll()
        assert not agent.enabled and not agent.config['mapping_enabled']
        assert not ConfigStore(tmp_path).data['mapping_enabled']
    finally:
        output.fail_output = False
        agent.close()

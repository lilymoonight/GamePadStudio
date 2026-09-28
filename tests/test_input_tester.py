import math
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from gamepadstudio.input_tester import InputTester, StickHistory
from gamepadstudio.controller_schematic import ControllerSchematic
from gamepadstudio.studio import Studio, STYLE
from gamepadstudio.agent import Agent
from gamepadstudio.studio_core import ConfigStore


def sample(family='switch', instance=1):
    return dict(instance_id=instance, family=family, controller_type={'switch':5, 'xbox':2, 'dualsense':7}[family],
                profile_key=f'fixture:{family}', name=f'TEST FIXTURE {family}', available_buttons=list(range(16)),
                buttons=[0, 9], axes=[-.025, .032, -.108, -.013, .25, 1.0],
                power=-1, led=False, rumble=False, touchpad=family=='dualsense', touch=[])


def test_sweep_needs_full_coverage_and_measures_radial_error():
    history = StickHistory()
    for i in range(36):
        angle = (i+.5)*math.tau/36
        history.add(.02*math.cos(angle), .02*math.sin(angle), True)
    assert history.coverage == 0 and history.error is None
    for i in range(35):
        angle = (i+.5)*math.tau/36
        history.add(math.cos(angle), math.sin(angle), True)
    assert history.error is None
    angle = 35.5*math.tau/36
    history.add(math.cos(angle), math.sin(angle), True)
    assert history.coverage == 1 and history.error == pytest.approx(0, abs=1e-10)
    history.clear()
    for i in range(36):
        angle = (i+.5)*math.tau/36
        history.add(.8*math.cos(angle), .8*math.sin(angle), True)
    assert history.error == pytest.approx(20)
    history.clear()
    for i in range(1000):
        history.add(math.cos(i), math.sin(i))
    assert len(history.points) == 600 and history.error is None


@pytest.mark.parametrize('family,names,triggers', [
    ('switch', ['B','A','Y','X'], ['ZL','ZR']),
    ('xbox', ['A','B','X','Y'], ['LT','RT']),
    ('dualsense', ['×','○','□','△'], ['L2','R2']),
])
def test_raw_values_labels_and_capabilities(family, names, triggers):
    app = QApplication.instance() or QApplication([])
    tester = InputTester(lambda: None)
    state = sample(family)
    tester.update_state(state)
    assert [tester.diagram.names[i].split()[0] for i in range(4)] == names
    assert tester.axis_names[4:] == triggers
    assert tester.snapshot()['raw_positions'] == [(-.025,.032),(-.108,-.013)]
    assert tester.snapshot()['triggers'] == [.25,1.0]
    assert tester.snapshot()['pressed'] == [0,9]
    assert tester.key_count.text() == '2 / 16'
    assert 20 not in tester.diagram.available
    state = {**state, 'available_buttons':list(range(15)), 'buttons':[15]}
    tester.update_state(state)
    assert 15 not in tester.diagram.available and tester.snapshot()['pressed'] == []
    tester.deleteLater()


def test_disconnect_and_device_switch_clear_state_and_trail():
    app = QApplication.instance() or QApplication([])
    tester = InputTester(lambda: None); tester.resize(824,506); tester.show()
    tester.sweep.setChecked(True); tester.update_state(sample())
    tester.update_state({**sample(), 'axes':[.8,.2,0.,-.9,0.,1.]})
    assert tester.histories[0].coverage > 0
    tester.update_state(None)
    assert tester.snapshot()['raw_positions'] == [None,None]
    assert tester.snapshot()['triggers'] == [None,None]
    assert tester.snapshot()['pressed'] == []
    assert all(not history.points and history.coverage == 0 for history in tester.histories)
    tester.update_state(sample('xbox',2))
    assert tester.snapshot()['family'] == 'xbox'
    assert len(tester.histories[0].points) == 1
    tester.reset()
    assert all(not history.points for history in tester.histories)
    tester.close()


def test_schematic_lights_only_pressed_available_controls():
    app = QApplication.instance() or QApplication([])
    diagram = ControllerSchematic(); diagram.resize(680,540); diagram.show()
    idle = {**sample('xbox'), 'buttons': [], 'axes': [0.,0.,0.,0.,0.,0.]}
    diagram.set_state(idle,[]); app.processEvents()
    # The face key at the south position lights up on the diagram itself.
    def green_pixels(image):
        return sum(image.pixelColor(x,y).green()-image.pixelColor(x,y).red()>30
                   for x in range(470,510) for y in range(235,280))
    before = green_pixels(diagram.grab().toImage())
    diagram.set_state({**idle, 'buttons': [0,15], 'available_buttons': list(range(15))},[])
    assert diagram.pressed == {0}
    diagram.set_state({**idle, 'buttons': [0]},[])
    app.processEvents()
    after = green_pixels(diagram.grab().toImage())
    assert after > before + 20
    diagram.close()


def test_vibration_controls_use_current_device_capability():
    app = QApplication.instance() or QApplication([])
    sent=[]
    tester=InputTester(lambda:None,sent.append)
    tester.update_state({**sample('xbox'),'rumble':True})
    assert all(button.isEnabled() for button in tester.rumble_buttons)
    tester.rumble_buttons[0].click();app.processEvents()
    assert sent == [.85]
    tester.update_state(None)
    assert not any(button.isEnabled() for button in tester.rumble_buttons)
    tester.rumble_pattern(.4,1);app.processEvents()
    assert sent == [.85]
    tester.deleteLater()


def test_small_window_instruments_paint_and_fit_with_sweep(tmp_path,monkeypatch):
    class DeviceFixture:
        available = []
        def scan(self): pass
        def read(self): return {**sample('dualsense'), 'available_buttons':list(range(21)), 'touch':[.25,.75]}
        def close(self): pass
    monkeypatch.setattr('gamepadstudio.studio.Device',DeviceFixture)
    app = QApplication.instance() or QApplication([]); app.setStyleSheet(STYLE)
    window = Studio(tmp_path,standalone=True)
    try:
        window.enabled=False;window.resize(960,640);window.navigate(3);window.show()
        window.config['deadzone'] = .9
        window.tester.sweep.setChecked(True); window.poll(); app.processEvents()
        assert window.tester.snapshot()['raw_positions'][0] == (-.025,.032)
        area = window.stack.widget(3)
        assert area.horizontalScrollBar().maximum() <= 20
        assert area.verticalScrollBar().maximum() <= 20
        # The complete schematic must paint, including the active face key.
        image = window.tester.diagram.grab().toImage()
        blues = sum(1 for x in range(0,image.width(),2) for y in range(0,image.height(),2)
                    if image.pixelColor(x,y).blue()-image.pixelColor(x,y).red()>80)
        assert blues > 20
    finally:
        window.cleanup();window.hide()


def test_test_protection_leases_agent_and_preserves_manual_pause(tmp_path,monkeypatch):
    class DeviceFixture:
        def __init__(self): self.state=sample(); self.state['buttons']=[]; self.available=[]
        def scan(self): pass
        def read(self): return self.state
        def close(self): pass
    class Actions:
        def release_all(self): pass
    app = QApplication.instance() or QApplication([])
    device=DeviceFixture();agent=Agent(tmp_path/'agent',device,Actions())
    captures=[];agent.capture=lambda:captures.append(True)
    monkeypatch.setattr('gamepadstudio.studio.request',lambda *args,**kwargs:{'ok':True})
    window=Studio(tmp_path/'ui')
    try:
        window.client.timer.stop();window.client.socket.abort()
        # Drive the real GUI lease path and real background dispatcher together.
        window.client.connected=True;window.client.status=agent.status()
        sent=[]
        def send(command,**values):
            sent.append(command);agent.handle(dict(command=command,**values));return True
        monkeypatch.setattr(window.client,'send',send)
        monkeypatch.setattr(window.device,'read',lambda:device.state)
        agent.poll();window.navigate(3);window.show();window.poll()
        assert window.testing_protected() and 'suspend' in sent
        assert agent.enabled and agent.status()['suspended']
        device.state['buttons']=[15];agent.poll()
        window.navigate(0);window.poll();agent.suspended_until=0;agent.poll()
        device.state['buttons']=[];agent.poll()
        assert captures == []
        device.state['buttons']=[15];agent.poll();device.state['buttons']=[];agent.poll()
        assert captures == [True]
        agent.handle({'command':'pause'});window.navigate(3);window.last_suspend=0;window.poll()
        window.close();agent.suspended_until=0;agent.poll()
        assert not agent.enabled
        assert 'resume' not in sent and 'pause' not in sent
    finally:
        window.cleanup();window.hide();agent.close()


def test_disconnected_pages_follow_saved_controller_family(tmp_path,monkeypatch):
    class OfflineDevice:
        available=[]
        def scan(self): pass
        def read(self): return None
        def close(self): pass
    monkeypatch.setattr('gamepadstudio.studio.Device',OfflineDevice)
    store=ConfigStore(tmp_path)
    store.data['profiles']['Switch Pro · 默认']={'15':{'short':{'action':'capture'},'long':{'action':'gallery'}}}
    store.data['profile_families']['Switch Pro · 默认']='switch'
    store.data['profiles']['Xbox · 默认']={}
    store.data['profile_families']['Xbox · 默认']='xbox'
    store.data['active_profile']='Switch Pro · 默认';store.save()
    app=QApplication.instance() or QApplication([])
    window=Studio(tmp_path,standalone=True)
    try:
        window.poll()
        assert window.art.family == window.mapping_art.family == 'switch'
        assert window.button_names[0]=='B' and window.capture_heading.text()=='Capture'
        assert window.mapping_boxes[15][0].isVisibleTo(window.mapping_boxes[15][0].parentWidget())
        window.change_profile('Xbox · 默认')
        assert window.art.family == window.mapping_art.family == 'xbox'
        assert window.button_names[0]=='A' and window.capture_heading.text()=='截图'
        assert window.mapping_boxes[15][0].isHidden()
    finally:
        window.cleanup();window.hide()

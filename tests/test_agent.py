import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication
from gamepadstudio.agent import Agent


class DeviceStub:
    def __init__(self):self.state=dict(buttons=[],touch=[],led=False)
    def scan(self):pass
    def read(self):return self.state
    def close(self):pass


class ActionsStub:
    def __init__(self):
        self.held=set()
        self.shortcuts=[]
    def hold(self,value,down):
        if down:self.held.add(value)
        else:self.held.discard(value)
    def shortcut(self,value):
        self.shortcuts.append(value)
    def release_all(self):self.held.clear()



def test_background_releases_keys_and_requires_fresh_press_after_resume(tmp_path):
    app=QApplication.instance() or QApplication([])
    device=DeviceStub(); actions=ActionsStub(); agent=Agent(tmp_path,device,actions)
    agent.store.mappings['0']={'short':{'action':'hold','value':'Enter'}}
    try:
        agent.poll();device.state['buttons']=[0];agent.poll();assert actions.held=={'Enter'}
        agent.handle({'command':'pause'});assert not actions.held
        agent.handle({'command':'resume'});agent.poll();assert not actions.held
        device.state['buttons']=[];agent.poll();device.state['buttons']=[0];agent.poll()
        assert actions.held=={'Enter'}
        device.state=None;agent.poll();assert not actions.held
    finally:agent.close()


def test_editor_lease_expires_without_delayed_capture(tmp_path):
    app=QApplication.instance() or QApplication([])
    device=DeviceStub();agent=Agent(tmp_path,device,ActionsStub());captures=[]
    agent.capture=lambda:captures.append(True)
    try:
        agent.poll();agent.handle({'command':'suspend','seconds':2})
        device.state['buttons']=[4];agent.poll()
        agent.suspended_until=0;agent.poll();device.state['buttons']=[];agent.poll()
        assert not captures
        device.state['buttons']=[4];agent.poll();device.state['buttons']=[];agent.poll()
        assert captures==[True]
    finally:agent.close()


def test_controller_switch_releases_old_keys_and_ignores_new_held_buttons(tmp_path):
    app=QApplication.instance() or QApplication([])
    device=DeviceStub();device.state.update(instance_id=1,family='dualsense',profile_key='ps')
    actions=ActionsStub();agent=Agent(tmp_path,device,actions);captures=[];agent.capture=lambda:captures.append(True)
    try:
        agent.poll();agent.store.mappings['0']={'short':{'action':'hold','value':'Enter'}}
        device.state['buttons']=[0];agent.poll();assert actions.held=={'Enter'}
        # Both old and new states are connected: identity, not bool(state), must detect the switch.
        device.state=dict(instance_id=2,family='xbox',profile_key='xb',buttons=[15],touch=[],led=False,available_buttons=list(range(16)))
        agent.poll();assert not actions.held and not captures
        device.state['buttons']=[];agent.poll();assert not captures
        device.state['buttons']=[15];agent.poll();device.state['buttons']=[];agent.poll();assert captures==[True]
    finally:agent.close()


def test_xbox_without_share_does_not_run_ps_create_or_home_actions(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([]);device=DeviceStub();captures=[];launches=[]
    device.state.update(instance_id=1,family='xbox',profile_key='xbox:one',available_buttons=list(range(15)))
    monkeypatch.setattr('gamepadstudio.agent.spawn',lambda *args:launches.append(args))
    agent=Agent(tmp_path,device,ActionsStub());agent.capture=lambda:captures.append(True)
    try:
        agent.poll()
        for key in range(15):
            device.state['buttons']=[key];agent.poll();device.state['buttons']=[];agent.poll()
        assert not captures and not launches and not agent.actions.held
        device.state={**device.state,'instance_id':2,'profile_key':'xbox:series','available_buttons':list(range(16))}
        agent.poll();device.state['buttons']=[15];agent.poll();device.state['buttons']=[];agent.poll()
        assert captures==[True] and not launches
    finally:agent.close()


def test_create_button_long_press_triggers_replay_record(tmp_path):
    app = QApplication.instance() or QApplication([])
    device = DeviceStub()
    device.state.update(instance_id=1, family='dualsense', profile_key='ps')
    actions = ActionsStub()
    agent = Agent(tmp_path, device, actions)
    captures = []
    agent.capture = lambda: captures.append(True)
    try:
        agent.poll()
        assert agent.store.mappings['4']['short']['action'] == 'capture'
        assert agent.store.mappings['4']['long']['action'] == 'replay_record'

        now = 100.0
        agent.engine.update({4}, agent.store.mappings, now)
        assert not captures and not actions.shortcuts
        # Exceed threshold (0.65s) -> fires long press
        agent.engine.update({4}, agent.store.mappings, now + 0.70)
        assert actions.shortcuts == ['Win+Alt+G']
        assert not captures

        # Release does not fire short
        agent.engine.update(set(), agent.store.mappings, now + 0.80)
        assert not captures
    finally:
        agent.close()


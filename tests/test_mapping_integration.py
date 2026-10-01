import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication
from gamepadstudio.agent import Agent
from gamepadstudio.studio import Studio
from gamepadstudio.studio_core import ConfigStore
from gamepadstudio.kbm_mapper import NIKKI_PROFILE_NAME
from tests.test_unified_mapping import Actions, entry


def test_remote_views_share_authoritative_profile_and_output(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([])
    class Device:
        available=[]
        state=None
        def scan(self):pass
        def read(self):return self.state
        def close(self):pass
    backend_actions=Actions();agent=Agent(tmp_path,Device(),backend_actions)
    agent.timer.stop();agent.scan_timer.stop();agent.broadcast_timer.stop()
    ui_actions=Actions()
    class Client(QObject):
        event=Signal(dict)
        def __init__(self,root,parent=None):
            super().__init__(parent);self.connected=True;self.status=agent.status();self.state=None
        def send(self,command,**kwargs):agent.handle({'command':command,**kwargs});return True
        def close(self):pass
    monkeypatch.setattr('gamepadstudio.studio.AgentClient',Client)
    monkeypatch.setattr('gamepadstudio.studio.WindowsActions',lambda:ui_actions)
    monkeypatch.setattr('gamepadstudio.studio.request',lambda root,command,**kw: {'ok':True,**agent.handle({'command':command,**{k:v for k,v in kw.items() if k not in ('role','timeout')}})})
    window=Studio(tmp_path);window.timer.stop();window.scan_timer.stop();window.gallery_timer.stop()
    try:
        assert window.engine.mouse_thread is None
        window.virtual_kbm_page.scheme_combo.setCurrentText(NIKKI_PROFILE_NAME)
        window.virtual_kbm_page.activate_current_scheme()
        assert agent.config['active_profile']==window.profile_combo.currentText()==window.virtual_kbm_page.scheme_combo.currentText()==NIKKI_PROFILE_NAME
        assert NIKKI_PROFILE_NAME not in [window.mapping_combo.itemText(i) for i in range(window.mapping_combo.count())]
        window.mapping_change({'op':'binding','trigger':'LB+0','mapping':entry('shortcut','Ctrl+S','mouse_hold','left')})
        assert ConfigStore(tmp_path).mappings['0+9']['long']['value']=='left'
        assert '0+9' in window.virtual_kbm_page.bindings.rows
        state=dict(instance_id=8,family='dualsense',controller_type=0,name='test fixture',profile_key='test',
                   buttons=[0,9],axes=[0.]*6,led=False,rumble=False,power=-1,touchpad=False,touch=[],available_buttons=list(range(16)))
        agent.state=state
        agent.engine.update(state,agent.config,now=0);agent.engine.update(state,agent.config,now=.7)
        window.client.status=agent.status();window.client.state=state;window.poll()
        assert window.virtual_kbm_page.keycaps['mouse:left'].is_pressed
        assert '鼠标左键' in window.home_input_feedback.text()
        assert backend_actions.mouse=={'left'} and not ui_actions.calls
        agent.engine.update(state,agent.config,enabled=False,now=.8)
        window.client.status=agent.status();window.poll()
        assert not window.virtual_kbm_page.keycaps['mouse:left'].is_pressed
        assert not backend_actions.mouse
        window.change_profile('主机体验')
        assert agent.config['active_profile']==window.profile_combo.currentText()==window.mapping_combo.currentText()=='主机体验'
        assert '主机体验' not in [window.virtual_kbm_page.scheme_combo.itemText(i) for i in range(window.virtual_kbm_page.scheme_combo.count())]
    finally:window.cleanup();window.hide();agent.close()


def test_moving_a_binding_is_atomic_and_does_not_leave_old_shortcut(tmp_path):
    store=ConfigStore(tmp_path)
    store.apply_mapping_change({'op':'binding','trigger':'0+9','mapping':entry('hold','Ctrl+S')})
    store.apply_mapping_change({'op':'binding','trigger':'1+9','previous_trigger':'0+9','mapping':entry('hold','Ctrl+S')})
    loaded=ConfigStore(tmp_path)
    assert loaded.mappings['0+9']['short']['action']=='none'
    assert loaded.mappings['1+9']['short']['value']=='Ctrl+S'

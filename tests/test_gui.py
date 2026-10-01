import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from gamepadstudio.studio import Studio, STYLE, MappingDialog
from gamepadstudio.kbm_mapper import NIKKI_PROFILE_NAME


def test_remote_gui_signal_connection_and_close(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr('gamepadstudio.studio.request',lambda *a,**kw:{'ok':True})
    window=Studio(tmp_path);window.show();app.processEvents()
    assert window.remote and window.client is not None
    window.close();app.processEvents();assert window.closed


def test_controller_refresh_keeps_shared_settings_store(tmp_path, monkeypatch):
    from gamepadstudio.studio_core import ConfigStore
    monkeypatch.setattr('gamepadstudio.studio.request', lambda *a, **kw: {'ok':True})
    monkeypatch.setattr('gamepadstudio.ipc.request', lambda *a, **kw: {'ok':True})
    app = QApplication.instance() or QApplication([])
    window = Studio(tmp_path)
    try:
        original_store = window.store
        fresh = ConfigStore(tmp_path)
        fresh.data['gamebar_shield_enabled'] = True
        fresh.save()
        window.update_controller_ui(dict(instance_id=1, family='xbox', controller_type=2,
                                         name='Fixture', available_buttons=list(range(16)),
                                         buttons=[], axes=[0.]*6, led=False, rumble=False,
                                         touchpad=False, touch=[], power=-1))
        assert window.store is original_store is window.virtual_kbm_page.store
        window.store.save()
        assert ConfigStore(tmp_path).data['gamebar_shield_enabled'] is True
    finally:
        window.cleanup(); window.hide()


def test_gui_pages_capture_and_persistent_mapping(tmp_path,monkeypatch):
    class Disconnected:
        available=[]
        def scan(self):pass
        def read(self):return None
        def close(self):pass
    monkeypatch.setattr('gamepadstudio.studio.Device',Disconnected)
    app=QApplication.instance() or QApplication([]); QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc'); app.setStyleSheet(STYLE)
    window=Studio(tmp_path,standalone=True); window.show(); app.processEvents()
    assert window.snapshot is None or 'name' in window.snapshot
    for index in range(6):
        window.navigate(index); app.processEvents()
        assert window.stack.currentIndex()==index
    native_profile=window.config['active_profile']
    window.store.apply_mapping_change({'op':'binding','trigger':'4','mapping':{
        'short':{'action':'capture'},'long':{'action':'replay_record'}}},None)
    dialog=MappingDialog(window,4,window.store.mappings['4'])
    assert dialog.value()['short']['action']=='capture'
    assert dialog.value()['long']['action']=='replay_record'
    window.change_profile(NIKKI_PROFILE_NAME)
    assert window.store.mappings['0']['short']['value']=='Space'
    window.change_profile(native_profile)
    window.toggle_pause(); assert not window.enabled
    window.toggle_pause(); assert window.enabled
    window.cleanup(); window.quitting=True; window.hide()


def test_gallery_filters_favorites_and_device_specific_capabilities(tmp_path,monkeypatch):
    class XboxStub:
        def __init__(self):
            self.state=dict(instance_id=10,family='xbox',controller_type=2,profile_key='test-xbox',name='Xbox test fixture',
                            available_buttons=list(range(16)),buttons=[],axes=[0.]*6,led=False,rumble=True,touchpad=False,touch=[],power=-1)
            self.available=[{**self.state,'supported':True}]
        def scan(self):pass
        def read(self):return self.state
        def close(self):pass
    monkeypatch.setattr('gamepadstudio.studio.Device',XboxStub)
    app=QApplication.instance() or QApplication([]);window=Studio(tmp_path,standalone=True)
    try:
        window.enabled=False;window.show();window.poll();app.processEvents()
        assert window.button_names[0]=='A' and window.capture_heading.text()=='Share'
        assert window.tester.axis_names[-2:]==['LT','RT']
        assert '主机体验' not in [window.mapping_combo.itemText(i) for i in range(window.mapping_combo.count())]
        assert '4' not in window.store.mappings and '5' not in window.store.mappings
        window.navigate(1);app.processEvents()
        QTest.mouseClick(window.mapping_boxes[2][0],Qt.LeftButton)
        assert window.selected_key==2 and window.mapping_deck.selected_trigger=='2' and window.mapping_art.selected==2
        assert all(not button.isEnabled() for button in window.led_buttons)
        assert not window.touch_mouse_box.isEnabled() and window.feedback_rumble.isEnabled()
        assert window.mapping_boxes[20][0].isHidden()
        gallery=window.controllers;assert gallery.grid.count()==1
        assert gallery.current_family()=='xbox'
        assert gallery.grid.itemAt(0).widget().accessibleName()=='Xbox test fixture'
        gallery.search.setText('Xbox');assert gallery.grid.count()==1
        gallery.search.clear();gallery.filter.setCurrentIndex(1);assert gallery.grid.count()==1
        gallery.toggle_favorite('xbox');gallery.filter.setCurrentIndex(2);assert gallery.grid.count()==1
        assert not gallery.empty_state
        from gamepadstudio.studio_core import ConfigStore
        assert ConfigStore(tmp_path).data['controller_favorites']==['xbox']
        window.navigate(5);window.resize(960,640);QTest.qWait(80);assert gallery.columns==2
        assert gallery.grid.itemAt(0).widget().isVisible()
    finally:window.cleanup();window.hide()


def test_xbox_photo_clicks_match_official_face_and_stick_positions():
    from gamepadstudio.controller_photo import ControllerInput
    from PySide6.QtCore import QPoint
    app=QApplication.instance() or QApplication([]);photo=ControllerInput();photo.set_family('xbox');photo.available=set(range(16));clicked=[]
    photo.button_clicked.connect(clicked.append)
    # Independently marked centers in the original 1200px Microsoft asset.
    centers={0:(840,520),1:(902,454),2:(776,452),3:(842,394),4:(532,454),6:(668,454),7:(362,450),8:(721,593),15:(600,506)}
    try:
        for width,height in [(640,420),(340,510)]:
            photo.resize(width,height);photo.show();app.processEvents();rect=photo.product_rect()
            for key,(x,y) in centers.items():
                point=QPoint(round(rect.left()+(x-45)/1060*rect.width()),round(rect.top()+(y-272)/680*rect.height()))
                QTest.mouseClick(photo,Qt.LeftButton,pos=point)
                assert clicked[-1]==key
        # A driver with no Share does not offer a clickable phantom button.
        photo.available=set(range(15));before=len(clicked)
        x,y=centers[15];point=QPoint(round(rect.left()+(x-45)/1060*rect.width()),round(rect.top()+(y-272)/680*rect.height()))
        QTest.mouseClick(photo,Qt.LeftButton,pos=point);assert len(clicked)==before
    finally:photo.close()


def test_icon_navigation_pause_and_child_window_close(tmp_path):
    from gamepadstudio.glass import IconButton
    app=QApplication.instance() or QApplication([]);window=Studio(tmp_path,standalone=True)
    try:
        window.timer.stop();window.show();QTest.qWait(30)
        for page in (1,2,3,4,5,0):
            QTest.mouseClick(window.nav[page],Qt.LeftButton)
            assert window.stack.currentIndex()==page and window.nav[page].isChecked()
        for control in window.findChildren(IconButton):
            assert control.accessibleName() and control.toolTip() and not control.icon().isNull()
        original=window.enabled
        QTest.mouseClick(window.pause_button,Qt.LeftButton)
        assert window.enabled!=original
        assert window.pause_button.symbol==('pause' if window.enabled else 'play')
        QTest.mouseClick(window.pause_button,Qt.LeftButton)
        assert window.enabled==original
        window.navigate(5);QTest.qWait(30)
        original_card=window.controllers.grid.itemAt(0).widget()
        for width in (960,1200,960):
            window.navigate(4);window.resize(width,640);window.navigate(5);QTest.qWait(30)
            assert window.controllers.grid.itemAt(0).widget() is original_card
            assert original_card.isVisible() and original_card.geometry().width()>0
            assert window.controllers.columns==(2 if width==960 else 3)
        window.events.show();app.processEvents();assert window.events.isVisible()
        window.close();app.processEvents()
        assert window.closed and not window.events.isVisible()
    finally:window.cleanup();window.hide()


def test_controller_card_favorite_does_not_open_and_keyboard_opens():
    from gamepadstudio.controller_gallery import ControllerGallery
    from gamepadstudio.glass import IconButton
    app=QApplication.instance() or QApplication([]);opened=[];saved=[]
    gallery=ControllerGallery(lambda i:None,saved.append,lambda:None,lambda:opened.append('manage'))
    gallery.set_devices([dict(instance_id=7,name='Fixture DualSense',family='dualsense',supported=True)],7)
    gallery.resize(1050,680);gallery.show();QTest.qWait(30)
    try:
        assert gallery.grid.count()==1
        box=gallery.grid.itemAt(0).widget()
        assert box.accessibleName()=='Fixture DualSense'
        heart=next(b for b in box.findChildren(IconButton) if b.symbol=='heart')
        QTest.mouseClick(heart,Qt.LeftButton);app.processEvents()
        assert saved==[['dualsense']] and opened==[]
        box=gallery.grid.itemAt(0).widget();box.setFocus();QTest.keyClick(box,Qt.Key_Return)
        assert opened==['manage']
    finally:gallery.close()


def test_delete_buttons_interactive_and_responsive(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from gamepadstudio.screenshot_service import take_screenshot
    class Disconnected:
        available = []
        def scan(self): pass
        def read(self): return None
        def close(self): pass
    monkeypatch.setattr('gamepadstudio.studio.Device', Disconnected)
    app = QApplication.instance() or QApplication([])
    window = Studio(tmp_path, standalone=True)
    window.enabled = False

    # Mock QMessageBox to verify dialog is triggered without blocking
    questions = []
    infos = []
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: (questions.append(a), QMessageBox.No)[1])
    monkeypatch.setattr(QMessageBox, 'information', lambda *a, **kw: (infos.append(a), QMessageBox.Ok)[1])

    try:
        window.show(); app.processEvents()

        # 1. Profile delete button: responsive and prompts user
        window.navigate(1)
        app.processEvents()
        assert hasattr(window, 'delete_profile_btn')
        window.delete_profile_btn.click()
        assert len(questions) + len(infos) == 1

        # 2. Screenshot gallery delete buttons: responsive on each card
        take_screenshot(window.config['save_dir'])
        window.gallery_signature = None
        window.navigate(2)
        app.processEvents()
        del_btns = [b for b in window.gallery_content.findChildren(object) if getattr(b, 'toolTip', lambda: '')() == '删除截图']
        assert len(del_btns) >= 1
        q_before = len(questions)
        del_btns[0].click()
        assert len(questions) == q_before + 1

        # 3. Clean unfavorited button: responsive
        c_before = len(questions)
        window.clean_captures_btn.click()
        assert len(questions) == c_before + 1
    finally:
        window.cleanup(); window.hide()

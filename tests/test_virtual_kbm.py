import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import time
import sys
import pytest
from PySide6.QtWidgets import QApplication, QScrollArea
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from gamepadstudio.virtual_kbm import VirtualMouseThread, NIKKI_PRESET_CONFIG, GENERAL_PRESET_CONFIG
from gamepadstudio.virtual_kbm_ui import VirtualKbmPage, TYPING_BLOCK_ROWS, NAV_BLOCK_ROWS, NUMPAD_BLOCK_GRID
from gamepadstudio.mapping_ui import BindingDialog, KeySequenceField
from gamepadstudio.mapping_engine import convert_scheme, validate_mappings, output_tokens
from tests.mapping_fixtures import MappingOwner


@pytest.fixture
def view(tmp_path):
    app=QApplication.instance() or QApplication([])
    owner=MappingOwner(tmp_path); page=VirtualKbmPage(owner); owner.page=page
    yield owner,page
    page.close();owner.close()


def test_all_legacy_preset_bindings_are_valid_and_explicit():
    for scheme in (NIKKI_PRESET_CONFIG,GENERAL_PRESET_CONFIG):
        mapping=validate_mappings(convert_scheme(scheme))
        assert mapping['LS:up']['short']['value']=='W'
        assert mapping['LS:outer']['short']['value']=='Shift'
        assert mapping['RT']['short']['action']=='mouse_hold'
        assert '0+9' in mapping


def test_continuous_mouse_stops_and_thread_joins():
    class Actions:
        moves=[]
        def move_mouse(self,x,y): self.moves.append((x,y))
    a=Actions();thread=VirtualMouseThread(a);thread.start()
    try:
        thread.update_stick(1,0,is_desktop=True)
        deadline=time.monotonic()+1
        while not a.moves and time.monotonic()<deadline: time.sleep(.005)
        assert sum(x for x,y in a.moves)>0
        thread.update_stick(0,0);time.sleep(.02);before=len(a.moves);time.sleep(.02)
        assert len(a.moves)==before
    finally: thread.stop();thread.join(timeout=1)
    assert not thread.is_alive()


def test_fixed_108_key_panel_and_valid_numpad_keys(view):
    owner,page=view
    count=sum(1 for rows in (TYPING_BLOCK_ROWS,NAV_BLOCK_ROWS) for row in rows for key,*_ in row if not key.startswith('__'))+len(NUMPAD_BLOCK_GRID)
    assert count==108
    assert len(page.keycaps)==116
    for key in page.keycaps:
        if key!='Fn': assert page.key_token(key)
    assert page.findChildren(QScrollArea)


def test_clicking_keyboard_or_mouse_uses_same_binding_editor(view):
    owner,page=view
    # An empty user-created preset exercises adding outputs as well as editing
    # existing bindings without relying on shipped preset contents.
    owner.config['profiles']['Editor keyboard fixture'] = {}
    owner.config['profile_modes']['Editor keyboard fixture'] = 'kbm'
    owner.config['profile_devices']['Editor keyboard fixture'] = 'offline:xinput'
    owner.change_profile('Editor keyboard fixture')
    page.keycaps['Space'].left_clicked.emit('Space','Space')
    assert owner.edits[-1]==(('0',),{'new':True,'output':'Space','profile':'Editor keyboard fixture','mode':'kbm'})
    page.keycaps['mouse:left'].left_clicked.emit('mouse:left','left')
    assert owner.edits[-1]==(('0',),{'new':True,'output':'mouse:left','profile':'Editor keyboard fixture','mode':'kbm'})
    owner.mapping_change({'op':'binding','trigger':'0+9','mapping':{'short':{'action':'hold','value':'Ctrl+Space'}}})
    page.edit_output('Space','Space')
    assert owner.edits[-1]==(('0+9',),{'profile':'Editor keyboard fixture','mode':'kbm'})
    assert page.keycaps['Ctrl'].badges and page.keycaps['Space'].badges


def test_actual_output_feedback_is_distinct_from_binding_badges_and_clears(view):
    owner,page=view
    owner.mapping_change({'op':'binding','trigger':'0','mapping':{'short':{'action':'hold','value':'Ctrl+Space'}}})
    assert page.keycaps['Space'].badges and not page.keycaps['Space'].is_pressed
    page.update_feedback({'outputs':['key:17','key:32','mouse:left'],'active':['0']},True)
    assert page.keycaps['Space'].is_pressed and page.keycaps['Ctrl'].is_pressed and page.keycaps['mouse:left'].is_pressed
    assert page.bindings.active=={'0'}
    page.update_feedback({},False)
    assert not any(cap.is_pressed for cap in page.keycaps.values())


@pytest.mark.parametrize('first,second',[(9,0),(0,9),(10,12),(12,10)])
def test_editor_captures_simultaneous_chord_regardless_of_order(view,first,second):
    owner,page=view
    dialog=BindingDialog(owner,'0',{},output='Ctrl+S'); dialog.timer.stop()
    try:
        dialog.start_capture();owner.snapshot={'buttons':[]};dialog.poll()
        owner.snapshot={'buttons':[first]};dialog.poll()
        owner.snapshot={'buttons':[first,second]};dialog.poll()
        owner.snapshot={'buttons':[second]};dialog.poll()
        owner.snapshot={'buttons':[]};dialog.poll()
        assert dialog.trigger()=='+'.join(map(str,sorted((first,second))))
        assert not dialog.capturing
        assert dialog.value()['short']['value']=='Ctrl+S'
    finally: dialog.close()


def test_editor_does_not_merge_sequential_inputs_and_captures_trigger(view):
    owner,page=view;dialog=BindingDialog(owner);dialog.timer.stop()
    try:
        dialog.start_capture();owner.snapshot={'buttons':[]};dialog.poll()
        owner.snapshot={'buttons':[0]};dialog.poll()
        owner.snapshot={'buttons':[9]};dialog.poll()
        owner.snapshot={'buttons':[]};dialog.poll()
        assert dialog.trigger()=='0'
        dialog.start_capture();dialog.poll()
        owner.snapshot={'buttons':[0],'axes':[0,0,0,0,0,.8]};dialog.poll()
        owner.snapshot={'buttons':[]};dialog.poll()
        assert dialog.trigger()=='0+RT'
    finally:dialog.close()


def test_keyboard_capture_supports_modifiers_and_two_ordinary_keys(view):
    owner,page=view;field=KeySequenceField();field.start_recording()
    QTest.keyPress(field,Qt.Key_Control);QTest.keyPress(field,Qt.Key_Shift);QTest.keyPress(field,Qt.Key_S)
    QTest.keyRelease(field,Qt.Key_S);QTest.keyRelease(field,Qt.Key_Shift);QTest.keyRelease(field,Qt.Key_Control)
    assert field.text()==('Cmd+Shift+S' if sys.platform == 'darwin' else 'Ctrl+Shift+S') and not field.recording
    if sys.platform == 'darwin':
        field.start_recording()
        QTest.keyPress(field, Qt.Key_Meta)
        QTest.keyRelease(field, Qt.Key_Meta)
        assert field.text() == 'Ctrl'
    field.start_recording();QTest.keyPress(field,Qt.Key_W);QTest.keyPress(field,Qt.Key_Space)
    QTest.keyRelease(field,Qt.Key_W);QTest.keyRelease(field,Qt.Key_Space)
    assert field.text()=='W+Space'


def test_long_hold_can_be_edited_and_all_selectors_share_profile(view):
    owner,page=view
    dialog=BindingDialog(owner,'LT',{'short':{'action':'hold','value':'Ctrl+S'},'long':{'action':'hold','value':'Shift+W'}})
    try:
        assert dialog.value()['long']=={'action':'hold','value':'Shift+W'}
        assert dialog.value()['short']=={'action':'hold','value':'Ctrl+S'}
        from gamepadstudio.kbm_mapper import NIKKI_PROFILE_NAME
        page.scheme_combo.setCurrentText(NIKKI_PROFILE_NAME)
        page.activate_current_scheme()
        assert owner.config['active_profile']==NIKKI_PROFILE_NAME
        assert owner.store.mappings['0']['short']['value']=='Space'
    finally:dialog.close()


def test_mouse_stick_unblocked_in_preview_and_auto_desktop_mode():
    from gamepadstudio.mapping_engine import MappingRuntime
    class MockActions:
        def __init__(self):
            self.moves = []
        def move_mouse(self, dx, dy):
            self.moves.append((dx, dy))
        def is_nikki_game_focused(self):
            return False

    actions = MockActions()
    runtime = MappingRuntime(actions, lambda *_: None, start_mouse=True)
    try:
        config = {
            'active_profile': 'test_p',
            'profile_options': {'test_p': {'right_stick_mouse': True, 'mouse': {'mode': 'game'}}},
            'profiles': {'test_p': {}},
        }
        state = {'axes': [0.0, 0.0, 0.75, 0.5, 0.0, 0.0], 'buttons': []}

        # 即使 preview=True，摇杆移动鼠标也绝不能被阻断
        runtime.update(state, config, enabled=True, preview=True)
        assert runtime.mouse_thread.stick_x == 0.75
        assert runtime.mouse_thread.stick_y == 0.5
        # 非游戏窗口前台时，自动激活桌面指针模式 (is_desktop=True)
        assert runtime.mouse_thread.is_desktop is True
    finally:
        runtime.mouse_thread.stop()
        runtime.mouse_thread.join(timeout=1.0)


def test_keycap_short_and_long_press_text_distinction(view):
    owner, page = view
    cap = page.keycaps['Space']
    cap.set_mapping_info([
        {'trigger': 'A', 'gesture': 'short'},
        {'trigger': 'LB', 'gesture': 'long'},
    ], capturing=False)

    assert 'A' in cap.short_bindings
    assert 'LB' in cap.long_bindings
    assert '短' in cap.badges[0]
    assert '长' in cap.badges[1]

    # 验证键帽重绘无异常
    cap.repaint()


def test_canvas_dynamic_key_size_scaling(view):
    owner, page = view
    cap_space = page.keycaps['Space']
    old_w = cap_space.width()

    # 模拟视口放大至 4K/超宽屏
    page.recompute_key_sizes(72, 68)
    assert cap_space.width() > old_w
    assert cap_space.height() == 68

    # 模拟视口缩小
    page.recompute_key_sizes(46, 44)
    assert cap_space.width() < old_w
    assert cap_space.height() == 44


def test_binding_list_collapsed_by_default_to_maximize_keyboard_space(view):
    owner, page = view
    # 默认折叠隐藏，主区域留给全画幅键盘
    assert page.bindings.isHidden()
    page.toggle_bindings_list()
    assert not page.bindings.isHidden()
    page.toggle_bindings_list()
    assert page.bindings.isHidden()

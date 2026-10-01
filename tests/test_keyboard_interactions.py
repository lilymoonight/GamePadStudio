import copy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from gamepadstudio.kbm_mapper import NIKKI_PROFILE_NAME
from gamepadstudio.studio_core import ConfigStore
from gamepadstudio.virtual_kbm_ui import VirtualKbmPage, trigger_tokens
from tests.mapping_fixtures import MappingOwner


@pytest.fixture
def keyboard_view(tmp_path):
    app = QApplication.instance() or QApplication([])
    owner = MappingOwner(tmp_path)
    owner.config['profiles'][NIKKI_PROFILE_NAME] = {
        '0': {'short': {'action': 'hold', 'value': 'Space'}},
        '9+10': {'long': {'action': 'hold', 'value': 'Ctrl+Space'}},
        '1': {'short': {'action': 'hold', 'value': 'Enter'}},
        'LS:up': {'short': {'action': 'hold', 'value': 'W'}},
    }
    owner.store.save()
    page = VirtualKbmPage(owner)
    owner.page = page
    yield owner, page
    page.close()
    owner.close()


def test_right_click_unbinds_matching_triggers_and_preserves_presets(keyboard_view, monkeypatch):
    owner, page = keyboard_view
    original = copy.deepcopy(owner.config)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Yes)

    QTest.mouseClick(page.keycaps['Space'], Qt.RightButton)

    assert owner.changes == [
        {'op': 'unbind', 'trigger': '0', 'profile': NIKKI_PROFILE_NAME},
        {'op': 'unbind', 'trigger': '9+10', 'profile': NIKKI_PROFILE_NAME},
    ]
    assert set(owner.config['profiles']) == set(original['profiles'])
    assert owner.config['active_profile'] == original['active_profile']
    assert owner.config['profiles'][original['active_profile']] == original['profiles'][original['active_profile']]
    nikki = owner.config['profiles'][NIKKI_PROFILE_NAME]
    for trigger in ('0', '9+10'):
        assert nikki[trigger] == {'short': {'action': 'none'}, 'long': {'action': 'none'}}
    for trigger in ('1', 'LS:up'):
        assert nikki[trigger] == original['profiles'][NIKKI_PROFILE_NAME][trigger]
    assert not page.keycaps['Space'].badges
    assert page.keycaps['Enter'].badges

    reloaded = ConfigStore(owner.store.root)
    assert reloaded.data['profiles'] == owner.config['profiles']
    assert reloaded.data['profile_options'][NIKKI_PROFILE_NAME] == original['profile_options'][NIKKI_PROFILE_NAME]


def test_cancelled_right_click_keeps_bindings(keyboard_view, monkeypatch):
    owner, page = keyboard_view
    original = copy.deepcopy(owner.config['profiles'])
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.No)

    QTest.mouseClick(page.keycaps['Space'], Qt.RightButton)

    assert not owner.changes
    assert owner.config['profiles'] == original


def test_keyboard_existing_binding_editor_has_explicit_profile(keyboard_view):
    owner, page = keyboard_view
    page.keycaps['Enter'].left_clicked.emit('Enter', 'Enter')

    assert owner.edits[-1] == (('1',), {'profile': NIKKI_PROFILE_NAME, 'mode': 'kbm'})
    assert owner.config['active_profile'] != NIKKI_PROFILE_NAME


def test_keyboard_new_binding_editor_has_explicit_profile(keyboard_view):
    owner, page = keyboard_view
    page.keycaps['F12'].left_clicked.emit('F12', 'F12')

    assert owner.edits[-1] == (('0',), {
        'new': True, 'output': 'F12', 'profile': NIKKI_PROFILE_NAME, 'mode': 'kbm',
    })
    assert owner.config['active_profile'] != NIKKI_PROFILE_NAME


@pytest.mark.parametrize('choice', [0, 2])
def test_keyboard_multi_binding_editor_has_explicit_profile(keyboard_view, monkeypatch, choice):
    owner, page = keyboard_view
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *args: (args[3][choice], True))

    page.edit_output('Space', 'Space')

    assert owner.edits[-1] == (('0' if choice == 0 else '9+10',), {
        'new': choice == 0, 'output': 'Space' if choice == 0 else None,
        'profile': NIKKI_PROFILE_NAME, 'mode': 'kbm',
    })
    assert owner.config['active_profile'] != NIKKI_PROFILE_NAME


def test_keyboard_preselection_waits_for_explicit_activation(keyboard_view):
    owner, page = keyboard_view
    active = owner.config['active_profile']

    assert page.scheme_combo.currentText() == NIKKI_PROFILE_NAME
    assert page.current_scheme() == NIKKI_PROFILE_NAME
    assert page.scheme_activate_btn.isEnabled()
    assert owner.config['active_profile'] == active
    page.scheme_combo.setCurrentIndex(-1)
    assert page.current_scheme() == NIKKI_PROFILE_NAME
    page.refresh_display()
    assert page.scheme_combo.currentText() == NIKKI_PROFILE_NAME
    assert owner.config['active_profile'] == active

    page.activate_current_scheme()
    assert owner.config['active_profile'] == NIKKI_PROFILE_NAME


@pytest.mark.parametrize('raw, expected', [
    ('×  交叉 + L1', ('L1', '×')),
    ('R3 + L1', ('L1', 'R3')),
    ('A + LB + LT', ('LB', 'LT', 'A')),
    ('左摇杆↑ + R1', ('R1', 'LS↑')),
])
def test_chord_tokens_promote_modifiers_without_merging_members(raw, expected):
    assert trigger_tokens(raw) == expected


def test_compact_chord_preserves_members_and_full_tooltip(keyboard_view):
    _, page = keyboard_view
    cap = page.keycaps['1']
    raw = '×  交叉 + L1 + R1 + L2'
    cap.set_mapping_info([{'trigger': raw, 'gesture': 'long'}])

    plan = cap.binding_display_plan(23, 22)

    assert len(plan) == 1 and plan[0]['kind'] == 'binding'
    assert plan[0]['tokens'] == ('L1', 'R1', 'L2', '×')
    assert plan[0]['stacked'] and plan[0]['long']
    assert raw in cap.toolTip()
    assert '1 项绑定' in cap.toolTip()
    assert cap.long_bindings == [raw]


@pytest.mark.parametrize('width', [23, 58, 100, 250])
def test_binding_overflow_counts_bindings_not_chord_members(keyboard_view, width):
    _, page = keyboard_view
    cap = page.keycaps['Space']
    cap.set_mapping_info([
        {'trigger': 'L1 + ×  交叉', 'gesture': 'short'},
        {'trigger': 'L1 + ×  交叉', 'gesture': 'short'},
        {'trigger': 'L1 + ×  交叉', 'gesture': 'long'},
        {'trigger': 'L2 + R1 + □  方块', 'gesture': 'short'},
        {'trigger': 'R3', 'gesture': 'short'},
    ])

    plan = cap.binding_display_plan(width, 24)

    assert sum(item['count'] if item['kind'] == 'count' else 1 for item in plan) == 4
    assert sum(item['width'] for item in plan) + 8 * (len(plan) - 1) <= width
    assert '4 项绑定' in cap.toolTip()
    if width == 23:
        assert plan == [{'kind': 'count', 'count': 4, 'overflow': False, 'width': 23}]
    if width == 250:
        assert len(plan) == 4 and all(item['kind'] == 'binding' for item in plan)
        assert sum(item['long'] for item in plan) == 1


def test_readable_chord_uses_space_before_shrinking_font(keyboard_view):
    _, page = keyboard_view
    cap = page.keycaps['1']
    cap.set_mapping_info([{'trigger': '×  交叉 + L1', 'gesture': 'short'}])

    plan = cap.binding_display_plan(59, 24)

    assert plan[0]['font'] >= 11
    assert not plan[0]['stacked']
    assert plan[0]['tokens'] == ('L1', '×')


def test_keyboard_fits_during_rapid_resize_and_page_switching(tmp_path, monkeypatch):
    from PySide6.QtGui import QFont
    from gamepadstudio.studio import Studio, STYLE

    class Disconnected:
        available = []
        def scan(self): pass
        def read(self): return None
        def close(self): pass

    monkeypatch.setattr('gamepadstudio.studio.Device', Disconnected)
    app = QApplication.instance() or QApplication([])
    previous_font = app.font()
    previous_style = app.styleSheet()
    app.setStyleSheet(STYLE)
    window = Studio(tmp_path, standalone=True, lang='zh')
    window.enabled = False
    for timer in (window.timer, window.scan_timer, window.gallery_timer):
        timer.stop()
    try:
        window.show()
        window.navigate(6)
        QTest.qWait(20)
        page = window.virtual_kbm_page
        for point_size in (9, 11):
            app.setFont(QFont('Segoe UI', point_size))
            for width, height in ((1920, 1080), (960, 640), (1600, 900), (1200, 780)):
                window.navigate(0)
                window.resize(width, height)
                QTest.qWait(5)
                window.navigate(6)
                QTest.qWait(5)
                sizes = (point_size, width, page.area.viewport().width(),
                         page.area.widget().width(), page.key_group.width())
                assert page.area.horizontalScrollBar().maximum() == 0, sizes
                assert page.area.widget().width() == page.area.viewport().width(), sizes
                assert page.key_group.width() <= page.canvas.width(), sizes
    finally:
        window.cleanup()
        window.hide()
        app.setFont(previous_font)
        app.setStyleSheet(previous_style)

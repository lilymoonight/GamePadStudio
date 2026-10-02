"""macOS backend contract tests: every native input call uses a fake."""
import ctypes as C
import sys

import pytest

from gamepadstudio import actions
from gamepadstudio.actions import parse_keys
from gamepadstudio.macos_actions import (CAPS_LOCK_FLAG, MEDIA_KEYS, CGPoint, CGRect,
                                         CGSize, MacActions, MODIFIER_FLAGS)


class FakeQuartz:
    def __init__(self, granted=True):
        self.granted = granted
        self.requests = 0
        self.events = []
        self.released = []
        self.failure = None
        self.create_failure = None
        self.physical_flags = 0
        self.position = CGPoint(300, 200)
        self.bounds = CGRect(CGPoint(0, 0), CGSize(1920, 1080))
        self.warps = []
        self.caps_changes = []
        self.caps_failure = False

    def CGPreflightPostEventAccess(self):
        return self.granted

    def CGRequestPostEventAccess(self):
        self.requests += 1
        self.granted = True

    def CGEventCreateKeyboardEvent(self, source, key, down):
        if self.create_failure is not None and self.create_failure(key, down):
            return None
        return {'kind': 'key', 'key': key, 'down': down}

    def create_media_event(self, key_type, down):
        if self.create_failure is not None and self.create_failure(key_type, down):
            return None
        return {'kind': 'media', 'key_type': key_type, 'down': down}

    def set_caps_lock_state(self, enabled):
        if self.caps_failure:
            raise OSError('Fake CapsLock refused')
        self.caps_changes.append(enabled)
        if enabled:
            self.physical_flags |= CAPS_LOCK_FLAG
        else:
            self.physical_flags &= ~CAPS_LOCK_FLAG

    def CGEventCreateMouseEvent(self, source, event_type, position, button):
        return {'kind': 'mouse', 'type': event_type, 'position': (position.x, position.y), 'button': button}

    def CGEventCreateScrollWheelEvent2(self, source, units, count, steps, x, z):
        return {'kind': 'scroll', 'units': units, 'count': count, 'steps': steps}

    def CGEventSetFlags(self, event, flags):
        event['flags'] = flags

    def CGEventSourceFlagsState(self, source):
        assert source == 1
        return self.physical_flags

    def CGEventSetIntegerValueField(self, event, field, value):
        event[field] = value

    def CGEventPost(self, tap, event):
        assert tap == 0
        if self.failure is not None and self.failure(event):
            raise OSError('Fake refused event')
        self.events.append(event.copy())

    def CFRelease(self, event):
        self.released.append(event)

    def cursor_position(self):
        return self.position

    def display_bounds_at(self, point):
        return self.bounds

    def CGWarpMouseCursorPosition(self, point):
        self.warps.append((point.x, point.y))
        return 0


def output(granted=True):
    native = FakeQuartz(granted)
    return MacActions(native=native), native


def test_constructor_never_requests_permission_and_denial_posts_nothing():
    value, native = output(False)
    assert value.input_permission_status()['granted'] is False
    assert native.requests == 0
    for operation in (lambda: value.hold('Cmd+S', True),
                      lambda: value.mouse_button('left', True),
                      lambda: value.move_mouse(10, 2), lambda: value.scroll(1)):
        with pytest.raises(PermissionError, match='辅助功能'):
            operation()
    assert not value.held and not value.held_mouse
    assert native.events == []
    assert value.request_input_permission()['granted'] is True
    assert native.requests == 1


def test_command_and_control_remain_distinct_and_release_flags_follow_order():
    value, native = output()
    assert parse_keys('Cmd') == parse_keys('Command') == parse_keys('Meta') == parse_keys('Win') == [91]
    value.hold('Cmd+Ctrl+S', True)
    value.hold('Cmd+Ctrl+S', False)
    command, control = MODIFIER_FLAGS[91], MODIFIER_FLAGS[17]
    assert [(event['key'], event['down'], event['flags']) for event in native.events] == [
        (55, True, command), (59, True, command | control), (1, True, command | control),
        (1, False, command | control), (59, False, command), (55, False, 0),
    ]
    assert not value.held and not value._posted_keys
    assert len(native.released) == len(native.events)


def test_shared_modifier_stays_held_after_shortcut(monkeypatch):
    monkeypatch.setattr(actions.time, 'sleep', lambda duration: None)
    value, native = output()
    value.hold('Cmd', True)
    value.shortcut('Meta+S')
    assert value.held == {91: 1}
    assert value._posted_keys == {91}
    assert [(e['key'], e['down']) for e in native.events] == [(55, True), (1, True), (1, False)]
    value.release_all()
    assert native.events[-1]['flags'] == 0


def test_partial_chord_failure_rolls_back_already_posted_modifier():
    value, native = output()
    native.failure = lambda event: event.get('key') == 5 and event.get('down')
    with pytest.raises(OSError, match='Fake'):
        value.hold('Alt+G', True)
    assert not value.held and not value._posted_keys
    assert [(e['key'], e['down']) for e in native.events] == [(58, True), (5, False), (58, False)]
    assert len(native.released) == 4


def test_batch_allocation_failure_never_posts_a_partial_modifier_press():
    value, native = output()
    native.create_failure = lambda key, down: key == 5 and down
    with pytest.raises(OSError, match='创建按键'):
        value.hold('Alt+G', True)
    assert all(not event['down'] for event in native.events)
    assert not value.held and not value._posted_keys
    assert len(native.released) == 3  # allocated Alt-down, G-up, Alt-up


def test_failed_modifier_release_remains_in_later_cleanup_flags():
    value, native = output()
    value.hold('Ctrl+Cmd+S', True)
    native.failure = lambda event: event.get('key') == 55 and not event.get('down')
    with pytest.raises(OSError):
        value.release_all()
    assert value.held == {91: 1} and value._posted_keys == {91}
    assert native.events[-1]['key'] == 59
    assert native.events[-1]['flags'] == MODIFIER_FLAGS[91]
    native.failure = None
    value.release_all()
    assert native.events[-1]['flags'] == 0 and not value.held


def test_failed_key_and_mouse_releases_are_retained_and_retried():
    value, native = output()
    value.hold('Ctrl+G', True)
    value.mouse_button('left', True)
    native.failure = lambda event: event.get('key') == 5 or event.get('type') == 2
    with pytest.raises(OSError, match='未能释放'):
        value.release_all()
    assert value.held == {71: 1}
    assert value._posted_keys == {71}
    assert value.held_mouse == {'left'}
    assert any(e.get('key') == 59 and not e['down'] for e in native.events)
    native.failure = None
    value.release_all()
    assert not value.held and not value._posted_keys and not value.held_mouse


def test_permission_revocation_keeps_ownership_until_permission_restored():
    value, native = output()
    value.hold('Shift', True)
    value.mouse_button('right', True)
    native.granted = False
    with pytest.raises(OSError):
        value.release_all()
    assert value.held == {16: 1} and value.held_mouse == {'right'}
    native.granted = True
    value.release_all()
    assert not value.held and not value.held_mouse


@pytest.mark.parametrize('name', ['Insert', 'PrintScreen', 'Pause', 'ScrollLock', 'NumLock',
                                 'Calc', 'F21', 'F24'])
def test_unsupported_keys_refuse_complete_chord_before_any_event(name):
    value, native = output()
    assert not value.supports_key(name)
    with pytest.raises(ValueError, match='macOS'):
        value.hold('Cmd+' + name, True)
    assert not value.held and not native.events


@pytest.mark.parametrize('name', ['A', '0', 'F20', 'Num9', 'Num+', 'Delete', 'Home', 'PgDn', '`', 'Menu'])
def test_supported_physical_keys(name):
    value, native = output()
    assert value.supports_key(name)
    value.hold(name, True)
    value.hold(name, False)
    assert len(native.events) == 2 and not value.held


def test_menu_uses_apples_contextual_menu_key_and_not_help_or_pointer_click():
    value, native = output()
    value.shortcut('Menu')
    assert [(event['kind'], event['key'], event['down']) for event in native.events] == [
        ('key', 0x6E, True), ('key', 0x6E, False)]


@pytest.mark.parametrize('key,reason', [('Insert', '插入'), ('NumLock', '导航'),
                                      ('F21', '逻辑'), ('Calc', '同一键值')])
def test_unsupported_key_capability_explains_effect_without_substitution(key, reason):
    capability = MacActions.key_capability(key)
    assert capability['supported'] is False and reason in capability['reason']


def test_supported_key_capability_reports_empty_reason():
    assert MacActions.key_capability('Cmd+CapsLock') == {'supported': True, 'reason': ''}


@pytest.mark.parametrize('action,vk,key_type', [
    ('volume_mute', 0xAD, 7), ('volume_down', 0xAE, 1),
    ('volume_up', 0xAF, 0), ('media', 0xB3, 16),
])
def test_media_actions_emit_one_native_down_up_with_correct_command(action, vk, key_type):
    value, native = output()
    native.physical_flags = MODIFIER_FLAGS[16]
    value.hold('Cmd', True)
    value.media(action)
    down, up = native.events[1:]
    assert (down['kind'], up['kind']) == ('media', 'media')
    assert down['key_type'] == up['key_type'] == key_type
    assert down['down'] is True and up['down'] is False
    assert down['flags'] == up['flags'] == MODIFIER_FLAGS[16] | MODIFIER_FLAGS[91]
    assert vk not in value.held and vk not in value._posted_keys
    value.release_all()


@pytest.mark.parametrize('name', ['CapsLock', 'Caps', 'Mute', 'VolumeMute', 'Vol+', 'Vol-', 'VolumeUp'])
def test_new_supported_keys_keep_same_stored_tokens_and_ownership(name):
    value, native = output()
    assert value.supports_key(name)
    value.hold(name, True)
    value.hold(name, True)
    assert len(native.events) == 1
    value.hold(name, False)
    assert len(native.events) == 1
    value.hold(name, False)
    assert len(native.events) == 2 and not value.held


def test_caps_lock_toggles_once_on_fresh_down_and_persists_after_release_pause():
    value, native = output()
    value.hold('CapsLock', True)
    value.hold('Caps', True)
    value.hold('CapsLock', False)
    value.release_all()
    assert native.caps_changes == [True]
    assert [event['down'] for event in native.events] == [True, False]
    assert all(event['key'] == 57 and event['flags'] & CAPS_LOCK_FLAG for event in native.events)
    assert native.physical_flags & CAPS_LOCK_FLAG and not value.held
    value.hold('A', True)
    value.hold('A', False)
    value.scroll(1)
    assert all(event['flags'] & CAPS_LOCK_FLAG for event in native.events)
    value.hold('CapsLock', True)
    value.hold('CapsLock', False)
    assert native.caps_changes == [True, False]
    assert not native.physical_flags & CAPS_LOCK_FLAG
    assert not native.events[-1]['flags'] & CAPS_LOCK_FLAG


def test_caps_lock_preserves_existing_physical_caps_and_modifiers():
    value, native = output()
    native.physical_flags = CAPS_LOCK_FLAG | MODIFIER_FLAGS[16]
    value.hold('Ctrl+CapsLock', True)
    value.hold('Ctrl+CapsLock', False)
    assert native.caps_changes == [False]
    assert native.physical_flags == MODIFIER_FLAGS[16]
    down = next(event for event in native.events if event.get('key') == 57 and event['down'])
    assert down['flags'] == MODIFIER_FLAGS[16] | MODIFIER_FLAGS[17]
    assert not value.held


def test_caps_and_media_permission_denial_and_unsupported_chords_have_no_side_effects():
    value, native = output(False)
    for operation in (lambda: value.hold('CapsLock', True), lambda: value.hold('Mute', True),
                      lambda: value.media('media')):
        with pytest.raises(PermissionError):
            operation()
    assert not native.caps_changes and not native.events and not value.held
    native.granted = True
    with pytest.raises(ValueError):
        value.hold('CapsLock+PrintScreen', True)
    assert not native.caps_changes and not native.events and not value.held


def test_caps_event_creation_failure_before_batch_output_never_toggles():
    value, native = output()
    native.create_failure = lambda key, down: key == 0 and down  # A
    with pytest.raises(OSError):
        value.hold('CapsLock+A', True)
    assert not native.caps_changes
    assert not any(event['down'] for event in native.events)
    assert not value.held


def test_caps_setter_failure_releases_ownership_without_toggling_or_pressing():
    value, native = output()
    native.caps_failure = True
    with pytest.raises(OSError, match='CapsLock refused'):
        value.hold('CapsLock', True)
    assert not native.caps_changes and not value.held
    assert not any(event['down'] for event in native.events)


def test_caps_post_failure_cleanup_does_not_undo_lock_state():
    value, native = output()
    native.failure = lambda event: event.get('key') == 57 and event['down']
    with pytest.raises(OSError):
        value.hold('CapsLock', True)
    assert native.caps_changes == [True] and native.physical_flags & CAPS_LOCK_FLAG
    assert not value.held
    assert all(not event['down'] for event in native.events)


@pytest.mark.parametrize('name', ['CapsLock', 'Mute'])
def test_new_key_failed_release_is_retained_and_retried_without_another_press(name):
    value, native = output()
    value.hold(name, True)
    native.failure = lambda event: not event['down']
    with pytest.raises(OSError):
        value.release_all()
    assert value.held
    native.failure = None
    value.release_all()
    assert [event['down'] for event in native.events] == [True, False]
    assert not value.held
    assert native.caps_changes == ([True] if name == 'CapsLock' else [])


@pytest.mark.parametrize('button, press_type, release_type, drag_type, number', [
    ('left', 1, 2, 6, 0), ('right', 3, 4, 7, 1), ('middle', 25, 26, 27, 2),
])
def test_drag_and_scroll_preserve_modifiers(button, press_type, release_type, drag_type, number):
    value, native = output()
    native.physical_flags = MODIFIER_FLAGS[16]  # physical Shift
    value.hold('Cmd', True)
    value.mouse_button(button, True)
    value.move_mouse(8, -4)
    value.scroll(-2)
    value.mouse_button(button, False)
    press, drag, scroll, release = native.events[1:]
    assert (press['type'], release['type'], drag['type'], drag['button']) == (
        press_type, release_type, drag_type, number)
    assert drag['position'] == (308, 196)
    assert (drag[4], drag[5]) == (8, -4)
    assert (scroll['units'], scroll['count'], scroll['steps']) == (1, 1, -2)
    assert all(e['flags'] == MODIFIER_FLAGS[16] | MODIFIER_FLAGS[91]
               for e in (press, drag, scroll, release))
    assert not value.held_mouse


def test_cursor_guard_only_recenters_recognized_game_at_display_edge():
    class Workspace:
        def foreground_process_name(self):
            return 'infinitynikki'
    native = FakeQuartz()
    native.position = CGPoint(1915, 100)
    value = MacActions(native=native, workspace=Workspace())
    value.guard_cursor_edge()
    assert native.warps == [(960, 540)]
    value._cached_pname = 'nikki launcher'
    value._last_guard_check = -1.
    value.guard_cursor_edge()
    assert native.warps == [(960, 540)]


def test_foreground_name_has_half_second_cache(monkeypatch):
    class Workspace:
        calls = 0
        def foreground_process_name(self):
            self.calls += 1
            return 'finder'
    workspace = Workspace()
    value = MacActions(native=FakeQuartz(), workspace=workspace)
    now = [10.]
    monkeypatch.setattr('gamepadstudio.macos_actions.time.monotonic', lambda: now[0])
    assert value.get_foreground_process_name() == 'finder'
    assert value.get_foreground_process_name() == 'finder'
    assert workspace.calls == 1
    now[0] += .6
    assert value.get_foreground_process_name() == 'finder'
    assert workspace.calls == 2


def test_factory_selects_backend_and_explicitly_refuses_unknown_platform(monkeypatch):
    marker = object()
    monkeypatch.setattr(actions.sys, 'platform', 'win32')
    monkeypatch.setattr(actions, 'WindowsActions', lambda: marker)
    assert actions.create_actions() is marker
    monkeypatch.setattr(actions.sys, 'platform', 'darwin')
    monkeypatch.setattr('gamepadstudio.macos_actions.MacActions', lambda: marker)
    assert actions.create_actions() is marker
    assert actions.supports_key('Cmd+S') and not actions.supports_key('F24')
    monkeypatch.setattr(actions.sys, 'platform', 'linux')
    with pytest.raises(OSError, match='linux'):
        actions.create_actions()


def test_module_permission_wrappers_request_only_on_explicit_call(monkeypatch):
    native = FakeQuartz(False)
    monkeypatch.setattr(actions.sys, 'platform', 'darwin')
    monkeypatch.setattr('gamepadstudio.macos_actions._QuartzNative', lambda: native)
    assert actions.input_permission_status()['granted'] is False
    assert native.requests == 0
    assert actions.request_input_permission()['granted'] is True
    assert native.requests == 1


def test_macos_launch_uses_posix_argv_without_shell(monkeypatch):
    calls = []
    monkeypatch.setattr(actions.sys, 'platform', 'darwin')
    monkeypatch.setattr(actions.subprocess, 'Popen', lambda *args, **kwargs: calls.append((args, kwargs)))
    actions.launch_command('/Applications/My Tool', r'--name "hello world" escaped\ path "$(touch x)"')
    assert calls == [((['/Applications/My Tool', '--name', 'hello world', 'escaped path', '$(touch x)'],),
                     {'shell': False})]


def test_macos_application_bundle_uses_open_and_preserves_argument_boundaries(tmp_path, monkeypatch):
    app = tmp_path / 'My App.app'
    app.mkdir()
    calls = []
    monkeypatch.setattr(actions.sys, 'platform', 'darwin')
    monkeypatch.setattr(actions.subprocess, 'Popen', lambda *args, **kwargs: calls.append((args, kwargs)))
    actions.launch_command(str(app), '--name "hello world"')
    assert calls == [((['/usr/bin/open', '-a', str(app), '--args', '--name', 'hello world'],), {'shell': False})]


def test_launch_refuses_blank_executable():
    with pytest.raises(ValueError, match='可执行文件'):
        actions.launch_command('   ')


def test_windows_launch_keeps_windows_backslashes_and_quoted_paths(monkeypatch):
    calls = []
    monkeypatch.setattr(actions.sys, 'platform', 'win32')
    monkeypatch.setattr(actions.subprocess, 'Popen', lambda *args, **kwargs: calls.append((args, kwargs)))
    actions.launch_command(r'C:\Apps\tool.exe', r'--path "C:\User Name\folder" --flag')
    assert calls == [
        (([r'C:\Apps\tool.exe', '--path', r'C:\User Name\folder', '--flag'],), {'shell': False})]

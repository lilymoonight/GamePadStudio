"""Application ownership, exact foreground paths and bounded native access."""
import copy
import ctypes as C
from ctypes import wintypes as W
from types import SimpleNamespace

import pytest

from gamepadstudio.application_profiles import (
    ApplicationProfileResolver, ForegroundApplicationReader,
    PROCESS_QUERY_LIMITED_INFORMATION, canonical_executable,
    foreground_application, normalize_application_profiles, normalize_executable,
)


STATE = {'device_key': 'dualsense:one', 'instance_id': 17}
GAME = r'D:\游戏\无限暖暖\InfinityNikki.exe'


def configuration():
    return {
        'profiles': {'普通': {}, '暖暖': {}, '桌面': {}, '其他设备': {}, '离线': {}},
        'profile_devices': {'普通': 'dualsense:one', '暖暖': 'dualsense:one',
                            '桌面': 'dualsense:one', '其他设备': 'dualsense:two',
                            '离线': 'offline:xinput'},
        'controller_profiles': {'dualsense:one': '普通', 'dualsense:two': '其他设备',
                                'offline:xinput': '离线'},
        'active_profile': '普通',
        'application_profiles': {'dualsense:one': {'enabled': True, 'rules': [
            {'executable': GAME, 'profile': '暖暖'},
            {'executable': r'C:\Desktop\tool.exe', 'profile': '桌面'},
        ]}},
    }


def foreground(path=GAME, pid=321, hwnd=123):
    return {'pid': pid, 'hwnd': hwnd, 'executable': path}


@pytest.mark.parametrize('path', [
    '', None, 123, 'game.exe', r'Games\game.exe', r'C:game.exe', r'\Games\game.exe',
    '/usr/bin/game.exe', r'%USERPROFILE%\game.exe', r'C:\Game\game.bat',
    r'C:\Game\game.exe --play', '"C:\\Game\\game.exe"', r'C:\Game\game.exe:other',
    r'C:\Game*\game.exe', r'\\server\share', r'\\.\pipe\game.exe',
    'C:\\Bad\nFolder\\game.exe', r'C:\trailing.\game.exe',
])
def test_executable_requires_a_full_windows_executable_path(path, monkeypatch):
    import gamepadstudio.application_profiles as module
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='win32'))
    with pytest.raises(ValueError):
        canonical_executable(path)


@pytest.mark.parametrize('left,right', [
    (r'C:\Games\Game.EXE', 'c:/games/game.exe'),
    (GAME, r'd:\游戏\无限暖暖\INFINITYNIKKI.EXE'),
    (r'\\server\share\Game.exe', r'\\SERVER\SHARE\game.EXE'),
    (r'\\?\C:\Games\game.exe', r'C:\Games\game.exe'),
    (r'\\?\UNC\server\share\game.exe', r'\\server\share\game.exe'),
])
def test_path_identity_is_case_insensitive_and_handles_unicode(left, right):
    assert canonical_executable(left) == canonical_executable(right)
    assert normalize_executable(left).lower().endswith('.exe')


def test_same_executable_basename_in_different_locations_is_distinct():
    assert canonical_executable(r'C:\One\game.exe') != canonical_executable(r'D:\Two\game.exe')


def test_rules_default_to_disabled_and_do_not_mutate_the_input():
    config = configuration()
    assert normalize_application_profiles(None, config, STATE) == {'enabled': False, 'rules': []}
    value = {'rules': [{'executable': GAME.replace('\\', '/'), 'profile': '暖暖'}]}
    original = copy.deepcopy(value)
    assert normalize_application_profiles(value, config, STATE) == {
        'enabled': False, 'rules': [{'executable': GAME, 'profile': '暖暖'}]}
    assert value == original


@pytest.mark.parametrize('value', [
    [], {'enabled': 1}, {'enabled': 'true'}, {'rules': {}}, {'extra': 1},
    {'rules': [{'executable': GAME}]}, {'rules': [{'executable': GAME, 'profile': '暖暖', 'command': 'x'}]},
    {'rules': [{'executable': GAME, 'profile': '其他设备'}]},
    {'rules': [{'executable': GAME, 'profile': '不存在'}]},
    {'rules': [{'executable': GAME, 'profile': []}]},
    {'rules': [{'executable': GAME, 'profile': '暖暖'},
               {'executable': GAME.upper().replace('\\', '/'), 'profile': '普通'}]},
])
def test_invalid_or_foreign_rules_are_rejected(value):
    with pytest.raises(ValueError):
        normalize_application_profiles(value, configuration(), STATE)


def test_disconnected_configuration_cannot_enable_or_add_rules():
    config = configuration()
    assert normalize_application_profiles({'enabled': False, 'rules': []}, config, None) == {
        'enabled': False, 'rules': []}
    with pytest.raises(ValueError, match='先连接'):
        normalize_application_profiles({'enabled': True, 'rules': []}, config, None)
    with pytest.raises(ValueError, match='先连接'):
        normalize_application_profiles({'rules': [{'executable': GAME, 'profile': '离线'}]}, config, None)


class NativeFunction:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


def reader_apis(path=GAME, hwnd=2 ** 34, handle=2 ** 40, open_ok=True,
                query_ok=True, thread_ok=True, query_error=False, windows=None):
    calls = []
    window_values = iter(windows) if windows else None

    def get_window():
        calls.append(('foreground',))
        return next(window_values) if window_values else hwnd

    def get_pid(window, pointer):
        calls.append(('pid', window))
        pointer._obj.value = 91
        return 7 if thread_ok else 0

    def open_process(rights, inherit, pid):
        calls.append(('open', rights, inherit, pid))
        return handle if open_ok else 0

    def query(process, flags, buffer, length):
        calls.append(('query', process, flags))
        if query_error:
            raise OSError('process disappeared')
        buffer.value = path
        length._obj.value = len(path)
        return query_ok

    def close(process):
        calls.append(('close', process))
        return True

    user32 = SimpleNamespace(GetForegroundWindow=NativeFunction(get_window),
                             GetWindowThreadProcessId=NativeFunction(get_pid))
    kernel32 = SimpleNamespace(OpenProcess=NativeFunction(open_process),
                               QueryFullProcessImageNameW=NativeFunction(query),
                               CloseHandle=NativeFunction(close))
    return ForegroundApplicationReader(user32, kernel32), calls


def test_native_reader_preserves_full_unicode_path_and_64_bit_handles():
    reader, calls = reader_apis()
    assert reader.read() == {'pid': 91, 'hwnd': 2 ** 34, 'executable': GAME}
    assert ('open', PROCESS_QUERY_LIMITED_INFORMATION, False, 91) in calls
    assert ('query', 2 ** 40, 0) in calls
    assert calls[-1] == ('close', 2 ** 40)
    assert reader.kernel32.OpenProcess.restype is W.HANDLE
    assert C.sizeof(reader.kernel32.OpenProcess.restype) == C.sizeof(C.c_void_p)
    assert reader.user32.GetForegroundWindow.restype is W.HWND
    assert reader.kernel32.QueryFullProcessImageNameW.argtypes == [
        W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]


@pytest.mark.parametrize('kwargs', [
    {'query_ok': False}, {'query_error': True}, {'path': 'only-basename.exe'},
])
def test_reader_failure_preserves_known_pid_and_always_closes_handle(kwargs):
    reader, calls = reader_apis(**kwargs)
    assert reader.read() == {'pid': 91, 'hwnd': 2 ** 34, 'executable': ''}
    assert calls[-1] == ('close', 2 ** 40)
    assert len([call for call in calls if call[0] == 'close']) == 1


@pytest.mark.parametrize('kwargs,pid', [({'hwnd': 0}, 0), ({'thread_ok': False}, 0),
                                      ({'open_ok': False}, 91)])
def test_reader_never_closes_an_unopened_handle(kwargs, pid):
    reader, calls = reader_apis(**kwargs)
    assert reader.read()['pid'] == pid
    assert not any(call[0] in ('query', 'close') for call in calls)


def test_reader_focus_change_during_query_does_not_return_old_application():
    reader, calls = reader_apis(windows=[2 ** 34, 888])
    assert reader.read() == {'pid': 0, 'hwnd': 0, 'executable': ''}
    assert calls[-1] == ('close', 2 ** 40)


def test_foreground_reader_is_safe_when_native_libraries_are_unavailable(monkeypatch):
    import gamepadstudio.application_profiles as module
    monkeypatch.setattr(module, '_foreground_reader', None)
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='win32'))
    def unavailable():
        raise OSError('user32 unavailable')
    monkeypatch.setattr(module, 'ForegroundApplicationReader', unavailable)
    assert foreground_application() == {'pid': 0, 'hwnd': 0, 'executable': ''}


def test_resolver_matches_the_full_path_and_falls_back_without_changing_configuration():
    config = configuration()
    before = copy.deepcopy(config)
    resolver = ApplicationProfileResolver()
    assert resolver.resolve(config, STATE, foreground(GAME.upper()), 0) == {
        'automatic': True, 'executable': GAME.upper(), 'profile': '暖暖'}
    assert resolver.resolve(config, STATE, foreground(r'C:\Other\InfinityNikki.exe'), .25)['profile'] == '普通'
    assert config == before


@pytest.mark.parametrize('value', [None, {}, foreground(''), foreground('game.exe')])
def test_unreadable_or_invalid_foreground_stably_uses_baseline(value):
    resolver = ApplicationProfileResolver()
    config = configuration()
    assert resolver.resolve(config, STATE, foreground(), 0)['profile'] == '暖暖'
    result = resolver.resolve(config, STATE, value, .25)
    assert result == {'automatic': False, 'executable': '', 'profile': '普通'}
    assert resolver.resolve(config, STATE, value, .5) == result


def test_manual_override_lasts_until_the_observed_foreground_identity_changes():
    resolver = ApplicationProfileResolver()
    config = configuration()
    resolver.resolve(config, STATE, foreground(), 0)
    resolver.manual_selection('桌面')
    config['active_profile'] = '桌面'
    result = resolver.resolve(config, STATE, foreground(), .25)
    assert result['profile'] == '桌面' and not result['automatic']
    assert resolver.resolve(config, STATE, foreground(hwnd=124), .5)['profile'] == '暖暖'


def test_manual_selection_before_the_first_foreground_sample_is_preserved():
    resolver = ApplicationProfileResolver(protected_pids={555})
    config = configuration()
    resolver.manual_selection('桌面', foreground('', pid=555, hwnd=33))
    config['active_profile'] = '桌面'
    assert resolver.resolve(config, STATE, foreground('', pid=555, hwnd=33), 0)['profile'] == '桌面'
    assert resolver.resolve(config, STATE, foreground(), .25)['profile'] == '桌面'
    assert resolver.resolve(config, STATE, foreground(hwnd=124), .5)['profile'] == '暖暖'


@pytest.mark.parametrize('path', ['', r'C:\Studio\GamePadStudio.exe'])
def test_studio_focus_preserves_manual_override_until_the_game_changes(path):
    config = configuration()
    resolver = ApplicationProfileResolver()
    resolver.resolve(config, STATE, foreground(), 0, protected_pids={555})
    config['active_profile'] = '暖暖'
    assert resolver.resolve(config, STATE, foreground(path, pid=555, hwnd=33), .25,
                            protected_pids={555})['profile'] == '暖暖'
    resolver.manual_selection('桌面', foreground(path, pid=555, hwnd=33))
    config['active_profile'] = '桌面'
    assert resolver.resolve(config, STATE, foreground(), .5, protected_pids={555})['profile'] == '桌面'
    assert resolver.resolve(config, STATE, foreground(pid=322), .75,
                            protected_pids={555})['profile'] == '暖暖'


@pytest.mark.parametrize('freeze', ['editing', 'preview'])
def test_mapping_edit_and_preview_freeze_the_current_profile(freeze):
    config = configuration()
    resolver = ApplicationProfileResolver()
    resolver.resolve(config, STATE, foreground(), 0)
    config['active_profile'] = '暖暖'
    result = resolver.resolve(config, STATE, foreground(r'C:\Desktop\tool.exe'), .25, **{freeze: True})
    assert result['profile'] == '暖暖' and result['automatic']
    assert resolver.resolve(config, STATE, foreground(r'C:\Desktop\tool.exe'), .5)['profile'] == '桌面'


def test_disabling_automatic_switching_and_disconnecting_override_focus_freeze():
    config = configuration()
    resolver = ApplicationProfileResolver(protected_pids={555})
    resolver.resolve(config, STATE, foreground(), 0)
    config['active_profile'] = '暖暖'
    config['application_profiles'][STATE['device_key']]['enabled'] = False
    assert resolver.resolve(config, STATE, foreground('', 555), .25, editing=True)['profile'] == '普通'
    config['application_profiles'][STATE['device_key']]['enabled'] = True
    assert resolver.resolve(config, None, foreground('', 555), .5, preview=True)['profile'] == '离线'


def test_switching_devices_drops_override_and_keeps_profiles_owned_by_each_device():
    config = configuration()
    config['application_profiles']['dualsense:two'] = {
        'enabled': True, 'rules': [{'executable': GAME, 'profile': '其他设备'}]}
    resolver = ApplicationProfileResolver()
    resolver.resolve(config, STATE, foreground(), 0)
    resolver.manual_selection('桌面')
    other = {'device_key': 'dualsense:two', 'instance_id': 18}
    assert resolver.resolve(config, other, foreground(), .25)['profile'] == '其他设备'
    assert resolver.resolve(config, STATE, foreground(), .5)['profile'] == '暖暖'


@pytest.mark.parametrize('settings', [None, [], {'enabled': 'yes'}, {'enabled': True, 'rules': None},
                                      {'enabled': True, 'rules': ['invalid', {},
                                          {'executable': GAME, 'profile': '其他设备'}]}])
def test_resolver_tolerates_saved_invalid_settings_without_borrowing_other_devices(settings):
    config = configuration()
    config['application_profiles'][STATE['device_key']] = settings
    assert ApplicationProfileResolver().resolve(config, STATE, foreground(), 0)['profile'] == '普通'


def test_removed_baseline_and_manual_profile_cannot_be_selected():
    config = configuration()
    resolver = ApplicationProfileResolver()
    resolver.resolve(config, STATE, foreground(), 0)
    resolver.manual_selection('桌面')
    del config['profiles']['桌面']
    del config['profiles']['普通']
    config['active_profile'] = '其他设备'
    result = resolver.resolve(config, STATE, foreground(''), .25)
    assert result['profile'] == '暖暖' and not result['automatic']


def test_runtime_does_not_accept_true_from_non_boolean_saved_values():
    config = configuration()
    config['application_profiles'][STATE['device_key']]['enabled'] = 1
    assert ApplicationProfileResolver().resolve(config, STATE, foreground(), 0)['profile'] == '普通'


@pytest.mark.parametrize('field', ['profiles', 'profile_devices', 'controller_profiles', 'application_profiles'])
def test_runtime_tolerates_invalid_top_level_metadata(field):
    config = configuration()
    config[field] = None
    result = ApplicationProfileResolver().resolve(config, STATE, foreground(), 0)
    assert result['profile'] in ('', '普通', '暖暖')
    assert result['profile'] != '其他设备'

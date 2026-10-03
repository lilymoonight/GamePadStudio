"""Full executable ownership and read-only Mac foreground metadata."""
import ctypes as C
import plistlib
import sys
from types import SimpleNamespace

import pytest

from gamepadstudio import application_profiles as apps


pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='macOS application bundles and AppKit only')


@pytest.fixture(autouse=True)
def mac_platform(monkeypatch):
    monkeypatch.setattr(apps, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(apps, '_foreground_reader', None)


def bundle(root, name='游戏.app', executable='Actual Launcher'):
    path = root / name
    (path / 'Contents' / 'MacOS').mkdir(parents=True)
    (path / 'Contents' / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable': executable}))
    binary = path / 'Contents' / 'MacOS' / executable
    binary.write_bytes(b'fixture only, never executed')
    return path, binary


def test_bundle_uses_metadata_and_matches_its_actual_full_process_path(tmp_path):
    path, binary = bundle(tmp_path)
    assert apps.normalize_executable(str(path)) == str(binary)
    assert apps.canonical_executable(str(path)) == apps.canonical_executable(str(binary))
    other, other_binary = bundle(tmp_path, '另一个.app')
    assert apps.canonical_executable(str(other)) != apps.canonical_executable(str(path))
    assert binary.name == other_binary.name


def test_bundle_alias_and_executable_alias_share_the_same_identity(tmp_path):
    path, binary = bundle(tmp_path)
    alias = tmp_path / '别名.app'
    alias.symlink_to(path, target_is_directory=True)
    binary_alias = tmp_path / 'game-alias'
    binary_alias.symlink_to(binary)
    assert apps.canonical_executable(str(alias)) == str(binary)
    assert apps.canonical_executable(str(binary_alias)) == str(binary)


@pytest.mark.parametrize('name', [None, '', '..', '../outside', 'nested/exec', r'nested\exec', 'bad\nname'])
def test_bundle_never_guesses_or_escapes_the_declared_main_executable(tmp_path, name):
    path, _ = bundle(tmp_path)
    info = {} if name is None else {'CFBundleExecutable': name}
    (path / 'Contents' / 'Info.plist').write_bytes(plistlib.dumps(info))
    with pytest.raises(ValueError, match='macOS'):
        apps.normalize_executable(str(path))


@pytest.mark.parametrize('payload', [b'not a plist', plistlib.dumps([])])
def test_corrupt_bundle_metadata_is_rejected(tmp_path, payload):
    path, _ = bundle(tmp_path)
    (path / 'Contents' / 'Info.plist').write_bytes(payload)
    with pytest.raises(ValueError):
        apps.normalize_executable(str(path))


def test_missing_declared_binary_and_plain_directory_are_rejected(tmp_path):
    path, binary = bundle(tmp_path)
    binary.unlink()
    for value in (str(path), str(tmp_path), str(tmp_path / 'Unknown.app')):
        with pytest.raises(ValueError):
            apps.normalize_executable(value)


def test_absolute_executable_rules_remain_durable_after_app_is_removed(tmp_path):
    path = tmp_path / 'Removed.app' / 'Contents' / 'MacOS' / 'Game'
    assert apps.normalize_executable(str(path)) == str(path)
    assert apps.canonical_executable(str(path)) != apps.canonical_executable(str(path).lower())
    assert apps.canonical_executable(r'C:\Games\Game.EXE') == r'c:\games\game.exe'
    for value in ('Game.app', '~/Games/Game', 'Game', '/path/with\nnewline'):
        with pytest.raises(ValueError):
            apps.normalize_executable(value)


def test_bundle_and_binary_rules_are_duplicates_and_resolver_matches_full_paths(tmp_path):
    path, binary = bundle(tmp_path)
    other, _ = bundle(tmp_path, 'Other.app')
    state = {'device_key': 'dualsense:one'}
    config = {'profiles': {'普通': {}, '游戏': {}},
              'profile_devices': {'普通': 'dualsense:one', '游戏': 'dualsense:one'},
              'controller_profiles': {'dualsense:one': '普通'}, 'active_profile': '普通'}
    rule = {'executable': str(path), 'profile': '游戏'}
    settings = apps.normalize_application_profiles({'enabled': True, 'rules': [rule]}, config, state)
    assert settings['rules'][0]['executable'] == str(binary)
    with pytest.raises(ValueError, match='同一个应用'):
        apps.normalize_application_profiles({'rules': [rule, {'executable': str(binary), 'profile': '普通'}]},
                                             config, state)
    config['application_profiles'] = {'dualsense:one': settings}
    resolver = apps.ApplicationProfileResolver()
    assert resolver.resolve(config, state, {'pid': 81, 'hwnd': 0, 'executable': str(binary)}, 0) == {
        'automatic': True, 'executable': str(binary), 'profile': '游戏'}
    assert resolver.resolve(config, state, {'pid': 82, 'hwnd': 0, 'executable': str(other)}, 1)['profile'] == '普通'


def workspace(*values):
    sequence = iter(values)
    return SimpleNamespace(frontmost_application=lambda: next(sequence))


def test_reader_preserves_full_unicode_executable_path(tmp_path):
    _, binary = bundle(tmp_path)
    metadata = {'pid': 81, 'executable': str(binary)}
    reader = apps.MacForegroundApplicationReader(workspace(metadata, metadata))
    assert reader.read() == {'pid': 81, 'hwnd': 0, 'executable': str(binary)}


@pytest.mark.parametrize('current', [{'pid': 82, 'executable': '/other'}, {'pid': 0, 'executable': ''}])
def test_reader_rejects_a_focus_change_or_disappearing_app_during_read(current):
    reader = apps.MacForegroundApplicationReader(workspace({'pid': 81, 'executable': '/Game'}, current))
    assert reader.read() == {'pid': 0, 'hwnd': 0, 'executable': ''}


def test_reader_safely_handles_missing_native_metadata():
    missing = {'pid': 81, 'executable': ''}
    assert apps.MacForegroundApplicationReader(workspace(missing, missing)).read() == {
        'pid': 81, 'hwnd': 0, 'executable': ''}
    assert apps.MacForegroundApplicationReader(workspace(None)).read() == {
        'pid': 0, 'hwnd': 0, 'executable': ''}


def test_platform_factory_is_lazy_and_safe_if_appkit_is_unavailable(monkeypatch):
    assert apps.application_profiles_supported()
    monkeypatch.setattr(apps, 'MacForegroundApplicationReader', lambda: (_ for _ in ()).throw(OSError('no AppKit')))
    assert apps.foreground_application() == {'pid': 0, 'hwnd': 0, 'executable': ''}
    monkeypatch.setattr(apps, 'sys', SimpleNamespace(platform='linux'))
    assert not apps.application_profiles_supported()
    assert apps.foreground_application() == {'pid': 0, 'hwnd': 0, 'executable': ''}


def test_objc_bridge_preserves_pointer_width_and_drains_its_autorelease_pool(monkeypatch):
    classes = {b'NSAutoreleasePool': 2 ** 34, b'NSWorkspace': 2 ** 35}
    selectors, calls = {}, []
    pool, shared, app, url, path = (2 ** power for power in range(36, 41))
    text = C.create_string_buffer('/Applications/游戏.app/Contents/MacOS/Launcher'.encode())

    def register(value):
        if value not in selectors:
            selectors[value] = len(selectors) + 1
        return selectors[value]

    def message(obj, selector):
        name = next(key for key, value in selectors.items() if value == selector)
        calls.append((obj, name))
        return {(classes[b'NSAutoreleasePool'], b'alloc'): pool, (pool, b'init'): pool,
                (classes[b'NSWorkspace'], b'sharedWorkspace'): shared,
                (shared, b'frontmostApplication'): app, (app, b'processIdentifier'): 91,
                (app, b'executableURL'): url, (url, b'path'): path,
                (path, b'UTF8String'): C.addressof(text), (pool, b'drain'): 0}[(obj, name)]

    callback = C.CFUNCTYPE(C.c_void_p, C.c_void_p, C.c_void_p)(message)

    class Function:
        def __init__(self, function):
            self.function = function

        def __call__(self, value):
            return self.function(value)

    objc = SimpleNamespace(objc_getClass=Function(classes.get), sel_registerName=Function(register),
                           objc_msgSend=callback)
    monkeypatch.setattr(apps.C, 'CDLL', lambda name: objc if name.endswith('libobjc.A.dylib') else object())
    native = apps._MacWorkspaceNative()
    assert native.frontmost_application() == {
        'pid': 91, 'executable': '/Applications/游戏.app/Contents/MacOS/Launcher'}
    assert native._send_object.restype is C.c_void_p
    assert native._send_pid.restype is C.c_int
    assert (app, b'executableURL') in calls and calls[-1] == (pool, b'drain')

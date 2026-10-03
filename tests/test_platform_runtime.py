"""Real macOS SDL state and child processes exercise platform startup boundaries."""
import ctypes as C
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

import pygame
import pytest
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from gamepadstudio import ipc
from gamepadstudio.device import Device, GUID, AxisBind


def test_non_windows_sdl_shares_pygame_virtual_joystick_state(monkeypatch):
    if sys.platform == 'win32':
        pytest.skip('POSIX extension dependency resolution')
    # A non-Windows device must not need the Windows-only cloaking backend.
    monkeypatch.setitem(sys.modules, 'gamepadstudio.hidhide', None)
    device = Device()
    lib = device.lib
    for name, args, result in [
        ('SDL_JoystickAttachVirtual', [C.c_int, C.c_int, C.c_int, C.c_int], C.c_int),
        ('SDL_JoystickDetachVirtual', [C.c_int], C.c_int),
        ('SDL_JoystickSetVirtualButton', [C.c_void_p, C.c_int, C.c_uint8], C.c_int),
        ('SDL_JoystickSetVirtualAxis', [C.c_void_p, C.c_int, C.c_int16], C.c_int),
    ]:
        fn = getattr(lib, name)
        fn.argtypes, fn.restype = args, result
    assert C.sizeof(GUID) == 16
    assert C.sizeof(AxisBind) == 12
    index = lib.SDL_JoystickAttachVirtual(1, 6, 21, 0)
    assert index >= 0
    instance = lib.SDL_JoystickGetDeviceInstanceID(index)
    joystick = None
    try:
        device.select(instance)
        joystick = pygame.joystick.Joystick(index)
        assert joystick.get_instance_id() == instance
        handle = lib.SDL_GameControllerGetJoystick(device.handle)
        assert lib.SDL_JoystickSetVirtualButton(handle, 0, 1) == 0
        assert lib.SDL_JoystickSetVirtualAxis(handle, 0, 16384) == 0
        state = device.read()
        assert 0 in state['buttons']
        assert state['axes'][0] == pytest.approx(.5)
        assert joystick.get_button(0) == 1
        assert joystick.get_axis(0) == pytest.approx(.5)
        assert not device.access_warning
    finally:
        if joystick is not None:
            joystick.quit()
        device.close_handle()
        lib.SDL_JoystickDetachVirtual(index)
        device.close()


def test_endpoint_matches_real_child_and_isolates_data_dirs(tmp_path):
    script = ('import json,sys; from gamepadstudio.ipc import endpoint; '
              'print(json.dumps([endpoint(sys.argv[1]),endpoint(sys.argv[1],"ui")]))')
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)],
                            check=True, capture_output=True, text=True, timeout=15)
    assert json.loads(result.stdout) == [ipc.endpoint(tmp_path), ipc.endpoint(tmp_path, 'ui')]
    assert ipc.endpoint(tmp_path) != ipc.endpoint(tmp_path / 'other')


def test_posix_endpoint_uses_uid_instead_of_environment_user(monkeypatch, tmp_path):
    if sys.platform == 'win32':
        pytest.skip('POSIX uid and process session')
    original = ipc.endpoint(tmp_path)
    monkeypatch.setenv('USERNAME', 'another-user')
    monkeypatch.setenv('USER', 'another-user')
    assert ipc.endpoint(tmp_path) == original
    monkeypatch.setattr(ipc.os, 'getuid', lambda: 123456)
    assert ipc.endpoint(tmp_path) != original


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS UID and symlink semantics')
def test_darwin_endpoint_ignores_process_session_and_resolves_root_alias(monkeypatch, tmp_path):
    monkeypatch.setattr(ipc, 'sys', SimpleNamespace(platform='darwin'))
    original = ipc.endpoint(tmp_path)
    monkeypatch.setattr(ipc.os, 'getsid', lambda pid: 123456)
    assert ipc.endpoint(tmp_path) == original
    alias = tmp_path / 'alias'
    alias.symlink_to(tmp_path, target_is_directory=True)
    assert ipc.endpoint(alias) == original


def test_darwin_new_session_child_cannot_replace_active_listener(tmp_path):
    if sys.platform != 'darwin':
        pytest.skip('Finder and Terminal must share one macOS backend')
    app = QApplication.instance() or QApplication([])
    server = ipc.LocalServer(tmp_path, lambda message: {'alive': True})
    script = '''
import json,os,sys
from PySide6.QtCore import QCoreApplication
from gamepadstudio.ipc import LocalServer,endpoint
app=QCoreApplication([])
result={'endpoint':endpoint(sys.argv[1]),'session':os.getsid(0)}
try:
    server=LocalServer(sys.argv[1],lambda message:{})
except RuntimeError:
    result['listener_created']=False
else:
    result['listener_created']=True
    server.close()
print(json.dumps(result))
'''
    try:
        child = subprocess.run([sys.executable, '-c', script, str(tmp_path)],
                               start_new_session=True, check=True,
                               capture_output=True, text=True, timeout=15)
        result = json.loads(child.stdout)
        assert result['session'] != os.getsid(0)
        assert result['endpoint'] == ipc.endpoint(tmp_path)
        assert result['listener_created'] is False
        # The original listener still serves its endpoint after the rejected
        # child startup, including Qt's UserAccessOption socket handling.
        probe = QLocalSocket()
        probe.connectToServer(ipc.endpoint(tmp_path))
        assert probe.waitForConnected(1000)
        probe.abort()
        app.processEvents()
    finally:
        server.close()


def test_mac_data_path_and_autostart_are_explicit(monkeypatch, tmp_path):
    monkeypatch.setattr(ipc, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(ipc.Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'windows'))
    assert ipc.default_root() == tmp_path / 'Library' / 'Application Support' / 'GamePadStudio'
    assert ipc.autostart_supported()
    assert not ipc.autostart_enabled()


def test_windows_data_path_preserves_legacy_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(ipc, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    old_dir = tmp_path / 'DualSenseStudio'
    old_dir.mkdir()
    assert ipc.default_root() == old_dir


def test_spawn_launches_module_with_active_python_from_outside_checkout(tmp_path, monkeypatch):
    if sys.platform == 'win32':
        pytest.skip('Native Python launcher on POSIX')
    monkeypatch.chdir(tmp_path)
    command = ipc.command_line(tmp_path, '--agent-command', 'status')
    assert command[0] == sys.executable
    assert command[1:3] == ['-m', 'gamepadstudio']
    process = ipc.spawn(tmp_path, '--agent-command', 'status')
    try:
        # No server exists: actual entry/IPC request code returns status failure.
        assert process.wait(timeout=15) == 1
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_unix_listener_recovers_crashed_socket_and_preserves_live_server(tmp_path):
    if sys.platform == 'win32':
        pytest.skip('Unix socket files persist after process crashes')
    app = QApplication.instance() or QApplication([])
    name = ipc.endpoint(tmp_path)
    # Qt's relative local-server name uses the system temporary directory.
    from PySide6.QtCore import QDir
    stale_path = Path(QDir.tempPath()) / name
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(stale_path))
    stale.close()
    server = None
    try:
        server = ipc.LocalServer(tmp_path, lambda message: {'alive': True})
        with pytest.raises(RuntimeError):
            ipc.LocalServer(tmp_path, lambda message: {})
        probe = QLocalSocket()
        probe.connectToServer(name)
        assert probe.waitForConnected(1000)
        probe.abort()
        app.processEvents()
    finally:
        if server is not None:
            server.close()
        QLocalServer.removeServer(name)


def test_permission_denied_process_remains_alive(monkeypatch):
    if sys.platform == 'win32':
        pytest.skip('POSIX signal probing')
    def denied(*args):
        raise PermissionError(1, 'Operation not permitted')
    monkeypatch.setattr(ipc.os, 'kill', denied)
    assert ipc.is_process_alive(34567)

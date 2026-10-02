"""Real local sockets carry portable presets without weakening state backpressure."""
from concurrent.futures import ThreadPoolExecutor
import copy
import itertools
import json
import os
import subprocess
import sys
import time

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import pytest
from PySide6.QtCore import QEventLoop
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from gamepadstudio.ipc import (
    AgentClient, LocalServer, MAX_REPLY_BYTES, MAX_REQUEST_BYTES,
    STATE_BACKPRESSURE_BYTES, endpoint, request,
)
from gamepadstudio.profile_transfer import preview_profile_import
from gamepadstudio.studio_core import ConfigStore, profile_scope


def wire(message):
    return (json.dumps(message, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def pump_until(app, predicate, timeout=5.):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail('local socket operation did not complete')
        app.processEvents(QEventLoop.AllEvents, 5)
        time.sleep(.001)
    app.processEvents(QEventLoop.AllEvents, 5)


def request_over_socket(app, root, command, *, process_timeout=10., **values):
    with ThreadPoolExecutor(max_workers=1) as worker:
        process, future = start_request_process(worker, root, command, values)
        try:
            pump_until(app, future.done, timeout=process_timeout)
            stdout, stderr = future.result()
            assert process.returncode == 0, stderr
            return json.loads(stdout)
        finally:
            # A failed assertion must not strand the executor waiting for a
            # client process whose socket peer is no longer being pumped.
            if process.poll() is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass


def start_request_process(worker, root, command, values):
    # Production UI and backend have separate Qt event dispatchers. Running a
    # blocking Qt socket in a Python worker can hold the GIL needed by this
    # process's server callbacks; use a real client process for the same setup.
    script = ('import json,sys; from PySide6.QtCore import QCoreApplication; '
              'from gamepadstudio.ipc import request; app=QCoreApplication([]); '
              'values=json.loads(sys.stdin.read()); '
              'result=request(sys.argv[1],sys.argv[2],timeout=2000,**values); '
              'print(json.dumps(result,ensure_ascii=False))')
    process = subprocess.Popen([sys.executable, '-X', 'utf8', '-c', script, str(root), command],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding='utf-8',
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    future = worker.submit(process.communicate, json.dumps(values, ensure_ascii=False))
    return process, future


def connected_socket(app, root, server):
    socket = QLocalSocket(); socket.connectToServer(endpoint(root))
    pump_until(app, lambda: socket.state() == QLocalSocket.ConnectedState and bool(server.clients))
    return socket


def portable_package():
    pairs = list(itertools.combinations([str(i) for i in range(15)], 2))
    triples = list(itertools.combinations([str(i) for i in range(15)], 3))
    triggers = ['+'.join(keys) for keys in (pairs + triples)[:500]]
    return {'format': 'gamepadstudio-profile', 'version': 1,
            'source': {'family': 'xbox', 'input_kind': 'sdl_gamecontroller'},
            'profile': {'name': '暖暖按键' * 20, 'mode': 'kbm', 'options': {},
                        'mappings': {key: {'short': {'action': 'shortcut', 'value': 'Ctrl+Shift+W'},
                                           'long': {'action': 'none'}} for key in triggers}}}


def test_real_import_request_and_full_large_config_reply_survive_broadcasts(app, tmp_path):
    state = {'device_key': 'xbox:ipc-fixture', 'instance_id': 23, 'family': 'xbox',
             'is_gamecontroller': True, 'available_buttons': list(range(15)),
             'available_axes': list(range(6)), 'touchpad': False}
    package = portable_package()
    assert len(wire(package)) > 16384
    assert len(wire(package)) < 256 * 1024
    store = ConfigStore(tmp_path); store.activate_controller(state)
    for index in range(6):
        name = f'Existing preset {index}'
        store.data['profiles'][name] = copy.deepcopy(package['profile']['mappings'])
        store.data['profile_devices'][name] = profile_scope(state)
        store.data['profile_modes'][name] = 'kbm'
        store.data['profile_families'][name] = 'xbox'
    store.save()
    seen = []
    def handle(message):
        seen.append(message)
        config = store.apply_mapping_change(message['change'], state)
        return {'config': config, 'imported_profile': store.last_imported_profile}
    server = LocalServer(tmp_path, handle)
    change = {'op': 'import_profile', 'package': package,
              'expected_inputs': preview_profile_import(package, state)['input_signature']}
    try:
        with ThreadPoolExecutor(max_workers=1) as worker:
            process, future = start_request_process(worker, tmp_path, 'mapping_change',
                {'device_scope': profile_scope(state), 'instance_id': 23, 'change': change})
            deadline = time.monotonic() + 10.
            while not future.done():
                assert time.monotonic() < deadline
                app.processEvents(QEventLoop.AllEvents, 1)
                server.broadcast({'type': 'state', 'device': state, 'profile': store.data['active_profile']})
                time.sleep(.001)
            stdout, stderr = future.result()
            assert process.returncode == 0, stderr
            result = json.loads(stdout)
        assert result['ok'] is True
        assert len(wire(result)) > STATE_BACKPRESSURE_BYTES
        imported = result['imported_profile']
        assert result['config']['profiles'][imported] == store.data['profiles'][imported]
        assert len(result['config']['profiles'][imported]) == 500
        assert ConfigStore(tmp_path).data['profiles'][imported] == result['config']['profiles'][imported]
        assert seen[0]['change']['package']['profile']['name'] == package['profile']['name']
    finally:
        server.close()


def test_request_uses_utf8_instead_of_expanding_chinese_to_ascii(app, tmp_path):
    seen = []
    server = LocalServer(tmp_path, lambda message: seen.append(message) or {'received': True})
    text = '暖' * 180000
    # This fits the UTF-8 request limit but would exceed it as \uXXXX escapes.
    assert len(wire({'command': 'echo', 'text': text})) < MAX_REQUEST_BYTES
    assert len(json.dumps({'command': 'echo', 'text': text}).encode()) > MAX_REQUEST_BYTES
    try:
        result = request_over_socket(app, tmp_path, 'echo', text=text)
        assert result['ok'] and seen[0]['text'] == text
    finally:
        server.close()


def test_request_rejects_an_oversized_payload_before_connecting(tmp_path):
    result = request(tmp_path, 'echo', payload='x' * MAX_REQUEST_BYTES, timeout=10)
    assert result['ok'] is False and '1 MiB' in result['error']


def test_malicious_oversized_request_disconnects_only_its_client(app, tmp_path):
    seen = []
    server = LocalServer(tmp_path, lambda message: seen.append(message) or {'alive': True})
    offender = connected_socket(app, tmp_path, server)
    try:
        offender.write(b'{"command":"echo","payload":"' + b'x' * MAX_REQUEST_BYTES + b'"}\n')
        offender.flush()
        pump_until(app, lambda: offender.state() == QLocalSocket.UnconnectedState)
        assert seen == []
        result = request_over_socket(app, tmp_path, 'status')
        assert result['ok'] and result['alive']
        assert seen == [{'command': 'status'}]
    finally:
        offender.abort(); server.close()


def test_oversized_reply_returns_a_clear_error_over_real_socket(app, tmp_path):
    server = LocalServer(tmp_path, lambda message: {'payload': 'x' * MAX_REPLY_BYTES})
    try:
        result = request_over_socket(app, tmp_path, 'large')
        assert result['ok'] is False and '16 MiB' in result['error']
    finally:
        server.close()


def test_pending_large_reply_skips_state_then_resumes_stream_when_drained(app, tmp_path):
    server = LocalServer(tmp_path, lambda message: {'ready': True})
    socket = connected_socket(app, tmp_path, server)
    socket.setReadBufferSize(1)
    remote = next(iter(server.clients))
    reply = {'type': 'reply', 'ok': True, 'config': {'large': 'x' * (2 * 1024 * 1024)}}
    try:
        server.send(remote, reply)
        assert remote in server.pending_replies
        buffered = remote.bytesToWrite()
        server.broadcast({'type': 'state', 'must_not_queue': True})
        assert remote in server.clients
        assert remote.bytesToWrite() <= buffered
        assert remote in server.pending_replies
        socket.setReadBufferSize(MAX_REPLY_BYTES + 1)
        received = bytearray()
        def finished():
            received.extend(bytes(socket.readAll()))
            return b'\n' in received and remote not in server.pending_replies
        pump_until(app, finished)
        assert json.loads(bytes(received).split(b'\n')[0]) == reply
        assert b'must_not_queue' not in received
        server.broadcast({'type': 'state', 'profile': 'resumed'})
        def streamed():
            received.extend(bytes(socket.readAll()))
            return b'resumed' in received
        pump_until(app, streamed)
        assert socket.state() == QLocalSocket.ConnectedState
    finally:
        socket.abort(); server.close()


def test_slow_normal_state_client_still_has_small_backpressure_limit(app, tmp_path):
    server = LocalServer(tmp_path, lambda message: {})
    socket = connected_socket(app, tmp_path, server); socket.setReadBufferSize(1)
    try:
        for index in range(200):
            server.broadcast({'type': 'state', 'index': index, 'padding': 'x' * 16384})
            if not server.clients:
                break
        assert not server.clients
        assert not server.pending_replies
        assert STATE_BACKPRESSURE_BYTES == 128 * 1024
    finally:
        socket.abort(); server.close()


def test_pending_reply_disconnect_cleans_bookkeeping(app, tmp_path):
    server = LocalServer(tmp_path, lambda message: {})
    socket = connected_socket(app, tmp_path, server); socket.setReadBufferSize(1)
    remote = next(iter(server.clients))
    try:
        server.send(remote, {'type': 'reply', 'ok': True, 'large': 'x' * (2 * 1024 * 1024)})
        assert remote in server.pending_replies
        socket.abort()
        pump_until(app, lambda: not server.clients)
        assert not server.pending_replies
    finally:
        socket.abort(); server.close()


def test_agent_client_utf8_send_and_size_limit_keep_normal_state_stream(app, tmp_path):
    seen = []
    def handle(message):
        seen.append(message)
        return {'device': {'name': 'fixture'}, 'profile': 'current'}
    server = LocalServer(tmp_path, handle)
    client = AgentClient(tmp_path); client.timer.stop()
    try:
        pump_until(app, lambda: client.connected and bool(client.status))
        text = '暖' * 180000
        assert client.send('echo', text=text)
        pump_until(app, lambda: any(row.get('command') == 'echo' for row in seen))
        assert seen[-1]['text'] == text
        assert not client.send('oversized', padding='x' * MAX_REQUEST_BYTES)
        app.processEvents()
        assert not any(row['command'] == 'oversized' for row in seen)
        server.broadcast({'type': 'state', 'device': {'name': '正常状态'}, 'profile': 'updated'})
        pump_until(app, lambda: client.status.get('profile') == 'updated')
        assert client.state['name'] == '正常状态' and client.connected
    finally:
        client.close(); server.close()


def test_queued_request_waits_for_large_reply_instead_of_growing_output_queue(app, tmp_path):
    seen = []
    def handle(message):
        seen.append(message['command'])
        return {'large': 'x' * (2 * 1024 * 1024)} if message['command'] == 'large' else {'done': True}
    server = LocalServer(tmp_path, handle)
    socket = connected_socket(app, tmp_path, server); socket.setReadBufferSize(1)
    try:
        socket.write(wire({'command': 'large'}) + wire({'command': 'next'})); socket.flush()
        pump_until(app, lambda: bool(server.pending_replies))
        assert seen == ['large']
        assert len(next(iter(server.clients.values()))) < MAX_REQUEST_BYTES
        socket.setReadBufferSize(MAX_REPLY_BYTES + 1)
        data = bytearray()
        def complete():
            data.extend(bytes(socket.readAll()))
            return data.count(b'\n') == 2
        pump_until(app, complete)
        messages = [json.loads(line) for line in bytes(data).splitlines()]
        assert len(messages[0]['large']) == 2 * 1024 * 1024
        assert messages[1]['done'] and seen == ['large', 'next']
    finally:
        socket.abort(); server.close()


def test_giant_state_packet_cannot_bypass_small_backpressure(app, tmp_path):
    server = LocalServer(tmp_path, lambda message: {})
    socket = connected_socket(app, tmp_path, server)
    try:
        server.broadcast({'type': 'state', 'payload': 'x' * STATE_BACKPRESSURE_BYTES})
        assert not server.clients
        assert not server.pending_replies
    finally:
        socket.abort(); server.close()


def malicious_reply_peer(root):
    """A native peer bypasses LocalServer's outgoing cap to probe receivers."""
    native = QLocalServer()
    assert native.listen(endpoint(root))
    peers, sent = [], []
    payload = b'{"type":"reply","ok":true,"payload":"' + b'x' * MAX_REPLY_BYTES + b'"}\n'
    def accept():
        peer = native.nextPendingConnection(); peers.append(peer)
        def respond():
            peer.readAll()
            if not sent:
                sent.append(True); peer.write(payload); peer.flush()
        peer.readyRead.connect(respond)
    native.newConnection.connect(accept)
    return native, peers, sent


def test_request_receiver_rejects_an_oversized_frame_from_a_native_peer(app, tmp_path):
    native, peers, sent = malicious_reply_peer(tmp_path)
    try:
        # CI can deliver this deliberate 16 MiB frame in many small reads
        # while other test modules are active on the same runner.
        result = request_over_socket(app, tmp_path, 'status', process_timeout=30.)
        assert sent and result['ok'] is False and '16 MiB' in result['error']
    finally:
        for peer in peers: peer.abort()
        native.close()


def test_agent_client_receiver_disconnects_an_oversized_native_peer(app, tmp_path):
    native, peers, sent = malicious_reply_peer(tmp_path)
    client = AgentClient(tmp_path); client.timer.stop()
    try:
        pump_until(app, lambda: bool(sent))
        pump_until(app, lambda: client.socket.state() == QLocalSocket.UnconnectedState)
        assert not client.connected and not client.buffer and not client.status
    finally:
        client.close()
        for peer in peers: peer.abort()
        native.close()

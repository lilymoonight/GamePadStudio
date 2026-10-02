"""User/session-scoped local IPC. No network listener and no arbitrary execution RPC."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from PySide6.QtCore import QObject, QTimer, Signal, QLockFile
from PySide6.QtNetwork import QLocalServer, QLocalSocket

MAX_REQUEST_BYTES = 1024 * 1024
MAX_REPLY_BYTES = 16 * 1024 * 1024
STATE_BACKPRESSURE_BYTES = 128 * 1024


def _encode_message(message, maximum):
    data = (json.dumps(message, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
    if len(data) > maximum:
        raise ValueError('请求内容超过 1 MiB，无法发送' if maximum == MAX_REQUEST_BYTES
                         else '后台回复超过 16 MiB，无法完整传送')
    return data


def default_root():
    local_appdata = Path(os.environ.get('LOCALAPPDATA',str(Path.home())))
    new_dir = local_appdata / 'GamePadStudio'
    old_dir = local_appdata / 'DualSenseStudio'
    if not new_dir.exists() and old_dir.exists():
        return old_dir
    if new_dir.exists() and old_dir.exists():
        old_cfg = old_dir / 'studio.json'
        new_cfg = new_dir / 'studio.json'
        if old_cfg.is_file() and (not new_cfg.is_file() or old_cfg.stat().st_mtime > new_cfg.stat().st_mtime):
            try:
                import shutil
                shutil.copy2(old_cfg, new_cfg)
            except Exception:
                pass
    return new_dir


def endpoint(root, role='agent', legacy=False):
    session=ctypes.c_ulong()
    ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(),ctypes.byref(session))
    identity=f'{Path(root).resolve()}|{os.environ.get("USERNAME","")}|{session.value}'.casefold()
    prefix = 'DualSenseStudio-' if legacy else 'GamePadStudio-'
    return prefix+role+'-'+hashlib.sha256(identity.encode()).hexdigest()[:20]


def command_line(root, *args):
    if getattr(sys,'frozen',False):
        command=[sys.executable]
    else:
        project_root = Path(__file__).resolve().parents[1]
        venv_pythonw = project_root / '.venv' / 'Scripts' / 'pythonw.exe'
        if venv_pythonw.exists():
            python = venv_pythonw
        else:
            python = Path(sys.executable).with_name('pythonw.exe')
        command = [str(python), str(project_root / 'main.py')]
    return [*command,'--data-dir',str(Path(root).resolve()),*args]


def spawn(root,*args):
    flags = subprocess.CREATE_NO_WINDOW
    if sys.platform == 'win32':
        flags |= getattr(subprocess, 'DETACHED_PROCESS', 0x00000008)
        flags |= getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x00000200)
    return subprocess.Popen(command_line(root,*args),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,creationflags=flags,close_fds=True)


def request(root, command, role='agent', timeout=1200, **values):
    try:
        payload = _encode_message({'command': command, **values}, MAX_REQUEST_BYTES)
    except (ValueError, TypeError, UnicodeError) as exc:
        return {'type': 'reply', 'ok': False, 'error': str(exc)}
    for legacy in (False, True):
        socket=QLocalSocket();socket.setReadBufferSize(MAX_REPLY_BYTES + 1)
        socket.connectToServer(endpoint(root,role,legacy=legacy))
        if socket.waitForConnected(timeout):
            if socket.write(payload) < 0:
                socket.abort();continue
            socket.flush()
            data=bytearray()
            while socket.waitForReadyRead(timeout):
                data.extend(bytes(socket.readAll()))
                while b'\n' in data:
                    line,_,rest=data.partition(b'\n');data=bytearray(rest)
                    if len(line) + 1 > MAX_REPLY_BYTES:
                        socket.abort()
                        return {'type': 'reply', 'ok': False, 'error': '后台回复超过 16 MiB，无法完整传送'}
                    try: result=json.loads(line)
                    except (ValueError, UnicodeError): continue
                    if not isinstance(result, dict): continue
                    if result.get('type')=='reply':socket.disconnectFromServer();return result
                if len(data) >= MAX_REPLY_BYTES:
                    socket.abort()
                    return {'type': 'reply', 'ok': False, 'error': '后台回复超过 16 MiB，无法完整传送'}
            socket.abort()
    return None


def read_lock_pid(lock_path: Path) -> int:
    """从 QLockFile 锁文件中提取持有进程 PID"""
    try:
        if lock_path.exists():
            content = lock_path.read_text(encoding='utf-8', errors='ignore').strip()
            if content:
                first_line = content.splitlines()[0].strip()
                if first_line.isdigit():
                    return int(first_line)
    except Exception:
        pass
    return 0


def is_process_alive(pid: int) -> bool:
    """判断指定 PID 进程是否依然存活"""
    if pid <= 0:
        return False
    if sys.platform == 'win32':
        try:
            hproc = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not hproc:
                return False
            exit_code = ctypes.c_ulong()
            ret = ctypes.windll.kernel32.GetExitCodeProcess(hproc, ctypes.byref(exit_code))
            ctypes.windll.kernel32.CloseHandle(hproc)
            return bool(ret and exit_code.value == 259)
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def terminate_pid(pid: int, timeout_ms: int = 800) -> bool:
    """强行终止残留孤儿进程并等待其释放内核句柄与手柄占用"""
    if pid <= 0 or pid == os.getpid():
        return False
    if sys.platform == 'win32':
        # 1. 尝试 Win32 API 直接终止
        try:
            hproc = ctypes.windll.kernel32.OpenProcess(0x0001 | 0x00100000, False, pid)
            if hproc:
                ctypes.windll.kernel32.TerminateProcess(hproc, 1)
                ctypes.windll.kernel32.WaitForSingleObject(hproc, timeout_ms)
                ctypes.windll.kernel32.CloseHandle(hproc)
                if not is_process_alive(pid):
                    return True
        except Exception:
            pass

        # 2. 若 API 终止失败 (如权限受限或句柄锁定)，回退至 taskkill 强制终止
        try:
            import subprocess
            subprocess.run(['taskkill', '/F', '/PID', str(pid)], capture_output=True, timeout=2)
            time.sleep(0.1)
            return not is_process_alive(pid)
        except Exception:
            pass
    else:
        try:
            import signal
            os.kill(pid, signal.SIGKILL)
            return True
        except Exception:
            pass
    return False


def cleanup_stale_agent(root: Path):
    """Remove a dead process's lock; never kill a live input owner on startup."""
    root = Path(root).resolve()
    lock_path = root / 'agent.lock'
    old_pid = read_lock_pid(lock_path)
    if old_pid and (old_pid == os.getpid() or is_process_alive(old_pid)):
        return False
    lock = QLockFile(str(lock_path))
    lock.setStaleLockTime(0)
    return not lock_path.exists() or lock.removeStaleLockFile()


def cleanup_stale_ui(root: Path):
    """检测并清理之前残留或卡死的旧 Studio 界面进程与孤儿锁"""
    root = Path(root).resolve()
    lock_path = root / 'studio.lock'
    old_pid = read_lock_pid(lock_path)
    if old_pid and old_pid != os.getpid() and is_process_alive(old_pid):
        try:
            request(root, 'exit', role='ui', timeout=300)
            time.sleep(0.1)
        except Exception:
            pass
        if is_process_alive(old_pid):
            terminate_pid(old_pid, timeout_ms=500)
    try:
        if lock_path.exists():
            lock_path.unlink(missing_ok=True)
    except Exception:
        pass


class LocalServer(QObject):
    def __init__(self,root,handler,role='agent',parent=None):
        super().__init__(parent);self.handler=handler;self.clients={};self.pending_replies=set()
        self.server=QLocalServer(self);self.server.setSocketOptions(QLocalServer.UserAccessOption)
        if not self.server.listen(endpoint(root,role)):raise RuntimeError(self.server.errorString())
        self.server.newConnection.connect(self.accept)

    def accept(self):
        while self.server.hasPendingConnections():
            socket=self.server.nextPendingConnection();self.clients[socket]=bytearray()
            socket.setReadBufferSize(MAX_REQUEST_BYTES + 1)
            socket.readyRead.connect(lambda s=socket:self.read(s))
            socket.disconnected.connect(lambda s=socket:self.remove(s))
            socket.bytesWritten.connect(lambda _count, s=socket:self.reply_progress(s))

    def remove(self,socket):
        self.pending_replies.discard(socket);self.clients.pop(socket,None);socket.deleteLater()

    def reply_progress(self, socket):
        if socket not in self.clients:
            return
        if socket in self.pending_replies and socket.bytesToWrite() == 0:
            self.pending_replies.discard(socket)
            # One bounded request buffer can wait behind a large reply. Do not
            # queue another full configuration until the previous one drains.
            if self.clients[socket]:
                self.read(socket)

    def read(self,socket):
        buffer=self.clients.get(socket)
        if buffer is None:return
        buffer.extend(bytes(socket.readAll()))
        if socket in self.pending_replies:
            if len(buffer) > MAX_REQUEST_BYTES: socket.abort()
            return
        while b'\n' in buffer:
            line,_,rest=buffer.partition(b'\n');buffer[:]=rest
            if len(line) + 1 > MAX_REQUEST_BYTES: socket.abort();return
            try:
                message=json.loads(line)
                if not isinstance(message,dict):raise ValueError('Invalid message')
                result=self.handler(message) or {}
                self.send(socket,{'type':'reply','ok':True,**result})
            except Exception as exc:self.send(socket,{'type':'reply','ok':False,'error':str(exc)})
            if socket not in self.clients or socket.state() == QLocalSocket.UnconnectedState:
                return
            if socket in self.pending_replies:
                break
        if len(buffer) > MAX_REQUEST_BYTES or (len(buffer) == MAX_REQUEST_BYTES and b'\n' not in buffer):
            socket.abort()

    def send(self,socket,message):
        reply = message.get('type') == 'reply'
        if socket in self.pending_replies:
            return
        if socket.bytesToWrite()>STATE_BACKPRESSURE_BYTES:socket.abort();return
        if reply:
            try:
                data = _encode_message(message, MAX_REPLY_BYTES)
            except (ValueError, TypeError, UnicodeError) as exc:
                data = _encode_message({'type': 'reply', 'ok': False, 'error': str(exc)}, MAX_REPLY_BYTES)
            if socket.bytesToWrite() + len(data) > STATE_BACKPRESSURE_BYTES:
                self.pending_replies.add(socket)
        else:
            data = _encode_message(message, MAX_REPLY_BYTES)
            if len(data) > STATE_BACKPRESSURE_BYTES:
                socket.abort();return
        if socket.write(data) < 0:
            self.pending_replies.discard(socket);socket.abort();return
        socket.flush()

    def broadcast(self,message):
        for socket in list(self.clients):self.send(socket,message)

    def close(self):
        for socket in list(self.clients):socket.disconnectFromServer()
        self.pending_replies.clear()
        self.server.close()


class AgentClient(QObject):
    event=Signal(dict)
    def __init__(self,root,parent=None):
        super().__init__(parent);self.root=root;self.socket=QLocalSocket(self);self.buffer=bytearray()
        self.socket.setReadBufferSize(MAX_REPLY_BYTES + 1)
        self.state=None;self.status={};self.connected=False
        self.socket.connected.connect(self.online);self.socket.disconnected.connect(self.offline)
        self.socket.readyRead.connect(self.receive)
        self.timer=QTimer(self);self.timer.timeout.connect(self.try_connect);self.timer.start(1000);self.try_connect()

    def try_connect(self):
        if self.socket.state()==QLocalSocket.UnconnectedState:
            self.socket.connectToServer(endpoint(self.root))
            if self.socket.state()==QLocalSocket.UnconnectedState:
                self.socket.connectToServer(endpoint(self.root, legacy=True))

    def online(self):
        self.connected=True;self.send('status')

    def offline(self):
        self.connected=False;self.state=None;self.status={};self.buffer.clear()

    def send(self,command,**values):
        if not self.connected:return False
        try: payload = _encode_message({'command': command, **values}, MAX_REQUEST_BYTES)
        except (ValueError, TypeError, UnicodeError): return False
        if self.socket.write(payload) < 0:return False
        self.socket.flush();return True

    def receive(self):
        self.buffer.extend(bytes(self.socket.readAll()))
        while b'\n' in self.buffer:
            line,_,rest=self.buffer.partition(b'\n');self.buffer[:]=rest
            if len(line) + 1 > MAX_REPLY_BYTES:self.socket.abort();self.buffer.clear();return
            try:message=json.loads(line)
            except ValueError:continue
            if not isinstance(message, dict):continue
            if message.get('type')=='state' or 'device' in message:
                self.state=message.get('device');self.status=message
            self.event.emit(message)
        if len(self.buffer) >= MAX_REPLY_BYTES:self.socket.abort();self.buffer.clear()

    def close(self):
        self.timer.stop();self.socket.disconnectFromServer()


class RemoteDevice:
    def __init__(self,client):self.client=client
    def read(self):return self.client.state
    @property
    def available(self):return self.client.status.get('devices',[])
    def scan(self):pass
    def close(self):self.client.close()
    def led(self,color):return self.client.send('led',color=color)
    def rumble(self,strength):return self.client.send('rumble',strength=strength)


RUN_KEY=r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_NAME='GamePadStudioAgent'
LEGACY_RUN_NAME='DualSenseStudioAgent'


def autostart_enabled():
    import winreg
    for name in (RUN_NAME, LEGACY_RUN_NAME):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,RUN_KEY) as key:
                if winreg.QueryValueEx(key,name)[0]:
                    return True
        except FileNotFoundError:
            continue
    return False


def set_autostart(root,enabled):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key,RUN_NAME,0,winreg.REG_SZ,subprocess.list2cmdline(command_line(root,'--agent')))
            try: winreg.DeleteValue(key,LEGACY_RUN_NAME)
            except FileNotFoundError: pass
        else:
            for name in (RUN_NAME, LEGACY_RUN_NAME):
                try: winreg.DeleteValue(key,name)
                except FileNotFoundError: pass

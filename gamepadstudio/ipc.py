"""User/session-scoped local IPC. No network listener and no arbitrary execution RPC."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def default_root():
    local_appdata = Path(os.environ.get('LOCALAPPDATA',str(Path.home())))
    new_dir = local_appdata / 'GamePadStudio'
    old_dir = local_appdata / 'DualSenseStudio'
    if not new_dir.exists() and old_dir.exists():
        return old_dir
    return new_dir


def endpoint(root, role='agent'):
    session=ctypes.c_ulong()
    ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(),ctypes.byref(session))
    identity=f'{Path(root).resolve()}|{os.environ.get("USERNAME","")}|{session.value}'.casefold()
    return 'GamePadStudio-'+role+'-'+hashlib.sha256(identity.encode()).hexdigest()[:20]


def command_line(root, *args):
    if getattr(sys,'frozen',False):
        command=[sys.executable]
    else:
        python=Path(sys.executable).with_name('pythonw.exe')
        command=[str(python),str(Path(__file__).resolve().parents[1]/'main.py')]
    return [*command,'--data-dir',str(Path(root).resolve()),*args]


def spawn(root,*args):
    return subprocess.Popen(command_line(root,*args),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW,close_fds=True)


def request(root, command, role='agent', timeout=1200, **values):
    socket=QLocalSocket();socket.connectToServer(endpoint(root,role))
    if not socket.waitForConnected(timeout):return None
    socket.write((json.dumps({'command':command,**values})+'\n').encode());socket.flush()
    data=bytearray()
    while socket.waitForReadyRead(timeout):
        data.extend(bytes(socket.readAll()))
        while b'\n' in data:
            line,_,rest=data.partition(b'\n');data=bytearray(rest)
            result=json.loads(line)
            if result.get('type')=='reply':socket.disconnectFromServer();return result
    socket.abort();return None


class LocalServer(QObject):
    def __init__(self,root,handler,role='agent',parent=None):
        super().__init__(parent);self.handler=handler;self.clients={}
        self.server=QLocalServer(self);self.server.setSocketOptions(QLocalServer.UserAccessOption)
        if not self.server.listen(endpoint(root,role)):raise RuntimeError(self.server.errorString())
        self.server.newConnection.connect(self.accept)

    def accept(self):
        while self.server.hasPendingConnections():
            socket=self.server.nextPendingConnection();self.clients[socket]=bytearray()
            socket.readyRead.connect(lambda s=socket:self.read(s))
            socket.disconnected.connect(lambda s=socket:self.remove(s))

    def remove(self,socket):
        self.clients.pop(socket,None);socket.deleteLater()

    def read(self,socket):
        buffer=self.clients.get(socket)
        if buffer is None:return
        buffer.extend(bytes(socket.readAll()))
        if len(buffer)>16384:socket.abort();return
        while b'\n' in buffer:
            line,_,rest=buffer.partition(b'\n');buffer[:]=rest
            try:
                message=json.loads(line)
                if not isinstance(message,dict):raise ValueError('Invalid message')
                result=self.handler(message) or {}
                self.send(socket,{'type':'reply','ok':True,**result})
            except Exception as exc:self.send(socket,{'type':'reply','ok':False,'error':str(exc)})

    def send(self,socket,message):
        if socket.bytesToWrite()>131072:socket.abort();return
        socket.write((json.dumps(message,ensure_ascii=False)+'\n').encode());socket.flush()

    def broadcast(self,message):
        for socket in list(self.clients):self.send(socket,message)

    def close(self):
        for socket in list(self.clients):socket.disconnectFromServer()
        self.server.close()


class AgentClient(QObject):
    event=Signal(dict)
    def __init__(self,root,parent=None):
        super().__init__(parent);self.root=root;self.socket=QLocalSocket(self);self.buffer=bytearray()
        self.state=None;self.status={};self.connected=False
        self.socket.connected.connect(self.online);self.socket.disconnected.connect(self.offline)
        self.socket.readyRead.connect(self.receive)
        self.timer=QTimer(self);self.timer.timeout.connect(self.try_connect);self.timer.start(1000);self.try_connect()

    def try_connect(self):
        if self.socket.state()==QLocalSocket.UnconnectedState:self.socket.connectToServer(endpoint(self.root))

    def online(self):
        self.connected=True;self.send('status')

    def offline(self):
        self.connected=False;self.state=None;self.status={};self.buffer.clear()

    def send(self,command,**values):
        if not self.connected:return False
        self.socket.write((json.dumps({'command':command,**values})+'\n').encode());self.socket.flush();return True

    def receive(self):
        self.buffer.extend(bytes(self.socket.readAll()))
        while b'\n' in self.buffer:
            line,_,rest=self.buffer.partition(b'\n');self.buffer[:]=rest
            try:message=json.loads(line)
            except ValueError:continue
            if message.get('type')=='state' or 'device' in message:
                self.state=message.get('device');self.status=message
            self.event.emit(message)

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

"""Independent, headless mapping process for the interactive Windows user session."""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import QCoreApplication, QLockFile, QObject, QTimer, Signal
from .studio_core import ConfigStore, GestureEngine, BUTTONS
from .device import Device
from .actions import WindowsActions, launch_command
from .screenshot_service import take_screenshot
from .replay_service import ReplayBufferEngine
from .haptic_engine import HapticEngine
from .ipc import LocalServer, request, spawn, default_root
from .kbm_mapper import NIKKI_PROFILE_NAME, NikkiKbmEngine
from .virtual_kbm import VirtualKbmEngine, NIKKI_PRESET_CONFIG


class Agent(QObject):
    captured=Signal(str,str)
    def __init__(self,root,device=None,actions=None):
        super().__init__();self.root=Path(root);self.store=ConfigStore(self.root);self.config=self.store.data
        if not self.store.path.exists():self.store.save()
        self.device=device or Device();self.actions=actions or WindowsActions()
        self.nikki_engine=NikkiKbmEngine(self.actions,on_chord=self.log)
        self.virtual_kbm_engine=VirtualKbmEngine(self.actions,on_notice=self.log)
        schemes = self.store.data.get("virtual_kbm_schemes", {})
        if not schemes:
            from .virtual_kbm import NIKKI_PRESET_CONFIG, GENERAL_PRESET_CONFIG, NIKKI_SCHEME_NAME, GENERAL_SCHEME_NAME
            import copy
            schemes = {
                NIKKI_SCHEME_NAME: copy.deepcopy(NIKKI_PRESET_CONFIG),
                GENERAL_SCHEME_NAME: copy.deepcopy(GENERAL_PRESET_CONFIG),
            }
            schemes[NIKKI_SCHEME_NAME]["enabled"] = (self.config.get('active_profile') == NIKKI_PROFILE_NAME)
            self.store.data["virtual_kbm_schemes"] = schemes
            self.store.save()
        active_s = next((s for s in schemes.values() if s.get("enabled")), None)
        if active_s:
            self.virtual_kbm_engine.load_scheme(active_s)
        else:
            self.virtual_kbm_engine.scheme["enabled"] = False
        self.device.preferred_key=self.config.get('preferred_controller','')
        self.engine=GestureEngine(self.dispatch,self.config['long_press'])
        self.state=None;self.enabled=True;self.suspended_until=0.;self.blocked=set();self.last_touch=None
        self.busy=False;self.last_capture=0.;self.last_buttons=set();self.last_ui=0.;self.closed=False
        self.executor=ThreadPoolExecutor(max_workers=1);self.captured.connect(self.on_captured)
        self.server=LocalServer(self.root,self.handle)
        self.timer=QTimer(self);self.timer.timeout.connect(self.poll);self.timer.start(16)
        self.scan_timer=QTimer(self);self.scan_timer.timeout.connect(self.scan);self.scan_timer.start(1000)
        self.broadcast_timer=QTimer(self);self.broadcast_timer.timeout.connect(self.broadcast);self.broadcast_timer.start(33)
        self.replay_engine = ReplayBufferEngine(
            save_dir=Path(self.config.get('save_dir', self.root / 'Captures')),
            minutes=self.config.get('replay_buffer_minutes', 5),
            codec=self.config.get('replay_codec', 'hevc'),
            bitrate_mbps=self.config.get('replay_bitrate_mbps', 50),
            fps=self.config.get('replay_fps', 30),
            on_event=self.log
        )
        if self.config.get('replay_buffer_enabled', False):
            self.replay_engine.start()
        self.haptic_engine = HapticEngine(self.device, on_notice=self.log)
        self.haptic_engine.set_config(
            sound_enabled=self.config.get('capture_sound_enabled', True),
            haptics_enabled=self.config.get('capture_haptics_enabled', True),
            intensity=self.config.get('haptic_intensity', 1.0),
            profile=self.config.get('haptic_profile', 'crisp')
        )
        self.scan();self.log('后台映射已启动')

    def status(self):
        return {'pid':os.getpid(),'device':self.state,'enabled':self.enabled,'capturing':self.busy,
                'devices':getattr(self.device,'available',[]),
                'profile':self.config['active_profile'],'suspended':time.monotonic()<self.suspended_until}

    def broadcast(self):self.server.broadcast({'type':'state',**self.status()})

    def log(self,text):
        row={'time':datetime.now().isoformat(),'message':text}
        with (self.root/'agent-events.jsonl').open('a',encoding='utf-8') as file:file.write(json.dumps(row,ensure_ascii=False)+'\n')
        self.server.broadcast({'type':'notice',**row})

    def release(self):
        self.engine.reset();self.actions.release_all();self.nikki_engine.reset();self.last_touch=None
        if hasattr(self, 'virtual_kbm_engine'): self.virtual_kbm_engine.reset()
        self.blocked.update(self.state['buttons'] if self.state else [])

    def scan(self):
        try:self.device.scan()
        except Exception as exc:self.log('连接失败：'+str(exc))

    def poll(self):
        try:
            previous=self.state;self.state=self.device.read()
            identity=lambda s: (True,s.get('instance_id')) if s else None
            if identity(previous)!=identity(self.state):
                self.release();self.last_buttons=set()
                self.log('手柄已连接' if self.state else '手柄已断开')
                if self.state:
                    self.store.activate_controller(self.state);self.store.save()
                if self.state and self.state['led']:self.device.led(self.config['led'])
            if not self.state:return
            buttons=set(self.state['buttons']);new=buttons-self.last_buttons;self.last_buttons=buttons
            if new:
                self.server.broadcast({'type':'buttons','buttons':sorted(new)})
                names = [BUTTONS.get(b, str(b)) for b in sorted(new)]
                self.log(f"按键输入: {' / '.join(names)}")
            if not self.enabled or time.monotonic()<self.suspended_until:
                self.release();return
            self.blocked.intersection_update(buttons)
            if hasattr(self, 'virtual_kbm_engine') and (
                self.virtual_kbm_engine.scheme.get("enabled", False)
                or self.config.get('active_profile') == NIKKI_PROFILE_NAME
            ):
                self.virtual_kbm_engine.update(self.state)
            else:
                self.engine.update(buttons - self.blocked, self.store.mappings)
                touch=self.state['touch']
                if self.config['touch_mouse'] and touch and self.last_touch:
                    dx,dy=(touch[0]-self.last_touch[0])*1600,(touch[1]-self.last_touch[1])*900
                    if abs(dx)<250 and abs(dy)<250:self.actions.move_mouse(dx,dy)
                self.last_touch=touch or None
        except Exception as exc:
            self.enabled=False
            try:self.release()
            except Exception:pass
            self.log('映射已暂停：'+str(exc))

    def handle(self,message):
        command=message.get('command')
        if command=='status':return self.status()
        if command in ('pause','resume'):
            self.release();self.enabled=command=='resume';self.log('映射已恢复' if self.enabled else '映射已暂停')
        elif command=='suspend':
            self.release();self.suspended_until=time.monotonic()+min(3.,max(0.,float(message.get('seconds',2))))
        elif command=='reload':
            self.release()
            self.store=ConfigStore(self.root)
            self.config=self.store.data
            self.engine.threshold=self.config['long_press']
            schemes = self.store.data.get("virtual_kbm_schemes", {})
            if not schemes:
                from .virtual_kbm import NIKKI_PRESET_CONFIG, GENERAL_PRESET_CONFIG, NIKKI_SCHEME_NAME, GENERAL_SCHEME_NAME
                import copy
                schemes = {
                    NIKKI_SCHEME_NAME: copy.deepcopy(NIKKI_PRESET_CONFIG),
                    GENERAL_SCHEME_NAME: copy.deepcopy(GENERAL_PRESET_CONFIG),
                }
                schemes[NIKKI_SCHEME_NAME]["enabled"] = True
                self.store.data["virtual_kbm_schemes"] = schemes
                self.store.save()
            active_s = next((s for s in schemes.values() if s.get("enabled")), None)
            if active_s:
                self.virtual_kbm_engine.load_scheme(active_s)
            else:
                self.virtual_kbm_engine.scheme["enabled"] = False
            if hasattr(self, 'replay_engine'):
                self.replay_engine.stop()
                self.replay_engine.save_dir = Path(self.config.get('save_dir', self.root / 'Captures'))
                self.replay_engine.minutes = self.config.get('replay_buffer_minutes', 5)
                self.replay_engine.codec = self.config.get('replay_codec', 'hevc')
                self.replay_engine.bitrate_mbps = self.config.get('replay_bitrate_mbps', 50)
                self.replay_engine.fps = self.config.get('replay_fps', 30)
                if self.config.get('replay_buffer_enabled', False):
                    self.replay_engine.start()
            if hasattr(self, 'haptic_engine'):
                self.haptic_engine.set_config(
                    sound_enabled=self.config.get('capture_sound_enabled', True),
                    haptics_enabled=self.config.get('capture_haptics_enabled', True),
                    intensity=self.config.get('haptic_intensity', 1.0),
                    profile=self.config.get('haptic_profile', 'crisp')
                )
            if self.state and self.state['led']:self.device.led(self.config['led'])
        elif command=='select_device':
            instance=message.get('instance_id')
            if type(instance) is not int:raise ValueError('无效设备')
            self.release();self.device.select(instance);self.poll();self.broadcast()
            if self.state:
                self.config['preferred_controller']=self.state.get('profile_key','')
                self.device.preferred_key=self.config['preferred_controller'];self.store.save()
            self.log('已切换手柄：'+self.state['name'] if self.state else '设备已断开')
        elif command=='scan':self.scan();self.poll();self.broadcast()
        elif command=='capture':self.capture()
        elif command=='replay_record':self.replay_record()
        elif command=='save_replay':
            if hasattr(self, 'replay_engine') and self.replay_engine.is_running():
                path = self.replay_engine.save_replay(message.get('title'))
                if path and hasattr(self, 'haptic_engine'):
                    self.haptic_engine.trigger_feedback('replay_saved')
                return {'path': path, 'status': 'success' if path else 'empty'}
            else:
                self.replay_record()
                return {'mode': 'system'}
        elif command=='get_replay_status':
            if hasattr(self, 'replay_engine'):
                return self.replay_engine.get_status()
            return {'enabled': False, 'running': False}
        elif command=='test_haptics':
            pattern = message.get('pattern', 'shutter')
            if hasattr(self, 'haptic_engine'):
                self.haptic_engine.trigger_feedback(pattern)
            return {'status': 'ok', 'pattern': pattern}
        elif command=='record_toggle':self.record_toggle()
        elif command=='rumble':
            success=self.device.rumble(max(0.,min(1.,float(message.get('strength',.35)))))
            self.log('振动已发送' if success else '振动不可用');return {'supported':success}
        elif command=='led':
            color=message.get('color','')
            if not isinstance(color,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',color):raise ValueError('无效颜色')
            success=self.device.led(color);self.log('灯条已更新' if success else '灯条不可用');return {'supported':success}
        elif command=='stop':QTimer.singleShot(80,QCoreApplication.quit)
        else:raise ValueError('不支持的操作')
        return self.status()

    def dispatch(self,binding,down=True):
        action=binding.get('action','none')
        if action=='hold':self.actions.hold(binding.get('value',''),down);return
        if not down or action=='none':return
        if action=='capture':self.capture()
        elif action=='replay_record':self.replay_record()
        elif action=='record_toggle':self.record_toggle()
        elif action in ('home','gallery'):
            now=time.monotonic()
            if now-self.last_ui<1:return
            self.last_ui=now
            # The UI command is handled in a short-lived helper; mapping polling never blocks.
            spawn(self.root,'--page','gallery' if action=='gallery' else 'home')
        elif action=='shortcut':self.actions.shortcut(binding['value'])
        elif action=='launch':launch_command(binding['executable'],binding.get('arguments',''))
        else:self.actions.media(action)

    def replay_record(self):
        now=time.monotonic()
        if now-getattr(self,'last_replay',0.)<1.0:return
        self.last_replay=now
        # 1. 优先使用本地 4K 极清硬件编码回放缓冲区
        if hasattr(self, 'replay_engine') and self.replay_engine.is_running():
            path = self.replay_engine.save_replay()
            if path:
                if hasattr(self, 'haptic_engine'):
                    self.haptic_engine.trigger_feedback('replay_saved')
                self.server.broadcast({'type':'replay_record','status':'success','path':path})
                return
        # 2. 未启用或切片未就绪时平滑回退触发系统 Windows Game Bar (Win+Alt+G)
        try:
            self.actions.shortcut('Win+Alt+G')
            self.log('已触发系统回放录制 (Win+Alt+G)')
            self.server.broadcast({'type':'replay_record','status':'success','mode':'system'})
        except Exception as exc:
            self.log('回放录制触发失败：'+str(exc))

    def record_toggle(self):
        now=time.monotonic()
        if now-getattr(self,'last_record_toggle',0.)<1.0:return
        self.last_record_toggle=now
        try:
            self.actions.shortcut('Win+Alt+R')
            self.log('已切换录屏状态 (Win+Alt+R)')
            self.server.broadcast({'type':'record_toggle','status':'success'})
        except Exception as exc:
            self.log('录屏触发失败：'+str(exc))

    def capture(self):
        now=time.monotonic()
        if self.busy or now-self.last_capture<self.config['cooldown']:return
        self.busy=True;self.last_capture=now
        folder,mode=self.config['save_dir'],self.config['capture_mode']
        def worker():
            try:self.captured.emit(take_screenshot(folder,mode=mode),'')
            except Exception as exc:self.captured.emit('',str(exc))
        self.executor.submit(worker)

    def on_captured(self,path,error):
        self.busy=False
        if not error:
            if hasattr(self, 'haptic_engine'):
                self.haptic_engine.trigger_feedback('capture')
            self.log('截图已保存 (已触发机械快门与触觉反馈)')
        else:
            self.log('截图失败：'+error)
        self.server.broadcast({'type':'capture','path':path,'error':error})

    def close(self):
        if self.closed:return
        self.closed=True;self.timer.stop();self.scan_timer.stop();self.broadcast_timer.stop()
        if hasattr(self, 'replay_engine'):
            try: self.replay_engine.stop()
            except Exception: pass
        if hasattr(self, 'haptic_engine'):
            try: self.haptic_engine.close()
            except Exception: pass
        self.release();self.device.close();self.executor.shutdown(wait=True);self.server.close()




def run(root):
    from .actions import attach_to_default_desktop
    attach_to_default_desktop()
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    app=QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    lock=QLockFile(str(root/'agent.lock'))
    lock.setStaleLockTime(2000)
    if not lock.tryLock(100):
        if request(root,'status',role='agent',timeout=200) is not None:
            return 0
        try:
            lock.removeStaleLockFile()
            (root/'agent.lock').unlink(missing_ok=True)
        except Exception:
            pass
        if not lock.tryLock(100):
            return 0
    agent=Agent(root);app.aboutToQuit.connect(agent.close)
    try:return app.exec()
    finally:agent.close();lock.unlock()

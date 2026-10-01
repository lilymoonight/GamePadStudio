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
from .studio_core import ConfigStore, GestureEngine, BUTTONS, device_config, profile_scope
from .controller_catalog import button_labels
from .device import Device
from .actions import WindowsActions, launch_command
from .screenshot_service import take_screenshot
from .replay_service import ReplayBufferEngine
from .haptic_engine import HapticEngine
from .ipc import LocalServer, request, spawn, default_root
from .mapping_engine import MappingRuntime


class Agent(QObject):
    captured=Signal(str,str)
    replay_finished=Signal(str,str)
    def __init__(self,root,device=None,actions=None):
        super().__init__();self.root=Path(root);self.store=ConfigStore(self.root);self.config=self.store.data
        if not self.store.path.exists():self.store.save()
        self.device=device or Device();self.actions=actions or WindowsActions()
        self.device.preferred_key=self.config.get('preferred_controller','')
        self.engine=MappingRuntime(self.actions, self.dispatch)
        self.state=None;self.enabled=bool(self.config.get('mapping_enabled', True));self.suspended_until=0.;self.blocked=set();self.last_touch=None
        self.preview_until = 0.
        self.busy=False;self.last_capture=0.;self.last_buttons=set();self.last_ui=0.;self.closed=False
        self.capture_device_context = None
        self.replay_device_context = None
        self.executor=ThreadPoolExecutor(max_workers=1);self.captured.connect(self.on_captured)
        self.replay_finished.connect(self.on_replay_finished)
        self.replay_busy = False
        self.server=LocalServer(self.root,self.handle)
        self.timer=QTimer(self);self.timer.timeout.connect(self.poll);self.timer.start(4)
        self.scan_timer=QTimer(self);self.scan_timer.timeout.connect(self.scan);self.scan_timer.start(1000)
        self.broadcast_timer=QTimer(self);self.broadcast_timer.timeout.connect(self.broadcast);self.broadcast_timer.start(33)
        self.replay_engine = ReplayBufferEngine(
            save_dir=Path(self.config.get('save_dir', self.root / 'Captures')),
            minutes=self.config.get('replay_buffer_minutes', 5),
            codec=self.config.get('replay_codec', 'hevc'),
            bitrate_mbps=self.config.get('replay_bitrate_mbps', 50),
            fps=self.config.get('replay_fps', 30),
            capture_mode=self.config.get('replay_capture_mode') or self.config.get('capture_mode', 'game'),
            on_event=self.log
        )
        if self.config.get('replay_buffer_enabled', False):
            self.replay_engine.start()
        self.haptic_engine = HapticEngine(self.device, on_notice=self.log)
        self.apply_device_settings()
        self.apply_gamebar_shield()
        self.scan()
        self.apply_device_cloaking()
        self.log('后台映射已启动')

    def apply_device_cloaking(self):
        settings = device_config(self.config, self.state)
        if not self.state:
            return
        vendor = self.state.get('vendor')
        product = self.state.get('product')
        if not vendor:
            return
        try:
            from .hidhide import HidHideClient
            client = HidHideClient()
            if client.is_driver_installed():
                enabled = settings.get('device_cloaking_enabled', True)
                options = {'device_path': self.state['device_path']} if self.state.get('device_path') else {}
                ok, msg = client.cloak_controller(vendor, product, **options) if enabled else client.uncloak_controller(vendor, product, **options)
                if ok:
                    self.log(f'硬件独占隐身已就绪：{msg}' if enabled else f'手柄原始输入已恢复：{msg}')
                else:
                    self.log(f'硬件独占隐身未生效：{msg}')
        except Exception as exc:
            self.log(f'硬件独占隐身配置异常：{exc}')

    def apply_device_settings(self):
        """Apply settings for the selected input device, including reconnects."""
        settings = device_config(self.config, self.state)
        if hasattr(self.device, 'set_response_curves'):
            self.device.set_response_curves(settings)
        if hasattr(self, 'haptic_engine'):
            self.haptic_engine.set_config(
                sound_enabled=settings.get('capture_sound_enabled', True),
                haptics_enabled=bool(self.state and self.state.get('rumble', True)
                                     and settings.get('haptic_engine_enabled', True)
                                     and settings.get('capture_haptics_enabled', True)),
                intensity=settings.get('haptic_intensity', 1.0),
                profile=settings.get('haptic_profile', 'crisp'),
                trigger_rumble_enabled=bool(self.state and self.state.get('trigger_rumble')
                                           and settings.get('trigger_rumble_enabled', False))
            )
        if self.state and self.state.get('led'):
            self.device.led(settings.get('led', '#5686ff'))

    def check_device_scope(self, message):
        """A delayed editor request must not edit a newly selected controller."""
        if 'device_scope' in message and message['device_scope'] != profile_scope(self.state):
            raise ValueError('输入设备已变化，请重新打开当前设备设置')

    def feedback_device_context(self):
        return (profile_scope(self.state), self.state.get('instance_id')) if self.state else None

    def completed_feedback(self, event_type, context):
        if not hasattr(self, 'haptic_engine'):
            return
        if context == self.feedback_device_context():
            self.haptic_engine.trigger_feedback(event_type)
        else:
            # Capture/replay audio is global. A completed task from a previous
            # controller must not create a new pulse on the selected one.
            self.haptic_engine.play_shutter_sound()

    def status(self):
        return {'pid':os.getpid(),'device':self.state,'enabled':self.enabled,'capturing':self.busy,
                'devices':getattr(self.device,'available',[]),
                'replay':self.replay_engine.get_status() if hasattr(self,'replay_engine') else {},
                'mapping':self.engine.feedback(), 'mapping_revision':self.config.get('mapping_revision',0),
                'profile':self.config['active_profile'],'suspended':time.monotonic()<self.suspended_until}

    def apply_gamebar_shield(self):
        if self.config.get('gamebar_shield_enabled', False):
            from .gamebar_shield import set_gamebar_shield, is_gamebar_shield_active
            if not is_gamebar_shield_active():
                ok, message = set_gamebar_shield(self.store, True)
                if not ok:
                    self.log(message)

    def broadcast(self):
        if hasattr(self, 'server') and self.server:
            self.server.broadcast({'type':'state',**self.status()})

    def log(self,text):
        row={'time':datetime.now().isoformat(),'message':text}
        with (self.root/'agent-events.jsonl').open('a',encoding='utf-8') as file:file.write(json.dumps(row,ensure_ascii=False)+'\n')
        if hasattr(self, 'server') and self.server:
            self.server.broadcast({'type':'notice',**row})

    def release(self):
        try:
            self.engine.reset()
        finally:
            try:
                self.actions.release_all()
            finally:
                self.last_touch=None
                self.blocked.update(self.state['buttons'] if self.state else [])

    def set_enabled(self, enabled):
        # Persist pause before attempting any release. If that fails, restart
        # must still be paused; enabling happens only after a clean release.
        self.enabled=False
        self.config['mapping_enabled']=False
        self.store.save()
        self.release()
        if enabled:
            self.config['mapping_enabled']=True
            self.store.save()
            self.enabled=True

    def scan(self):
        try:self.device.scan()
        except Exception as exc:self.log('连接失败：'+str(exc))

    def poll(self):
        try:
            # 键盘 PrintScreen (VK_SNAPSHOT 0x2C) 物理快捷键全局监听
            if not hasattr(self, '_user32'):
                import ctypes
                self._user32 = ctypes.WinDLL('user32')
                self._last_prtsc_down = False
            prtsc_down = bool(self._user32.GetAsyncKeyState(0x2C) & 0x8000)
            if prtsc_down and not self._last_prtsc_down:
                self.capture()
            self._last_prtsc_down = prtsc_down

            previous=self.state;self.state=self.device.read()
            identity=lambda s: (s.get('instance_id'),profile_scope(s)) if s else None
            if identity(previous)!=identity(self.state):
                self.release();self.last_buttons=set()
                self.log('手柄已连接' if self.state else '手柄已断开')
                # Legacy test/headless clients may provide buttons without an
                # identity. Keep their explicitly selected profile untouched.
                if not self.state or any(key in self.state for key in ('instance_id', 'family', 'profile_key', 'device_key')):
                    self.store.activate_controller(self.state);self.store.save()
                if self.state:
                    self.apply_device_cloaking()
                self.apply_device_settings()
                settings = device_config(self.config, self.state)
                options = settings.get('profile_options', {}).get(settings['active_profile'], {})
                inputs = dict(options.get('input') or {})
                inputs['trigger_curves'] = settings.get('trigger_curves', {})
                self.engine.blocked.update(self.engine.normalizer.update(self.state, inputs))
            if not self.state:
                self.engine.update(None, self.config, enabled=False);return
            buttons=set(self.state['buttons']);new=buttons-self.last_buttons;self.last_buttons=buttons
            if new:
                self.server.broadcast({'type':'buttons','buttons':sorted(new)})
                labels = button_labels(self.state.get('family', 'generic'), self.state.get('controller_type', 0))
                names = [labels.get(b, str(b)) for b in sorted(new)]
                self.log(f"按键输入: {' / '.join(names)}")
            running = self.enabled and time.monotonic() >= self.suspended_until
            self.engine.update(self.state, self.config, enabled=running, preview=time.monotonic()<self.preview_until)
        except Exception as exc:
            self.enabled=False
            self.config['mapping_enabled']=False
            try:self.store.save()
            except Exception:pass
            try:self.release()
            except Exception:pass
            self.log('映射已暂停：'+str(exc))

    def handle(self,message):
        command=message.get('command')
        if command in ('mapping_change', 'set_device_cloaking', 'test_haptics', 'rumble', 'led', 'preview_curve'):
            self.check_device_scope(message)
        if command == 'preview_curve' and 'instance_id' in message:
            if message['instance_id'] != (self.state or {}).get('instance_id'):
                raise ValueError('输入设备已变化，请重新打开当前设备设置')
        if command=='status':return self.status()
        if command in ('pause','resume'):
            self.set_enabled(command=='resume');self.log('映射已恢复' if self.enabled else '映射已暂停')
        elif command=='suspend':
            self.release();self.suspended_until=time.monotonic()+min(3.,max(0.,float(message.get('seconds',2))))
        elif command=='preview':
            self.preview_until=time.monotonic()+min(1.,max(0.,float(message.get('seconds',.75))))
        elif command=='mapping_change':
            self.release()
            latest = ConfigStore(self.root)
            data = latest.apply_mapping_change(message['change'], self.state)
            self.store = latest; self.config = latest.data
            self.broadcast()
            return {'config': data}
        elif command=='reload':
            self.release()
            previous_config = self.config
            self.store=ConfigStore(self.root)
            self.config=self.store.data
            self.apply_gamebar_shield()
            self.apply_device_cloaking()
            if hasattr(self, 'replay_engine'):
                replay_defaults = {'replay_buffer_minutes': 5, 'replay_codec': 'hevc',
                                   'replay_bitrate_mbps': 50, 'replay_fps': 30,
                                   'replay_capture_mode': 'game'}
                replay_changed = any(previous_config.get(k, default) != self.config.get(k, default)
                                     for k, default in replay_defaults.items())
                replay_enabled = bool(self.config.get('replay_buffer_enabled', False))
                newly_enabled = replay_enabled and not previous_config.get('replay_buffer_enabled', False)
                if replay_changed or not replay_enabled:
                    self.replay_engine.stop()
                self.replay_engine.save_dir = Path(self.config.get('save_dir', self.root / 'Captures'))
                self.replay_engine.minutes = self.config.get('replay_buffer_minutes', 5)
                self.replay_engine.codec = self.config.get('replay_codec', 'hevc')
                self.replay_engine.bitrate_mbps = self.config.get('replay_bitrate_mbps', 50)
                self.replay_engine.fps = self.config.get('replay_fps', 30)
                self.replay_engine.capture_mode = self.config.get('replay_capture_mode') or self.config.get('capture_mode', 'game')
                if replay_enabled and (replay_changed or newly_enabled):
                    self.replay_engine.start()
            self.apply_device_settings()
        elif command=='set_device_cloaking':
            if not self.state or not self.state.get('vendor'):
                raise ValueError('请先连接支持设备隐身的手柄')
            enabled = bool(message.get('enabled', True))
            self.store.set_setting('device_cloaking_enabled', enabled, self.state)
            self.store.save()
            applied = False
            msg = '未连接手柄或无需隐身'
            if self.state:
                from .hidhide import HidHideClient
                client = HidHideClient()
                if client.is_driver_installed():
                    vendor = self.state.get('vendor')
                    product = self.state.get('product')
                    options = {'device_path': self.state['device_path']} if self.state.get('device_path') else {}
                    applied, msg = client.cloak_controller(vendor, product, **options) if enabled else client.uncloak_controller(vendor, product, **options)
                    self.log(msg)
            return {'applied': applied, 'message': msg, 'enabled': enabled}
        elif command=='set_gamebar_shield':
            from .gamebar_shield import set_gamebar_shield
            if type(message.get('enabled')) is not bool:
                raise ValueError('无效屏蔽状态')
            self.store=ConfigStore(self.root); self.config=self.store.data
            ok, text=set_gamebar_shield(self.store, message['enabled'])
            self.log(text)
            return {'applied':ok, 'message':text,
                    'enabled':self.config.get('gamebar_shield_enabled',False),
                    'backup':self.config.get('gamebar_shield_backup',{})}
        elif command=='select_device':
            instance=message.get('instance_id')
            if type(instance) is not int:raise ValueError('无效设备')
            self.release();self.device.select(instance);self.poll();self.broadcast()
            if self.state:
                self.config['preferred_controller']=self.state.get('device_key') or self.state.get('profile_key','')
                self.device.preferred_key=self.config['preferred_controller'];self.store.save()
            self.log('已切换手柄：'+self.state['name'] if self.state else '设备已断开')
        elif command=='scan':self.scan();self.poll();self.broadcast()
        elif command=='capture':self.capture()
        elif command=='replay_record':self.replay_record()
        elif command=='save_replay':
            return self.replay_record(message.get('title'))
        elif command=='get_replay_status':
            if hasattr(self, 'replay_engine'):
                return self.replay_engine.get_status()
            return {'enabled': False, 'running': False}
        elif command=='preview_curve':
            from .curve_preview import preview_response_curve
            success = preview_response_curve(
                self.device, device_config(self.config, self.state), self.state,
                message.get('kind'), message.get('channel'), message.get('curve'),
                message.get('strength', .55), self.haptic_engine)
            return {'supported': success}
        elif command=='test_haptics':
            pattern = message.get('pattern', 'shutter')
            if not self.state or not self.state.get('rumble', False):
                return {'status': 'unsupported', 'pattern': pattern}
            if hasattr(self, 'haptic_engine'):
                self.haptic_engine.trigger_feedback(pattern)
            return {'status': 'ok', 'pattern': pattern}
        elif command=='record_toggle':self.record_toggle()
        elif command=='rumble':
            if not self.state or not self.state.get('rumble', False):
                return {'supported': False}
            success=self.device.rumble(max(0.,min(1.,float(message.get('strength',.35)))))
            self.log('振动已发送' if success else '振动不可用');return {'supported':success}
        elif command=='led':
            color=message.get('color','')
            if not isinstance(color,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',color):raise ValueError('无效颜色')
            if not self.state or not self.state.get('led', False):
                return {'supported': False}
            success=self.device.led(color);self.log('灯条已更新' if success else '灯条不可用');return {'supported':success}
        elif command in ('stop', 'exit', 'quit'):
            QTimer.singleShot(50, QCoreApplication.quit)
            return {'status': 'ok', 'action': 'stopping'}
        else:raise ValueError('不支持的操作')
        return self.status()

    def dispatch(self,binding,down=True):
        action=binding.get('action','none')
        if action=='gamepad_button':
            self.actions.gamepad_button(binding.get('value', '0'), down)
            return
        elif action=='gamepad_chord':
            self.actions.gamepad_chord(binding.get('value', '0'), down)
            return
        elif action=='gamepad_turbo':
            self.actions.gamepad_turbo(binding.get('value', '0'), down, binding.get('rate_hz', 15))
            return
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

    def replay_record(self, title=None):
        if self.replay_busy:return {'status':'busy'}
        now=time.monotonic()
        if now-getattr(self,'last_replay',0.)<1.0:return {'status':'busy'}
        self.last_replay=now
        # 1. 优先使用本地 4K 极清硬件编码回放缓冲区
        if self.config.get('replay_buffer_enabled', False) and hasattr(self, 'replay_engine'):
            if not self.replay_engine.is_running():
                self.replay_engine.start()
            self.replay_busy = True
            self.replay_device_context = self.feedback_device_context()
            def worker():
                try:self.replay_finished.emit(self.replay_engine.save_replay(title) or '', '')
                except Exception as exc:self.replay_finished.emit('', str(exc))
            self.executor.submit(worker)
            return {'status':'saving'}
        if self.config.get('gamebar_shield_enabled', False):
            self.log('系统录制已屏蔽，请先开启后台回放')
            return {'status':'disabled'}
        # 2. 未启用或切片未就绪时平滑回退触发系统 Windows Game Bar (Win+Alt+G)
        try:
            self.engine._dispatch({'action':'shortcut','value':'Win+Alt+G'})
            self.log('已触发系统回放录制 (Win+Alt+G)')
            self.server.broadcast({'type':'replay_record','status':'success','mode':'system'})
            return {'status':'requested','mode':'system'}
        except Exception as exc:
            self.log('回放录制触发失败：'+str(exc))

    def on_replay_finished(self, path, error):
        self.replay_busy = False
        if path:
            self.completed_feedback('replay_saved', self.replay_device_context)
            self.log('回放已保存：' + Path(path).name)
        else:
            self.log('回放保存失败：' + error if error else '回放尚未就绪，请稍后重试')
        self.replay_device_context = None
        self.server.broadcast({'type':'replay_record', 'status':'success' if path else 'not_ready', 'path':path, 'error':error})

    def record_toggle(self):
        if self.config.get('gamebar_shield_enabled', False):
            self.log('系统录制已屏蔽，可使用后台回放保存录像')
            return
        now=time.monotonic()
        if now-getattr(self,'last_record_toggle',0.)<1.0:return
        self.last_record_toggle=now
        try:
            self.engine._dispatch({'action':'shortcut','value':'Win+Alt+R'})
            self.log('已切换录屏状态 (Win+Alt+R)')
            self.server.broadcast({'type':'record_toggle','status':'success'})
        except Exception as exc:
            self.log('录屏触发失败：'+str(exc))

    def capture(self):
        now=time.monotonic()
        if self.busy or now-self.last_capture<self.config['cooldown']:return
        self.busy=True;self.last_capture=now
        self.capture_device_context = self.feedback_device_context()
        folder,mode=self.config['save_dir'],self.config['capture_mode']
        def worker():
            try:self.captured.emit(take_screenshot(folder,mode=mode),'')
            except Exception as exc:self.captured.emit('',str(exc))
        self.executor.submit(worker)

    def on_captured(self,path,error):
        self.busy=False
        if not error:
            self.completed_feedback('capture', self.capture_device_context)
            self.log('截图已保存')
        else:
            self.log('截图失败：'+error)
        self.capture_device_context = None
        self.server.broadcast({'type':'capture','path':path,'error':error})

    def close(self):
        if self.closed:return
        self.closed=True;self.timer.stop();self.scan_timer.stop();self.broadcast_timer.stop()
        # Release input BEFORE waiting for recording workers or other teardown.
        try:self.release()
        except Exception as exc:self.log('释放输入失败：'+str(exc))
        try:self.engine.close()
        except Exception as exc:self.log('关闭映射失败：'+str(exc))
        if hasattr(self, 'replay_engine'):
            try: self.replay_engine.stop()
            except Exception: pass
        if hasattr(self, 'haptic_engine'):
            try: self.haptic_engine.close()
            except Exception: pass
        self.device.close();self.executor.shutdown(wait=True);self.server.close()




def run(root):
    from .actions import attach_to_default_desktop
    attach_to_default_desktop()
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    app=QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    existing=request(root,'status',timeout=400)
    if existing and existing.get('ok'):
        return 0

    # A second startup must never terminate the process that owns held keys.
    # QLockFile handles dead PIDs; a live but busy owner keeps its lock.
    lock=QLockFile(str(root/'agent.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        return 1
    agent=None
    try:
        agent=Agent(root)
        app.aboutToQuit.connect(agent.close)
        return app.exec()
    finally:
        if agent is not None:agent.close()
        lock.unlock()

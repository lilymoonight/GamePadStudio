"""Persistent profiles and deterministic button gestures (independent of the UI)."""
from __future__ import annotations

import copy
import json
import os
import re
import time
from pathlib import Path
from .controller_catalog import CATALOG, controller_defaults, desktop_defaults

BUTTONS = {0: '×  交叉', 1: '○  圆圈', 2: '□  方块', 3: '△  三角',
           4: 'Create', 5: 'PS', 6: 'Options', 7: 'L3', 8: 'R3',
           9: 'L1', 10: 'R1', 11: '方向键 ↑', 12: '方向键 ↓',
           13: '方向键 ←', 14: '方向键 →', 15: '麦克风', 16:'背键 P1',17:'背键 P3',18:'背键 P2',19:'背键 P4',20: '触摸板'}
ACTION_NAMES = {'none': '保留原始输入', 'capture': '保存截图', 'gallery': '打开截图资料库',
                'home': '打开控制中心', 'shortcut': '键盘快捷键', 'hold': '按住键盘按键',
                'launch': '启动应用 / 命令', 'volume_mute': '系统声音静音',
                'volume_up': '音量 +', 'volume_down': '音量 −', 'media': '播放 / 暂停',
                'replay_record': '保存精彩瞬间 (回放录制)',
                'record_toggle': '开始/停止录屏'}


def default_config(root: Path):
    from .kbm_mapper import NIKKI_PROFILE_NAME, infinity_nikki_defaults
    base = {'4': {'short': {'action': 'capture'}, 'long': {'action': 'replay_record'}},
            '5': {'short': {'action': 'home'}, 'long': {'action': 'none'}}}
    desktop = copy.deepcopy(base)
    for key, value in [(0, 'Enter'), (1, 'Esc'), (2, 'Space'), (3, 'Tab'),
                       (11, 'Up'), (12, 'Down'), (13, 'Left'), (14, 'Right')]:
        desktop[str(key)] = {'short': {'action': 'hold', 'value': value}, 'long': {'action': 'none'}}
    desktop['15'] = {'short': {'action': 'volume_mute'}, 'long': {'action': 'none'}}
    nikki = infinity_nikki_defaults('dualsense')
    return {'version': 1, 'active_profile': '主机体验',
            'profiles': {'主机体验': base, '桌面导航': desktop, NIKKI_PROFILE_NAME: nikki},
            'save_dir': str(root / 'Captures'), 'capture_mode': 'monitor', 'cooldown': .5,
            'long_press': .65, 'deadzone': .10, 'rumble': .35, 'led': '#5686ff',
            'touch_mouse': False, 'close_to_tray': True, 'controller_profiles':{}, 'controller_favorites':[], 'preferred_controller':'',
            'profile_families':{'主机体验':'dualsense','桌面导航':'dualsense', NIKKI_PROFILE_NAME: 'all'},
            'replay_buffer_enabled': False, 'replay_buffer_minutes': 5, 'replay_codec': 'hevc',
            'replay_bitrate_mbps': 50, 'replay_fps': 30,
            'capture_sound_enabled': True, 'capture_haptics_enabled': True,
            'haptic_engine_enabled': True, 'haptic_intensity': 1.0, 'haptic_profile': 'crisp',
            'device_cloaking_enabled': False}


class ConfigStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / 'studio.json'
        self.data = default_config(root)
        self.warning = ''
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(saved, dict) or not isinstance(saved.get('profiles'), dict):
                    raise ValueError('配置格式错误')
                if not saved['profiles'] or saved.get('active_profile') not in saved['profiles']:
                    raise ValueError('配置预设不存在')
                for profile in saved['profiles'].values():
                    if not isinstance(profile, dict):
                        raise ValueError('按键配置格式错误')
                    for key, mapping in profile.items():
                        if int(key) not in BUTTONS or not isinstance(mapping, dict):
                            raise ValueError('按键格式错误')
                        for gesture in ('short', 'long'):
                            binding = mapping.get(gesture, {'action': 'none'})
                            if binding.get('action') not in ACTION_NAMES:
                                raise ValueError('未知映射动作')
                self.data.update(saved)
                if not isinstance(self.data['controller_profiles'],dict):self.data['controller_profiles']={}
                self.data['controller_profiles']={key:value for key,value in self.data['controller_profiles'].items() if isinstance(value,str) and value in self.data['profiles']}
                if not isinstance(self.data['profile_families'],dict):self.data['profile_families']={}
                for device_key,profile in self.data['controller_profiles'].items():
                    family=device_key.split(':')[0]
                    if family in CATALOG:self.data['profile_families'].setdefault(profile,family)
                self.data.setdefault('legacy_dualsense_profile',self.data['active_profile'])
                favorites=self.data['controller_favorites']
                self.data['controller_favorites']=[x for x in favorites if isinstance(x,str) and x in CATALOG] if isinstance(favorites,list) else []
                for key, lo, hi in [('deadzone', 0, .5), ('rumble', 0, 1), ('long_press', .2, 3), ('cooldown', .1, 10)]:
                    self.data[key] = max(lo, min(hi, float(self.data[key])))
                self.data.setdefault('replay_buffer_enabled', False)
                self.data.setdefault('replay_buffer_minutes', 5)
                self.data.setdefault('replay_codec', 'hevc')
                self.data.setdefault('replay_bitrate_mbps', 50)
                self.data.setdefault('replay_fps', 30)
                self.data['replay_buffer_minutes'] = max(1, min(10, int(self.data.get('replay_buffer_minutes', 5))))
                if self.data.get('replay_codec') not in ('hevc', 'av1', 'h264'):
                    self.data['replay_codec'] = 'hevc'
                self.data.setdefault('capture_sound_enabled', True)
                self.data.setdefault('capture_haptics_enabled', True)
                self.data.setdefault('haptic_engine_enabled', True)
                self.data.setdefault('haptic_intensity', 1.0)
                self.data.setdefault('haptic_profile', 'crisp')
                from .kbm_mapper import NIKKI_PROFILE_NAME, infinity_nikki_defaults
                if NIKKI_PROFILE_NAME not in self.data['profiles']:
                    self.data['profiles'][NIKKI_PROFILE_NAME] = infinity_nikki_defaults('dualsense')
                self.data['profile_families'][NIKKI_PROFILE_NAME] = 'all'
            except (ValueError, TypeError, AttributeError, OSError) as exc:
                backup = self.path.with_name(f'studio.invalid-{time.time_ns()}.json')
                backup.write_bytes(self.path.read_bytes())
                self.warning = f'配置无法读取，已备份并恢复默认值：{exc}'
                self.data = default_config(root)

        from .kbm_mapper import NIKKI_PROFILE_NAME
        from .virtual_kbm import NIKKI_PRESET_CONFIG, GENERAL_PRESET_CONFIG, NIKKI_SCHEME_NAME, GENERAL_SCHEME_NAME
        if 'virtual_kbm_schemes' not in self.data or not self.data['virtual_kbm_schemes']:
            self.data['virtual_kbm_schemes'] = {
                NIKKI_SCHEME_NAME: copy.deepcopy(NIKKI_PRESET_CONFIG),
                GENERAL_SCHEME_NAME: copy.deepcopy(GENERAL_PRESET_CONFIG),
            }
            is_nikki = self.data.get('active_profile') == NIKKI_PROFILE_NAME
            self.data['virtual_kbm_schemes'][NIKKI_SCHEME_NAME]['enabled'] = is_nikki

    @property
    def mappings(self):
        return self.data['profiles'][self.data['active_profile']]

    def activate_controller(self,state):
        family=state.get('family','dualsense')
        key=state.get('profile_key',family)
        saved=self.data['controller_profiles'].get(key)
        if saved not in self.data['profiles']:
            if family=='dualsense':saved=self.data.get('legacy_dualsense_profile','主机体验')
            else:
                base=CATALOG.get(family,CATALOG['generic'])['name']+' · 默认'
                claimed=[k for k,p in self.data['controller_profiles'].items() if p==base and k!=key]
                if base in self.data['profiles'] and not claimed:
                    saved=base
                elif base not in self.data['profiles']:
                    saved=base
                    self.data['profiles'][saved]=controller_defaults(family,state.get('available_buttons'))
                else:
                    saved=base;number=2
                    while saved in self.data['profiles']:saved=f'{base} {number}';number+=1
                    self.data['profiles'][saved]=controller_defaults(family,state.get('available_buttons'))
            if saved not in self.data['profiles']:saved=next(iter(self.data['profiles']))
            self.data['controller_profiles'][key]=saved
        elif re.fullmatch(r'.+ · 默认 \d+', str(saved)):
            base = saved.rsplit(' ', 1)[0]
            claimed = [k for k, p in self.data['controller_profiles'].items() if p == base and k != key]
            if base in self.data['profiles'] and not claimed:
                if self.data['profiles'].get(saved) == self.data['profiles'].get(base) or self.data['profiles'].get(saved) == controller_defaults(family, state.get('available_buttons')):
                    self.data['profiles'].pop(saved, None)
                    saved = base
                    self.data['controller_profiles'][key] = saved
        self.data['profile_families'].setdefault(saved,family)
        if family=='xbox':
            # Migrate only untouched auto-generated Xbox presets. Preserve all
            # user edits, including deliberate View or Guide assignments.
            legacy=[]
            for capture in ('4','15'):
                for act in ('gallery','replay_record'):
                    legacy.append({capture:{'short':{'action':'capture'},'long':{'action':act}},
                                   '5':{'short':{'action':'home'},'long':{'action':'none'}}})
            if re.fullmatch(r'Xbox · 默认(?: \d+)?',saved) and self.data['profiles'][saved] in legacy:
                self.data['profiles'][saved]=controller_defaults('xbox',state.get('available_buttons'))
                self.data['profile_families'][saved]='xbox'
        if family!='dualsense':
            desktop=CATALOG.get(family,CATALOG['generic'])['name']+' · 桌面'
            if desktop not in self.data['profiles']:
                self.data['profiles'][desktop]=desktop_defaults(family,state.get('available_buttons'))
                self.data['profile_families'][desktop]=family
        changed=self.data['active_profile']!=saved
        self.data['active_profile']=saved
        return changed

    def remember_profile(self,state,name):
        self.data['active_profile']=name
        if state:self.data['controller_profiles'][state.get('profile_key',state.get('family','dualsense'))]=name

    def profiles_for(self,state):
        if not state:return list(self.data['profiles'])
        family=state.get('family','generic')
        return [name for name in self.data['profiles'] if self.data['profile_families'].get(name,family) in (family, 'all') or name==self.data['active_profile']]

    def delete_profile(self, name, state=None):
        if name not in self.data['profiles']:
            return None
        family_profiles = self.profiles_for(state)
        if len(family_profiles) <= 1 and name in family_profiles:
            return None
        self.data['profiles'].pop(name, None)
        self.data['profile_families'].pop(name, None)
        remaining = self.profiles_for(state)
        fallback = remaining[0] if remaining else (next(iter(self.data['profiles'])) if self.data['profiles'] else '主机体验')
        for k, p in list(self.data['controller_profiles'].items()):
            if p == name:
                self.data['controller_profiles'][k] = fallback
        if self.data['active_profile'] == name:
            self.data['active_profile'] = fallback
        self.save()
        return fallback

    def save(self):
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, self.path)


class GestureEngine:
    """Long press fires once; it never also fires the short action on release."""
    def __init__(self, dispatch, threshold=.65):
        self.dispatch = dispatch
        self.threshold = threshold
        self.pressed = {}

    def update(self, buttons, mappings, now=None):
        now = time.monotonic() if now is None else now
        for button in buttons - self.pressed.keys():
            binding = copy.deepcopy(mappings.get(str(button), {}))
            self.pressed[button] = [now, False, binding]
            short = binding.get('short', {})
            if short.get('action') == 'hold':
                self.dispatch(short, True)
        for button, state in list(self.pressed.items()):
            start, fired, mapping = state
            short = mapping.get('short', {'action': 'none'})
            long = mapping.get('long', {'action': 'none'})
            if button not in buttons:
                if short.get('action') == 'hold':
                    self.dispatch(short, False)
                elif not fired:
                    self.dispatch(short, True)
                del self.pressed[button]
            elif not fired and now - start >= self.threshold and long.get('action', 'none') != 'none' and short.get('action') != 'hold':
                self.dispatch(long, True)
                state[1] = True

    def reset(self):
        for _, _, mapping in self.pressed.values():
            if mapping.get('short', {}).get('action') == 'hold':
                self.dispatch(mapping['short'], False)
        self.pressed.clear()


def radial_deadzone(x, y, deadzone):
    magnitude = (x*x + y*y) ** .5
    if magnitude <= deadzone:
        return 0., 0.
    gain = min(1., (magnitude - deadzone) / (1 - deadzone)) / magnitude
    return x * gain, y * gain

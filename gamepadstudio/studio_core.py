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
ACTION_NAMES.update({'mouse_hold': '按住鼠标按键', 'mouse_click': '点击鼠标按键', 'wheel': '鼠标滚轮'})
ACTION_NAMES['suppress'] = '不触发（组合键前缀）'


def get_action_names(lang=None):
    from .i18n import get_language
    is_en = (lang == 'en') or (lang is None and get_language() == 'en')
    if is_en:
        return {'none': 'Pass-through (Native Input)', 'capture': 'Take Screenshot', 'gallery': 'Open Captures Gallery',
                'home': 'Open Control Center', 'shortcut': 'Keyboard Shortcut', 'hold': 'Hold Keyboard Key',
                'launch': 'Launch App / Command', 'volume_mute': 'Mute System Audio',
                'volume_up': 'Volume +', 'volume_down': 'Volume −', 'media': 'Play / Pause Media',
                'replay_record': 'Instant Replay (Save Clip)',
                'record_toggle': 'Toggle Screen Recording'}
    return ACTION_NAMES


def get_buttons(lang=None):
    from .i18n import get_language
    is_en = (lang == 'en') or (lang is None and get_language() == 'en')
    if is_en:
        return {0: '×  Cross', 1: '○  Circle', 2: '□  Square', 3: '△  Triangle',
                4: 'Create', 5: 'PS', 6: 'Options', 7: 'L3', 8: 'R3',
                9: 'L1', 10: 'R1', 11: 'D-Pad ↑', 12: 'D-Pad ↓',
                13: 'D-Pad ←', 14: 'D-Pad →', 15: 'Mic / Mute',
                16: 'Paddle P1', 17: 'Paddle P3', 18: 'Paddle P2', 19: 'Paddle P4', 20: 'Touchpad'}
    return BUTTONS


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
    options = {NIKKI_PROFILE_NAME: {'right_stick_mouse': True,
                                    'mouse': {'mode': 'game', 'sensitivity': 28.0, 'deadzone': 0.06, 'y_ratio': 0.70, 'edge_boost': 1.70}}}
    return {'version': 1, 'active_profile': '主机体验',
            'profiles': {'主机体验': base, '桌面导航': desktop, NIKKI_PROFILE_NAME: nikki},
            'profile_options': options,
            'save_dir': str(root / 'Captures'), 'capture_mode': 'game', 'cooldown': .5,
            'long_press': .65, 'deadzone': .10, 'rumble': .35, 'led': '#5686ff',
            'touch_mouse': False, 'close_to_tray': False, 'mapping_enabled': True, 'controller_profiles':{}, 'controller_favorites':[], 'preferred_controller':'',
            'profile_families':{'主机体验':'dualsense','桌面导航':'dualsense', NIKKI_PROFILE_NAME: 'all'},
            'replay_buffer_enabled': False, 'replay_buffer_minutes': 5, 'replay_codec': 'hevc',
            'replay_bitrate_mbps': 50, 'replay_fps': 30,
            'capture_sound_enabled': True, 'capture_haptics_enabled': True,
            'haptic_engine_enabled': True, 'haptic_intensity': 1.0, 'haptic_profile': 'crisp',
            'device_cloaking_enabled': True,
            'gamebar_shield_enabled': False, 'gamebar_shield_backup': {}}


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
                    from .mapping_engine import validate_mappings
                    validate_mappings(profile)
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
                if self.data.get('capture_mode') == 'monitor':
                    self.data['capture_mode'] = 'game'
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

        if self.data.get('mapping_version', 0) < 2:
            self._migrate_mappings()
        self._baseline = copy.deepcopy(self.data)

    def _migrate_mappings(self):
        from .mapping_engine import convert_scheme
        from .kbm_mapper import NIKKI_PROFILE_NAME, CHORD_MAPPINGS
        from .virtual_kbm import NIKKI_PRESET_CONFIG, GENERAL_PRESET_CONFIG, NIKKI_SCHEME_NAME, GENERAL_SCHEME_NAME
        if self.path.exists():
            backup = self.path.with_name('studio.before-unified-mapping-v2.json')
            if not backup.exists():
                backup.write_bytes(self.path.read_bytes())
        schemes = self.data.pop('virtual_kbm_schemes', {})
        active_name = self.data.pop('active_virtual_kbm_scheme', '')
        active = schemes.get(active_name)
        if not active or not active.get('enabled', True):
            active = next((s for s in schemes.values() if s.get('enabled')), None)
        options = self.data.setdefault('profile_options', {})
        active_target = None
        for name, scheme in (schemes or {NIKKI_SCHEME_NAME: NIKKI_PRESET_CONFIG, GENERAL_SCHEME_NAME: GENERAL_PRESET_CONFIG}).items():
            target = name
            while target in self.data['profiles']:
                target += ' · 键鼠'
            self.data['profiles'][target] = convert_scheme(scheme)
            self.data['profile_families'][target] = 'all'
            options[target] = {'right_stick_mouse': True, 'mouse': copy.deepcopy(scheme.get('mouse_settings', scheme.get('mouse', {})))}
            if scheme is active:
                active_target = target
        if active_target:
            # The old KBM switch was global and overrode every device profile.
            # Preserve that effective selection without overwriting native presets.
            self.data['active_profile'] = active_target
            self.data['controller_profiles'] = {k: active_target for k in self.data['controller_profiles']}
            self.data['legacy_dualsense_profile'] = active_target
        # Make the older game preset's implicit WASD/chords explicit as well.
        if NIKKI_PROFILE_NAME in self.data['profiles']:
            from .mapping_engine import canonical_trigger
            game = self.data['profiles'][NIKKI_PROFILE_NAME]
            for parts, value in CHORD_MAPPINGS.items():
                game.setdefault(canonical_trigger('+'.join(map(str, parts))), {'short': {'action': 'shortcut', 'value': value}, 'long': {'action': 'none'}})
            for direction, value in [('up', 'W'), ('down', 'S'), ('left', 'A'), ('right', 'D')]:
                game.setdefault('LS:' + direction, {'short': {'action': 'hold', 'value': value}, 'long': {'action': 'none'}})
            for key, value in [('LT', 'right'), ('RT', 'left')]:
                game.setdefault(key, {'short': {'action': 'mouse_hold', 'value': value}, 'long': {'action': 'none'}})
            game.setdefault('LS:outer', {'short': {'action': 'hold', 'value': 'Shift'}, 'long': {'action': 'none'}})
            if '9' in game:
                game['9']['long'] = {'action': 'suppress'}
                game['9']['long_press'] = 0.25
            if '10' in game:
                game['10']['long'] = {'action': 'suppress'}
                game['10']['long_press'] = 0.25
            options.setdefault(NIKKI_PROFILE_NAME, {'right_stick_mouse': True,
                                                   'mouse': {'mode': 'game', 'sensitivity': 28.0, 'deadzone': 0.06, 'y_ratio': 0.70, 'edge_boost': 1.70}})
        self.data['mapping_version'] = 2
        self.data.setdefault('mapping_revision', 0)
        self._baseline = {}
        self.save(merge=False)

    @property
    def mappings(self):
        return self.data['profiles'][self.data['active_profile']]

    def activate_controller(self,state):
        family=state.get('family','dualsense')
        key=state.get('profile_key',family)
        # Moving the same Xbox from XInput to Raw Input changes SDL's GUID.
        # Keep the user's active profile instead of resurrecting an old one.
        prefix = ':'.join(key.split(':')[:3])
        migrated = self.data.setdefault('xbox_rawinput_profiles', [])
        if family == 'xbox' and key.endswith('7200') and prefix not in migrated:
            active = self.data['active_profile']
            previous = [k for k, p in self.data['controller_profiles'].items()
                        if k.startswith(prefix + ':') and k.endswith('7801') and p == active]
            if previous:
                self.data['controller_profiles'][key] = active
                if self.data.get('preferred_controller') in previous:
                    self.data['preferred_controller'] = key
            migrated.append(prefix)
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

    def save(self, merge=True):
        self.save_data(merge)

    def save_data(self, merge=True):
        # Three-way merge prevents a stale UI settings panel from overwriting
        # a profile selected or edited by the independent mapping process.
        if merge and self.path.exists() and hasattr(self, '_baseline'):
            try:
                latest = json.loads(self.path.read_text(encoding='utf-8'))
                merged = merge_changes(self._baseline, self.data, latest)
                self.data.clear(); self.data.update(merged)
            except (OSError, ValueError):
                pass
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, self.path)
        self._baseline = copy.deepcopy(self.data)

    def apply_mapping_change(self, change, state=None):
        from .mapping_engine import canonical_trigger, validate_mappings
        op = change['op']
        name = change.get('profile', self.data['active_profile'])
        if op != 'create' and name not in self.data['profiles']:
            raise ValueError('预设已删除，请重新选择')
        if op == 'binding':
            key = canonical_trigger(change['trigger'])
            mapping = validate_mappings({key: change['mapping']})[key]
            previous = change.get('previous_trigger')
            if previous and canonical_trigger(previous) != key:
                self.data['profiles'][name][canonical_trigger(previous)] = {g: {'action': 'none'} for g in ('short','long')}
            self.data['profiles'][name][key] = mapping
        elif op == 'unbind':
            self.data['profiles'][name][canonical_trigger(change['trigger'])] = {g: {'action': 'none'} for g in ('short', 'long')}
        elif op == 'select':
            self.remember_profile(state, name)
        elif op == 'create':
            if not name.strip() or name in self.data['profiles']:
                raise ValueError('请使用不重复的预设名称')
            source = self.data['active_profile']
            self.data['profiles'][name] = copy.deepcopy(self.mappings)
            self.data['profile_options'][name] = copy.deepcopy(self.data['profile_options'].get(source, {}))
            self.data['profile_families'][name] = self.data['profile_families'].get(source, 'all')
            self.remember_profile(state, name)
        elif op == 'options':
            allowed = {'right_stick_mouse', 'mouse'}
            if set(change['options']) - allowed:
                raise ValueError('未知预设选项')
            self.data['profile_options'].setdefault(name, {}).update(copy.deepcopy(change['options']))
        elif op == 'delete':
            if self.delete_profile(name, state) is None:
                raise ValueError('至少保留一个预设')
            self.data['profile_options'].pop(name, None)
        elif op == 'reset':
            if not state:
                raise ValueError('请先连接手柄')
            self.data['profiles'][name] = controller_defaults(state['family'], state.get('available_buttons'))
            self.data['profile_options'][name] = {}
        else:
            raise ValueError('未知映射修改')
        self.data['mapping_revision'] = self.data.get('mapping_revision', 0) + 1
        self.save()
        return copy.deepcopy(self.data)


def merge_changes(base, edited, latest):
    """Apply only this writer's changes; keep unrelated concurrent edits."""
    result = copy.deepcopy(latest)
    for key in base.keys() - edited.keys():
        result.pop(key, None)
    for key, value in edited.items():
        if key not in base or value != base[key]:
            if isinstance(value, dict) and isinstance(base.get(key), dict) and isinstance(result.get(key), dict):
                result[key] = merge_changes(base[key], value, result[key])
            else:
                result[key] = copy.deepcopy(value)
    return result


# Compatibility import: there is only one gesture implementation.
from .mapping_engine import GestureEngine


def radial_deadzone(x, y, deadzone):
    magnitude = (x*x + y*y) ** .5
    if magnitude <= deadzone:
        return 0., 0.
    gain = min(1., (magnitude - deadzone) / (1 - deadzone)) / magnitude
    return x * gain, y * gain

"""Persistent profiles and deterministic button gestures (independent of the UI)."""
from __future__ import annotations

import copy
import json
import os
import re
import time
from pathlib import Path
from .controller_catalog import CATALOG, controller_defaults, desktop_defaults

KEYBOARD_PROFILE_CLEANUP_VERSION = 1
NIKKI_PROFILE_OPTIONS = {'right_stick_mouse': True,
                         'mouse': {'mode': 'game', 'sensitivity': 28.0, 'deadzone': 0.06,
                                   'y_ratio': 0.70, 'edge_boost': 1.70}}

BUTTONS = {0: '×  交叉', 1: '○  圆圈', 2: '□  方块', 3: '△  三角',
           4: 'Create', 5: 'PS', 6: 'Options', 7: 'L3', 8: 'R3',
           9: 'L1', 10: 'R1', 11: '方向键 ↑', 12: '方向键 ↓',
           13: '方向键 ←', 14: '方向键 →', 15: '麦克风', 16:'背键 P1',17:'背键 P3',18:'背键 P2',19:'背键 P4',20: '触摸板'}
ACTION_NAMES = {'none': '保留原始输入', 'capture': '保存截图', 'gallery': '打开截图资料库',
                'home': '打开控制中心', 'shortcut': '键盘快捷键', 'hold': '按住键盘按键',
                'launch': '启动应用 / 命令', 'volume_mute': '系统声音静音',
                'volume_up': '音量 +', 'volume_down': '音量 −', 'media': '播放 / 暂停',
                'replay_record': '保存精彩瞬间 (回放录制)',
                'record_toggle': '开始/停止录屏',
                'gamepad_button': '映射为手柄按键',
                'gamepad_chord': '手柄多键组合宏',
                'gamepad_turbo': '手柄高频连发 (Turbo)',
                'gamepad_macro': '手柄连招序列宏'}
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
                'record_toggle': 'Toggle Screen Recording',
                'gamepad_button': 'Remap to Gamepad Button',
                'gamepad_chord': 'Gamepad Multi-button Macro',
                'gamepad_turbo': 'Gamepad Turbo (Rapid Fire)',
                'gamepad_macro': 'Gamepad Sequence Macro',
                'mouse_hold': 'Hold Mouse Button', 'mouse_click': 'Click Mouse Button', 'wheel': 'Mouse Wheel',
                'suppress': 'Suppress (Chord Prefix)'}
    return ACTION_NAMES


def profile_mode(config, name):
    if not name:
        return 'gamepad'
    modes = config.get('profile_modes')
    modes = modes if isinstance(modes, dict) else {}
    if modes.get(name) in ('gamepad', 'kbm'):
        return modes[name]
    from .kbm_mapper import NIKKI_PROFILE_NAME
    options = config.get('profile_options')
    options = options if isinstance(options, dict) else {}
    profile_options = options.get(name)
    profile_options = profile_options if isinstance(profile_options, dict) else {}
    if name in (NIKKI_PROFILE_NAME, '桌面导航') or '键鼠' in name or profile_options.get('right_stick_mouse'):
        return 'kbm'
    profile = config.get('profiles', {}).get(name, {})
    for entry in profile.values():
        if isinstance(entry, dict):
            for g in ('short', 'long'):
                act = entry.get(g, {}).get('action')
                if act in ('hold', 'shortcut', 'mouse_hold', 'mouse_click', 'wheel'):
                    return 'kbm'
    return 'gamepad'


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
    action_macro = copy.deepcopy(base)
    action_macro['0'] = {'short': {'action': 'none'}, 'long': {'action': 'gamepad_turbo', 'value': '0', 'rate_hz': 15}}
    action_macro['2'] = {'short': {'action': 'none'}, 'long': {'action': 'gamepad_chord', 'value': '2+3'}}
    action_macro['9+10'] = {'short': {'action': 'gamepad_chord', 'value': '9+10+3'}, 'long': {'action': 'none'}}

    nikki = infinity_nikki_defaults('dualsense')
    options = {NIKKI_PROFILE_NAME: copy.deepcopy(NIKKI_PROFILE_OPTIONS)}
    return {'version': 1, 'mapping_version': 2, 'mapping_revision': 0,
            'keyboard_profile_cleanup_version': KEYBOARD_PROFILE_CLEANUP_VERSION,
            'active_profile': '主机体验',
            'profiles': {'主机体验': base, '动作与格斗宏': action_macro, NIKKI_PROFILE_NAME: nikki},
            'profile_modes': {'主机体验': 'gamepad', '动作与格斗宏': 'gamepad', NIKKI_PROFILE_NAME: 'kbm'},
            'profile_options': options,
            'save_dir': str(root / 'Captures'), 'capture_mode': 'game', 'cooldown': .5,
            'long_press': .65, 'deadzone': .10, 'rumble': .35, 'led': '#5686ff',
            'touch_mouse': False, 'close_to_tray': False, 'mapping_enabled': True, 'controller_profiles':{}, 'controller_favorites':[], 'preferred_controller':'',
            'profile_families':{'主机体验':'dualsense','动作与格斗宏':'dualsense', NIKKI_PROFILE_NAME: 'all'},
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
                if not isinstance(self.data.get('profile_modes'), dict):
                    self.data['profile_modes'] = {}
                options = self.data.get('profile_options')
                self.data['profile_options'] = {name: value if isinstance(value, dict) else {}
                                                for name, value in options.items()} if isinstance(options, dict) else {}
                # Defaults describe fresh installs; absent saved markers still
                # identify old configurations that need a one-time migration.
                self.data['mapping_version'] = saved.get('mapping_version', 0)
                self.data['keyboard_profile_cleanup_version'] = saved.get('keyboard_profile_cleanup_version', 0)
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
                from .kbm_mapper import NIKKI_PROFILE_NAME, LEGACY_NIKKI_PROFILE_NAME, infinity_nikki_defaults
                if NIKKI_PROFILE_NAME not in self.data['profiles']:
                    self.data['profiles'][NIKKI_PROFILE_NAME] = copy.deepcopy(
                        self.data['profiles'].get(LEGACY_NIKKI_PROFILE_NAME, infinity_nikki_defaults('dualsense')))
                    old_options = self.data.get('profile_options', {}).get(LEGACY_NIKKI_PROFILE_NAME)
                    if old_options is not None:
                        self.data['profile_options'][NIKKI_PROFILE_NAME] = copy.deepcopy(old_options)
                self.data['profile_families'][NIKKI_PROFILE_NAME] = 'all'
            except (ValueError, TypeError, AttributeError, OSError) as exc:
                backup = self.path.with_name(f'studio.invalid-{time.time_ns()}.json')
                backup.write_bytes(self.path.read_bytes())
                self.warning = f'配置无法读取，已备份并恢复默认值：{exc}'
                self.data = default_config(root)

        needs_cleanup = self.data.get('keyboard_profile_cleanup_version', 0) < KEYBOARD_PROFILE_CLEANUP_VERSION
        if needs_cleanup and self.path.exists():
            backup = self.path.with_name('studio.before-keyboard-profile-cleanup-v1.json')
            if not backup.exists():
                backup.write_bytes(self.path.read_bytes())
        needs_mapping_migration = self.data.get('mapping_version', 0) < 2
        if needs_mapping_migration:
            self._migrate_mappings()
        if needs_cleanup:
            self._cleanup_keyboard_profiles()
        if needs_mapping_migration or needs_cleanup:
            self.data['mapping_revision'] = self.data.get('mapping_revision', 0) + 1
            self.save(merge=False)
        self._baseline = copy.deepcopy(self.data)

    def _migrate_mappings(self):
        if self.path.exists():
            backup = self.path.with_name('studio.before-unified-mapping-v2.json')
            if not backup.exists():
                backup.write_bytes(self.path.read_bytes())
        # Existing unified bindings are authoritative. Legacy standalone
        # schemes are retired by the keyboard-profile cleanup below.
        self.data.pop('virtual_kbm_schemes', None)
        self.data.pop('active_virtual_kbm_scheme', None)
        self.data['mapping_version'] = 2
        self.data.setdefault('mapping_revision', 0)

    def _cleanup_keyboard_profiles(self):
        from .kbm_mapper import NIKKI_PROFILE_NAME, LEGACY_NIKKI_PROFILE_NAME
        removed = {name for name in self.data['profiles']
                   if name != NIKKI_PROFILE_NAME
                   and (name == LEGACY_NIKKI_PROFILE_NAME or profile_mode(self.data, name) == 'kbm')}
        remove_profiles(self.data, removed)
        self.data.setdefault('profile_modes', {})[NIKKI_PROFILE_NAME] = 'kbm'
        self.data['profile_families'][NIKKI_PROFILE_NAME] = 'all'
        self.data.pop('virtual_kbm_schemes', None)
        self.data.pop('active_virtual_kbm_scheme', None)
        self.data['keyboard_profile_cleanup_version'] = KEYBOARD_PROFILE_CLEANUP_VERSION
        return removed

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
        changed=self.data['active_profile']!=saved
        self.data['active_profile']=saved
        return changed

    def remember_profile(self,state,name):
        self.data['active_profile']=name
        if state:self.data['controller_profiles'][state.get('profile_key',state.get('family','dualsense'))]=name

    def profiles_for(self, state, mode=None):
        if not state:
            profiles = list(self.data['profiles'])
        else:
            family = state.get('family', 'generic')
            profiles = [name for name in self.data['profiles'] if self.data['profile_families'].get(name, family) in (family, 'all') or name == self.data['active_profile']]
        if mode:
            profiles = [name for name in profiles if profile_mode(self.data, name) == mode]
        return profiles

    def delete_profile(self, name, state=None):
        if name not in self.data['profiles']:
            return None
        family_profiles = self.profiles_for(state)
        if len(family_profiles) <= 1 and name in family_profiles:
            return None
        self.data['profiles'].pop(name, None)
        self.data['profile_families'].pop(name, None)
        self.data.get('profile_modes', {}).pop(name, None)
        self.data.get('profile_options', {}).pop(name, None)
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
                if latest.get('keyboard_profile_cleanup_version', 0) > self._baseline.get('keyboard_profile_cleanup_version', 0):
                    # A settings window opened before cleanup may still have
                    # edited a removed preset. Do not resurrect it on save.
                    from .kbm_mapper import NIKKI_PROFILE_NAME, LEGACY_NIKKI_PROFILE_NAME
                    removed = {name for name in self._baseline.get('profiles', {})
                               if name != NIKKI_PROFILE_NAME and name not in latest.get('profiles', {})
                               and (name == LEGACY_NIKKI_PROFILE_NAME or profile_mode(self._baseline, name) == 'kbm')}
                    old_active = self.data.get('active_profile')
                    old_legacy = self.data.get('legacy_dualsense_profile')
                    old_devices = {key for key, name in self.data.get('controller_profiles', {}).items() if name in removed}
                    remove_profiles(self.data, removed)
                    if old_active in removed:
                        self.data['active_profile'] = latest['active_profile']
                    if old_legacy in removed:
                        self.data['legacy_dualsense_profile'] = latest.get('legacy_dualsense_profile', NIKKI_PROFILE_NAME)
                    for key in old_devices:
                        self.data['controller_profiles'][key] = latest.get('controller_profiles', {}).get(key, NIKKI_PROFILE_NAME)
                    self.data.pop('virtual_kbm_schemes', None)
                    self.data.pop('active_virtual_kbm_scheme', None)
                    self.data['keyboard_profile_cleanup_version'] = latest['keyboard_profile_cleanup_version']
                merged = merge_changes(self._baseline, self.data, latest)
                merged['mapping_revision'] = max(latest.get('mapping_revision', 0), self.data.get('mapping_revision', 0))
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
            source = change.get('source') or self.data['active_profile']
            mode = change.get('mode') or profile_mode(self.data, source)
            family = (state or {}).get('family', 'generic')
            source_mode = profile_mode(self.data, source)
            if mode == source_mode and source in self.data['profiles']:
                self.data['profiles'][name] = copy.deepcopy(self.data['profiles'][source])
                self.data['profile_options'][name] = copy.deepcopy(self.data['profile_options'].get(source, {}))
            elif mode == 'gamepad':
                self.data['profiles'][name] = controller_defaults(family, (state or {}).get('available_buttons'))
                self.data['profile_options'][name] = {}
            else:
                self.data['profiles'][name] = desktop_defaults(family, (state or {}).get('available_buttons'))
                self.data['profile_options'][name] = {'right_stick_mouse': True}
            self.data['profile_families'][name] = self.data['profile_families'].get(source, family)
            self.data.setdefault('profile_modes', {})[name] = mode
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
            self.data.get('profile_modes', {}).pop(name, None)
        elif op == 'reset':
            from .kbm_mapper import NIKKI_PROFILE_NAME, infinity_nikki_defaults
            if profile_mode(self.data, name) == 'kbm':
                family = (state or {}).get('family', 'dualsense')
                available = (state or {}).get('available_buttons')
                if name == NIKKI_PROFILE_NAME:
                    self.data['profiles'][name] = infinity_nikki_defaults(family, available)
                    self.data['profile_options'][name] = copy.deepcopy(NIKKI_PROFILE_OPTIONS)
                else:
                    self.data['profiles'][name] = desktop_defaults(family, available)
                    self.data['profile_options'][name] = {'right_stick_mouse': True}
            else:
                if not state:
                    raise ValueError('请先连接手柄')
                self.data['profiles'][name] = controller_defaults(state['family'], state.get('available_buttons'))
                self.data['profile_options'][name] = {}
        else:
            raise ValueError('未知映射修改')
        self.data['mapping_revision'] = self.data.get('mapping_revision', 0) + 1
        self.save()
        return copy.deepcopy(self.data)


def remove_profiles(config, removed):
    """Remove presets and stale metadata, keeping device references usable."""
    from .kbm_mapper import NIKKI_PROFILE_NAME
    profiles = config['profiles']
    for name in removed:
        profiles.pop(name, None)
    for field in ('profile_modes', 'profile_families', 'profile_options'):
        metadata = config.get(field)
        if isinstance(metadata, dict):
            config[field] = {name: value for name, value in metadata.items() if name in profiles}
        else:
            config[field] = {}
    if config.get('active_profile') in removed:
        config['active_profile'] = NIKKI_PROFILE_NAME
    if 'legacy_dualsense_profile' in config and config['legacy_dualsense_profile'] not in profiles:
        config['legacy_dualsense_profile'] = NIKKI_PROFILE_NAME
    controller_profiles = config.get('controller_profiles', {})
    config['controller_profiles'] = {key: NIKKI_PROFILE_NAME if name in removed else name
                                     for key, name in controller_profiles.items()
                                     if name in removed or name in profiles}


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

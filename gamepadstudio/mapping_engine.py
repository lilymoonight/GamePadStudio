"""The input/gesture/output contract shared by the agent and every editor.

SDL button positions are canonical (never the printed A/B labels). Chords are
unordered sets. A consumed chord cannot fall through to its component buttons.
The clock is injectable so arbitration and releases can be tested without I/O.
"""
from __future__ import annotations

import copy
import time
from collections import Counter
from .actions import parse_keys

ALIASES = {'A': '0', 'B': '1', 'X': '2', 'Y': '3', 'BACK': '4',
           'VIEW': '4', 'CREATE': '4', 'GUIDE': '5', 'HOME': '5', 'PS': '5', 'XBOX': '5',
           'START': '6', 'OPTIONS': '6', 'L3': '7', 'R3': '8', 'LB': '9', 'L1': '9',
           'RB': '10', 'R1': '10', 'UP': '11', 'DOWN': '12', 'LEFT': '13', 'RIGHT': '14',
           'SHARE': '15', 'CAPTURE': '15', 'L2': 'LT', 'R2': 'RT',
           'LS_UP': 'LS:up', 'LS_DOWN': 'LS:down', 'LS_LEFT': 'LS:left', 'LS_RIGHT': 'LS:right'}
INPUTS = [str(i) for i in range(21)] + ['LT', 'RT'] + [f'{s}:{d}' for s in ('LS', 'RS') for d in ('up', 'down', 'left', 'right')] + ['LS:outer']
GESTURES = ('short', 'long')
HOLD_ACTIONS = ('hold', 'mouse_hold')
ACTIONS = {'none', 'suppress', 'hold', 'shortcut', 'mouse_hold', 'mouse_click', 'wheel', 'capture',
           'replay_record', 'record_toggle', 'home', 'gallery', 'launch',
           'volume_mute', 'volume_up', 'volume_down', 'media'}


def canonical_trigger(value):
    parts = [ALIASES.get(p.strip().upper(), p.strip()) for p in str(value).split('+')]
    names = {key.upper(): key for key in INPUTS}
    parts = [names.get(p.upper(), p) for p in parts]
    if not parts or any(p not in INPUTS for p in parts):
        raise ValueError(f'未知手柄输入：{value}')
    if len(set(parts)) != len(parts) or len(parts) > 4:
        raise ValueError('组合键需要 1 至 4 个不同按键')
    return '+'.join(sorted(parts, key=INPUTS.index))


def validate_binding(binding):
    if not isinstance(binding, dict) or binding.get('action', 'none') not in ACTIONS:
        raise ValueError('未知映射动作')
    action = binding.get('action', 'none')
    if action in ('hold', 'shortcut') and not parse_keys(binding.get('value', '')):
        raise ValueError('请设置键盘按键或组合键')
    if action in ('mouse_hold', 'mouse_click') and binding.get('value') not in ('left', 'right', 'middle'):
        raise ValueError('请选择鼠标按键')
    if action == 'wheel' and binding.get('value') not in ('up', 'down'):
        raise ValueError('请选择滚轮方向')
    if action == 'launch' and not binding.get('executable', '').strip():
        raise ValueError('请选择应用或命令')
    return copy.deepcopy(binding)


def validate_mappings(mappings):
    result = {}
    for trigger, entry in mappings.items():
        key = canonical_trigger(trigger)
        if not isinstance(entry, dict):
            raise ValueError('按键配置格式错误')
        if key in result:
            raise ValueError(f'重复的组合键：{trigger}')
        result[key] = {g: validate_binding(entry.get(g, {'action': 'none'})) for g in GESTURES}
        if 'long_press' in entry:
            threshold = float(entry['long_press'])
            if not .15 <= threshold <= 3:
                raise ValueError('长按时长应在 0.15 至 3 秒之间')
            result[key]['long_press'] = threshold
    return result


def legacy_action(value, chord=False):
    if not value or value == 'none':
        return {'action': 'none'}
    if isinstance(value, dict):
        return validate_binding(value)
    if value.startswith('action:'):
        return {'action': value.split(':', 1)[1]}
    if value.startswith('mouse:wheel_'):
        return {'action': 'wheel', 'value': value.rsplit('_', 1)[1]}
    if value.startswith('mouse:'):
        return {'action': 'mouse_click' if chord else 'mouse_hold', 'value': value.split(':', 1)[1]}
    return {'action': 'shortcut' if chord else 'hold', 'value': value}


def convert_scheme(scheme):
    mappings = {}
    chords_keys = [set(canonical_trigger(chord).split('+')) for chord in scheme.get('chords', {})]
    timing_window = max(.15, min(3., float(scheme.get('timing_window_s', .2))))
    for group in ('buttons', 'chords'):
        for key, value in scheme.get(group, {}).items():
            canon = canonical_trigger(key)
            entry = value if isinstance(value, dict) and 'action' not in value else {'short': value}
            target = {g: legacy_action(entry.get(g), group == 'chords') for g in GESTURES}
            if isinstance(value, dict) and 'short' in value:
                target['short'] = legacy_action(value['short'], chord=True)
                if value.get('long') == 'none':
                    target['long'] = {'action': 'suppress'}
                target['long_press'] = max(.15, min(3., float(scheme.get('timing_window_s', timing_window))))
            elif group == 'buttons' and any(canon in chord and len(chord) > 1 for chord in chords_keys):
                # Universal rule: any button acting as a chord prefix/modifier suppresses long hold
                if target['long'].get('action') == 'none':
                    target['long'] = {'action': 'suppress'}
                    target['long_press'] = timing_window
                if target['short'].get('action') == 'hold':
                    target['short']['action'] = 'shortcut'
            mappings[canon] = target
    # These used to be invisible, hardcoded side effects in the KBM engine.
    for direction, key in [('up', 'W'), ('down', 'S'), ('left', 'A'), ('right', 'D')]:
        mappings.setdefault('LS:' + direction, {'short': {'action': 'hold', 'value': key}, 'long': {'action': 'none'}})
    mappings.setdefault('LS:outer', {'short': {'action': 'hold', 'value': 'Shift'}, 'long': {'action': 'none'}})
    return mappings


def effective_mappings(config, state=None):
    mappings = copy.deepcopy(config['profiles'][config['active_profile']])
    family = (state or {}).get('family', config.get('profile_families', {}).get(config['active_profile'], 'generic'))
    if family == 'xbox' and config.get('gamebar_shield_enabled'):
        available = (state or {}).get('available_buttons', [5, 15])
        if 5 in available:
            mappings.setdefault('5', {'short': {'action': 'home'}, 'long': {'action': 'none'}})
        if 15 in available:
            mappings.setdefault('15', {'short': {'action': 'capture'}, 'long': {'action': 'replay_record'}})
    return mappings


def profile_family(config, state=None):
    from .controller_catalog import CATALOG
    family = (state or {}).get('family', config.get('profile_families', {}).get(config['active_profile'], 'dualsense'))
    return family if family in CATALOG else 'dualsense'


def trigger_label(trigger, family='dualsense'):
    from .controller_catalog import button_labels
    names = button_labels(family)
    extra = {'LT': 'L2' if family in ('dualsense', 'dualshock') else 'LT',
             'RT': 'R2' if family in ('dualsense', 'dualshock') else 'RT', 'LS:outer': '左摇杆推满'}
    for s, title in [('LS', '左摇杆'), ('RS', '右摇杆')]:
        extra.update({f'{s}:{d}': title + a for d, a in [('up', '↑'), ('down', '↓'), ('left', '←'), ('right', '→')]})
    return ' + '.join(names.get(int(p), p) if p.isdigit() else extra.get(p, p) for p in canonical_trigger(trigger).split('+'))


def binding_label(binding):
    from .studio_core import ACTION_NAMES
    action = binding.get('action', 'none')
    value = binding.get('value', '')
    if action in ('hold', 'shortcut'):
        return value + ('（按住）' if action == 'hold' else '')
    if action in ('mouse_hold', 'mouse_click'):
        return '鼠标' + {'left': '左键', 'right': '右键', 'middle': '中键'}.get(value, value) + ('（按住）' if action == 'mouse_hold' else '')
    if action == 'wheel':
        return '滚轮' + ('↑' if value == 'up' else '↓')
    return ACTION_NAMES.get(action, action)


def output_tokens(binding):
    action = binding.get('action', 'none')
    if action in ('hold', 'shortcut'):
        return [f'key:{key}' for key in parse_keys(binding.get('value', ''))]
    if action in ('mouse_hold', 'mouse_click'):
        return ['mouse:' + binding['value']]
    if action == 'wheel':
        return ['mouse:wheel_' + binding['value']]
    return [] if action in ('none', 'suppress') else ['action:' + action]


class InputNormalizer:
    """One hysteresis rule for execution, capture and input feedback."""
    def __init__(self):
        self.active = set()

    def update(self, state):
        current = {str(b) for b in (state or {}).get('buttons', [])}
        axes = (state or {}).get('axes', [0.] * 6)
        axes = list(axes) + [0.] * (6 - len(axes))
        values = {'LT': axes[4], 'RT': axes[5], 'LS:up': -axes[1], 'LS:down': axes[1],
                  'LS:left': -axes[0], 'LS:right': axes[0], 'RS:up': -axes[3],
                  'RS:down': axes[3], 'RS:left': -axes[2], 'RS:right': axes[2]}
        for key, value in values.items():
            threshold = (.35 if key in self.active else .5) if ':' in key else (.35 if key in self.active else .55)
            if value >= threshold:
                current.add(key)
        if (axes[0] ** 2 + axes[1] ** 2) ** .5 >= (.75 if 'LS:outer' in self.active else .85):
            current.add('LS:outer')
        self.active = current
        return set(current)

    def reset(self):
        self.active.clear()


class GestureEngine:
    """Exclusive short/long gestures; holds release on break, cancel or reset."""
    def __init__(self, dispatch, threshold=.65, chord_window=.08):
        self.dispatch = dispatch
        self.threshold = threshold
        self.chord_window = chord_window
        self.pressed = {}
        self.consumed = set()
        self.chord_locks = []
        self.pending_releases = []
        self.current_trigger = ''
        self.current_gesture = ''

    def _emit(self, key, gesture, binding, down):
        self.current_trigger, self.current_gesture = key, gesture
        if binding.get('action', 'none') not in ('none', 'suppress'):
            self.dispatch(binding, down)

    def _finish(self, key, state, now, cancel=False):
        mapping, fired = state['mapping'], state['fired']
        if fired:
            action = mapping.get(fired, {})
            if action.get('action') in HOLD_ACTIONS:
                self._emit(key, fired, action, False)
        elif not cancel:
            action = mapping.get('short', {})
            self._emit(key, 'short', action, True)
            if action.get('action') in HOLD_ACTIONS:
                # A quick tap still needs a nonzero key-down interval for games (65ms ensures crossing 30/60Hz frame boundaries).
                self.pending_releases.append((now + .065, key, 'short', action))

    def update(self, buttons, mappings, now=None):
        now = time.monotonic() if now is None else now
        current = {str(b) for b in buttons}
        newly_pressed = current - getattr(self, '_prev_buttons', set())
        for item in list(self.pending_releases):
            when, key, gesture, action = item
            if now >= when:
                self._emit(key, gesture, action, False)
                self.pending_releases.remove(item)

        # 组合键两键依次松开锁定：只要组合键中任一按键仍被按下，该组按键全部处于锁定压制状态
        # 判定结束的时间是最后一个按键被释放的时间，无关松开顺序
        self.chord_locks = [lock for lock in self.chord_locks if lock & current]
        locked_buttons = set().union(*self.chord_locks) if self.chord_locks else set()

        self.consumed.intersection_update(current)
        # Inactive mappings do not introduce latency or reserve chord components.
        valid = {canonical_trigger(k): v for k, v in mappings.items()
                 if any(v.get(g, {}).get('action', 'none') != 'none' for g in GESTURES)}
        chords = [set(k.split('+')) for k in valid if '+' in k]
        # 动态优先级：按键包含新按下按键的组合优先；长度更长组合优先；彻底杜绝修饰键按住连续切技能时的按键吞吐与单键泄漏
        candidates = sorted((k for k in valid if set(k.split('+')) <= current),
                            key=lambda k: (-len(k.split('+')), -int(bool(set(k.split('+')) & newly_pressed)), k))
        chosen, occupied = [], set()
        for key in candidates:
            parts = set(key.split('+'))
            if parts & occupied or (parts <= self.consumed and key not in self.pressed):
                continue
            # 单键若属于当前仍生效的组合键锁定组，绝对严禁触发单键动作
            if len(parts) == 1 and parts <= locked_buttons:
                continue
            chosen.append(key)
            occupied.update(parts)
        for key, state in list(self.pressed.items()):
            if key not in chosen:
                parts = set(key.split('+'))
                cancel = bool(parts & occupied) or bool(parts & locked_buttons)  # upgraded to a chord or locked
                self._finish(key, state, now, cancel)
                if len(parts) > 1 or cancel:
                    self.consumed.update(parts & current)
                del self.pressed[key]
        for key in chosen:
            parts = set(key.split('+'))
            # Re-evaluate consumption after finishing a chord this frame.
            if parts <= self.consumed and key not in self.pressed:
                continue
            if key not in self.pressed:
                self.pressed[key] = {'start': now, 'fired': '', 'mapping': copy.deepcopy(valid[key])}
                if len(parts) > 1:
                    self.consumed.update(parts)
                    self.chord_locks.append(set(parts))
            state = self.pressed[key]
            if state['fired']:
                continue
            mapping = state['mapping']
            short, long = mapping.get('short', {}), mapping.get('long', {})
            age = now - state['start']
            threshold = mapping.get('long_press', self.threshold)
            is_prefix = any(parts < chord for chord in chords)
            is_chord = len(parts) > 1

            if long.get('action', 'none') not in ('none', 'suppress'):
                if age >= threshold:
                    self._emit(key, 'long', long, True)
                    state['fired'] = 'long'
            elif long.get('action') == 'suppress':
                if age >= threshold:
                    state['fired'] = 'long'
            elif (is_chord and not is_prefix) or short.get('action') in HOLD_ACTIONS:
                wait = (max(self.chord_window, 0.15) if is_prefix and short.get('action') == 'mouse_hold' else (self.chord_window if is_prefix else 0.))
                if age >= wait:
                    self._emit(key, 'short', short, True)
                    state['fired'] = 'short'
            elif is_prefix and age >= threshold:
                state['fired'] = 'long'
        self._prev_buttons = set(current)

    def reset(self):
        for key, state in list(self.pressed.items()):
            self._finish(key, state, 0, cancel=True)
        for _, key, gesture, action in self.pending_releases:
            self._emit(key, gesture, action, False)
        self.pressed.clear()
        self.pending_releases.clear()
        self.consumed.clear()
        self.chord_locks.clear()
        self._prev_buttons = set()


class MappingRuntime:
    """Only this object sends virtual input. Remote UIs merely display its state."""
    def __init__(self, actions, dispatch, start_mouse=True):
        self.actions, self.dispatch = actions, dispatch
        self.normalizer = InputNormalizer()
        self.engine = GestureEngine(self._dispatch)
        self.output_counts = Counter()
        self.pulses = []
        self.recent = {}
        self.events = []
        self.seq = 0
        self.now = 0.
        self.inputs = set()
        self.last_touch = None
        self.preview = False
        self.blocked = set()
        self.mouse_thread = None
        self.config_signature = None
        if start_mouse:
            from .virtual_kbm import VirtualMouseThread
            self.mouse_thread = VirtualMouseThread(actions)
            self.mouse_thread.start()

    @property
    def threshold(self):
        return self.engine.threshold

    @threshold.setter
    def threshold(self, value):
        self.engine.threshold = value

    def _hold(self, binding, down):
        tokens = output_tokens(binding)
        if self.preview:
            pass
        elif binding['action'] == 'hold':
            self.actions.hold(binding['value'], down)
        else:
            token = tokens[0]
            if (down and self.output_counts[token] == 0) or (not down and self.output_counts[token] == 1):
                self.actions.mouse_button(binding['value'], down)
        for token in tokens:
            self.output_counts[token] = max(0, self.output_counts[token] + (1 if down else -1))

    def _dispatch(self, binding, down=True):
        action = binding.get('action', 'none')
        if action in HOLD_ACTIONS:
            self._hold(binding, down)
        elif down and action in ('shortcut', 'mouse_click'):
            held = dict(binding, action='hold' if action == 'shortcut' else 'mouse_hold')
            # 连续高频敲击同一快捷键/点击时，立刻完结前序未到期脉冲释放，确保 Windows 发出独立两次按压
            for item in list(self.pulses):
                when, old_held = item
                if old_held == held:
                    self._hold(old_held, False)
                    self.pulses.remove(item)
            self._hold(held, True)
            self.pulses.append((self.now + .05, held))
        elif down and action == 'wheel':
            if not self.preview:self.actions.scroll(1 if binding['value'] == 'up' else -1)
        elif down and not self.preview:
            self.dispatch(binding, True)
        if down:
            self.seq += 1
            for token in output_tokens(binding):
                self.recent[token] = self.now + .18
            self.events.append({'seq': self.seq, 'trigger': self.engine.current_trigger,
                                'gesture': self.engine.current_gesture, 'action': binding_label(binding)})
            self.events = self.events[-8:]

    def update(self, state, config, enabled=True, now=None, preview=False):
        self.now = time.monotonic() if now is None else now
        previous_inputs = set(self.inputs)
        if self.preview != preview:
            self.reset(blocked=previous_inputs)
            self.preview = preview
        self.inputs = self.normalizer.update(state)
        self.blocked.intersection_update(self.inputs)
        options = config.get('profile_options', {}).get(config['active_profile'], {})
        mappings = effective_mappings(config, state)
        signature = (config['active_profile'], repr(mappings), repr(options), config.get('long_press', .65), config.get('touch_mouse', False))
        if signature != self.config_signature:
            if self.config_signature is not None:
                self.reset(blocked=previous_inputs)
            self.config_signature = signature
            if self.mouse_thread:
                self.mouse_thread.configure(options.get('mouse', {}))
        self.engine.threshold = config.get('long_press', .65)
        if not enabled or not state:
            self.reset()
            return
        for when, binding in list(self.pulses):
            if self.now >= when:
                self._hold(binding, False)
                self.pulses.remove((when, binding))
        self.engine.update(self.inputs - self.blocked, mappings, self.now)
        touch = state.get('touch')
        if config.get('touch_mouse') and touch and self.last_touch:
            dx, dy = (touch[0] - self.last_touch[0]) * 1600, (touch[1] - self.last_touch[1]) * 900
            if abs(dx) < 250 and abs(dy) < 250:
                self.actions.move_mouse(dx, dy)
        self.last_touch = touch
        if self.mouse_thread:
            axes = state.get('axes', [0.] * 6)
            move = options.get('right_stick_mouse', True) and not self.blocked.intersection({'RS:up', 'RS:down', 'RS:left', 'RS:right'})
            desktop = options.get('mouse', {}).get('mode', 'game') == 'desktop' or (hasattr(self.actions, 'is_nikki_game_focused') and not self.actions.is_nikki_game_focused())
            self.mouse_thread.update_stick(axes[2] if move else 0., axes[3] if move else 0., is_desktop=desktop)
            self.mouse_thread.set_click_lock(any(n for k, n in self.output_counts.items() if k.startswith('mouse:')))

    def feedback(self):
        return {'preview': self.preview, 'inputs': sorted(self.inputs), 'active': list(self.engine.pressed),
                'outputs': sorted(k for k, n in self.output_counts.items() if n),
                'recent_outputs': sorted(k for k, until in self.recent.items() if until > self.now),
                'events': list(self.events), 'sequence': self.seq}

    def reset(self, blocked=None):
        # Stop continuous motion even if a following key-up is rejected.
        if self.mouse_thread:
            self.mouse_thread.update_stick(0., 0.)
        self.engine.reset()
        for _, binding in self.pulses:
            self._hold(binding, False)
        self.pulses.clear()
        self.output_counts.clear()
        self.recent.clear()
        self.last_touch = None
        self.blocked.update(self.inputs if blocked is None else blocked)
        if self.mouse_thread:
            self.mouse_thread.update_stick(0., 0.)

    def close(self):
        try:
            self.reset()
        finally:
            if self.mouse_thread:
                self.mouse_thread.stop()
                self.mouse_thread.join(timeout=1.)

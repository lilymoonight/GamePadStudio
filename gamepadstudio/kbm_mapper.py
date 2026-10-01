"""Infinity Nikki PC controls, grouped by frequency and hand position.

Keyboard defaults follow the post-2.0 layout. Slot numbers deliberately do
not name an ability: players can rearrange those slots inside the game.
"""

import math

NIKKI_PROFILE_NAME = "《无限暖暖》专属预设"
LEGACY_NIKKI_PROFILE_NAME = "无限暖暖 · 键鼠全盘接管"
NIKKI_LAYOUT_VERSION = 3
NIKKI_SLOT_HOLD_SECONDS = .50
NIKKI_MENU_HOLD_SECONDS = .60

# Gestures deliberately open menus and exploration tools. Movement, combat and
# continuous aiming remain on physical controls, so a delayed tap or a stray
# touch cannot turn into a jump, dash or held mouse button.
NIKKI_TOUCH_MAPPINGS = {
    'TP:tap': 'T',
    'TP:double_tap': 'P',
    'TP:hold': 'CapsLock',
    'TP:swipe_up': 'U',
    'TP:swipe_down': 'M',
    'TP:swipe_left': 'C',
    'TP:swipe_right': 'B',
    'TP:two_tap': 'V',
}
NIKKI_TOUCH_SCROLL_MAPPINGS = {'TP:scroll_up': 'up', 'TP:scroll_down': 'down'}
NIKKI_TOUCH_SETTINGS = {'touch_gestures_enabled': True, 'touch_mouse': False,
                        'touch_scroll': False, 'touch_gesture_sensitivity': .4}

# SDL physical positions: south=0, east=1, west=2, north=3.
# LB is the ability modifier; View/Create is the menu modifier.
# Triggers remain dedicated mouse buttons, including while held for aiming.
CHORD_MAPPINGS = {
    (9, 0): '1', (9, 2): '2', (9, 3): '3', (9, 1): '4',
    (9, 11): '5', (9, 14): '6', (9, 12): '7', (9, 13): '8',
    (9, 10): 'G',
    (4, 0): 'C', (4, 2): 'B', (4, 3): 'U', (4, 1): 'I',
    (4, 8): 'Enter', (4, 6): 'K', (4, 9): 'H',
}

NIKKI_KEY_ROLES = {
    'W': '前进 / 自行车前进', 'S': '后退 / 钓鱼提杆',
    'A': '向左移动 / 钓鱼拉线', 'D': '向右移动 / 钓鱼拉线',
    'SPACE': '跳跃 / 漂浮 / 自行车跳跃', 'SHIFT': '冲刺 / 闪避',
    'CTRL': '步行', 'F': '交互 / 拾取 / 对话', 'E': '奇想战技 1',
    'Q': '下落攻击', 'R': '奇想战技 2 / 家园派生能力',
    'G': '派生能力 1 / 流转灵珠', 'T': '任务追踪 / 派生能力 2',
    'TAB': '能力轮盘（按住）', 'X': '鸣星铃', 'V': '大喵视角',
    'Z': '使用消耗品', 'ESC': '美鸭梨 / 返回', 'M': '地图',
    'P': '大喵相机', 'F12': '游戏快拍', 'ALT': '显示鼠标光标（按住）',
    'C': '衣柜 / 相机世界漂浮', 'N': '服装进化', 'B': '背包 / 相机相册',
    'L': '奇想手账', 'U': '任务 / 相机拓展', 'Y': '设计图',
    'I': '无限之心', 'O': '共鸣', 'ENTER': '聊天 / 确认',
    'F10': '联机', 'K': '活动', 'J': '奇迹之旅 / 圆梦创想',
    'H': '商城', 'CAPSLOCK': '功能汇总',
    **{str(i): f'能力快捷槽 {i}' for i in range(1, 9)},
    **{f'F{i}': f'常用搭配 {i}' for i in range(1, 8)},
}

NIKKI_LAYOUT_GROUPS = (
    {'title': '基础行动', 'description': '轻推慢走，推深正常跑；冲刺由你主动控制。',
     'triggers': ('LS:up', 'LS:inner', '0', '1', '2', '3', '10', '7', 'LT', 'RT')},
    {'title': '探索与相机', 'description': '方向键管理探索工具；按住上方向，再用右摇杆和 RT 选择能力。',
     'triggers': ('11', '12', '13', '14', '8', '9+7', '9+8', '6')},
    {'title': '能力与常用搭配', 'description': '先按住 L1 / LB：轻点切能力，按住 0.50 秒换常用搭配。槽位顺序在游戏中设置。',
     'triggers': ('0+9', '2+9', '3+9', '1+9', '9+11', '9+14', '9+12', '9+13', '9+10')},
    {'title': '衣柜、任务与社交', 'description': '触摸板四向滑动直达常用菜单；Create / View 组合保留次级功能和无触摸板手柄的入口。',
     'triggers': (*NIKKI_TOUCH_MAPPINGS, *NIKKI_TOUCH_SCROLL_MAPPINGS,
                  '0+4', '2+4', '3+4', '1+4', '4+8', '4+6', '4+9')},
)


def nikki_binding_hint(binding):
    """A concise game-context label for the actual current output."""
    action, value = binding.get('action'), str(binding.get('value', ''))
    if action in ('hold', 'shortcut'):
        return NIKKI_KEY_ROLES.get(value.upper(), '')
    if action in ('mouse_hold', 'mouse_click'):
        return {'left': '净化 / 弓箭 / 点击', 'right': '能力使用 / 潜行捕捉 / 钓鱼收线',
                'middle': '鼠标中键'}.get(value, '')
    if action == 'wheel':
        return '镜头缩放 / 列表滚动'
    return {'replay_record': '保存精彩回放', 'capture': '保存软件截图',
            'home': '打开控制中心'}.get(action, '')

def stick_to_wasd(x, y, deadzone=0.15, sprint_threshold=0.85):
    """将左摇杆连续量解析为平滑 8 向 WASD，推满附加 Shift 疾跑"""
    mag = math.sqrt(x*x + y*y)
    if mag < deadzone:
        return set()
    
    # SDL: Y=-1 为上，Y=1 为下。反转 Y 轴使上为正
    y_up = -y
    angle = math.degrees(math.atan2(y_up, x)) % 360
    
    keys = set()
    if 22.5 <= angle < 157.5:
        keys.add('W')
    if 202.5 <= angle < 337.5:
        keys.add('S')
    if 112.5 <= angle < 247.5:
        keys.add('A')
    if angle < 67.5 or angle >= 292.5:
        keys.add('D')
        
    if mag >= sprint_threshold:
        keys.add('Shift')
        
    return keys

def stick_to_mouse(rx, ry, deadzone=0.07, sensitivity=28.0, y_ratio=0.65, boost=1.0):
    """
    将右摇杆解析为影视级平滑鼠标视角位移 (专为 3D 动作 RPG 深度优化)：
    1. 线性-高阶混合渐进曲线：兼顾微调即时跟手（彻底告别前段呆滞卡顿）与大幅旋转敏捷感
    2. Y 轴俯仰阻尼 (y_ratio=0.65)：符合 3D 游戏人机工学，水平旋转时视平线稳定不晃动
    3. 边缘推满调头加速 (boost)：外圈推满平滑提升转向速度，轻松 180° 疾速调头
    """
    mag = math.sqrt(rx*rx + ry*ry)
    if mag <= deadzone:
        return 0.0, 0.0
    norm = min(1.0, (mag - deadzone) / (1.0 - deadzone))
    # 35% 线性保证微动跟手、消除阶梯感，65% 二次曲线保证大推转向张力
    curve = 0.35 * norm + 0.65 * (norm ** 2.0)
    speed = sensitivity * curve * boost
    dx = (rx / mag) * speed
    dy = (ry / mag) * speed * y_ratio
    return dx, dy

def infinity_nikki_defaults(family='generic', available=None, *, touch_inputs=None,
                            layout_version=NIKKI_LAYOUT_VERSION):
    """Generate the PC layout with device-specific, independently fired gestures.

    Device-bound callers pass the actual ``touch_sources(state)``. Family-based
    inference is only for the Sony template and legacy configuration comparison;
    an explicit empty list keeps a Sony device without touch reporting usable.
    ``layout_version=2`` reproduces the previous layout for a preserving upgrade.
    """
    available = set(range(21) if available is None else available)
    mapping = {}
    
    def entry(action, value=None, long_action='none', long_value=None, threshold=None):
        short = {'action': action}
        if value is not None:
            short['value'] = value
        long = {'action': long_action}
        if long_value is not None:
            long['value'] = long_value
        result = {'short': short, 'long': long}
        if threshold is not None:
            result['long_press'] = threshold
        return result

    # Holding these keys preserves variable jump height and sprint duration.
    base_actions = [
        (0, 'Space'), # 跳跃 / 浮空
        (1, 'Shift'), # 冲刺 / 闪避
        (2, 'F'),     # 交互 / 拾取
        (3, 'E'),     # 奇想战技 1；当前 PC 默认已不是鸣星铃
    ]
    for btn, key in base_actions:
        if btn in available:
            mapping[str(btn)] = entry('hold', key)
            
    # 4 号键 (Create / View / Back / Share)：统一作为精彩回放录制/保存键，严禁映射为打开地图
    if 4 in available:
        mapping['4'] = entry('replay_record', long_action='suppress', threshold=.28)

    # 截图与相册专用键适配 (若其他型号手柄有专用截图键如 15 号键)
    from .controller_catalog import capture_button
    cap_btn = capture_button(family, available)
    if cap_btn is not None and str(cap_btn) != '4':
        mapping[str(cap_btn)] = entry('capture', long_action='replay_record', threshold=.60)
        
    # 主页键 / PS键 / Guide
    if 5 in available and family != 'xbox':
        mapping['5'] = entry('home')
        
    # 菜单 / 暂停键 (Options / Menu / Start)
    if 6 in available:
        mapping['6'] = entry('shortcut', 'Esc')
        
    # 摇杆按下
    if 7 in available:
        mapping['7'] = entry('hold', 'R')
    if 8 in available:
        mapping['8'] = entry('shortcut', 'V', 'shortcut', 'Z', .50)
        
    if 9 in available:
        mapping['9'] = entry('suppress')
    if 10 in available:
        mapping['10'] = entry('hold', 'Q')
        
    # 十字键功能快捷键
    navigation = {
        11: entry('hold', 'Tab'),
        12: entry('shortcut', 'T', 'shortcut', 'X', .50),
        13: entry('shortcut', 'M', 'hold', 'Alt', .50),
        14: entry('shortcut', 'P', 'shortcut', 'F12', .50),
    }
    for btn, binding in navigation.items():
        if btn in available:
            mapping[str(btn)] = binding
            
    # 辅助键与触摸板
    if 15 in available and cap_btn != 15:
        mapping['15'] = entry('shortcut', 'C')
    if 20 in available:
        mapping['20'] = entry('shortcut', 'Esc')

    # No dual short/long arbitration or chords: press/release injects immediately.
    mapping['LT'] = entry('mouse_hold', 'right')
    mapping['RT'] = entry('mouse_hold', 'left')

    # Light deflection holds Ctrl; full deflection never adds an unrequested dash.
    for direction, key in [('up', 'W'), ('down', 'S'), ('left', 'A'), ('right', 'D')]:
        mapping['LS:' + direction] = entry('hold', key)
    mapping['LS:inner'] = entry('hold', 'Ctrl')
    mapping['LS:outer'] = entry('none')

    from .mapping_engine import canonical_trigger
    for parts, val in CHORD_MAPPINGS.items():
        if not set(parts) <= available:
            continue
        trig = canonical_trigger('+'.join(str(p) for p in parts))
        if val.isdigit() and int(val) <= 7:
            mapping[trig] = entry('shortcut', val, 'shortcut', 'F' + val, NIKKI_SLOT_HOLD_SECONDS)
        elif val == '8':
            mapping[trig] = entry('shortcut', val)
        elif parts == (9, 10):
            mapping[trig] = entry('shortcut', 'G', 'shortcut', 'T', .50)
        else:
            secondary = {'C': 'N', 'B': 'L', 'U': 'Y', 'I': 'O',
                         'Enter': 'F10', 'K': 'J', 'H': 'CapsLock'}[val]
            mapping[trig] = entry('shortcut', val, 'shortcut', secondary, NIKKI_MENU_HOLD_SECONDS)

    for button, direction in ((7, 'up'), (8, 'down')):
        if {9, button} <= available:
            mapping[canonical_trigger(f'9+{button}')] = entry('wheel', direction)

    if layout_version >= 3:
        if touch_inputs is None:
            touch_inputs = (*NIKKI_TOUCH_MAPPINGS, *NIKKI_TOUCH_SCROLL_MAPPINGS) if family in ('dualsense', 'dualshock4') and 20 in available else ()
        for trigger in touch_inputs:
            if trigger in NIKKI_TOUCH_MAPPINGS:
                mapping[trigger] = entry('shortcut', NIKKI_TOUCH_MAPPINGS[trigger])
            elif trigger in NIKKI_TOUCH_SCROLL_MAPPINGS:
                mapping[trigger] = entry('wheel', NIKKI_TOUCH_SCROLL_MAPPINGS[trigger])
        # The direct menu gesture replaces only its matching chord's short
        # action. Rare secondary functions keep their established held chord;
        # devices without that gesture retain the full keyboard-only layout.
        for gesture, chord in (('TP:swipe_left', '0+4'),
                               ('TP:swipe_right', '2+4'),
                               ('TP:swipe_up', '3+4')):
            if gesture in mapping and chord in mapping:
                mapping[chord]['short'] = {'action': 'none'}

    return mapping



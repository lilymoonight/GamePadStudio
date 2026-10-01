"""
GamePad Studio · 无限暖暖 专属全盘键鼠与前缀换挡映射引擎
支持全手柄生态：DualSense / DualShock 4 / Xbox / Switch / 通用PC手柄
100% 硬件扫描码注入，零丢帧、零模式冲突，单键极速瞬发换装！
"""

import math
import time

NIKKI_PROFILE_NAME = "《无限暖暖》专属预设"
LEGACY_NIKKI_PROFILE_NAME = "无限暖暖 · 键鼠全盘接管"

# 4大前缀换挡组合键定义：
# 前缀键:
# 9: LB / L1
# 10: RB / R1
# 'LT': 左扳机 (行程 > 0.35)
# 'RT': 右扳机 (行程 > 0.35)
# 动作键 (SDL 物理方位):
# 0: A / 交叉 (南 / 下方位)
# 1: B / 圆圈 (东 / 右方位)
# 2: X / 方块 (西 / 左方位)
# 3: Y / 三角 (北 / 上方位)

CHORD_MAPPINGS = {
    # 【左手前缀】1 ~ 8 号能力套装极速秒切换装
    (9, 0): '1',    # LB + A (南) -> 套装 1 (主线跳跃/漂浮套)
    (9, 2): '2',    # LB + X (西) -> 套装 2 (清洁/净化套)
    (9, 3): '3',    # LB + Y (北) -> 套装 3 (电工/特殊能力套)
    (9, 1): '4',    # LB + B (东) -> 套装 4 (捕虫/交互套)
    
    ('LT', 0): '5', # LT + A (南) -> 套装 5
    ('LT', 2): '6', # LT + X (西) -> 套装 6
    ('LT', 3): '7', # LT + Y (北) -> 套装 7
    ('LT', 1): '8', # LT + B (东) -> 套装 8
    
    # 【右手前缀】探索工具与系统全景面板
    (10, 0): 'M',   # RB + A -> 大地图
    (10, 2): 'P',   # RB + X -> 拍照模式相机
    (10, 3): 'C',   # RB + Y -> 完整衣柜搭配间
    (10, 1): 'U',   # RB + B -> 任务追踪面板
    
    ('RT', 0): 'O', # RT + A -> 共鸣 (抽卡)
    ('RT', 2): 'I', # RT + X -> 无限之心 (能力天赋树)
    ('RT', 3): 'Y', # RT + Y -> 奇想设计图 (制作配方)
    ('RT', 1): 'K', # RT + B -> 活动界面
}

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

def infinity_nikki_defaults(family='generic', available=None):
    """生成《无限暖暖》专属基础按键配置（支持全手柄系列通用）"""
    available = set(range(21) if available is None else available)
    mapping = {}
    
    # 动作基础层 (南=跳跃, 东=冲刺, 西=交互, 北=技能)
    base_actions = [
        (0, 'Space'), # 跳跃 / 浮空
        (1, 'Shift'), # 冲刺 / 闪避
        (2, 'F'),     # 交互 / 拾取
        (3, 'E'),     # 套装主动技能
    ]
    for btn, key in base_actions:
        if btn in available:
            mapping[str(btn)] = {'short': {'action': 'hold', 'value': key}, 'long': {'action': 'none'}}
            
    # 4 号键 (Create / View / Back / Share)：统一作为精彩回放录制/保存键，严禁映射为打开地图
    if 4 in available:
        mapping['4'] = {'short': {'action': 'replay_record'}, 'long': {'action': 'none'}}

    # 截图与相册专用键适配 (若其他型号手柄有专用截图键如 15 号键)
    from .controller_catalog import capture_button
    cap_btn = capture_button(family, available)
    if cap_btn is not None and str(cap_btn) != '4':
        mapping[str(cap_btn)] = {'short': {'action': 'capture'}, 'long': {'action': 'replay_record'}}
        
    # 主页键 / PS键 / Guide
    if 5 in available and family != 'xbox':
        mapping['5'] = {'short': {'action': 'home'}, 'long': {'action': 'none'}}
        
    # 菜单 / 暂停键 (Options / Menu / Start)
    if 6 in available:
        mapping['6'] = {'short': {'action': 'shortcut', 'value': 'Esc'}, 'long': {'action': 'none'}}
        
    # 摇杆按下
    if 7 in available: # L3
        mapping['7'] = {'short': {'action': 'hold', 'value': 'Shift'}, 'long': {'action': 'none'}}
    if 8 in available: # R3
        mapping['8'] = {'short': {'action': 'shortcut', 'value': 'V'}, 'long': {'action': 'none'}}
        
    # 肩键前缀换挡与单发功能：短按触发单发，长按进入组合键静默压制状态
    if 9 in available: # LB / L1
        mapping['9'] = {'short': {'action': 'shortcut', 'value': 'Tab'}, 'long': {'action': 'suppress'}, 'long_press': 0.25}
    if 10 in available: # RB / R1
        mapping['10'] = {'short': {'action': 'shortcut', 'value': 'V'}, 'long': {'action': 'suppress'}, 'long_press': 0.25}
        
    # 十字键功能快捷键
    for btn, key in [(11, '3'), (12, '4'), (13, 'G'), (14, 'M')]:
        if btn in available:
            mapping[str(btn)] = {'short': {'action': 'shortcut', 'value': key}, 'long': {'action': 'none'}}
            
    # 辅助键与触摸板
    if 15 in available and cap_btn != 15:
        mapping['15'] = {'short': {'action': 'shortcut', 'value': 'C'}, 'long': {'action': 'none'}}
    if 20 in available:
        mapping['20'] = {'short': {'action': 'shortcut', 'value': 'Esc'}, 'long': {'action': 'none'}}

    # 双扳机：LT 鼠标右键长按瞄准/蓄力，RT 鼠标左键普攻/快门
    mapping['LT'] = {'short': {'action': 'mouse_hold', 'value': 'right'}, 'long': {'action': 'mouse_hold', 'value': 'right'}, 'long_press': 0.20}
    mapping['RT'] = {'short': {'action': 'mouse_hold', 'value': 'left'}, 'long': {'action': 'mouse_hold', 'value': 'left'}, 'long_press': 0.20}

    # 左摇杆 8 向平滑走位 WASD + 推满疾跑 Shift
    for direction, key in [('up', 'W'), ('down', 'S'), ('left', 'A'), ('right', 'D')]:
        mapping['LS:' + direction] = {'short': {'action': 'hold', 'value': key}, 'long': {'action': 'none'}}
    mapping['LS:outer'] = {'short': {'action': 'hold', 'value': 'Shift'}, 'long': {'action': 'none'}}

    # 4 大前缀换挡组合键：完整注入当前核心配置
    from .mapping_engine import canonical_trigger
    for parts, val in CHORD_MAPPINGS.items():
        trig = canonical_trigger('+'.join(str(p) for p in parts))
        mapping[trig] = {'short': {'action': 'shortcut', 'value': val}, 'long': {'action': 'none'}}
        
    return mapping



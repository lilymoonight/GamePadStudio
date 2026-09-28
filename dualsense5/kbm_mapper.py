"""
GamePad Studio · 3D 动作与开放世界专属全盘键鼠与前缀换挡映射引擎
支持全手柄生态：DualSense / DualShock 4 / Xbox / Switch / 通用PC手柄
100% 硬件扫描码注入，零丢帧、零模式冲突，单键极速瞬发！
"""

import math
import time

NIKKI_PROFILE_NAME = "3D 动作 · 键鼠全盘接管"

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
    """生成 3D 动作通用基础按键配置（支持全手柄系列通用）"""
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
            
    # 截图与相册 (根据各手柄型号适配专用物理键)
    from .controller_catalog import capture_button
    cap_btn = capture_button(family, available)
    if cap_btn is not None:
        mapping[str(cap_btn)] = {'short': {'action': 'capture'}, 'long': {'action': 'replay_record'}}
        
    # 主页键 / PS键 / Guide
    if 5 in available and family != 'xbox':
        mapping['5'] = {'short': {'action': 'home'}, 'long': {'action': 'none'}}
        
    # 菜单 / 暂停键 (Options / Menu / Start)
    if 6 in available:
        mapping['6'] = {'short': {'action': 'shortcut', 'value': 'Esc'}, 'long': {'action': 'none'}}
        
    # 如果手柄的 4 号键不是截图键（例如 Xbox 的 View 或 Switch 的 -），映射为地图
    if 4 in available and cap_btn != 4:
        mapping['4'] = {'short': {'action': 'shortcut', 'value': 'M'}, 'long': {'action': 'none'}}
        
    # 摇杆按下
    if 7 in available: # L3
        mapping['7'] = {'short': {'action': 'hold', 'value': 'Shift'}, 'long': {'action': 'none'}}
    if 8 in available: # R3
        mapping['8'] = {'short': {'action': 'shortcut', 'value': 'V'}, 'long': {'action': 'none'}}
        
    # 肩键单按备用 (前缀换挡未命中动作键时的单发功能)
    if 9 in available: # LB / L1
        mapping['9'] = {'short': {'action': 'shortcut', 'value': 'Tab'}, 'long': {'action': 'none'}}
    if 10 in available: # RB / R1
        mapping['10'] = {'short': {'action': 'shortcut', 'value': 'V'}, 'long': {'action': 'none'}}
        
    # 十字键功能快捷键
    for btn, key in [(11, '3'), (12, '4'), (13, 'G'), (14, 'M')]:
        if btn in available:
            mapping[str(btn)] = {'short': {'action': 'shortcut', 'value': key}, 'long': {'action': 'none'}}
            
    # 辅助键与触摸板
    if 15 in available and cap_btn != 15:
        mapping['15'] = {'short': {'action': 'shortcut', 'value': 'C'}, 'long': {'action': 'none'}}
    if 20 in available:
        mapping['20'] = {'short': {'action': 'shortcut', 'value': 'Esc'}, 'long': {'action': 'none'}}
        
    return mapping


class NikkiKbmEngine:
    """
    状态驱动的前缀换挡与全盘键鼠转换引擎
    负责处理：
    1. 组合换挡 (LB/LT/RB/RT + A/B/X/Y)
    2. 无换挡时的基础四键按压保持 (Space/Shift/F/E)
    3. 扳机非组合时的平滑瞄准/普攻 (鼠标右键/左键)
    4. 左摇杆平滑 8 向 WASD 与推满疾跑
    5. 右摇杆亚像素累加非线性平滑视角
    """
    def __init__(self, actions, on_chord=None):
        self.actions = actions
        self.on_chord = on_chord
        self.held_wasd = set()
        self.held_face = {}  # btn -> key_name
        self.last_buttons = set()
        
        # 前缀换挡状态
        self.active_modifiers = set()
        self.consumed_modifiers = set()
        
        # 扳机单按状态 (LT -> 鼠标右键, RT -> 鼠标左键)
        self.lt_mouse_active = False
        self.rt_mouse_active = False
        self.lt_press_time = 0.0
        self.rt_press_time = 0.0
        
        # 鼠标视角亚像素累加器与平滑滤波
        self.smooth_dx = 0.0
        self.smooth_dy = 0.0
        self.outer_hold_time = 0.0
        self.mouse_acc_x = 0.0
        self.mouse_acc_y = 0.0
        
        self.last_time = time.monotonic()

    def reset(self):
        """释放所有键鼠状态，杜绝任何按键粘滞"""
        for k in list(self.held_wasd):
            try: self.actions.hold(k, False)
            except Exception: pass
        self.held_wasd.clear()
        
        for k in list(self.held_face.values()):
            try: self.actions.hold(k, False)
            except Exception: pass
        self.held_face.clear()
        
        if self.lt_mouse_active:
            try: self.actions.mouse_button('right', False)
            except Exception: pass
            self.lt_mouse_active = False
            
        if self.rt_mouse_active:
            try: self.actions.mouse_button('left', False)
            except Exception: pass
            self.rt_mouse_active = False
            
        self.consumed_modifiers.clear()
        self.active_modifiers.clear()
        self.last_buttons.clear()
        self.lt_press_time = 0.0
        self.rt_press_time = 0.0
        self.smooth_dx = 0.0
        self.smooth_dy = 0.0
        self.outer_hold_time = 0.0
        self.mouse_acc_x = 0.0
        self.mouse_acc_y = 0.0

    def update(self, state, mappings=None):
        """
        每帧轮询处理手柄全量状态并驱动键鼠
        返回供 GestureEngine 继续处理的剩余按键集 (排除了已被接管的 0,1,2,3,9,10)
        """
        if mappings is None:
            mappings = {}
            
        now = time.monotonic()
        dt = max(0.001, min(0.05, now - self.last_time))
        self.last_time = now
        
        axes = state.get('axes', [0.0]*6)
        buttons = set(state.get('buttons', []))
        
        # 1. 检测活跃的前缀换挡键
        current_modifiers = set()
        if 9 in buttons:   # LB
            current_modifiers.add(9)
        if 10 in buttons:  # RB
            current_modifiers.add(10)
            
        lt_val = axes[4] if len(axes) > 4 else 0.0
        rt_val = axes[5] if len(axes) > 5 else 0.0
        
        if lt_val > 0.20:
            current_modifiers.add('LT')
        if rt_val > 0.20:
            current_modifiers.add('RT')
            
        new_modifiers = current_modifiers - self.active_modifiers
        if 'LT' in new_modifiers:
            self.lt_press_time = now
        if 'RT' in new_modifiers:
            self.rt_press_time = now

        # 释放已松开的前缀
        released_mods = self.active_modifiers - current_modifiers
        for rm in released_mods:
            was_consumed = rm in self.consumed_modifiers
            self.consumed_modifiers.discard(rm)
            
            # 若前缀未用于组合换装，松开时触发单按功能
            if not was_consumed:
                if rm == 9: # 单按 LB
                    lb_act = mappings.get('9', {}).get('short', {}).get('value', 'Tab')
                    if lb_act:
                        self.actions.shortcut(lb_act)
                elif rm == 10: # 单按 RB
                    rb_act = mappings.get('10', {}).get('short', {}).get('value', 'V')
                    if rb_act:
                        self.actions.shortcut(rb_act)
                        
            if rm == 'LT' and self.lt_mouse_active:
                self.actions.mouse_button('right', False)
                self.lt_mouse_active = False
            elif rm == 'RT' and self.rt_mouse_active:
                self.actions.mouse_button('left', False)
                self.rt_mouse_active = False
                
        self.active_modifiers = current_modifiers

        # 2. 检查组合换挡触发 (A/B/X/Y: 0, 1, 2, 3)
        new_buttons = buttons - self.last_buttons
        action_buttons = {0, 1, 2, 3}
        chord_fired = False
        
        if self.active_modifiers:
            for btn in action_buttons:
                if btn in buttons and (btn in new_buttons or bool(new_modifiers)):
                    for mod in [9, 'LT', 10, 'RT']:
                        if mod in self.active_modifiers:
                            target_key = CHORD_MAPPINGS.get((mod, btn))
                            if target_key:
                                # 触发瞬发键盘单键
                                self.actions.shortcut(target_key)
                                self.consumed_modifiers.add(mod)
                                chord_fired = True
                                
                                # 取消可能伴随按下的基础动作
                                if btn in self.held_face:
                                    try: self.actions.hold(self.held_face[btn], False)
                                    except Exception: pass
                                    del self.held_face[btn]
                                    
                                if self.on_chord:
                                    try: self.on_chord(f"【3D动作】触发换挡：{target_key}")
                                    except Exception: pass
                                
                                # 扳机作为组合键时，取消其作为鼠标按键的持续判定
                                if mod == 'LT' and self.lt_mouse_active:
                                    self.actions.mouse_button('right', False)
                                    self.lt_mouse_active = False
                                elif mod == 'RT' and self.rt_mouse_active:
                                    self.actions.mouse_button('left', False)
                                    self.rt_mouse_active = False
                                break

        # 3. 基础动作按键处理 (无前缀激活时的 0, 1, 2, 3)
        face_defs = {
            0: mappings.get('0', {}).get('short', {}).get('value', 'Space'),
            1: mappings.get('1', {}).get('short', {}).get('value', 'Shift'),
            2: mappings.get('2', {}).get('short', {}).get('value', 'F'),
            3: mappings.get('3', {}).get('short', {}).get('value', 'E'),
        }
        
        for btn in action_buttons:
            key_name = face_defs.get(btn)
            if not key_name:
                continue
            if btn in buttons and not self.active_modifiers:
                if btn not in self.held_face:
                    self.actions.hold(key_name, True)
                    self.held_face[btn] = key_name
            else:
                if btn in self.held_face:
                    self.actions.hold(self.held_face[btn], False)
                    del self.held_face[btn]

        # 4. 扳机非组合状态下的原生鼠标模拟 (80ms 防误触缓冲，杜绝组合换装时误触发瞄准/普攻)
        has_face_button = bool(buttons & action_buttons)
        if 'LT' in self.active_modifiers and not has_face_button and 'LT' not in self.consumed_modifiers:
            if not self.lt_mouse_active and (now - self.lt_press_time >= 0.08):
                self.actions.mouse_button('right', True)
                self.lt_mouse_active = True
        elif self.lt_mouse_active and ('LT' in self.consumed_modifiers or lt_val < 0.20 or has_face_button):
            self.actions.mouse_button('right', False)
            self.lt_mouse_active = False

        if 'RT' in self.active_modifiers and not has_face_button and 'RT' not in self.consumed_modifiers:
            if not self.rt_mouse_active and (now - self.rt_press_time >= 0.08):
                self.actions.mouse_button('left', True)
                self.rt_mouse_active = True
        elif self.rt_mouse_active and ('RT' in self.consumed_modifiers or rt_val < 0.20 or has_face_button):
            self.actions.mouse_button('left', False)
            self.rt_mouse_active = False

        # 5. 左摇杆平滑驱动 WASD (+ 推满 Shift 疾跑)
        lx = axes[0] if len(axes) > 0 else 0.0
        ly = axes[1] if len(axes) > 1 else 0.0
        target_wasd = stick_to_wasd(lx, ly)
        
        keys_to_press = target_wasd - self.held_wasd
        keys_to_release = self.held_wasd - target_wasd
        
        for k in keys_to_press:
            self.actions.hold(k, True)
        for k in keys_to_release:
            self.actions.hold(k, False)
        self.held_wasd = target_wasd

        # 6. 右摇杆智能平滑驱动鼠标视角 (影视级平滑 + Y轴阻尼 + 边缘调头加速)
        rx = axes[2] if len(axes) > 2 else 0.0
        ry = axes[3] if len(axes) > 3 else 0.0
        mag = math.sqrt(rx*rx + ry*ry)

        if mag >= 0.90:
            self.outer_hold_time = min(0.35, self.outer_hold_time + dt)
        else:
            self.outer_hold_time = max(0.0, self.outer_hold_time - dt * 2.5)

        edge_boost = 1.0 + 0.65 * (self.outer_hold_time / 0.35)
        raw_dx, raw_dy = stick_to_mouse(rx, ry, boost=edge_boost)

        if raw_dx != 0.0 or raw_dy != 0.0:
            # 动态指数平滑滤波 (微调更柔顺，大幅转向更紧凑)
            alpha = 0.65 if mag > 0.6 else 0.45
            self.smooth_dx = self.smooth_dx * (1.0 - alpha) + raw_dx * alpha
            self.smooth_dy = self.smooth_dy * (1.0 - alpha) + raw_dy * alpha
        else:
            # 松手物理惯性阻尼滑行 (约 40ms 平滑收束，彻底消除生硬截断顿挫感)
            decay = max(0.0, 1.0 - dt * 26.0)
            self.smooth_dx *= decay
            self.smooth_dy *= decay
            if abs(self.smooth_dx) < 0.05:
                self.smooth_dx = 0.0
            if abs(self.smooth_dy) < 0.05:
                self.smooth_dy = 0.0
            self.outer_hold_time = 0.0

        if abs(self.smooth_dx) > 0.001 or abs(self.smooth_dy) > 0.001:
            # dt 帧间隔动态时间补偿，消除 Windows 定时器抖动带来的忽快忽慢
            time_factor = dt / 0.01667
            self.mouse_acc_x += self.smooth_dx * time_factor
            self.mouse_acc_y += self.smooth_dy * time_factor
            dx = int(self.mouse_acc_x)
            dy = int(self.mouse_acc_y)
            if dx != 0 or dy != 0:
                if hasattr(self.actions, "guard_cursor_edge"):
                    try: self.actions.guard_cursor_edge()
                    except Exception: pass
                self.actions.move_mouse(dx, dy)
                self.mouse_acc_x -= dx
                self.mouse_acc_y -= dy

        self.last_buttons = buttons
        
        # 返回未被接管的按钮（由 GestureEngine 处理截图、主页、Options 暂停等）
        return buttons - {0, 1, 2, 3, 9, 10}

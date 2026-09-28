"""
Virtual KBM Suite (虚拟键鼠套装引擎)
1. 300Hz 独立高精度多媒体工作线程 (winmm timeBeginPeriod(1))
2. 二阶临界阻尼质点动力学 (Spring-Mass-Damper) + 亚像素时域误差扩散
3. 手柄完全接管伪装：通用按键映射、组合换挡宏 (Chords)、鼠标点击 (Left/Right/Middle)
4. 多方案管理与 3D 动作原生级预设
"""

import sys
import math
import time
import json
import copy
import ctypes
import threading
from pathlib import Path
from typing import Dict, Any, Optional, Callable

# Windows 1ms 多媒体时钟支持
winmm = None
if sys.platform == 'win32':
    try:
        winmm = ctypes.WinDLL('winmm')
    except Exception:
        winmm = None


def set_system_timer_resolution(enable: bool = True):
    if winmm is not None:
        try:
            if enable:
                winmm.timeBeginPeriod(1)
            else:
                winmm.timeEndPeriod(1)
        except Exception:
            pass


# 默认预设 1: 3D 动作 / 开放世界通用预设
NIKKI_SCHEME_NAME = "3D 动作通用预设"
NIKKI_PRESET_CONFIG = {
    "name": NIKKI_SCHEME_NAME,
    "desc": "专为 3D 动作与开放世界定制：右摇杆平滑移动视角/鼠标，A键与RT双通道原生点击；进游戏后A键自动切换为跳跃，RT普攻，LB/LT快捷组合扩展换挡",
    "enabled": True,
    "smart_launcher_click": True,
    "mouse_settings": {
        "deadzone": 0.07,
        "sensitivity": 28.0,
        "y_ratio": 0.65,
        "edge_boost": 1.65,
        "omega": 32.0,      # 二阶响应角频率
    },
    "buttons": {
        "0": "Space",       # A / 南 -> 跳跃 (启动器与桌面上智能自适应为原生鼠标左键点击)
        "1": "Shift",       # B / 东 -> 冲刺 / 闪避 (启动器与桌面上智能自适应为原生鼠标右键)
        "2": "F",           # X / 西 -> 交互 / 拾取
        "3": "E",           # Y / 北 -> 主动技能
        "4": "M",           # View -> 地图
        "6": "Esc",         # Menu -> 暂停/退出
        "7": "Shift",       # L3 -> 疾跑保持
        "8": "V",           # R3 -> 大喵视角
        "9": "Tab",         # LB -> 单按 Tab (能力轮盘)
        "10": "V",          # RB -> 大喵视角
        "11": "3",          # 十字键 ↑
        "12": "4",          # 十字键 ↓
        "13": "G",          # 十字键 ← -> 引导手册
        "14": "M",          # 十字键 → -> 地图
        "LT": "mouse:right", # LT -> 鼠标右键 (瞄准/蓄力)
        "RT": "mouse:left",  # RT -> 鼠标左键 (普攻/点击)
    },
    "chords": {
        "LB + 0": "1",      # LB + A -> 套装 1
        "LB + 1": "2",      # LB + B -> 套装 2
        "LB + 2": "3",      # LB + X -> 套装 3
        "LB + 3": "4",      # LB + Y -> 套装 4
        "LT + 0": "5",      # LT + A -> 套装 5
        "LT + 1": "6",      # LT + B -> 套装 6
        "LT + 2": "7",      # LT + X -> 套装 7
        "LT + 3": "8",      # LT + Y -> 套装 8
    }
}

# 默认预设 2: 全能桌面 / 3D 动作游戏通用方案
GENERAL_SCHEME_NAME = "全能桌面与游戏通用"
GENERAL_PRESET_CONFIG = {
    "name": GENERAL_SCHEME_NAME,
    "desc": "标准 PC 游戏模式：A 键与 RT 鼠标左键，B 键与 LT 鼠标右键，左摇杆 WASD，右摇杆物理鼠标指针",
    "enabled": False,
    "smart_launcher_click": True,
    "mouse_settings": {
        "deadzone": 0.08,
        "sensitivity": 24.0,
        "y_ratio": 0.70,
        "edge_boost": 1.5,
        "omega": 30.0,
    },
    "buttons": {
        "0": "mouse:left",
        "1": "mouse:right",
        "2": "E",
        "3": "F",
        "4": "Tab",
        "6": "Enter",
        "7": "Shift",
        "8": "mouse:middle",
        "9": "Q",
        "10": "E",
        "11": "Up",
        "12": "Down",
        "13": "Left",
        "14": "Right",
        "LT": "mouse:right",
        "RT": "mouse:left",
    },
    "chords": {
        "LB + 0": "1",
        "LB + 1": "2",
        "LB + 2": "3",
        "LB + 3": "4",
    }
}

# 标准化手柄通用槽位清单（免除对手柄型号的关注）
STANDARD_INPUT_SLOTS = [
    {"id": "0", "name": "A 键 (南)", "sub": "Xbox A / PS × / NS B", "group": "动作四键"},
    {"id": "1", "name": "B 键 (东)", "sub": "Xbox B / PS ○ / NS A", "group": "动作四键"},
    {"id": "2", "name": "X 键 (西)", "sub": "Xbox X / PS □ / NS Y", "group": "动作四键"},
    {"id": "3", "name": "Y 键 (北)", "sub": "Xbox Y / PS △ / NS X", "group": "动作四键"},

    {"id": "RT", "name": "RT (右扳机)", "sub": "R2 / RT (模拟量/下压)", "group": "扳机与肩键"},
    {"id": "LT", "name": "LT (左扳机)", "sub": "L2 / LT (模拟量/下压)", "group": "扳机与肩键"},
    {"id": "10", "name": "RB (右肩键)", "sub": "R1 / RB (数字按钮)", "group": "扳机与肩键"},
    {"id": "9",  "name": "LB (左肩键)", "sub": "L1 / LB (数字按钮)", "group": "扳机与肩键"},

    {"id": "11", "name": "十字键 ↑", "sub": "方向键 上", "group": "方向按键"},
    {"id": "12", "name": "十字键 ↓", "sub": "方向键 下", "group": "方向按键"},
    {"id": "13", "name": "十字键 ←", "sub": "方向键 左", "group": "方向按键"},
    {"id": "14", "name": "十字键 →", "sub": "方向键 右", "group": "方向按键"},

    {"id": "7", "name": "L3 (左摇杆下压)", "sub": "Left Stick Click", "group": "摇杆与功能"},
    {"id": "8", "name": "R3 (右摇杆下压)", "sub": "Right Stick Click", "group": "摇杆与功能"},
    {"id": "4", "name": "View / Back / Share", "sub": "视图 / 创建 / 减号", "group": "摇杆与功能"},
    {"id": "6", "name": "Menu / Start / Options", "sub": "菜单 / 开始 / 加号", "group": "摇杆与功能"},
]

STANDARD_CHORD_SLOTS = [
    {"id": "LB + 0", "name": "LB + A", "sub": "换挡层：按住 LB 时按 A"},
    {"id": "LB + 1", "name": "LB + B", "sub": "换挡层：按住 LB 时按 B"},
    {"id": "LB + 2", "name": "LB + X", "sub": "换挡层：按住 LB 时按 X"},
    {"id": "LB + 3", "name": "LB + Y", "sub": "换挡层：按住 LB 时按 Y"},
    {"id": "LT + 0", "name": "LT + A", "sub": "换挡层：按住 LT 时按 A"},
    {"id": "LT + 1", "name": "LT + B", "sub": "换挡层：按住 LT 时按 B"},
    {"id": "LT + 2", "name": "LT + X", "sub": "换挡层：按住 LT 时按 X"},
    {"id": "LT + 3", "name": "LT + Y", "sub": "换挡层：按住 LT 时按 Y"},
    {"id": "RB + 0", "name": "RB + A", "sub": "换挡层：按住 RB 时按 A"},
    {"id": "RB + 1", "name": "RB + B", "sub": "换挡层：按住 RB 时按 B"},
    {"id": "RB + 2", "name": "RB + X", "sub": "换挡层：按住 RB 时按 X"},
    {"id": "RB + 3", "name": "RB + Y", "sub": "换挡层：按住 RB 时按 Y"},
]


def format_action_display(act_str: str) -> str:
    """美化动作在 UI 中的显示徽章"""
    if not act_str:
        return "未绑定"
    if act_str == "mouse:left":
        return "🖱️ 鼠标左键 (点击/普攻)"
    if act_str == "mouse:right":
        return "🖱️ 鼠标右键 (瞄准/蓄力)"
    if act_str == "mouse:middle":
        return "🖱️ 鼠标中键"
    if act_str == "mouse:wheel_up":
        return "🖱️ 滚轮向上"
    if act_str == "mouse:wheel_down":
        return "🖱️ 滚轮向下"
    if act_str == "action:replay_record":
        return "🎬 保存精彩回放 (Win+Alt+G)"
    if act_str == "action:record_toggle":
        return "🎥 开始/停止录屏 (Win+Alt+R)"
    if act_str == "action:capture":
        return "📸 快速截图"
    return f"⌨️ {act_str}"



class VirtualMouseThread(threading.Thread):
    """
    300Hz 独立高精度物理动力学工作线程
    二阶临界阻尼质点系统 + 亚像素误差扩散，彻底消灭阶梯顿挫，赋予镜头真实物理动量
    """
    def __init__(self, actions_provider):
        super().__init__(daemon=True, name="VirtualMouseDynamicsThread")
        self.actions = actions_provider
        self.running = False
        
        # 共享线程安全输入状态
        self._lock = threading.Lock()
        self.stick_x = 0.0
        self.stick_y = 0.0
        
        # 物理动力学参数
        self.deadzone = 0.07
        self.sensitivity = 28.0
        self.y_ratio = 0.65
        self.edge_boost = 1.65
        self.omega = 32.0  # 临界阻尼响应角频率
        
        # 状态机累加器
        self.vel_x = 0.0
        self.vel_y = 0.0
        self.acc_x = 0.0
        self.acc_y = 0.0
        self.outer_hold_time = 0.0

    def configure(self, settings: Dict[str, Any]):
        with self._lock:
            self.deadzone = settings.get("deadzone", 0.07)
            self.sensitivity = settings.get("sensitivity", 28.0)
            self.y_ratio = settings.get("y_ratio", 0.65)
            self.edge_boost = settings.get("edge_boost", 1.65)
            self.omega = settings.get("omega", 32.0)

    def update_stick(self, rx: float, ry: float):
        with self._lock:
            self.stick_x = rx
            self.stick_y = ry

    def run(self):
        self.running = True
        set_system_timer_resolution(True)
        last_t = time.perf_counter()
        
        while self.running:
            now = time.perf_counter()
            dt = max(0.001, min(0.02, now - last_t))
            last_t = now
            
            with self._lock:
                rx, ry = self.stick_x, self.stick_y
                deadzone = self.deadzone
                sensitivity = self.sensitivity
                y_ratio = self.y_ratio
                boost_max = self.edge_boost
                omega = self.omega

            mag = math.sqrt(rx * rx + ry * ry)
            
            # 1. 边缘推满调头加速检测
            if mag >= 0.90:
                self.outer_hold_time = min(0.35, self.outer_hold_time + dt)
            else:
                self.outer_hold_time = max(0.0, self.outer_hold_time - dt * 2.5)
            
            boost = 1.0 + (boost_max - 1.0) * (self.outer_hold_time / 0.35)
            
            # 2. 目标角速度计算 (双曲渐进混合)
            if mag > deadzone:
                if hasattr(self.actions, "guard_cursor_edge"):
                    try:
                        self.actions.guard_cursor_edge()
                    except Exception:
                        pass
                norm = min(1.0, (mag - deadzone) / (1.0 - deadzone))
                # 35% 线性消除死区粘滞阶梯，65% 二次曲线保证大推转向张力
                curve = 0.35 * norm + 0.65 * (norm ** 2.0)
                speed = sensitivity * curve * boost * 60.0  # 像素/秒
                target_vx = (rx / mag) * speed
                target_vy = (ry / mag) * speed * y_ratio
            else:
                target_vx = 0.0
                target_vy = 0.0

            # 3. 二阶临界阻尼质点动力学演进 (质点加速度与惯性衰减)
            # m=1, zeta=1.0 -> 临界阻尼: a = omega * (target_v - v)
            force_x = (target_vx - self.vel_x) * omega
            force_y = (target_vy - self.vel_y) * omega
            
            self.vel_x += force_x * dt
            self.vel_y += force_y * dt
            
            # 微小静止阈值清理
            if abs(self.vel_x) < 0.2 and target_vx == 0.0:
                self.vel_x = 0.0
            if abs(self.vel_y) < 0.2 and target_vy == 0.0:
                self.vel_y = 0.0

            # 4. 高频亚像素时域误差扩散
            self.acc_x += self.vel_x * dt
            self.acc_y += self.vel_y * dt
            
            dx = int(self.acc_x)
            dy = int(self.acc_y)
            
            if dx != 0 or dy != 0:
                try:
                    self.actions.move_mouse(dx, dy)
                except Exception:
                    pass
                self.acc_x -= dx
                self.acc_y -= dy

            # 约 300Hz 轮询 (3.3ms)
            time.sleep(0.0033)
            
        set_system_timer_resolution(False)

    def stop(self):
        self.running = False


class VirtualKbmEngine:
    """
    通用虚拟键鼠套装引擎
    负责全量输入状态解算、组合换挡发射、即时鼠标点击与摇杆移动
    """
    def __init__(self, actions, on_notice: Optional[Callable[[str], None]] = None):
        self.actions = actions
        self.on_notice = on_notice
        
        # 激活的配置方案
        self.scheme = copy.deepcopy(NIKKI_PRESET_CONFIG)
        
        # 启动 300Hz 独立高精度物理鼠标视角线程
        self.mouse_thread = VirtualMouseThread(self.actions)
        self.mouse_thread.configure(self.scheme.get("mouse_settings", {}))
        self.mouse_thread.start()
        
        # 按键与移动跟踪状态
        self.held_wasd = set()
        self.held_buttons = {}  # btn_id -> mapped_key
        self.active_mouse_buttons = set()  # 'left', 'right', 'middle'
        
        # 换挡状态
        self.active_modifiers = set()
        self.consumed_modifiers = set()
        self.fired_chords = set()
        self.last_buttons = set()
        self.last_lb_down = False
        self.last_lt_down = False
        self.last_rt_down = False

    def load_scheme(self, scheme_data: Dict[str, Any]):
        """载入新方案配置"""
        self.reset()
        self.scheme = copy.deepcopy(scheme_data)
        self.mouse_thread.configure(self.scheme.get("mouse_settings", {}))

    def reset(self):
        """释放所有键鼠按压，避免任何粘滞"""
        for k in list(self.held_wasd):
            try: self.actions.hold(k, False)
            except Exception: pass
        self.held_wasd.clear()
        
        for k in list(self.held_buttons.values()):
            try: self.actions.hold(k, False)
            except Exception: pass
        self.held_buttons.clear()
        
        for mb in list(self.active_mouse_buttons):
            try: self.actions.mouse_button(mb, False)
            except Exception: pass
        self.active_mouse_buttons.clear()
        
        self.mouse_thread.update_stick(0.0, 0.0)
        self.active_modifiers.clear()
        self.consumed_modifiers.clear()
        self.fired_chords.clear()
        self.last_buttons.clear()
        self.last_lb_down = False
        self.last_lt_down = False
        self.last_rt_down = False

    def trigger_action(self, action_str: str, down: bool):
        """触发映射动作（支持按键、鼠标点击与宏）"""
        if not action_str:
            return
            
        if action_str.startswith("action:"):
            act = action_str.split(":", 1)[1]
            if down:
                if act == "replay_record":
                    try:
                        self.actions.shortcut('Win+Alt+G')
                        if self.on_notice: self.on_notice('已触发精彩瞬间回放录制 (Win+Alt+G)')
                    except Exception: pass
                elif act == "record_toggle":
                    try:
                        self.actions.shortcut('Win+Alt+R')
                        if self.on_notice: self.on_notice('已切换录屏状态 (Win+Alt+R)')
                    except Exception: pass
                elif act == "capture":
                    try:
                        self.actions.shortcut('Win+Alt+PrtScn')
                        if self.on_notice: self.on_notice('已触发快速截图')
                    except Exception: pass
            return
        elif action_str.startswith("mouse:"):
            mb = action_str.split(":", 1)[1]
            if down:
                if mb not in self.active_mouse_buttons:
                    self.actions.mouse_button(mb, True)
                    self.active_mouse_buttons.add(mb)
            else:
                if mb in self.active_mouse_buttons:
                    self.actions.mouse_button(mb, False)
                    self.active_mouse_buttons.discard(mb)
        else:
            # 键盘按键
            self.actions.hold(action_str, down)

    def trigger_shortcut(self, key_str: str):
        """瞬发单键 / 组合键"""
        try:
            self.actions.shortcut(key_str)
        except Exception:
            pass

    def update(self, state: Dict[str, Any]):
        """
        每帧轮询解算手柄全量状态并驱动虚拟键鼠套装：
        - 300Hz 物理视角线程结合光标边缘守护，防止 3D 视角卡死截断
        - RT 即刻原生鼠标左键点击/蓄力与交互，彻底解决游戏捕获问题
        - LT 瞄准蓄力/右键保持，与 A/B/X/Y 组合秒切套装 5~8
        - LB 独立能力轮盘(Tab)，与 A/B/X/Y 组合秒切套装 1~4
        - 左摇杆 WASD 8向物理平滑移动 + Shift 疾跑保持
        """
        if not self.scheme.get("enabled", True):
            return
            
        axes = state.get('axes', [0.0] * 6)
        buttons = set(state.get('buttons', []))
        button_map = self.scheme.get("buttons", {})
        chords_map = self.scheme.get("chords", {})
        action_buttons = {0, 1, 2, 3}  # A, B, X, Y
        
        # 1. 更新 300Hz 物理视角线程的右摇杆模拟量 (独立物理线程 + 边缘防卡死守护)
        rx = axes[2] if len(axes) > 2 else 0.0
        ry = axes[3] if len(axes) > 3 else 0.0
        self.mouse_thread.update_stick(rx, ry)

        # 2. RT (右扳机)：原生鼠标左键 / 直发按键
        # 绝不延迟、绝不等待松开，下压即刻按住左键 (普攻/蓄力/UI点击)，松开即释放
        rt_val = axes[5] if len(axes) > 5 else 0.0
        rt_pressed = rt_val > 0.25
        if rt_pressed != self.last_rt_down:
            act = button_map.get("RT")
            if act:
                self.trigger_action(act, rt_pressed)
            self.last_rt_down = rt_pressed

        # 3. LB 与 LT 组合换挡检测及按键状态更新
        lb_pressed = 9 in buttons
        lt_val = axes[4] if len(axes) > 4 else 0.0
        lt_pressed = lt_val > 0.25
        
        # 3a. LB 换挡层 (LB + A/B/X/Y -> 套装 1~4)
        if lb_pressed:
            for btn in action_buttons:
                if btn in buttons:
                    chord_key = f"LB + {btn}"
                    target = chords_map.get(chord_key)
                    if target and btn not in self.fired_chords:
                        self.trigger_shortcut(target)
                        self.fired_chords.add(btn)
                        self.consumed_modifiers.add("LB")
                        if self.on_notice:
                            try: self.on_notice(f"【换挡发射】{chord_key} -> {target}")
                            except Exception: pass
        else:
            if self.last_lb_down:
                # LB 松开：若未用于组合换挡，则触发单按功能 (如 Tab 能力轮盘)
                if "LB" not in self.consumed_modifiers:
                    single_act = button_map.get("9")
                    if single_act:
                        self.trigger_shortcut(single_act)
                self.consumed_modifiers.discard("LB")
        self.last_lb_down = lb_pressed

        # 3b. LT 换挡层 (LT + A/B/X/Y -> 套装 5~8) 与 原生鼠标右键 (瞄准/蓄力)
        if lt_pressed:
            chord_fired_now = False
            for btn in action_buttons:
                if btn in buttons:
                    chord_key = f"LT + {btn}"
                    target = chords_map.get(chord_key)
                    if target and btn not in self.fired_chords:
                        # 触发换装组合，立即释放可能激活的原生瞄准/右键
                        if self.last_lt_down:
                            lt_act = button_map.get("LT")
                            if lt_act:
                                self.trigger_action(lt_act, False)
                            self.last_lt_down = False
                        self.trigger_shortcut(target)
                        self.fired_chords.add(btn)
                        self.consumed_modifiers.add("LT")
                        chord_fired_now = True
                        if self.on_notice:
                            try: self.on_notice(f"【换挡发射】{chord_key} -> {target}")
                            except Exception: pass
            
            # 若当前没有组合键触发，且 LT 未被组合消耗，则立即激活单按瞄准/右键 (如 mouse:right)
            if not chord_fired_now and "LT" not in self.consumed_modifiers:
                if not self.last_lt_down:
                    lt_act = button_map.get("LT")
                    if lt_act:
                        self.trigger_action(lt_act, True)
                    self.last_lt_down = True
        else:
            if self.last_lt_down:
                lt_act = button_map.get("LT")
                if lt_act:
                    self.trigger_action(lt_act, False)
                self.last_lt_down = False
            self.consumed_modifiers.discard("LT")

        # 4. 基础面部按键映射 (A, B, X, Y: 0, 1, 2, 3)
        # 智能感知：当处于启动器 (xstarter.exe / launcher.exe) 或 Windows 桌面时，
        # A 键自动切换为原生鼠标左键点击，方便对准后一键点击“开始游戏”！
        # 一旦 3D 游戏本体窗口处于前台，A 键瞬间恢复为跳跃 (Space)。
        smart_click = self.scheme.get("smart_launcher_click", True)
        in_game = True
        if smart_click and hasattr(self.actions, "is_nikki_game_focused"):
            try:
                in_game = self.actions.is_nikki_game_focused()
            except Exception:
                in_game = True

        for btn in action_buttons:
            if btn in buttons:
                if btn not in self.fired_chords and not lb_pressed and not (lt_pressed and "LT" in self.consumed_modifiers):
                    if btn not in self.held_buttons:
                        act = button_map.get(str(btn))
                        if not in_game:
                            if btn == 0 and act == "Space":
                                act = "mouse:left"
                            elif btn == 1 and act in ("Shift", "1"):
                                act = "mouse:right"
                        if act:
                            self.trigger_action(act, True)
                            self.held_buttons[btn] = act
            else:
                self.fired_chords.discard(btn)
                if btn in self.held_buttons:
                    self.trigger_action(self.held_buttons[btn], False)
                    del self.held_buttons[btn]

        # 5. 其他常规按键直通 (动态支持任意按键编号 4..63 及飞行摇杆按键)
        for sid, act in button_map.items():
            if not sid.isdigit():
                continue
            btn_id = int(sid)
            if btn_id in action_buttons:
                continue
            if btn_id in buttons:
                if btn_id not in self.held_buttons and btn_id not in self.fired_chords:
                    self.trigger_action(act, True)
                    self.held_buttons[btn_id] = act
            else:
                if btn_id in self.held_buttons:
                    self.trigger_action(self.held_buttons[btn_id], False)
                    del self.held_buttons[btn_id]

        # 6. 左摇杆平滑驱动 8 向 WASD 与 Shift 疾跑 (支持自定义摇杆四向重映射)
        lx = axes[0] if len(axes) > 0 else 0.0
        ly = axes[1] if len(axes) > 1 else 0.0
        target_wasd = set()
        l_mag = math.sqrt(lx * lx + ly * ly)

        ls_up = button_map.get("LS:Up", "W")
        ls_down = button_map.get("LS:Down", "S")
        ls_left = button_map.get("LS:Left", "A")
        ls_right = button_map.get("LS:Right", "D")
        
        if l_mag > 0.16:
            angle = (math.degrees(math.atan2(-ly, lx)) + 360.0) % 360.0
            if 22.5 <= angle < 157.5 and ls_up:
                target_wasd.add(ls_up)
            if 202.5 <= angle < 337.5 and ls_down:
                target_wasd.add(ls_down)
            if 112.5 <= angle < 247.5 and ls_left:
                target_wasd.add(ls_left)
            if (angle < 67.5 or angle >= 292.5) and ls_right:
                target_wasd.add(ls_right)
            if l_mag >= 0.88:
                target_wasd.add('Shift')
                
        keys_to_press = target_wasd - self.held_wasd
        keys_to_release = self.held_wasd - target_wasd
        for k in keys_to_press:
            self.actions.hold(k, True)
        for k in keys_to_release:
            self.actions.hold(k, False)
        self.held_wasd = target_wasd

        self.last_buttons = buttons

    def close(self):
        self.reset()
        self.mouse_thread.stop()


def get_trigger_label(trigger_id: str, family: str = 'generic') -> str:
    """获取硬件触发器在 UI 虚拟按键上的简明徽章文本"""
    if not trigger_id:
        return ""
    if trigger_id == "RT":
        return "RT" if family in ('xbox', 'generic') else "R2"
    if trigger_id == "LT":
        return "LT" if family in ('xbox', 'generic') else "L2"
    if trigger_id == "LS:Up": return "摇杆↑"
    if trigger_id == "LS:Down": return "摇杆↓"
    if trigger_id == "LS:Left": return "摇杆←"
    if trigger_id == "LS:Right": return "摇杆→"
    if trigger_id == "RS:Up": return "右杆↑"
    if trigger_id == "RS:Down": return "右杆↓"
    if trigger_id == "RS:Left": return "右杆←"
    if trigger_id == "RS:Right": return "右杆→"
    
    # 组合换挡
    if " + " in trigger_id:
        prefix, btn = trigger_id.split(" + ", 1)
        sub_label = get_trigger_label(btn, family)
        return f"{prefix}+{sub_label}"
        
    if trigger_id.isdigit():
        idx = int(trigger_id)
        if family in ('dualsense', 'dualshock4'):
            ps_map = {0: "×", 1: "○", 2: "□", 3: "△", 4: "Create", 6: "Options", 7: "L3", 8: "R3", 9: "L1", 10: "R1", 11: "↑", 12: "↓", 13: "←", 14: "→", 15: "Mic"}
            if idx in ps_map: return ps_map[idx]
        elif family == 'xbox':
            xb_map = {0: "A", 1: "B", 2: "X", 3: "Y", 4: "View", 6: "Menu", 7: "LS", 8: "RS", 9: "LB", 10: "RB", 11: "↑", 12: "↓", 13: "←", 14: "→", 15: "Share"}
            if idx in xb_map: return xb_map[idx]
        elif family == 'switch':
            sw_map = {0: "B", 1: "A", 2: "Y", 3: "X", 4: "−", 6: "＋", 7: "LS", 8: "RS", 9: "L", 10: "R", 11: "↑", 12: "↓", 13: "←", 14: "→", 15: "Capture"}
            if idx in sw_map: return sw_map[idx]
        
        gen_map = {0: "A", 1: "B", 2: "X", 3: "Y", 4: "Back", 6: "Start", 7: "L3", 8: "R3", 9: "LB", 10: "RB", 11: "↑", 12: "↓", 13: "←", 14: "→"}
        if idx in gen_map:
            return gen_map[idx]
        return f"键{idx+1}"
        
    return trigger_id


def get_inverted_mapping(scheme: Dict[str, Any]) -> Dict[str, list]:
    """反转方案映射：将目标动作映射为触发该动作的所有外设按键列表"""
    inv = {}
    buttons = scheme.get("buttons", {})
    for tid, act in buttons.items():
        if act:
            inv.setdefault(act, []).append(tid)
            
    chords = scheme.get("chords", {})
    for cid, act in chords.items():
        if act:
            inv.setdefault(act, []).append(cid)
            
    return inv

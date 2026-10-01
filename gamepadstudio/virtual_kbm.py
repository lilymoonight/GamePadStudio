"""
Virtual KBM Suite (虚拟键鼠套装引擎)
1. 300Hz 独立高精度多媒体工作线程 (winmm timeBeginPeriod(1))
2. 二阶临界阻尼质点动力学 (Spring-Mass-Damper) + 亚像素时域误差扩散
3. 手柄完全接管伪装：通用按键映射、组合换挡宏 (Chords)、鼠标点击 (Left/Right/Middle)
4. 多方案管理与《无限暖暖》原生级预设
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
    "desc": "专为 3D 动作与开放世界定制：500Hz 物理鼠标级瞬时速度跟踪，右摇杆无延迟平滑转镜；A跳跃/B冲刺/X交互/Y挥击净化/RB技能/LB轮盘(短按)，方向键大喵寻宝与奇想道具，LB组合键全景拍照与快捷换装",
    "enabled": True,
    "timing_window_s": 0.20,
    "smart_launcher_click": True,
    "mouse_settings": {
        "deadzone": 0.06,
        "sensitivity": 28.0,
        "y_ratio": 0.70,
        "edge_boost": 1.70,
        "omega": 32.0,
    },
    "buttons": {
        "0": "Space",       # A / 南 -> 跳跃 / 浮空 (启动器与桌面上智能自适应为原生鼠标左键点击)
        "1": "Shift",       # B / 东 -> 冲刺 / 闪避 (启动器与桌面上智能自适应为原生鼠标右键)
        "2": "F",           # X / 西 -> 交互 / 拾取 / 开箱 / 对话
        "3": "Q",           # Y / 北 -> 挥动净化星光 / 魔法攻击 (对应画面右下角 Q 星光图标)
        "4": "action:replay_record", # Create / View -> 保存精彩回放视频
        "6": "Esc",         # Menu -> 暂停 / 系统菜单
        "7": "Shift",       # L3 -> 疾跑长按保持
        "8": "mouse:left",  # R3 -> 鼠标左键 (防抖锁定，启动器点击/游戏内交互)
        "9": {"short": "Tab", "long": "none"},  # LB -> 短按换装能力轮盘，长按静默作为组合换挡修饰键
        "10": "E",          # RB -> 奇想主动技能 / 核心动作 (对应画面右下角 E 风车图标)
        "11": "V",          # 十字键 ↑ -> 大喵视角 / 寻宝指引 (对应左上角大喵猫咪头像)
        "12": "Z",          # 十字键 ↓ -> 随身道具 / 奇想小摆件 (对应底部生命条旁 Z 图标)
        "13": "X",          # 十字键 ← -> 奇想摇铃 (对应右下角铃铛 X 图标)
        "14": "R",          # 十字键 → -> 特殊奇想道具槽 (对应右侧齿轮道具 R 图标)
        "LT": "mouse:right", # LT -> 鼠标右键 (长按瞄准 / 奇想蓄力 / 聚焦观察)
        "RT": "mouse:left",  # RT -> 鼠标左键 (普攻 / 挥击 / 快门拍照 / 启动器点击)
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
        "LB + 11": "P",     # LB + 十字键 ↑ -> 拍照模式 (左上相机图标 P)
        "LB + 12": "C",     # LB + 十字键 ↓ -> 换装界面 (右上衣橱图标 C)
        "LB + 13": "I",     # LB + 十字键 ← -> 任务追踪 (右上任务图标 I)
        "LB + 14": "O",     # LB + 十字键 → -> 共鸣抽卡 (右上共鸣图标 O)
        "LB + 8": "CapsLock", # LB + R3 -> 镜头复位 / 视角锁定 (右侧双箭头图标 CapsLock)
        "LB + RT": "action:capture", # LB + RT -> 无损 4K 截图 (带机械快门音效与震动)
        "LB + LT": "action:replay_record", # LB + LT -> 保存精彩瞬间 (回放录制)
        "LB + 10": "action:replay_record", # LB + RB -> 精彩回放兼容快捷键
    }
}

# 默认预设 2: 全能桌面 / 3D 动作游戏通用方案
GENERAL_SCHEME_NAME = "全能桌面与游戏通用"
GENERAL_PRESET_CONFIG = {
    "name": GENERAL_SCHEME_NAME,
    "desc": "标准 PC 游戏模式：A 键与 RT 鼠标左键，B 键与 LT 鼠标右键，左摇杆 WASD，右摇杆物理鼠标指针",
    "enabled": False,
    "timing_window_s": 0.20,
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
        "9": {"short": "Q", "long": "none"},
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


def format_action_display(act_str: Any) -> str:
    """美化动作在 UI 中的显示徽章"""
    if isinstance(act_str, dict):
        short_act = act_str.get("short", "")
        long_act = act_str.get("long", "")
        if short_act and (not long_act or str(long_act).lower() in ("none", "null", "false", "")):
            return f"{format_action_display(short_act)} (短按)"
        if short_act == long_act:
            return format_action_display(short_act)
        return f"{format_action_display(short_act)} / {format_action_display(long_act)}"

    if not act_str or str(act_str).lower() in ("none", "null", "false", ""):
        return "未绑定"
    act_str = str(act_str).strip()
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
    500Hz 物理鼠标级瞬时速度跟踪工作线程
    借鉴专业电竞鼠标传感器与 Steam Input 算法：
    1. 零质量阻尼（Zero-Inertia）：彻底移除二阶弹簧滞后，实现手到眼到的 1:1 瞬发响应；
    2. Steam Input 黄金伽马响应曲线 (x^2.2)：微推极其细腻平滑（微距瞄准），推满平滑无缝衔接 180° 调头；
    3. 500Hz 高频时钟配合亚像素误差时域平滑扩散，彻底消灭 0/1/0/2 阶梯顿挫；
    4. 回正绝对硬刹车（Zero Drift）：摇杆归位瞬间切断一切位移并重置积分器，绝无拖尾漂移。
    """
    def __init__(self, actions_provider):
        super().__init__(daemon=True, name="VirtualMouseDynamicsThread")
        self.actions = actions_provider
        self.running = True
        
        # 共享线程安全输入状态
        self._lock = threading.Lock()
        self.stick_x = 0.0
        self.stick_y = 0.0
        self.is_desktop = False
        self.click_locked = False
        
        # 物理动力学参数
        self.deadzone = 0.06
        self.sensitivity = 28.0
        self.y_ratio = 0.70
        self.edge_boost = 1.70
        
        # 亚像素积分累加器
        self.acc_x = 0.0
        self.acc_y = 0.0
        self.outer_hold_time = 0.0

    def configure(self, settings: Dict[str, Any]):
        with self._lock:
            self.deadzone = settings.get("deadzone", 0.06)
            self.sensitivity = settings.get("sensitivity", 28.0)
            self.y_ratio = settings.get("y_ratio", 0.70)
            self.edge_boost = settings.get("edge_boost", 1.70)

    def update_stick(self, rx: float, ry: float, is_desktop: bool = False):
        with self._lock:
            self.stick_x = rx
            self.stick_y = ry
            self.is_desktop = is_desktop

    def set_click_lock(self, locked: bool):
        with self._lock:
            self.click_locked = locked

    def run(self):
        try:
            from .actions import attach_to_default_desktop
            attach_to_default_desktop()
        except Exception:
            pass
        set_system_timer_resolution(True)
        last_t = time.perf_counter()
        
        while self.running:
            now = time.perf_counter()
            dt = now - last_t
            if dt < 0.0019:  # 约 500Hz 限频 (2ms 周期)，保证高频亚像素平滑并避免空转
                time.sleep(0.001)
                continue
            last_t = now
            dt = min(0.02, dt)
            
            with self._lock:
                rx, ry = self.stick_x, self.stick_y
                deadzone = self.deadzone
                sensitivity = self.sensitivity
                is_desktop = self.is_desktop
                y_ratio = 1.0 if is_desktop else self.y_ratio
                boost_max = self.edge_boost
                is_click_locked = self.click_locked

            mag = math.sqrt(rx * rx + ry * ry)
            # 点击防抖锁定：按下鼠标按键时，过滤掉拇指下压微小晃动，防止 Windows 识别为拖拽而取消按钮点击
            if is_click_locked and mag < 0.35:
                mag = 0.0
            
            if mag > deadzone:
                # 归一化死区偏移 (0.0 ~ 1.0)
                norm = min(1.0, (mag - deadzone) / (1.0 - deadzone))
                
                # 边缘推满调头动态增益 (当摇杆推向极限区时平滑蓄力加速)
                if norm >= 0.88:
                    self.outer_hold_time = min(0.30, self.outer_hold_time + dt)
                else:
                    self.outer_hold_time = max(0.0, self.outer_hold_time - dt * 3.0)
                boost = 1.0 + (boost_max - 1.0) * (self.outer_hold_time / 0.30)
                
                # 工业级混合幂次曲线：25% 线性响应保底 (低推力即刻跟手，桌面选点与微距瞄准清脆敏锐) + 75% 幂律顺滑转镜
                power_exp = 1.5 if is_desktop else 2.0
                curve = 0.25 * norm + 0.75 * (norm ** power_exp)
                effective_sens = sensitivity * (1.20 if is_desktop else 1.0)
                speed = effective_sens * curve * boost * 60.0  # 像素/秒
                
                target_vx = (rx / mag) * speed
                target_vy = (ry / mag) * speed * y_ratio
                
                # 边缘守护节流检查 (仅在 3D 游戏处于前台时检查)
                if not is_desktop and (not hasattr(self, '_last_guard') or (now - self._last_guard > 0.10)):
                    self._last_guard = now
                    if hasattr(self.actions, 'guard_cursor_edge'):
                        try:
                            self.actions.guard_cursor_edge()
                        except Exception:
                            pass
            else:
                target_vx = 0.0
                target_vy = 0.0
                self.outer_hold_time = 0.0
                # 摇杆回正瞬间：强制清零亚像素累加器，回正即停，绝对不带任何残余漂移
                self.acc_x = 0.0
                self.acc_y = 0.0

            # 亚像素时域时钟积分
            self.acc_x += target_vx * dt
            self.acc_y += target_vy * dt
            
            dx = int(self.acc_x)
            dy = int(self.acc_y)
            
            if dx != 0 or dy != 0:
                try:
                    self.actions.move_mouse(dx, dy)
                except Exception:
                    pass
                self.acc_x -= dx
                self.acc_y -= dy
            
        set_system_timer_resolution(False)

    def stop(self):
        self.running = False


class KeyPressState:
    IDLE = 0
    PENDING = 1      # 按下，处于等待判定窗口 [t_press, t_press + X]
    LONG_PRESS = 2   # 达到 X ms，长按效果已触发并持续生效中
    CONSUMED = 3     # 已被组合键消耗，释放时绝不结算任何单键/短按动作


def get_key_aliases(k: str) -> list:
    """返回按键的标准名称及所有等价别名 (例: 9/LB/L1, 10/RB/R1, LT/L2, RT/R2, 0/A, 1/B 等)"""
    s = str(k).strip()
    u = s.upper()
    aliases = [s]
    if u in ("9", "LB", "L1"):
        aliases.extend(["LB", "9", "L1"])
    elif u in ("10", "RB", "R1"):
        aliases.extend(["RB", "10", "R1"])
    elif u in ("LT", "L2"):
        aliases.extend(["LT", "L2"])
    elif u in ("RT", "R2"):
        aliases.extend(["RT", "R2"])
    elif u in ("0", "A", "CROSS"):
        aliases.extend(["0", "A"])
    elif u in ("1", "B", "CIRCLE"):
        aliases.extend(["1", "B"])
    elif u in ("2", "X", "SQUARE"):
        aliases.extend(["2", "X"])
    elif u in ("3", "Y", "TRIANGLE"):
        aliases.extend(["3", "Y"])
    elif u in ("4", "VIEW", "SELECT", "BACK", "CREATE"):
        aliases.extend(["4", "VIEW", "SELECT", "BACK", "CREATE"])
    elif u in ("5", "GUIDE", "XBOX", "PS", "HOME"):
        aliases.extend(["5", "GUIDE", "XBOX", "PS", "HOME"])
    elif u in ("15", "SHARE", "CAPTURE"):
        aliases.extend(["15", "SHARE", "CAPTURE"])
    elif u in ("6", "MENU", "START", "OPTIONS"):
        aliases.extend(["6", "MENU", "START", "OPTIONS"])
    elif u in ("7", "L3", "LS"):
        aliases.extend(["7", "L3"])
    elif u in ("8", "R3", "RS"):
        aliases.extend(["8", "R3"])
    elif u in ("11", "UP"):
        aliases.extend(["11", "UP"])
    elif u in ("12", "DOWN"):
        aliases.extend(["12", "DOWN"])
    elif u in ("13", "LEFT"):
        aliases.extend(["13", "LEFT"])
    elif u in ("14", "RIGHT"):
        aliases.extend(["14", "RIGHT"])

    seen = set()
    res = []
    for a in aliases:
        if a not in seen:
            seen.add(a)
            res.append(a)
    return res


def match_chord(k1: str, k2: str, chords_map: Dict[str, Any]) -> Optional[tuple]:
    """
    检查两个物理按键 (k1, k2) 是否匹配已定义的组合宏。
    支持对称无序匹配及别名自动解析 (LB+LT 等价于 LT+LB，LB+0 等价于 9+0)。
    返回 (匹配到的定义键名, 目标动作) 或 None。
    """
    if not chords_map:
        return None
    a1_list = get_key_aliases(k1)
    a2_list = get_key_aliases(k2)
    for a1 in a1_list:
        for a2 in a2_list:
            for cand in (f"{a1} + {a2}", f"{a2} + {a1}"):
                if cand in chords_map:
                    return (cand, chords_map[cand])
                for ck, val in chords_map.items():
                    if ck.strip().upper() == cand.upper():
                        return (ck, val)
    return None


def get_button_action(k: str, gesture: str, button_map: Dict[str, Any], in_game: bool) -> Optional[str]:
    """
    根据物理按键 ID 及手势 ('short' 或 'long') 解析其目标动作，
    并根据游戏/桌面环境进行智能自适应。
    配置支持两种格式：
    1. 字典格式: {"short": "Tab", "long": "none"}
    2. 字符串格式: "Space" (若未显式区分，按通用语义短按与长按均映射为此动作)
    """
    aliases = get_key_aliases(k)
    raw_entry = None
    for a in aliases:
        if a in button_map:
            raw_entry = button_map[a]
            break

    if not raw_entry:
        return None

    if isinstance(raw_entry, dict):
        act = raw_entry.get(gesture)
    else:
        act = raw_entry

    if not act or str(act).strip().lower() in ("none", "null", "false", ""):
        return None

    act = str(act).strip()

    if not in_game:
        # 桌面/启动器环境智能自适应
        if k == "0" and act == "Space":
            return "mouse:left"
        if k == "1" and act in ("Shift", "1"):
            return "mouse:right"

    return act


def get_single_action(k: str, button_map: Dict[str, Any], in_game: bool) -> Optional[str]:
    """向后兼容接口：获取按键的单按动作 (默认 short 短按语义)"""
    return get_button_action(k, "short", button_map, in_game)


def get_trigger_label(trigger_id: str, family: str = 'generic') -> str:
    """获取硬件触发器在 UI 虚拟按键上的简明徽章文本"""
    from .i18n import get_language
    is_en = (get_language() == 'en')
    if not trigger_id:
        return ""
    if trigger_id == "RT":
        return "RT" if family in ('xbox', 'generic') else "R2"
    if trigger_id == "LT":
        return "LT" if family in ('xbox', 'generic') else "L2"
    if trigger_id == "LS:Up": return "LS↑" if is_en else "摇杆↑"
    if trigger_id == "LS:Down": return "LS↓" if is_en else "摇杆↓"
    if trigger_id == "LS:Left": return "LS←" if is_en else "摇杆←"
    if trigger_id == "LS:Right": return "LS→" if is_en else "摇杆→"
    if trigger_id == "RS:Up": return "RS↑" if is_en else "右杆↑"
    if trigger_id == "RS:Down": return "RS↓" if is_en else "右杆↓"
    if trigger_id == "RS:Left": return "RS←" if is_en else "右杆←"
    if trigger_id == "RS:Right": return "RS→" if is_en else "右杆→"
    
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
        return f"B{idx+1}" if is_en else f"键{idx+1}"
        
    return trigger_id


def get_inverted_mapping(scheme: Dict[str, Any]) -> Dict[str, list]:
    """反转方案映射：将目标动作映射为触发该动作的所有外设按键列表"""
    inv = {}
    buttons = scheme.get("buttons", {})
    for tid, act in buttons.items():
        if isinstance(act, dict):
            for g_k, g_act in act.items():
                if g_act and str(g_act).lower() not in ("none", "null", "false", ""):
                    inv.setdefault(g_act, []).append(tid)
        elif act and str(act).lower() not in ("none", "null", "false", ""):
            inv.setdefault(act, []).append(tid)

    chords = scheme.get("chords", {})
    for cid, act in chords.items():
        if act and str(act).lower() not in ("none", "null", "false", ""):
            inv.setdefault(act, []).append(cid)

    for k in inv:
        seen = set()
        dedup = []
        for x in inv[k]:
            if x not in seen:
                seen.add(x)
                dedup.append(x)
        inv[k] = dedup

    return inv

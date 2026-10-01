"""
Internationalization (i18n) module for GamePad Studio.
Provides high-fidelity bilingual support (Simplified Chinese & English)
with zero external dependencies and automatic OS locale detection.
"""
from __future__ import annotations

import locale
import os
import sys
from typing import Dict, Optional, Callable, List

# Active language state
_CURRENT_PREFERENCE: str = 'auto'
_CURRENT_LANG: str = 'zh'
_LISTENERS: List[Callable[[], None]] = []


def detect_system_language() -> str:
    """Detect operating system UI language. Returns 'zh' for Chinese locales, 'en' otherwise."""
    if sys.platform == 'win32':
        try:
            import ctypes
            # GetUserDefaultUILanguage returns LANGID (e.g. 0x0804 for zh-CN, 0x0404 for zh-TW)
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            primary_lang = lang_id & 0x3FF
            if primary_lang == 0x04:  # LANG_CHINESE
                return 'zh'
        except Exception:
            pass

    # Fallback to locale
    try:
        loc = locale.getlocale()[0] or ''
        if not loc:
            loc = os.environ.get('LANG', '') or os.environ.get('LC_ALL', '')
        if loc.lower().startswith('zh'):
            return 'zh'
    except Exception:
        pass

    return 'en'


def init_language(pref: Optional[str] = None):
    """Initialize language from preference ('auto', 'zh', 'en')."""
    global _CURRENT_PREFERENCE, _CURRENT_LANG
    if pref in ('zh', 'en', 'auto'):
        _CURRENT_PREFERENCE = pref
    else:
        _CURRENT_PREFERENCE = 'auto'

    if _CURRENT_PREFERENCE == 'auto':
        _CURRENT_LANG = detect_system_language()
    else:
        _CURRENT_LANG = _CURRENT_PREFERENCE


def set_language(pref: str):
    """Update language preference and notify listeners."""
    init_language(pref)
    for listener in _LISTENERS:
        try:
            listener()
        except Exception:
            pass


def get_language() -> str:
    """Returns 'zh' or 'en'."""
    return _CURRENT_LANG


def get_language_preference() -> str:
    """Returns 'auto', 'zh', or 'en'."""
    return _CURRENT_PREFERENCE


def is_english() -> bool:
    return _CURRENT_LANG == 'en'


def register_language_listener(cb: Callable[[], None]):
    if cb not in _LISTENERS:
        _LISTENERS.append(cb)


# Translation dictionary: { Chinese / Canonical Key : English Translation }
TRANSLATIONS: Dict[str, str] = {
    # Navigation & Windows
    'GamePad Studio': 'GamePad Studio',
    '控制器库': 'Controller Library',
    '设备概览': 'Overview',
    '虚拟键鼠': 'Virtual KBM',
    '按键配置': 'Key Mapping',
    '截图图库': 'Captures Gallery',
    '硬件遥测': 'Telemetry',
    '系统设置': 'Settings',
    '使用指南': 'User Guide',
    'GamePad Studio · 手柄控制中心 (返回概览)': 'GamePad Studio · Gamepad Control Center (Back to Overview)',
    '最小化': 'Minimize',
    '最大化 / 还原': 'Maximize / Restore',
    '关闭窗口': 'Close Window',
    '关闭': 'Close',
    '帮助': 'Help',

    # Status Badges
    '未连接': 'Disconnected',
    '●  已连接': '●  Connected',
    '○  等待连接': '○  Waiting for Connection',
    '○  未连接': '○  Disconnected',
    '已连接': 'Connected',
    '可切换': 'Available',
    '连接中': 'Connecting',
    '正在连接': 'Connecting',
    '运行中': 'Running',
    '已停止': 'Stopped',
    '暂停手柄映射': 'Pause Gamepad Mapping',
    '暂停映射': 'Pause Mapping',
    '恢复映射': 'Resume Mapping',
    '即时截屏 (Create / F12)': 'Instant Screenshot (Create / F12)',

    # Battery & Power
    '电量未报告': 'Battery Unknown',
    '电量极低': 'Battery Critical',
    '电量低': 'Battery Low',
    '电量中等': 'Battery Medium',
    '电量充足': 'Battery Full',
    '外接供电': 'External Power',
    '电量未知': 'Power Unknown',
    '上次使用的型号 · 当前未连接': 'Last used model · Currently disconnected',
    '设备未提供灯条控制': 'Device does not support lightbar control',
    '灯条颜色': 'Lightbar Color',
    '设备未提供触摸板': 'Device has no touchpad',
    '触摸板鼠标': 'Touchpad Mouse',

    # Navigation Rows
    'Xbox 导航': 'Xbox Guide',
    'Home 导航': 'Home Guide',
    'PS 导航': 'PS Guide',
    '系统导航': 'System Guide',
    'Xbox 导航键：短按呼出 Game Bar，长按切换任务': 'Xbox Guide: Short press for Game Bar, long press for Task View',
    'Switch Home 键：短按返回主界面，长按快捷菜单': 'Switch Home: Short press for Home, long press for Quick Menu',
    'PlayStation 系统键：呼出控制中心与多任务切换': 'PlayStation Key: Control Center and task switching',
    '通用手柄 Guide 键：呼出系统快捷主控': 'Universal Guide: System quick master controls',

    # Overview Page
    'HARDWARE WORKSTATION': 'HARDWARE WORKSTATION',
    '01 // CONTROL & PROFILES': '01 // CONTROL & PROFILES',
    '02 // SHORTCUT DISPATCH': '02 // SHORTCUT DISPATCH',
    '配置预设': 'Profile Preset',
    '按键映射方案与预设配置切换': 'Switch button mapping scheme & presets',
    '触觉反馈': 'Haptic Feedback',
    '双马达触觉脉冲与响应测试': 'Dual-motor haptic pulse & response test',
    '脉冲测试': 'Test Pulse',
    '按键映射': 'Key Mapping',
    '自定义按键键位与长按/短按宏映射': 'Custom button bindings & macro mappings',
    '编辑按键 ›': 'Edit Keys ›',
    '截图': 'Captures',
    '查看图库 ›': 'View Gallery ›',
    '配置按键 ›': 'Configure Keys ›',
    '系统菜单 ›': 'System Menu ›',
    '触摸板': 'Touchpad',
    '手势映射 ›': 'Gestures ›',
    'CAPTURES BUFFER // 缓冲与最近保存': 'CAPTURES BUFFER // Buffer & Recents',
    '近期截图': 'Recent Captures',
    '查看完整图库 ›': 'View Full Gallery ›',
    '暂无截图': 'No captures yet',
    '拍摄于 ': 'Captured at ',

    # Mapping Page & Dialog
    '当前配置预设：': 'Active Profile:',
    '动态识别按键': 'Learn Button',
    '识别手柄按键': 'Recognize Button',
    '请按手柄按键…': 'Press Gamepad Button…',
    '按下要配置的手柄按键（10 秒内）': 'Press gamepad button to configure (within 10s)',
    '恢复默认配置': 'Restore Defaults',
    '新建配置预设': 'New Profile',
    '删除配置预设': 'Delete Profile',
    'SELECTED INPUT // 当前按键': 'SELECTED INPUT // Active Input',
    '配置动作...': 'Configure Action...',
    '编辑 ': 'Edit ',
    'SHORT PRESS // 短按': 'SHORT PRESS // Short',
    'LONG PRESS // 长按': 'LONG PRESS // Long',
    '短按': 'Short Press',
    '长按': 'Long Press',
    '原始输入': 'Pass-through',
    '编辑映射 · ': 'Edit Mapping · ',
    '选择应用': 'Select Application',
    '选择程序…': 'Browse…',
    '启动参数（可选）': 'Launch Arguments (Optional)',
    '键盘映射不会屏蔽游戏接收到的原始手柄输入。': 'Keyboard mapping does not block native controller input to games.',
    '保存映射': 'Save Mapping',
    '取消': 'Cancel',
    '另存为预设': 'Save as New Profile',
    '配置名称': 'Profile Name',
    '名称重复': 'Duplicate Name',
    '请使用不同的配置名称。': 'Please use a unique profile name.',
    '恢复默认': 'Restore Defaults',
    '无法删除配置': 'Cannot Delete Profile',

    # Action Names
    '保留原始输入': 'Pass-through (Native Input)',
    '保存截图': 'Take Screenshot',
    '打开截图资料库': 'Open Captures Gallery',
    '打开控制中心': 'Open Control Center',
    '键盘快捷键': 'Keyboard Shortcut',
    '按住键盘按键': 'Hold Keyboard Key',
    '启动应用 / 命令': 'Launch App / Command',
    '系统声音静音': 'Mute System Audio',
    '音量 +': 'Volume +',
    '音量 −': 'Volume −',
    '播放 / 暂停': 'Play / Pause Media',
    '保存精彩瞬间 (回放录制)': 'Instant Replay (Save Clip)',
    '开始/停止录屏': 'Toggle Screen Recording',

    # Captures Gallery
    '仅看收藏': 'Favorites Only',
    '打开截图文件夹': 'Open Captures Folder',
    '清理未收藏截图': 'Clean Unfavorited',
    '搜索截图文件...': 'Search captures...',
    '收藏': 'Favorite',
    '取消收藏': 'Unfavorite',
    '删除截图': 'Delete Capture',
    '截图预览': 'Capture Preview',
    '复制图像': 'Copy Image',
    '打开原图': 'Open Original',
    '截图保存位置': 'Captures Storage Location',
    '截图位置：': 'Captures Location: ',
    '截图目录已更新': 'Captures directory updated',
    '截图文件不存在或已被删除': 'Capture file does not exist or was deleted',
    '没有可清理的未收藏截图': 'No unfavorited captures to clean',
    '清理未收藏截图': 'Clean Unfavorited Captures',

    # Diagnostics & Telemetry
    '活动记录': 'Activity Log',
    '测试保护': 'Test Protection',
    '显示摇杆运动轨迹': 'Show Stick Motion Trail',
    '圆周测试': 'Circular Sweep Test',
    '  ONLINE · 实时遥测  ': '  ONLINE · Real-time Telemetry  ',
    'SDL 原始标准轴值；不应用软件死区。': 'SDL raw normalized values; software deadzone bypassed.',
    '摇杆坐标与运动轨迹': 'Stick Coordinates & Motion Trail',
    '左摇杆 (LS)': 'Left Stick (LS)',
    '右摇杆 (RS)': 'Right Stick (RS)',
    '摇杆回中与死区分析': 'Stick Centering & Deadzone Analysis',
    '● 回中良好': '● Centering Optimal',
    '硬件安全死区阈值: 12% 刻度参考': 'Hardware Safety Deadzone: 12% Reference',
    '按键响应与动力反馈': 'Button Response & Actuator Feedback',
    'TACTILE MATRIX // 全键位物理矩阵': 'TACTILE MATRIX // Full Button Matrix',
    '实时响应:': 'Live Input:',
    '等待按键操作...': 'Waiting for button press...',
    '线性扳机': 'Linear Triggers',
    '霍尔 /压感行程深度': 'Hall Effect / Pressure Depth',
    '霍尔 / 压感行程深度': 'Hall Effect / Pressure Depth',
    '触觉马达': 'Haptic Actuators',
    '双声道触觉脉冲发生器': 'Dual-channel haptic pulse generator',
    '重震': 'Heavy',
    '轻震': 'Light',
    '爆发': 'Burst',
    '脉冲': 'Pulse',
    '重度触觉脉冲 (85%)': 'Heavy tactile pulse (85%)',
    '轻度触觉脉冲 (25%)': 'Light tactile pulse (25%)',
    '爆发脉冲 (70% 双段)': 'Burst pulse (70% dual-stage)',
    '连续脉冲 (40% 三段)': 'Continuous pulse (40% triple-stage)',
    '双马达独立频段 · 就绪': 'Dual-motor independent channels · Ready',

    # Settings Page
    '01 // CAPTURE ENGINE · 截图服务与存储': '01 // CAPTURE ENGINE · Screen Capture & Storage',
    '当前活动显示器': 'Active Display',
    '当前活动窗口': 'Active Window',
    '全部连接显示器': 'All Connected Displays',
    '捕获目标范围': 'Capture Target Scope',
    '设定活动显示器或独立活动窗口': 'Set active display or independent focused window',
    '截图存储路径': 'Captures Storage Path',
    '更改目录...': 'Change Folder...',
    '防抖冷却间隔': 'Debounce Cooldown',
    '连击防误触时间阈值': 'Threshold to prevent accidental double-tap',
    '机械快门音效反馈': 'Mechanical Shutter Sound',
    '截图成功时通过系统播放清脆的高保真相机机械快门声': 'Play crisp high-fidelity camera shutter sound on successful capture',
    '掌心触觉脉冲反馈': 'Haptic Shutter Pulse',
    '截图成功瞬间手柄给予 60ms 两段式物理快门轻触确认': 'Trigger 60ms two-stage physical shutter tap upon capture',
    '屏蔽 Windows 截图与 Game Bar 弹窗': 'Suppress Windows Game Bar & Screenshot Popups',
    '关闭 Windows 的手柄游戏栏与游戏录制响应': 'Disable Windows controller Game Bar and game capture responses',
    '屏蔽系统截图弹窗': 'Suppress Windows Screenshot Popups',
    '已开启 Windows 截图与 Game Bar 屏蔽，手柄按键已释放': 'Windows Game Bar & screenshot suppression enabled. Controller buttons released',
    '已恢复 Windows 默认截图键与 Game Bar 响应': 'Restored Windows default screenshot key & Game Bar response',
    '系统截图屏蔽设置失败：': 'System screenshot shield setting failed: ',

    '02 // HAPTICS & ILLUMINATION · 硬件交互与反馈': '02 // HAPTICS & ILLUMINATION · Haptics & Feedback',
    'LED 状态光条': 'LED Status Lightbar',
    '手柄呼吸光条发光色调': 'Controller breathing lightbar accent color',
    '双马达振动强度': 'Dual-Motor Vibration Strength',
    '触觉反馈马达输出力度': 'Output intensity of haptic rumble motors',
    '触摸板手势扩展': 'Touchpad Gesture Extension',
    '双指轻扫模拟 Windows 鼠标指针': 'Two-finger swipe emulates Windows mouse cursor',
    '触觉拟真引擎 (Haptic Engine)': 'Haptic Synthesizer Engine',
    '微秒级双音圈多段触觉波形合成 (快门/棘轮/冲击/心跳)': 'Microsecond dual voice-coil waveform synthesis (Shutter/Impact/Heartbeat)',
    '快门触觉': 'Shutter Pulse',
    '冲击阻尼': 'Heavy Impact',
    '心跳律动': 'Heartbeat Rhythm',

    '03 // GESTURES & AUTOMATION · 手势与高级设置': '03 // GESTURES & AUTOMATION · Gestures & Advanced',
    '长按手势识别阈值': 'Long Press Threshold',
    '按住按键达到设定时长触发二次宏动作': 'Hold button to trigger secondary macro action',
    '使用指南与硬件支持': 'User Guide & Hardware Support',
    '查阅全型号支持与高级特性说明': 'View controller compatibility and advanced features',
    '查阅指南 ›': 'View Guide ›',

    '04 // RUNTIME & DAEMON · 系统服务与开机启动': '04 // RUNTIME & DAEMON · Runtime & Background Daemon',
    '常驻映射监听进程 (Agent)': 'Resident Mapping Daemon (Agent)',
    '后台超低延迟按键拦截与手势守护服务': 'Ultra-low latency button interceptor and gesture guardian',
    '停止服务': 'Stop Service',
    '启动服务': 'Start Service',
    '停止后台': 'Stop Daemon',
    '启动后台': 'Start Daemon',
    '系统开机自动启动': 'Launch on System Startup',
    'Windows 登录后在后台安静自启运行': 'Run silently in background upon Windows logon',

    '05 // 4K INSTANT REPLAY BUFFER · HEVC / AV1 标杆极清即时回放': '05 // 4K INSTANT REPLAY BUFFER · 4K Hardware Replay Buffer',
    '4K 极清回放缓存': '4K Instant Replay Buffer',
    '开启后长按 Create 键保存本地极清 MP4（若关闭则联动系统 Game Bar）': 'Long press Create to save local high-bitrate MP4 (falls back to Game Bar if disabled)',
    '最大回看时间 (滑动窗口)': 'Max Replay Window (Rolling)',
    '常驻内存/磁盘环形缓冲区保留的最长历史片段': 'Longest historical clip buffered in rolling memory/disk',
    '硬件编码器与画质方案': 'Hardware Encoder & Quality',
    '选择显卡硬件加速格式与码率': 'Select GPU hardware encoding codec and bitrate',
    'HEVC 标杆极清 (推荐 · 50Mbps)': 'HEVC Ultra (Recommended · 50Mbps)',
    'AV1 次世代极清 (AMF/NVENC · 45Mbps)': 'AV1 Next-Gen (AMF/NVENC · 45Mbps)',
    'H.264 兼容模式 (60Mbps)': 'H.264 Universal (60Mbps)',
    '正在探测硬件加速状态...': 'Detecting hardware acceleration...',
    '🟢 [已启用·实时录制中]': '🟢 [Active · Recording in progress]',
    '⚪ [未启用·回退系统 Game Bar]': '⚪ [Disabled · Fallback to Game Bar]',
    '立即保存当前回放': 'Save Current Replay Now',

    # Language Settings Row
    '界面语言 / Language': 'Language / 界面语言',
    '界面语言与国际化设置': 'Select interface display language',
    '跟随系统 (System Default)': 'System Default (跟随系统)',
    '简体中文 (Simplified Chinese)': '简体中文 (Simplified Chinese)',
    'English (US)': 'English (US)',

    # Controller Catalog & Gallery
    '全部手柄': 'All Controllers',
    '我的收藏': 'Favorites',
    '搜索手柄型号...': 'Search controller model...',
    '重新扫描设备': 'Rescan Devices',
    '管理': 'Manage',
    '切换到此手柄': 'Switch to this',
    '未识别，尝试 XInput 模式': 'Unrecognized, try XInput mode',
    '进入配置 ': 'Configure ',
    '8BitDo · 示例': '8BitDo · Example',
    '未找到匹配的手柄设备': 'No matching controller found',
    '官方产品页': 'Official Product Page',
    '通用手柄 / 外设': 'Universal Gamepad / Peripherals',
    '第三方 · 8BitDo 图片示例': 'Third-party · 8BitDo Visual Example',

    # Button Names
    '×  交叉': '×  Cross',
    '○  圆圈': '○  Circle',
    '□  方块': '□  Square',
    '△  三角': '△  Triangle',
    '方向键 ↑': 'D-Pad ↑',
    '方向键 ↓': 'D-Pad ↓',
    '方向键 ←': 'D-Pad ←',
    '方向键 →': 'D-Pad →',
    '左摇杆按下': 'Left Stick Click (LS)',
    '右摇杆按下': 'Right Stick Click (RS)',
    '麦克风': 'Mute / Mic',
    '辅助键': 'Auxiliary',
    '背键 P1': 'Paddle P1',
    '背键 P2': 'Paddle P2',
    '背键 P3': 'Paddle P3',
    '背键 P4': 'Paddle P4',
    '按键 ': 'Button ',
    '左摇杆 X': 'Left Stick X',
    '左摇杆 Y': 'Left Stick Y',
    '右摇杆 X': 'Right Stick X',
    '右摇杆 Y': 'Right Stick Y',

    # Virtual KBM
    '全盘虚拟键鼠接管': 'Virtual KBM Hijack Engine',
    '300Hz 动力学转镜 · 零延迟宏 · 硬件级独占隐身': '300Hz Dynamics · Zero-latency Macros · Hardware Cloaking',
    '当前映射方案：': 'Active Scheme:',
    '新建方案': 'New Scheme',
    '重置方案': 'Reset Scheme',
    '删除方案': 'Delete Scheme',
    '总开关': 'Master Toggle',
    '点击键帽进入捕获监听，按下手柄按键即刻绑定；右键键帽可直接解绑': 'Click keycap to start capture & press gamepad button to bind; right-click keycap to unbind',
    '虚拟键盘与按键映射 // KEYBOARD MATRIX': 'KEYBOARD MATRIX // Virtual Keyboard Mapping',
    '标准布局虚拟键盘 // VIRTUAL KEYBOARD': '108-KEY FULL MATRIX // Virtual Keyboard Workstation',
    '108 键全键盘物理矩阵 // 108-KEY FULL MATRIX': '108-KEY FULL MATRIX // Full Keyboard Workstation',
    '🎯 点击下方任意虚拟键帽，即可捕获手柄/飞行摇杆操作并绑定为触发': '🎯 Click any keycap below to capture controller/flight stick input & bind as trigger',
    '🎯 正在为虚拟按键【{name}】捕获外设触发操作...': '🎯 Capturing peripheral trigger for key [{name}]...',
    '👉 请在手柄上按下按键/扳机/组合键（松开所有按键即可完成绑定），或点击右侧手动点选': '👉 Press button/trigger/chord on gamepad (release all to bind), or pick manually on the right',
    '✅ 成功绑定：外设【{trigger}】 -> 虚拟按键【{key}】': '✅ Successfully bound: Gamepad [{trigger}] -> Virtual Key [{key}]',
    '映射已即时生效并在后台驻留。点击其他按键可继续配置。': 'Mapping active in background. Click other keys to continue configuring.',
    '🎮 手动选择外设触发': '🎮 Select Trigger Manually',
    '✕ 取消捕获': '✕ Cancel Capture',
    '🗑️ 清除该键绑定': '🗑️ Clear Binding',
    '虚拟鼠标与核心动作 // MOUSE & ACTIONS': 'MOUSE & ACTIONS // Virtual Mouse & Core Actions',
    '虚拟鼠标与动作 // MOUSE & ACTIONS': 'MOUSE & ACTIONS // Virtual Mouse & Shortcuts',
    '视角动力学与硬件隐身 // DYNAMICS & CLOAKING': 'DYNAMICS & CLOAKING // 300Hz & Hardware Cloaking',
    '右摇杆灵敏度:': 'Stick Sensitivity:',
    '右摇杆灵敏度：': 'Stick Sensitivity: ',
    '静音 (Mute)': 'Mute',
    '音量- (Vol -)': 'Volume -',
    '音量+ (Vol +)': 'Volume +',
    '计算器 (Calc)': 'Calculator',
    '数字键盘锁定 (NumLock)': 'NumLock',
    '小键盘回车 (Num Enter)': 'Num Enter',
    '小键盘加号 (Num +)': 'Num +',
    '小键盘减号 (Num -)': 'Num -',
    '小键盘乘号 (Num *)': 'Num *',
    '小键盘除号 (Num /)': 'Num /',
    '小键盘点 (Num .)': 'Num .',
    '小键盘 0': 'Num 0',
    '小键盘 1': 'Num 1',
    '小键盘 2': 'Num 2',
    '小键盘 3': 'Num 3',
    '小键盘 4': 'Num 4',
    '小键盘 5': 'Num 5',
    '小键盘 6': 'Num 6',
    '小键盘 7': 'Num 7',
    '小键盘 8': 'Num 8',
    '小键盘 9': 'Num 9',
    '🔇': '🔇',
    '🔉': '🔉',
    '🔊': '🔊',
    '🧮': '🧮',
    'Num': 'Num',
    '↵': '↵',
    '+': '+',
    '-': '-',
    '*': '*',
    '/': '/',
    '.': '.',
    '0': '0',
    '1': '1',
    '2': '2',
    '3': '3',
    '4': '4',
    '5': '5',
    '6': '6',
    '7': '7',
    '8': '8',
    '9': '9',
    '0 Ins': '0 Ins',
    '. Del': '. Del',
    '7 Home': '7 Home',
    '8 ↑': '8 ↑',
    '9 PgUp': '9 PgUp',
    '4 ←': '4 ←',
    '6 →': '6 →',
    '1 End': '1 End',
    '2 ↓': '2 ↓',
    '3 PgDn': '3 PgDn',
    '🖱️ 左键': '🖱️ Left',
    '🖱️ 右键': '🖱️ Right',
    '🖱️ 中键': '🖱️ Middle',
    '🔼 滚轮上': '🔼 Wheel Up',
    '🔽 滚轮下': '🔽 Wheel Down',
    '📸 快捷截屏': '📸 Fast Capture',
    '🎬 精彩回放': '🎬 Replay',
    '🎥 录屏开关': '🎥 Record',
    '鼠标左键': 'Mouse Left',
    '鼠标右键': 'Mouse Right',
    '鼠标中键': 'Mouse Middle',
    '滚轮向上': 'Scroll Up',
    '滚轮向下': 'Scroll Down',
    '快速换装': 'Quick Wardrobe',
    '全景地图': 'World Map',
    '相机拍照': 'Photo Camera',
    '任务追踪': 'Quest Tracker',
    '共鸣抽卡': 'Resonance',
    '天赋树': 'Talent Tree',
    '设计图': 'Crafting',
    '活动面板': 'Events',
    '300Hz 物理动力学视角': '300Hz Mouse Dynamics',
    'winmm 1ms · 二阶阻尼质点 · 亚像素扩散': 'winmm 1ms · 2nd-order Damped Particle · Subpixel Diffusion',
    '右摇杆视角灵敏度：': 'Right Stick Sensitivity:',
    '硬件独占隐身 // HARDWARE CLOAKING (防游戏双重输入)': 'HARDWARE CLOAKING // Exclusive Mode (Prevent Double Inputs)',
    '启用微软 WHQL 认证的 HidHide 驱动向系统与游戏隐藏物理手柄，仅输出纯净虚拟键鼠，彻底杜绝双重动作与 UI 狂闪': 'Hide physical controller from Windows & games via WHQL HidHide driver, emitting pure virtual KBM to eliminate double inputs & UI jitter',
    '检测中...': 'Detecting...',
    '⚡ 驱动未安装 (可选)': '⚡ Driver Not Installed (Optional)',
    '✓ 硬件已隐身 (独占接管中)': '✓ Hardware Cloaked (Exclusive Mode)',
    '○ 共享模式 (未隐身)': '○ Shared Mode (Not Cloaked)',
    '📥 安装 HidHide 驱动 (官方 WHQL)': '📥 Install HidHide Driver (Official WHQL)',
    '🛡️ 提权重启 (穿透反作弊)': '🛡️ Relaunch as Admin (Bypass Anti-Cheat)',
    '开启独占屏蔽': 'Enable Exclusive Cloaking',
    '新建映射方案': 'New Mapping Scheme',
    '方案名称:': 'Scheme Name:',
    '名称已存在': 'Name Already Exists',
    '请使用唯一的方案名称。': 'Please use a unique scheme name.',
    '确定重置方案': 'Confirm Reset Scheme',
    '确定要将当前方案重置为默认值吗？': 'Are you sure you want to reset this scheme to defaults?',
    '确定删除方案': 'Confirm Delete Scheme',
    '提权启动失败': 'Admin Relaunch Failed',
    '开启硬件独占隐身失败': 'Enable Hardware Cloaking Failed',
    '当前未检测到已连接的控制器，请先连接手柄后再开启独占屏蔽。': 'No connected controller detected. Please connect gamepad before enabling exclusive cloaking.',
    '安装 HidHide 驱动 (官方 WHQL 认证)': 'Install HidHide Driver (Official WHQL Certified)',
    '解除映射': 'Unbind Key',
    '需要先安装 HidHide 内核驱动后方可开启独占屏蔽': 'HidHide kernel driver must be installed before enabling exclusive cloaking',
    '物理控制器已对系统与游戏完全隐藏，仅本程序能接收硬件信号并输出虚拟键鼠': 'Physical controller is hidden from Windows and games; only this app receives hardware signals and outputs virtual KBM',
    '物理控制器正常向系统广播输入，可能在同时支持手柄的游戏中产生双重输入': 'Physical controller broadcasts input normally to Windows, which may cause double inputs in games with native controller support',
    '当前处于普通权限。若 3D 游戏反作弊拦截了视角转镜或键位，点击此按钮可一键以管理员权限重启工作台': 'Running with standard privileges. If game anti-cheat blocks camera tracking or keys, click to restart as administrator',

    # Presets
    '主机体验': 'Console Standard',
    '桌面导航': 'Desktop Navigation',
    '3D 动作通用预设': '3D Action Preset',

    # General UI & Units
    '启用': 'Enable',
    '已启用': 'Enabled',
    '已禁用': 'Disabled',
    '秒': 's',
    '分钟': 'min',
    '张': 'photos',
    '程序 (*.exe);;所有文件 (*)': 'Applications (*.exe);;All Files (*)',
    '选择程序': 'Select Application',
    '请选择存在的可执行文件': 'Please select an existing executable',
    '检查映射': 'Check Mapping',
    '界面语言已更新，部分设置重启后生效': 'UI language updated. Restart recommended for full effect.',
    '映射已保存': 'Mapping saved',
    '已恢复默认映射': 'Restored default mapping',
    '另存为新预设': 'Save as New Profile',
    '当前设备至少需要保留一个配置预设（当前为“{name}”）。\n\n如需重置按键设定，请点击“恢复默认配置”；如需建立新配置，请点击“另存为新预设”。': 'At least one profile must be retained for the current device (active: "{name}").\n\nTo reset bindings, click "Restore Defaults"; to create a new profile, click "Save as New Profile".',
    '确定要永久删除配置预设“{name}”吗？\n删除后不可恢复。': 'Are you sure you want to permanently delete profile preset "{name}"?\nThis cannot be undone.',
    '已删除配置预设：': 'Deleted profile preset: ',
    '已切换配置：': 'Switched to profile: ',
    '截图已保存': 'Screenshot saved',
    '精彩瞬间已保存': 'Instant Replay Clip Saved',
    '截图已删除': 'Screenshot deleted',
    '登录启动已开启': 'Launch on startup enabled',
    '登录启动已关闭': 'Launch on startup disabled',
    '设置失败：': 'Setting failed: ',
    '后台启动中': 'Starting background daemon...',
    '已触发系统回放录制 (Win+Alt+G)': 'Triggered system replay recording (Win+Alt+G)',
    '已发送 350 ms 振动测试': 'Sent 350ms rumble test pulse',
    '当前设备暂不支持振动或尚未连接': 'Device does not support rumble or is disconnected',
    '灯条颜色已更新': 'Lightbar color updated',
    '当前设备暂不支持灯条控制或尚未连接': 'Device does not support lightbar control or is disconnected',
    '已连接 ': 'Connected ',
    '手柄已断开，等待重新连接': 'Gamepad disconnected, waiting to reconnect',
    '按下 ': 'Pressed ',
    '映射已恢复': 'Mapping resumed',
    '映射已暂停 · 设备监测继续运行': 'Mapping paused · Device monitoring active',
    '映射已暂停：': 'Mapping paused: ',
    '请先启动后台映射': 'Please start background daemon first',
    '截图失败：': 'Capture failed: ',
    '无法读取截图目录：': 'Cannot read captures folder: ',
    '收藏失败：': 'Failed to favorite capture: ',
    '删除失败：': 'Failed to delete: ',
    '快门触觉微脉冲': 'Shutter Tactile Micro-pulse',
    '重度撞击阻尼': 'Heavy Impact Damping',
    '心跳仿真律动': 'Heartbeat Simulation Rhythm',
    '已触发触觉波形：': 'Triggered haptic waveform: ',
    '确定要永久删除截图 "{title}" 吗？\n文件：{name}': 'Are you sure you want to permanently delete capture "{title}"?\nFile: {name}',
    '确定要清理所有未收藏的截图吗？\n将永久删除 {count} 张截图，已收藏的 {retained} 张截图将被保留。': 'Are you sure you want to clean all unfavorited captures?\n{count} captures will be permanently deleted, and {retained} favorited captures will be kept.',
    '已清理 {count} 张未收藏截图': 'Cleaned {count} unfavorited captures',
    'GamePad Studio 手柄管理软件': 'GamePad Studio Controller Manager',
    'UI 界面语言选择 (auto/zh/en)': 'UI language preference (auto/zh/en)',

    # Section Eyebrows & Subtitles
    '缓冲与最近保存': 'Buffer & Recents',
    '当前按键': 'Active Input',
    '截图服务与存储': 'Screen Capture & Storage',
    '硬件交互与反馈': 'Haptics & Illumination',
    '手势与高级设置': 'Gestures & Advanced',
    '系统服务与开机启动': 'Runtime & Daemon',
    'HEVC / AV1 标杆极清即时回放': 'HEVC / AV1 Hardware Instant Replay',

    # Input Tester & Telemetry
    '  OFFLINE · 未连接  ': '  OFFLINE · Disconnected  ',
    '  ONLINE · 已连接  ': '  ONLINE · Connected  ',
    '未连接手柄': 'No Controller Connected',
    '等待手柄接入...': 'Waiting for controller...',
    '● 离线': '● Offline',
    '偏移: —': 'Offset: —',
    '偏移: 0.0%  (X +0.000, Y +0.000)': 'Offset: 0.0%  (X +0.000, Y +0.000)',
    '偏移:': 'Offset:',
    '未连接马达': 'Motors Disconnected',
    '双马达独立频段 · 支持触觉': 'Dual-motor independent · Haptics ready',
    '当前设备不支持震动': 'Device does not support rumble',
    '● 异常偏移': '● Abnormal Drift',
    '游戏控制器': 'Game Controller',
    'DualSense 控制器': 'DualSense Controller',
    '按下: ': 'Pressed: ',
    '完整覆盖 36 个方向后显示最大半径相对单位圆的平均绝对径向误差。': 'Displays average absolute radial error against unit circle after completing 36 radial sectors.',

    # Virtual KBM Toolbar & Controls
    '当前键鼠方案：': 'Active Scheme:',
    '克隆方案': 'Clone Scheme',
    '恢复预设': 'Restore Preset',
    '完全接管为虚拟键鼠': 'Hijack as Virtual KBM',
    '🎯 点击下方任意虚拟键帽，即可捕获手柄/飞行摇杆操作并绑定为触发': '🎯 Click any keycap below to capture controller/flight stick input & bind as trigger',
    '支持设备：DualSense / Xbox / Switch / ECHO 飞行手柄 / HOTAS 飞行摇杆 / 赛车踏板': 'Supported Devices: DualSense / Xbox / Switch / Flight Sticks / Racing Pedals',
    '💡 左键点击捕获 · 右键快速解绑 · 发光徽章表示已绑定外设触发': '💡 Left-click to capture · Right-click to unbind · Glowing badges indicate bound triggers',
    '🎮 手动选择外设触发': '🎮 Select Trigger Manually',
    '✕ 取消捕获': '✕ Cancel Capture',
    '🗑️ 清除该键绑定': '🗑️ Clear Binding',
    '⏳ 捕获中': '⏳ Capturing',
    '标准布局虚拟键盘 // VIRTUAL KEYBOARD': 'VIRTUAL KEYBOARD // Standard ANSI Matrix',
    '虚拟鼠标与核心动作 // MOUSE & ACTIONS': 'MOUSE & ACTIONS // Virtual Mouse & Core Actions',
    '🖱️ 鼠标左键': '🖱️ Mouse Left',
    '🖱️ 鼠标右键': '🖱️ Mouse Right',
    '🖱️ 鼠标中键': '🖱️ Mouse Middle',
    '🔼 滚轮向上': '🔼 Scroll Up',
    '🔽 滚轮向下': '🔽 Scroll Down',
    '📸 快速截屏': '📸 Take Screenshot',
    '🎬 精彩回放': '🎬 Instant Replay',
    '🎥 录屏开关': '🎥 Screen Record',
    'Space (空格)': 'Space',
    '《无限暖暖》专属预设': '3D Action Preset',
}


def tr(text: str, **kwargs) -> str:
    """Translate a text string according to the active language."""
    if _CURRENT_LANG == 'en':
        translated = TRANSLATIONS.get(text, text)
    else:
        # In Chinese mode, if passed text is in English, check reverse lookup
        translated = text
        if text not in TRANSLATIONS and text in TRANSLATIONS.values():
            for k, v in TRANSLATIONS.items():
                if v == text:
                    translated = k
                    break

    if kwargs:
        try:
            return translated.format(**kwargs)
        except Exception:
            return translated
    return translated


def tr_button(btn: str) -> str:
    """Translate button label."""
    if _CURRENT_LANG == 'en':
        return TRANSLATIONS.get(btn, btn)
    return btn


def tr_profile(name: str) -> str:
    """Translate built-in profile name for display."""
    if _CURRENT_LANG == 'en':
        if name == '主机体验':
            return 'Console Standard'
        elif name == '桌面导航':
            return 'Desktop Navigation'
        elif '3D 动作' in name or '键鼠全盘接管' in name or '无限暖暖' in name:
            return '3D Action Preset'
        return TRANSLATIONS.get(name, name)
    return name

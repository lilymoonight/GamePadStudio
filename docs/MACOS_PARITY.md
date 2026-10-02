# Windows / macOS 用户功能对齐

日期：2026-10-02。比较依据是当前源码中的实际行为，平台可以使用不同 API，但不能只保留同名按钮就算完成。下表的“已实现”表示代码路径存在；“待实机”表示还缺真实设备、目标游戏或系统交互结果。本轮 Mac 离线全量回归为 2,482 项：2,481 通过、1 项 Windows 专属进程标志测试跳过。

Windows 与 Mac 共用映射引擎、设备配置和界面；系统输入、进程、窗口、捕获和登录启动在边界选择平台后端。开发分支用于集中适配，后续合入共同主线，避免长期分裂两套配置和业务逻辑。[启动与权限步骤](MACOS_SUPPORT.md)

## 功能矩阵

| 用户功能 | 当前 Windows 行为 | Mac 实现与状态 | 验证／剩余差异 |
| --- | --- | --- | --- |
| 设备扫描、选择、原始按键／摇杆／扳机 | SDL 控制器或原始 joystick；按设备实际能力显示 | **已实现**：使用 pygame 同一份 SDL，保留同一设备和映射状态 | 蓝牙 DS5 曾读取到名称、17 个可用按键、6 轴、双指触摸能力与 FULL 电量档；实际操作验收仍待连接设备 |
| 设备身份、预设、导入导出、反转／响应曲线 | 按物理设备和预设保存，导入不自动激活 | **共用实现** | 旧 Windows 预设中 Mac 不支持的键保留原配置、仅跳过该输出并在状态／活动记录说明原因；其他映射继续运行 |
| 短按／长按、组合层、键鼠宏、切换保持 | 共用来源状态机及输出持有计数 | **共用实现**，输出边界替换为 Mac 原生事件 | 键盘与鼠标“按一次保持、再按一次取消”已通过模拟回归，暂停、断线和换预设会释放；真实目标游戏接受事件仍待实机 |
| 键鼠按住连发 | 当前仅有普通保持／点击；手柄目标的 `gamepad_turbo` 未提供真实虚拟手柄输出 | **尚未实现** | 需要独立设计重复时钟、输出占用与停止规则；不能把 `gamepad_turbo` 算作键鼠连发 |
| 普通键、修饰键、方向／导航键、数字区、F1–F20 | Win32 `SendInput` | **已实现**：CoreGraphics 硬件键码 | 按当前键盘布局产生字符；`Ctrl` 保持 Control，`Win`／`Cmd` 是 Command，`Alt` 是 Option；不自动改写所有 Ctrl 绑定 |
| CapsLock | Windows 锁定键事件 | **已实现**：IOKit 锁定状态加 CoreGraphics CapsLock 事件 | 新按下切换锁定，释放／暂停只释放事件，不撤销锁定；模拟和未投递原生对象 ABI 验证通过，尚未真实切换用户锁定状态 |
| Menu、音量增减、静音、播放／暂停 | VK_APPS 与 Windows 媒体键 | **已实现**：Menu 硬件码、AppKit systemDefined 媒体事件 | 实际菜单及媒体应用响应待实机；只声称代码中列出的媒体动作 |
| Insert、Pause、ScrollLock、NumLock、PrintScreen、F21–F24 | 按对应 Windows VK 发送 | **缺口明确**：当前没有保持原作用的通用 Mac 合成输出；界面与保存校验给原因 | Help 不等于 Insert，Clear 不等于 NumLock；AppKit F21–F24 逻辑字符不能证明游戏接收通用硬件事件。用户明确改绑应用快捷键或项目截图动作属于替代方案 |
| Calc／Calculator | **既有缺陷**：`actions.KEYS` 把 Calc 与 F24 都编码为 `0x87`，实际不是计算器启动 | **同一缺陷明确拒绝**：不能从旧值推断用户要启动计算器 | 需独立动作及配置迁移，不能把 Windows 当前按钮当作已完成基线。[Microsoft VK 表](https://learn.microsoft.com/en-us/windows/win32/inputdev/virtual-key-codes) |
| 鼠标左／右／中键、拖拽、滚轮、指针与镜头 | `SendInput`；共用镜头物理模型 | **已实现**：CoreGraphics，拖拽带正确鼠标事件类型和相对位移字段 | 需要辅助功能权限；取消路径和分配失败通过模拟；双屏内接缝不回中且外缘回中已用负坐标布局模拟；镜头手感、游戏 Raw Input 接受情况与延迟待实机 |
| 触摸板手势、双指滚动、指针 | SDL 触摸读取与共用手势状态机 | **共用实现** | 仅在 SDL 报告能力时显示；不能据双指能力报告声称已完成实际滑动验收 |
| 普通震动、扳机震动、灯光、低电量提醒 | SDL 能力检测及共用设备／曲线逻辑 | **共用实现** | 能力依设备、连接与 SDL；普通震动不是完整音频触觉波形，扳机震动不是 DualSense 自适应扳机。当前没有真实输出验收 |
| 设备隐身，避免物理手柄和映射双重输入 | 所选设备 HidHide 驱动隐藏和应用白名单 | **已实现替代路径，待实机**：所选原生 HID 的 IOKit 独占访问 | 不安装 HidHide；仅 SDL 2.28.4 已知 HIDAPI 路径，身份、实例、CF 类型和 usage 核对，拒绝键鼠复合／GCSyntheticDevice。其他版本／后端明确不可用 |
| PS／Home 系统弹窗屏蔽 | Windows 设置及现有 Game Bar 屏蔽路径 | **原生隔离接入中，待实机**：使用所选手柄独占访问抑制原始设备事件 | 该替代会阻止游戏直接读此物理手柄，不能只屏蔽单个系统按键；并非全局更改 macOS 弹窗设置。USB／蓝牙及 GameController 路径需要实际 PS／Home 验收 |
| 游戏／前台应用关联 | 完整 `.exe` 路径选择当前设备预设 | **已实现**：NSWorkspace 完整执行路径；`.app` 解析实际主程序 | 不按猜测名字匹配；手动覆盖、编辑保护、暂停、设备归属和释放语义共用 |
| 游戏／前台窗口与源进程识别 | 前台窗口、进程路径与大型可见窗口回退 | **已实现**：NSWorkspace 前台身份、CoreGraphics 窗口列表、进程启动时间与路径 | 排除本程序 GUI／后台 PID 和系统窗口；保留第三方 Python 应用。前后身份复验，拒绝 PID／窗口 ID 复用；实际用户窗口未由 CLI 读取 |
| 截图 `game` / `window` / `all` / `monitor_N` | 游戏所在屏幕、窗口、所有屏幕、指定屏幕 | **已实现**：ScreenCaptureKit 与 SCScreenshotManager | `window` 精确过滤已验证窗口；`game` 跟随最大重叠显示器；指定屏断开失败。Retina 逻辑点／物理像素分离，负坐标和混合缩放拼接；通过模拟及合成图片，真实游戏待验收 |
| 物理键盘快速截图 | 后台监听 PrintScreen，保存到项目图库 | **已实现 Mac 替代入口**：默认 `Ctrl+Alt+Shift+S`，可在设置中修改或关闭 | Carbon 全局热键按当前截图范围保存；与紧急暂停热键冲突时拒绝，系统截图组合键不会被占用。原生注册／分发／注销由模拟验证，实际按键及屏幕权限待验收；不改变旧映射中 `PrintScreen` 的含义 |
| 截图／录像历史、元数据、收藏、删除、快门反馈 | 共同本地捕获列表与设备反馈 | **共用实现**；快门声音使用 `afplay` | PNG 与 MP4 取实际像素尺寸；整段录像生成 JPEG 封面、实际时长与音轨元数据，未完成 MP4 不进入图库；真实设备反馈待验收 |
| 内存回放、时间轴、HEVC／H.264／AV1 选择 | 带逐帧 PTS 的 Matroska 输入，内存 MPEG-TS 缓冲及 MP4 保存；GPU 候选实际探测 | **已实现**：SCK 帧时间戳，实际探测 VideoToolbox 后软件回退 | Mac 需提前启用回放缓存并等待画面积累，不能补录启用前的画面；HEVC／H.264 合成编码解码验证通过，AV1 使用软件编码且界面明确标示。FPS 是目标，不能保证 4K 达到设置帧率 |
| 开始／结束整段录像 | 委托 Windows Game Bar 录屏开关 | **已通过离线集成，待实机**：项目内 ManualRecording 服务，独立开始／结束、MP4 完成后反馈 | `game` 与回放缓存一致，录游戏所在显示器；显式 `window` 锁定原窗口身份。暂停／退出／后台通信与生成成片已用合成源测试；真实游戏与长时间录像待验收 |
| 系统声音随录像保存 | Game Bar 的系统录音范围与设置由系统决定；项目内回放此前纯视频 | **已通过离线集成，待实机**：SCK 系统 PCM，Matroska 第二轨，同源音画 PTS，再编码 MP4 音轨 | AVWriter 已通过纯合成 FFmpeg 编码／解码、音轨／时长／同步验证；SCK 合成 CMSampleBuffer 转换验证通过。尚未真实采集系统声音；不是手柄麦克风或语音转写 |
| 多屏／Retina／刷新率 | Windows 物理像素及真实刷新率匹配；异刷新或未知多屏禁止合并录像 | **已实现**：CG points、CGDisplayMode 物理像素／刷新率与每屏 scale，按最大 scale 合成 | 单屏和同刷新率多屏支持；异刷新或未知多屏明确拒绝录制。混合 scale 不按全局固定 Retina 倍率推测 |
| HDR 内容转换与 SDR 导出 | DXGI FP16 scRGB，实际白点，浮点色调映射后 SDR BT.709；跨 HDR 屏限制 | **已实现受条件约束路径**：macOS 15+ Apple Silicon、SCK RGBA half、线性颜色附件确认、浮点色调映射 | EDR 当前 headroom 与潜在能力分开读取，不编造 Windows 式 SDR nits。合成 Float16 色块转换已验证；实际 HDR 游戏、外接 HDR 屏及跨屏同步待验收；两边都不是原始 HDR 视频导出 |
| 全局紧急暂停 | Windows 全局热键 | **已实现**：Carbon 全局注册，冲突明确失败，停止时注销 | 原生注册／冲突／注销已验证，实际快捷键触发待验收；无需为了该注册申请输入监控 |
| 登录启动与后台单实例 | Windows 登录启动和本地进程／IPC | **已实现**：用户 LaunchAgent、同 UID＋配置根目录稳定 IPC，POSIX 进程启停 | Finder／Terminal 新会话共享同配置后台；LaunchAgent 仅显式设置时写入。临时目录／模拟 launchctl 测试通过，没有安装真实用户登录项 |
| 权限、暂停、断线、退出、异常释放 | 共用持有状态及 Windows 输出释放 | **已实现**：辅助功能／屏幕录制预检，失败可见，平台异常路径清理 | 启动不自动请求授权；停止不能伪报已释放／已恢复。真实键鼠／手柄隔离状态恢复仍待设备与权限实机验证 |
| 真实系统虚拟手柄报告、手柄连发／手柄宏 | **尚未实现**：`gamepad_button`／`gamepad_turbo` 仅改内部集合或速率 | **尚未实现**：同一内存状态，未新增虚拟手柄驱动 | 两平台共同功能缺口，编辑器选项和内存状态不是系统或游戏看见虚拟手柄的证据 |
| 陀螺仪瞄准、自适应扳机、DS5 麦克风与语音助手 | 当前设备路径未实现这些完整用户流程 | **尚未完成**：DS5 麦克风有独立诊断，语音入口先做应用定位／草稿／明确原生启停 | 用户已选择暂不测试麦克风；不纳入本次既有 Windows 功能对齐的完成声明 |

## 隔离与特殊键的边界

IOKit 的 `IOHIDDeviceOpen` 提供 `kIOHIDOptionsTypeSeizeDevice` 独占选项，SDK 文档确认它可建立独占连接；本项目还需借用 SDL 已打开对象，因此严格限制已审查的 SDL 版本。当前实现先退出同对象共享访问，再独占打开；恢复失败保留 `restore_pending` 状态。接口成功与实际游戏／系统均不再收到输入是两种证据，最后一步仍需真实设备验证。[Apple IOHIDDeviceOpen](https://developer.apple.com/documentation/iokit/iohiddeviceopen(_:_:))、[SDL 2.28.4 Mac HIDAPI 源码](https://github.com/libsdl-org/SDL/blob/release-2.28.4/src/hidapi/mac/hid.c)

特殊键的缺口来自当前公开硬件事件路径及目标应用差异，不能用另一个硬件键或一次猜测的系统快捷键掩盖。项目可以提供独立的“截图”或“启动计算器”动作，但需明确的动作语义和旧配置迁移；它们不能使 `PrintScreen`／旧 `Calc=F24` 自动成为已对齐功能。CapsLock 和媒体键采用的公开 SDK 声明分别来自 `IOKit/hidsystem/IOHIDLib.h`、`IOHIDParameter.h`、`ev_keymap.h`，硬件键码来自 `HIToolbox/Events.h`；当前代码是可追溯的实现，不代表每个目标游戏都会接受事件。

## 捕获与音视频证据

Apple 的 SCScreenshotManager 和 ScreenCaptureKit 提供窗口／显示器过滤与帧采集；CGDisplayMode 的物理像素尺寸和 CGDisplayBounds 的逻辑点分别用于图像尺寸与窗口定位。SCK 的系统音频输出与麦克风是不同入口，本项目该录制路径只启用系统音频；捕获帧与音频首样本都使用 CMSampleBuffer 的源 PTS，不能改成回调到达时间。[屏幕捕获示例](https://developer.apple.com/documentation/screencapturekit/capturing-screen-content-in-macos)、[SCScreenshotManager](https://developer.apple.com/documentation/screencapturekit/scscreenshotmanager)、[capturesAudio](https://developer.apple.com/documentation/screencapturekit/scstreamconfiguration/capturesaudio)、[CGDisplayMode.pixelWidth](https://developer.apple.com/documentation/coregraphics/cgdisplaymode/pixelwidth)

`TimestampedAVWriter` 为 RGB24 和 PCM s16le 写入独立轨道，共用调用方的首视频时间原点。音频延迟送达不会被改写成最新视频 PTS，非法采样对齐、格式变化、倒退和重叠明确报错。合成视频标记与立体声音调经真实 FFmpeg 编码／解码保持同步；这能验证容器和编码链，不能代替真实 SCK 音频、长时间录像或外接多屏的测试。[Matroska 元素](https://www.matroska.org/technical/elements.html)、[PCM 编码规范](https://www.matroska.org/technical/codec_specs.html#a_pcmintlit)

HDR 可用性分成屏幕潜在 EDR 能力、当前 EDR headroom、OS／架构接口和实际缓冲证明；任何一层缺失不宣称 HDR。输出经过浮点色调映射到 SDR，Mac 视频对桌面显示内容使用 inverse BT.1886（gamma 2.4）保留显示亮度，并写入相符的 SDR 标签。真实屏幕颜色仍须逐场景比较。[Apple 当前 EDR headroom](https://developer.apple.com/documentation/appkit/nsscreen/maximumextendeddynamicrangecolorcomponentvalue)、[FFmpeg 色彩和像素格式](https://ffmpeg.org/ffmpeg-filters.html#zscale)、[zimg gamma 实现](https://github.com/sekrit-twc/zimg/blob/master/src/zimg/colorspace/gamma.cpp)

## 收尾验收

剩余关键检查是已授权情况下的真实 DS5 操作与键鼠释放、蓝牙／USB 独占及恢复、PS／Home 系统弹窗、目标游戏接受合成输入、真实窗口与多屏截图、包含系统声音的长时录像、HDR 游戏颜色与性能。用户已连接手柄但暂不在场，本轮未为了文档结论开启设备独占、发送桌面按键、录制屏幕／声音、安装登录项或修改系统权限。

验收使用 `.venv/bin/python scripts/test_modules.py --jobs 4`，109 个模块、2,481 项通过，唯一跳过项是 Windows 专属 FFmpeg 进程启动标志。合成 RGB／PCM 的 FFmpeg 封装与解码、原生 Swift 捕获组件的合成帧／音频、模拟窗口与设备以及离线界面均已验证；没有把它们当作真实桌面录制或 DS5 操作。`dist/GamePadStudio.app` 为本机 arm64 构建，内含 FFmpeg 与原生捕获组件，`codesign --verify --deep --strict` 通过，模拟设备的打包界面冒烟测试通过；签名为 ad hoc，公开分发仍需 Developer ID 签名与公证。Mac 测试中的 Windows API mock 只支持逻辑回归，Windows 原生输出、驱动与系统录屏仍需 Windows 机器复验。

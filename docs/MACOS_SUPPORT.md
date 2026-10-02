# macOS 适配预览

Windows 和 macOS 共用界面、配置、映射与手势引擎。系统输出通过 `create_actions()` 选择后端；Windows 保留 `WindowsActions`，Mac 使用 CoreGraphics、AppKit 与 IOKit。适配以原有用户作用为目标，允许平台使用不同系统接口；当前实现、已验证范围和剩余缺口见 [功能对齐矩阵](MACOS_PARITY.md)。macOS 版本仍需真实设备和游戏验证，不能宣称与 Windows 功能、延迟或兼容性完全一致。

## 从源码启动

使用 macOS 14+、Python 3.10+，本轮测试使用 Apple Silicon 和 Python 3.12。源码开发需要 Xcode 提供的 Swift 编译器及 SDK，先构建捕获组件；安装已打包的 `.app` 无需 Xcode。打开终端进入项目目录：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/build_macos_capture.py
.venv/bin/python main.py
```

环境建立后，也可以双击项目根目录的 `Start Studio.command`。默认关闭窗口会释放按键并停止后台；启用“关闭到托盘”后，关闭窗口才会保持后台映射。托盘菜单的完全退出会释放按键并停止后台。

macOS 配置保存在 `~/Library/Application Support/GamePadStudio`；可用 `--data-dir` 指定独立测试目录。Windows 的配置位置和运行方式保持原样。

## DS5 蓝牙验证

1. 在系统蓝牙设置中连接 DualSense。软件列出设备后再选择它。
2. 在硬件遥测页检查摇杆、扳机、面键、方向键和触摸板。电池、触摸和震动只根据实际 SDL 报告的能力显示。
3. 在设置页查看辅助功能权限，按“授权”后根据系统提示允许应用。源码运行时，权限可能归属于 Python 或启动它的终端；打包运行时归属于 GamePadStudio。
4. 在一个空白文本编辑窗口，先绑定单个字母，验证按下、释放、暂停与断线释放；再验证组合键和鼠标。没有授权时应报告权限缺失，不能假装输出成功。

`Ctrl` 在 Mac 上表示物理 Control，`Cmd` / `Command` / `Meta` / 旧配置的 `Win` 表示 Command，`Alt` 表示 Option。不会自动把所有旧预设的 Ctrl 换成 Cmd。Qt 在 Mac 上对 Control/Meta 的特殊映射在录入边界转换，保存的名称对应真实修饰键。

CapsLock 已有锁定状态切换，Menu 有对应硬件键码，音量增减、静音及播放／暂停使用系统媒体事件。按下、释放、暂停和退出沿用共用的输出持有与清理逻辑；这些路径通过模拟及原生接口检查，实际游戏和媒体应用仍需验证。

Insert、Pause、ScrollLock、NumLock、PrintScreen 和 F21–F24 尚无保持原作用的通用 Mac 输出路径，键帽与保存校验会给出具体原因。Help 不能代替 Insert，数字区 Clear 不能代替 NumLock；PrintScreen 可由用户明确改绑项目截图动作，但这会改变绑定类型。Mac 另有默认 `Ctrl+Alt+Shift+S` 的物理键盘全局截图入口，可在设置中修改或关闭，保存到同一图库；它不把旧预设里的 PrintScreen 输出偷偷转换为项目截图。现有 `Calc` 配置与 F24 共用 `0x87`，Windows 实际发送的是 F24，尚未实现独立计算器启动；Mac 不把该旧值猜成计算器。应用自定义快捷键只有经过用户明确配置才能作为替代，不能称为通用等效支持。

## 当前能力范围

| 功能 | macOS 预览行为 |
| --- | --- |
| 手柄读取、设备预设、映射、触摸手势 | 使用 pygame 同一份 SDL 动态库；实际能力依设备、连接与 SDL 支持而定 |
| 键盘、鼠标按键、滚轮与指针 | CoreGraphics 输出；CapsLock 与常用媒体键已有对应系统接口，需要辅助功能权限；特殊键缺口见上文 |
| 游戏／前台窗口识别 | NSWorkspace、CoreGraphics 与进程启动时间核对目标；排除本软件界面和后台，不把所有 Python 应用都当作工作台 |
| 截图 | ScreenCaptureKit；保留 `game`、`window`、`all`、`monitor_N` 范围，保存真实像素；需要屏幕录制权限 |
| 内存回放 | ScreenCaptureKit 逐帧时间戳、显示器跟随及同刷新率多屏；Mac 包附带 FFmpeg，实际探测 VideoToolbox，失败时软件回退 |
| 开始／结束整段录像 | 已加入项目内录像服务，替代 Windows Game Bar 的录屏开关；系统音轨与界面／后台已通过离线合成源测试，真实游戏待验收 |
| HDR 屏幕内容 | macOS 15+、Apple Silicon 且实际浮点缓冲与颜色附件确认后，转换为 SDR；没有原始 HDR 视频导出承诺 |
| 快门声音 | 使用 macOS afplay |
| 设备隐身／系统手柄弹窗屏蔽 | 使用所选物理手柄的 IOKit 独占访问替代；严格限定已验证 SDL 后端，真实游戏和 PS／Home 键效果待验收 |
| 全局紧急暂停 | Carbon 全局注册，后台支持；冲突会明确报错，不申请输入监控权限，功能默认关闭 |
| 登录自动启动 | 用户 LaunchAgent；仅在设置中明确启用时安装，使用当前应用和配置目录 |
| 应用自动关联 | NSWorkspace 读取前台应用完整执行路径；选择 `.app` 时解析真实主程序，规则默认关闭 |
| 桌面语音入口 | 设置页可独立检查已运行的 Codex／Antigravity、定位输入框及追加测试草稿；原生听写仅在明确启停控件验证后开放 |
| 虚拟手柄输出 | Windows 与 Mac 当前均只有内部状态，尚未生成系统虚拟手柄报告 |

截图和录制通过系统权限预检，启动不会自动弹出权限申请。设置页提供明确的授权入口；授权变更后可能需要重启应用。

截图的 `game` 范围选择游戏所在显示器，`window` 只捕获经身份复验的目标窗口，`all` 拼接全部屏幕，`monitor_N` 精确选择当前枚举的显示器。录像的 `game` 设置与回放缓存一致，均捕获游戏所在显示器；显式 `window` 模式才使用智能目标并锁定原窗口身份。当前录像范围选择器提供 `game`、`all` 和指定显示器，窗口录像模式尚未加入该选择器。`window` 目标消失或进程重启、指定显示器断开时明确失败。Retina 的逻辑点与物理像素分别保存，负坐标及混合缩放按显示器几何拼接；截图实际尺寸取捕获图像，不用逻辑窗口宽高冒充像素。窗口捕获、跟随与多屏路由已由模拟窗口和合成图片验证，尚未录制用户屏幕或真实游戏。

录制沿用独立的 `replay_capture_mode`。多屏实测刷新率相同才允许合并录制；不同或无法确认时提示选择单屏，不把未知刷新率当成 60 Hz。Mac HDR 路径要求 ScreenCaptureKit 实际返回 RGBA half 和线性颜色信息，高光压缩发生在浮点域；输出是 SDR PNG 或 SDR BT.709 视频。它不会把普通 8 位捕获标成 HDR。详见 [录制颜色与时间轴](RECORDING_COLOR_AND_TIMING.md)。

Mac 的设备隐身和系统手柄弹窗屏蔽使用所选 SDL 手柄已有的原生 HID 对象，显式开启后尝试 IOKit 独占访问；不是安装 HidHide，也不是修改系统所有游戏的弹窗设置。当前桥接严格限定 SDL 2.28.4 的原生 HIDAPI 路径，核对设备实例、厂商／产品和接口类型，拒绝键鼠组合接口与兼容层合成 HID。未知后端、独占失败或恢复失败会显示原因。关闭、换设备和退出会恢复共享访问；仍需 DS5 蓝牙、真实游戏及 PS／Home 按键确认独占后的实际作用。

全局暂停采用和 Windows 相同的配置格式，Mac 可使用 `Ctrl`、`Alt`、`Shift`、`Cmd` 组合；`Ctrl` 仍是物理 Control。启用前会检测快捷键注册是否成功，关闭后台时注销。Carbon 注册和应用前台元数据读取不需要输入监控授权；实际合成键鼠输出仍需要辅助功能权限。

登录启动安装的是当前用户的 `~/Library/LaunchAgents/com.gamepadstudio.agent.plist`，不是系统服务。源码模式会引用当前 Python 和项目目录，打包模式会引用 `.app` 内的程序；移动或删除它们后应重新设置。取消登录启动时，登录项管理的后台会先释放输入并退出；仍在使用工作台时会继续启动手动后台，已暂停的映射保持暂停。只有点击设置开关才会修改登录项，本轮验证没有安装真实用户登录项。

应用关联按完整路径匹配，支持 macOS `.app` 或实际可执行文件；Windows `.exe` 规则仍能保存在共享配置中。两平台共用代码与配置结构，系统特有能力按功能分别判断。建议 Mac 开发通过短期 `codex/macos-support` 分支合入同一主线，不长期维护两份平台分支。

## 语音入口：先验证应用，再接入手柄

产品方向是把手柄作为 Mac 工作流入口。用户确认的行为：按住指定手柄键说话，松开后将识别文字填入已运行的 Codex 或 Google Antigravity 输入框，再由用户手动发送。按键、目标应用与录音设备应可配置；目标未运行、录音失败或识别为空时明确提示，避免触发其他窗口。已有草稿、焦点改变、设备断线和暂停都需要独立处理，不能直接把回车当成结束录音。

当前按用户最新要求，先独立验证窗口定位、输入激活和原生语音激活，最后接入手柄触发与 DS5 收音。Codex 官方提供听写入口和 `Control+Shift+D` 快捷键，但原生听写读取的是应用选定的音频输入，不会自动接收本软件的 HID PCM。向第三方应用提供系统虚拟麦克风属于后续独立能力。[Codex 官方语音说明](https://developers.openai.com/codex/features/voice)、[快捷键](https://developers.openai.com/codex/reference/commands)。

设置页已加入独立的“语音入口”测试区，不需要手柄：

1. 选择 Codex 或 Google Antigravity，刷新运行状态。默认仅查进程，不启动应用、不切换窗口。
2. 点击“唤起并定位输入框”。只接收固定 bundle ID 的已运行进程，使用 PID 和启动时间检查进程代次，定位当前窗口中语义明确的聊天输入框；代码编辑器、搜索框、密码框及有歧义的窗口均拒绝。需要辅助功能权限。
3. 可在测试文字栏输入内容并点击“填入测试文字”。重新确认原进程、原窗口、焦点和草稿后在末尾追加，保留原有内容，不发送 Enter，不点击 Send。
4. 只有观察到唯一且含义明确的原生听写开始/停止控件，才启用“按住说话测试”。开始和停止都等待实际状态确认；权限丢失、窗口变化和停止失败不会假装成功。独立测试从按下起最多 60 秒（含启动等待），到时或鼠标抓取丢失会请求停止；窗口失焦本身不结束测试。停止前恢复原窗口，即使用户切到同一应用的其他窗口。停止未确认时保留观察和退出保护；只有明确尚未尝试点击的失败才允许重试，调用结果未知时不重复切换语音按钮。

此阶段调用的是目标应用自带的语音功能，录音设备、权限和转写处理遵循目标应用本身设置。本软件没有新增云端语音 API，也没有自动选择 DS5 为系统麦克风；手柄按键、HID 音频和语音识别结果注入尚未连接。

Antigravity 官方 2.0 文档提供 `Cmd+L` 聚焦及 `Ctrl+M` 语音启停，并说明转录到输入框。本机真实窗口已确认 `Message input` 标签及 `Cmd+L` 的聚焦结果，但麦克风的可访问性标签仍为 `Record voice memo`；没有真实录音结果或明确停止控件证据时，诊断为 `native_voice_memo`，不把它直接当成已验证的文字听写。Codex 的真实 UI 由本应用的用户测试入口验收；自动 Computer Use 工具禁止控制自身应用，本轮没有绕过该限制。[Antigravity 功能文档](https://antigravity.google/docs/features)、[2.0 语音转写说明](https://antigravity.google/blog/introducing-google-antigravity-2?hl=en)。

DS5 蓝牙不会在本机直接列出标准音频输入。Sony 的标准兼容说明不承诺 Mac 的手柄内置麦克风；蓝牙 HID 中的 Opus 音频可由独立诊断解码，但当前链路无法提供完整的连续语音，不能当成 macOS 原生麦克风。[Sony 兼容说明](https://www.playstation.com/en-us/support/hardware/pair-dualsense-controller-bluetooth/)、[DS5Dongle 音频协议](https://github.com/awalol/DS5Dongle/blob/c67c7f685fe8d8cc44f519d27710c5a639a1be7d/src/audio.cpp)。

已准备独立诊断脚本，默认只读取报告；与映射后台分开运行，不安装虚拟音频设备，不修改系统音频源。实际收音需要明确的 `--enable-mic`，最长 30 秒，结束和异常时尝试关闭麦克风；只有合法 CRC 的音频帧完成 Opus 解码后才保存本地 WAV。WAV 按真实捕获时间保留缺帧区间为静音，JSON 报告音频覆盖率；低于 90% 标为 `INCOMPLETE_AUDIO` 并返回非零状态。录音和报告以仅当前用户可读写的权限保存。关闭失败或通信异常同样报告错误。

```sh
# 先关闭 GamePadStudio，连接一只蓝牙 DS5。默认不会启用麦克风。
.venv/bin/python scripts/probe_dualsense_mic.py --seconds 5 --output /tmp/ds5-mic-read-only.json
# 准备好说话后，单独执行限时收音，结果和可能的 WAV 均留在本地。
.venv/bin/python scripts/probe_dualsense_mic.py --enable-mic --seconds 10 --output /tmp/ds5-mic-capture.json
# 只检查包速率、编码格式和解码情况；声音仅在内存中处理，不保存 WAV。
.venv/bin/python scripts/probe_dualsense_mic.py --enable-mic --no-save-audio --seconds 5 --output /tmp/ds5-mic-format.json
```

2026-10-03 实机测试：USB 连接时，macOS 将 DS5 列为音频输入；静音样本 RMS 约 23，说话样本 RMS 约 1,096。切换为蓝牙后，只读探测收到合法的 78 字节报告；开启麦克风 12 秒获得 518 个 CRC 合法、Opus 解码成功的音频包，关闭后复查没有音频标志。但 518 包只代表 5.18 秒声音，原先直接拼接导致播放语速明显加快。修正时间轴后，4 秒复测生成 4.00 秒 WAV，其中仅 168 个 10 毫秒音频包，覆盖率 42%；其余保留静音并明确判为 `INCOMPLETE_AUDIO`。蓝牙总报告率约 64–65 包/秒、音频包约 42 包/秒，而连续 10 毫秒音频需要约 100 包/秒；缺失内容无法靠修正 WAV 采样率恢复。此结果只证明蓝牙 HID 音频可解码，不证明蓝牙语音可用于识别或向 Codex／Antigravity 输入。后续须找到能提供完整音频流的采集路径；需要可靠收音时先用 USB 或 Mac 自身麦克风。

### 蓝牙包格式和传输排查

48,000 Hz 是当前 **PCM 解码输出** 的设置，不能据此认定手柄的硬件 ADC 采样率。2026-10-03 实测所有有效音频包为 71 字节、Opus TOC `0xd4`、编码双声道、`superwideband`（编码频率上限 12 kHz），每包仅一个 10 毫秒帧；单声道解码输出是下混结果。相同包按 16,000 Hz 输出为 160 点，按 48,000 Hz 输出为 480 点，两者都是 10 毫秒。编码频率上限也不是包中真实声音频谱的测量结果。诊断 JSON 使用 `encoded_packet_formats` 与 `pcm_output_sample_rate_hz` 分开报告这些信息。[Opus 包信息与解码 API](https://opus-codec.org/docs/opus_api-1.5/group__opus__decoder.html)、[Opus 带宽定义](https://github.com/xiph/opus/blob/main/include/opus_defines.h)

在 macOS 26.5.2 的这只 DS5 上，独立原生 IOHID 回调及其内核时间戳均约每 15 毫秒一次，与 SDL/Python 读取结果一致。增加 IOHID 队列至 512 项仍无改善；报告高四位序号逐包连续，而音频头的第二个字节频繁跳变。`extended_sequence_deltas` 与 `audio_header_counter_deltas` 只报告实际观察的模计数变化，不擅自将未知字段认定为确定的丢包位置。把麦克风控制间隔从 500 毫秒缩短到 10 毫秒、切换 ASR/聊天输入路由、以及对比公开项目的完整控制块（含全零触觉段），仍只获得约 42–43 个音频包/秒。另一次先发送带标签的音频路由初始化（仅选择内置 processed 输入，不改音量及静音状态），6 秒收到 253 个音频包，覆盖率 42.2%，结束关闭麦克风并恢复输入路由，仍无改善。此时没有证据把瓶颈归因于解码输出采样率或 Python 读取速度。[SDL macOS HID 实现](https://github.com/libsdl-org/SDL/blob/release-2.28.4/src/hidapi/mac/hid.c)、[ControlDeck 控制协议](https://github.com/ihansel/control-deck/blob/main/Sources/ControlDeck/DualSenseBluetoothAudioProtocol.swift)

15 毫秒间隔与 Bluetooth Sniff 省电模式相符，但还未读取真实连接模式来确认。本机宿主进程的蓝牙授权为 `denied`，旧 IOBluetooth 接口因而报告手柄未连接及控制器关闭，这些结果不能用于调整链路。项目提供独立的只读诊断 `.app`，在自己的应用身份下申请一次标准系统权限；默认不申请权限，也不调用 HCI 写命令。授权不可用时跳过全部 IOBluetooth 查询；授权可用后只匹配已连接 DS5，报告模式及间隔，文件权限为 `0600`，不输出地址。原生 helper 的编译、签名与静态分析已通过。首次系统权限请求也返回 `denied`，因此没有执行真实连接模式查询；需要用户在「系统设置 → 隐私与安全性 → 蓝牙」允许 `GamePadStudio Bluetooth Diagnostics` 后继续验证。[Apple 蓝牙授权状态](https://developer.apple.com/documentation/corebluetooth/cbmanager/authorization)、[Apple 蓝牙设备框架](https://developer.apple.com/documentation/iobluetooth)

```sh
.venv/bin/python scripts/macos/build_bluetooth_diagnostics.py
# 通过 LaunchServices 启动，读取独立应用的权限状态。
open -n 'build/macos/GamePadStudio Bluetooth Diagnostics.app' --args --output /tmp/ds5-bt-link.jsonl
# 需要用户在 macOS 系统对话框选择允许，之后只读取连接状态。
open -n 'build/macos/GamePadStudio Bluetooth Diagnostics.app' --args --request-permission --output /tmp/ds5-bt-link-authorized.jsonl --observe-seconds 2
```

不能把其他项目可列出虚拟麦克风、插入静音或 PLC 当成丢失语音已恢复的证明。当前诊断代码全量离线回归为 110 个模块、2,508 项通过、1 项 Windows 专属测试跳过；真实蓝牙连续语音仍未通过验收。

## 本地验证与打包

```sh
.venv/bin/python scripts/test_macos.py
QT_QPA_PLATFORM=offscreen .venv/bin/python main.py --smoke-test --data-dir /tmp/gamepadstudio-smoke
.venv/bin/python -m pip install pyinstaller
.venv/bin/python scripts/build_macos_capture.py
.venv/bin/pyinstaller --noconfirm GamePadStudio.spec
```

Mac 产物为 `dist/GamePadStudio.app`。本地构建按本机架构生成，不能自动宣称同时支持 Intel 和 Apple Silicon。当前包使用本地签名，公开分发需要稳定的 Developer ID 签名与公证，重建后系统可能要求重新授权。

Mac 源码依赖安装会同时安装匹配本机架构的 `imageio-ffmpeg`，无需全局安装 FFmpeg；打包时复制为包内的 `bin/ffmpeg`，并附带构建好的 `gps-mac-capture`。VideoToolbox 通过实际合成画面编码探测，使用 `allow_sw=0` 避免把软件编码报告为硬件编码；失败时明确回退。录制仍需要屏幕录制权限。本轮仅合成画面完成硬件编码与解码检查，未采集用户屏幕。

自动测试使用虚拟手柄和模拟输出，不向真实桌面发送按键或修改系统权限。窗口启动和后台通信检查不能代替 DS5 蓝牙的震动、触摸、断线、游戏镜头和实际输入验收。Windows 分支代码可以在 Mac 上做逻辑回归；Windows 原生 API 和驱动仍需 Windows 实机验收。

2026-10-02 此前新增窗口识别、截图／录像路由、显示器／捕获协议、CapsLock／媒体键、手柄隔离及音视频封装测试。窗口路由使用模拟目标与合成图片；Matroska 音视频轨使用合成 PCM 和 RGB，经 FFmpeg 编码、解码验证音轨存在、采样格式、原始时间戳、时长及音画同步。手动录像保存后提供封面与实际分辨率／音轨元数据，未完成文件不进入图库；`game` 录像范围与回放缓存统一为游戏所在显示器。旧 Windows 预设中 Mac 不支持的键只停该输出，其他映射继续。Mac 新增物理键盘截图快捷键，普通游戏鼠标边缘保护扩展到经过前台窗口核对的进程，并按整个虚拟桌面外缘判断，避免双屏接缝误回中；回放提示和 AV1 软件编码标签按实际行为显示。语音测试覆盖草稿保留、进程重启、跨窗口停止、延迟状态、点击超时与测试上限。此前全量离线回归共 2,431 项：2,430 通过、1 项 Windows 专属进程标志测试跳过。这些离线测试不代表真实 DS5、游戏画面或麦克风验收。

本次加入键鼠切换保持与脱敏诊断导出后，重新运行 `.venv/bin/python scripts/test_modules.py --jobs 4`：109 个模块、2,481 项通过、1 项 Windows 专属测试跳过。最新 `dist/GamePadStudio.app` 为 arm64、ad hoc 签名，包含 FFmpeg、原生捕获组件和诊断模块；`codesign --verify --deep --strict` 与模拟设备的打包界面冒烟测试通过。真实 DS5、游戏、麦克风与 Windows 原生行为仍待现场验证。

此前 Apple Silicon 原生 Cocoa 设置窗口启动、渲染及退出，应用包后台跨会话通信、暂停及正常退出，以及 HEVC/H264 VideoToolbox 合成 RGB 编码、解码已验证。这些检查不代表最新捕获组件已完成真实游戏录像验收。

此前应用包成功读到蓝牙 DualSense（054c:0ce6）、17 个可用按键、6 轴、双指触摸能力及 FULL 电量档；独立进程连续读取 60 秒、2,792 次，没有读取缺失或断线。用户已重新连接手柄，但暂不在场，本轮没有启动新的真实手柄操作测试。实际按键/摇杆/扳机/触摸操作、灯光/震动输出及目标游戏尚未验收；能力报告不能替代这些操作测试。辅助功能尚未授权，未发送真实键鼠事件。全局热键原生注册、重复占用和注销已验证，真实按键触发尚待验证；登录启动只通过临时目录和模拟 launchctl 验证，没有修改真实用户登录项。Windows 原生行为仍待 Windows 设备回归。

原生 API 依据：[Apple CoreGraphics](https://developer.apple.com/documentation/coregraphics/core-graphics-functions)、[ScreenCaptureKit](https://developer.apple.com/documentation/screencapturekit/capturing-screen-content-in-macos)、[SCScreenshotManager](https://developer.apple.com/documentation/screencapturekit/scscreenshotmanager)、[系统音频捕获](https://developer.apple.com/documentation/screencapturekit/scstreamconfiguration/capturesaudio)、[IOHIDDeviceOpen](https://developer.apple.com/documentation/iokit/iohiddeviceopen(_:_:))、[Qt Mac 修饰键说明](https://doc.qt.io/qt-6/macos-issues.html)、[PyInstaller Mac 应用打包](https://pyinstaller.org/en/stable/spec-files.html#spec-file-options-for-a-macos-bundle)。

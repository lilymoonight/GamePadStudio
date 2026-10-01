# GamePad Studio 实现与使用体验审查

日期：2026-09-29。依据本地 `DESIGN.md`、`docs/DESIGN.md`、README、当前源码和原生 Qt 界面。两份设计文档内容相同，标注 v2.0-LTS；项目和安装器仍标注 1.9.0。

当前不合理之处主要来自功能之间缺少统一规则：两套映射彼此覆盖，前后台各自保存整份配置，录制状态由设置值代替实际状态，部分界面描述与执行结果不同。继续增加功能或调整圆角，无法解决这些问题。

**验证方式与范围**

- 使用隔离数据目录复现操作，虚拟键盘、鼠标、系统配置写入和驱动修改均被替换为测试对象。没有改动产品源码或现有用户配置。
- 导出 1200×780、960×640 两种尺寸的实际 Qt 界面；不是设计稿。
- 限定运行 `tests/`：104 项通过，耗时 6.41 秒。Game Bar 注册表和 HidHide 驱动访问在本次运行中隔离，因此不能据此宣称真实驱动或系统恢复验收完成。
- 补充检查发现了原测试未覆盖的跨功能问题。保存阻塞检查使用实际 FFmpeg 和 2 秒、320×240 合成画面，没有录制用户桌面。
- 本次未重新验证真实手柄按键、蓝牙、特定游戏、高权限目标、4K 长时录制性能。

证据：[复现结果](D:/dualsense5_screenshot/artifacts/design-audit-20260929/reproductions.json)、[测试记录](D:/dualsense5_screenshot/artifacts/design-audit-20260929/tests.log)、[复现工具](D:/dualsense5_screenshot/artifacts/design-audit-20260929/run_checks.py)。

**优先修复的行为问题（P1）**

1. **编辑键鼠方案会覆盖刚保存的其他设置。**

   设备刷新后，主窗口重新创建 ConfigStore，键鼠页面仍引用旧对象。先更改截图目录，再编辑键鼠方案，目录会被写回原值；已隔离复现。影响不限于目录，旧对象保存的是整份配置。[主窗口刷新](D:/dualsense5_screenshot/dualsense5/studio.py:1129)、[键鼠保存](D:/dualsense5_screenshot/dualsense5/virtual_kbm_ui.py:418)。

   应由后台统一持有配置，界面发送局部修改；至少先保证所有页面使用同一配置对象。验收：切换设备、改目录、改映射、改键鼠方案、重启，所有修改都保留，不能互相回退。

2. **“按键配置”和“虚拟键鼠”不是同一套映射，截图键会失效。**

   后台在两套引擎之间二选一；启用键鼠方案就跳过普通映射。DualSense 默认 Create 截图因此被键鼠方案的 `4 → M` 替代，PS 控制中心也没有统一保留规则。复现：普通模式截图 1 次，启用键鼠后同样操作新增截图 0 次。旧“无限暖暖”配置还会强制进入键鼠分支，即使键鼠开关关闭，也不能回到普通映射。[调度分支](D:/dualsense5_screenshot/dualsense5/agent.py:135)、[方案默认值](D:/dualsense5_screenshot/dualsense5/virtual_kbm.py:54)。

   应统一为一个设备方案，明确系统键、游戏层、组合键的优先级。验收：开启、关闭键鼠映射，Create／Share、PS／Home 的实际动作和页面展示一致；切换普通预设确实退出键鼠接管。

3. **修改无关设置会清空回放缓存。**

   所有设置修改都发出整份 reload；后台每次 reload 都 stop 回放，stop 会清空缓存。因此改变振动、语言、收藏、鼠标灵敏度甚至拖动设置滑块，都可能丢掉已经缓存的精彩片段。本次将振动改为 0.5，缓存从 20,000 字节变为 0。[设置保存](D:/dualsense5_screenshot/dualsense5/studio.py:1269)、[后台重载](D:/dualsense5_screenshot/dualsense5/agent.py:187)、[缓存释放](D:/dualsense5_screenshot/dualsense5/replay_service.py:245)。

   应按字段更新，仅在录制参数确需改变时重建录制管线，并合并滑块连续变更。验收：调灯光、振动、映射、收藏、语言，缓存时长持续增长。

4. **关闭回放仍可能启动录制，界面又会误报成功。**

   “保存回放”调用不检查 `replay_buffer_enabled`，引擎未运行时直接 start。已复现设置为 false 仍调用 start。缓存未就绪则偷偷改用 Game Bar；快捷键发出后广播 success，没有确认系统是否生成了文件。设置页“录制中”也只依据开关值，不读取实际缓冲状态。[保存入口](D:/dualsense5_screenshot/dualsense5/agent.py:269)、[设置页状态](D:/dualsense5_screenshot/dualsense5/studio.py:1014)。

   应区分关闭、准备中、录制中、保存中、失败，展示真实缓存秒数。验收：关闭时不启动引擎；无缓存时说明尚无片段；成功必须有可验证文件，不能把“快捷键已发送”当成“回放已保存”。

5. **保存录像会阻塞后台手柄响应。**

   IPC handler 和长按动作直接调用 save_replay；内存拼接、FFmpeg 等待、海报提取都同步完成，期间后台事件循环不能轮询手柄、释放按键或回复状态。仅保存 2 秒、320×240 合成录像就占用约 99ms，10ms 定时器约 100.4ms 才执行；这不是 4K 性能测试，但确认了阻塞路径。FFmpeg 等待还没有超时。[IPC 调用](D:/dualsense5_screenshot/dualsense5/agent.py:216)、[保存与海报生成](D:/dualsense5_screenshot/dualsense5/replay_service.py:374)。

   应由独立工作任务保存，完成后发送结果。验收：保存过程中仍可暂停映射、释放键、切换设备、读取状态；编码器故障不能无限等待。

6. **Game Bar 屏蔽与录像回退相互冲突，恢复流程不完整。**

   屏蔽功能同时禁用 GameDVR 和历史录制；录像回退却仍使用同一系统的 Win+Alt+G／R。设计文档承诺退出恢复，但前台和后台退出都没有恢复调用。手动恢复函数对部分原本不存在的值写入 1，而且忽略写入失败，始终返回成功；键鼠页面也在失败时显示成功通知。[屏蔽项目](D:/dualsense5_screenshot/dualsense5/gamebar_shield.py:25)、[恢复函数](D:/dualsense5_screenshot/dualsense5/gamebar_shield.py:216)、[页面反馈](D:/dualsense5_screenshot/dualsense5/virtual_kbm_ui.py:857)、[后台退出](D:/dualsense5_screenshot/dualsense5/agent.py:323)。

   应把系统修改交给后台统一管理，定义作用范围、备份、失败回滚和退出恢复；屏蔽启用时不能自动选择被禁用的回退路径。验收须覆盖失败、中途退出、重复启停、原值不存在以及卸载场景。本次仅代码审查，没有修改真实注册表。

7. **“清理未收藏截图”也会永久删除视频。**

   list_captures 同时返回图片和视频；清理函数没有按类型过滤，确认框仍只说删除多少“张截图”。用户以为只清照片，实际视频也在删除名单中。[媒体枚举](D:/dualsense5_screenshot/dualsense5/screenshot_service.py:289)、[清理操作](D:/dualsense5_screenshot/dualsense5/studio.py:1687)。

   应限制为截图，或改为“清理未收藏媒体”并分别列出图片数、视频数及容量。验收：混合媒体目录的删除名单与确认内容完全一致。此次没有执行删除。

**界面与操作规则问题（P2）**

8. **原先明确要求的手柄测试示意图被隐藏。**

   测试页仍创建 ControllerSchematic，但直接 hide，只保留内部状态供旧测试断言。用户看到的是数字按键格和仪表；测试通过也不代表示意图可见。正常推动左摇杆至 0.8 还会被显示为“异常偏移”，没有松手阶段和稳定采样，不能作为漂移判断。[隐藏示意图](D:/dualsense5_screenshot/dualsense5/input_tester.py:120)、[偏移判定](D:/dualsense5_screenshot/dualsense5/input_tester.py:423)、[当前测试页](D:/dualsense5_screenshot/artifacts/design-audit-20260929/ui-3-1200x780.png)。

   应恢复与型号对应的实时示意图，仪表作为辅助信息；漂移测试使用“松开摇杆→采样→结果”的明确流程，并分别检测左右摇杆。

9. **默认尺寸也出现横向溢出，设计语言与文档不一致。**

   设置页内容宽 1334px；1200px 窗口内可用宽 1078px，需横向滚动 256px；960px 窗口需滚动 496px。键鼠页小窗口的顶部开关文字和部分控制被挤压。大量工程编号、英文标签、旋钮与重复滑块增加信息密度。设计写 Apple Modern Design，样式则明确采用 Teenage Engineering／Dieter Rams，缺少一致的产品方向。[设置页](D:/dualsense5_screenshot/artifacts/design-audit-20260929/ui-4-1200x780.png)、[小窗口键鼠页](D:/dualsense5_screenshot/artifacts/design-audit-20260929/ui-6-960x640.png)、[样式定义](D:/dualsense5_screenshot/dualsense5/glass.py:23)。

   应使用真正响应式单／双列布局、紧凑工具菜单、统一图标与排版，避免同一数值同时配旋钮和滑块。验收：最小窗口、长目录、长设备名、中文／英文、高 DPI 都无需横向滚动查看主要操作。

10. **映射编辑器能保存永远不会执行的组合。**

    短按设为“按住键盘按键”后，仍可给长按设置动作；但 GestureEngine 特意禁止这类长按触发。已复现编辑器保存长按截图，执行却只有 hold 按下／抬起。设计还承诺双击与统一状态机，普通映射仅支持短／长按；键鼠引擎另用默认 0.20 秒的时间窗，与全局默认 0.65 秒不一致。[编辑器](D:/dualsense5_screenshot/dualsense5/studio.py:98)、[手势执行](D:/dualsense5_screenshot/dualsense5/studio_core.py:237)、[键鼠时间窗](D:/dualsense5_screenshot/dualsense5/virtual_kbm.py:748)。

    应只允许可执行的配置，或明确定义冲突时如何释放／切换。双击、长按、组合键应使用同一套规则和可见设置。

11. **“快门触觉”和“心跳律动”测试按钮无效。**

    设置页将 shutter／heartbeat 发送给 trigger_feedback；该函数没有这两个分支，真正的波形在 play_pattern 中。隔离检查得到 shutter、heartbeat 均无波形调用，impact 正常。现有测试直接测 play_pattern，因此漏掉了 UI→后台链路。[按钮入口](D:/dualsense5_screenshot/dualsense5/studio.py:810)、[后台入口](D:/dualsense5_screenshot/dualsense5/agent.py:233)、[反馈分发](D:/dualsense5_screenshot/dualsense5/haptic_engine.py:101)。

    应统一测试波形入口，并根据设备能力禁用按钮；必须区分“请求已发送”“设备接受”和“设备不支持”。

12. **键鼠页仍会给 PS 手柄显示 Xbox／通用标签，外设支持缺少适配层。**

    键鼠页从 ConfigStore.snapshot 读取型号，但该对象没有 snapshot，实际设备保存在 current_device_state。已复现 DualSense 的空格键绑定徽章显示 A，而非 ×。Raw Joystick 虽已能读取任意按钮和轴，键鼠引擎却仍假定前六轴依次是双摇杆和双扳机；普通映射仅容纳 0–20 按钮，无法据此宣称飞行摇杆／踏板完整适配。[徽章刷新](D:/dualsense5_screenshot/dualsense5/virtual_kbm_ui.py:710)、[原始设备读取](D:/dualsense5_screenshot/dualsense5/device.py:161)、[轴假定](D:/dualsense5_screenshot/dualsense5/virtual_kbm.py:701)、[配置按键校验](D:/dualsense5_screenshot/dualsense5/studio_core.py:90)。

    应区分“设备已识别”和“布局已适配”，为未知外设提供轴／按钮学习、方向校正和校准；显示和方案都按当前设备更新。

13. **截图、回放、录屏还没有形成统一媒体管理体验。**

    “智能游戏”只排除少数系统窗口，普通浏览器等也会被选中；录制仅在启动时决定抓取范围，`window` 模式仍落到整块显示器。录制命令只有视频输入，没有系统声音／麦克风输入。系统录屏只发开关快捷键，没有录制状态或保存文件导入。图库同步载入最多 180 项，无继续加载入口；视频交给系统播放器，`.jpg` 海报与 `.json` 元数据放在媒体旁，与设计所说专属缓存、单文件自包含有出入。[目标识别](D:/dualsense5_screenshot/dualsense5/screenshot_service.py:154)、[录制输入](D:/dualsense5_screenshot/dualsense5/replay_service.py:283)、[录屏开关](D:/dualsense5_screenshot/dualsense5/agent.py:292)、[图库加载](D:/dualsense5_screenshot/dualsense5/studio.py:1594)。

    应明确显示实际捕获目标，分别配置截图、回放、录屏；提供准备／录制／停止／导入闭环，媒体页区分照片与视频并支持继续浏览。音轨和目标跟随需作为明确能力，不能隐藏在“4K 极清”描述里。

14. **安装包没有包含录制所需 FFmpeg，后台也缺少快捷管理入口。**

    当前构建定义只收集 pygame 动态库和 assets，现有 `dist/GamePadStudio` 没有 FFmpeg。引擎能在开发机找到项目 bin 或 D 盘语音工程的 FFmpeg，会掩盖其他电脑缺组件的问题。GUI 关闭后后台继续工作，但没有后台托盘入口；GUI 创建的 tray 也未 show，保存通知难以承担后台反馈职责。[构建定义](D:/dualsense5_screenshot/GamePadStudio.spec:4)、[组件查找](D:/dualsense5_screenshot/dualsense5/replay_service.py:35)、[托盘创建](D:/dualsense5_screenshot/dualsense5/studio.py:284)、[退出行为](D:/dualsense5_screenshot/dualsense5/studio.py:1742)。

    应打包并检查必要组件，在干净 Windows 环境验收；后台提供运行／暂停状态、恢复／暂停、打开管理器、退出的托盘菜单，以及截图和录制结果反馈。

**设计文档本身也需要修订**

文档更像架构介绍，没有定义关键使用规则：同一按键被多个功能配置时谁优先；方案属于哪个设备／游戏；关闭 GUI 与停止后台的区别；驱动失败如何恢复；回放未准备好时该提示什么；媒体删除范围与确认内容。缺少这些规则，界面和后台容易分别做出不同决定。

以下表述不能作为已验收事实：零性能损耗、硬实时 1000Hz、0.05 秒导出、0.03 秒海报、键盘输入 100% 识别率、104 项端到端无死角覆盖。当前是 GDI／mss 抓帧后将 RGB 数据送入编码器，并非全程 GPU 直通；250Hz 的调度计时器也不是硬实时保证。ConfigStore 使用 os.replace，没有文档写的 SHA 校验与显式 ReplaceFileW。PNG 收藏更新会读取并重写整个文件，视频仍生成伴生文件。应写清目标值、实现方式、实际测量、平台条件和未验证边界。[设计承诺](D:/dualsense5_screenshot/DESIGN.md:29)、[配置保存](D:/dualsense5_screenshot/dualsense5/studio_core.py:224)、[PNG 更新](D:/dualsense5_screenshot/dualsense5/screenshot_service.py:67)。

**建议下一版的产品组织与验收顺序**

主导航收敛为“设备、映射、媒体、设置”。设备页包含概览与测试；映射页统一系统键、游戏键鼠和组合键；型号展厅放在设备页的次级入口。首页直接回答：连接了什么、当前方案是什么、映射是否生效、回放是否就绪。状态只来自后台真实结果。

先修配置回退、映射冲突、缓存清空、错误删除范围和后台阻塞，再恢复可见测试示意图、校正漂移流程，最后整合布局与视觉。每一项功能都按“用户操作→后台状态→实际结果→界面反馈→重启后保留”验收；增加跨功能操作测试，避免只测试内部对象存在就视为完整实现。

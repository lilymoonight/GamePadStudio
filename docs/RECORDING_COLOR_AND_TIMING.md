# 录制颜色、时间轴与多屏限制

本次修改针对 HDR 录制偏色，以及抓取速度低于设置帧率时的视频快进。普通屏幕和 HDR 屏幕共用带逐帧时间戳的编码入口；HDR 内容先保留浮点数据，再转换为 SDR 视频。下面 Windows 路径与 Mac 路径分开说明：平台使用不同捕获接口，不把 Windows SDR 白点或像素坐标直接套用于 Mac。

## 时间轴保持实际时长

旧入口将 RGB24 作为固定帧率的 rawvideo 交给编码器。rawvideo 不携带每帧采集时间：例如设置 30 FPS、实际只能抓取 15 FPS，按帧数计算的时间轴会缩短一半。

现在每帧通过 Matroska 输入写入单调递增的呈现时间戳（PTS），编码时保留这些时间戳，不再用输入 `-r` 重建固定帧率。抓取或转换变慢时，实际 FPS 降低，视频继续按采集时间播放，不会因为帧数减少而整体快进。回放内存窗口与导出时长也读取编码媒体的时间轴。

这一区别对应 [FFmpeg 的输入 `-r` 与时间戳说明](https://ffmpeg.org/ffmpeg.html#Video-Options)，Matroska 的时间戳字段见 [官方容器规范](https://www.matroska.org/technical/elements.html)。设置中的 FPS 是采集目标，不是硬件达成的保证。

## Windows HDR 先保留浮点亮度，再转换颜色

HDR 捕获使用 DXGI Desktop Duplication，经 FFmpeg `ddagrab` 获取 RGBA FP16 数据，禁止捕获层自动退回 8 位。Windows 桌面数据是线性 scRGB，采用 BT.709 原色；这里的 `1.0` 表示 80 nits，不能当作 PQ 编码或直接截断到 `[0, 1]`。[Microsoft 的 Advanced Color 说明](https://learn.microsoft.com/en-us/windows/win32/direct3darticles/high-dynamic-range)、[FFmpeg 的 `ddagrab` 文档](https://ffmpeg.org/ffmpeg-filters.html#ddagrab)

转换顺序为：

1. 读取目标屏幕的 Windows SDR 白点；原始 `SDRWhiteLevel / 1000 × 80` 得到 nits。
2. 在浮点域按 `80 / SDR白点` 归一化，保留中间调，对高光做连续、平滑的压缩。
3. 转换为 SDR BT.709 传递函数、色彩矩阵和视频范围，再量化为编码器所需的格式，并写入相符的颜色标签。

本机 HDR 电视的实测 SDR 白点为 **240 nits**，因此不能统一按 80 nits 处理。[SDR 白点接口与换算规则](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/ns-wingdi-displayconfig_sdr_white_level)

禁止先把 HDR 浮点值降为普通 RGB24，再执行色调映射；被截断的高光与颜色无法恢复。HDR 状态未知、白点读取失败、浮点捕获失败时明确停止录制，不自动改用 MSS。当前 HDR 路径支持单个屏幕内的捕获，跨 HDR 屏幕或 HDR/SDR 混合跨屏暂不支持；导出目标是 **SDR BT.709**。

## Windows 多屏使用真实刷新率

通过 Windows `QueryDisplayConfig` 读取刷新率的分子与分母，优先使用实际目标信号；备用接口为 `EnumDisplaySettingsEx`。屏幕编号按 MSS 的像素位置和尺寸匹配，不把 Windows 或 DXGI 枚举顺序当作 MSS 编号。[Microsoft 显示配置接口](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-querydisplayconfig)

当多个屏幕的实测刷新率不同，或其中一屏刷新率无法确认，**0 号“全部屏幕”禁止录制**，提示中列出已检测到的 Hz，用户应选择单个屏幕。分数刷新率保留其实际数值，例如 59.94006 Hz 与 60 Hz 不合并为同一个 60 Hz；这一限制与时间戳修复同时生效。

截图范围与回放录制范围独立保存：截图使用 `capture_mode`，回放使用 `replay_capture_mode`。旧配置尚未保存回放范围时，首次从原 `capture_mode` 继承；之后调整截图范围不会改变回放范围。

## Mac 使用 ScreenCaptureKit 和物理像素

Mac 捕获组件使用公开的 ScreenCaptureKit。屏幕／窗口定位使用 CoreGraphics 全局逻辑点，允许负坐标；图像尺寸使用 CGDisplayMode 物理像素、每屏 scale 及 SCK 内容的 pointPixelScale，不把 Retina 统一假定为 2 倍。截图保留 `game`／`window`／`all`／`monitor_N`：游戏范围跟随窗口最大重叠的显示器，窗口范围按已验证窗口 ID 过滤，全部屏幕按最大 scale 拼接到统一像素画布；指定显示器断开时失败。[Apple 屏幕捕获示例](https://developer.apple.com/documentation/screencapturekit/capturing-screen-content-in-macos)、[窗口截图接口](https://developer.apple.com/documentation/screencapturekit/scscreenshotmanager)、[物理像素宽度](https://developer.apple.com/documentation/coregraphics/cgdisplaymode/pixelwidth)

录制读取 CGDisplayMode 的实际刷新率，保留分数值，不用 NSScreen 的最大可能 FPS 代替当前 Hz。和 Windows 一样，多屏刷新率不同或不能确认时拒绝合并录像，单屏仍可使用；显示器布局、物理像素、刷新率或 EDR 状态改变后需要重新开启。截图拼接不受录像的同刷新率限制。

Mac HDR 路径要求 macOS 15+ 和 Apple Silicon。NSScreen 当前 EDR headroom 与潜在 EDR 能力分开显示，不伪造 Windows 的 `SDRWhiteLevel` 或 nits。真正捕获还必须返回 RGBA half 浮点像素及线性 BT.709／extendedLinearSRGB 颜色证明，缺少时明确失败，不能先截成 8 位再标为 HDR。Core Image 在浮点域压缩高光；截图输出 sRGB，视频对桌面显示内容采用 inverse BT.1886（gamma 2.4）并输出 SDR BT.709 色彩标签，不套用摄像机的 BT.709 OETF。当前输出并非原始 HDR 文件。[Apple EDR headroom](https://developer.apple.com/documentation/appkit/nsscreen/maximumextendeddynamicrangecolorcomponentvalue)、[zimg gamma 实现](https://github.com/sekrit-twc/zimg/blob/master/src/zimg/colorspace/gamma.cpp)

合成 Float16 色块已通过原生 Core Image 转换及像素比对，窗口和混合缩放拼接已通过模拟／合成图片测试。没有实际录制用户桌面、HDR 游戏或外接 HDR 屏，这些结果不能替代真实颜色和跨屏同步验收。

## 系统音频与整段录像：本轮集成中

Mac 整段录像以项目内 `ManualRecording` 替代 Windows Game Bar 的录屏开关。系统音频来自 SCK 的 `.audio` 输出，配置为 48 kHz 双声道，只开启一个显示器流的音轨，避免多屏重复音频；不会因此打开麦克风。音频首样本和视频帧使用同一 SCK 源时钟，统一映射到单调时间；早于首视频的样本裁掉，回调延迟不会改变原始 PTS。[Apple capturesAudio](https://developer.apple.com/documentation/screencapturekit/scstreamconfiguration/capturesaudio)

新增 `TimestampedAVWriter` 为 RGB24 和 interleaved PCM s16le 写入 Matroska 的两条轨，微秒时间刻度保留音频包时间，调用方减去同一个首视频时间原点。格式变化、不完整采样、时间倒退和重叠明确报错。纯视频 `TimestampedRGBWriter` 的原接口与行为保留。[Matroska 时间戳与轨道元素](https://www.matroska.org/technical/elements.html)、[PCM 编码规范](https://www.matroska.org/technical/codec_specs.html#a_pcmintlit)

合成音视频已由 FFmpeg 编码和解码，检查 PCM 原样还原、非整毫秒 PTS、不规则视频时间、音频迟到、最终立体声音轨、时长和音画同步；原生合成 CMSampleBuffer 覆盖 float32 交错／平面布局转 PCM16。实际系统音轨、整段录像保存及界面／后台集成正在验收，本轮最终回归数字与包结果待最终报告填写。

## Windows 既有验证范围与性能

已检查本机屏幕的几何位置、分数刷新率、HDR 状态及 SDR 白点，并通过合成浮点帧验证转换顺序和时间轴。4K 合成帧的当前 CPU 色彩转换耗时约 **67–73 ms/帧**，只计算转换阶段约为 **14 FPS**；抓取、传输和编码还会增加耗时，因此不承诺 4K HDR 达到 30 或 60 FPS。这是开发机测试，不代表所有设备或最终性能上限。静态画面复用已转换的帧，并按真实经过时间延长显示。

尚未完成真实 HDR 游戏录像的逐场景颜色验证，包括暗部、强高光与快速镜头运动。自动化与合成帧结果不能替代该验证。

较旧 Windows 若只能读取旧版 AdvancedColorInfo，开启“高级颜色”只能确认 HDR 或 WCG 中至少一种已启用。当前会重新枚举对应设备的 `IDXGIOutput6::GetDesc1().ColorSpace` 补充判断；已知克隆目标、无效字段或无法确认的状态仍停止录制，不能按“10 位输出”推测 HDR。该接口并不能证明 EDID 本身有效，见 [Microsoft 的 GetDesc1 说明](https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_6/nf-dxgi1_6-idxgioutput6-getdesc1)。

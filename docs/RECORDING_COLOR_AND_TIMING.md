# 录制颜色、时间轴与多屏限制

本次修改针对 HDR 录制偏色，以及抓取速度低于设置帧率时的视频快进。普通屏幕和 HDR 屏幕共用带逐帧时间戳的编码入口；HDR 内容先保留浮点数据，再转换为 SDR 视频。

## 时间轴保持实际时长

旧入口将 RGB24 作为固定帧率的 rawvideo 交给编码器。rawvideo 不携带每帧采集时间：例如设置 30 FPS、实际只能抓取 15 FPS，按帧数计算的时间轴会缩短一半。

现在每帧通过 Matroska 输入写入单调递增的呈现时间戳（PTS），编码时保留这些时间戳，不再用输入 `-r` 重建固定帧率。抓取或转换变慢时，实际 FPS 降低，视频继续按采集时间播放，不会因为帧数减少而整体快进。回放内存窗口与导出时长也读取编码媒体的时间轴。

这一区别对应 [FFmpeg 的输入 `-r` 与时间戳说明](https://ffmpeg.org/ffmpeg.html#Video-Options)，Matroska 的时间戳字段见 [官方容器规范](https://www.matroska.org/technical/elements.html)。设置中的 FPS 是采集目标，不是硬件达成的保证。

## HDR 先保留浮点亮度，再转换颜色

HDR 捕获使用 DXGI Desktop Duplication，经 FFmpeg `ddagrab` 获取 RGBA FP16 数据，禁止捕获层自动退回 8 位。Windows 桌面数据是线性 scRGB，采用 BT.709 原色；这里的 `1.0` 表示 80 nits，不能当作 PQ 编码或直接截断到 `[0, 1]`。[Microsoft 的 Advanced Color 说明](https://learn.microsoft.com/en-us/windows/win32/direct3darticles/high-dynamic-range)、[FFmpeg 的 `ddagrab` 文档](https://ffmpeg.org/ffmpeg-filters.html#ddagrab)

转换顺序为：

1. 读取目标屏幕的 Windows SDR 白点；原始 `SDRWhiteLevel / 1000 × 80` 得到 nits。
2. 在浮点域按 `80 / SDR白点` 归一化，保留中间调，对高光做连续、平滑的压缩。
3. 转换为 SDR BT.709 传递函数、色彩矩阵和视频范围，再量化为编码器所需的格式，并写入相符的颜色标签。

本机 HDR 电视的实测 SDR 白点为 **240 nits**，因此不能统一按 80 nits 处理。[SDR 白点接口与换算规则](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/ns-wingdi-displayconfig_sdr_white_level)

禁止先把 HDR 浮点值降为普通 RGB24，再执行色调映射；被截断的高光与颜色无法恢复。HDR 状态未知、白点读取失败、浮点捕获失败时明确停止录制，不自动改用 MSS。当前 HDR 路径支持单个屏幕内的捕获，跨 HDR 屏幕或 HDR/SDR 混合跨屏暂不支持；导出目标是 **SDR BT.709**。

## 多屏使用真实刷新率

通过 Windows `QueryDisplayConfig` 读取刷新率的分子与分母，优先使用实际目标信号；备用接口为 `EnumDisplaySettingsEx`。屏幕编号按 MSS 的像素位置和尺寸匹配，不把 Windows 或 DXGI 枚举顺序当作 MSS 编号。[Microsoft 显示配置接口](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-querydisplayconfig)

当多个屏幕的实测刷新率不同，或其中一屏刷新率无法确认，**0 号“全部屏幕”禁止录制**，提示中列出已检测到的 Hz，用户应选择单个屏幕。分数刷新率保留其实际数值，例如 59.94006 Hz 与 60 Hz 不合并为同一个 60 Hz；这一限制与时间戳修复同时生效。

截图范围与回放录制范围独立保存：截图使用 `capture_mode`，回放使用 `replay_capture_mode`。旧配置尚未保存回放范围时，首次从原 `capture_mode` 继承；之后调整截图范围不会改变回放范围。

## 验证范围与性能

已检查本机屏幕的几何位置、分数刷新率、HDR 状态及 SDR 白点，并通过合成浮点帧验证转换顺序和时间轴。4K 合成帧的当前 CPU 色彩转换耗时约 **67–73 ms/帧**，只计算转换阶段约为 **14 FPS**；抓取、传输和编码还会增加耗时，因此不承诺 4K HDR 达到 30 或 60 FPS。这是开发机测试，不代表所有设备或最终性能上限。静态画面复用已转换的帧，并按真实经过时间延长显示。

尚未完成真实 HDR 游戏录像的逐场景颜色验证，包括暗部、强高光与快速镜头运动。自动化与合成帧结果不能替代该验证。

较旧 Windows 若只能读取旧版 AdvancedColorInfo，开启“高级颜色”只能确认 HDR 或 WCG 中至少一种已启用。当前会重新枚举对应设备的 `IDXGIOutput6::GetDesc1().ColorSpace` 补充判断；已知克隆目标、无效字段或无法确认的状态仍停止录制，不能按“10 位输出”推测 HDR。该接口并不能证明 EDID 本身有效，见 [Microsoft 的 GetDesc1 说明](https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_6/nf-dxgi1_6-idxgioutput6-getdesc1)。

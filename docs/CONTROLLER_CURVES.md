# 主流手柄的曲线能力

核对日期：2026-10-01。调查依据为厂商支持文档、微软接口文档及 SDL 官方实现；项目当前打包依赖为 pygame 2.6.1 / SDL 2.28.4。

## 软件可以调节什么

扳机输入曲线把实际行程转换为映射使用的输入值。震动响应曲线把请求强度转换为实际发送强度。两者可以由软件完成，但前提是连接后确实能读取模拟行程或发送震动。[XInput 输入值](https://learn.microsoft.com/en-us/windows/win32/api/xinput/ns-xinput-xinput_gamepad)、[XInput 两路震动](https://learn.microsoft.com/en-us/windows/win32/api/xinput/ns-xinput-xinput_vibration)

自适应扳机改变实际阻力，扳机震动驱动扳机内的马达，两者需要不同硬件和输出接口。微软的四马达接口分别提供握柄低频、高频及左右扳机强度；不能由此推断设备具有自适应阻力。[GameInput 四马达接口](https://learn.microsoft.com/en-us/gaming/gdk/docs/reference/input/gameinput/structs/gameinputrumbleparams)、[SDL 扳机震动接口](https://wiki.libsdl.org/SDL2/SDL_GameControllerRumbleTriggers)

## 当前实现

当前曲线编辑的是数值响应：左右扳机的“行程 → 映射输入”、低频和高频马达的“请求强度 → 输出强度”，以及设备确实开放接口时左右扳机马达的强度响应。曲线有五个控制点，并可设置起始死区与满量程位置。

截图、回放等反馈的长短和间隔仍由触觉预设安排。当前没有可自由绘制的时间包络编辑器；调节震动响应曲线不会改变预设的脉冲顺序与时长。

DualSense / Edge 硬件可以产生自适应阻力，当前软件尚未实现专用阻力效果或任意阻力曲线。下面的硬件能力矩阵不能当作已经实现的自适应效果菜单。

## 主流设备支持矩阵

“可调”表示可在本软件处理链中实现；震动仍须通过实际连接的能力检测。

| 手柄 / PC 模式 | 扳机输入曲线 | 普通震动曲线 | 独立扳机震动 | 自适应阻力 |
| --- | --- | --- | --- | --- |
| Xbox One / Xbox Series / Elite | 可调，LT / RT 为模拟输入 | 可调，检测到普通震动时显示 | 硬件具备；连接后必须单独检测接口 | 无对应硬件 |
| Xbox 360 / 通用双马达 XInput | 可调，实际提供模拟扳机时显示 | 可调，检测到普通震动时显示 | 不作为通用能力 | 不作为通用能力 |
| DualSense / DualSense Edge | 可调，L2 / R2 为模拟输入 | SDL 普通震动可调；不等同于完整音频触觉波形 | SDL 普通扳机震动接口不支持 | 具有硬件，需 DualSense 专用效果包 |
| DualShock 4 | 可调，L2 / R2 为模拟输入 | 可调；SDL HIDAPI 需要增强模式 | SDL 后端不支持 | 无对应硬件 |
| Nintendo Switch Pro / Joy-Con | 不提供连续曲线，ZL / ZR 只有按下和松开 | 可调，SDL 实际报告震动支持时显示 | 不作为通用能力 | 无对应硬件 |
| 8BitDo Ultimate Bluetooth，PC XInput 模式 | 可调，2.4G / 有线模式 | 可调，检测到普通震动时显示 | 不因 Xbox 外观或名称而默认开放 | 不作为通用能力 |
| GameSir G7 SE，PC 有线模式 | 可调，LT / RT 为模拟输入 | 可调，检测到普通震动时显示 | 以驱动检测结果为准 | 不作为通用能力 |

Xbox 的模拟输入和双马达输出依据微软 XInput 文档；SDL 2.28.4 的 Windows XInput 后端只报告普通震动，Xbox One HIDAPI 后端会为微软设备开放独立扳机震动。第三方 Xbox 类设备不能仅凭名称认定具有相同能力。[SDL XInput 后端](https://github.com/libsdl-org/SDL/blob/release-2.28.4/src/joystick/windows/SDL_xinputjoystick.c)、[SDL Xbox One 后端](https://github.com/libsdl-org/SDL/blob/release-2.28.4/src/joystick/hidapi/SDL_hidapi_xboxone.c)

DualSense 和 DualShock 4 的模拟扳机及普通震动由 SDL HIDAPI 实现；两者的 `RumbleJoystickTriggers` 都返回不支持。DualSense 专用效果包另有左右扳机效果字段，SDL 官方测试程序演示了清除、恒定阻力和阻力加震动。[SDL DualSense 后端](https://github.com/libsdl-org/SDL/blob/release-2.28.4/src/joystick/hidapi/SDL_hidapi_ps5.c)、[SDL DualShock 4 后端](https://github.com/libsdl-org/SDL/blob/release-2.28.4/src/joystick/hidapi/SDL_hidapi_ps4.c)、[SDL 自适应扳机示例](https://github.com/libsdl-org/SDL/blob/SDL2/test/testgamecontroller.c)

Switch 模式即使存在 SDL 扳机轴，仍只有两个端点，不能通过 `HasAxis` 单独判断模拟能力。SDL 官方 Switch 后端直接把 ZL / ZR 的按键位转换为轴端点。[SDL Switch 后端](https://github.com/libsdl-org/SDL/blob/SDL2/src/joystick/hidapi/SDL_hidapi_switch.c)

第三方 XInput 也应按实际驱动识别。SDL 2.28.4 用 GUID 索引 14 的字节（从 0 开始计数）为 `x` 标记 XInput，未知型号通常会报告 Xbox One 类型；部分已知的 Switch 类 XInput 设备却仍使用 Switch 界面类型。因此界面家族、设备名称和模拟轴是否存在都不能单独决定能否调节。实际 XInput 手柄通道、轴绑定来源及连续中间行程能提供更可靠的证据。[SDL 驱动与类型判断](https://github.com/libsdl-org/SDL/blob/release-2.28.4/src/joystick/SDL_joystick.c)、[SDL 轴绑定查询](https://wiki.libsdl.org/SDL2/SDL_GameControllerGetBindForAxis)

## 连接模式与厂商配置

8BitDo Ultimate Bluetooth 官方明确支持扳机灵敏度、震动和配置文件；Windows 配置使用 XInput 的 2.4G 或有线连接。该型号通过蓝牙连接到 PC 时，官方 Ultimate Software 不提供同样的配置路径。[8BitDo 官方支持](https://support.8bitdo.com/ultimate/ultimate-bluetooth-controller.html)

GameSir G7 SE 官方 Nexus 软件提供扳机范围、震动级别等配置。这证明该型号具有相关输入和输出能力，并不意味着本软件能够直接读写 Nexus 的固件配置。[GameSir 官方说明](https://gamesir.com/pages/tutorial-how-to-use-gamesir-g7-se)

DualSense Edge 的 PlayStation Accessories 可以把扳机输入范围、震动和扳机效果强度保存到手柄；不能将 Edge 的固件配置能力推定给普通 DualSense。Edge 的物理行程锁在中、短行程时会关闭自适应效果。[索尼 Edge PC 配置说明](https://www.playstation.com/en-us/support/hardware/set-up-edge-pc/)

索尼注明完整 PC 触觉反馈需支持游戏及 USB 连接。SDL 的兼容普通震动和专用扳机输出支持 USB / 蓝牙封包，但不代表任何 PC 游戏都能获得完整触觉反馈。[索尼 DualSense PC 支持](https://www.playstation.com/en-us/support/hardware/pair-dualsense-controller-bluetooth/)、[SDL DualSense 输出实现](https://github.com/libsdl-org/SDL/blob/release-2.28.4/src/joystick/hidapi/SDL_hidapi_ps5.c)

## 本软件的显示和生效范围

- 设置随当前设备保存；设备切换、断开或连接模式变化时重新计算可用项。
- 模拟扳机、普通震动、独立扳机震动、自适应阻力分别判断。数字扳机和未确认的原始轴不显示连续输入曲线。
- 普通震动和独立扳机震动分别使用 SDL 的能力查询；输出失败时不能继续显示已成功应用。
- 输入曲线只作用于本软件的映射输入。键盘映射仍输出按下和松开，曲线会改变触发手感，不会让键盘产生模拟行程。
- 震动曲线只作用于本软件发送的测试、截图、回放等反馈，不拦截或统一修改其他游戏原生震动。
- 软件曲线不改写厂商固件或板载配置，也不替代厂商设置软件。

能力检测接口：[SDL HasAxis](https://wiki.libsdl.org/SDL2/SDL_GameControllerHasAxis)、[SDL HasRumble](https://wiki.libsdl.org/SDL2/SDL_GameControllerHasRumble)、[SDL HasRumbleTriggers](https://wiki.libsdl.org/SDL2/SDL_GameControllerHasRumbleTriggers)、[SDL 专用效果输出](https://wiki.libsdl.org/SDL2/SDL_GameControllerSendEffect)

## 本机验证

本轮接入了一只真实 DualSense。SDL 报告左右模拟扳机、普通震动、灯光和触摸板可用，独立扳机震动不可用。一分钟采样中，L2 和 R2 均记录到从 0 到接近 1 的连续行程，分别包含 100 和 110 个不同输入值。

低频通道的精细与灵敏曲线，以及高频通道的线性曲线预览均得到驱动成功返回；草稿未写入用户配置。该结果验证设备访问和输出接口，不代表已经评价用户实际感受到的震动品质。其他型号本轮依据官方接口和模拟设备测试核对，尚未逐一实机验证。

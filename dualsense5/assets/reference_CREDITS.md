# 测试页手柄绘图来源

以下 SVG 几何图形提取自 GamepadTester.cn 页面，并将网页计算后的颜色、描边写入 SVG，以便 Qt 离线绘制。软件绘制实时高亮、摇杆轨迹和扳机反馈；这些输入来自本机 SDL。文件保留在安装包中，运行时不请求网页。

| 文件 | 原页面 | 对应图形 |
| --- | --- | --- |
| reference_xbox.svg | https://www.gamepadtester.cn/xbox-controller-test | Xbox Controller |
| reference_playstation.svg | https://www.gamepadtester.cn/ps5-controller-test | PS5 DualSense |
| reference_switch.svg | https://www.gamepadtester.cn/switch-joycon-test | Switch Joy-Con 双侧 |

提取日期：2026-09-24。Switch Pro 页面借用 Joy-Con 按键位置示意图，外形不代表 Switch Pro。PlayStation 参考图的可见范围作裁切，原始路径形状不变。Xbox SVG 的六个文字节点改为 Qt 原生文字绘制，因为 Qt SVG 在离线环境中无法正确还原其网页字体。请在公开分发前核对原站的素材授权。

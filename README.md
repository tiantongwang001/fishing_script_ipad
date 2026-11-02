# Fishing Script for iPad

通过本地 Mac、Appium XCUITest 和 USB 连接的真实 iPad，执行有轮数与时间上限的 Fishing Superstars 固定触摸流程。

**[零基础中文部署与使用指南 → README_CN.md](README_CN.md)**

- 手动启动入口：`run_fishing.py`，可配置轮数、收轮次数、按压时长、提竿等待和修理间隔。
- 预览：`python3 run_fishing.py --preview`；不带该参数会直接执行触摸。
- 依赖：Python 3.11+、Mac/Xcode、Appium、XCUITest及接收方自行签名的WDA。
- 当前坐标针对1366×1024逻辑尺寸；新设备必须重新核对并验证。
- 固定脚本不识别鱼漂、钓获或未知弹窗，不保证钓鱼成功。

## 维护

见 [贡献说明](CONTRIBUTING.md)。GitHub Actions只运行离线测试，不操作设备。本机配置、签名、游戏截图及历史运行记录均不随仓库分发。

本仓库暂未指定开源许可证。

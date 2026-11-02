# 参与维护

部署及使用见 [中文指南](README_CN.md)。开发环境使用Python 3.11或以上：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
```

测试使用合成数据和模拟驱动，不连接iPad。GitHub Actions仅执行离线测试；通过CI不代表真实设备的触摸或钓鱼成功。

修改前新建分支，修改后提交Pull Request，说明问题、行为变化和验证结果。涉及按钮位置、时序、Appium连接的改动需要特别注明真实设备是否测试；不得在CI中调用真实设备命令或运行run_fishing.py的默认执行模式。

不要提交config.json、runs/、dist/、.venv/、签名、账号信息或设备标识。不要在Issue中直接粘贴未经脱敏的日志。截图中的账号和个人信息也需要处理。

本项目不重置或重装目标游戏，不包含WDA签名或游戏数据。所有真实触摸测试必须先获得设备使用者对位置和范围的授权，并设置轮数或时间上限。

本项目采用 [MIT 许可证](LICENSE)，允许使用、修改、商业使用和再分发；须保留原版权声明及许可证文本。建议在衍生项目中注明来源：[fishing_script_ipad](https://github.com/tiantongwang001/fishing_script_ipad)，来源链接是推荐署名方式，不是额外许可条件。

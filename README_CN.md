# Fishing Superstars：零基础 iPad 自动钓鱼部署指南

这份指南适合不懂编程、但能与 GPT 简单交流的用户。目标是在自己的 Mac 上，通过 USB 控制真实 iPad，手动启动 Python 文件后执行有限轮数的固定钓鱼动作。

**第一次部署先完成连接和截图，再测试一轮；不要解压后立即点击运行。** 本项目不是 iPad App，也不是安卓脚本，不能直接在 iPad、Windows 或云端 GPT 中运行。

## 1. 你需要准备什么

| 物品或软件 | 用途 |
|---|---|
| 一台 Mac | 运行脚本和 Apple 开发工具 |
| 一台安装好 Fishing Superstars 的 iPad | 实际运行游戏，账号由你自己登录 |
| 能传输数据的 USB 线 | 将 iPad 连接到 Mac |
| Apple Account | 给控制程序 WDA 签名；免费 Personal Team 可使用，但需完成实际签名验证 |
| Xcode | Apple 提供的开发工具，可从 Mac App Store 获取 |
| Python 3.11 或以上 | 运行本项目 |
| Node.js、Appium 和 XCUITest 驱动 | 建立 Mac 到 iPad 的控制连接 |
| GPT | 帮你检查环境、解释错误和填写配置 |

原设备使用 Apple 芯片 Mac、Appium 3、XCUITest 12.22.8。新设备应先检查兼容性，不要为了照抄版本而降级或重复安装已有工具。

**两种 GPT 使用方式：** 如果 GPT 能在本地项目中读取文件、执行终端命令，可以让它直接协助部署；如果只是网页版聊天，它无法替你操作 Mac，需要你复制它提供的命令并回传结果。这里的“让 GPT 执行”只适用于前一种情况。

## 2. 解压并打开项目

1. 双击压缩包解压，将文件夹放到容易找到的位置，例如“下载”。不要直接在压缩包预览中运行。
2. 打开文件夹，应能看到 `run_fishing.py`、`pyproject.toml`、`config.example.json`、`clicker_recipe.json` 和 `src`。
3. 如果使用 VS Code，选择“文件 → 打开文件夹”，选择这个文件夹，再选择“终端 → 新建终端”。
4. 如果使用 Mac“终端”App，输入 `cd `（末尾有空格），把解压后的文件夹从 Finder 拖进终端，然后按回车。

下面命令均在这个文件夹中运行。复制代码框内的内容，不要复制代码框符号；每完成一步再继续，不要一次执行所有步骤。

## 3. 发给 GPT 的首次部署提示词

复制以下文字给能访问本地项目的 GPT：

> 请按 README_CN.md 帮我部署这个项目。我不懂编程，请逐步操作，解释每一步的结果。先确认运行在我的本地 Mac，并检查 Python、Node.js、Xcode、Appium、XCUITest 和 USB iPad。复用已有工具，只在缺少时提出安装方案。先读取项目源码和 config.example.json，不要猜设备标识或游戏 bundle ID。保留游戏数据，不清除、重装或重置游戏。免费 Apple Personal Team 的 WDA 签名需要按实际日志排查。遇到 Apple 登录、设备信任或开发者模式，只告诉我当下需要做的具体操作；不要索取密码和验证码。先完成真实连接、游戏识别和截图，任何触摸测试都先让我确认坐标。不要直接启动钓鱼循环，也不要把执行成功当成钓鱼成功。日志只保存在本机 runs/。

如果只有网页版 GPT，再加一句：

> 你不能直接操作我的电脑，请每次只给我当前一步需要复制的命令，并等我反馈结果再继续。

### 环境检查的完成标准

GPT 应检查 Xcode 能使用、Appium 驱动已安装、iPad 能被 Xcode 识别。如果提示 `command not found`，说明某个工具没安装或找不到路径；请把完整报错交给 GPT，不要随意重复安装整套工具。

Xcode 首次打开可能要求下载组件或同意许可，请按 Apple 界面完成。缺少 Python、Node.js 或 Appium 时，由 GPT 根据你的 Mac 和当前版本选择安装方法。这些软件不包含在分发压缩包中。

## 4. 连接 iPad：需要你亲手完成的部分

1. 用 USB 连接 Mac，解锁 iPad。
2. 如果 iPad 提示“要信任此电脑吗”，选择“信任”，在 iPad 上输入锁屏密码。
3. 如果系统要求开发者模式，进入 iPad“设置 → 隐私与安全性 → 开发者模式”，按提示启用、重新启动并确认，然后再次解锁。
4. 如果暂时找不到开发者模式，让 GPT 检查 Xcode 的设备配对状态，不要重置 iPad。

每完成一个要求，回复 GPT“已完成”。开发者模式说明见 [Apple 官方文档](https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device)。

## 5. 配置 WDA：让 Mac 获得触摸控制能力

WDA（WebDriverAgent）是运行在 iPad 上的测试控制程序，不是游戏插件。它需要用你自己的 Apple Account 签名，别人的签名和设备配置不能直接沿用。

让 GPT 先检查有没有已经可用的 WDA。若需要首次签名，通常由 GPT 打开正确工程，你在 Xcode 中完成：

1. 在 Xcode 的 Settings → Accounts 登录自己的 Apple Account，密码和验证码仅输入 Apple 界面。
2. 在 WebDriverAgent 工程中选择 `WebDriverAgentRunner`，打开 `Signing & Capabilities`。
3. 按实际配置选择自动签名及你的 Personal Team；需要时由 GPT 帮你确定可用的唯一 Bundle Identifier。
4. 选择真实 iPad 作为运行目标，并按 GPT 指示执行 Product → Test。
5. 如果 iPad 提示不受信任的开发者，在“设置 → 通用 → VPN 与设备管理”中找到对应开发者并信任，再通知 GPT 继续。

以实际日志为准；看见 WDA 图标不等于成功，应能建立真实 Appium 会话。免费账号的签名后续可能需要重新签署。签名失败时不必重装游戏。

参考：[Appium 12.22 手动签名配置](https://appium.github.io/appium-xcuitest-driver/12.22/getting-started/provisioning-profile/full-manual-config/)、[已安装 WDA 的使用方式](https://appium.github.io/appium-xcuitest-driver/12.22/guides/run-preinstalled-wda/)。

## 6. 安装本项目的 Python 环境

如果 GPT 已完成这一节，不需要重复做。否则在项目终端逐条运行：

```sh
python3 -m venv .venv
```

```sh
.venv/bin/python -m pip install -e .
```

`.venv` 是本项目专用的 Python 环境，第一次安装依赖需要网络。命令结束且没有报错后继续。不要从别人的电脑复制 `.venv`。

仅在项目没有 `config.json` 时，复制示例配置：

```sh
cp -n config.example.json config.json
```

让 GPT 根据实际检测结果填写 `config.json`，不要把下面说明文字原样填入字段：

| 字段 | 应填写什么 |
|---|---|
| `udid` | 你的 iPad 的真实设备标识，由 Xcode/本地检测读取 |
| `appium_url` | 默认 `http://127.0.0.1:4723`，表示这台 Mac 上的服务 |
| `wda_mode` | `xcode` 或 `preinstalled`，由 GPT 根据已验证的 WDA 启动方式选择 |
| `wda_bundle_id` | 你实际签名的 WDA 标识，不是游戏标识 |
| `game_bundle_id` | 设备上实际查询到的游戏标识；首次检查游戏前可留空 |
| `expected_ipados` | 可记录系统版本；该字段不会自动解决版本兼容问题 |
| `derived_data_path` | 若已有构建需要复用，填已验证的构建目录；否则保留空字符串 |
| `wda_bundle_id_suffix` | 通常保留示例的 `null`；仅按实际 WDA 安装信息修改 |

配置文件是 JSON：保留双引号和逗号，不添加注释。不会修改时让 GPT 代填或检查。`config.json` 包含本机信息，不要放进公开分发包。

## 7. 启动服务并确认能截图

准备两个终端：终端 A 保持 Appium 服务运行；终端 B 操作本项目。两者都可以通过 VS Code 的终端“＋”打开。

如果 GPT 确认没有已有服务，在终端 A 运行：

```sh
appium --address 127.0.0.1 --port 4723
```

终端持续显示日志是正常现象，先不要关闭。若提示端口被占用，让 GPT 检查是否已有 Appium，不要盲目结束其它进程。

在项目目录的终端 B 运行：

```sh
.venv/bin/python -m fishing_probe inspect
```

这个命令会连接真实 iPad、查询游戏、打开游戏并保存截图，不执行游戏触摸。成功时会输出游戏标识、屏幕尺寸和截图路径。请让 GPT 将实际识别的游戏标识写入 `config.json`；`inspect` 不会自动替你保存这个字段。

在 `runs/` 中打开本次生成的 `before.png`，确认确实是你的游戏。若出现多个候选应用或找不到游戏，让 GPT 查看本地检测结果再选择。

**此步完成标准：真实会话建立、游戏正确、截图可打开。** 只有“命令没有报错”还不够。

## 8. 新设备必须先核对按钮位置

当前配方针对横屏 `1366×1024` 逻辑尺寸；原始截图可能是 `2732×2048` 像素，两者不能混用。即使屏幕尺寸相同，界面版本或布局也可能不同。

把以下文字交给 GPT：

> 请根据本机刚保存的截图、实际逻辑尺寸和 clicker_recipe.json，核对 CAST、HIT、鱼轮上半部分、弹窗确认、出售、继续及修理按钮。说明所有触点和按钮边框是否匹配，不要直接沿用原设备的 manually_verified 标记。先提出一个安全触点，等我确认后再测试一次，并对比前后画面。不同分辨率不要只按比例缩放就声称验证通过。

不确定的按钮先不要测试。运行中不要让脚本在角色页、商店、鱼战或未知弹窗上启动。

## 9. 修改钓鱼参数：只改等号右边的数字

用 VS Code 打开 `run_fishing.py`，修改顶部参数，按 `Command+S` 保存。不要使用 Word 编辑，不要删掉变量名。小数使用英文句点，例如 `0.55`。

| 参数 | 含义 | 允许范围或说明 |
|---|---|---|
| `MAX_ROUNDS` | 最多尝试多少轮，成功失败均算尝试 | 整数 1–1000；首次建议设为 1 |
| `MAX_RUN_SECONDS` | 总输入时限，单位秒 | 1–86400；3600 为一小时；到时可能在半轮停止 |
| `HOOK_WAIT_SECONDS` | CAST 调用返回后，等多久再提竿 | 0–60秒，随抛竿距离和鱼池调整 |
| `REEL_PULSES` | 每轮收轮按压次数 | 整数 1–120，不会自动判断是否钓获 |
| `REEL_HOLD_SECONDS` | 每次鱼轮上半部分按住多久 | 0.02–1秒；能通过校验不等于游戏中安全 |
| `REEL_RELEASE_SECONDS` | 每次按压返回后的额外松开等待 | 0–60秒；0仍然会松开，不等于持续按住 |
| `AUTO_REPAIR` | 每多少轮尝试修理一次 | 整数 0–1000；0关闭，不要写 True/False |
| `CONTINUE_TO_CAST_SECONDS` | 继续调用返回到开始CAST的总目标间隔 | 0–60秒，包含修理耗时；0表示不补等待 |
| `REPAIR_STEP_WAIT_SECONDS` | 每个修理操作返回后的等待 | 0–60秒；太短可能漏掉尚未出现的弹窗 |

文件实际数值为准，不要以旧注释中的次数为准。原设备曾在0.56秒按压时钓获、较长按压时失败，这不构成所有鱼的安全时长保证。只有确认单轮可用后，再增加轮数。参数修改不会改变已经运行中的那一轮程序，需停止后重新启动。

以下额外等待在 `clicker_recipe.json` 的 `settings` 中；不熟悉 JSON 时让 GPT 修改：

| 字段 | 当前基础配方值 | 用途 |
|---|---:|---|
| `initial_delay_seconds` | 0秒 | 第一轮CAST前等待 |
| `next_cycle_delay_seconds` | 0秒 | 后续轮次CAST前等待 |
| `hook_settle_seconds` | 0秒 | 提竿返回后等待 |
| `result_wait_seconds` | 2秒 | 全部收轮完成后等待 |
| `confirm_to_sell_seconds` | 0.6秒 | 弹窗确认到出售 |
| `sell_to_continue_seconds` | 0.6秒 | 普通轮出售到继续 |
| `final_wait_seconds` | 0.6秒 | 最后一轮出售后等待再结束 |

同名设置若由 `run_fishing.py` 覆盖，以启动文件为准。使用显式 `--recipe` 参数时会直接使用指定配方，不应用启动文件顶部参数；新手不需要使用这个选项。

## 10. 先预览，再运行一轮

先把 `MAX_ROUNDS` 改为 `1` 并保存，然后在终端 B 运行：

```sh
python3 run_fishing.py --preview
```

预览只生成计划，不连接 iPad；会在 `runs/` 写入记录。输出中 `NOT_RUN` 表示没有执行，是正常结果。请让 GPT 核对轮数、坐标、等待时间和 `missing_calibration` 是否为空；预览通过不是实机验证。

确认触点和单轮计划后，解锁 iPad，保持游戏横屏，手动进入“失败/继续钓鱼”页或无弹窗 CAST 页。准备好后运行：

```sh
python3 run_fishing.py
```

**没有 `--preview` 就会直接开始触摸。** 首次请观察全过程。发现错位或误操作，立刻在运行脚本的终端 B 按 `Control+C`。不是 Command+C。

程序正常结束后可以查看 `runs/` 中的记录；`PASS` 只表示固定动作计划完成，不代表钓到了鱼。请结合实际画面判断。

## 11. 脚本具体做什么

启动时先在“继续钓鱼”位置点击一次，再进入循环。当前所有普通点击按住0.1秒：

1. CAST抛竿，等待 `HOOK_WAIT_SECONDS`；期间不增加其它触摸。
2. HIT提竿，然后按 `REEL_PULSES` 次数重复：按住鱼轮上半部分 `REEL_HOLD_SECONDS`，松开并额外等 `REEL_RELEASE_SECONDS`。
3. 等2秒，无条件轻触弹窗确认位置，再等0.6秒点击出售。
4. 普通轮出售后等0.6秒点击继续；不修理时按总间隔设置尽快进入下一次CAST。
5. 需要修理的中间轮次，在继续后依次点击“装备修理 → 全部修理 → 确认”，每步后等待 `REPAIR_STEP_WAIT_SECONDS`，无论修理按钮是否出现。修理会使用游戏金币。
6. 最后一轮出售后等0.6秒结束，不再点击继续，也不修理。

所有等待以调用返回后计时，实际触摸间隔还包括检查和串行请求耗时。继续到CAST的目标间隔会扣除已经花在修理等操作上的时间；修理耗时超过目标时不会倒退或压缩操作。

这是固定连点脚本，不识别鱼漂、成功失败、提前钓获或未知弹窗。抛竿距离会影响提竿时机；零额外等待也不能保证最短距离。失败页的“出售”坐标可能实际点击继续，所以失败轮和最后一轮的停留页面不能保证。脚本不自动补能；由你自行安排能量。

## 12. 以后每次如何使用

1. 连接并解锁 iPad，确认游戏界面正确、没有未知弹窗。
2. 确认 Appium 服务运行，且 WDA 签名仍有效。
3. 打开项目终端，检查并保存这次需要的轮数和参数。
4. 运行 `python3 run_fishing.py`；需要停止时在该终端按 Control+C。

结束后可在 Appium 所在的终端 A 按 Control+C 关闭服务。总时限限制后续输入，在途请求和清理可能稍晚结束。重新运行会从第一轮重新计数，不会恢复上次进度。

## 13. 常见问题

| 现象 | 先怎么处理 |
|---|---|
| `can't open file` 或找不到文件 | 检查终端是否在含 run_fishing.py 的解压目录 |
| `.venv` 缺失、`ModuleNotFoundError` | 回到第6节，让GPT检查项目专用环境和依赖 |
| `config.json missing` | 复制示例后填写实际本机信息 |
| `Connection refused` | 检查终端A的Appium服务和配置中的端口 |
| Xcode看不到iPad | 检查数据线、解锁、信任以及Xcode配对状态 |
| WDA签名、开发者不受信任或会话创建失败 | 把对应Appium/Xcode错误交给GPT，检查签名、开发者模式和设备信任 |
| 能截图但按钮位置不对 | 停止，重新核对逻辑尺寸、方向和按钮边框，不要继续循环 |
| 提竿太早或太晚 | 停止后单轮调整抛竿衔接及HOOK_WAIT_SECONDS，不要一次改多个变量 |
| 按住鱼轮后失败 | 区分长按过久、次数不足和错过提竿；看实际画面再调整 |
| 有弹窗没有被关掉 | 停止并保存画面，让GPT先定位，不要在未知弹窗上盲点 |
| 运行提前结束 | 查看是否到轮数/时限、被取消、前台切换或尺寸改变；查看本次日志 |

向GPT求助可以这样说：

> 我正在README第几节，执行的命令是……，看到的完整报错是……。请先分析本地runs中的相关日志，只修复当前问题，不重装或清除游戏。需要我在iPad或Xcode上操作时，请告诉我具体按钮。

本地GPT可以直接读取日志；向网页聊天发送内容前，隐藏设备标识、账号和个人路径。Apple密码、验证码和签名私钥不要发到聊天中。

## 14. 文件与分发说明

- `run_fishing.py`：手动启动入口和常用参数。
- `clicker_recipe.json`：按钮位置、几何检查和基础动作等待。
- `config.example.json`：空白配置模板；自己的配置保存为 `config.json`。
- `src/fishing_probe/`、`pyproject.toml`：运行代码和Python依赖声明，不必手动修改。
- `runs/`：运行时自动生成的日志、计划和检查截图。

最小分发包不包含 `.venv`、本机 `config.json`、账号签名、WDA安装包、游戏或历史运行截图。接收者必须在自己的Mac上配置并验证。完整开发项目中还可能有测试、STATUS.md和历史资料；它们不是最小运行包的必要文件。

本指南说明部署方法，不代表任何新设备已验证。请按“环境正常 → 真机连接 → 游戏截图正确 → 单次触点验证 → 单轮观察 → 有限循环”的顺序完成部署。

## 15. 许可与来源署名

本项目采用 [MIT 许可证](LICENSE)，允许使用、修改、商业使用和再分发；须保留原版权声明及许可证文本。建议在衍生项目中注明来源：[fishing_script_ipad](https://github.com/tiantongwang001/fishing_script_ipad)，来源链接是推荐署名方式，不是额外许可条件。

分发本项目代码时请同时附上LICENSE文件。本许可只覆盖本项目有权授权的内容，不改变游戏、Apple/Appium等第三方内容各自的权利和许可。

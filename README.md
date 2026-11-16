# 积分活动管理助手

一个**独立开发、非官方**的桌面小工具，用 Python + Qt 在本机查看 Microsoft Rewards 的积分、每日任务与历史记录，并设置提醒。

> **非官方声明**：本项目不是 Microsoft 产品，未经 Microsoft 授权、赞助、认证或认可。文中出现的 Microsoft、Bing、Microsoft Edge、Microsoft Rewards 仅用于说明兼容环境，相关商标归其权利人所有。



| 主页 | 设置页 | 深色主题 |
| --- | --- | --- |
| ![主页](docs/previews/home.png) | ![设置页](docs/previews/settings_full.png) | ![深色主题](docs/previews/dark.png) |

---

## 它能做什么

- **只读查看**：解析 `/dashboard` 与 `/earn` 两页，显示可用积分、今日积分、可领取积分，以及搜索 / 每日活动 / 日常任务的完成进度。
- **本地记录**：积分历史、积分赚取日历（日 / 周 / 月，只显示有积分的时间）、运行日志与网页快照导出。
- **提醒与托盘**：定时计划（最多 10 个时间点）、开机自启、托盘四项快捷操作。
- **界面**：浅色 / 深色 / 跟随系统、中文 / English / 跟随系统、动画常开，不弹鼠标悬浮提示。

**它不做什么**：不代填密码、不读取或导出 Cookie、不自动领取积分、不把「没读到」写成 0 分。首次登录由你本人在弹出的 Edge 窗口完成（含 MFA）。

> ⚠️ 项目还包含一个**实验性的自动完成任务链路**（打开浏览器、点击活动卡、执行电脑端搜索）。Microsoft Rewards 条款要求奖励搜索由用户本人手动进行，机器人 / 宏产生的搜索不属于合格搜索。**不要依赖这部分功能获取积分**，详见[合规与使用风险](#合规与使用风险)。

---

## 快速开始

环境：Python 3.10+、带图形桌面的 Windows / macOS / Linux，已安装 [Microsoft Edge](https://www.microsoft.com/edge)。

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python -m rewards_assistant
```

运行时依赖只有三个：`PySide6`、`requests`、`playwright`。不需要 QtWebEngine，也不需要浏览器扩展。

**打包构建建议用 Python 3.12 或 3.13**，这是 Qt for Python 与 PyInstaller 回归最充分的组合。`requirements.txt` 已按 `python_version` 分段：3.10~3.13 装经过完整回归的 PySide6 6.9.x（`<6.10`），3.14 装 6.10.1+，3.15+ 装 6.12+。早期版本把上界写死为 `<6.10`，在 Python 3.15 上会报 `No matching distribution found for PySide6`——因为 6.9.x 的 wheel 不支持 3.14+。若你仍在用旧清单，重新解压最新源码包即可。

首次启动后，点击右上角设置 → **打开浏览器登录**，在应用弹出的独立 Edge 窗口中完成登录，再点「我已完成登录」。会话保存在应用自己的 `browser_profile/` 中，与你日常使用的 Edge 配置互不影响；窗口不会自动关闭，登录期间可以慢慢操作。

`--widgets` 与 `--classic` 只是兼容别名，和默认命令完全等价——本版本只有一种界面。

---

## 界面与设置

设置页保持精简：**10 个可见设置**，不常改或不建议改的项目直接固定为推荐默认值。

| 分组 | 内容 |
| --- | --- |
| 外观 | 语言、主题 |
| 同步与桌面 | 自动刷新、任务提醒、后台与通知、开机自启 |
| 定时运行 | 总开关、时间胶囊（`+` 从 08:00 起按 30 分钟自动补档） |
| 积分日历 | 日（一排七个）/ 周（一排一周，含 7 个圆点）/ 月；只显示有积分的时间，无记录的未来与过去都不显示。汇总随视图切换为日均 / 周均 / 月均 |
| 拟人搜索默认设置 | **拟人强度 低 / 中 / 高**，统一控制 5 项参数 |

「拟人强度」不是摆设：切换档位会一次写齐经典 `SearchPanel` 的真实执行参数，自动完成任务的默认搜索次数为 **12 次**（中档）。

| 展示项 | 低 | 中（默认） | 高 |
| --- | --- | --- | --- |
| 搜索轮数 | 8 轮 | 12 轮 | 16 轮 |
| 单轮耗时 | 10–18 秒 | 16–29 秒 | 24–40 秒 |
| 回车提交 | 35% | 50% | 70% |
| 结果点击 | 20% | 35% | 55% |
| 滚动屏数 | 0.6 屏 | 1.2 屏 | 2 屏 |

托盘菜单只有四项：**显示主页面 / 立即同步 / 自动完成任务 / 退出**。日志导出、网页快照导出、恢复默认收在设置页底部一行。

更细的架构与数据流见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

---

## 工作原理（简述）

```
登录：LoginDialog ──> 可见 Edge 窗口（本人登录 / MFA）
        └─> session.probe() 五态：已登录 / 未登录 / 部分可用 / 暂时性网络错误 / 未知

同步：SyncWorker ──> sync_once() ──> session.probe() 只读 GET /dashboard + /earn
        └─> readonly_report.build_report()  ──> 三项积分 + 任务分组 + 搜索进度
              └─> storage：页面显示值优先，失败保留上次快照

自动完成（主按钮 / 托盘 / 快捷面板）：
        Application.run_pending_tasks()  ──> SyncWorker / ActivityWorker / SearchRunner
        运行期：禁止同步、主按钮与托盘改「停止」、不做任何官方接口核验
        整轮结束：统一同步一次 → 刷新积分与任务状态
```

几个刻意的设计选择：

- 三项积分取**页面显示值**，「卡片标签 → 相邻数值」优先、RSC 字段兜底，**绝不用任务合计反推**；读不到显示 `--`，不写 0。
- 识别登录页 / 公开「关于」页直接判定为未登录，不写假的 0 分；429 / 5xx / 超时归类为「网络错误」，不会诱导重新登录。
- 搜索进度读 `pointsCounters`；读不到显示「未知」，不伪造「已满」。官方每日搜索上限按会员等级为 15 / 30 / 60 分。
- 只有两页都完整可用时才替换当日任务集合；否则保留上次快照并标记 stale。

登录与同步链路只做两页 GET，不 POST、不注入脚本、不点击活动卡、不代领积分。

---

## 项目结构

```
rewards_assistant/
├── __main__.py / app.py          入口与唯一控制器（托盘、调度、同步编排）
├── session.py / auth.py          浏览器会话、五态探测、只读门面
├── readonly_report.py           两页 HTML 解析（零依赖纯函数）
├── rewards_client.py / storage.py / points.py   任务结构与本地 SQLite
├── sync.py / notifier.py / autostart.py        桌面集成
├── ui/modern/                   唯一 Qt Widgets 界面
├── ui/legacy.py                 经典「自动完成未完成任务」执行内核
├── ui/login_dialog.py           登录对话框
└── api.py · browser.py · human.py · ...      实验性浏览器链路（1.3.0 沿用）
tests/                           离线单元测试（不访问网络）
tools/render_previews.py         离屏渲染界面预览图
docs/previews/                   界面截图
```

---

## 依赖

| 文件 | 用途 |
| --- | --- |
| `requirements.txt` | 运行时：`PySide6>=6.6,<6.10`（Python < 3.14）/ `PySide6>=6.10.1`（Python ≥ 3.14）、`requests>=2.28`、`playwright>=1.40` |
| `requirements-build.txt` | 在运行时依赖之上加 `pyinstaller>=6.0` |
| `pytest` | 仅测试时单独安装，不进运行时 |

`PySide6` 用标准发行包；打包时由 PyInstaller 的 Qt hook 按实际 import 图收集模块，不会把 QtWebEngine 等扩展打进安装包。

---

## 构建安装包

**Debian / Ubuntu**（需 `dpkg-dev`，建议装 `python3-venv`；缺失时脚本会自动回退系统解释器）：

```bash
chmod +x build_deb.sh build_ubuntu.sh
./build_deb.sh                    # PyInstaller 打包 + dpkg-deb 封装，一步完成
sudo apt install ./deb-dist/*.deb
```

已有 `dist/MSRewardsAssistant/` 时可只重新封装：`./build_deb.sh --package-only`。构建脚本会检查产物完整性，避免把被中断的构建封成残缺包。`Depends` 已覆盖 Qt xcb 平台插件所需的系统库，并兼容 Ubuntu 24.04 的 `t64` 包名。

**Windows**：运行 `build_windows.bat`，同样使用 `MSRewardsAssistant.spec`，产物在 `dist/MSRewardsAssistant/`。EXE 图标由 spec 内嵌的 `assets/icons/app.ico` 提供（7 种尺寸），重建后资源管理器与任务栏即可显示；源码运行时窗口图标走 `QApplication.setWindowIcon()`，两者互不影响。

自动化链路每次运行都在**全新的唯一浏览器配置快照**里启动，不需要关闭你日常打开的 Edge；上一轮窗口被提前关闭也不会影响下一次。启动若因残留进程退出，会自动换一份全新配置重试，重试失败才给出提示。运行日志只记录 IP 网段，不写完整公网地址。

**自动运行期间的防误操作**：浏览器任务执行时，主页主按钮与托盘第 3 项一起变成「停止自动运行」，点击只做停止（停止请求发出后按钮立即锁定，避免连点）；「立即同步」「刷新」、顶部放大镜和快捷键全部禁用，定时自动同步与定时启动请求也不会插队。运行期间不做任何官方接口核验，整轮结束后统一同步一次并刷新界面；进度与积分以官网为准。

构建脚本会打印实际使用的 Python 版本。常见问题：

- **`No matching distribution found for PySide6`**：说明用的是旧版 `requirements.txt`（锁死 `<6.10`）或 Python 版本过新而镜像未同步对应 wheel。确认清单已按 `python_version` 分段后，换官方源重试：
  ```bat
  .venv-build-win\Scripts\python.exe -m pip install -i https://pypi.org/simple -r requirements-build.txt
  ```
- **想换 Python 版本构建**（例如从 3.15 换到 3.13）：删掉旧虚拟环境再指定解释器，避免复用失败环境：
  ```bat
  rmdir /s /q .venv-build-win
  set PYTHON=py -3.13
  build_windows.bat
  ```
  Linux 侧对应 `PYTHON=python3.13 ./build_ubuntu.sh`。

跨架构构建、不同桌面环境的托盘表现不作保证。

---

## 测试

```bash
python -m pip install pytest
python -m pytest -q tests
```

测试全部离线运行、不访问网络、不需要登录；少数需要真实显示环境的交互用例会在无图形环境下自动跳过。预览图可离线重渲：

```bash
python tools/render_previews.py
```

---

## 本地数据与隐私

- 配置、积分历史、运行日志与浏览器登录态都保存在用户数据目录，Linux 默认为 `~/.local/share/msrewardsassistant/`，可用 `MS_REWARDS_HOME` 覆盖。
- 应用**不把 Cookie 写成明文文件**；旧版遗留的 `cookies.txt` 会在界面提示迁移，「退出登录」会删除它。`browser_profile/` 本身包含登录状态，请视为敏感数据，不要打包或分享；日志中的邮箱 / 长令牌会先脱敏。
- `.gitignore` 已排除凭证、数据库、日志、浏览器目录与构建产物。但忽略规则无法抹掉 Git 历史，公开仓库前请自行检查提交记录；若曾泄露凭证，请在官网重新登录使其失效。
- 程序会直接访问 Microsoft / Bing 页面，热搜关键词等功能还可能访问其他站点与 IP 归属服务，相关请求受目标站点隐私政策约束。

---

## 合规与使用风险

本项目包含与第三方网站交互的实验性功能，不代表已取得服务方许可。

**Microsoft Rewards 条款对奖励搜索有明确限制**：合格搜索须为用户本人出于真实研究目的手动进行的搜索；机器人、宏或其他自动化方式产生的搜索不属于合格搜索，Microsoft 也明确建议不要用程序、机器人或宏辅助 Rewards 搜索。使用自动化功能可能导致无法获得积分、积分作废或账户受限。**请以人工操作和官方要求为准，不要依赖本项目的自动化搜索功能获得积分。**

自动点击活动卡不等于完成活动：部分活动需要答题、领取或其他互动，进度与积分以服务端最终记录为准。页面结构、接口或服务规则变化后功能可能失效。`可领取` 积分必须在 [rewards.bing.com](https://rewards.bing.com) 由用户手动领取（通常为可领取后 30 天内有效）；「今日积分」与任务合计不必相等（周任务、额外奖励分等）。

参考：

- [Microsoft 服务协议](https://www.microsoft.com/en-us/servicesagreement)
- [Microsoft Rewards 搜索限制说明](https://support.microsoft.com/en-us/accounts-billing/rewards/limiting-your-searches-in-microsoft-rewards)
- [Microsoft 商标与品牌使用规范](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks)

以上仅提示已知风险，不构成法律意见，也不保证任何使用方式合规。公开发布、分发二进制或投入实际使用前，请自行核实适用条款与当地法律。

---

## 许可证

**本仓库目前未随附项目级开源许可证。** 公开源码并不自动授予复制、修改、再分发等权利。维护者应在确认全部代码与素材的权利归属后，按实际授权意愿添加 `LICENSE`；在此之前，请勿将本项目描述为已获得某种开源许可。

第三方依赖（PySide6、requests、Playwright 等）各自许可独立适用。发布 `.deb` 或 Windows 二进制前，请核对依赖许可义务，以及应用名称、图标、图片等资源的商标与版权风险。

---

本仓库为社区独立项目。问题反馈请使用 GitHub Issues，**切勿公开账户 Cookie、密码、令牌或个人浏览记录**。

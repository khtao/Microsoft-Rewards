# 积分活动管理助手 2.1

| 主页 | 设置页 | 深色主题 |
| --- | --- | --- |
| ![主页](docs/previews/home.png) | ![设置页](docs/previews/settings_full.png) | ![深色主题](docs/previews/dark.png) |

每天打开一次，今天的积分就都在眼前了。

**积分活动管理助手** 是一个独立开发、非官方的桌面小工具，用 Python + Qt 把 Microsoft Rewards 的积分、每日任务与积分日历收进一个干净的窗口：三项积分取页面显示值，圆环显示今日进度，积分日历只画真正有分的日子。读不到就老实显示 `--`，绝不写一个看起来很确定的 0。

它克制得有点固执：

- **只读**：登录之后同步只发两个 GET，不代填密码、不导出 Cookie、不自动领取积分。首次登录由你本人在弹出的 Edge 窗口完成（含 MFA），凭证加密后只留在本机。
- **本地**：积分记录、设置与运行日志都在自己的数据目录里；每个环境各自独立，多开也不会串号。日志分两层：`run.log` 是日常操作流水，另有一份带完整错误堆栈的 `debug.log`；导出的日志包与网页快照包文件名都带日期时间。
- **顺手**：浅色 / 深色 / 跟随系统、中文 / English、动画常开、不用鼠标悬浮提示；定时运行最多 10 个时间点，托盘里就能一键同步。
- **该手动的手动**：领取积分请到官网自己点。项目里那个实验性的自动完成任务链路只作辅助；Microsoft Rewards 要求奖励搜索由用户本人手动进行，**不要依赖它获取积分**。

Microsoft、Bing、Microsoft Edge 与 Microsoft Rewards 仅用于说明兼容环境，相关商标归其权利人所有。更细的架构、数据流与构建说明见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

---

## 安装

### 直接运行

环境：**Python 3.10+**、带图形桌面的 Windows / macOS / Linux，并已安装 [Microsoft Edge](https://www.microsoft.com/edge)。

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python -m rewards_assistant
```

运行时只有三个依赖：`PySide6-Essentials`、`requests`、`playwright`。首次启动后点右上角设置 → **打开浏览器登录**，登录窗口里的循环动图会把五步流程演一遍；在弹出的 Edge 窗口里登录并完成 MFA，再点「我已完成登录」即可开始同步。

### 安装包

不想装 Python，直接从 [Releases](https://github.com/khtao/Microsoft-Rewards/releases) 下载对应平台的产物：

| 平台 | 产物 | 安装方式 |
| --- | --- | --- |
| Windows | `MSRewardsAssistantSetup.exe` | 双击走安装向导（界面跟随系统语言：中文／英文），可选为所有用户安装，装完可直接打开 |
| Ubuntu / Debian | `ms-rewards-assistant_2.1_amd64.deb` | `sudo apt install ./ms-rewards-assistant_2.1_amd64.deb` |
| macOS | `MSRewardsAssistant_2.1_<arch>.dmg` | 打开后把 .app 拖进 Applications |

也可以自己从源码构建（建议 Python 3.12 / 3.13）：

- Ubuntu / Debian：`./build_deb.sh` → `.deb`
- Windows：`installer\build_inno.bat` → 单文件 `MSRewardsAssistantSetup.exe`
- macOS：`bash build_macos.sh` → `.app` + `.dmg`（需在 macOS 本机运行）

### 多开（可选）

想同时登录不同账号，给进程指定独立环境即可——设置、积分库、凭证与日志互不干扰：

```bash
python -m rewards_assistant --instance work
# 或：MS_REWARDS_INSTANCE=work python -m rewards_assistant
```

`--widgets` / `--classic` 是兼容别名，和默认命令完全一致。

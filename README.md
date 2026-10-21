# 积分活动管理助手

一个**独立开发、非官方**的 Python 桌面项目，用于在本机查看与整理 Microsoft Rewards 活动信息、记录积分变化、设置提醒，并提供部分浏览器操作辅助功能。

> **非官方声明**：本项目不是 Microsoft 产品，未经 Microsoft 授权、赞助、认证或认可，也不代表 Microsoft。文中提及 Microsoft、Bing、Microsoft Edge 和 Microsoft Rewards，仅用于说明所涉及的第三方服务及兼容环境；相关商标归其权利人所有。

## 项目功能

- 在桌面界面查看积分、每日活动和历史记录；支持从对应服务页面同步可用进度。
- 提供任务提醒、历史统计、托盘菜单与本地时间计划（最多 10 个不同启动时间点）。
- 支持打开活动页面及通过浏览器交互辅助定位活动卡片；部分操作需要用户进一步参与。
- 包含实验性的自动化浏览器及搜索功能；**这些功能可能不符合第三方服务规则，不建议用来获取奖励积分**，详见下方的「合规与使用风险」。
- 提供 Python 源码运行方式，以及 Debian/Ubuntu `.deb` 构建脚本。

项目基于 PySide6 构建图形界面，通过 requests 和 Playwright 实现部分页面访问及浏览器交互。实际可用性取决于操作系统、浏览器、网络环境及第三方页面变化。

## 环境要求

- Python **3.10 或更新版本**；建议使用虚拟环境。
- Windows、macOS 或带图形桌面的 Linux（Linux 打包脚本仅适用于 Debian/Ubuntu 等兼容环境）。
- 需要访问相关网页时，应自行安装 Microsoft Edge，并在合适的浏览器会话中登录对应账户。

### 从源码启动

在项目根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m rewards_assistant
```

Windows 上激活虚拟环境可使用 `.venv\Scripts\activate`，然后运行相同的 `python -m ...` 命令。

启动后可在「账户与设置」中管理本地配置。具体的登录数据或账户 Cookie **属于敏感会话凭证**，不应分享给他人、粘贴至 Issue 或提交至仓库。

### 在 Debian/Ubuntu 构建 DEB

在对应 Linux 机器中安装构建工具，然后执行：

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip dpkg-dev
chmod +x build_deb.sh
./build_deb.sh
```

构建产物保存在 `deb-dist/`。安装时在确认文件来源和包内容后执行：

```bash
sudo apt install ./deb-dist/*.deb
```

如已用当前代码成功执行 `build_ubuntu.sh`，可运行 `./build_deb.sh --package-only` 进行封装。**跨架构构建、图形界面运行和不同桌面环境中的托盘显示不作保证**。

## 本地数据与隐私

- 配置、积分历史、运行日志及可能包含登录状态的浏览器配置目录会保存在**当前用户的数据目录**。默认 Linux 路径为 `~/.local/share/msrewardsassistant/`；可用 `MS_REWARDS_HOME` 环境变量自定义。
- 项目目前将手动提供的 Cookie 保存在本地 `cookies.txt`，**并未使用系统凭证保险库加密**。在支持的平台上会尝试限制文件读取权限，但不能将其视为加密保护。
- 程序会直接访问 Microsoft/Bing 页面或接口；部分关键词与网络环境功能还可能访问其他网站及 IP 位置服务。网络请求将受目标网站及网络服务自身的隐私政策约束。
- `.gitignore` 已排除常见凭证、数据库、日志、浏览器用户目录和构建文件，但忽略规则**不能阻止已提交内容出现在 Git 历史中**。公开仓库前请先自行检查全部提交记录；如曾泄露 Cookie，请在相应服务退出会话或重新登录以使旧凭证失效。

## 合规与使用风险

本项目存在与第三方网站交互的实验性功能，不代表使用它们已经取得相应服务方许可。

**Microsoft Rewards 的适用条款对奖励搜索有明确限制**：奖励搜索要求为用户本人出于真实研究目的手动进行的搜索；机器人、宏或其他自动化方式产生的搜索不属于合格搜索。Microsoft 还明确建议不要使用程序、机器人或宏辅助 Rewards 搜索。使用这些自动化功能可能导致无法获得积分、积分作废或账户受限。因此，**不要依赖本项目的自动化搜索功能获得 Rewards 积分；以人工操作和官方要求为准**。

自动点击活动卡片不等于完成活动，也不保证获得积分。某些活动需要答题、领取或其他实际互动；进度及积分以服务端最终记录为准。页面结构、第三方接口或服务规则变化后，部分功能可能失效。

有关要求请直接查阅：

- [Microsoft 服务协议](https://www.microsoft.com/en-us/servicesagreement)
- [Microsoft Rewards 搜索限制说明](https://support.microsoft.com/en-us/accounts-billing/rewards/limiting-your-searches-in-microsoft-rewards)
- [Microsoft 商标与品牌使用规范](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks)

上述说明仅用于提示已知风险，**不构成法律意见，也不保证项目或任何特定使用方式合规**。在公开发布、分发二进制软件或投入实际使用前，请自行核实适用服务条款及当地法律。

## 测试

```bash
python -m pip install pytest
python -m pytest -q tests
```

自动化单元测试不能替代在实际 Linux 桌面、Edge 登录环境和第三方页面上的端到端测试。遇到网络接口、页面或托盘兼容性问题时，请优先检查实际运行环境与日志（分享日志前移除任何身份或会话信息）。

## 许可与第三方组件

**本仓库目前未随附项目级开源许可证。** 公开源码并不自动授予复制、修改、再分发等权利。仓库维护者应在确认所有代码及素材的权利归属后，按实际授权意愿另行添加 `LICENSE`；在此之前，请勿将本项目描述为已获得某种开源许可。

项目依赖 PySide6、requests、Playwright 等第三方软件，其各自的许可协议独立适用。公开发布 `.deb` 等二进制安装包前，请核对依赖的许可义务，以及应用名称、图标、图片和其它资源的商标或版权风险。

---

本仓库为社区独立项目；问题反馈请使用 GitHub Issues，**切勿公开账户 Cookie、密码、令牌或个人浏览记录**。

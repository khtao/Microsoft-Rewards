# 如何验证 2.0 安装包能在「干净系统」正常运行

> 本文是**开发者验证工具**，不随 `MSRewardsAssistantSetup.exe` 分发。
> 配套文件：`verify_setup.ps1`（自动检查）、`clean_sandbox.wsb`（沙箱配置）。

## 先说最重要的一条原则

**绝对不要在开发机上验证。** 开发机装了 Python、PyInstaller、Inno Setup、VC++ 运行库、可能还有 Qt / Playwright 浏览器 —— 这些会**掩盖缺失依赖**。PyInstaller onedir 最容易出的故障恰恰是「少一个 `_internal` 里的 DLL」，而这种故障只在没有这些运行库的机器上暴露。

「干净系统」的判定标准：`python` 不在 PATH、没有 Inno Setup、没有 VC++ 2015-2022 Redistributable、没有本程序的任何安装痕迹、没有 `dist\` 目录。`verify_setup.ps1 -Mode Pre` 会自动把这几项打出来。

同时要把**构建期依赖**和**最终用户依赖**分开看：Inno Setup 只存在于打包机；干净机里不能也不需要它，装完能跑才证明 exe 是自包含的。

## 三条路线

| 路线 | 适用场景 | 说明 |
| --- | --- | --- |
| **A. 虚拟机快照（推荐）** | 你已有 VMware / VirtualBox | 纯净 Win10/11 x64 虚拟机 → 打快照 `Clean` → 每次测完恢复快照。最贴近真实终端，可自由建标准用户、断网 |
| **B. Windows Sandbox** | 主机是 Win10/11 **Pro / Enterprise / Education** | 每次启动都是全新 Windows，关闭即全部丢弃，无需维护 VM。用随附的 `clean_sandbox.wsb`（已禁用网络、只读映射）。Home 版不支持；虚拟机里需嵌套虚拟化 |
| **C. 新建标准用户账户** | 只想 2 分钟冒烟测试 | 成本最低，能验证「无 UAC / per-user 隔离」，**不能**验证缺运行库，不算严格意义的干净系统 |

> 路线 B 的前提：BIOS 开启虚拟化、1903+、至少 4GB RAM；PowerShell 用 `Enable-WindowsOptionalFeature -FeatureName "Containers-DisposableClientVM" -All -Online` 开启。参考 [Microsoft Learn: Windows Sandbox](https://learn.microsoft.com/windows/security/application-security/application-isolation/windows-sandbox)。

## 准备（每次验证前）

1. **只拷 2 个文件**进干净机：`MSRewardsAssistantSetup.exe` + `verify_setup.ps1`（可选再加 `dist_manifest.json`）。
   **不要**拷 `dist\`、`assets\`、`installer\setup.iss`、Inno Setup、Python —— 拷了就等于自带依赖，验证失效。
2. **复制到虚拟机本地磁盘**（如桌面），不要从 `\\vmware-host\Shared Folders\...` 直接双击。共享文件夹会引入网络重定向器，也会带来 Web 锁定标记。
3. **解除 Web 锁定**，否则 SmartScreen 会拦下未签名 exe，看起来像「安装包坏了」：

   ```powershell
   Unblock-File .\MSRewardsAssistantSetup.exe
   ```

   或者右键 → 属性 → 勾选「解除锁定」→ 应用。
4. **断网**（虚拟机断开网卡 / 沙箱 `Networking=Disable`），顺便验证「无需联网」这条约束。
5. **用标准用户（非管理员）登录**。因为 `setup.iss` 用了 `PrivilegesRequired=lowest`，用标准用户才能真正暴露权限问题；管理员账户会把 UAC 相关问题掩盖掉。

## 一次性自动验证（推荐）

在干净机的 PowerShell 里：

```powershell
cd C:\Users\<user>\Desktop\MRA_TEST
powershell -ExecutionPolicy Bypass -NoProfile -File .\verify_setup.ps1 `
    -Mode Full `
    -SetupExe ".\MSRewardsAssistantSetup.exe" `
    -Manifest ".\dist_manifest.json"
```

`-ExecutionPolicy Bypass` 是进程级的，不需要改系统策略、不需要管理员。

`-Mode Full` 自动跑完 5 个阶段：

| 阶段 | 做什么 |
| --- | --- |
| 1 | `Pre`：断言机器干净（无安装目录/图标/卸载项/进程，无 Python、无 Inno Setup） |
| 2 | `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /LOG=...` 静默安装，检查退出码必须为 0 |
| 3 | `Installed`：目录树、关键 DLL、快捷方式指向、HKCU 卸载项、逐文件 SHA256 比对、启动主程序 5 秒不崩 |
| 4 | `unins000.exe /VERYSILENT ...` 静默卸载 |
| 5 | `Uninstalled`：程序目录/图标/开始菜单/卸载项全部消失，**用户数据目录保留** |

结束打印 `PASS / FAIL` 汇总并把 CSV 报告写到 `%TEMP%`；**有任一 FAIL 则脚本退出码 1**。全部通过打印 `VERIFICATION PASSED`。

其他用法：

```powershell
# 只测某个阶段（手工双击安装后用）
powershell -ExecutionPolicy Bypass -File .\verify_setup.ps1 -Mode Installed
powershell -ExecutionPolicy Bypass -File .\verify_setup.ps1 -Mode Uninstalled

# CI 里不想启动 GUI
powershell -ExecutionPolicy Bypass -File .\verify_setup.ps1 -Mode Full -SetupExe .\setup.exe -SkipLaunch

# 构建机生成 dist 清单（在 Windows 项目根目录执行）
powershell -ExecutionPolicy Bypass -File installer\verify_setup.ps1 -Mode Manifest `
    -SourceDir dist\MSRewardsAssistant -ManifestOut C:\MRA_TEST\dist_manifest.json
```

### 为什么值得做 SHA256 清单比对

Inno Setup 的 `recursesubdirs` 会保留目录树，但「装完能启动」不等于「文件一个不少」—— 某个只在特定功能里用到的 Qt 插件丢了，可能要跑几天才发现。构建机导出 `dist` 的「相对路径 + 大小 + SHA256」，干净机安装后逐文件比对，能一次性证明**目录结构和内容完全一致**。脚本会额外列出安装器自己添加的 `unins000.*` / `app.ico`（这些属于预期，不判失败）。

## 手工验证清单（配合图形向导）

自动脚本之外，建议至少手动走一遍真实用户路径，重点看「无 UAC」和界面：

| 检查项 | 通过标准 |
| --- | --- |
| 双击 exe | 出现简体中文向导，**全程不弹 UAC**、不弹黑框 |
| 向导页 | 不问安装目录、不问开始菜单组（`DisableDirPage/DisableProgramGroupPage=yes`） |
| 桌面图标 | 安装完成后桌面出现 `MSRewardsAssistant`，双击能启动 |
| 开始菜单 | 程序组里有主程序 + 卸载项，两个都能点 |
| 程序能跑 | 主界面正常出现，不报 `shiboken6.abi3.dll`/`qwindows.dll` 缺失 |
| 安装目录 | `%LOCALAPPDATA%\Programs\MSRewardsAssistant\` 下 `_internal\` 及子目录齐全 |
| 设置 → 应用 | 能找到 `MSRewardsAssistant 2.0`（`DisplayVersion` 为 `2.0`），点卸载可正常走完 |
| 用户数据 | `%LOCALAPPDATA%\MSRewardsAssistant\` 在卸载后仍在 |
| 断网 | 全程无网络也能装完、能启动 |

再恢复快照，分别补测这几个变体（每个都用干净快照起跑）：

- **覆盖安装**：连续安装两次 → 主程序仍正常，无残留文件。
- **静默无图标**：`/VERYSILENT /TASKS=""` → 桌面不出现图标，开始菜单仍有。
- **自定义目录**：`/VERYSILENT /DIR="D:\MyApp"` → 装到指定路径、快捷方式指向正确。
- **升级识别**：连续安装两次，确认 `AppId` 让它们被识别为同一应用、卸载项稳定。
- **中文/空格路径**：把 exe 放在 `D:\软件 测试\` 下双击，确认 Unicode 路径无问题。

## 退出码与日志

Inno Setup 的退出码（脚本已自动检查）：

| 码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 1 | 初始化失败（包损坏/被隔离/磁盘满） |
| 2 | 用户取消，或在「This will install...」提示上选了否 |
| 3 / 4 | 阶段切换 / 实际安装过程中的致命错误 |
| 5 | 安装中被取消（结果可能不完整） |
| 7 / 8 | 准备安装阶段判定无法继续（8 还需重启） |

任何一个非 0 都算失败。完整定义见 [Setup Exit Codes](https://jrsoftware.org/ishelp/topic_setupexitcodes.htm)。

`/LOG="..."` 生成的安装日志会逐条记录文件释放动作；验证时务必打开看一眼有没有 `Skipping` 或 `Error`。静默参数详见 [Setup Command-Line Parameters](https://jrsoftware.org/ishelp/topic_setupcmdline.htm) 与 [Uninstaller Command-Line Parameters](https://jrsoftware.org/ishelp/topic_uninstcmdline.htm)。

> 如果 `/VERYSILENT` 下仍弹出「This will install...」确认框，加 `/SP-`（脚本已带），或在 `setup.iss` 里加 `DisableStartupPrompt=yes`。

## 常见「假失败」

| 现象 | 其实不是包的问题 |
| --- | --- |
| 双击没反应 / SmartScreen 蓝框 | exe 带 `Zone.Identifier`（从网络、邮件、共享文件夹来的）。`Unblock-File` 或右键解除锁定；未签名包被提醒是正常现象 |
| 安装程序直接消失 | Defender / 杀软隔离。看 Windows 安全中心「保护历史记录」，把文件加白后重测（并考虑后续做代码签名） |
| 从共享文件夹双击异常 | 网络重定向器 + 路径含中文/空格。复制到本地 NTFS 再测 |
| 覆盖安装报文件占用 | 程序还在跑（安装包未设置 `CloseApplications`）。先关掉主程序，或命令行加 `/CLOSEAPPLICATIONS` |
| 管理员账户下「不弹 UAC」 | 用管理员测不出真实情况，必须换标准用户 |
| 开发机上一切正常 | 开发机自带 VC++ / Python / Qt，掩盖了缺依赖。必须换干净机 |

## 判定标准

一次验证算**通过**，需要同时满足：

1. `verify_setup.ps1 -Mode Full` 输出 `VERIFICATION PASSED`（0 个 FAIL），退出码 0；
2. 静默安装退出码 0，`/LOG` 里无错误；
3. 有 manifest 时，`missing=0 / size mismatch=0 / hash mismatch=0`；
4. 图形向导手动走一遍无 UAC、中文正常、三个图标都可用、主程序能启动；
5. 卸载后程序目录/图标/卸载项全清，用户数据目录保留；
6. 断网、标准用户、无 VC++ redist 的机器上全部复现通过。

任何一项不过，把 `%TEMP%\mra_verify_full_*.csv`、`%TEMP%\mra_inno_install.log` 和 `%TEMP%\mra_inno_uninstall.log` 一起留下来分析。

# Windows 安装包（Inno Setup，2.2）

`MSRewardsAssistantSetup.exe` 由 [Inno Setup](https://jrsoftware.org/isinfo.php) 生成，是一个**自带图形安装向导**的单文件 exe：双击 → 向导 → 自动装完。它自动创建桌面图标、开始菜单程序组、卸载程序、卸载注册项（「设置 → 应用」可见），不联网、不需要同目录任何其他文件。版本号与 `config.APP_VERSION` 一致，当前为 **2.2**。

这一版的安装体验做了四件事：

- **语言跟随系统**：按 Windows UI 语言自动显示简体中文（zh-CN）或英文，其它语言一律回落英文；不弹语言选择框，也无需手动切换（临时覆盖可用 `/LANG=`）。自定义文案（完成页「打开」）也一并跟随。
- **可为所有用户安装**：向导提供「为所有用户安装 / 仅为我安装」；默认是当前用户级（不弹 UAC），选所有用户时走标准 UAC 提权，程序装到 Program Files、快捷方式与卸载项写入公共位置。
- **升级先卸载旧版**：检测到同一 AppId 的已安装版本（HKCU / HKLM 都查，含跨安装模式）会先静默卸载再全新安装；用户数据目录 `%LOCALAPPDATA%\MSRewardsAssistant` 由应用自己持有，卸载器不碰，升级 / 覆盖安装都不丢数据。
- **装完可直接打开**：完成页有「打开 MSRewardsAssistant」复选框（默认勾选），静默安装不启动，不干扰无人值守脚本。

图标、快捷方式、目录树、语言、安装模式、卸载、注册表与完成页打开项全部由 Inno Setup 原生处理：一行 `[Files]` 递归打包整个 `dist`；唯一的过程式代码是 `[Code]` 里那段「升级前静默卸载旧版」，不依赖任何外部脚本。

## 依赖边界

- **构建机**：需要安装 **Inno Setup 6.5+（或 7.x）**，免费，从 <https://jrsoftware.org/isdl.php> 下载，装一次即可。它只在**打包时**使用。
- **最终用户机器**：`MSRewardsAssistantSetup.exe` 单文件，**零依赖** —— 不需要 Inno Setup、.NET、VC++ 运行库、Python，也不需要同目录其他文件。
- **项目**：不新增任何 Python 包，`requirements.txt` / `requirements-build.txt` 不变。

生成的 exe 是 Windows 原生 PE，向导文字来自官方 Unicode 语言文件，构建脚本保持纯 ASCII + CRLF、不调用 `chcp`，项目路径含中文或空格也不影响构建。

## 文件

| 文件 | 作用 |
| --- | --- |
| `setup.iss` | Inno Setup 脚本：一个 `[Files]` 递归打包 dist，声明式配置 + 升级卸载 `[Code]` |
| `Messages.en.isl` / `Messages.zh.isl` | 自定义消息的英 / 中文版（UTF-8 无 BOM），随 `setup.iss` 一起参与编译，不随 exe 分发 |
| `build_inno.bat` | 一键构建：定位 ISCC → 检查 dist 与消息文件 → 选语言 → 编译 → 校验产物 |
| `verify_clean_vm.md` | **验证 SOP**：如何在干净系统里验证安装包（开发者用，不随 exe 分发） |
| `verify_setup.ps1` | 干净机自动检查脚本：干净基线 → 静默安装 → 逐文件 SHA256 → 启动 → 静默卸载 → 清理校验 |
| `clean_sandbox.wsb` | Windows Sandbox 配置模板：断网 + 只读映射，每次启动都是全新系统 |
| `README.md` | 本文 |

后三个是**验证工具**，只给开发者用，**不要**放进分发目录 —— 最终用户只需要那一个 exe。

## 前置：装一次 Inno Setup

1. 下载 <https://jrsoftware.org/isdl.php> 的 `innosetup-6.x.exe`（或 7.x），默认下一步装完。
2. 默认路径 `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`，脚本会自动找；装在别处也可以把路径作为参数传进去。
3. 版本要求 **6.x 或 7.x**（6.5+ 推荐）。用到了 `PrivilegesRequired=lowest`、`WizardStyle=modern`、`recursesubdirs`，Inno Setup 5 不支持。

官方 6.x 安装包内置 `Languages\ChineseSimplified.isl`。`[Languages]` 把 english 放在第一位作为回落，`LanguageDetectionMethod=uilanguage` + `ShowLanguageDialog=no` 让向导**跟随系统**：中文系统简体中文、其它系统英文，从不弹语言选择框。脚本会自动检测 `.isl`；精简版没有它时降级成纯英文（任何系统都是英文），不会编译失败。

## 一键生成安装包

在**项目根目录**（`Microsoft-Rewards\`）执行：

```
installer\build_inno.bat
```

五步：

1. 定位 `ISCC.exe`（查 6/7 的常见安装路径、`where iscc`，或你传入的路径）。
2. 检查 `installer\setup.iss`、`assets\icons\app.ico`、`dist\MSRewardsAssistant\MSRewardsAssistant.exe`；缺 dist 就自动调用原有的 `build_windows.bat`。
3. 检测 `ChineseSimplified.isl` 与两个自定义 `.isl`，决定「中英跟随系统」还是「纯英文」向导。
4. 运行 `ISCC` 编译 `setup.iss`，输出到项目根。
5. 校验 `MSRewardsAssistantSetup.exe` 确实生成且大小正常。

之后**只分发这一个 exe**。

### 参数

```
installer\build_inno.bat                          正常构建（中文向导）
installer\build_inno.bat --no-build              缺 dist 时直接报错，不自动编译
installer\build_inno.bat --english              强制英文向导（跳过中文语言文件）
installer\build_inno.bat --quiet                ISCC /Q，只打印错误
installer\build_inno.bat --open                  打开图形化 Inno Setup Compiler
installer\build_inno.bat "D:\Tools\ISCC.exe"    手动指定编译器路径
```

### 图形化构建（可选）

`--open` 会调用 `Compil32.exe`（Inno Setup 自带的图形 IDE）打开 `setup.iss`，按 **Ctrl+F9** 编译。也可以直接在资源管理器里双击 `installer\setup.iss`。

> IDE 直接编译时没有 `/DEnglishOnly=1` 参数，会用「跟随系统」的双语向导；Inno Setup 缺中文语言文件时，改用命令行构建（会自动降级），或加 `--english`。

### CI 自动构建

`.github/workflows/build.yml` 的 `windows` job 完整复现这条链路：`build_windows.bat` 产出 `dist\MSRewardsAssistant\`，用 Chocolatey 安装 Inno Setup，再执行 `installer\build_inno.bat --no-build` 生成 exe。`--no-build` 避免重复编译；**不加 `--quiet`**，ISCC 的编译输出是 CI 里定位语言文件 / `[Files]` 错误的唯一线索。cmd 步骤显式 `if errorlevel 1 exit /b 1`，保证批处理失败不会被吞掉。产物作为 artifact 保留 30 天；推送 `v*` 标签时挂到 GitHub Release。runner 自带或 Chocolatey 装的 Inno Setup 缺中文语言文件时，`setup.iss` 的 `#ifdef EnglishOnly` 会降级成英文向导，不会导致 CI 失败。

## `setup.iss` 做了什么

| 段 | 关键配置 | 效果 |
| --- | --- | --- |
| `[Setup]` | `#define MyAppVersion "2.2"` | `AppVersion` / `VersionInfoVersion` / 向导显示版本统一为 2.2 |
| | `PrivilegesRequired=lowest` + `PrivilegesRequiredOverridesAllowed=dialog` + `UsePreviousPrivileges=no` | 默认当前用户级、**不弹 UAC**；向导提供「为所有用户安装」选项（选它才提权），并自动支持 `/ALLUSERS` `/CURRENTUSER`；每次交互安装都显示该选择，不沿用旧模式 |
| | `DefaultDirName={autopf}\{#MyAppName}` | auto 常量：所有用户 → Program Files，当前用户 → `%LOCALAPPDATA%\Programs`，一条配置覆盖两种模式 |
| | `LanguageDetectionMethod=uilanguage` / `ShowLanguageDialog=no` | 按系统 UI 语言自动选中文 / 英文，不弹语言选择框 |
| | `CloseApplications=yes` | 覆盖 / 升级安装时自动关闭占用文件的运行实例 |
| | `DisableDirPage=yes` / `DisableProgramGroupPage=yes` | 向导不问目录、不问开始菜单组，直接装 |
| | `WizardStyle=modern` / `Compression=lzma2` / `SolidCompression=yes` | 现代向导外观，高压缩单文件 |
| | `SetupIconFile` / `UninstallDisplayIcon` | 安装包与卸载项图标 |
| | 固定 `AppId={{...}` | 重复安装被识别为升级，卸载项稳定 |
| `[Languages]` | `english` 在 `chinesesimplified` **之前** + 各自 `MessagesFile` 叠加自定义 `.isl` | 中文系统中文、其它系统英文；自定义文案同步跟随 |
| `[Tasks]` | `desktopicon`，**无 `unchecked`** | 桌面图标**默认自动创建**，用户可取消 |
| `[Files]` | `Source: "..\dist\MSRewardsAssistant\*"; Flags: ignoreversion recursesubdirs createallsubdirs` | 一行递归打包整个 PyInstaller onedir，**完整保留 `_internal\`、`assets\icons\` 等目录层级** |
| `[InstallDelete]` | 只清 `{app}` | 覆盖安装前清掉残留文件，避免旧 DLL 引发版本冲突；**不碰用户数据目录** |
| `[Icons]` | `{autoprograms}` × 2 + `{autodesktop}` × 1 | 开始菜单程序组（主程序 + 卸载）、桌面图标；按安装模式自动落公共 / 用户位置 |
| `[Run]` | `postinstall nowait skipifsilent` + `{cm:MRA_LaunchApp}` | 完成页「打开」复选框，双语，静默安装不启动 |
| `[Code]` | `InitializeSetup` → 注册表查 HKLM / HKCU `UninstallString` → `RemoveQuotes` + `Exec` 静默卸载（失败回退 `ShellExec('runas')`） | 安装前先卸载旧版，`ewWaitUntilTerminated` 避免卸载与复制文件竞态；只记录 / 核对用户数据目录，从不删除 |

`recursesubdirs` 原生保留相对路径：`_internal\shiboken6.abi3.dll`、`_internal\PySide6\plugins\platforms\qwindows.dll` 这类深层文件直接原样释放到 `{app}` 对应位置，不需要任何中间层。

## 安装行为

| 项目 | 位置 / 行为 |
| --- | --- |
| 向导语言 | 系统 UI = 中文 → 简体中文；其它 → 英文（`ShowLanguageDialog=no`，不弹选择框） |
| 安装模式 | 默认「仅为我安装」（当前用户、不弹 UAC）；向导可选「为所有用户安装」（UAC 提权） |
| 程序文件 | 仅为我：`%LOCALAPPDATA%\Programs\MSRewardsAssistant\`；所有用户：`%ProgramFiles%\MSRewardsAssistant\`（由 `{autopf}` 自动映射） |
| 桌面图标 | 桌面 `\MSRewardsAssistant.lnk`（默认创建；所有用户模式落公共桌面） |
| 开始菜单 | 开始菜单\程序\MSRewardsAssistant\ ：主程序 + `卸载 MSRewardsAssistant` |
| 卸载程序 | `{app}\unins000.exe` + 完整卸载注册项（HKCU 或 HKLM，随安装模式） |
| 完成页 | 「打开 MSRewardsAssistant」复选框（默认勾选；`/SILENT` `/VERYSILENT` 不启动） |

- **升级 / 覆盖安装**：`InitializeSetup` 阶段先静默卸载发现的旧版（HKCU / HKLM 都查），等卸载进程结束再复制新文件；卸载器只清 `{app}`、图标与它自己的注册项，用户数据目录原样保留。跨安装模式（当前用户 ↔ 所有用户）也能正确识别旧版并清掉，不会留两套。
- **用户数据不会被卸载删除**：应用把 Cookie、`rewards.db`、设置写在 `%LOCALAPPDATA%\MSRewardsAssistant`（见 `rewards_assistant/config.py` 的 `base_data_dir()`），与安装目录是两条独立路径。卸载只删除它自己装进 `{app}` 的文件和它创建的图标/注册项，`[InstallDelete]` 也只针对 `{app}`；`[Code]` 会在卸载前后记录该目录状态并写进安装日志。
- Win10 起禁止程序化把图标固定到任务栏，所以「菜单栏图标」按标准做法落在开始菜单程序组。
- `CloseApplications=yes` 会自动关闭正在运行的 `MSRewardsAssistant.exe`；如仍遇到文件占用提示，手动关闭程序后重试即可。

## 卸载

三种方式，效果一致：

- 开始菜单 → `MSRewardsAssistant` → `卸载 MSRewardsAssistant`
- 设置 → 应用 → `MSRewardsAssistant 2.2` → 卸载
- 命令行：`"%LOCALAPPDATA%\Programs\MSRewardsAssistant\unins000.exe"`（所有用户安装时在 `%ProgramFiles%\MSRewardsAssistant\`）

向导本身自带卸载确认页，也可以 `/VERYSILENT` 静默卸载。

## 命令行 / 静默用法

Inno Setup 生成的 exe 自带这套标准参数：

```
MSRewardsAssistantSetup.exe /VERYSILENT /SP- /CURRENTUSER   静默装到当前用户（推荐，抑制安装模式对话框）
MSRewardsAssistantSetup.exe /VERYSILENT /SP- /ALLUSERS     静默为所有用户安装（需管理员权限，触发 UAC）
MSRewardsAssistantSetup.exe /LANG=english                 临时强制英文向导（也可 /LANG=chinesesimplified）
MSRewardsAssistantSetup.exe /SILENT                       显示进度条、无交互
MSRewardsAssistantSetup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-   完整静默
MSRewardsAssistantSetup.exe /VERYSILENT /TASKS=""         静默安装但不建桌面图标
MSRewardsAssistantSetup.exe /LOG="C:\mra_setup.log"     写安装日志
MSRewardsAssistantSetup.exe /NORESTART /NOCANCEL        无人值守常用组合
```

`/VERYSILENT` 单独用仍可能弹「This will install...」启动提示，无人值守时务必带 `/SP-`。启用 `PrivilegesRequiredOverridesAllowed=dialog` 后，静默安装**不弹**安装模式对话框、默认按当前用户安装；显式传 `/CURRENTUSER` 或 `/ALLUSERS` 可完全确定模式，`/LANG=` 可临时覆盖跟随系统的语言。`[Run]` 的 `skipifsilent` 保证 `/SILENT` `/VERYSILENT` 不会启动主程序。退出码 **0 才是成功**，其余都算失败。

卸载：

```
"%LOCALAPPDATA%\Programs\MSRewardsAssistant\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

## 验证安装包能在干净系统运行

**别在开发机上测** —— 开发机自带 Python、Inno Setup、VC++ 运行库，会掩盖缺失依赖。要用虚拟机快照 / Windows Sandbox / 干净的标准用户环境，只拷 `MSRewardsAssistantSetup.exe`（加验证脚本），断网、非管理员、`Unblock-File` 解除 Web 锁定。

最快的一条命令（干净机 PowerShell）：

```powershell
powershell -ExecutionPolicy Bypass -NoProfile -File .\verify_setup.ps1 -Mode Full -SetupExe .\MSRewardsAssistantSetup.exe -Manifest .\dist_manifest.json
```

它自动跑：干净基线 → 静默安装（查退出码 0）→ 目录树/关键 DLL/快捷方式/HKCU 卸载项/逐文件 SHA256/启动 5 秒 → **写数据标记后二次静默安装（验证旧版先卸载、数据保留、不留两套）** → 静默卸载 → 程序与图标全清、用户数据保留。全部通过才打印 `VERIFICATION PASSED`。

完整清单、三种干净环境搭建方式、退出码表、假失败排查见 **[verify_clean_vm.md](verify_clean_vm.md)**。

## 常见改动

| 想改什么 | 改哪里 |
| --- | --- |
| 版本号 | `setup.iss` 顶部 `#define MyAppVersion`（同时作用于 `AppVersion` / `VersionInfoVersion`）；同步修改 `rewards_assistant/config.py` 的 `APP_VERSION` 与 `verify_setup.ps1` 的 `$VersionExpected` |
| 显示名 / 快捷方式名 | `#define MyAppName` |
| 图标 | `SetupIconFile` 与 `[Icons]` 的 `IconFilename` |
| 默认不建桌面图标 | `[Tasks]` 的 `desktopicon` 加 `Flags: unchecked` |
| 允许选安装目录 | 删掉 `DisableDirPage=yes` |
| 装完打开选项默认不勾 | `[Run]` 的 `Flags` 加 `unchecked` |
| 语言跟随系统 | `[Setup]` 的 `LanguageDetectionMethod` / `ShowLanguageDialog`；临时覆盖用 `/LANG=` |
| 为所有用户安装 | `[Setup]` 的 `PrivilegesRequiredOverridesAllowed=dialog`；命令行 `/ALLUSERS` / `/CURRENTUSER` |
| 升级卸载逻辑 | `[Code]` 的 `UninstallOldVersion()` |
| 自定义文案 | `Messages.en.isl` / `Messages.zh.isl` 的 `[CustomMessages]` |
| 压缩更快（包更大） | `Compression=zip`，并去掉 `SolidCompression=yes` |
| 纯英文界面 | 构建时加 `--english`，或给 ISCC 传 `/DEnglishOnly=1` |
| 换 AppId | IDE 里 Tools → Generate GUID，**只换一次**，之后别再改 |

## 排查

| 现象 | 原因 / 处理 |
| --- | --- |
| `[ERROR] ISCC.exe not found` | 没装 Inno Setup，或装在非默认路径。去 jrsoftware.org 装 6.5+，或 `installer\build_inno.bat "D:\...\ISCC.exe"` 指定路径。 |
| `ISCC` 报 `Unknown directive` / `Parameter "PrivilegesRequired" ...` | 装的是 Inno Setup 5。必须 6.x / 7.x。 |
| 找不到 `ChineseSimplified.isl` | 用了精简版或 5.x。脚本会自动降级英文；想中文就装官方完整版 6.5+。 |
| 编译报 `No files found matching "..\dist\MSRewardsAssistant\*"` | `dist` 为空或目录名不对。先跑 `build_windows.bat`，确认 `dist\MSRewardsAssistant\MSRewardsAssistant.exe` 存在。 |
| 编译报 `SetupIconFile` 找不到 | `assets\icons\app.ico` 缺失。从项目包里拷回 `assets\icons\`。 |
| 向导显示英文但想要中文 | 系统 UI 语言非 zh-CN（跟随系统）；临时用 `/LANG=chinesesimplified`，或构建时别加 `--english` |
| 想让所有用户都能用 | 向导选「为所有用户安装」（会弹 UAC）；静默用 `/ALLUSERS` |
| 升级后数据没了 | 不应发生：卸载器只清 `{app}`；检查 `%LOCALAPPDATA%\MSRewardsAssistant`，并看安装日志里的 `User data preserved` |
| 升级时弹 UAC | 旧版是「所有用户」安装（HKLM），卸载它需要提权，属正常 |
| 完成页没有「打开」 | 静默安装 `skipifsilent` 会跳过；交互安装才有该复选框 |
| 中文向导出现方块字 | `setup.iss` 是纯 ASCII，中文来自官方 `ChineseSimplified.isl` 与 UTF-8 无 BOM 的 `Messages.zh.isl`（Inno Setup 6 起是 Unicode 编译器，与系统代码页无关）。出现方块字说明 `.isl` 被第三方改坏了，重装官方版。 |
| 脚本里出现乱码命令 | `build_inno.bat` 是纯 ASCII + CRLF、不调用 `chcp`；项目路径含中文不影响（只用 `%~dp0` / 绝对路径传参）。修改脚本时请保持这三条约束。 |
| 覆盖安装提示文件被占用 | 先关掉正在运行的 `MSRewardsAssistant.exe` 再装。 |
| 编译成功但根目录没有 exe | 检查 `setup.iss` 里的 `OutputDir=..`（相对 `installer\` 就是项目根），以及磁盘剩余空间。 |

**路径兜底**：项目位于 VMware 共享文件夹 / UNC / 含中文空格的路径时，可把整个项目复制到虚拟机本地 `C:\MRA` 再构建，本地 NTFS 最省心：

```powershell
robocopy "\\vmware-host\Shared Folders\...\Microsoft-Rewards" C:\MRA /E
cd C:\MRA
installer\build_inno.bat
```

复制的是已编译好的 `dist`，不用重跑 PyInstaller；生成的 exe 再拷回共享文件夹分发即可。

## 参考

- 官网与下载：<https://jrsoftware.org/isdl.php>
- `[Setup]` `PrivilegesRequired=lowest`：[官方文档](https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm) — Vista+ 不请求提权，卸载信息写 HKCU，`auto` 常量映射为用户级
- `[Files]` `recursesubdirs` / `createallsubdirs`：[官方文档](https://jrsoftware.org/ishelp/topic_filessection.htm)
- 常量 `{localappdata}` / `{autodesktop}` / `{autoprograms}`：[官方文档](https://jrsoftware.org/ishelp/topic_consts.htm)
- `[Setup]` `PrivilegesRequiredOverridesAllowed` / `UsePreviousPrivileges`：[安装模式覆盖](https://jrsoftware.org/ishelp/topic_setup_privilegesrequiredoverridesallowed.htm) / [沿用旧模式](https://jrsoftware.org/ishelp/topic_setup_usepreviousprivileges.htm)
- `[Setup]` `LanguageDetectionMethod` / `ShowLanguageDialog`：[语言检测](https://jrsoftware.org/ishelp/topic_setup_languagedetectionmethod.htm) / [语言对话框](https://jrsoftware.org/ishelp/topic_setup_showlanguagedialog.htm)
- `[Languages]` 多 `MessagesFile` + `[CustomMessages]`：[官方文档](https://jrsoftware.org/ishelp/topic_languagessection.htm)
- `[Run]` `postinstall` / `skipifsilent`：[官方文档](https://jrsoftware.org/ishelp/topic_runsection.htm)
- 常量 `{autopf}` 的 auto 语义：[官方文档](https://jrsoftware.org/ishelp/topic_consts.htm)
- Pascal Script `Exec` / `ShellExec` / `InitializeSetup`：[ShellExec](https://jrsoftware.org/ishelp/topic_isxfunc_shellexec.htm)
- `[Tasks]` `unchecked` 等标志：[官方文档](https://jrsoftware.org/ishelp/topic_taskssection.htm)
- ISCC 命令行 `/D` `/O` `/F` `/Q`：[ISPP 命令行编译器](https://jrsoftware.org/ispphelp/topic_isppcc.htm)
- Unicode / UTF-8 脚本编码说明：[官方文档](https://jrsoftware.org/ispphelp/topic_unicode.htm)

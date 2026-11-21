# Windows 安装包交付状态

## 已完成

- Windows x64 PyInstaller onedir 打包配置：`MSRewardsAssistant.spec`
- Windows 一键构建脚本：`build_windows.bat`
- Inno Setup 安装/卸载脚本：`installer/windows.iss`
- 自动创建桌面和开始菜单快捷方式
- 注册 Windows 标准卸载入口
- 卸载时清理本应用开机自启动注册项
- 默认保留用户数据目录 `%LOCALAPPDATA%\\MSRewardsAssistant`
- GitHub Actions Windows 构建工作流：`.github/workflows/build-windows-installer.yml`

## 本次验证

- 安装构建配置及打包静态测试：20 项通过
- Python 源码编译检查：通过
- ZIP 完整性检查：通过

完整应用测试套件未在本环境执行：当前环境未安装 PySide6，相关测试在收集阶段会因缺少该依赖而无法运行。

## 尚需 Windows 环境执行

当前工作环境为 Linux，无法在此直接完成 Windows PyInstaller 构建和 Inno Setup 编译。因此本项目包不包含已经编译的 `Microsoft-Rewards-Assistant-Setup.exe`。

在 Windows 10/11 x64 上安装 64 位 Python 3.12/3.13 和 Inno Setup 6.3+ 后，解压项目并运行 `build_windows.bat`。成功生成的安装程序位于：

`dist\\installer\\Microsoft-Rewards-Assistant-Setup.exe`

也可以将项目推送到 GitHub，使用 Actions 中的 `Build Windows installer` 工作流构建并下载安装包。

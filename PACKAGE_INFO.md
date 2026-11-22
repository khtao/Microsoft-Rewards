# Microsoft Rewards Assistant — Windows Installer Project Package

This archive contains the complete application source tree and the Windows installer build configuration.

## What is included

- Existing application source, UI, assets, and documentation
- Existing automated tests
- `MSRewardsAssistant.spec` for PyInstaller
- `build_windows.bat` for one-command Windows packaging
- `installer/windows.iss` for the self-contained Inno Setup installer and uninstaller
- `.github/workflows/build-windows-installer.yml` for optional Windows CI builds
- `docs/WINDOWS_INSTALLER.md` with build, install, uninstall, and CI instructions

## Important build status

The project source and installer configuration are complete. This archive does **not** contain a compiled `Microsoft-Rewards-Assistant-Setup.exe`: the current build environment is Linux, while PyInstaller must build the Windows application on Windows and Inno Setup's compiler must produce the Windows installer. The included local build script and GitHub Actions workflow perform those Windows-only steps.

## Build output

After a successful Windows build, the installer is created at:

`dist/installer/Microsoft-Rewards-Assistant-Setup.exe`

The resulting installer embeds the complete PyInstaller application bundle. End-user PCs do not need Python, PySide6, PyInstaller, or Inno Setup. Microsoft Edge must already be installed because the application launches Edge for browser tasks.

## Install and uninstall behavior

- Per-user installation; no administrator rights required by default.
- Desktop shortcut and Start Menu shortcut are created automatically.
- Standard entry appears in Windows Settings → Apps → Installed apps.
- Uninstall removes the application, shortcuts, and this app's startup registry entries.
- User data under `%LOCALAPPDATA%\\MSRewardsAssistant` is intentionally preserved.

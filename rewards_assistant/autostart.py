"""开机自启动：Windows 注册表 / macOS LaunchAgent / Linux autostart。"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List

from .config import (autostart_name, instance_id, is_default_instance,
                     window_title)


def _launch_command() -> List[str]:
    """返回用于自启动的命令行；非默认实例必须带上 --instance。

    否则多个环境会共用同一份自启命令，开机只拉起默认账号，另一个环境
    的定时与提醒永远不运行 —— 这是多开最容易漏掉的一处。
    """
    if getattr(sys, "frozen", False):  # PyInstaller / Nuitka
        cmd = [sys.executable]
    else:
        # 使用包内统一入口；无需额外 run.py，且不依赖当前工作目录。
        entry = Path(__file__).resolve().parent / "__main__.py"
        cmd = [sys.executable, str(entry)]
    if not is_default_instance():
        cmd += ["--instance", instance_id()]
    cmd.append("--minimized")
    return cmd


def _entry_name() -> str:
    """注册表值 / plist / .desktop 都按实例分开，互不覆盖。"""
    return autostart_name()


# --------------------------------------------------------------------- Windows

def _win_set(enabled: bool) -> bool:
    try:
        import winreg  # noqa: PLC0415
    except ImportError:
        return False
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                cmd = " ".join(f'"{c}"' if " " in c else c for c in _launch_command())
                winreg.SetValueEx(key, _entry_name(), 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, _entry_name())
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def _win_get() -> bool:
    try:
        import winreg  # noqa: PLC0415
    except ImportError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            winreg.QueryValueEx(key, _entry_name())
            return True
    except OSError:
        return False


# ----------------------------------------------------------------------- macOS

def _mac_label() -> str:
    return "com.user." + _entry_name().lower()


def _mac_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_mac_label()}.plist"


def _mac_set(enabled: bool) -> bool:
    p = _mac_plist()
    if not enabled:
        p.unlink(missing_ok=True)
        return True
    parts = "".join(f"<string>{c}</string>" for c in _launch_command())
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{_mac_label()}</string>
  <key>ProgramArguments</key><array>{parts}</array>
  <key>RunAtLoad</key><true/>
</dict></plist>
""",
        encoding="utf-8",
    )
    return True


# ----------------------------------------------------------------------- Linux

def _linux_desktop() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "autostart" / f"{_entry_name().lower()}.desktop"


def _linux_set(enabled: bool) -> bool:
    p = _linux_desktop()
    if not enabled:
        p.unlink(missing_ok=True)
        return True
    cmd = " ".join(f'"{c}"' if " " in c else c for c in _launch_command())
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={window_title()}",
        f"Exec={cmd}",
        "Hidden=false",
        "NoDisplay=false",
        "X-GNOME-Autostart-enabled=true",
    ]
    icon = _icon_path()
    if icon:
        lines.append(f"Icon={icon}")
    lines.append("StartupWMClass=MSRewardsAssistant")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def _icon_path() -> str:
    """系统安装版使用主题图标名；便携/源码模式使用绝对 SVG 路径。"""
    if (Path("/usr/share/icons/hicolor/scalable/apps/ms-rewards-assistant.svg").is_file()
            or Path("/usr/share/pixmaps/ms-rewards-assistant.png").is_file()):
        return "ms-rewards-assistant"
    from .ui.icons import icons_dir

    d = icons_dir()
    for name in ("app.svg", "app_256.png", "app_128.png"):
        f = d / name
        if f.is_file():
            return str(f)
    return ""


def set_enabled(enabled: bool) -> bool:
    if sys.platform == "win32":
        return _win_set(enabled)
    if sys.platform == "darwin":
        return _mac_set(enabled)
    return _linux_set(enabled)


def is_enabled() -> bool:
    if sys.platform == "win32":
        return _win_get()
    if sys.platform == "darwin":
        return _mac_plist().exists()
    return _linux_desktop().exists()



"""开机自启动：Windows 注册表 / macOS LaunchAgent / Linux autostart。"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List

from .config import APP_NAME, APP_TITLE


def _launch_command() -> List[str]:
    """返回用于自启动的命令行。打包成 exe 后直接用 exe 本身。"""
    if getattr(sys, "frozen", False):  # PyInstaller / Nuitka
        return [sys.executable, "--minimized"]
    # 使用包内统一入口；无需额外 run.py，且不依赖当前工作目录。
    entry = Path(__file__).resolve().parent / "__main__.py"
    return [sys.executable, str(entry), "--minimized"]


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
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
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
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False


# ----------------------------------------------------------------------- macOS

def _mac_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"com.user.{APP_NAME.lower()}.plist"


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
  <key>Label</key><string>com.user.{APP_NAME.lower()}</string>
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
    return base / "autostart" / f"{APP_NAME.lower()}.desktop"


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
        f"Name={APP_TITLE}",
        f"Exec={cmd}",
        "Hidden=false",
        "NoDisplay=false",
        "X-GNOME-Autostart-enabled=true",
    ]
    icon = _icon_path()
    if icon:
        lines.append(f"Icon={icon}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def _icon_path() -> str:
    """给 .desktop 用的图标路径：优先 SVG，其次 256 PNG。"""
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


def supported() -> bool:
    return True

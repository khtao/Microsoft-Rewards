"""桌面通知。

优先使用系统托盘的气泡通知（Windows 原生通知中心 / macOS 通知中心 /
Linux freedesktop），不可用时回落到命令行工具（notify-send / osascript），
最后回落到日志。所有失败都静默处理，绝不打断主流程。
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Callable, Optional

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QSystemTrayIcon


class Notifier(QObject):
    """统一的通知出口，绑定一个可选的托盘图标。"""

    def __init__(self, tray: Optional[QSystemTrayIcon] = None, logger: Optional[Callable[[str], None]] = None):
        super().__init__()
        self.tray = tray
        self.log = logger or (lambda msg: None)

    def attach(self, tray: QSystemTrayIcon) -> None:
        self.tray = tray

    # ------------------------------------------------------------------ 对外

    def notify(self, title: str, message: str, icon=QSystemTrayIcon.MessageIcon.Information,
               timeout_ms: int = 8000) -> bool:
        """发送一条桌面通知，返回是否真的送达到了系统。"""
        if self.tray is not None:
            try:
                if QSystemTrayIcon.isSystemTrayAvailable() and QSystemTrayIcon.supportsMessages():
                    self.tray.showMessage(title, message, icon, timeout_ms)
                    return True
            except Exception as exc:  # 托盘在多变的环境下容易抛错
                self.log(f"托盘通知失败: {exc}")
        return self._fallback(title, message)

    # ------------------------------------------------------------------ 回落

    def _fallback(self, title: str, message: str) -> bool:
        try:
            if shutil.which("notify-send"):
                subprocess.Popen(
                    ["notify-send", title, message],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return True
            if shutil.which("osascript"):
                script = f'display notification "{message}" with title "{title}"'
                subprocess.Popen(
                    ["osascript", "-e", script],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return True
            if shutil.which("powershell") or shutil.which("powershell.exe"):
                # Windows：Toast 需要应用 ID，这里仅做最保守的兜底
                ps = (
                    "[reflection.assembly]::loadwithpartialname('System.Windows.Forms');"
                    "$n=New-Object System.Windows.Forms.NotifyIcon;"
                    "$n.Icon=[System.Drawing.SystemIcons]::Information;"
                    "$n.BalloonTipTitle='" + title.replace("'", "") + "';"
                    "$n.BalloonTipText='" + message.replace("'", "") + "';"
                    "$n.Visible=$true;$n.ShowBalloonTip(6000);Start-Sleep -s 6;$n.Dispose()"
                )
                subprocess.Popen(
                    ["powershell", "-NoProfile", "-Command", ps],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return True
        except Exception as exc:
            self.log(f"回落通知失败: {exc}")
        self.log(f"[通知] {title} - {message}")
        return False

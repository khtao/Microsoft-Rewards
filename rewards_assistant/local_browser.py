"""通过操作系统的默认浏览器打开任务链接（Windows / Ubuntu / macOS）。

不依赖 Edge/Chrome 的安装位置，不读取浏览器列表，不启动自动搜索。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from urllib.parse import urlsplit


def open_url(url: str) -> bool:
    """在系统默认浏览器打开 HTTP(S) 链接；失败时返回 False。"""
    if not isinstance(url, str):
        return False
    parsed = urlsplit(url.strip())
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return False
    try:
        if sys.platform == "win32":
            os.startfile(url)  # Windows 默认 http/https 关联处理器
        elif sys.platform == "darwin":
            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        else:
            opener = shutil.which("xdg-open")
            if not opener:
                return False
            subprocess.Popen([opener, url], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        return True
    except (OSError, ValueError):
        return False

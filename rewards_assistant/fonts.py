"""字体加载：正常环境用系统字体，打包后从 bundle 里带的那份加载。

为什么需要这个
--------------
打包成可执行程序后，目标机器上不一定装了中文字体（尤其是干净的 Linux 服务器、
精简版 Windows、Docker 里）。一旦没有 CJK 字体，界面中文会全部变成方块/豆腐块
—— 这跟"缺动态库跑不起来"是同一类问题。

策略：
1. 打包时把开源字体放进 <bundle>/fonts
2. 启动时本模块找到这个目录，注册给 Qt
3. 如果系统本来就有中文字体，就优先用系统的，bundle 那份当兜底

图表已改为 Qt 自绘（不再依赖 matplotlib），字体只需要照顾 Qt 一侧。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

_FONT_EXTS = (".ttf", ".otf", ".ttc", ".otc")

_CJK_PREFERRED = [
    "Microsoft YaHei", "微软雅黑", "PingFang SC", "Hiragino Sans GB",
    "Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei",
    "Noto Sans SC", "Heiti SC", "SimHei", "Microsoft JhengHei",
]


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打出来的程序里。"""
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Optional[Path]:
    """打包后的资源根目录。

    --onefile: sys._MEIPASS（临时解压目录）
    --onedir : 可执行文件所在目录
    """
    if not is_frozen():
        return None
    base = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "_MEIPASS", "") else None
    if base is not None:
        return base
    return Path(sys.executable).resolve().parent


def bundled_font_dir() -> Optional[Path]:
    """返回 bundle 里的 fonts 目录（不存在则 None）。"""
    root = bundle_root()
    if root is None:
        return None
    for candidate in (root / "fonts", root / "_internal" / "fonts"):
        if candidate.is_dir():
            return candidate
    return None


def font_files(directory: Path) -> List[Path]:
    if not directory or not directory.is_dir():
        return []
    out: List[Path] = []
    for ext in _FONT_EXTS:
        out.extend(sorted(directory.glob(f"*{ext}")))
    return out


# --------------------------------------------------------------------------- #
# Qt 界面字体
# --------------------------------------------------------------------------- #

def _qt_ready() -> bool:
    """QFontDatabase 必须在**GUI** Application 之后才能用，否则进程直接 abort。

    注意不能只判断 instance() is not None —— QCoreApplication.instance()
    对纯控制台应用也返回对象，但那不是 QGuiApplication，照样会 abort。
    """
    try:
        from PySide6.QtGui import QGuiApplication  # noqa: PLC0415

        inst = QGuiApplication.instance()
        return inst is not None and isinstance(inst, QGuiApplication)
    except Exception:
        return False


def install_for_qt() -> int:
    """把 bundle 里的字体注册进 QFontDatabase，返回成功数量。"""
    if not _qt_ready():
        return 0
    try:
        from PySide6.QtGui import QFontDatabase  # noqa: PLC0415
    except ImportError:
        return 0
    count = 0
    for path in font_files(bundled_font_dir() or Path()):
        try:
            if QFontDatabase.addApplicationFont(str(path)) >= 0:
                count += 1
        except Exception:
            continue
    return count


def pick_qt_font_family() -> str:
    """挑一个系统或 bundle 里真实存在的中文字体名，给 QApplication 兜底用。"""
    if not _qt_ready():
        return ""
    try:
        from PySide6.QtGui import QFontDatabase  # noqa: PLC0415

        available = set(QFontDatabase.families())
    except Exception:
        return ""
    for name in _CJK_PREFERRED:
        if name in available:
            return name
    for fam in sorted(available):
        if any(k in fam for k in ("CJK", "Han", "Hei", "YaHei", "Song", "WenQuanYi")):
            return fam
    return ""


def bootstrap() -> dict:
    """启动时调用一次：装好 Qt 字体。

    返回统计信息，方便在日志里确认字体真的带上了。
    """
    info = {
        "frozen": is_frozen(),
        "bundle_fonts": 0,
        "qt_registered": 0,
        "qt_family": "",
    }
    d = bundled_font_dir()
    info["bundle_fonts"] = len(font_files(d)) if d else 0
    # 只在确实缺中文字体时才强行注册，避免覆盖用户系统里更好的字体
    if d and not pick_qt_font_family():
        info["qt_registered"] = install_for_qt()
    info["qt_family"] = pick_qt_font_family()
    return info


def warn_if_missing(logger) -> None:
    """字体确实没找到时给一条明确提示，方便排查方块字。"""
    info = bootstrap()
    if not info["qt_family"]:
        logger("⚠ 未找到中文字体，界面可能出现方块字；"
               f"请将一个 .ttf/.otf 中文字体放到 {bundled_font_dir() or '程序目录下 fonts/'}")
    elif info["frozen"] and info["bundle_fonts"]:
        logger(f"已加载内置字体 {info['bundle_fonts']} 个")

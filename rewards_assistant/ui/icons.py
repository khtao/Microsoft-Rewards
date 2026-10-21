"""界面用到的图标与配色。

程序图标（assets/icons/app.svg）
-------------------------------
造型：橙色徽章外环 + 左右齿轮 + 环上循环箭头 + 内部奖章/铃铛。
* 齿轮与循环箭头 = **自动化**（每天定时自动完成）；奖章 = 积分奖励。
* 用 SVG 而不是位图：托盘只有 16px，位图缩到这个尺寸会糊成一团，
  矢量渲染任意尺寸都清晰，且只需一个 1KB 的文件。

加载顺序（任何一步失败都不会让程序起不来）：
1. SVG 矢量渲染（首选）
2. 同尺寸/最接近尺寸的 PNG
3. QPainter 现画同款造型（资源文件缺失时的兜底）
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap

# 与 app.svg 保持一致的主色
BRAND = "#FF8630"
BRAND_DEEP = "#E85D04"

_ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def icons_dir() -> Path:
    """图标目录。兼容源码运行与 PyInstaller 打包（onedir / onefile）。"""
    here = Path(__file__).resolve()
    cands = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            cands.append(Path(meipass) / "assets" / "icons")
        cands.append(Path(sys.executable).resolve().parent / "assets" / "icons")
    cands.append(here.parents[2] / "assets" / "icons")   # <root>/assets/icons
    for c in cands:
        if c.is_dir():
            return c
    return cands[-1]


def _render_svg(size: int) -> QPixmap:
    """把 app.svg 渲染成指定尺寸的 QPixmap；失败返回空图。"""
    try:
        from PySide6.QtCore import QByteArray
        from PySide6.QtSvg import QSvgRenderer
    except ImportError:
        return QPixmap()

    path = icons_dir() / "app.svg"
    if not path.is_file():
        return QPixmap()
    try:
        renderer = QSvgRenderer(QByteArray(path.read_bytes()))
        if not renderer.isValid():
            return QPixmap()
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        renderer.render(p)
        p.end()
        return pm
    except Exception:
        return QPixmap()


def _png_for(size: int) -> QPixmap:
    """挑一张最接近 size 的 PNG（优先不小于它的）。"""
    d = icons_dir()
    available = [s for s in _ICON_SIZES if (d / f"app_{s}.png").is_file()]
    if not available:
        return QPixmap()
    up = [s for s in available if s >= size]
    pick = min(up) if up else max(available)
    path = d / f"app_{pick}.png"
    pm = QPixmap(str(path))
    return pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation) if pm.height() != size else pm


def _draw_fallback(size: int) -> QPixmap:
    """资源文件一个都没有时，用 QPainter 现画同款造型（保证程序照样能跑）。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    s = size / 64.0

    def px(v):
        return v * s

    # 外环（顶部留缺口，表达"循环"）
    p.setPen(QPen(QColor(BRAND), px(5.5), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    gap = 40
    rect = QRectF(px(8), px(8), px(48), px(48))
    p.drawArc(rect, int((-90 + gap / 2) * -16), int(-(360 - gap) * 16))

    # 左右齿轮（自动化）
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(BRAND_DEEP))
    for cx in (px(8), px(56)):
        teeth, r_out, r_in = 6, px(7.2), px(4.8)
        pts = []
        for i in range(teeth * 2):
            r = r_out if i % 2 == 0 else r_in
            a = math.pi * i / teeth
            pts.append(QPointF(cx + r * math.cos(a), px(32) + r * math.sin(a)))
        p.drawPolygon(pts)

    # 循环箭头
    end = math.radians(-90 - gap / 2)
    ax, ay = px(32) + px(24.5) * math.cos(end), px(32) + px(24.5) * math.sin(end)
    tx, ty = math.cos(end + math.pi / 2), math.sin(end + math.pi / 2)
    p.drawPolygon([QPointF(ax + tx * px(5), ay + ty * px(5)),
                   QPointF(ax - tx * px(1) + ty * px(3.6), ay - ty * px(1) - tx * px(3.6)),
                   QPointF(ax - tx * px(1) - ty * px(3.6), ay - ty * px(1) + tx * px(3.6))])

    # 内部奖章：斜条纹横块 + 下方铃铛
    p.setBrush(QColor(BRAND))
    p.drawRoundedRect(QRectF(px(24), px(21.5), px(16), px(11.5)), px(3.6), px(3.6))
    p.setBrush(QColor(255, 255, 255, 130))
    for i in range(1, 4):
        p.drawRect(QRectF(px(24 + i * 3.6), px(21.5), px(1.6), px(11.5)))
    p.setBrush(QColor(BRAND))
    p.drawEllipse(QPointF(px(32), px(40.5)), px(5.8), px(5.8))
    p.setBrush(QColor(255, 255, 255, 216))
    p.drawEllipse(QPointF(px(32), px(42.6)), px(2.2), px(2.2))
    p.end()
    return pm


def app_icon(size: int = 64) -> QIcon:
    """程序图标（窗口标题栏 + 系统托盘）。

    返回的 QIcon 内含多个尺寸，Qt 会按显示场景自动挑最合适的那张 ——
    托盘 16px 也有清晰的图，不用拉伸 64px 的大图。
    """
    icon = QIcon()
    for s in _ICON_SIZES:
        pm = _render_svg(s)
        if pm.isNull():
            pm = _png_for(s)
        if pm.isNull():
            pm = _draw_fallback(s)
        if not pm.isNull():
            icon.addPixmap(pm)
    if icon.isNull():                      # 理论上到不了这里
        icon = QIcon(_draw_fallback(max(16, size)))
    return icon


def badge_icon(text: str, color: str = "#2f6fed", size: int = 64) -> QIcon:
    """用文字生成一个圆形徽标图标（用于任务分类）。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(1, 1, size - 2, size - 2)
    p.setPen(QColor("#ffffff"))
    f = QFont("Segoe UI", int(size * 0.5), QFont.Weight.Bold)
    p.setFont(f)
    p.drawText(0, 0, size, size, Qt.AlignmentFlag.AlignCenter, text[:1])
    p.end()
    return QIcon(pm)


CATEGORY_COLORS = {
    "搜索": "#2f6fed",
    "每日任务": "#12b3a8",
    "更多活动": "#f59f00",
}


def category_color(category: str) -> str:
    return CATEGORY_COLORS.get(category, "#868e96")

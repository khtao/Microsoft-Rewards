"""界面用到的图标与配色。

程序图标（assets/icons/app.svg）
-------------------------------
造型：橙色徽章外环 + 左右齿轮 + 环上循环箭头 + 内部奖章/铃铛。
* 齿轮与循环箭头 = **自动化**（每天定时自动完成）；奖章 = 积分奖励。
* 用 SVG 而不是位图：托盘只有 16px，位图缩到这个尺寸会糊成一团，
  矢量渲染任意尺寸都清晰，且只需一个 1KB 的文件。

顶部栏图标（assets/icons/brand.svg / gear.svg）
---------------------------------------------
* ``brand.svg``：橙色渐变圆环 + 白色五角星 = 积分奖章，替代早期那枚
  被当作「刷新按钮」使用的放大镜；现在它是**纯展示的品牌图标**，刷新只由
  中央进度圆环承担。
* ``gear.svg``：矢量齿轮，和标题文字、品牌图标固定同一行高，不再依赖
  ``⚙`` 这类高度随字体变化的 emoji 字形。

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
from PySide6.QtGui import (
    QBrush, QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap, QPolygonF,
)

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


def _render_svg(size: int, name: str = "app.svg") -> QPixmap:
    """把指定 SVG 渲染成 size×size 的 QPixmap；失败返回空图。"""
    try:
        from PySide6.QtCore import QByteArray
        from PySide6.QtSvg import QSvgRenderer
    except ImportError:
        return QPixmap()

    path = icons_dir() / name
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


def _star_polygon(cx: float, cy: float, r_out: float, r_in: float,
                  points: int = 5):
    pts = []
    for i in range(points * 2):
        r = r_out if i % 2 == 0 else r_in
        a = -math.pi / 2 + math.pi * i / points
        pts.append(QPointF(cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _draw_brand_fallback(size: int) -> QPixmap:
    """brand.svg 缺失时用 QPainter 画同款「积分奖章」，保证界面照样能跑。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    s = size / 64.0
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(BRAND))
    p.drawEllipse(QPointF(32 * s, 32 * s), 28 * s, 28 * s)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(255, 255, 255, 235), 2.4 * s))
    p.drawEllipse(QPointF(32 * s, 32 * s), 21 * s, 21 * s)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#FFFFFF"))
    p.drawPolygon(_star_polygon(32 * s, 32 * s, 15 * s, 6.5 * s))
    p.end()
    return pm


def _draw_gear_fallback(size: int, color: str) -> QPixmap:
    """gear.svg 缺失时画齿轮环 + 八齿 + 中心孔；颜色跟随当前主题。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    s = size / 64.0
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(color), 4.4 * s, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap))
    p.drawEllipse(QPointF(32 * s, 32 * s), 18 * s, 18 * s)
    p.drawEllipse(QPointF(32 * s, 32 * s), 7.6 * s, 7.6 * s)
    for i in range(8):
        a = math.pi * i / 4
        x1, y1 = 32 * s + 20 * s * math.cos(a), 32 * s + 20 * s * math.sin(a)
        x2, y2 = 32 * s + 27 * s * math.cos(a), 32 * s + 27 * s * math.sin(a)
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    p.end()
    return pm


def brand_pixmap(size: int = 24) -> QPixmap:
    """顶部栏品牌图标（积分奖章），优先 brand.svg，缺失时现画同款造型。"""
    pm = _render_svg(size, "brand.svg")
    return pm if not pm.isNull() else _draw_brand_fallback(size)


def gear_pixmap(size: int = 32, color: str = "#8895A8") -> QPixmap:
    """顶部栏设置齿轮，优先 gear.svg；缺失时用主题色现画。"""
    pm = _render_svg(size, "gear.svg")
    return pm if not pm.isNull() else _draw_gear_fallback(size, color)


# --------------------------------------------------------------------------- #
# 顶栏登录状态奖章：按参考图复刻的矢量造型，不引入任何位图素材
#
# 已登录（第一张高清参考图）：白色圆盘 + 等宽细橙环 + 橙金渐变圆章，
#   圆章上方两条带 45° 斜纹的绶带向外展开，中央白色勾
# 未登录（第二张参考图）：实心圆底 + 白色描边环 + 白色同款奖章；
#   浅色主题下圆底改灰色，深色主题才用深棕
# 未检测：同一造型降为中性灰，避免状态未定时就给出明显的成功/失败暗示
# --------------------------------------------------------------------------- #

_BADGE_CACHE: dict = {}


def _ribbon(p: QPainter, cx: float, cy: float, s: float, flip: int,
            base: QColor, stripe: QColor) -> None:
    """一条向上外展的斜纹绶带；flip=-1 左带、+1 右带。64 单位坐标。

    参考图里绶带是奖章上方两条带斜条纹的短带，左右对称呈「八」字展开，
    底端被圆章盖住；这里用多边形裁切后画 45° 平行条纹，避免用位图。
    """
    pts = [
        QPointF((cx + flip * 3 * s), (cy + 5 * s)),     # 内下（贴圆章）
        QPointF((cx + flip * 18 * s), (cy - 10 * s)),   # 外下
        QPointF((cx + flip * 13 * s), (cy - 17 * s)),   # 外上
        QPointF((cx + flip * 0 * s), (cy - 4 * s)),     # 内上
    ]
    path = QPainterPath()
    path.addPolygon(QPolygonF(pts))
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(base))
    p.drawPath(path)
    p.setClipPath(path)
    p.setPen(QPen(stripe, 2.3 * s, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.FlatCap))
    for k in range(-10, 11):
        off = k * 4.6 * s
        p.drawLine(QPointF(cx + off - 24 * s, cy - 24 * s),
                   QPointF(cx + off + 24 * s, cy + 24 * s))
    p.restore()
    # 带子外沿一道细亮边，让绶带有厚度
    p.setPen(QPen(QColor(255, 255, 255, 90), 0.8 * s))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPolygon(pts)


def _medal(p: QPainter, cx: float, cy: float, s: float,
           body_top: QColor, body_mid: QColor, body_bottom: QColor,
           edge: QColor, ribbon_base: QColor, ribbon_stripe: QColor,
           mark: QColor) -> None:
    """奖章：两条斜纹绶带 + 橙金渐变圆章 + 中央白色勾。"""
    _ribbon(p, cx, cy, s, -1, ribbon_base, ribbon_stripe)
    _ribbon(p, cx, cy, s, +1, ribbon_base, ribbon_stripe)

    mc = QPointF(cx, cy + 8 * s)
    grad = QLinearGradient(cx - 11 * s, cy - 4 * s,
                           cx + 11 * s, cy + 19 * s)
    grad.setColorAt(0.0, body_top)
    grad.setColorAt(0.52, body_mid)
    grad.setColorAt(1.0, body_bottom)
    p.setPen(QPen(edge, 1.5 * s))
    p.setBrush(QBrush(grad))
    p.drawEllipse(mc, 10.6 * s, 10.6 * s)

    # 内圈细环 + 左上高光，复刻参考图圆章的立体感
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(255, 255, 255, 145), 1.0 * s))
    p.drawEllipse(mc, 8.0 * s, 8.0 * s)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(255, 255, 255, 110)))
    p.drawEllipse(QPointF(mc.x() - 3.4 * s, mc.y() - 4.4 * s),
                  2.5 * s, 1.6 * s)

    path = QPainterPath()
    path.moveTo(mc.x() - 4.4 * s, mc.y() + 0.3 * s)
    path.lineTo(mc.x() - 1.1 * s, mc.y() + 3.5 * s)
    path.lineTo(mc.x() + 4.8 * s, mc.y() - 4.0 * s)
    p.setPen(QPen(mark, 2.5 * s, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)


def _draw_login_badge(size: int, state: str, scheme: str) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    s = size / 64.0
    cx = cy = 32.0 * s

    if state == "signed_in":
        # 第一张高清参考图：白色圆盘 + 等宽细橙环（不是粗渐变环）
        inner = QColor("#FFFDF8") if scheme == "light" else QColor("#26303F")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(inner))
        p.drawEllipse(QPointF(cx, cy), 27.0 * s, 27.0 * s)
        ring = QLinearGradient(0.0, 0.0, float(size), float(size))
        ring.setColorAt(0.0, QColor("#FFC073"))
        ring.setColorAt(0.55, QColor("#FFA04A"))
        ring.setColorAt(1.0, QColor("#F98B36"))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QBrush(ring), 2.4 * s))
        p.drawEllipse(QPointF(cx, cy), 27.0 * s, 27.0 * s)
        _medal(p, cx, cy, s,
               QColor("#FFD98A"), QColor("#FFAE45"), QColor("#F47B1E"),
               QColor("#FFE7B0"), QColor("#F26B3C"), QColor("#FFC95C"),
               QColor(255, 255, 255))
    else:
        # 第二张参考图：实心圆底 + 白色细描边环 + 白色奖章
        if state == "unknown":
            disk = QColor("#B8C2D0") if scheme == "light" else QColor("#4A5668")
        elif scheme == "light":
            disk = QColor("#8A93A0")       # 浅色背景下改灰色，不用深色圆块
        else:
            disk = QColor("#3B2A24")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(disk))
        p.drawEllipse(QPointF(cx, cy), 28.0 * s, 28.0 * s)
        white = QColor(255, 255, 255, 232)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(white, 2.2 * s))
        p.drawEllipse(QPointF(cx, cy), 21.5 * s, 21.5 * s)
        _medal(p, cx, cy, s,
               QColor(255, 255, 255), QColor(240, 243, 248),
               QColor(220, 226, 234), white,
               QColor(255, 255, 255, 214), QColor(255, 255, 255, 150),
               disk)
    p.end()
    return pm


def login_badge_pixmap(size: int = 32, state: str = "signed_in",
                       scheme: str = "light") -> QPixmap:
    """顶栏登录状态奖章，按 (尺寸, 状态, 主题) 缓存；默认 32 与品牌图标同大。"""
    state = state if state in ("signed_in", "signed_out", "unknown") else "unknown"
    scheme = "dark" if scheme == "dark" else "light"
    key = (int(size), state, scheme)
    pm = _BADGE_CACHE.get(key)
    if pm is None:
        pm = _draw_login_badge(*key)
        _BADGE_CACHE[key] = pm
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







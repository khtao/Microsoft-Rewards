# -*- coding: utf-8 -*-
"""登录流程的图形化演示：一张会动的示意图，替代整段说明文字。

设计取舍
--------
* 一段 <ol> + 三段补充说明读起来累，也讲不清「窗口不会自动关闭」这类
  时序关系；这里用 5 帧循环插画把过程演出来，界面只留一行随帧切换的短句。
* 界面内用 ``QPainter`` + ``QTimer`` 实时绘制（40ms 一帧），**不依赖动图
  解码插件**：PyInstaller 的 Qt 插件集合里不保证带对应插件，用动图播放
  控件会在部分打包机上显示空白。
* 同一套 ``paint_guide()`` 也供离线脚本导出真正的 GIF 与静态预览图，
  因此界面里看到的和文档里的动图永远是同一份画面。
* 配色取自当前主题（浅色 / 深色），不写死白底黑字。
"""
from __future__ import annotations

import math
import time
from typing import Dict, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import QApplication, QWidget

from .icons import brand_pixmap

# 逻辑画布尺寸：widget 按实际大小整体缩放，绘制代码只认这一套坐标
BASE_W = 560.0
BASE_H = 232.0
FRAME_COUNT = 5
FRAME_SECONDS = 2.2

FRAME_LABELS = (
    "① 点一下，打开独立浏览器",
    "② 在浏览器里由你本人登录",
    "③ 完成短信 / 验证器确认",
    "④ 回来点「我已完成登录」",
    "⑤ 凭证加密留本机 · 只读同步",
)

_BRAND_CACHE: Dict[int, QPixmap] = {}


def _brand(size: int) -> QPixmap:
    """SVG 渲染按尺寸缓存：演示每 40ms 重绘一次，不能每帧重解析矢量图。"""
    pm = _BRAND_CACHE.get(size)
    if pm is None:
        pm = brand_pixmap(size)
        _BRAND_CACHE[size] = pm
    return pm


_FALLBACK_LIGHT: Dict[str, str] = {
    "window": "#f2f5fa", "topbar": "#eaf0f8", "card": "#ffffff",
    "card_alt": "#f7f9fc", "text": "#1b2430", "text_sub": "#7b8798",
    "text_muted": "#9aa6b6", "border": "#e5eaf2", "primary": "#2f6bff",
    "primary_soft": "#e8f0ff", "green": "#2f9e5f", "green_soft": "#e6f5ec",
    "track": "#e9edf4", "orange": "#f59e0b", "warn": "#c92a2a",
}


def guide_palette() -> Dict[str, str]:
    """取当前界面主题配色；延迟导入避免与 modern 包的导入循环。"""
    try:
        from .modern import theme

        return theme.current()
    except Exception:
        return dict(_FALLBACK_LIGHT)


def _c(value: str, alpha: int = 255) -> QColor:
    col = QColor(value)
    col.setAlpha(alpha)
    return col


def _panel(p: QPainter, r: QRectF, fill: str, border: str,
           radius: float = 10.0, width: float = 1.0, alpha: int = 255) -> None:
    p.setPen(QPen(_c(border, alpha), width))
    p.setBrush(QBrush(_c(fill, alpha)))
    p.drawRoundedRect(r, radius, radius)


def _bar(p: QPainter, x: float, y: float, w: float, h: float,
         color: str, radius: float = 3.0, alpha: int = 255) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c(color, alpha)))
    p.drawRoundedRect(QRectF(x, y, w, h), radius, radius)


def _check(p: QPainter, cx: float, cy: float, size: float,
           color: str, width: float = 3.2, alpha: int = 255) -> None:
    path = QPainterPath()
    path.moveTo(cx - size * 0.46, cy + size * 0.02)
    path.lineTo(cx - size * 0.10, cy + size * 0.36)
    path.lineTo(cx + size * 0.50, cy - size * 0.40)
    p.setPen(QPen(_c(color, alpha), width, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)


def _lock(p: QPainter, cx: float, cy: float, size: float,
          body: str, shackle: str, alpha: int = 255) -> None:
    p.setPen(QPen(_c(shackle, alpha), max(2.0, size * 0.13)))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(cx - size * 0.34, cy - size * 0.62,
                     size * 0.68, size * 0.72), 0, 180 * 16)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c(body, alpha)))
    p.drawRoundedRect(QRectF(cx - size * 0.50, cy - size * 0.10,
                             size, size * 0.72), size * 0.15, size * 0.15)
    p.setBrush(QBrush(_c("#ffffff", alpha)))
    p.drawEllipse(QRectF(cx - size * 0.07, cy + size * 0.18,
                         size * 0.14, size * 0.14))


def _cursor(p: QPainter, x: float, y: float,
            fill: str, outline: str, alpha: int = 255) -> None:
    path = QPainterPath()
    path.moveTo(x, y)
    path.lineTo(x, y + 17)
    path.lineTo(x + 4.6, y + 13)
    path.lineTo(x + 8.2, y + 20.5)
    path.lineTo(x + 11.2, y + 19)
    path.lineTo(x + 7.8, y + 11.5)
    path.lineTo(x + 13.4, y + 11)
    path.closeSubpath()
    p.setPen(QPen(_c(outline, alpha), 1.2, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(QBrush(_c(fill, alpha)))
    p.drawPath(path)


def _app_window(p: QPainter, pal: Dict[str, str], phase: float,
                active: bool, done: bool) -> None:
    """左侧：应用小窗口（奖章 + 进度圆环 + 主按钮）。"""
    r = QRectF(26, 34, 218, 142)
    # 柔和投影
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c("#0d1b33", 20 if active else 12)))
    p.drawRoundedRect(r.adjusted(0, 5, 0, 5), 11, 11)
    _panel(p, r, pal["card"], pal["border"], 10)

    # 顶栏
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c(pal["topbar"])))
    p.drawRoundedRect(QRectF(26, 34, 218, 22), 10, 10)
    p.drawRect(QRectF(26, 46, 218, 10))
    for i, col in enumerate(("#f2a25c", "#f0c14b", "#67c58a")):
        p.setBrush(QBrush(_c(col, 210)))
        p.drawEllipse(QRectF(39 + i * 12, 41, 7, 7))
    _bar(p, 86, 42, 62, 6, pal["text_muted"], 3, 90)

    # 品牌奖章：直接用界面同一枚图标
    pm: QPixmap = _brand(38)
    if not pm.isNull():
        p.drawPixmap(40, 66, pm)

    # 进度圆环
    cx, cy, rad = 152.0, 100.0, 29.0
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(_c(pal["track"]), 7))
    p.drawEllipse(QRectF(cx - rad, cy - rad, rad * 2, rad * 2))
    p.setPen(QPen(_c(pal["green"] if done else pal["primary"]), 7,
                  Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    sweep = 0.94 if done else 0.72 + 0.02 * math.sin(phase * math.tau)
    p.drawArc(QRectF(cx - rad, cy - rad, rad * 2, rad * 2),
              90 * 16, -int(360 * sweep * 16))
    _bar(p, cx - 15, cy - 3, 30, 6, pal["text_muted"], 3, 160)

    _bar(p, 40, 138, 96, 6, pal["text_muted"], 3, 105)
    _bar(p, 146, 138, 78, 6, pal["text_muted"], 3, 105)

    # 主按钮：第 0 帧呼吸高亮，第 3 帧起变绿（表示「我已完成登录」）
    btn = QRectF(40, 150, 190, 22)
    if active:
        glow = 34 + int(18 * (0.5 + 0.5 * math.sin(phase * math.tau)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_c(pal["primary"], glow)))
        p.drawRoundedRect(btn.adjusted(-4, -4, 4, 4), 10, 10)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c(pal["green"] if done else pal["primary"])))
    p.drawRoundedRect(btn, 6, 6)
    _bar(p, 82, 158, 106, 6, "#ffffff", 3, 235)


def _browser_window(p: QPainter, pal: Dict[str, str], frame: int,
                    phase: float) -> None:
    """右侧：独立 Edge 窗口。frame 2 换成验证卡，frame 3 盖完成印章。"""
    r = QRectF(306, 26, 228, 158)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c("#0d1b33", 16)))
    p.drawRoundedRect(r.adjusted(0, 5, 0, 5), 11, 11)
    _panel(p, r, pal["card"], pal["border"], 10)

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c(pal["topbar"])))
    p.drawRoundedRect(QRectF(306, 26, 228, 24), 10, 10)
    p.drawRect(QRectF(306, 40, 228, 10))
    for i, col in enumerate(("#f2a25c", "#f0c14b", "#67c58a")):
        p.setBrush(QBrush(_c(col, 210)))
        p.drawEllipse(QRectF(318 + i * 11, 33, 6.5, 6.5))
    _panel(p, QRectF(352, 30, 168, 15), pal["card"], pal["border"], 7)
    _lock(p, 361, 37.5, 7, pal["text_muted"], pal["text_muted"], 170)
    _bar(p, 374, 35, 96, 5, pal["text_muted"], 2.5, 105)

    card = QRectF(350, 62, 140, 106)
    _panel(p, card, pal["card_alt"], pal["border"], 9)

    if frame == 2:
        # 验证卡：四个验证码格 + 绿色对勾
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_c(pal["primary_soft"])))
        p.drawEllipse(QRectF(404, 72, 32, 32))
        _lock(p, 420, 90, 13, pal["primary"], pal["primary"], 210)
        for i in range(4):
            _panel(p, QRectF(365 + i * 34, 114, 26, 28),
                   pal["card"], pal["border"], 5)
            digit = str(3 + i)
            f = QFont()
            f.setPixelSize(13)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QPen(_c(pal["text"])))
            p.drawText(QRectF(365 + i * 34, 114, 26, 28),
                       Qt.AlignmentFlag.AlignCenter, digit)
        _bar(p, 365, 150, 110, 7, pal["primary"], 3.5, 200)
    else:
        # 登录卡：头像 + 两个输入条 + 主按钮
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_c(pal["primary_soft"])))
        p.drawEllipse(QRectF(404, 72, 32, 32))
        p.setBrush(QBrush(_c(pal["primary"], 200)))
        p.drawEllipse(QRectF(413, 79, 14, 14))
        _panel(p, QRectF(365, 112, 110, 16), pal["card"], pal["border"], 4)
        _panel(p, QRectF(365, 133, 110, 16), pal["card"], pal["border"], 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_c(pal["primary"])))
        p.drawRoundedRect(QRectF(365, 154, 110, 12), 5, 5)

    if frame == 3:
        # 完成印章
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_c(pal["green_soft"], 240)))
        p.drawEllipse(QRectF(470, 138, 46, 46))
        p.setBrush(QBrush(_c(pal["green"])))
        p.drawEllipse(QRectF(475, 143, 36, 36))
        _check(p, 493, 161, 19, "#ffffff", 3.4)


def _vault_card(p: QPainter, pal: Dict[str, str], phase: float) -> None:
    """第 5 帧：本机加密凭证 + 两个只读页面小图标。"""
    r = QRectF(330, 50, 180, 122)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c("#0d1b33", 16)))
    p.drawRoundedRect(r.adjusted(0, 5, 0, 5), 13, 13)
    _panel(p, r, pal["card"], pal["border"], 12)

    pulse = 0.5 + 0.5 * math.sin(phase * math.tau)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c(pal["green_soft"], int(150 + 70 * pulse))))
    p.drawEllipse(QRectF(396, 62, 48, 48))
    _lock(p, 420, 94, 26, pal["green"], pal["green"])

    _check(p, 476, 74, 14, pal["green"], 2.6)

    # 两个小页面：只读的两页，用图形而非路径文字表达
    for i, x in enumerate((362, 438)):
        _panel(p, QRectF(x, 122, 44, 32), pal["card_alt"], pal["border"], 5)
        _bar(p, x + 7, 130, 30, 4, pal["text_muted"], 2, 140)
        _bar(p, x + 7, 138, 22 if i == 0 else 27, 4,
             pal["text_muted"], 2, 110)
        _bar(p, x + 7, 146, 26, 3, pal["green"], 1.5, 190)


def _connector(p: QPainter, pal: Dict[str, str], frame: int,
               phase: float) -> None:
    """中间流动的连接线：亮起表示「这一步正在发生」。"""
    lit = frame in (1, 2, 3)
    y = 105.0
    p.setPen(QPen(_c(pal["primary"], 120 if lit else 45), 1.6,
                  Qt.PenStyle.DashLine))
    p.drawLine(QPointF(248, y), QPointF(302, y))
    if lit or frame == 4:
        t = (phase + frame * 0.17) % 1.0
        x = 248 + 54 * t
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_c(pal["primary"], 45)))
        p.drawEllipse(QRectF(x - 8, y - 8, 16, 16))
        p.setBrush(QBrush(_c(pal["primary"])))
        p.drawEllipse(QRectF(x - 3.5, y - 3.5, 7, 7))


def _scene(p: QPainter, pal: Dict[str, str], frame: int,
           phase: float, opacity: float) -> None:
    p.save()
    p.setOpacity(max(0.0, min(1.0, opacity)))

    _connector(p, pal, frame, phase)
    _app_window(p, pal, phase,
                active=(frame == 0), done=(frame >= 3))

    if frame == 4:
        _vault_card(p, pal, phase)
    else:
        # 第 0 帧浏览器还没出现，只留一个淡影预告
        if frame == 0:
            p.setOpacity(opacity * 0.22)
        _browser_window(p, pal, frame, phase)
        p.setOpacity(opacity)

    # 光标：位置随帧变化，轻微浮动让它看起来是「活的」
    spots = ((146, 172), (404, 116), (410, 126), (140, 172), (416, 104))
    cx, cy = spots[frame]
    bob = math.sin(phase * math.tau) * 2.4
    _cursor(p, cx + bob * 0.4, cy + bob, pal["text"], pal["card"])
    p.restore()


def paint_guide(p: QPainter, rect: QRectF, pal: Dict[str, str],
                frame: int, phase: float,
                prev_frame: Optional[int] = None, blend: float = 1.0) -> None:
    """画一帧；``blend < 1`` 时把上一帧按比例叠在下面做交叉淡入。"""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # 底板
    board = rect.adjusted(0.5, 0.5, -0.5, -0.5)
    _panel(p, board, pal["card_alt"], pal["border"], 14)
    # 顶部柔光：径向渐变 + 裁剪到圆角内，避免深色主题下出现硬边色块
    clip = QPainterPath()
    clip.addRoundedRect(board, 14, 14)
    p.save()
    p.setClipPath(clip)
    glow = QRadialGradient(
        QPointF(rect.width() * 0.48, -18), rect.width() * 0.72)
    glow.setColorAt(0.0, _c(pal["primary"], 26))
    glow.setColorAt(1.0, _c(pal["primary"], 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(glow))
    p.drawRect(rect)
    p.restore()

    if prev_frame is not None and blend < 1.0:
        _scene(p, pal, prev_frame, 1.0, 1.0 - blend)
    _scene(p, pal, frame % FRAME_COUNT, phase, blend)

    # 底部：步骤圆点 + 一行短说明
    dot_y = rect.height() - 22.0
    for i in range(FRAME_COUNT):
        x = 24.0 + i * 17.0
        if i == frame:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(_c(pal["primary"])))
            p.drawEllipse(QRectF(x - 4.5, dot_y - 4.5, 9, 9))
        else:
            p.setPen(QPen(_c(pal["text_muted"]), 1.3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(x - 3.4, dot_y - 3.4, 6.8, 6.8))

    f = QFont()
    f.setPixelSize(13)
    p.setFont(f)
    p.setPen(QPen(_c(pal["text"])))
    label_rect = QRectF(116, dot_y - 11, rect.width() - 128, 22)
    p.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter |
               Qt.AlignmentFlag.AlignLeft,
               FRAME_LABELS[frame % FRAME_COUNT])
    p.restore()


def render_frame_image(frame: int, phase: float,
                       pal: Optional[Dict[str, str]] = None,
                       width: int = 560, height: int = 232,
                       prev_frame: Optional[int] = None,
                       blend: float = 1.0) -> QImage:
    """离屏渲染一帧，供导出 GIF / 静态预览图使用（与界面同一套绘制）。"""
    colors = pal or guide_palette()
    img = QImage(width, height, QImage.Format.Format_ARGB32)
    img.fill(QColor(colors["window"]))
    p = QPainter(img)
    p.scale(width / BASE_W, height / BASE_H)
    paint_guide(p, QRectF(0, 0, BASE_W, BASE_H), colors,
                frame, phase, prev_frame, blend)
    p.end()
    return img


class LoginGuideWidget(QWidget):
    """自动循环播放的流程演示；不阻塞界面，不加载外部图片。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._pal: Dict[str, str] = guide_palette()
        self._t0 = time.monotonic()
        self._frozen: Optional[tuple[int, float]] = None
        self.setFixedHeight(int(BASE_H))
        self.setMinimumWidth(int(BASE_W))
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self.update)
        self._timer.start()

    def refresh_theme(self) -> None:
        self._pal = guide_palette()
        self.update()

    def freeze(self, frame: int, phase: float = 0.5) -> None:
        """停住动画并定格在某一帧：导出预览图 / 静态断言用。"""
        self._frozen = (frame % FRAME_COUNT, max(0.0, min(1.0, phase)))
        self._timer.stop()
        self.update()

    def resume(self) -> None:
        self._frozen = None
        self._t0 = time.monotonic()
        self._timer.start()
        self.update()

    def frame_state(self) -> tuple[int, float]:
        """返回当前 (帧号, 帧内进度)，定帧时返回定格值。"""
        if self._frozen is not None:
            return self._frozen
        total = (time.monotonic() - self._t0) / FRAME_SECONDS
        return int(total) % FRAME_COUNT, total - int(total)

    def current_frame(self) -> int:
        return self.frame_state()[0]

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        p = QPainter(self)
        p.scale(self.width() / BASE_W, self.height() / BASE_H)
        frame, phase = self.frame_state()
        blend = 1.0 if self._frozen is not None else min(1.0, phase / 0.16)
        prev = None if self._frozen is not None else (frame - 1) % FRAME_COUNT
        paint_guide(p, QRectF(0, 0, BASE_W, BASE_H), self._pal,
                    frame, phase, prev, blend)
        p.end()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        # 定帧（导出 / 测试）时保持静止，不因窗口显示而自动续播
        if self._frozen is None and not self._timer.isActive():
            self._timer.start()
        super().showEvent(event)

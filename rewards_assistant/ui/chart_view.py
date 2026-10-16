"""积分趋势图：纯 Qt 自绘（QPainter），不依赖 matplotlib / numpy。

为了把打包体积和依赖降到最低，这里用 QPainter 手绘
折线、柱状、网格、坐标轴与悬浮提示，功能与原来一致但零第三方依赖。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from ..storage import Store
from .controls import NoWheelComboBox

METRICS = [
    ("earned", "每日获得积分"),
    ("available", "累计可用积分"),
    ("streak", "连续完成天数"),
    ("rate", "任务完成率"),
]

RANGES = [(7, "近 7 天"), (30, "近 30 天"), (90, "近 90 天"), (0, "全部")]

# 配色
C_BG = QColor("#ffffff")
C_GRID = QColor("#eef0f3")
C_AXIS = QColor("#ced4da")
C_TEXT = QColor("#868e96")
C_LINE = QColor("#2f6fed")
C_BAR = QColor("#4c8bf5")
C_BAR_DONE = QColor("#12b3a8")
C_FILL = QColor(47, 111, 237, 38)
C_TIP_BG = QColor(33, 37, 41, 235)
C_TIP_FG = QColor("#ffffff")


class ChartCanvas(QWidget):
    """画布：负责把一串 (x, y) 画成折线或柱状，并处理鼠标悬浮。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.labels: List[str] = []     # x 轴文字
        self.values: List[float] = []   # y 值
        self.kind = "line"              # line | bar
        self.unit = ""
        self._hover = -1
        self._rects: List[Tuple[QRectF, int]] = []

    # ------------------------------------------------------------------ 数据

    def set_data(self, labels: Sequence[str], values: Sequence[float],
                 kind: str = "line", unit: str = "") -> None:
        self.labels = list(labels)
        self.values = [float(v) for v in values]
        self.kind = kind
        self.unit = unit
        self._hover = -1
        self.update()

    # ------------------------------------------------------------------ 布局

    def _plot_rect(self) -> QRectF:
        w, h = self.width(), self.height()
        left, top, right, bottom = 52, 18, 16, 34
        return QRectF(left, top, max(10, w - left - right), max(10, h - top - bottom))

    def _nice_max(self, vmax: float) -> float:
        """把最大值向上取整到一个好看的刻度。"""
        if vmax <= 0:
            return 10.0
        import math

        exp = math.floor(math.log10(vmax))
        base = 10 ** exp
        for m in (1, 2, 2.5, 5, 10):
            if vmax <= m * base:
                return m * base
        return 10 * base

    # ------------------------------------------------------------------ 绘制

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), C_BG)

        rect = self._plot_rect()
        n = len(self.values)
        if n == 0:
            p.setPen(QPen(C_TEXT))
            f = QFont(p.font())
            f.setPointSize(11)
            p.setFont(f)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "暂无数据，先同步一次")
            p.end()
            return

        vmax = self._nice_max(max(self.values) if max(self.values) > 0 else 10)
        if self.kind == "rate":
            vmax = 100.0

        self._draw_grid(p, rect, vmax)

        if self.kind == "bar":
            self._draw_bars(p, rect, vmax)
        else:
            self._draw_line(p, rect, vmax)

        if 0 <= self._hover < n:
            self._draw_tooltip(p, rect, self._hover, vmax)
        p.end()

    def _y_of(self, rect: QRectF, v: float, vmax: float) -> float:
        ratio = 0.0 if vmax <= 0 else max(0.0, min(1.0, v / vmax))
        return rect.bottom() - ratio * rect.height()

    def _x_of(self, rect: QRectF, i: int, n: int) -> float:
        if n <= 1:
            return rect.center().x()
        return rect.left() + rect.width() * i / (n - 1)

    def _draw_grid(self, p: QPainter, rect: QRectF, vmax: float) -> None:
        f = QFont(p.font())
        f.setPointSize(9)
        p.setFont(f)
        fm = QFontMetrics(f)

        for i in range(5):
            v = vmax * i / 4
            y = self._y_of(rect, v, vmax)
            p.setPen(QPen(C_GRID if i else C_AXIS, 1))
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            p.setPen(QPen(C_TEXT))
            txt = self._fmt(v)
            p.drawText(QRectF(4, y - fm.height() / 2, rect.left() - 8, fm.height()),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, txt)

        # x 轴标签：点太多就抽稀，避免糊成一片
        n = len(self.labels)
        step = max(1, (n + 7) // 8)
        p.setPen(QPen(C_TEXT))
        for i in range(0, n, step):
            x = self._x_of(rect, i, n)
            p.drawText(QRectF(x - 34, rect.bottom() + 4, 68, fm.height()),
                       Qt.AlignmentFlag.AlignCenter, self.labels[i])

    def _fmt(self, v: float) -> str:
        if self.kind == "rate":
            return f"{v:.0f}%"
        if v >= 10000:
            return f"{v / 1000:.0f}k"
        return f"{v:.0f}"

    def _draw_line(self, p: QPainter, rect: QRectF, vmax: float) -> None:
        n = len(self.values)
        pts = [QPointF(self._x_of(rect, i, n), self._y_of(rect, v, vmax))
               for i, v in enumerate(self.values)]

        # 面积
        from PySide6.QtGui import QPolygonF

        poly = QPolygonF(pts + [QPointF(pts[-1].x(), rect.bottom()),
                                QPointF(pts[0].x(), rect.bottom())])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(C_FILL)
        p.drawPolygon(poly)

        # 折线
        pen = QPen(C_LINE, 2)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        for i in range(n - 1):
            p.drawLine(pts[i], pts[i + 1])

        # 数据点
        p.setBrush(C_LINE)
        p.setPen(QPen(C_BG, 1.5))
        r = 3.5 if n <= 40 else 2.2
        for i, pt in enumerate(pts):
            if i == self._hover:
                p.setBrush(C_BAR_DONE)
                p.drawEllipse(pt, r + 2, r + 2)
                p.setBrush(C_LINE)
            else:
                p.drawEllipse(pt, r, r)

        # 悬浮竖线
        if 0 <= self._hover < n:
            p.setPen(QPen(QColor(47, 111, 237, 90), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(pts[self._hover].x(), rect.top()),
                       QPointF(pts[self._hover].x(), rect.bottom()))

    def _draw_bars(self, p: QPainter, rect: QRectF, vmax: float) -> None:
        n = len(self.values)
        if n == 0:
            return
        slot = rect.width() / n
        bw = max(2.0, min(slot * 0.62, 26.0))
        self._rects = []
        for i, v in enumerate(self.values):
            cx = rect.left() + slot * (i + 0.5)
            x = cx - bw / 2
            y = self._y_of(rect, v, vmax)
            h = rect.bottom() - y
            r = QRectF(x, y, bw, max(1.0, h))
            self._rects.append((r, i))
            done = self.kind == "rate" and v >= 100
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(C_BAR_DONE if (done or i == self._hover) else C_BAR)
            p.drawRoundedRect(r, 3, 3)

    def _draw_tooltip(self, p: QPainter, rect: QRectF, i: int, vmax: float) -> None:
        label = self.labels[i] if i < len(self.labels) else ""
        val = self.values[i]
        txt = f"{label}   {self._fmt(val)}{self.unit}"
        f = QFont(p.font())
        f.setPointSize(10)
        p.setFont(f)
        fm = QFontMetrics(f)
        tw = fm.horizontalAdvance(txt) + 16
        th = fm.height() + 12

        x = self._x_of(rect, i, len(self.values))
        tx = min(max(x - tw / 2, rect.left()), rect.right() - tw)
        ty = max(rect.top() + 2, self._y_of(rect, val, vmax) - th - 10)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(C_TIP_BG)
        p.drawRoundedRect(QRectF(tx, ty, tw, th), 5, 5)
        p.setPen(QPen(C_TIP_FG))
        p.drawText(QRectF(tx, ty, tw, th), Qt.AlignmentFlag.AlignCenter, txt)

    # ------------------------------------------------------------------ 交互

    def _index_at(self, pos) -> int:
        n = len(self.values)
        if n == 0:
            return -1
        rect = self._plot_rect()
        if self.kind == "bar":
            for r, i in self._rects:
                if r.adjusted(-2, -2, 2, 2).contains(pos):
                    return i
            return -1
        slot = rect.width() / max(1, n - 1)
        if slot <= 0:
            return -1
        idx = round((pos.x() - rect.left()) / slot)
        if idx < 0 or idx >= n:
            return -1
        # 竖向距离太远就不算悬浮，避免误触
        vmax = self._nice_max(max(self.values) if max(self.values) > 0 else 10)
        y = self._y_of(rect, self.values[idx], vmax)
        if abs(pos.y() - y) > rect.height() * 0.6:
            return -1
        return idx

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        idx = self._index_at(event.position())
        if idx != self._hover:
            self._hover = idx
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        if self._hover != -1:
            self._hover = -1
            self.update()
        super().leaveEvent(event)


class ChartView(QWidget):
    """趋势图面板：指标切换 + 时间范围切换 + 摘要文字。"""

    def __init__(self, store: Store, parent: Optional[QWidget] = None,
                 settings=None, save_settings=None) -> None:
        super().__init__(parent)
        self.store = store
        self.settings = settings
        self.save_settings = save_settings

        self.metric = NoWheelComboBox()
        for key, label in METRICS:
            self.metric.addItem(label, key)
        metric = settings.get("chart_metric", "earned") if settings else "earned"
        idx = self.metric.findData(metric)
        if idx >= 0:
            self.metric.setCurrentIndex(idx)

        self.range_box = NoWheelComboBox()
        for days, label in RANGES:
            self.range_box.addItem(label, days)
        idx = self.range_box.findData(settings.get("chart_days", 30) if settings else 30)
        if idx >= 0:
            self.range_box.setCurrentIndex(idx)
        self.metric.currentIndexChanged.connect(self._selection_changed)
        self.range_box.currentIndexChanged.connect(self._selection_changed)

        self.canvas = ChartCanvas()
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("color:#495057;font-size:12px;padding:4px 8px;")

        row = QHBoxLayout()
        row.addWidget(QLabel("指标"))
        row.addWidget(self.metric)
        row.addWidget(QLabel("范围"))
        row.addWidget(self.range_box)
        row.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addLayout(row)
        lay.addWidget(self.canvas, 1)
        lay.addWidget(self.summary)

    # ------------------------------------------------------------------ 数据

    def _rows(self) -> List:
        days = self.range_box.currentData() or 0
        rows = self.store.all_days()
        if days:
            since = (date.today() - timedelta(days=days - 1)).isoformat()
            rows = [r for r in rows if r["day"] >= since]
        return list(rows)

    def _selection_changed(self, *_args) -> None:
        if self.settings is not None:
            data = {"chart_metric": self.metric.currentData(),
                    "chart_days": self.range_box.currentData()}
            if self.save_settings is not None:
                self.save_settings(data)
            else:
                self.settings.update(data)
        self.refresh()

    def refresh(self) -> None:
        rows = self._rows()
        key = self.metric.currentData() or "earned"
        labels: List[str] = []
        values: List[float] = []

        for r in rows:
            d = r["day"]
            labels.append(d[5:] if len(d) >= 10 else d)  # 只看 月-日
            if key == "earned":
                values.append(float(r["points_earned"] or 0))
            elif key == "available":
                values.append(float(r["available_points"] or 0))
            elif key == "streak":
                values.append(float(r["streak"] or 0))
            else:  # rate
                mx = int(r["points_max"] or 0)
                got = int(r["points_earned"] or 0)
                values.append(100.0 if mx and got >= mx else (got / mx * 100 if mx else 0.0))

        kind = "bar" if key in ("earned", "rate") else "line"
        unit = "%" if key == "rate" else (" 天" if key == "streak" else " 分")
        self.canvas.set_data(labels, values, kind=kind, unit=unit)
        self.summary.setText(self._summary_text(rows, key, values))

    def _summary_text(self, rows: List, key: str, values: List[float]) -> str:
        if not rows:
            return "暂无数据，先同步一次。"
        vals = [v for v in values]
        total = sum(vals)
        avg = total / len(vals) if vals else 0
        best = max(vals) if vals else 0
        if key == "earned":
            return (f"共 {len(rows)} 天 · 累计 {total:,.0f} 分 · "
                    f"日均 {avg:,.0f} 分 · 单日最高 {best:,.0f} 分")
        if key == "available":
            return f"共 {len(rows)} 天 · 最新 {vals[-1]:,.0f} 分 · 区间最高 {best:,.0f} 分"
        if key == "streak":
            return f"共 {len(rows)} 天 · 最新连续 {vals[-1]:,.0f} 天 · 最长 {best:,.0f} 天"
        done = sum(1 for v in vals if v >= 100)
        return (f"共 {len(rows)} 天 · 全勤 {done} 天 · 平均完成率 {avg:.0f}% · "
                f"最高 {best:.0f}%")

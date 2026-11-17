# -*- coding: utf-8 -*-
"""复刻界面的可复用 Qt 组件：圆环、卡片、开关、分段器、任务行、日历格子。

这里只做展示；数据由 ``viewmodel`` 提供，业务由 ``main_window`` 编排。
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QEvent, QRectF, Qt,
                            QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from . import theme, viewmodel as vm


# ---------------------------------------------------------------- 圆环进度

class RingProgress(QAbstractButton):
    """蓝色圆环 + 中间百分比；**它本身就是「刷新」按钮**。

    对齐插件版圆环的 hover 观感：鼠标进入时光晕呼吸、弧线轻微外扩、末端
    出现高光点、中心数字微微放大，同时下方小字由「今日任务」变成「点击刷新」；
    移出即恢复。点击发 ``clicked``，由 HomePage 接到 ``Application.sync_now()``
    —— 任务卡头部那个「刷新」文字按钮已移除，手动刷新只有这一个显式入口。
    运行 / 同步期间 ``setEnabled(False)``，视觉变淡且不响应点击。
    """

    def __init__(self, parent=None, size: int = 176) -> None:
        super().__init__(parent)
        self._size = size
        self._value = 0.0
        self._target = 0.0
        self._has_value = False       # 首次渲染不动画，避免启动瞬间显示 0%
        self._caption = "今日任务"
        self._refresh_caption = "点击刷新"
        self._animate = True
        self._hovered = False
        self._hover_phase = 0.0
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setDuration(1500)
        self._hover_anim.setLoopCount(-1)     # 持续呼吸，直到鼠标移出
        self._hover_anim.setStartValue(0.0)
        self._hover_anim.setKeyValueAt(0.5, 1.0)
        self._hover_anim.setEndValue(0.0)
        self._hover_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._hover_anim.valueChanged.connect(self._on_hover_phase)
        # 数值动画：对齐插件版 SVG stroke-dashoffset 的 760ms ease-out
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self.setFixedSize(size, size)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # ------------------------------------------------------------ 文案 / 数值

    def set_caption(self, text: str) -> None:
        self._caption = text
        self.update()

    def set_refresh_caption(self, text: str) -> None:
        """多语言：hover 时圆环内显示的「点击刷新 / Click to refresh」。

        刻意不用 ↻ / ⟳ 之类符号：部分 CJK 字体缺字会渲染成方块或空心圆。
        """
        self._refresh_caption = str(text)
        self.update()

    def set_animate(self, enabled: bool) -> None:
        self._animate = bool(enabled)
        if not enabled:
            self._anim.stop()
            self._value = self._target
            self.update()

    def set_value(self, percent: int, animate: bool = True) -> None:
        self._target = float(max(0, min(100, int(percent))))
        instant = (not self._has_value or not (self._animate and animate))
        self._anim.stop()
        if instant:
            self._has_value = True
            self._value = self._target
            self.update()
            return
        self._anim.setStartValue(float(self._value))
        self._anim.setEndValue(self._target)
        self._anim.setDuration(vm.RING_ANIM_MS)
        self._anim.start()

    def _on_anim(self, value) -> None:
        self._value = float(value)
        self.update()

    # ------------------------------------------------------------ hover 呼吸

    def _on_hover_phase(self, value) -> None:
        self._hover_phase = float(value)
        self.update()

    def _start_hover(self) -> None:
        if not self.isEnabled():
            return
        self._hovered = True
        self._hover_anim.start()
        self.update()

    def _stop_hover(self) -> None:
        self._hovered = False
        self._hover_anim.stop()
        self._hover_phase = 0.0
        self.update()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self._start_hover()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._stop_hover()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self._stop_hover()

    def hover_phase(self) -> float:
        """供离屏渲染 / 回归断言读取当前呼吸相位。"""
        return self._hover_phase

    def is_hovered(self) -> bool:
        return self._hovered

    # ------------------------------------------------------------ 绘制

    def paintEvent(self, _event) -> None:
        c = theme.current()
        phase = self._hover_phase
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self.isEnabled():
            p.setOpacity(0.5)
        # hover 时整圈轻微外扩，幅度 2px，配合光晕形成「呼吸」
        inset = 13 - 2 * phase
        rect = QRectF(inset, inset, self.width() - 2 * inset,
                      self.height() - 2 * inset)
        width = 13 + 1.5 * phase
        base = QPen(QColor(c["track"]), width, Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap)
        p.setPen(base)
        p.drawArc(rect, 90 * 16, -360 * 16)
        if self._value > 0:
            span = -int(360 * 16 * self._value / 100.0)
            moving = self._anim.state() == QAbstractAnimation.State.Running
            glow = QColor(c["primary"])
            # 静止淡光晕 34；数值动画中 74；hover 呼吸最高到 128
            glow.setAlpha(max(34, 74 if moving else 34, int(34 + 94 * phase)))
            halo = QPen(glow, width + 9 + 6 * phase, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap)
            p.setPen(halo)
            p.drawArc(rect, 90 * 16, span)
            arc = QPen(QColor(c["primary_hover"] if self._hovered else c["primary"]),
                       width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(arc)
            p.drawArc(rect, 90 * 16, span)
            # 末端高光点：只在 hover 时出现，半径随相位脉动
            if phase > 0.02:
                ang = math.radians(90 - 360 * self._value / 100.0)
                r = rect.width() / 2.0
                ex = rect.center().x() + r * math.cos(ang)
                ey = rect.center().y() - r * math.sin(ang)
                tip = QColor("#ffffff")
                tip.setAlpha(int(150 + 90 * phase))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(tip)
                p.drawEllipse(QRectF(ex - 3 - 2 * phase, ey - 3 - 2 * phase,
                                    (6 + 4 * phase), (6 + 4 * phase)))
                p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QColor(c["text"]))
        f = QFont(self.font())
        f.setPointSize(25 + int(round(2 * phase)))
        f.setBold(True)
        p.setFont(f)
        p.drawText(rect.adjusted(0, -8, 0, 0), Qt.AlignmentFlag.AlignCenter,
                   f"{int(round(self._value))}%")
        f2 = QFont(self.font())
        f2.setPointSize(9)
        p.setFont(f2)
        # hover 时不用浮层提示，直接把圆环内的小字换成可点击说明
        if self._hovered:
            p.setPen(QColor(c["primary"]))
            caption = self._refresh_caption
        else:
            p.setPen(QColor(c["text_sub"]))
            caption = self._caption
        p.drawText(rect.adjusted(0, 34, 0, 0), Qt.AlignmentFlag.AlignCenter,
                   caption)
        p.end()


# ---------------------------------------------------------------- 圆环下状态条

class _StatusDot(QWidget):
    """插件版 .status-dot：小圆点；运行中做低幅度呼吸，静止时恒定。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self._color = QColor("#7b8798")
        self._alpha = 1.0

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def set_alpha(self, value: float) -> None:
        self._alpha = max(0.25, min(1.0, float(value)))
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        c = QColor(self._color)
        c.setAlpha(int(255 * self._alpha))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(QRectF(1, 1, 8, 8))
        p.end()


class StatusChip(QWidget):
    """对齐插件版 popup.html 的 ``#status``：圆点 + 文案 + 状态配色。

    旧实现是圆环下一行固定绿色「● 已完成 / 待完成」，待完成也是绿的，
    且只由任务完成度决定。插件版这行是**运行状态**：就绪灰、进行中蓝、
    全部完成「已完成，无需动作」绿、出错红，运行中小圆点有呼吸感。
    """

    def __init__(self, lang: str = "zh", parent=None) -> None:
        super().__init__(parent)
        self._lang = lang
        self._kind = "ready"
        self._ticks = 0
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addStretch(1)
        self._dot = _StatusDot(self)
        lay.addWidget(self._dot)
        self._label = QLabel(vm.tr(lang, "status.ready"))
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 插件版 #status 是单行：长文案省略号收起，完整内容放到页面底部次要详情。
        # 不换行可避免 QLabel 的 heightForWidth 在 QHBoxLayout 里被压成一行高、
        # 两行文字互相重叠，也不会把窗口撑宽。
        self._label.setWordWrap(False)
        self._label.setMaximumWidth(340)
        lay.addWidget(self._label)
        lay.addStretch(1)
        self._pulse = QTimer(self)
        self._pulse.setInterval(60)
        self._pulse.timeout.connect(self._tick_pulse)
        self.set_state("ready", vm.tr(lang, "status.ready"))

    def set_language(self, lang: str) -> None:
        self._lang = lang
        self.set_state(self._kind, vm.tr(lang, "status." + self._text_key))

    def kind(self) -> str:
        return self._kind

    def set_state(self, kind: str, text: str, text_key: str = "ready") -> None:
        kind = kind if kind in vm.STATUS_KINDS else "ready"
        self._kind = kind
        self._text_key = text_key
        c = theme.current()
        color = QColor(c[vm.status_color_key(kind)])
        self._dot.set_color(color.name())
        fm = self._label.fontMetrics()
        raw = str(text or "")
        # 单行省略：保留前段语义，窗口固定宽度不参与 sizeHint 计算。
        elided = fm.elidedText(raw, Qt.TextElideMode.ElideRight, 340)
        self._label.setText(elided)
        self._label.setMinimumHeight(fm.height() + 4)
        self._label.updateGeometry()
        self.setMinimumHeight(fm.height() + 4)
        self.updateGeometry()
        self._label.setStyleSheet(
            f"color:{color.name()};font-size:12px;font-weight:600;background:transparent;")
        # 运行中呼吸；其它状态恒定
        if kind == "busy":
            if not self._pulse.isActive():
                self._ticks = 0
                self._pulse.start()
        else:
            self._pulse.stop()
            self._dot.set_alpha(1.0)

    def _tick_pulse(self) -> None:
        self._ticks += 1
        # 0.55 ~ 1.0 之间缓慢呼吸，周期约 1.6s
        self._dot.set_alpha(0.55 + 0.45 * (0.5 + 0.5 * math.sin(
            self._ticks * 2 * math.pi / 26)))

    def set_text(self, text: str) -> None:
        """由「同步 / 搜索 / 收尾 / 失败」等状态文案自动判色。"""
        self.set_state(vm.status_kind(text), text, "ready")

    def set_from_progress(self, prog: dict, lang: str) -> None:
        """任务全部完成 → 绿色「已完成，无需动作」；进行中不被覆盖。"""
        if self._kind == "busy":
            return
        if prog.get("complete"):
            self.set_state("done", vm.tr(lang, "status.all_done"), "all_done")
        elif self._kind != "done":
            self.set_state("ready", vm.tr(lang, "status.ready"), "ready")

    def set_busy(self, busy: bool, lang: str) -> None:
        if busy:
            self.set_state("busy", vm.tr(lang, "status.syncing"), "syncing")
        elif self._kind == "busy":
            self.set_state("ready", vm.tr(lang, "status.ready"), "ready")


# ---------------------------------------------------------------- iOS 风格开关

class Switch(QAbstractButton):
    """自绘开关，避免 QCheckBox::indicator 在各平台样式不一致。"""

    def __init__(self, checked: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(bool(checked))
        self.setFixedSize(46, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._knob = 23.0 if checked else 3.0
        # 对齐插件版 iOS 开关过渡（桌面端 240ms）；减少动画时立即到位。
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_knob)
        self.toggled.connect(self._animate_knob)

    def _animate_knob(self, on: bool) -> None:
        target = 23.0 if on else 3.0
        self._anim.stop()
        self._anim.setStartValue(float(self._knob))
        self._anim.setEndValue(float(target))
        self._anim.setDuration(vm.SWITCH_ANIM_MS)
        self._anim.start()

    def _on_knob(self, value) -> None:
        self._knob = float(value)
        self.update()

    def paintEvent(self, _event) -> None:
        c = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        on = self.isChecked()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c["primary"] if on else c["scroll"]))
        p.drawRoundedRect(QRectF(1, 1, 44, 24), 12, 12)
        p.setBrush(QColor("#ffffff"))
        # blockSignals(True) 回填时不发 toggled，此时直接按真实状态绘制
        running = self._anim.state() == QAbstractAnimation.State.Running
        x = self._knob if running else (23.0 if on else 3.0)
        p.drawEllipse(QRectF(x, 3, 20, 20))
        p.end()

    def mouseReleaseEvent(self, event) -> None:
        # 不要调用 super：QAbstractButton.mouseReleaseEvent 会再次 click()
        # 触发一次 toggle，导致开关「按了等于没按」。
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self.isChecked())  # setChecked 自己会发 toggled


# ---------------------------------------------------------------- 分段选择

class Segmented(QWidget):
    """灰底容器 + 白色选中块（语言 / 主题 / 强度 / 日历视图）。"""

    changed = Signal(str)

    def __init__(self, options: Sequence[str], current: str = "", parent=None) -> None:
        super().__init__(parent)
        box = QFrame(self)
        box.setObjectName("segBox")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for text in options:
            b = QPushButton(text)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            lay.addWidget(b)
            self._buttons[text] = b
            self._group.addButton(b)
            b.clicked.connect(lambda _ok=False, t=text: self.changed.emit(t))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(box)
        self.set_value(current or (options[0] if options else ""))

    def set_value(self, text: str) -> None:
        b = self._buttons.get(text)
        if b is None and self._buttons:
            b = next(iter(self._buttons.values()))
        if b:
            b.setChecked(True)

    def value(self) -> str:
        for text, b in self._buttons.items():
            if b.isChecked():
                return text
        return ""


# ---------------------------------------------------------------- 卡片容器

class Card(QFrame):
    """白色圆角卡；可选「编号 + 标题 + 副标题」头部。"""

    def __init__(self, parent=None, number: str = "", title: str = "",
                 subtitle: str = "", color: str = "", collapsible: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self._body = QVBoxLayout(self)
        self._body.setContentsMargins(16, 14, 16, 16)
        self._body.setSpacing(10)
        self._content = QWidget(self)
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(0, 0, 0, 0)
        self._content_lay.setSpacing(9)
        if number or title or subtitle:
            head = QHBoxLayout()
            head.setSpacing(9)
            if number:
                tag = QFrame()
                tag.setObjectName("numberTag")
                tag.setFixedSize(24, 24)
                tl = QHBoxLayout(tag)
                tl.setContentsMargins(0, 0, 0, 0)
                lab = QLabel(number)
                lab.setObjectName("numberTagText")
                lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
                tl.addWidget(lab)
                head.addWidget(tag)
                if color:
                    tag.setStyleSheet(
                        f"QFrame#numberTag {{ background: {color}22; border-radius: 6px; }}"
                        f"QLabel#numberTagText {{ color: {color}; font-size: 11px; font-weight: 700; }}")
            if title or subtitle:
                col = QVBoxLayout()
                col.setSpacing(1)
                if title:
                    t = QLabel(title)
                    t.setObjectName("cardTitle")
                    col.addWidget(t)
                if subtitle:
                    s = QLabel(subtitle)
                    s.setObjectName("tiny")
                    s.setWordWrap(True)
                    col.addWidget(s)
                head.addLayout(col, 1)
            if collapsible:
                self._chevron = QPushButton("⌄")
                self._chevron.setObjectName("icon")
                self._chevron.setFixedWidth(26)
                self._chevron.clicked.connect(self._toggle_body)
                head.addWidget(self._chevron)
            self._body.addLayout(head)
        self._body.addWidget(self._content, 1)

    def _toggle_body(self) -> None:
        self._content.setVisible(not self._content.isVisible())
        self._chevron.setText("⌄" if self._content.isVisible() else "›")

    def add(self, widget) -> None:
        self._content_lay.addWidget(widget)

    def add_layout(self, layout) -> None:
        self._content_lay.addLayout(layout)


class StatCard(QFrame):
    """三项积分卡：大数字 + 小标签。"""

    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setMinimumHeight(82)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 14, 8, 12)
        lay.setSpacing(4)
        self._value = QLabel("--")
        f = QFont(self._value.font())
        f.setPointSize(18)
        f.setBold(True)
        self._value.setFont(f)
        self._value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label = QLabel(label)
        self._label.setObjectName("muted")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addStretch(1)
        lay.addWidget(self._value)
        lay.addWidget(self._label)
        lay.addStretch(1)

    def set_value(self, text: str) -> None:
        self._value.setText(text)

    def set_label(self, text: str) -> None:
        self._label.setText(text)


# ---------------------------------------------------------------- 任务行

class TaskRow(QFrame):
    """一条任务：左侧积分徽章 + 标题/描述 + 状态。"""

    def __init__(self, item: dict, parent=None) -> None:
        """所有任务统一绿色圆角徽章（搜索 60 分与活动 +10 完全同款）。"""
        super().__init__(parent)
        self.setObjectName("taskRow")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(10)
        badge = QFrame()
        badge.setObjectName("numBadge")
        badge.setFixedHeight(22)
        badge.setMinimumWidth(38)
        bl = QHBoxLayout(badge)
        bl.setContentsMargins(6, 0, 6, 0)
        b = QLabel(f"+{item['points']}")
        b.setObjectName("numBadgeText")
        b.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(b)
        lay.addWidget(badge)

        col = QVBoxLayout()
        col.setSpacing(1)
        name = QLabel(item["name"])
        name.setWordWrap(True)
        name.setStyleSheet("font-weight:600;")
        col.addWidget(name)
        if item.get("description"):
            desc = QLabel(item["description"])
            desc.setObjectName("tiny")
            desc.setWordWrap(True)
            col.addWidget(desc)
        lay.addLayout(col, 1)

        state = QLabel("已完成" if item["complete"] else "待完成")
        state.setObjectName("tiny")
        state.setStyleSheet(
            f"color: {theme.current()['green_text'] if item['complete'] else theme.current()['text_sub']};"
            "font-size:11px;")
        lay.addWidget(state)


class ProgressBar(QFrame):
    """按容器宽度比例自绘的细进度条（对齐插件版 --track / --success）。

    旧实现把 ``percent * 2.6`` 当固定像素宽度：30/30 分（100%）也只画 260px，
    容器一宽就填不满；而且 7px 子控件嵌在带 1px 边框的 7px 容器里会溢出，
    截图里进度条下方那条绿色细线就是它。这里整条自绘、随 resize 自适应，
    100% 严格等于容器宽度，并带 680ms ease-out 数值动画。
    """

    def __init__(self, percent: int = 0, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("progressBar")
        self.setFixedHeight(7)
        self.setMinimumWidth(40)
        self._target = float(max(0, min(100, int(percent))))
        self._display = float(self._target)
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def value(self) -> int:
        return int(round(self._target))

    def fill_width(self) -> int:
        """供离线测试断言：100% 一定等于容器宽度。"""
        return vm.progress_fill(int(round(self._display)), self.width())

    def set_value(self, percent: int, animate: bool = True) -> None:
        self._target = float(max(0, min(100, int(percent))))
        self._anim.stop()
        if not animate:
            self._display = self._target
            self.update()
            return
        self._anim.setStartValue(float(self._display))
        self._anim.setEndValue(self._target)
        self._anim.setDuration(vm.BAR_ANIM_MS)
        self._anim.start()

    def _on_anim(self, value) -> None:
        self._display = float(value)
        self.update()

    def paintEvent(self, _event) -> None:
        c = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(0, 0, self.width(), self.height())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c["track"]))
        p.drawRoundedRect(rect, 3.5, 3.5)
        fill = vm.progress_fill(int(round(self._display)), self.width())
        if fill > 0:
            # 裁到填充宽度后重画同一圆角矩形：左端圆角、右端直边，无溢出
            p.setClipRect(QRectF(0, 0, fill, self.height()))
            p.setBrush(QColor(c["progress"]))
            p.drawRoundedRect(rect, 3.5, 3.5)
        p.end()


class GroupBlock(QWidget):
    """分组：标题行（右对齐进度）+ 比例进度条 + 任务卡列表。"""

    def __init__(self, group: dict, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(7)
        head = QHBoxLayout()
        title = QLabel(group["label"])
        title.setObjectName("group")
        head.addWidget(title)
        head.addStretch(1)
        score = QLabel(f"{group['earned']} / {group['total']} 分")
        score.setObjectName("group")
        head.addWidget(score)
        lay.addLayout(head)
        bar = ProgressBar(group["percent"])
        lay.addWidget(bar)
        for item in group["items"]:
            lay.addWidget(TaskRow(item))


# ---------------------------------------------------------------- 表单行

class Row(QWidget):
    """左标签 + 右控件；标签可换行（截图里有些标签是两行）。"""

    def __init__(self, label: str, control: QWidget, parent=None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lab = QLabel(label)
        lab.setWordWrap(True)
        lab.setStyleSheet("font-weight:600;font-size:13px;")
        lab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay.addWidget(lab, 1)
        lay.addWidget(control)


class TimeChip(QFrame):
    """蓝色时间胶囊，带 × 删除。"""

    removed = Signal(str)

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("chip")
        self._text = text
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 3, 6, 3)
        lay.setSpacing(4)
        lab = QLabel(text)
        lab.setObjectName("chipText")
        lay.addWidget(lab)
        x = QPushButton("×")
        x.setObjectName("chipX")
        x.setFixedSize(16, 16)
        x.clicked.connect(lambda: self.removed.emit(self._text))
        lay.addWidget(x)


class DotsRow(QWidget):
    """卡片第三排：一排小圆点，实心=有积分，浅色=无积分（截图样式）。"""

    def __init__(self, dots: Sequence[bool], *, diameter: int = 7, gap: int = 6,
                 parent=None) -> None:
        super().__init__(parent)
        c = theme.current()
        self._dots = list(dots or [])
        self._d = diameter
        self._gap = gap
        self._on = QColor(c["primary"])
        # 空心点也要看得见：同色低透明度，而不是和底色几乎相同的浅蓝。
        self._off = QColor(c["primary"])
        self._off.setAlpha(70)
        self.setFixedHeight(diameter + 2)
        self.setFixedWidth(max(1, len(self._dots) * diameter
                               + max(0, len(self._dots) - 1) * gap))

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = (self.height() - self._d) // 2
        for i, filled in enumerate(self._dots):
            painter.setBrush(self._on if filled else self._off)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(i * (self._d + self._gap), y, self._d, self._d)
        painter.end()


class CalendarCell(QFrame):
    """圆角矩形内三排：标签（W3 11/9-11/15）/ 蓝色粗体积分 / 一排圆点。

    ``compact`` 用于「日」视图的一排七个：字号与内边距等比缩小，但三排结构
    和截图里的周卡片完全一致。
    """

    def __init__(self, cell: dict, compact: bool = False, parent=None) -> None:
        super().__init__(parent)
        c = theme.current()
        self.setObjectName("calCell")
        pad = 6 if compact else 12
        self.setFixedHeight(62 if compact else 88)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            "QFrame#calCell { background: %s; border: none; border-radius: %dpx; }"
            % (c["blue_chip"], 9 if compact else 12))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(pad, 7 if compact else 10, pad, 7 if compact else 10)
        lay.setSpacing(2 if compact else 4)

        label = QLabel(str(cell.get("label", "")))
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        label.setStyleSheet(
            "color:%s;font-size:%dpx;background:transparent;"
            % (c["blue_chip_text"], 9 if compact else 11))
        lay.addWidget(label)

        value = QLabel(f"{int(cell.get('points') or 0):,}")
        value.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        f = QFont(value.font())
        f.setBold(True)
        # 一排七个时格子只有 40 多 px：4 位数用更小字号，且不加千分位，
        # 免得把整列撑宽导致后面的格子被挤出容器。
        text = f"{int(cell.get('points') or 0):,}"
        if compact:
            text = str(int(cell.get('points') or 0))
            f.setPointSize(10 if len(text) >= 4 else 12)
        else:
            f.setPointSize(20)
        value.setText(text)
        value.setFont(f)
        if compact:
            label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                QSizePolicy.Policy.Preferred)
            value.setSizePolicy(QSizePolicy.Policy.Ignored,
                                QSizePolicy.Policy.Preferred)
        value.setStyleSheet("color:%s;background:transparent;" % c["primary"])
        lay.addWidget(value)

        dots = DotsRow(cell.get("dots") or [True],
                       diameter=5 if compact else 7,
                       gap=4 if compact else 6)
        lay.addWidget(dots, alignment=Qt.AlignmentFlag.AlignLeft)
        lay.addStretch(1) if not compact else None


class CalendarGrid(QWidget):
    """积分赚取日历：日=一排七个，周/月=一排一个；只显示有积分的时间。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(9)
        self._month_label = QLabel("")
        self._month_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._month_label.setStyleSheet("font-size:12px;color:" + theme.current()["text_sub"])
        self._lay.addWidget(self._month_label)
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(6)
        self._lay.addWidget(self._grid_host)
        # 「下面的图」改成卡片内第三排圆点，不再单独铺一行无意义的图例。
        self._summary = QLabel("")
        self._summary.setObjectName("tiny")
        self._summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lay.addWidget(self._summary)

    def clear_grid(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def set_cells(self, cells: Iterable[dict], title: str, summary: dict,
                  empty_text: str = "") -> None:
        cells = list(cells)
        self.clear_grid()
        unit = cells[0].get("unit", "day") if cells else "day"
        columns = 7 if unit == "day" else 1
        for i, cell in enumerate(cells):
            self._grid.addWidget(CalendarCell(cell, compact=(columns == 7)),
                                 i // columns, i % columns)
        if not cells:
            empty = QLabel(empty_text or "暂无积分记录")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setObjectName("tiny")
            self._grid.addWidget(empty, 0, 0, 1, columns)
        self._month_label.setText(title)
        self._summary.setText(
            f"{summary.get('avg_label', '日均')} {summary.get('avg', 0)} · "
            f"{summary.get('max_label', '最高')} {summary.get('max', 0)} · "
            f"{summary.get('active', 0)} {summary.get('unit_label', '天有积分')}")



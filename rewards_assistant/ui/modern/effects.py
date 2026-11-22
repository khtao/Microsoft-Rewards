# -*- coding: utf-8 -*-
"""界面动效：入场淡入、逐项错开、主题切换淡入与生命周期保护。

按需求不再提供任何鼠标悬浮提示：界面上的说明一律用常驻只读文字呈现，
既没有自定义浮层，也不接管 / 拦截原生 tooltip。动画默认始终开启，
不再提供「减少动画」开关。
"""
from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QTimer
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

from . import viewmodel as vm


# ---------------------------------------------------------------- 入场 / 淡入

def stop_animations(root: QWidget) -> None:
    """重建界面前停掉旧控件上的入场动画。

    ``deleteLater()`` 之后，已排队 ``QTimer.singleShot(delay, anim.start)``
    仍会去启动一个 target 已失效的动画，Qt 会刷
    ``QPropertyAnimation::updateState (opacity): ... without target``。
    这里同时打上取消标记，让延迟回调直接返回。
    """
    for w in [root] + root.findChildren(QWidget):
        try:
            anims = list(getattr(w, "_hsa_anims", []) or [])
            fade = getattr(w, "_hsa_fade", None)
            for anim in anims + ([fade] if fade is not None else []):
                if anim is None:
                    continue
                try:
                    anim.setProperty("hsaCancelled", True)
                    anim.stop()
                except RuntimeError:
                    pass
            if anims:
                w._hsa_anims = []           # type: ignore[attr-defined]
            if fade is not None:
                w._hsa_fade = None          # type: ignore[attr-defined]
        except RuntimeError:
            pass


def _safe_start(anim: QPropertyAnimation, target: QWidget):
    """延迟启动闭包：控件已销毁或动画已取消则不再启动。"""
    def _start():
        try:
            if bool(anim.property("hsaCancelled")) or anim.parent() is None:
                return
            target.isVisible()
            # eff 可能已随 setGraphicsEffect(None) 被销毁：此时 targetObject
            # 是悬空 wrapper，访问其属性会抛 RuntimeError，必须直接放弃。
            obj = anim.targetObject()
            if obj is None:
                return
            obj.opacity()
        except (RuntimeError, AttributeError):
            return
        anim.start()
    return _start


def _clear_fade(target: QWidget, anim: QPropertyAnimation) -> None:
    """停动画并清 effect；先停后清，否则 Qt 会打印 "without target" 警告。"""
    try:
        anim.setProperty("hsaCancelled", True)
        anim.stop()
    except RuntimeError:
        pass
    try:
        target.setGraphicsEffect(None)
    except RuntimeError:
        pass


def _fade_restore(target: QWidget, anim: QPropertyAnimation):
    """绑定当次控件与动画的结束回调，循环里不会串到后一个控件。"""
    def _restore():
        _clear_fade(target, anim)
    return _restore


def stagger_enter(widgets: Iterable[QWidget]) -> None:
    """对齐插件版 ``.enter``：260ms 淡入，逐项错开 85ms。动画始终开启。"""
    items = [w for w in widgets if w is not None]
    for i, w in enumerate(items):
        eff = QGraphicsOpacityEffect(w)
        eff.setOpacity(0.0)
        w.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", w)
        anim.setDuration(vm.TRANSITION_MS)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        _restore = _fade_restore(w, anim)
        anim.finished.connect(_restore)
        delay = vm.stagger_delay(i)
        if delay:
            QTimer.singleShot(delay, _safe_start(anim, w))
        else:
            anim.start()
        # 兜底：页面在延迟期间被重建也不让控件停在 0 透明度
        QTimer.singleShot(delay + vm.TRANSITION_MS + 150, _restore)
        if not hasattr(w, "_hsa_anims"):
            w._hsa_anims = []               # type: ignore[attr-defined]
        w._hsa_anims.append(anim)           # type: ignore[attr-defined]


def fade_in(widget: QWidget) -> None:
    """主题 / 语言切换后的整页淡入（对齐 View Transition 的观感），始终开启。"""
    eff = QGraphicsOpacityEffect(widget)
    eff.setOpacity(0.0)
    widget.setGraphicsEffect(eff)
    anim = QPropertyAnimation(eff, b"opacity", widget)
    anim.setDuration(vm.TRANSITION_MS)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    _restore = _fade_restore(widget, anim)
    anim.finished.connect(_restore)
    QTimer.singleShot(vm.TRANSITION_MS + 150, _restore)
    widget._hsa_fade = anim                 # type: ignore[attr-defined]
    anim.start()

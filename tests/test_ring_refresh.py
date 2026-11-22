# -*- coding: utf-8 -*-
"""四项界面回归：

1. 自动运行期间主按钮变红（停止态），空闲恢复蓝；
2. 搜索任务的 60 分与活动任务的 +10 使用同款绿色圆角徽章；
3. 进度圆环 hover 有持续呼吸动效，且圆环内文字变成「点击刷新」；
4. 手动刷新从任务卡文字按钮移到圆环，运行 / 同步期拒绝点击。
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF  # noqa: E402
from PySide6.QtGui import QEnterEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton  # noqa: E402

from rewards_assistant.ui.modern import theme, viewmodel as vm  # noqa: E402
from rewards_assistant.ui.modern.main_window import HomePage  # noqa: E402
from rewards_assistant.ui.modern.widgets import (GroupBlock, RingProgress,
                                                 TaskRow)  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def home():
    qapp = QApplication.instance() or QApplication([])
    calls = []
    app = SimpleNamespace(
        lang="zh",
        sync_now=lambda *a, **k: calls.append("sync"),
        open_login=lambda: calls.append("login"),
        show_settings=lambda: calls.append("settings"),
        primary_clicked=lambda: calls.append("run"),
        stop_running=lambda: calls.append("stop"),
    )
    page = HomePage(app)
    yield page, calls, qapp
    page.deleteLater()
    qapp.processEvents()


def _enter(ring, qapp):
    ev = QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5))
    QApplication.sendEvent(ring, ev)
    qapp.processEvents()


def _leave(ring, qapp):
    QApplication.sendEvent(ring, QEvent(QEvent.Type.Leave))
    qapp.processEvents()


# ---------------------------------------------------------------- 1. 红色按钮

def test_primary_is_red_only_while_running(home):
    page, _, _ = home
    assert page.primary.property("danger") == "false"
    page.set_running(True)
    assert page.primary.property("danger") == "true"
    assert vm.tr(page.app.lang, "stop_run") in page.primary.text()
    page.set_running(False)
    assert page.primary.property("danger") == "false"


def test_stopping_state_stays_red(home):
    page, _, _ = home
    page.set_running(True, stopping=True)
    assert page.primary.property("danger") == "true"
    assert not page.primary.isEnabled()


def test_theme_defines_danger_button_rule_in_both_schemes():
    old = theme.scheme()
    try:
        for scheme in ("light", "dark"):
            theme.set_scheme(scheme)
            css = theme.qss() if hasattr(theme, "qss") else theme.stylesheet()
            assert '[danger="true"]' in css
            assert theme.current()["danger"] in css
            assert theme.current()["danger_hover"] in css
    finally:
        theme.set_scheme(old)


# ---------------------------------------------------------------- 2. 徽章统一

def test_search_badge_matches_activity_badge(home):
    groups = [
        {"label": "搜索", "earned": 60, "total": 60, "percent": 100,
         "items": [{"name": "今日 Bing 搜索", "description": "", "points": 60,
                    "complete": True}]},
        {"label": "每日活动", "earned": 30, "total": 30, "percent": 100,
         "items": [{"name": "即将举行的体育赛事", "description": "",
                    "points": 10, "complete": True}]},
    ]
    badges = []
    for g in groups:
        block = GroupBlock(g)
        for row in block.findChildren(TaskRow):
            badge = next(w for w in row.findChildren(QFrame)
                         if w.objectName() == "numBadge")
            label = badge.findChild(QLabel)
            badges.append((badge.objectName(), label.text()))
    assert badges == [("numBadge", "+60"), ("numBadge", "+10")]


def test_no_flat_badge_branch_left_in_widgets():
    src = (ROOT / "rewards_assistant" / "ui" / "modern" / "widgets.py").read_text("utf-8")
    assert 'setObjectName("numBadge" if accent else "flat")' not in src
    assert 'f"+{item[\'points\']}" if accent' not in src
    assert "TaskRow(item, accent=" not in src
    assert 'lay.addWidget(TaskRow(item))' in src


# ---------------------------------------------------------------- 3. hover 动效

def test_ring_hover_breathes_and_restores(home, monkeypatch):
    page, _, qapp = home
    ring = page.ring
    assert isinstance(ring, RingProgress)
    assert ring.hover_phase() == 0.0

    _enter(ring, qapp)
    assert ring.is_hovered()
    # 不依赖真实时间：直接推一帧，断言相位与绘制链路都不抛异常
    ring._on_hover_phase(0.8)
    assert ring.hover_phase() > 0.5
    ring.repaint()
    assert ring._refresh_caption == "点击刷新"

    _leave(ring, qapp)
    assert not ring.is_hovered()
    assert ring.hover_phase() == 0.0
    ring.repaint()


def test_disabled_ring_stops_hover(home):
    page, _, qapp = home
    ring = page.ring
    _enter(ring, qapp)
    assert ring.is_hovered()
    page.set_busy(True)
    assert not ring.isEnabled()
    assert not ring.is_hovered() and ring.hover_phase() == 0.0


def test_refresh_caption_follows_language(home):
    page, _, _ = home
    page.set_language("en")
    assert page.ring._refresh_caption == "Click to refresh"
    assert vm.tr("en", "empty_tasks") == "No tasks yet. Click the ring to sync."
    page.set_language("zh")
    assert page.ring._refresh_caption == "点击刷新"


# ---------------------------------------------------------------- 4. 刷新移到圆环

def test_ring_click_syncs_and_is_the_only_text_refresh(home):
    page, calls, qapp = home
    page.ring.click()
    assert calls == ["sync"]
    page.logo.click()
    assert calls == ["sync", "sync"]
    # 任务卡头部不再有「刷新」文字按钮
    texts = [b.text() for b in page.task_card.findChildren(QPushButton)]
    assert vm.tr(page.app.lang, "refresh") not in texts
    src = (ROOT / "rewards_assistant" / "ui" / "modern" / "main_window.py").read_text("utf-8")
    assert "refresh_btn" not in src
    assert "self.ring.clicked.connect(self._on_refresh_clicked)" in src
    qapp.processEvents()


def test_ring_click_is_ignored_while_running_or_syncing(home):
    page, calls, _ = home
    page.set_running(True)
    page.ring.click()
    page.logo.click()
    assert calls == []
    page.set_running(False)
    page.set_busy(True)
    page.ring.click()
    assert calls == []
    page.set_busy(False)
    page.ring.click()
    assert calls == ["sync"]


# ---------------------------------------------------------------- 5. 最大相位不裁切

def test_ring_never_clips_at_any_hover_phase(home):
    """截图里的 bug：相位 1 时光晕/RoundCap/末端高光点被 widget 边界切平。"""
    page, _, _ = home
    ring = page.ring
    for ph in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0):
        inset, arc_w, halo_w, tip_r, outer = ring.paint_geometry(ph)
        assert outer >= 4.0, (ph, outer)          # 至少 4px 余量
        assert inset > halo_w / 2.0 + tip_r * 0
        # 圆心到边缘留得下最粗笔刷的一半
        assert inset - halo_w / 2.0 > 0
    # 相位增大时向内收缩，而不是向外顶出边界
    assert ring.paint_geometry(1.0)[0] > ring.paint_geometry(0.0)[0]
    # 视觉直径保持约 150px
    assert abs((188 - 2 * ring.paint_geometry(0)[0]) - 150) < 1


def test_max_phase_render_is_round_and_no_exception(home):
    """最大相位 + 100% 满圆：渲染不抛异常，抓图尺寸正确。"""
    page, _, qapp = home
    ring = page.ring
    ring.set_value(100, animate=False)
    ring._start_hover()
    ring._on_hover_phase(1.0)
    ring.repaint()
    img = ring.grab().toImage()
    assert img.width() == ring.width() and img.height() == ring.height()
    # 最外一圈像素不得出现蓝色弧线（说明没有被裁成平边贴到边界）
    from PySide6.QtGui import QColor
    def bluish(color):
        return color.blue() > color.red() + 30 and color.blue() > 120 and color.alpha() > 40
    for x in (0, 1, ring.width() - 2, ring.width() - 1):
        for y in range(0, ring.height(), 4):
            assert not bluish(QColor(img.pixel(x, y))), (x, y)
    for y in (0, 1, ring.height() - 2, ring.height() - 1):
        for x in range(0, ring.width(), 4):
            assert not bluish(QColor(img.pixel(x, y))), (x, y)
    ring._stop_hover()

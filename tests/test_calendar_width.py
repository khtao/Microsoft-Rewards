# -*- coding: utf-8 -*-
"""积分日历四位数显示宽度回归。

日视图七列严格等宽（每格约 42px），数字标签原来只剩 29px，而四位数
10pt 粗体要 36px —— QLabel 会直接把右边裁掉。修复后按标签实际拿到的
宽度自动收字号，四位数完整显示且列宽不被撑开。

周 / 月视图是单列宽卡片，这里一并覆盖带千分位的五位数，防止以后更大
数字再次溢出。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import (QApplication, QSizePolicy, QVBoxLayout,  # noqa: E402
                               QWidget)

from rewards_assistant.ui.modern import theme  # noqa: E402
from rewards_assistant.ui.modern.viewmodel import calendar_summary  # noqa: E402
from rewards_assistant.ui.modern.widgets import CalendarCell, CalendarGrid  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def grid_in_324(qapp):
    """按设置页日历卡的真实可用宽度（324px）装配一个网格。"""
    theme.set_scheme("light")
    host = QWidget()
    host.setFixedWidth(324)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    grid = CalendarGrid()
    lay.addWidget(grid)
    host.show()
    for _ in range(8):
        qapp.processEvents()
    yield grid
    host.close()
    theme.set_scheme("light")


def _days(points) -> list[dict]:
    return [{"key": f"2026-11-{d:02d}", "label": f"11/{d}",
             "points": p, "has": True, "dots": [True], "unit": "day"}
            for d, p in zip(range(2, 2 + len(points)), points)]


def _settle(qapp, rounds: int = 10) -> None:
    for _ in range(rounds):
        qapp.processEvents()


# ---------------------------------------------------------------- 日视图

def test_four_digit_day_points_are_fully_visible(qapp, grid_in_324):
    grid = grid_in_324
    cells = _days((1200, 980, 2100, 2750, 1300, 9999, 1800))
    grid.set_cells(cells, "2026 / 11", calendar_summary(cells))
    _settle(qapp)
    built = grid.findChildren(CalendarCell)
    assert len(built) == 7
    for c in built:
        assert c.value_fits(), (c.value_text(), c._value.width(),
                                c.value_point_size())
        assert c.value_point_size() >= 8, "不能为了塞下而无限缩小到不可读"
    # 七个四位 / 三位数一个不少，没有被 QLabel 裁掉或改成省略号
    assert {c.value_text() for c in built} == {
        "1200", "980", "2100", "2750", "1300", "9999", "1800"}
    # 四位数收到 9pt，三位数仍保持 12pt，不无谓缩小
    sizes = {c.value_text(): c.value_point_size() for c in built}
    assert sizes["1200"] < sizes["980"] == 12


def test_seven_columns_stay_equally_wide(qapp, grid_in_324):
    """修复不能靠撑宽某一列实现：七列必须仍严格等宽、仍为 Ignored。"""
    grid = grid_in_324
    cells = _days((9999, 8888, 7777, 6666, 5555, 4444, 3333))
    grid.set_cells(cells, "2026 / 11", calendar_summary(cells))
    _settle(qapp)
    built = grid.findChildren(CalendarCell)
    widths = {c.width() for c in built}
    assert len(widths) == 1, widths
    assert all(c._value.sizePolicy().horizontalPolicy()
               == QSizePolicy.Policy.Ignored for c in built)
    assert all(c.value_fits() for c in built)


def test_day_view_uses_tighter_spacing(qapp, grid_in_324):
    grid_in_324.set_cells(_days((1200,)), "2026 / 11", {})
    assert grid_in_324._grid.spacing() == 5
    grid_in_324.set_cells(
        [{"label": "W1", "points": 1200, "dots": [True], "unit": "week"}],
        "2026 / 11", {})
    assert grid_in_324._grid.spacing() == 6


# ---------------------------------------------------------------- 周 / 月视图

def test_week_and_month_large_numbers_with_separators_fit(qapp, grid_in_324):
    grid = grid_in_324
    for unit, points in (("week", 12345), ("month", 98765)):
        cells = [{"label": "2026/11" if unit == "month" else "W1  11/2-11/8",
                  "points": points, "has": True, "dots": [True] * 7,
                  "unit": unit}]
        grid.set_cells(cells, "有积分的月份", calendar_summary(cells))
        _settle(qapp)
        c = grid.findChildren(CalendarCell)[0]
        assert c.value_text() == f"{points:,}"
        assert c.value_fits(), (unit, c.value_text(), c._value.width())
        assert c.value_point_size() >= 12


# ---------------------------------------------------------------- 生命周期

def _drop_grid_with_pending_refit() -> None:
    """造一个局部 grid、排队 0ms 回调，然后让它的 Python 引用消失。

    旧实现用全局 ``QTimer.singleShot(0, lambda: [...built...])`` 捕获格子列表：
    grid 一销毁，格子 C++ 对象被删，但回调仍在事件队列里；下一个用例只要
    ``processEvents()`` 就会抛 ``Internal C++ object already deleted``。
    """
    grid = CalendarGrid()
    grid.set_cells(_days((1200,)), "2026 / 11", {})
    return None


def test_pending_refit_never_touches_deleted_cells(qapp):
    _drop_grid_with_pending_refit()
    # 关键：这里处理的是上一个（已销毁）grid 排队的事件，绝不能抛异常
    for _ in range(12):
        qapp.processEvents()


def test_clear_grid_before_refit_is_safe(qapp):
    """切视图时旧格子被 deleteLater，待执行回调也不能碰它们。"""
    grid = CalendarGrid()
    grid.set_cells(_days((1200, 2400)), "2026 / 11", {})
    grid.set_cells(
        [{"label": "W1", "points": 9999, "dots": [True] * 7, "unit": "week"}],
        "2026 / 11", {})
    for _ in range(12):
        qapp.processEvents()
    cells = grid.findChildren(CalendarCell)
    assert len(cells) == 1 and cells[0].value_text() == "9,999"
    assert cells[0].value_fits()
    grid.deleteLater()
    for _ in range(6):
        qapp.processEvents()


def test_destroyed_cell_is_inert(qapp):
    """极端情况：外部仍持有已删格子的引用，方法也不能炸。

    ``deleteLater`` 在还有 Python 引用时不保证立刻销毁，这里显式删除 C++ 对象，
    精确复现报错里的 ``Internal C++ object already deleted``。
    """
    shiboken = pytest.importorskip("shiboken6")
    cell = CalendarCell({"label": "11/9", "points": 1200,
                         "has": True, "dots": [True], "unit": "day"},
                        compact=True)
    for _ in range(8):
        qapp.processEvents()
    shiboken.delete(cell)                  # 子 QLabel 一并销毁，Python 包装仍在
    assert cell._value_width() == -1
    assert cell.value_fits() is False
    assert cell.fit_value() == 8


# ---------------------------------------------------------------- 静态契约

def test_fit_logic_is_wired_into_contract():
    widgets = (ROOT / "rewards_assistant/ui/modern/widgets.py").read_text("utf-8")
    assert "def fit_value(" in widgets
    assert "def resizeEvent(" in widgets
    assert "QFontMetrics" in widgets
    assert "def _value_width(" in widgets
    assert "def _refit_cells(" in widgets
    assert "self._refit_timer = QTimer(self)" in widgets
    assert "self._refit_timer.start(0)" in widgets
    assert "self._refit_timer.stop()" in widgets
    # 全局 singleShot + 捕获旧控件列表的写法必须彻底消失
    assert "QTimer.singleShot(0, lambda" not in widgets
    assert "built" not in widgets
    assert "pad = 4 if compact else 12" in widgets
    assert "setSpacing(5 if columns == 7 else 6)" in widgets
    # 旧的硬编码「四位数一律 10pt」不再决定最终字号
    assert "f.setPointSize(10 if len(text) >= 4 else 12)" not in widgets

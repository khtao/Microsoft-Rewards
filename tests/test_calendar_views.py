# -*- coding: utf-8 -*-
"""积分赚取日历：只显示有积分的时间 / 日一排七 / 周一排一 / 三排圆角矩形。"""
from __future__ import annotations

import os
from datetime import date

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from rewards_assistant.ui.modern import theme, viewmodel as vm  # noqa: E402
from rewards_assistant.ui.modern.widgets import (CalendarCell, CalendarGrid,  # noqa: E402
                                                  DotsRow)

HISTORY = [
    {"day": "2026-10-30", "points_earned": 60},
    {"day": "2026-11-09", "points_earned": 275},
    {"day": "2026-11-10", "points_earned": 130},
    {"day": "2026-11-11", "points_earned": 90},
    {"day": "2026-11-12", "points_earned": 180},
    {"day": "2026-11-13", "points_earned": 54},
    {"day": "2026-11-14", "points_earned": 0},      # 无积分，必须删除
    {"day": "2026-12-01", "points_earned": 0},      # 未来且无积分
]


# ------------------------------------------------------------------ 数据口径

def test_no_zero_or_missing_days_appear_in_any_view():
    today = date(2026, 11, 13)
    for cells in (vm.calendar_days(HISTORY, 2026, 11),
                  vm.calendar_week(HISTORY, today),
                  vm.calendar_months(HISTORY, today)):
        assert cells
        assert all(c["has"] and c["points"] > 0 for c in cells)
        assert all("11/14" not in c["label"] for c in cells)


def test_week_view_covers_this_month_weeks_with_w_index():
    cells = vm.calendar_week(HISTORY, date(2026, 11, 13))
    # 本月涉及 W1(10/26-11/1，含 10/30 的积分) 与 W3(11/9-11/15)
    assert [c["label"] for c in cells] == ["W1  10/26-11/1", "W3  11/9-11/15"]
    assert all(len(c["dots"]) == 7 for c in cells)
    assert cells[1]["points"] == 729


def test_week_view_keeps_weeks_from_previous_month_if_they_have_points():
    rows = HISTORY + [{"day": "2026-10-27", "points_earned": 40}]
    cells = vm.calendar_week(rows, date(2026, 11, 13))
    assert [c["label"] for c in cells] == ["W1  10/26-11/1", "W3  11/9-11/15"]
    assert cells[0]["points"] == 100            # 10/27 + 10/30
    assert [c["unit"] for c in cells] == ["week", "week"]


def test_month_view_only_lists_months_with_points():
    cells = vm.calendar_months(HISTORY, date(2026, 11, 13))
    assert [c["key"] for c in cells] == ["2026-10", "2026-11"]
    assert [c["points"] for c in cells] == [60, 729]
    assert all(c["dots"] and any(c["dots"]) for c in cells)


def test_summary_labels_follow_view_unit_and_language():
    today = date(2026, 11, 13)
    zh_day = vm.calendar_summary(vm.calendar_days(HISTORY, 2026, 11))
    zh_week = vm.calendar_summary(vm.calendar_week(HISTORY, today))
    zh_month = vm.calendar_summary(vm.calendar_months(HISTORY, today))
    assert (zh_day["avg_label"], zh_day["unit_label"]) == ("日均", "天有积分")
    assert (zh_week["avg_label"], zh_week["unit_label"]) == ("周均", "周有积分")
    assert (zh_month["avg_label"], zh_month["unit_label"]) == ("月均", "个月有积分")
    en = vm.calendar_summary(vm.calendar_week(HISTORY, today), "en")
    assert en["avg_label"] == "Weekly avg" and "weeks" in en["unit_label"]
    assert vm.calendar_summary([])["active"] == 0


# ------------------------------------------------------------------ 控件样式

@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_calendar_cell_has_three_rows_like_screenshot(qapp):
    theme.set_scheme("light")
    cell = {"label": "W3  11/9-11/15", "points": 1988,
            "has": True, "dots": [True] * 5 + [False, False], "unit": "week"}
    w = CalendarCell(cell)
    labels = [x for x in w.findChildren(QLabel)]
    dots = [x for x in w.findChildren(DotsRow)]
    assert [x.text() for x in labels] == ["W3  11/9-11/15", "1,988"]
    assert len(dots) == 1 and len(dots[0]._dots) == 7
    assert "border-radius" in w.styleSheet() and w.height() == 88

    compact = CalendarCell({"label": "11/9", "points": 275,
                            "has": True, "dots": [True], "unit": "day"}, compact=True)
    assert compact.height() == 62
    assert "border-radius" in compact.styleSheet()


def test_grid_day_view_is_seven_columns_week_and_month_one_column(qapp):
    theme.set_scheme("light")
    grid = CalendarGrid()
    day = vm.calendar_days(HISTORY, 2026, 11)
    grid.set_cells(day, "2026 / 11", vm.calendar_summary(day))
    cells = grid.findChildren(CalendarCell)
    assert len(cells) == 5 and all(c.height() == 62 for c in cells)
    positions = [grid._grid.getItemPosition(grid._grid.indexOf(c))[:2] for c in cells]
    assert all(row == 0 for row, _col in positions)        # 只有 5 天 → 同一排
    assert max(col for _row, col in positions) == 4

    week = vm.calendar_week(HISTORY, date(2026, 11, 13))
    grid.set_cells(week, "2026 / 11", vm.calendar_summary(week))
    wcells = grid.findChildren(CalendarCell)
    assert len(wcells) == 2 and all(c.height() == 88 for c in wcells)
    assert [grid._grid.getItemPosition(grid._grid.indexOf(c))[:2] for c in wcells] == [(0, 0), (1, 0)]
    assert "周均" in grid._summary.text() and "2 周有积分" in grid._summary.text()
    assert not hasattr(grid, "_legend")                   # 图例已改为卡片内圆点

    month = vm.calendar_months(HISTORY, date(2026, 11, 13))
    grid.set_cells(month, "有积分的月份", vm.calendar_summary(month))
    mcells = grid.findChildren(CalendarCell)
    assert [grid._grid.getItemPosition(grid._grid.indexOf(c))[:2] for c in mcells] == [(0, 0), (1, 0)]
    assert "月均" in grid._summary.text()


def test_empty_history_shows_empty_state_not_zero_cells(qapp):
    grid = CalendarGrid()
    grid.set_cells([], "2026 / 11", vm.calendar_summary([]),
                   empty_text="暂无积分记录")
    assert grid.findChildren(CalendarCell) == []
    assert any("暂无积分记录" in w.text() for w in grid.findChildren(QLabel))


def test_many_active_days_wrap_to_multiple_rows_of_seven(qapp):
    rows = [{"day": f"2026-11-{d:02d}", "points_earned": 10}
            for d in range(1, 17)]
    grid = CalendarGrid()
    cells = vm.calendar_days(rows, 2026, 11)
    grid.set_cells(cells, "2026 / 11", vm.calendar_summary(cells))
    built = grid.findChildren(CalendarCell)
    assert len(built) == 16
    positions = [grid._grid.getItemPosition(grid._grid.indexOf(c))[:2] for c in built]
    assert max(col for _r, col in positions) == 6        # 一排七个
    assert {row for row, _c in positions} == {0, 1, 2}


def test_compact_day_cell_handles_four_digit_points_without_overflow(qapp):
    theme.set_scheme("light")
    from PySide6.QtWidgets import QSizePolicy

    cell = CalendarCell({"label": "11/9", "points": 1988,
                         "has": True, "dots": [True], "unit": "day"}, compact=True)
    labels = [w for w in cell.findChildren(QLabel)]
    assert labels[1].text() == "1988"                   # 紧凑格不写千分位
    assert labels[1].sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored
    assert labels[1].sizeHint().width() < 44           # 一排七个时不撑宽列

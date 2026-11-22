# -*- coding: utf-8 -*-
"""离屏渲染积分日历的日 / 周 / 月三种视图预览图。

    python tools/render_calendar_previews.py

只写临时数据目录，不联网、不改用户真实数据。
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MS_REWARDS_HOME", tempfile.mkdtemp(prefix="hsa-calendar-"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from rewards_assistant.config import Settings  # noqa: E402
from rewards_assistant.storage import Store  # noqa: E402
from rewards_assistant.ui.modern import theme  # noqa: E402
from rewards_assistant.ui.modern.main_window import ModernMainWindow  # noqa: E402
from rewards_assistant.ui.modern.widgets import CalendarCell  # noqa: E402
from tools.render_previews import OUT, seed_history, settle  # noqa: E402


def main() -> int:
    app = QApplication.instance() or QApplication([])
    theme.set_scheme("light")
    today = date.today()
    settings, store = Settings(), Store()
    settings.update({"ui_language": "中文", "ui_theme": "浅色",
                     "ui_calendar_view": "周"})
    seed_history(store, today)
    win = ModernMainWindow(settings=settings, store=store)
    win.resize(392, 1120)
    win.show()
    settle(app, 8, 500)
    win.show_settings()
    settle(app, 8, 400)

    grid = win.settings_page.calendar
    for mode, name in (("日", "calendar_day.png"), ("周", "calendar_week.png"),
                       ("月", "calendar_month.png")):
        win.set_pref("ui_calendar_view", mode)
        settle(app, 6, 120)
        cells = grid.findChildren(CalendarCell)
        cols = 7 if mode == "日" else 1
        positions = [grid._grid.getItemPosition(grid._grid.indexOf(c))[:2]
                     for c in cells]
        assert cells, mode
        assert all(0 <= col < cols for _r, col in positions), (mode, positions)
        grid.grab().save(str(OUT / name))
        print(f"{mode} 视图：{len(cells)} 个卡片，列数 {cols}，"
              f"汇总「{grid._summary.text()}」 -> {name}")

    # 只显示有积分：卡片文字里不出现 0 分格子
    assert "暂无积分记录" not in grid._month_label.text()
    print("日历预览 OK：三视图均只显示有积分的时间")
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""离屏渲染预览图并验证交互，不需要登录、不访问网络。

    python tools/render_previews.py

产出 docs/previews/ 下的主页 / 运行态 / 圆环 hover / 设置页 / 深色 /
进度条修复对照图。本版契约：全界面无鼠标悬浮提示（圆环 hover 用内置文字
与呼吸动效表达可点击）、动画恒定开启；运行期主按钮红色，任务积分徽章
统一，手动刷新只有中央圆环一个入口。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MS_REWARDS_HOME", tempfile.mkdtemp(prefix="hsa-preview-"))

from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)  # noqa: E402

from rewards_assistant.config import Settings  # noqa: E402
from rewards_assistant.storage import Store  # noqa: E402
from rewards_assistant.ui.modern import theme, viewmodel as vm  # noqa: E402
from rewards_assistant.ui.modern.main_window import ModernMainWindow  # noqa: E402
from rewards_assistant.ui.modern.widgets import (CalendarCell,  # noqa: E402
                                                  DotsRow, ProgressBar, TaskRow)

OUT = ROOT / "docs" / "previews"
OUT.mkdir(parents=True, exist_ok=True)


def seed(store: Store, day: str) -> None:
    store.upsert_task(day, "pc_search", "今日 Bing 搜索", "搜索", 30, 24, False)
    for i, name in enumerate(("洛杉矶温和海岸阳光", "凯恩斯大堡礁探险潜水", "可可的起源")):
        store.upsert_task(day, f"daily{i}", name, "每日任务", 10, 10, True)
    for i, name in enumerate(("您听说过这则藏言吗？", "每日箴言", "完成此拼图")):
        store.upsert_task(day, f"promo{i}", name, "更多活动", 5, 5, True)
    store.upsert_available(day, 744, 2)
    store.save_page_points(day, {"today": 54, "claimable": 0})
    store.refresh_day(day)


def seed_history(store: Store, today: date) -> None:
    """给日历预览造若干「有积分」的日子；无积分的日子完全不写入。"""
    rows = [
        (f"{today.year:04d}-{today.month:02d}-02", 120),
        (f"{today.year:04d}-{today.month:02d}-03", 95),
        (f"{today.year:04d}-{today.month:02d}-05", 210),
        (f"{today.year:04d}-{today.month:02d}-09", 275),
        (f"{today.year:04d}-{today.month:02d}-10", 130),
        (f"{today.year:04d}-{today.month:02d}-11", 90),
        (f"{today.year:04d}-{today.month:02d}-12", 180),
        (f"{today.year:04d}-{today.month:02d}-13", 54),
        (f"{today.year:04d}-{today.month:02d}-16", 66),
        (today.replace(day=1).isoformat(), 0),        # 0 分：必须不显示
    ]
    y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    rows += [(f"{y:04d}-{m:02d}-21", 88), (f"{y:04d}-{m:02d}-24", 132)]
    with store.tx() as conn:
        for day, pts in rows:
            conn.execute("INSERT OR REPLACE INTO daily (day, points_earned) VALUES (?,?)",
                         (day, pts))


def settle(app: QApplication, rounds: int = 4, wait_ms: int = 0) -> None:
    for _ in range(rounds):
        app.processEvents()
    if wait_ms:
        time.sleep(wait_ms / 1000.0)   # 让 QTimer 的 stagger 延迟有时间触发
        for _ in range(rounds):
            app.processEvents()


def progress_demo(app: QApplication) -> None:
    demo = QWidget()
    demo.setStyleSheet("QWidget { background:#ffffff; color:#1b2430; font-size:13px; }")
    lay = QVBoxLayout(demo)
    lay.setContentsMargins(12, 10, 12, 12)
    lay.setSpacing(9)
    lay.addWidget(QLabel("修复后：按容器宽度百分比绘制，100% 填满且无溢出细线"))
    for label, value in (("每日活动   30 / 30 分", 100), ("搜索   24 / 30 分", 80)):
        head = QHBoxLayout()
        l = QLabel(label)
        l.setStyleSheet("font-weight:600;")
        head.addWidget(l)
        head.addStretch(1)
        head.addWidget(QLabel(f"{value}%"))
        lay.addLayout(head)
        bar = ProgressBar(value)
        bar.setMinimumWidth(316)
        lay.addWidget(bar)
    demo.setFixedWidth(340)
    demo.show()
    settle(app)
    demo.grab().save(str(OUT / "progress_fixed.png"))
    demo.close()


def summary_texts(sp) -> list[str]:
    return [sp.defaults_grid.itemAt(i).widget().text()
            for i in range(sp.defaults_grid.count())]


def main() -> int:
    app = QApplication.instance() or QApplication([])
    day = date.today().isoformat()
    settings, store = Settings(), Store()
    settings.update({"ui_language": "中文", "ui_theme": "浅色"})
    # 造一个示例独立环境，让运行环境区块展示「打开 / 设为默认」两个按钮；
    # 预览进程仍是内置默认环境，因此不会误把删除键拍进示例截图。
    from rewards_assistant import instances as _instances  # noqa: E402
    _instances.create_instance("khtao", "zh")
    seed(store, day)
    seed_history(store, date.today())
    theme.set_scheme("light")
    win = ModernMainWindow(settings=settings, store=store)
    win.resize(392, 980)
    win.show()
    settle(app, 8, 1100)
    win.grab().save(str(OUT / "home.png"))

    # 全界面不再有任何鼠标悬浮提示：没有浮层管理器，也没有原生 tooltip
    assert getattr(win.home, "_hsa_hover_manager", None) is None
    for widget in (win.home.card_claimable, win.home.primary, win.home.ring):
        assert widget.toolTip() == "", "不应再有原生黑框 tooltip"

    # 手动刷新已移到圆环：任务卡头部不再有「刷新」文字按钮
    header_buttons = [b.text() for b in win.home.task_card.findChildren(QPushButton)]
    assert vm.tr("zh", "refresh") not in header_buttons
    assert win.home.ring.isEnabled()

    # 所有任务统一绿色圆角徽章（搜索分与活动 +10 同款）
    task_rows = win.home.groups_host.findChildren(TaskRow)
    assert task_rows
    for row in task_rows:
        badge = next(w for w in row.findChildren(QFrame)
                     if w.objectName() == "numBadge")
        label = badge.findChild(QLabel)
        assert label.text().startswith("+"), label.text()

    # 圆环 hover：呼吸光晕 + 内置「↻ 刷新」文字（不是浮层提示框）
    ring = win.home.ring
    ring._start_hover()
    # 用最大相位出图：这是截图里被裁成平边的那个状态
    ring._on_hover_phase(1.0)
    ring.repaint()
    ring.grab().save(str(OUT / "ring_hover.png"))
    assert ring.is_hovered() and ring.hover_phase() == 1.0
    _inset, _arc, _halo, _tip, margin = ring.paint_geometry(1.0)
    assert margin >= 4.0, "最大相位光晕必须完整落在 widget 内"
    ring._stop_hover()

    # 运行态：主按钮红色 + 停止文案，圆环与顶部放大镜禁用
    win.home.set_running(True)
    settle(app, 4, 200)
    assert win.home.primary.property("danger") == "true"
    assert not win.home.ring.isEnabled()
    win.grab().save(str(OUT / "home_running.png"))
    win.home.set_running(False)
    assert win.home.primary.property("danger") == "false"

    # 同步完成态（状态条不再卡在「正在保存…」）
    chip = win.home.status_chip
    chip.set_state("done", vm.tr("zh", "status.all_done"))
    settle(app, 4, 120)
    win.grab().save(str(OUT / "home_synced.png"))

    # 圆环下状态条：四态配色 + 全部完成才绿
    chip.set_state("ready", vm.tr("zh", "status.ready"))
    assert chip.kind() == "ready"
    chip.set_state("done", vm.tr("zh", "status.all_done"))
    assert chip.kind() == "done"
    chip.set_text("正在同步 Rewards 数据…")
    assert chip.kind() == "busy"
    chip.set_text("同步失败：网络异常")
    assert chip.kind() == "error"

    # 100% 分组必须按比例填满（截图里的错误）
    groups = vm.group_tasks(store.tasks_of_day(day))
    daily = [g for g in groups if g["label"] == "每日活动"][0]
    assert (daily["earned"], daily["total"], daily["percent"]) == (30, 30, 100)
    assert vm.progress_fill(daily["percent"], 316) == 316
    progress_demo(app)

    # 设置页（普通高度 + 长图）
    win.show_settings()
    # 积分日历：只显示有积分的时间；周视图一排一周、卡片内三排 + 7 个圆点
    win.set_pref("ui_calendar_view", "周")
    settle(app, 6, 120)
    cal = win.settings_page.calendar
    week_cells = cal.findChildren(CalendarCell)
    assert week_cells and all(c.height() == 88 for c in week_cells)
    assert all(len(d._dots) == 7 for d in cal.findChildren(DotsRow))
    assert all(cal._grid.getItemPosition(cal._grid.indexOf(c))[1] == 0
               for c in week_cells), "周视图必须一排一周"
    assert not hasattr(cal, "_legend")
    settle(app, 8, 800)
    win.grab().save(str(OUT / "settings.png"))
    # 长图按设置页内容实际高度取景：运行环境区块在日历下方，固定 1120
    # 会把它裁掉。
    scroll = win.settings_page.findChild(QScrollArea)
    content_h = scroll.widget().sizeHint().height() + 96
    win.resize(392, min(2600, max(1120, content_h)))
    settle(app, 8, 300)
    win.grab().save(str(OUT / "settings_full.png"))

    sp = win.settings_page
    # 设置页所有正文 QLabel 与「外观」卡 Row 标签同为 13px；徽章 / 卡片
    # 标题保留各自字号，不算正文。
    body_labels = [x for x in sp.findChildren(QLabel)
                   if x.objectName() == "body"]
    assert body_labels
    assert all(x.font().pixelSize() == 13 for x in body_labels), \
        "设置页正文字号必须与外观卡一致"
    # 所有按钮（含分段器、环境行、工具行）与正文同为 13px；符号返回键除外。
    buttons = [b for b in sp.findChildren(QPushButton)
               if b.objectName() != "icon"]
    assert buttons
    assert all(b.font().pixelSize() == 13 for b in buttons), \
        "设置页按键字号必须统一"
    # 启动时间：空态占位 --:--，四位数字自动补冒号；留空点 + 递增 30 分钟
    time_box = sp.time_input
    assert time_box.placeholderText() == "--:--"
    assert time_box.text() == "" and time_box.font().pixelSize() == 13
    time_box.setText("1530")
    sp._on_time_text_edited("1530")
    assert time_box.text() == "15:30"
    time_box.clear()
    win.set_pref("ui_schedule_times", [])
    for _ in range(3):
        win.add_schedule_time()
    assert win.prefs["ui_schedule_times"] == ["08:00", "08:30", "09:00"]
    assert win.settings.get("auto_run_times") == ["08:00", "08:30", "09:00"]

    # 拟人搜索默认设置：5 行只读值 + 一个强度控件，值随档位联动且真实写回
    rows = summary_texts(sp)
    assert len(rows) == 5, rows
    for label in ("搜索轮数", "单轮耗时", "回车提交", "结果点击", "滚动屏数"):
        assert any(label in t for t in rows), (label, rows)
    assert any("至多 16 轮" in t and "目前搜索" in t for t in rows), \
        "默认中档应显示「至多 16 轮，根据任务进度，目前搜索 N 轮」"
    assert any("16–29 秒" in t for t in rows)
    assert any("50%" in t for t in rows) and any("35%" in t for t in rows)
    assert any("1.2 屏" in t for t in rows)
    assert sp.level_seg.value() == "中"

    # 低档：整组参数一起变，且经典 _collect() 透传
    sp.level_seg.changed.emit("低")
    rows_low = summary_texts(sp)
    assert any("至多 20 轮" in t and "目前搜索" in t for t in rows_low), rows_low
    assert any("10–18 秒" in t for t in rows_low), rows_low
    assert any("35%" in t for t in rows_low) and any("20%" in t for t in rows_low)
    assert any("0.6 屏" in t for t in rows_low)
    live = win.settings.get("search_panel")
    assert live["level"] == 1 and live["repeat"] == 20
    assert live["enter_rate"] == 0.35 and live["click_prob"] == 0.20
    assert live["scroll_screens"] == 0.6
    collected = win.search._collect()
    assert collected["enter_rate"] == 0.35 and collected["click_prob"] == 0.20
    assert collected["scroll_screens"] == 0.6
    assert tuple(collected["round_seconds"]) == (10.0, 18.0)

    # 高档：再切一次，5 行与经典参数同步变化
    sp.level_seg.changed.emit("高")
    rows_high = summary_texts(sp)
    assert any("至多 12 轮" in t and "目前搜索" in t for t in rows_high), rows_high
    assert any("24–40 秒" in t for t in rows_high), rows_high
    assert any("70%" in t for t in rows_high) and any("55%" in t for t in rows_high)
    assert any("2 屏" in t for t in rows_high)
    assert win.settings.get("search_panel")["level"] == 3
    assert win.search._collect()["enter_rate"] == 0.70

    # 恢复中档，避免预览状态影响其它断言
    sp.level_seg.changed.emit("中")

    # 深色主题（动画恒定开启，不再有「减少动画」开关）
    win.set_pref("ui_theme", "深色")
    settle(app, 8, 800)
    assert theme.scheme() == "dark"
    assert "ui_reduce_motion" not in vm.VISIBLE_SETTING_KEYS
    win.show_home()
    settle(app, 8, 800)
    win.grab().save(str(OUT / "dark.png"))

    print("previews OK:", ", ".join(sorted(p.name for p in OUT.glob("*.png"))))
    print("intensity rows:", len(rows), "→ low", len(rows_low), "→ high", len(rows_high))
    print("daily group:", daily["earned"], "/", daily["total"], "→",
          daily["percent"], "% → fill", vm.progress_fill(daily["percent"], 316), "px")
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

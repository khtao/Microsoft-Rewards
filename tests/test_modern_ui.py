# -*- coding: utf-8 -*-
"""复刻界面测试：全部离线，不创建 QWidget / QApplication。

覆盖分组、进度、三项积分缺失显示、日历三种视图、主题 QSS、文案、
设置默认值合并，以及「不引入自动化执行链路」的静态契约。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from rewards_assistant.config import Settings
from rewards_assistant.ui.modern import theme, viewmodel as vm

ROOT = Path(__file__).parents[1]


def row(**kw):
    base = {"key": "", "name": "", "category": "", "description": "",
            "points_max": 0, "points_earned": 0, "complete": 0, "link": ""}
    base.update(kw)
    return base


TASKS = [
    row(key="pc_search", name="今日 Bing 搜索", category="搜索",
        points_max=30, points_earned=24, complete=0),
    row(key="a", name="洛杉矶温和海岸阳光", category="每日任务",
        points_max=10, points_earned=10, complete=1, description="阳光明媚的天空"),
    row(key="b", name="凯恩斯大堡礁探险潜水", category="每日任务",
        points_max=10, points_earned=10, complete=1),
    row(key="c", name="可可的起源", category="每日任务",
        points_max=10, points_earned=10, complete=1),
    row(key="d", name="您听说过这则藏言吗？", category="更多活动",
        points_max=5, points_earned=5, complete=1),
    row(key="e", name="每日箴言", category="更多活动",
        points_max=5, points_earned=5, complete=1),
    row(key="f", name="完成此拼图", category="更多活动",
        points_max=5, points_earned=5, complete=1),
]


# ---------------------------------------------------------------- 主面板

def test_progress_matches_screenshot_ballpark():
    p = vm.progress_of(TASKS)
    assert p["earned"] == 69 and p["total"] == 75
    assert p["percent"] == 92 and p["done"] == 6 and p["count"] == 7
    assert p["complete"] is False


def test_group_tasks_uses_screenshot_labels_and_order():
    groups = vm.group_tasks(TASKS)
    assert [g["label"] for g in groups] == ["搜索", "每日活动", "日常任务"]
    assert groups[0]["earned"] == 24 and groups[0]["total"] == 30
    assert groups[1]["earned"] == 30 and groups[1]["total"] == 30
    assert groups[2]["earned"] == 15 and groups[2]["total"] == 15
    assert groups[1]["items"][0]["name"] == "洛杉矶温和海岸阳光"


def test_points_text_never_fakes_zero():
    assert vm.points_text(None) == "--"
    assert vm.points_text(0) == "0"
    assert vm.points_text(1234) == "1,234"


def test_empty_tasks_is_zero_percent_not_division_error():
    p = vm.progress_of([])
    assert p["percent"] == 0 and p["complete"] is False


# ---------------------------------------------------------------- 日历

HISTORY = [
    {"day": "2026-11-09", "points_earned": 275},
    {"day": "2026-11-10", "points_earned": 130},
    {"day": "2026-11-11", "points_earned": 90},
    {"day": "2026-11-12", "points_earned": 180},
    {"day": "2026-11-13", "points_earned": 54},
]


def test_month_total_and_day_cells_only_keep_days_with_points():
    """未来 / 过去没有积分的日子全部删除。"""
    assert vm.month_total(HISTORY, 2026, 11) == 729
    cells = vm.calendar_days(HISTORY, 2026, 11)
    assert len(cells) == 5
    assert [c["label"] for c in cells] == ["11/9", "11/10", "11/11", "11/12", "11/13"]
    assert cells[0]["points"] == 275
    assert all(c["has"] and c["unit"] == "day" and c["dots"] == [True] for c in cells)


def test_week_cells_are_month_weeks_with_seven_dots():
    """本月涉及到的周 = 一排一周；W3 11/9-11/15 与截图一致。"""
    cells = vm.calendar_week(HISTORY, date(2026, 11, 13))
    assert len(cells) == 1
    c = cells[0]
    assert c["label"] == "W3  11/9-11/15"
    assert c["points"] == 729 and c["unit"] == "week"
    assert c["dots"] == [True, True, True, True, True, False, False]
    s = vm.calendar_summary(cells)
    assert s["max"] == 729 and s["active"] == 1 and s["avg"] == 729
    assert s["avg_label"] == "周均" and s["unit_label"] == "周有积分"


def test_month_cells_only_keep_months_with_points():
    cells = vm.calendar_months(HISTORY, date(2026, 11, 13))
    assert len(cells) == 1
    assert cells[0]["key"] == "2026-11" and cells[0]["points"] == 729
    assert cells[0]["has"] is True and cells[0]["unit"] == "month"
    assert any(cells[0]["dots"])


# ---------------------------------------------------------------- 主题 / 文案

def test_theme_switch_changes_stylesheet_colors():
    theme.set_scheme("light")
    light = theme.stylesheet()
    theme.set_scheme("dark")
    dark = theme.stylesheet()
    assert "#f2f5fa" in light and "#141922" in dark
    assert "QFrame#card" in dark and "QPushButton#primary" in dark
    assert theme.current()["card"] == "#202836"
    theme.set_scheme("light")


def test_resolve_theme_and_language():
    assert theme.resolve_theme("浅色") == "light"
    assert theme.resolve_theme("深色") == "dark"
    assert vm.resolve_language("中文") == "zh"
    assert vm.resolve_language("English") == "en"
    assert vm.tr("zh", "available") == "可用积分"
    assert vm.tr("en", "available") == "Available"


def test_settings_values_read_classic_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    s = Settings()
    vals = vm.settings_values(s)
    assert vals["ui_theme"] == "跟随系统"
    # 时间表直接来自经典 auto_run_times（默认 ["09:00"]）
    assert vals["ui_schedule_times"] == ["09:00"]
    assert vals["ui_schedule_enabled"] is False
    assert vals["ui_search_level"] == "中"
    s.update({"ui_theme": "深色", "search_panel": {"level": 3, "repeat": 17}})
    again = vm.settings_values(Settings())
    assert again["ui_theme"] == "深色"
    assert again["ui_search_level"] == "高"
    assert again["ui_repeat"] == 17 and again["ui_search_cap"] == 17


# ---------------------------------------------------------------- 设置绑定

def test_pref_to_settings_writes_real_classic_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    s = Settings()
    v = vm.settings_values(s)
    cases = [
        ("ui_search_level", "高", "search_panel"),
        ("ui_repeat", 23, "search_panel"),
        ("ui_search_cap", "30", "search_panel"),
        ("ui_keywords", "旅行, 美食", "search_panel"),
        ("ui_sources", "baidu,bing", "search_panel"),
        ("ui_auto_sync", False, "sync_interval_minutes"),
        ("ui_sync_interval", 37, "sync_interval_value_minutes"),
        ("ui_remind_enabled", False, "remind_time"),
        ("ui_remind_time", "13:47", "remind_time_value"),
        ("ui_evening_time", "21:15", "evening_remind_time_value"),
        ("ui_notify_after_sync", False, "notify_after_sync"),
        ("ui_silent_all_done", False, "silent_when_all_done"),
        ("ui_close_to_tray", False, "close_to_tray"),
        ("ui_sleep_all_done", True, "sleep_when_all_done"),
        ("ui_launch_at_login", True, "launch_at_login"),
        ("ui_chart_days", 90, "chart_days"),
        ("ui_schedule_enabled", True, "auto_run_enabled"),
        ("ui_schedule_times", ["08:00", "12:00"], "auto_run_times"),
    ]
    for key, value, target in cases:
        patch = vm.pref_to_settings(v, key, value)
        assert target in patch, (key, patch)
    assert vm.pref_to_settings(v, "ui_search_level", "高")["search_panel"]["level"] == 3
    assert vm.pref_to_settings(v, "ui_auto_sync", False)["sync_interval_minutes"] == 0
    assert vm.pref_to_settings(v, "ui_remind_enabled", False)["remind_time"] == ""
    assert vm.pref_to_settings(v, "ui_theme", "深色") == {"ui_theme": "深色"}


def test_pref_patch_survives_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    s = Settings()
    v = vm.settings_values(s)
    s.update(vm.pref_to_settings(v, "ui_search_level", "低"))
    s.update(vm.pref_to_settings(vm.settings_values(s), "ui_repeat", 42))
    s.update(vm.pref_to_settings(vm.settings_values(s), "ui_schedule_times",
                                 ["07:30", "18:30"]))
    again = vm.settings_values(Settings())
    assert again["ui_search_level"] == "低"
    assert again["ui_repeat"] == 42 and again["ui_search_cap"] == 42
    assert again["ui_schedule_times"] == ["07:30", "18:30"]
    assert Settings().get("search_panel")["level"] == 1
    assert Settings().get("auto_run_times") == ["07:30", "18:30"]


def test_reset_patch_only_touches_bound_keys():
    patch = vm.reset_patch()
    assert "sleep_until_day" not in patch and "prune_version" not in patch
    assert patch["auto_run_enabled"] is False
    assert patch["auto_run_times"] == ["09:00"]
    assert "search_panel" in patch and "sync_interval_minutes" in patch


# ---------------------------------------------------------------- 静态契约

def test_only_one_mode_exists():
    root = ROOT / "rewards_assistant"
    assert not (root / "web_app.py").exists()
    assert not (root / "modern_app.py").exists()
    assert not (root / "ui" / "web").exists()
    assert not (root / "ui" / "main_window.py").exists()
    assert not (root / "ui" / "panels.py").exists()
    assert (root / "ui" / "legacy.py").is_file()
    entry = (root / "__main__.py").read_text(encoding="utf8")
    assert "app import main as app_main" in entry
    assert "--widgets" in entry and "--classic" in entry
    assert "modern_app" not in entry and "web_app" not in entry
    req = (ROOT / "requirements.txt").read_text(encoding="utf8")
    # 只看依赖声明行，注释里可以解释为什么不用扩展包
    dep_lines = [l.strip() for l in req.splitlines()
                 if l.strip() and not l.lstrip().startswith("#")]
    assert not any("Addons" in l or "WebEngine" in l for l in dep_lines)
    assert any(l.startswith("PySide6") for l in dep_lines)
    assert any(l.startswith("requests") for l in dep_lines)
    assert any(l.startswith("playwright") for l in dep_lines)
    spec = (ROOT / "MSRewardsAssistant.spec").read_text(encoding="utf8")
    # 只检查可执行代码行；注释里允许解释「为什么不用 QtWebEngine」
    spec_code = "\n".join(l for l in spec.splitlines()
                          if l.strip() and not l.lstrip().startswith("#"))
    assert "QtWebEngine" not in spec_code and "ui/web" not in spec_code
    # 构建脚本与手写 spec 不能再次分叉；Qt 由官方 hook 按 import 图收集
    assert "collect_all('PySide6')" not in spec_code
    ubuntu = (ROOT / "build_ubuntu.sh").read_text(encoding="utf8")
    win = (ROOT / "build_windows.bat").read_text(encoding="utf8")
    assert "MSRewardsAssistant.spec" in ubuntu and "MSRewardsAssistant.spec" in win
    assert "PyInstaller" in ubuntu and "--noconfirm --clean" in ubuntu


def test_primary_button_runs_the_same_classic_entrypoint():
    """主页主按钮 / 托盘动作 / 经典托盘全部落到 Application.run_pending_tasks。"""
    app_src = (ROOT / "rewards_assistant" / "app.py").read_text(encoding="utf8")
    win_src = (ROOT / "rewards_assistant" / "ui" / "modern" /
               "main_window.py").read_text(encoding="utf8")
    # 托盘空闲态仍与主页主按钮共用经典入口；运行态改走停止。
    assert "self.act_tray_run.triggered.connect(self._on_tray_run)" in app_src
    assert "self.window.quick.tasks_btn.click()" in app_src
    assert "self.window.stop_running()" in app_src
    assert "def run_pending_tasks(self) -> None:" in app_src
    assert "_request_run_pending(scheduled=False)" in app_src
    assert "self.controller.run_pending_tasks()" in win_src
    assert "self.quick.run_pending_requested.connect(self._run_pending)" in win_src
    assert "primary.clicked.connect(self._on_primary_clicked)" in win_src
    assert "self.app.primary_clicked()" in win_src
    assert "self.app.stop_running()" in win_src
    # 经典执行内核沿用同一份实现
    legacy = (ROOT / "rewards_assistant" / "ui" / "legacy.py").read_text(encoding="utf8")
    for name in ("class SearchRunner", "class SearchWorker", "class SearchPanel",
                 "class QuickPanel", "run_human_search", "pending_desktop_rounds"):
        assert name in legacy, name
    # 控制器仍使用经典 SyncWorker / ActivityWorker
    for name in ("SyncWorker(self.store", "ActivityWorker(", "_run_pending_from_snapshot",
                 "_start_activity_clicks", "_start_pc_search"):
        assert name in app_src, name


def test_window_implements_full_classic_facade():
    src = (ROOT / "rewards_assistant" / "ui" / "modern" /
           "main_window.py").read_text(encoding="utf8")
    for method in ("def refresh_all", "def set_busy", "def set_status_message",
                   "def set_login_status", "def update_running_state",
                   "def run_pending_tasks", "def stop_running", "def sync_now"):
        assert method in src, method
    for attr in ("self.runner = SearchRunner", "self.quick = QuickPanel",
                 "self.search = SearchPanel", "self.act_sync = QAction",
                 "self.account = _AccountFacade"):
        assert attr in src, attr
    # 不再有自己的定时调度器（统一由 Application._tick 驱动）
    assert "_tick_schedule" not in src


def test_settings_page_uses_classic_binding():
    src = (ROOT / "rewards_assistant" / "ui" / "modern" /
           "main_window.py").read_text(encoding="utf8")
    assert "vm.pref_to_settings" in src and "vm.reset_patch()" in src
    assert "search_panel._collect" not in src
    vmm = (ROOT / "rewards_assistant" / "ui" / "modern" /
           "viewmodel.py").read_text(encoding="utf8")
    for key in ("sync_interval_minutes", "auto_run_times", "remind_time_value",
                "search_panel.level", "notify_after_sync", "launch_at_login"):
        assert key in vmm, key

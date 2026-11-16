"""v1.3 余额日差、任务回退、旧库迁移和低内存休眠回归。"""
from __future__ import annotations

import ast
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from rewards_assistant.config import Settings
from rewards_assistant.points import BALANCE_DELTA, TASK_FALLBACK, calculate_daily_points
from rewards_assistant.rewards_client import parse_dashboard
from rewards_assistant.storage import Store


def _task(store, day, pts, complete=False):
    store.upsert_task(day=day, key="pc_search", name="测试搜索", category="搜索",
                      points_max=100, points_earned=pts, complete=complete)


def test_calculate_points_requires_both_balances_and_accepts_zero():
    assert calculate_daily_points(1080, 1000, 12) == (80, BALANCE_DELTA, 1000)
    assert calculate_daily_points(0, 0, 12) == (0, BALANCE_DELTA, 0)
    assert calculate_daily_points(990, 1000, 12) == (-10, BALANCE_DELTA, 1000)
    assert calculate_daily_points(1000, None, 12) == (12, TASK_FALLBACK, None)
    assert calculate_daily_points(None, 1000, 12) == (12, TASK_FALLBACK, None)


def test_yesterday_missing_uses_task_sum_and_today_snapshot_not_fake_baseline(tmp_path):
    store = Store(str(tmp_path / "r.sqlite"))
    _task(store, "2026-11-02", 35)
    store.upsert_available("2026-11-02", 1250, 2)
    row = store.refresh_day("2026-11-02")
    assert row["points_earned"] == 35
    assert row["points_source"] == TASK_FALLBACK
    assert row["baseline_points"] is None
    assert row["available_synced_at"]
    _task(store, "2026-11-02", 50)
    assert store.refresh_day("2026-11-02")["points_earned"] == 50


def test_balance_delta_overrides_task_sum_and_refresh_does_not_change_it(tmp_path):
    store = Store(str(tmp_path / "r.sqlite"))
    store.upsert_available("2026-11-01", 1000, 2)
    _task(store, "2026-11-02", 35)
    store.upsert_available("2026-11-02", 1155, 2)
    row = store.refresh_day("2026-11-02")
    assert row["points_earned"] == 155
    assert row["points_source"] == BALANCE_DELTA
    assert row["baseline_points"] == 1000
    assert row["points_max"] == 100
    _task(store, "2026-11-02", 85)
    assert store.refresh_day("2026-11-02")["points_earned"] == 155


def test_zero_balance_is_valid_and_negative_delta_is_preserved(tmp_path):
    store = Store(str(tmp_path / "r.sqlite"))
    store.upsert_available("2026-11-01", 0, 2)
    store.upsert_available("2026-11-02", 0, 2)
    _task(store, "2026-11-02", 35)
    assert store.refresh_day("2026-11-02")["points_earned"] == 0
    store.upsert_available("2026-11-01", 200, 2)
    row = store.refresh_day("2026-11-02")
    assert row["points_earned"] == -200
    assert row["points_source"] == BALANCE_DELTA
    assert row["baseline_points"] == 200


def test_nonconsecutive_previous_snapshot_does_not_count(tmp_path):
    store = Store(str(tmp_path / "r.sqlite"))
    store.upsert_available("2026-10-30", 500, 2)
    store.upsert_available("2026-11-02", 900, 2)
    _task(store, "2026-11-02", 45)
    assert store.refresh_day("2026-11-02")["points_earned"] == 45


def test_missing_current_balance_does_not_use_default_zero(tmp_path):
    store = Store(str(tmp_path / "r.sqlite"))
    store.upsert_available("2026-11-01", 1000, 2)
    _task(store, "2026-11-02", 30)
    row = store.refresh_day("2026-11-02")
    assert row["points_earned"] == 30
    assert row["points_source"] == TASK_FALLBACK
    assert row["available_synced_at"] is None


@pytest.mark.parametrize("value,valid,expected", [
    (None, False, 0), ("1,234", True, 1234), (0, True, 0),
    (False, False, 0), (-1, False, 0), ("not a number", False, 0),
])
def test_dashboard_balance_validity(value, valid, expected):
    user = {} if value is None else {"availablePoints": value}
    snap = parse_dashboard({"dashboard": {"userStatus": user}}, "2026-11-02")
    assert snap.balance_valid is valid
    assert snap.available_points == expected


def test_backfill_yesterday_recalculates_tomorrow(tmp_path):
    store = Store(str(tmp_path / "r.sqlite"))
    _task(store, "2026-11-02", 40)
    store.upsert_available("2026-11-02", 1500, 2)
    assert store.refresh_day("2026-11-02")["points_earned"] == 40
    store.upsert_available("2026-11-01", 1430, 2)
    store.refresh_day("2026-11-01")
    row = store.daily("2026-11-02")
    assert row["points_earned"] == 70
    assert row["points_source"] == BALANCE_DELTA


def test_legacy_earn_records_migrated_without_earn_calculation(tmp_path):
    path = tmp_path / "old.sqlite"
    store = Store(str(path))
    for day, bal, task in [("2026-11-01", 1000, 30), ("2026-11-02", 1100, 15)]:
        _task(store, day, task)
        store.upsert_available(day, bal, 2)
        store.refresh_day(day)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE daily SET points_source='earn', points_earned=999, "
                     "official_points_earned=999 WHERE day='2026-11-02'")
        conn.commit()
    reopened = Store(str(path))
    row = reopened.daily("2026-11-02")
    assert row["points_earned"] == 100
    assert row["points_source"] == BALANCE_DELTA
    assert row["official_points_earned"] is None


def test_old_db_zero_balance_not_assumed_to_be_valid(tmp_path):
    path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript("""CREATE TABLE daily (
        day TEXT PRIMARY KEY, points_earned INTEGER DEFAULT 0, points_max INTEGER DEFAULT 0,
        tasks_done INTEGER DEFAULT 0, tasks_total INTEGER DEFAULT 0,
        available_points INTEGER DEFAULT 0, level INTEGER DEFAULT 0,
        streak INTEGER DEFAULT 0, all_done INTEGER DEFAULT 0, synced_at TEXT);
        INSERT INTO daily VALUES ('2026-11-01', 500, 100, 1, 1, 0, 2, 1, 1, '2026-11-01T12:00:00');
        """)
    conn.close()
    store = Store(str(path))
    assert store.daily("2026-11-01")["available_synced_at"] is None


def test_stats_include_both_sources_and_net_negative(tmp_path):
    store = Store(str(tmp_path / "stats.sqlite"))
    month = date.today().replace(day=1)
    day1 = month.isoformat()
    day2 = (month + timedelta(days=1)).isoformat()
    _task(store, day1, 25)
    store.upsert_available(day1, 500, 2)
    store.refresh_day(day1)
    _task(store, day2, 20)
    store.upsert_available(day2, 480, 2)
    store.refresh_day(day2)
    stats = store.stats()
    assert stats["total_points"] == 5
    assert stats["month_points"] == 5
    assert stats["month_fallback_days"] == 1


def test_simplified_settings_drop_obsolete_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    settings = Settings()
    assert settings.update({"sleep_when_all_done": True,
                            "earn_browser_fallback": True,
                            "streak_rule": "all_done",
                            "auto_open_gap_min": 1,
                            "search_panel": {"repeat": 20, "level": 3, "headless": True,
                                             "copy_profile": True, "keep_open_seconds": 900}})
    loaded = Settings()
    assert loaded.get("sleep_when_all_done") is True
    assert loaded.get("earn_browser_fallback") is None
    assert loaded.get("streak_rule") is None
    assert loaded.get("auto_open_gap_min") is None
    assert loaded.get("search_panel") == {"repeat": 20, "level": 3}


def _get_method(name):
    source = (Path(__file__).parents[1] / "rewards_assistant" / "app.py").read_text("utf8")
    tree = ast.parse(source)
    clazz = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Application")
    func = next(n for n in clazz.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return func, source


def test_suspended_tick_does_not_trigger_other_scheduled_actions():
    method, _ = _get_method("_tick")
    scope = {"datetime": datetime}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), "<ast>", "exec"), scope)

    class Stub:
        _sleep_day = date.today().isoformat()
        calls = []
        def _wake_from_sleep(self, **kw): self.calls.append("wake")
        def sync_now(self, **kw): self.calls.append("sync")

    stub = Stub()
    scope["_tick"](stub)
    assert stub.calls == []


def test_suspended_tick_wakes_after_next_calendar_day():
    method, _ = _get_method("_tick")
    scope = {"datetime": datetime}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), "<ast>", "exec"), scope)

    class Stub:
        def __init__(self):
            self._sleep_day = (date.today() - timedelta(days=1)).isoformat()
            self._today = date.today()
            self._next_sync = None
            self.calls = []
            self.settings = type("S", (), {"get": lambda *_: None})()
            self._fired = set()
        def _wake_from_sleep(self, **kw):
            self.calls.append("wake")
            self._sleep_day = ""

    stub = Stub()
    scope["_tick"](stub)
    assert stub.calls == ["wake"]


def test_memory_safe_ui_and_menu_contract_static():
    method, source = _get_method("_build_tray")
    # 只按 AST 取 _build_tray 里 addAction 的菜单标签（整文件源码含注释里的
    # 「刷新界面」等词，不能用全文子串判断）。
    labels = [n.args[0].value for n in ast.walk(method)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "addAction" and n.args
              and isinstance(n.args[0], ast.Constant)
              and isinstance(n.args[0].value, str)]
    assert sorted(labels) == sorted(["显示主页面", "立即同步", "自动完成任务", "退出"])
    for gone in ("今日完成后休眠至明天", "仅打开未完成任务页", "刷新界面",
                 "真正退出程序", "停止"):
        assert gone not in labels, gone
    assert "登录微软账号\").triggered.connect" not in source
    assert "self._sleep_day = today" in source
    assert "window.deleteLater()" in source
    ui = (Path(__file__).parents[1] / "rewards_assistant" / "ui" / "legacy.py").read_text("utf8")
    assert "setMaximumBlockCount(350)" in ui
    assert "class SearchRunner" in ui
    assert "bar.addAction(act_login)" not in ui
    assert "self.chk_headless" not in ui
    assert "self.chk_copy" not in ui
    assert not (Path(__file__).parents[1] / "rewards_assistant" / "ui" / "main_window.py").exists()


def test_browser_cleanup_on_normal_run_and_launch_failure_source_contract():
    source = (Path(__file__).parents[1] / 'rewards_assistant' / 'api.py').read_text('utf8')
    assert 'pw = None  # Playwright' in source
    assert 'if own_context and close_browser:' in source
    assert 'pw.stop()' in source
    assert 'except BaseException:' in source
    assert source.count('pw.stop()') >= 2


def test_task_remaining_does_not_subtract_daily_net_from_task_limit():
    source = (Path(__file__).parents[1] / 'rewards_assistant' / 'app.py').read_text('utf8')
    assert 'left_points = sum(max(0, int(t["points_max"]) - int(t["points_earned"]))' in source
    assert 'left_points = int(row["points_max"]) - int(row["points_earned"])' not in source


def test_chart_handles_negative_balance_delta_static():
    """经典 ChartView 已随旧界面删除；负净变化仍由 points.py 如实显示。"""
    from rewards_assistant.points import points_display, BALANCE_DELTA
    root = Path(__file__).parents[1] / "rewards_assistant"
    assert not (root / "ui" / "chart_view.py").exists()
    assert "-20" in points_display(BALANCE_DELTA, -20)
    assert "无昨日余额" in points_display("task_fallback", 10)


def test_sqlite_thread_connection_can_be_closed_and_reopened(tmp_path):
    store = Store(str(tmp_path / "connections.sqlite"))
    store.upsert_available("2026-11-01", 100, 2)
    conn = store._connect()
    store.close_thread_connection()
    assert store._connect() is not conn
    assert store.daily("2026-11-01")["available_points"] == 100


def test_earn_scraping_is_removed_from_sync_pipeline():
    root = Path(__file__).parents[1] / "rewards_assistant"
    sync = (root / "sync.py").read_text(encoding="utf8")
    app = (root / "app.py").read_text(encoding="utf8")
    assert not (root / "earn_points.py").exists()
    assert "fetch_today_points_in_edge" not in sync
    assert "parse_earn_today_points" not in sync
    assert "earn_browser_fallback" not in app
    assert "finally:\n            self.store.close_thread_connection()" in sync


def test_source_labels_for_chart_and_history_are_consistent():
    from rewards_assistant.points import points_source_label, points_display
    assert points_source_label(BALANCE_DELTA) == "账户余额日差"
    assert points_source_label(TASK_FALLBACK) == "任务积分合计（回退）"
    assert "-20" in points_display(BALANCE_DELTA, -20)
    assert "无昨日余额" in points_display(TASK_FALLBACK, 10)


def test_single_schedule_and_manual_open_entry_not_duplicated():
    root = Path(__file__).parents[1] / "rewards_assistant"
    app = (root / "app.py").read_text(encoding="utf8")
    legacy = (root / "ui" / "legacy.py").read_text(encoding="utf8")
    assert 'self.settings.get("auto_open_time")' not in app
    assert 'self.auto_open_time' not in legacy
    # 托盘已精简为 4 项，手动打开入口不再有重复菜单项
    assert '"仅打开未完成任务页（手动完成）"' not in app
    assert 'self._after_sync.add("open")' in app

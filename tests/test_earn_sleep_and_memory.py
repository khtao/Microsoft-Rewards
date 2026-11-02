"""新增重点回归：Earn 真实来源、旧数据迁移、设置与低内存休眠。"""
from __future__ import annotations

import ast
from datetime import date, datetime
from pathlib import Path

import pytest

from rewards_assistant.config import Settings
from rewards_assistant.earn_points import parse_earn_today_points
from rewards_assistant.storage import Store


@pytest.mark.parametrize("html,expected", [
    ("<div>Today's points: 42</div>", 42),
    ("<span>今日获得积分：0</span>", 0),
    ("<script>{\"todayPointsEarned\":135}</script>", 135),
    ("<script>{\"pointsEarnedToday\":46}</script>", 46),
    ("<div data-testid='today-points'><b>Today's points</b><strong>66</strong></div>", 66),
    ("<p>Today's points: 50 / 100</p>", 50),
    ("<div>任务积分 999，可用积分 32881</div>", None),
    ("<script>{\"pointsEarnedToday\":21,\"todayPointsEarned\":22}</script>", None),
    ("Today's points: 25; Today's points: 28", None),
    ("", None),
    ("<div data-testid='today-points'>今日积分 45 和另一统计 90</div>", 45),
])
def test_earn_parser_only_accepts_unambiguous_daily_point_value(html, expected):
    assert parse_earn_today_points(html) == expected


def _task(store, day, pts, complete=False):
    store.upsert_task(day=day, key="pc_search", name="测试搜索", category="搜索",
                      points_max=100, points_earned=pts, complete=complete)


def test_earn_total_overrides_tasks_and_remains_after_subsequent_unverified_sync(tmp_path):
    store = Store(str(tmp_path / "rewards.sqlite"))
    day = "2026-11-02"
    _task(store, day, 35)
    initial = store.refresh_day(day)
    assert initial["points_earned"] == 35
    assert initial["points_source"] == "estimated"
    assert initial["official_points_earned"] is None

    actual = store.refresh_day(day, official_points=155)
    assert actual["points_earned"] == 155
    assert actual["official_points_earned"] == 155
    assert actual["points_source"] == "earn"
    assert actual["points_max"] == 100  # 上限属于任务，不与 Earn 今日积分混为一谈。

    _task(store, day, 85)
    again = store.refresh_day(day)  # 页面暂时不可访问时不回退成求和。
    assert again["points_earned"] == 155
    assert again["points_source"] == "earn"
    assert store.tasks_of_day(day)[0]["points_earned"] == 85


def test_zero_earn_today_is_verified_not_missing(tmp_path):
    store = Store(str(tmp_path / "r.db"))
    _task(store, "2026-11-02", 30)
    row = store.refresh_day("2026-11-02", official_points=0)
    assert row["points_earned"] == 0
    assert row["points_source"] == "earn"


def test_negative_earn_points_rejected_without_overwriting(tmp_path):
    store = Store(str(tmp_path / "r.db"))
    day = "2026-11-02"
    _task(store, day, 15)
    store.refresh_day(day)
    with pytest.raises(ValueError):
        store.refresh_day(day, official_points=-2)
    assert store.daily(day)["points_source"] == "estimated"


def test_old_database_points_are_retained_but_not_claimed_official(tmp_path):
    store = Store(str(tmp_path / "r.db"))
    day = "2024-05-02"
    _task(store, day, 65)
    store.refresh_day(day)
    after_reopen = Store(str(tmp_path / "r.db"))
    old = after_reopen.daily(day)
    assert old["points_earned"] == 65
    assert old["points_source"] == "estimated"


def test_streak_setting_recomputes_many_days_and_best_streak(tmp_path):
    store = Store(str(tmp_path / "r.db"))
    # 450天历史，覆盖旧 400 天重算上限。
    from datetime import timedelta
    start = date(2025, 1, 1)
    for i in range(450):
        day = (start + timedelta(days=i)).isoformat()
        _task(store, day, 10, complete=False)
        store.refresh_day(day)
    assert store.best_streak() == 450
    store.set_streak_rule("all_done")
    assert store.best_streak() == 0
    assert store.daily((start + timedelta(days=449)).isoformat())["streak"] == 0
    store.set_streak_rule("any_points")
    assert store.best_streak() == 450
    assert store.daily((start + timedelta(days=449)).isoformat())["streak"] == 450


def test_sleep_and_streak_preferences_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    settings = Settings()
    assert settings.get("sleep_when_all_done") is False
    settings.update({"sleep_when_all_done": True, "sleep_until_day": "2026-11-02",
                     "streak_rule": "all_done", "close_to_tray": False})
    again = Settings()
    assert again.get("sleep_until_day") == "2026-11-02"
    assert again.get("sleep_when_all_done") is True
    assert again.get("streak_rule") == "all_done"
    assert again.get("close_to_tray") is False


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
    from datetime import timedelta

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
    assert "今日完成后休眠至明天" in source
    assert "登录微软账号\").triggered.connect" not in source
    assert "self._sleep_day = today" in source
    assert "window.deleteLater()" in source
    ui = (Path(__file__).parents[1] / "rewards_assistant" / "ui" / "main_window.py").read_text("utf8")
    assert "setMaximumBlockCount(350)" in ui
    assert "_refresh_active_detail" in ui
    assert "bar.addAction(act_login)" not in ui


def test_browser_cleanup_on_normal_run_and_launch_failure_source_contract():
    source = (Path(__file__).parents[1] / 'rewards_assistant' / 'api.py').read_text('utf8')
    assert 'pw = None  # Playwright' in source
    assert 'if own_context and close_browser:' in source
    assert 'pw.stop()' in source
    assert 'except BaseException:' in source
    assert source.count('pw.stop()') >= 2


def test_store_stats_exclude_old_estimates(tmp_path):
    store = Store(str(tmp_path / 'stats.sqlite'))
    old, new = '2026-08-01', date.today().isoformat()
    _task(store, old, 400)
    store.refresh_day(old)  # 旧数据不应算作已验证历史积分。
    _task(store, new, 25)
    store.refresh_day(new, official_points=10)
    stats = store.stats()
    assert stats['total_points'] == 10
    assert stats['month_points'] == 10


def test_earn_breakdown_parser_accepts_day_not_month_or_balance():
    from rewards_assistant.earn_points import parse_earn_breakdown_text
    assert parse_earn_breakdown_text('Today: 65\nThis month: 160\nThis year: 2010') == 65
    assert parse_earn_breakdown_text('This month: 160\nThis year: 2010') is None
    assert parse_earn_breakdown_text('Today\'s points: 0\nBalance: 10000') == 0
    assert parse_earn_breakdown_text('Today: 100') is None  # 无月份/明细上下文，不猜。


def test_task_remaining_does_not_subtract_official_earn_from_task_limit():
    source = (Path(__file__).parents[1] / 'rewards_assistant' / 'app.py').read_text('utf8')
    assert 'left_points = sum(max(0, int(t["points_max"]) - int(t["points_earned"]))' in source
    assert 'left_points = int(row["points_max"]) - int(row["points_earned"])' not in source

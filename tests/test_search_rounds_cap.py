# -*- coding: utf-8 -*-
"""「至多 N 轮，根据任务进度，目前搜索 M 轮」的文本与行为一致性。

真实日志里强度=高（上限 16）却依次跑了 12 / 7 / 4 / 2 轮：
设置页原来写死「16 轮」，既没表达上限，也和按任务剩余进度递减的实际
轮数冲突；而经典 ``for_pending()`` 只用 need、完全没有把档位上限当硬约束。

本轮把两者统一到 ``pending.planned_rounds()``：N 是档位上限，M 是
min(剩余电脑搜索次数, N)，设置页显示与执行链路取同一个 M。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from rewards_assistant.config import Settings
from rewards_assistant.pending import pending_desktop_rounds, planned_rounds
from rewards_assistant.storage import Store
from rewards_assistant.ui.legacy import SearchRunner
from rewards_assistant.ui.modern import viewmodel as vm

ROOT = Path(__file__).resolve().parents[1]
LEGACY_SRC = (ROOT / "rewards_assistant" / "ui" / "legacy.py").read_text("utf-8")
WIN_SRC = (ROOT / "rewards_assistant" / "ui" / "modern" /
           "main_window.py").read_text("utf-8")
README = (ROOT / "README.md").read_text("utf-8")


def _tasks(points_max: int, points_earned: int = 0, *, complete=False):
    return [dict(key="pc_search", name="今日 Bing 搜索", category="搜索",
                 points_max=points_max, points_earned=points_earned,
                 complete=complete, link="", offer_id="")]


def _prefs(choice: str) -> dict:
    p = dict(vm.UI_DEFAULTS)
    p["ui_search_level"] = choice
    p["ui_intensity_params"] = vm.intensity_profile(choice)
    return p


def _store(tmp_path, monkeypatch, points_max=60, points_earned=0):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    st = Store()
    day = date.today().isoformat()
    st.upsert_task(day, "pc_search", "今日 Bing 搜索", "搜索",
                   points_max, points_earned, False, False, "", "official", "", "")
    st.refresh_day(day)
    return st, day


# ---------------------------------------------------------------- 纯计算

def test_planned_rounds_is_min_of_need_and_cap():
    # 60 分剩余 / 5 分一次 = 12 次；高档上限 16 → 目前 12 轮
    assert pending_desktop_rounds(_tasks(60)) == 12
    assert planned_rounds(_tasks(60), 16) == 12
    # 100 分剩余 = 20 次，但「至多 16 轮」是硬约束
    assert planned_rounds(_tasks(100), 16) == 16
    # 任务进度推进后按剩余次数递减：35 / 20 / 10 分 → 7 / 4 / 2 轮
    for left, expected in ((35, 7), (20, 4), (10, 2), (0, 0)):
        assert planned_rounds(_tasks(60, 60 - left), 16) == expected
    # 无电脑搜索待办 / 已完成 / 非法上限都不启动
    assert planned_rounds([], 16) == 0
    assert planned_rounds([dict(key="mobile_search", points_max=60,
                                points_earned=0, complete=False)], 16) == 0
    assert planned_rounds(_tasks(60, 60, complete=True), 16) == 0
    assert planned_rounds(_tasks(60), 0) == 0
    assert planned_rounds(_tasks(60), "bad") == 0


def test_repeat_cap_follows_intensity():
    assert [vm.repeat_cap(_prefs(c)) for c in ("低", "中", "高")] == [8, 12, 16]
    assert vm.repeat_cap({"ui_search_level": "不存在"}) == 12


def test_summary_first_row_states_cap_and_current_rounds():
    high = dict(vm.default_settings_summary(_prefs("高"), "zh", current_rounds=12))
    assert high["搜索轮数"] == "至多 16 轮，根据任务进度，目前搜索 12 轮"
    low = dict(vm.default_settings_summary(_prefs("低"), "zh", current_rounds=8))
    assert low["搜索轮数"] == "至多 8 轮，根据任务进度，目前搜索 8 轮"
    done = dict(vm.default_settings_summary(_prefs("高"), "zh", current_rounds=0))
    assert done["搜索轮数"] == "至多 16 轮，根据任务进度，目前搜索 0 轮"
    en = dict(vm.default_settings_summary(_prefs("中"), "en", current_rounds=7))
    assert en["Search rounds"] == (
        "Up to 12 rounds, 7 planned now based on task progress")
    # 其余 5 行仍是档位参数，不受新文本影响
    assert high["单轮耗时"] == "24–40 秒" and high["地址栏搜索"] == "25%"


# ---------------------------------------------------------------- 行为一致

def test_for_pending_uses_the_same_planned_rounds(tmp_path, monkeypatch):
    st, day = _store(tmp_path, monkeypatch, 60, 0)
    settings = {"repeat": 16, "level": 3}          # 高档上限 16
    p = SearchRunner.for_pending(st, settings)
    assert p is not None and p["repeat"] == 12
    assert p["repeat"] == planned_rounds(list(st.tasks_of_day(day)),
                                         vm.repeat_cap(_prefs("高")))


def test_cap_is_a_hard_limit_when_more_rounds_are_pending(tmp_path, monkeypatch):
    """100 分剩余本来要 20 轮，强度=高时至多只跑 16 轮。"""
    st, day = _store(tmp_path, monkeypatch, 100, 0)
    assert pending_desktop_rounds(list(st.tasks_of_day(day))) == 20
    p = SearchRunner.for_pending(st, {"repeat": 16, "level": 3})
    assert p["repeat"] == 16
    mid = SearchRunner.for_pending(st, {"repeat": 12, "level": 2})
    assert mid["repeat"] == 12
    low = SearchRunner.for_pending(st, {"repeat": 8, "level": 1})
    assert low["repeat"] == 8


def test_log_sequence_12_7_4_2_matches_summary_and_behaviour(tmp_path, monkeypatch):
    """复现用户日志：高档 16，但四次运行按任务进度依次 12 / 7 / 4 / 2。"""
    st, day = _store(tmp_path, monkeypatch, 60, 0)
    for earned, expected in ((0, 12), (25, 7), (40, 4), (50, 2)):
        st.set_task_complete(day, "pc_search", earned >= 60)
        st.upsert_task(day, "pc_search", "今日 Bing 搜索", "搜索",
                       60, earned, earned >= 60, False, "", "official", "", "")
        st.refresh_day(day)
        tasks = list(st.tasks_of_day(day))
        shown = dict(vm.default_settings_summary(
            _prefs("高"), "zh",
            current_rounds=planned_rounds(tasks, vm.repeat_cap(_prefs("高")))))
        p = SearchRunner.for_pending(st, {"repeat": 16, "level": 3})
        assert f"目前搜索 {expected} 轮" in shown["搜索轮数"], shown
        assert p is not None and p["repeat"] == expected, (earned, p)


def test_all_done_shows_zero_and_does_not_start(tmp_path, monkeypatch):
    st, day = _store(tmp_path, monkeypatch, 60, 60)
    st.set_task_complete(day, "pc_search", True)
    st.refresh_day(day)
    tasks = list(st.tasks_of_day(day))
    assert planned_rounds(tasks, 16) == 0
    shown = dict(vm.default_settings_summary(
        _prefs("高"), "zh", current_rounds=planned_rounds(tasks, 16)))
    assert shown["搜索轮数"] == "至多 16 轮，根据任务进度，目前搜索 0 轮"
    assert SearchRunner.for_pending(st, {"repeat": 16, "level": 3}) is None


def test_settings_patch_writes_cap_that_execution_reads(tmp_path, monkeypatch):
    """切强度 → 写 search_panel.repeat → 「至多」与执行上限同步变化。"""
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    s = Settings()
    for choice, cap in (("低", 8), ("中", 12), ("高", 16)):
        s.update(vm.pref_to_settings(vm.settings_values(s), "ui_search_level", choice))
        v = vm.settings_values(s)
        assert vm.repeat_cap(v) == cap
        assert s.get("search_panel")["repeat"] == cap


# ---------------------------------------------------------------- 接线契约

def test_for_pending_calls_shared_planned_rounds():
    assert "planned_rounds(tasks, cap)" in LEGACY_SRC
    assert "p['repeat'] = min(need, 200)" not in LEGACY_SRC
    assert "from ..pending import planned_rounds" in LEGACY_SRC


def test_settings_page_computes_current_rounds_from_store():
    assert "from ...pending import planned_rounds" in WIN_SRC
    assert "def current_search_rounds(self) -> int:" in WIN_SRC
    assert "vm.repeat_cap(self.app.prefs)" in WIN_SRC
    assert "current_rounds=self.current_search_rounds()" in WIN_SRC
    # 首行长文本整行显示，不塞进两列网格
    assert "self.defaults_grid.addWidget(cell, 0, 0, 1, 2)" in WIN_SRC


def test_sync_refreshes_current_rounds():
    """每次同步后官方进度可能变了，摘要必须跟着刷新。"""
    block = WIN_SRC.split("def refresh_all")[1].split("def set_busy")[0]
    assert "refresh_defaults_summary()" in block
    assert "self.refresh()" in block


def test_readme_documents_cap_not_fixed_rounds():
    assert "至多 8 轮" in README and "至多 12 轮" in README and "至多 16 轮" in README
    assert "根据任务进度" in README
    assert "搜索轮数 | 8 轮" not in README


def test_hidden_classic_panel_label_says_cap():
    assert 'QLabel("搜索轮数上限")' in LEGACY_SRC

# -*- coding: utf-8 -*-
"""「至多 N 轮，根据任务进度，目前搜索 M 轮」的文本与行为一致性。

次数只有一个来源：min(ceil(搜索任务剩余积分 ÷ 3), 拟人强度档位的最多次数)。
设置页摘要与经典 ``for_pending()`` 共用 ``pending.planned_rounds()``，
显示值即执行值；不叠加全局硬顶、会员等级封顶或「至少 1 轮」保底。
档位预设：低 20 / 中 16 / 高 12 次。
"""
from __future__ import annotations

import inspect
from datetime import date
from pathlib import Path

from rewards_assistant.config import Settings
from rewards_assistant.pending import pending_desktop_rounds, planned_rounds
from rewards_assistant.search_terms import POINTS_PER_SEARCH
from rewards_assistant.storage import Store
from rewards_assistant.ui.legacy import SearchRunner
from rewards_assistant.ui.modern import viewmodel as vm

ROOT = Path(__file__).resolve().parents[1]
LEGACY_SRC = (ROOT / "rewards_assistant" / "ui" / "legacy.py").read_text("utf-8")
PENDING_SRC = (ROOT / "rewards_assistant" / "pending.py").read_text("utf-8")
VIEWMODEL_SRC = (ROOT / "rewards_assistant" / "ui" / "modern" /
                 "viewmodel.py").read_text("utf-8")
WIN_SRC = (ROOT / "rewards_assistant" / "ui" / "modern" /
           "main_window.py").read_text("utf-8")
ARCH = (ROOT / "docs" / "ARCHITECTURE.md").read_text("utf-8")


def _tasks(points_max: int, points_earned: int = 0, *, complete=False):
    return [dict(key="pc_search", name="今日 Bing 搜索", category="search",
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
    assert POINTS_PER_SEARCH == 3
    # 用户给定的两个例子：强度=中，最多 16 次
    assert pending_desktop_rounds(_tasks(51)) == 17       # 51 / 3 = 17
    assert planned_rounds(_tasks(51), 16) == 16
    assert planned_rounds(_tasks(6), 16) == 2           # 6 / 3 = 2
    # 剩余积分不能整除 3 时向上取整，不丢掉尾巴
    assert pending_desktop_rounds(_tasks(7)) == 3
    assert pending_desktop_rounds(_tasks(1)) == 1
    # 上限是唯一封顶：60 分 → 20 次，100 分 → 34 次，中档都取 16
    assert pending_desktop_rounds(_tasks(60)) == 20
    assert pending_desktop_rounds(_tasks(100)) == 34
    assert planned_rounds(_tasks(60), 16) == 16
    assert planned_rounds(_tasks(100), 16) == 16
    # 档位上限之外不再有 200 轮之类的全局硬顶
    assert planned_rounds(_tasks(100), 300) == 34
    # 无电脑搜索待办 / 已完成 / 非法上限都不启动
    assert planned_rounds([], 16) == 0
    assert planned_rounds([dict(key="mobile_search", points_max=60,
                                points_earned=0, complete=False)], 16) == 0
    assert planned_rounds(_tasks(60, 60, complete=True), 16) == 0
    assert planned_rounds(_tasks(60), 0) == 0
    assert planned_rounds(_tasks(60), -5) == 0
    assert planned_rounds(_tasks(60), "bad") == 0


def test_repeat_cap_follows_intensity():
    assert [vm.repeat_cap(_prefs(c)) for c in ("低", "中", "高")] == [20, 16, 12]
    assert vm.repeat_cap({"ui_search_level": "不存在"}) == 16


def test_summary_first_row_states_cap_and_current_rounds():
    mid = dict(vm.default_settings_summary(_prefs("中"), "zh", current_rounds=16))
    assert mid["搜索轮数"] == "至多 16 轮，根据任务进度，目前搜索 16 轮"
    low = dict(vm.default_settings_summary(_prefs("低"), "zh", current_rounds=20))
    assert low["搜索轮数"] == "至多 20 轮，根据任务进度，目前搜索 20 轮"
    high = dict(vm.default_settings_summary(_prefs("高"), "zh", current_rounds=12))
    assert high["搜索轮数"] == "至多 12 轮，根据任务进度，目前搜索 12 轮"
    done = dict(vm.default_settings_summary(_prefs("中"), "zh", current_rounds=0))
    assert done["搜索轮数"] == "至多 16 轮，根据任务进度，目前搜索 0 轮"
    en = dict(vm.default_settings_summary(_prefs("中"), "en", current_rounds=7))
    assert en["Search rounds"] == (
        "Up to 16 rounds, 7 planned now based on task progress")
    # 其余 5 行仍是档位参数，不受首行影响
    assert high["单轮耗时"] == "24–40 秒"


# ---------------------------------------------------------------- 行为一致

def test_for_pending_uses_the_same_planned_rounds(tmp_path, monkeypatch):
    st, day = _store(tmp_path, monkeypatch, 60, 0)
    settings = {"repeat": 16, "level": 2}          # 中档，最多 16 次
    p = SearchRunner.for_pending(st, settings)
    assert p is not None and p["repeat"] == 16
    assert p["repeat"] == planned_rounds(list(st.tasks_of_day(day)),
                                         vm.repeat_cap(_prefs("中")))


def test_cap_is_the_only_limit_when_more_rounds_are_pending(tmp_path, monkeypatch):
    """100 分剩余需要 34 次，但强度档位的最多次数是唯一封顶。"""
    st, day = _store(tmp_path, monkeypatch, 100, 0)
    assert pending_desktop_rounds(list(st.tasks_of_day(day))) == 34
    assert SearchRunner.for_pending(st, {"repeat": 16, "level": 2})["repeat"] == 16
    assert SearchRunner.for_pending(st, {"repeat": 12, "level": 3})["repeat"] == 12
    assert SearchRunner.for_pending(st, {"repeat": 20, "level": 1})["repeat"] == 20
    # 调大档位上限后不再被任何全局硬顶截断
    assert SearchRunner.for_pending(st, {"repeat": 100, "level": 3})["repeat"] == 34


def test_rounds_follow_remaining_points_including_user_examples(tmp_path, monkeypatch):
    """同一强度下，次数只随剩余积分收敛：51 分→16，6 分→2。"""
    st, day = _store(tmp_path, monkeypatch, 60, 0)
    for earned, expected in ((0, 16), (9, 16), (30, 10), (51, 3), (54, 2)):
        st.set_task_complete(day, "pc_search", earned >= 60)
        st.upsert_task(day, "pc_search", "今日 Bing 搜索", "搜索",
                       60, earned, earned >= 60, False, "", "official", "", "")
        st.refresh_day(day)
        tasks = list(st.tasks_of_day(day))
        shown = dict(vm.default_settings_summary(
            _prefs("中"), "zh",
            current_rounds=planned_rounds(tasks, vm.repeat_cap(_prefs("中")))))
        p = SearchRunner.for_pending(st, {"repeat": 16, "level": 2})
        assert f"目前搜索 {expected} 轮" in shown["搜索轮数"], shown
        assert p is not None and p["repeat"] == expected, (earned, p)
    # 直接按「剩余积分」构造用户给的两个例子
    assert planned_rounds(_tasks(51), 16) == 16
    assert planned_rounds(_tasks(6), 16) == 2


def test_user_examples_with_current_presets(tmp_path, monkeypatch):
    """强度=中（最多 16 次）：剩 51 分 → 16 次；剩 6 分 → 2 次。"""
    cap = vm.repeat_cap(_prefs("中"))
    assert cap == 16
    st, day = _store(tmp_path / "case51", monkeypatch, 51, 0)
    tasks = list(st.tasks_of_day(day))
    assert pending_desktop_rounds(tasks) == 17        # ceil(51 / 3)
    assert planned_rounds(tasks, cap) == 16           # min(17, 16)
    assert SearchRunner.for_pending(st, {"repeat": cap, "level": 2})["repeat"] == 16
    st2, day2 = _store(tmp_path / "case6", monkeypatch, 6, 0)
    tasks2 = list(st2.tasks_of_day(day2))
    assert pending_desktop_rounds(tasks2) == 2
    assert planned_rounds(tasks2, cap) == 2           # min(2, 16)
    assert SearchRunner.for_pending(st2, {"repeat": cap, "level": 2})["repeat"] == 2


def test_all_done_shows_zero_and_does_not_start(tmp_path, monkeypatch):
    st, day = _store(tmp_path, monkeypatch, 60, 60)
    st.set_task_complete(day, "pc_search", True)
    st.refresh_day(day)
    tasks = list(st.tasks_of_day(day))
    assert planned_rounds(tasks, 16) == 0
    shown = dict(vm.default_settings_summary(
        _prefs("中"), "zh", current_rounds=planned_rounds(tasks, 16)))
    assert shown["搜索轮数"] == "至多 16 轮，根据任务进度，目前搜索 0 轮"
    assert SearchRunner.for_pending(st, {"repeat": 16, "level": 2}) is None


def test_settings_patch_writes_cap_that_execution_reads(tmp_path, monkeypatch):
    """切强度 → 写 search_panel.repeat → 「至多」与执行上限同步变化。"""
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    s = Settings()
    for choice, cap in (("低", 20), ("中", 16), ("高", 12)):
        s.update(vm.pref_to_settings(vm.settings_values(s), "ui_search_level", choice))
        v = vm.settings_values(s)
        assert vm.repeat_cap(v) == cap
        assert s.get("search_panel")["repeat"] == cap


# ---------------------------------------------------------------- 接线契约

def test_for_pending_calls_shared_planned_rounds():
    assert "planned_rounds(tasks, p.get(\"repeat\", 16))" in LEGACY_SRC
    assert "from ..pending import planned_rounds" in LEGACY_SRC


def test_no_other_round_limits_remain():
    """次数公式之外不再叠加任何夹逼 / 硬顶 / 保底。"""
    params = inspect.signature(planned_rounds).parameters
    assert list(params) == ["tasks", "cap"]
    assert "hard_limit" not in PENDING_SRC
    assert "min(200" not in LEGACY_SRC and "min(200" not in PENDING_SRC
    assert "min(200" not in VIEWMODEL_SRC
    assert "setRange(0, 1000000)" in LEGACY_SRC
    # 写入设置时不夹逼：超大值与 0 都原样落到 search_panel.repeat
    assert vm.pref_to_settings(vm.UI_DEFAULTS, "ui_repeat", 300)[
        "search_panel"]["repeat"] == 300
    assert vm.pref_to_settings(vm.UI_DEFAULTS, "ui_repeat", 0)[
        "search_panel"]["repeat"] == 0
    # 执行链路只把公式结果写回 repeat，不在别处二次夹逼
    assert 'p["repeat"] = repeat' in LEGACY_SRC
    api_src = (ROOT / "rewards_assistant" / "api.py").read_text("utf-8")
    assert "min(repeat" not in api_src and "max(repeat" not in api_src
    assert "repeat //" not in api_src and "repeat %" not in api_src


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


def test_architecture_documents_cap_not_fixed_rounds():
    """期望值从档位真源生成，避免改预设时漏改测试字面量。

    CI 读的是仓库里的架构文档；本地文档已同步但漏推时，这里会明确列出
    缺哪个「至多 N 轮」以及残留的旧预设数字，而不是给一个裸 AssertionError。
    """
    import re

    caps = [int(vm.INTENSITY_PROFILES[c]["repeat"]) for c in ("低", "中", "高")]
    expected = [f"至多 {n} 轮" for n in caps]
    missing = [s for s in expected if s not in ARCH]
    written = sorted({int(n) for n in re.findall(r"至多 (\d+) 轮", ARCH)})
    stale = sorted(set(written) - set(caps))
    assert not missing, (
        f"ARCHITECTURE.md 未与当前档位预设 {caps} 同步：缺少 {missing}；"
        f"文中出现过 {written}，其中非当前预设的旧值为 {stale}。"
        "请确认 docs/ARCHITECTURE.md 已随代码一起提交并推送。"
    )
    assert " / ".join(str(n) for n in caps) in ARCH
    assert "根据任务进度" in ARCH
    # 上限不能写成固定次数
    for n in caps:
        assert f"搜索轮数 | {n} 轮" not in ARCH, n
    assert "剩余积分 ÷ 3" in ARCH
    mid = caps[1]
    assert f"min(17, {mid}) = {min(17, mid)}" in ARCH
    assert f"min(2, {mid}) = 2" in ARCH


def test_hidden_classic_panel_label_says_cap():
    assert 'QLabel("搜索轮数上限")' in LEGACY_SRC

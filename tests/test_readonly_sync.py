# -*- coding: utf-8 -*-
"""只读登录 / 同步链路的离线单元测试：构造 HTML，不访问网络、不启动浏览器。

运行：python -m pytest -q tests/test_readonly_sync.py
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from rewards_assistant import sync as sync_mod
from rewards_assistant.readonly_report import (
    build_report, cards_from_rewards_html, decode_payload, is_sign_in_page,
    json_array_objects, merge_trio, num_from, parse_points_trio,
    parse_search_counter, structured_items_from, to_legacy_dashboard,
)
from rewards_assistant.rewards_client import parse_dashboard
from rewards_assistant.session import (
    DEGRADED, SIGNED_IN, SIGNED_OUT, TRANSIENT_ERROR, PageResult,
    ReadOnlyFetcher, assert_readonly, probe, redact,
)
from rewards_assistant.storage import Store

DAY = "2026-11-13"

# ---------------------------------------------------------------- fixtures

ABOUT_ZH = """
<title>关于 – Microsoft Rewards</title>
<h1>了解微软积分商城的优势</h1>
<a href="#">登录</a><a href="#">创建帐户</a>
<p>礼品卡为你添彩</p><p>立即开始赚取积分</p>
<p>什么是 Microsoft Rewards? 加入 Microsoft Rewards 需要付费吗?</p>
"""

RSC_ESC = '\\"'


def rsc_card(label: str, value: str) -> str:
    return (f'{RSC_ESC}className{RSC_ESC}:{RSC_ESC}text-labelControl{RSC_ESC},'
            f'{RSC_ESC}children{RSC_ESC}:{RSC_ESC}{label}{RSC_ESC}'
            f'<span></span>'
            f'{RSC_ESC}className{RSC_ESC}:{RSC_ESC}text-pageHeader{RSC_ESC},'
            f'{RSC_ESC}children{RSC_ESC}:{RSC_ESC}{value}{RSC_ESC}')


def dom_card(title: str, desc: str = "说明", points: int = 10,
             done: bool = False, heading: str = "每日活动") -> str:
    badge = (f'<div class="bg-statusSuccessRewardsBg">'
             f'<p class="text-metadata text-fgCtrlOnImage">{points}</p></div>')
    tail = "<span>已完成</span>" if done else ""
    return (f'<h2>{heading}</h2><a href="#">{badge}'
            f'<p class="line-clamp-3 text-globalBody2Strong">{title}</p>'
            f'<p class="line-clamp-3 text-fgCtrlNeutralSecondaryRest">{desc}</p>'
            f'{tail}</a>')


POINTS_HTML = (rsc_card("可用积分", "1,234") + rsc_card("今日积分", "45")
               + rsc_card("可领取", "60"))
TASKS_HTML = (
    dom_card("必应首页测验", done=True)
    + dom_card("今日投票")
    + dom_card("连续打卡奖励", points=50, heading="升级活动")
    + '"dailySetItems":['
      '{"title":"必应首页测验","points":10,"description":"说明",'
      '"isCompleted":true,"destination":"https://rewards.bing.com/quiz"},'
      '{"title":"今日看点","points":10,"description":"说明","isCompleted":false}]'
)
COUNTER_HTML = '"pointsCounters":{"pc":{"max":60,"progress":30}}'


def page(html: str, path: str = "/dashboard", status: int = 200,
         signin: bool = False, kind: str = "ok", ok: bool = True) -> PageResult:
    return PageResult(path=path, ok=ok, status=status, html=html,
                      kind=kind, signin_page=signin)


class FakeFetcher(ReadOnlyFetcher):
    def __init__(self, dash: PageResult, earn: PageResult) -> None:
        self.results = {"/dashboard": dash, "/earn": earn}

    def fetch_many(self, paths):
        return {p: self.results[p] for p in paths}

    def fetch(self, path):
        return self.results[path]


def make_store(path: Path) -> Store:
    return Store(str(path))


def run_sync(store, dash_html, earn_html, **kw):
    fetcher = FakeFetcher(page(dash_html, "/dashboard"), page(earn_html, "/earn"))
    return sync_mod.sync_once(store, fetcher, day=DAY, force=True, **kw)


# ---------------------------------------------------------------- 解析层

def test_decode_payload_and_num_from():
    assert "可用积分" in decode_payload(rsc_card("可用积分", "1,234"))
    assert num_from("1,234 分") == 1234
    assert num_from("暂无") is None


def test_is_sign_in_page_catches_chinese_about_page():
    # 原 JS 只认 /About – Microsoft Rewards/，中文「关于」页会漏判
    assert is_sign_in_page(ABOUT_ZH) is True
    assert is_sign_in_page("<html><h2>每日活动</h2>登录后的页面</html>") is False


def test_signed_out_report_never_writes_zero():
    rep = build_report(ABOUT_ZH, ABOUT_ZH)
    assert rep.signed_out is True
    assert rep.points == {"available": None, "today": None, "claimable": None}
    assert rep.groups == [] and rep.total == 0


def test_rsc_cards_and_field_fallback():
    trio = parse_points_trio(POINTS_HTML)
    assert trio == {"available": 1234, "today": 45, "claimable": 60}
    fallback = parse_points_trio('<script>{"availablePoints":"888"}</script>')
    assert fallback["available"] == 888
    assert fallback["today"] is None and fallback["claimable"] is None
    assert merge_trio({"available": None, "today": 12},
                      {"available": 999, "today": None}) == {
        "available": 999, "today": 12, "claimable": None}


def test_dom_badge_grouping_and_excluded_sections():
    items, stats = cards_from_rewards_html(TASKS_HTML)
    assert stats["domCards"] == 2          # 升级活动区那张被排除
    assert stats["otherSeen"] == 1
    assert all(i.section == "daily" for i in items)
    assert {i.name for i in items} >= {"必应首页测验", "今日投票", "今日看点"}
    quiz = next(i for i in items if i.name == "必应首页测验")
    assert quiz.completed is True and quiz.earned == 10


def test_rsc_dailyset_objects_and_bracket_in_title():
    src = ('"dailySetItems":[{"title":"活动]含右括号","points":10,'
           '"description":"说明","isCompleted":false},'
           '{"title":"第二个","points":5}]')
    objs = json_array_objects(decode_payload(src), "dailySetItems")
    assert len(objs) == 2
    items = structured_items_from(src)
    assert [i.name for i in items] == ["活动]含右括号", "第二个"]


def test_search_counter_missing_is_none_not_fake_60_of_60():
    # 原 JS 在 pointsCounters 读不到时伪造 {max:60, progress:60}（已满）
    assert parse_search_counter("<div>没有计数器</div>") is None
    rep = build_report(TASKS_HTML, TASKS_HTML)
    assert rep.counter is None
    assert not any(g.key == "search" for g in rep.groups)
    assert not any("已满" in line for line in rep.logs)
    c = parse_search_counter(COUNTER_HTML)
    assert c is not None and (c.max, c.progress) == (60, 30)
    rep2 = build_report(POINTS_HTML, TASKS_HTML + COUNTER_HTML)
    assert any(g.key == "search" for g in rep2.groups)
    assert any("30/60" in line for line in rep2.logs)
    assert not any("未读到" in line for line in rep2.logs)


def test_two_page_dedup_and_logs():
    rep = build_report(POINTS_HTML + TASKS_HTML,
                       POINTS_HTML + TASKS_HTML + COUNTER_HTML)
    names = [i.name for i in rep.cards]
    assert len(names) == len(set(names))
    assert rep.points["available"] == 1234
    assert any("两页去重后" in line for line in rep.logs)
    assert any("不必相等" in line for line in rep.logs)


def test_legacy_dashboard_bridge_feeds_old_parser():
    """兼容桥接：旧的 parse_dashboard / app.py 能吃到页面真值。"""
    rep = build_report(POINTS_HTML + TASKS_HTML,
                       POINTS_HTML + TASKS_HTML + COUNTER_HTML)
    snap = parse_dashboard(to_legacy_dashboard(rep, DAY, level=2), DAY)
    assert snap.balance_valid is True and snap.available_points == 1234
    assert snap.level == 2
    assert any(t.name == "必应首页测验" for t in snap.tasks)
    assert any(t.key == "pc_search" and t.points_earned == 30 for t in snap.tasks)


# ---------------------------------------------------------------- 会话层

def test_readonly_whitelist_and_redaction():
    assert assert_readonly("/dashboard").endswith("/dashboard")
    with pytest.raises(ValueError):
        assert_readonly("/api/getuserinfo")
    masked = redact("Cookie: a=1; b=2; Authorization: Bearer abcdef")
    assert "[已脱敏]" in masked and "a=1" not in masked and "abcdef" not in masked


def test_probe_states():
    ok_dash = page(POINTS_HTML + TASKS_HTML)
    ok_earn = page(POINTS_HTML + COUNTER_HTML, "/earn")
    assert probe(FakeFetcher(ok_dash, ok_earn)).state == SIGNED_IN
    assert probe(FakeFetcher(page(ABOUT_ZH, signin=True),
                             page(ABOUT_ZH, "/earn", signin=True))).state == SIGNED_OUT
    assert probe(FakeFetcher(ok_dash, page(ABOUT_ZH, "/earn", signin=True))).state == DEGRADED
    st = probe(FakeFetcher(
        page("", kind="transient", ok=False, status=503),
        page("", "/earn", kind="transient", ok=False, status=429)))
    assert st.state == TRANSIENT_ERROR
    assert "无需重新登录" in st.reason and not st.can_sync


# ---------------------------------------------------------------- 同步层

def test_sync_once_writes_three_points_and_page_today(tmp_path: Path):
    store = make_store(tmp_path / "rewards.db")
    out = run_sync(store, POINTS_HTML + TASKS_HTML,
                   POINTS_HTML + TASKS_HTML + COUNTER_HTML)
    assert out["ok"] is True and out["stale"] is False
    assert out["points"] == {"available": 1234, "today": 45, "claimable": 60}
    assert "手动领取" in out["message"]
    assert out["snapshot"].balance_valid is True       # 旧 UI 契约保持
    row = store.daily(DAY)
    assert row["today_points"] == 45 and row["claimable_points"] == 60
    assert row["points_earned"] == 45                  # 页面真值，非余额日差
    assert row["points_source"] == "page_today"
    assert len(store.tasks_of_day(DAY)) >= 3


def test_sync_once_signed_out_writes_nothing(tmp_path: Path):
    store = make_store(tmp_path / "rewards.db")
    fetcher = FakeFetcher(page(ABOUT_ZH, signin=True),
                          page(ABOUT_ZH, "/earn", signin=True))
    out = sync_mod.sync_once(store, fetcher, day=DAY, force=True)
    assert out["ok"] is False and out["state"] == SIGNED_OUT and out["stale"] is True
    assert "未登录或会话不可用" in out["message"]
    with sqlite3.connect(store.path) as c:
        assert c.execute("SELECT COUNT(*) FROM daily").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_sync_once_transient_keeps_previous_snapshot(tmp_path: Path):
    store = make_store(tmp_path / "rewards.db")
    store.upsert_available(DAY, 999, 2)
    fetcher = FakeFetcher(
        page("", kind="transient", ok=False, status=503),
        page("", "/earn", kind="transient", ok=False, status=429))
    out = sync_mod.sync_once(store, fetcher, day=DAY, force=True)
    assert out["ok"] is False and out["state"] == TRANSIENT_ERROR
    assert out["stale"] is True and out["summary"]["available_points"] == 999
    assert store.daily(DAY)["available_points"] == 999


def test_missing_points_never_written_as_zero(tmp_path: Path):
    store = make_store(tmp_path / "rewards.db")
    html = '<script>{"availablePoints":"777"}</script>' + dom_card("一个小测验")
    out = run_sync(store, html, html)
    assert out["points"]["today"] is None and out["points"]["claimable"] is None
    row = store.daily(DAY)
    assert row["today_points"] is None and row["claimable_points"] is None
    assert row["available_points"] == 777


def test_degraded_sync_keeps_existing_tasks(tmp_path: Path):
    """单页可用时采用能读到的字段，但不贸然替换整日任务集合。"""
    store = make_store(tmp_path / "rewards.db")
    html = POINTS_HTML + TASKS_HTML
    run_sync(store, html, html)                       # 先建立完整记录
    before = {r["key"] for r in store.tasks_of_day(DAY)}
    fetcher = FakeFetcher(page(html, "/dashboard"),
                          page("", "/earn", kind="transient", ok=False, status=503))
    out = sync_mod.sync_once(store, fetcher, day=DAY, force=True)
    assert out["ok"] is True and out["state"] == DEGRADED
    assert {r["key"] for r in store.tasks_of_day(DAY)} == before

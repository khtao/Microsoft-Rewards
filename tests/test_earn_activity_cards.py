# -*- coding: utf-8 -*-
"""真实 /earn 页的两处回归：

1. 「日常任务 / MoreActivities」的有积分卡片放在 RSC ``activityCards`` 里，
   默认折叠（"显示更多"）的那张**没有渲染进 DOM**。旧解析器只认
   ``dailySetItems`` 和可见 DOM 徽章卡，于是今天未完成的
   「你是否知道答案？ +5」完全不入库，主界面误报「今日全部任务已完成」。
   同名但完成态不同的两张卡还会因去重/落库撞 key 互相覆盖。

2. 抓取 zip 的 README 写死「抓取 /dashboard 与 /earn 两页」，但导出时只写
   ``usable`` 的页：/dashboard 一旦被误判成登录页（新版页脚仍带
   「登录 / 创建帐户」推广链接）就静默丢失，zip 里只剩一页。

本测试用脱敏的构造 HTML，不访问网络、不打包用户真实页面。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from rewards_assistant.readonly_report import (
    build_report, cards_from_rewards_html, is_sign_in_page, structured_items_from,
    to_legacy_dashboard,
)
from rewards_assistant.rewards_client import parse_dashboard
from rewards_assistant.session import (
    PageResult, SIGNED_IN, is_sign_in_page as session_is_sign_in_page, probe,
)
from rewards_assistant.sync import rows_from_report

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- fixtures

def _rsc_array(key: str, cards: list) -> str:
    """模拟 Next.js ``self.__next_f.push`` 里的转义 RSC payload。"""
    payload = f'"{key}":[' + ",".join(
        json.dumps(c, ensure_ascii=False) for c in cards) + "]"
    return 'self.__next_f.push([1,"54:' + payload.replace('"', '\\"') + '"]);'


QUIZ_SAT = ("ZHstar_Rewards_DailyGlobalOffer_Evergreen_Saturday",
            "你是否知道答案？", "通过这些小问题挑战自己", False)
QUIZ_TUE = ("ZHstar_Rewards_DailyGlobalOffer_Evergreen_Tuesday",
            "你是否知道答案？", "测试你对这些主题的知识", True)
PUZZLE = ("ZHstar_Rewards_DailyGlobalOffer_Evergreen_Monday",
          "完成此拼图", "排列图块以显示图像", True)
PUZZLE_THU = ("ZHstar_Rewards_DailyGlobalOffer_Evergreen_Thursday",
              "周中拼图", "你是否能够完成这个拼图？", True)


def _activity(offer, title, desc, done, points=5) -> dict:
    return {"destination": "https://www.bing.com/search?q=quiz",
            "isCompleted": done, "points": points, "title": title,
            "description": desc, "offerId": offer,
            "name": offer, "hash": "deadbeef"}


ACTIVITY_CARDS = [
    _activity(*QUIZ_SAT),                       # 今天未完成，DOM 里看不到
    _activity(*PUZZLE),                        # 已完成
    _activity(*PUZZLE_THU),                    # 已完成
    _activity(*QUIZ_TUE),                      # 同名同分值、已完成
    _activity("WW_banner_free_wallpaper", "每日新壁纸", "无积分推广横幅",
              False, points=0),
]  # 4 张有积分：对应截图左侧「日常任务 20/20」

DAILY_ITEMS = [
    {"title": "开普敦春夏", "description": "野花与海滩", "points": 10,
     "isCompleted": True, "offerId": "Gamification_DailySet_Child1",
     "destination": "https://www.bing.com/search?q=spring"},
    {"title": "贝多芬的才华", "description": "探索贝多芬作品", "points": 10,
     "isCompleted": True, "offerId": "Gamification_DailySet_Child2",
     "destination": "https://www.bing.com/search?q=beethoven"},
    {"title": "盆景养护建议", "description": "照料您的小树", "points": 10,
     "isCompleted": True, "offerId": "Gamification_DailySet_Child3",
     "destination": "https://www.bing.com/search?q=bonsai"},
]

def _dom_card(title: str, desc: str, points: int, done: bool) -> str:
    badge = ('<div class="bg-statusSuccessRewardsBg">'
             f'<p class="text-metadata text-fgCtrlOnImage">{points}</p></div>')
    tail = "<div>已完成</div>" if done else ""
    return ('<a href="#">' + badge
            + f'<p class="line-clamp-3 text-globalBody2Strong">{title}</p>'
            + f'<p class="line-clamp-3 text-fgCtrlNeutralSecondaryRest">{desc}</p>'
            + tail + '</a>')


# DOM 只渲染「显示更多」展开前的卡，其中「你是否知道答案？」是已完成的
# Tuesday 那张；未完成的 Saturday 只存在于 RSC。
DOM_CARDS = (
    '<h2>日常任务</h2>'
    + _dom_card("完成此拼图", "排列图块以显示图像", 5, done=True)
    + _dom_card("周中拼图", "你是否能够完成这个拼图？", 5, done=True)
    + _dom_card("你是否知道答案？", "测试你对这些主题的知识", 5, done=True)
)


EARN_HTML = (
    _rsc_array("activityCards", ACTIVITY_CARDS)
    + _rsc_array("dailySetItems", DAILY_ITEMS)
    + '"pointsCounters":{"pc":{"max":30,"progress":30}}'
    + DOM_CARDS
)

# 已登录的 /dashboard：页脚带「登录 / 创建帐户」推广链接，但 payload 里有
# pointsCounters / dailySetItems。旧版只看文案组合会误判成登录页。
DASH_LIKE = (
    '<a href="#">登录</a><a href="#">创建帐户</a>'
    '<p>了解微软积分商城的优势</p>'
    '"pointsCounters":{"pc":{"max":30,"progress":30}}'
    + _rsc_array("dailySetItems", DAILY_ITEMS)
)

ABOUT = ('<title>关于 – Microsoft Rewards</title>'
         '<h1>了解微软积分商城的优势</h1>'
         '<a href="#">登录</a><a href="#">创建帐户</a>'
         '<p>礼品卡为你添彩</p>')


# ---------------------------------------------------------------- 解析

def test_activity_cards_include_folded_unfinished_quiz():
    items, _stats = cards_from_rewards_html(EARN_HTML)
    quiz = [i for i in items if i.name == "你是否知道答案？"]
    # 两张同名卡（一天未完成、另一天已完成）都保留，靠 offerId / 描述区分
    assert len(quiz) == 2, [(i.name, i.completed) for i in quiz]
    assert sorted(i.completed for i in quiz) == [False, True]
    unfinished = next(i for i in quiz if not i.completed)
    assert unfinished.section == "promo" and unfinished.points == 5
    assert unfinished.earned == 0 and unfinished.source == "rsc"
    assert unfinished.offer_id == QUIZ_SAT[0]


def test_only_cards_with_points_are_kept():
    items, _stats = cards_from_rewards_html(EARN_HTML)
    assert all(i.points > 0 for i in items)
    assert "每日新壁纸" not in {i.name for i in items}


def test_rsc_wins_over_dom_for_same_card():
    """DOM 里已完成的同名卡不能把 RSC 未完成卡挤掉，也不能重复显示。"""
    items, _stats = cards_from_rewards_html(EARN_HTML)
    promo = [i for i in items if i.section == "promo"]
    assert len(promo) == 4, [(i.name, i.source) for i in promo]
    assert not any(i.source == "dom" for i in promo), \
        "RSC 已覆盖这些卡，DOM 副本应跳过"


def test_structured_items_defaults_include_activity_cards():
    items = structured_items_from(EARN_HTML)
    assert {i.section for i in items} == {"daily", "promo"}
    assert sum(1 for i in items if i.section == "promo") == 4


def test_report_is_no_longer_all_done():
    rep = build_report(None, EARN_HTML)
    assert not rep.signed_out
    assert rep.percent < 100
    assert rep.earned < rep.total
    names = [(i.name, i.completed) for i in rep.cards if i.name == "你是否知道答案？"]
    assert (("你是否知道答案？", False) in names)
    assert any("activityCards" in line for line in rep.logs)


def test_rows_have_unique_keys_and_keep_unfinished_task():
    rep = build_report(None, EARN_HTML)
    rows = rows_from_report(rep)
    keys = [r["key"] for r in rows]
    assert len(keys) == len(set(keys)), keys
    unfinished = [r for r in rows if r["name"] == "你是否知道答案？"
                  and not r["complete"]]
    assert len(unfinished) == 1 and unfinished[0]["points_max"] == 5
    assert unfinished[0]["category"] == "更多活动"
    assert unfinished[0]["offer_id"] == QUIZ_SAT[0]


def test_legacy_bridge_exposes_unfinished_quiz_to_classic_parser():
    rep = build_report(None, EARN_HTML)
    snap = parse_dashboard(to_legacy_dashboard(rep, "2026-11-21"), "2026-11-21")
    left = [t for t in snap.tasks if not t.complete and t.points_max > 0]
    assert any(t.name == "你是否知道答案？" and t.points_max == 5 for t in left)


def test_same_title_same_desc_conflict_gets_offer_suffix(tmp_path, monkeypatch):
    """同名同描述同分但 offerId 不同的两张卡不能撞 row key。"""
    cards = [
        _activity("offer-A", "同名活动", "同一句说明", False),
        _activity("offer-B", "同名活动", "同一句说明", True),
    ]
    rep = build_report(None, _rsc_array("activityCards", cards))
    rows = rows_from_report(rep)
    keys = [r["key"] for r in rows]
    assert len(keys) == len(set(keys)) == 2, keys


# ---------------------------------------------------------------- 登录页误判

def test_signed_in_page_with_footer_login_links_is_not_signin_page():
    assert is_sign_in_page(DASH_LIKE) is False
    assert session_is_sign_in_page(DASH_LIKE) is False
    assert is_sign_in_page(ABOUT) is True     # 公开 About 页仍正确识别
    assert is_sign_in_page(EARN_HTML) is False


def test_probe_is_signed_in_when_both_pages_have_rsc_payload():
    class Fetcher:
        def fetch_many(self, paths):
            out = {}
            for p in paths:
                html = DASH_LIKE if p == "/dashboard" else EARN_HTML
                out[p] = PageResult(path=p, ok=True, status=200, html=html,
                                    kind="ok", signin_page=is_sign_in_page(html))
            return out

    st = probe(Fetcher())
    assert st.state == SIGNED_IN, (st.state, st.reason)
    assert all(r.usable for r in st.pages.values())


# ---------------------------------------------------------------- 抓取 zip

class _FakeFetcher:
    def __init__(self, dash: PageResult, earn: PageResult) -> None:
        self.results = {"/dashboard": dash, "/earn": earn}

    def fetch_many(self, paths):
        return {p: self.results[p] for p in paths}


def _run_capture(monkeypatch, tmp_path, dash: PageResult, earn: PageResult):
    from rewards_assistant.ui.modern import main_window as mw

    monkeypatch.setattr(mw, "default_fetcher",
                        lambda: _FakeFetcher(dash, earn))
    target = tmp_path / "rewards-pages.zip"
    runner = mw.CaptureRunner(target)
    done: list = []
    runner.done.connect(done.append)
    failed: list = []
    runner.fail.connect(failed.append)
    runner.run()                     # 直接调用，不牵涉线程调度
    assert not failed, failed
    assert done == [str(target)]
    return target


def test_capture_zip_contains_both_pages_and_truthful_readme(tmp_path, monkeypatch):
    ok_dash = PageResult(path="/dashboard", ok=True, status=200,
                         html=DASH_LIKE, kind="ok",
                         signin_page=is_sign_in_page(DASH_LIKE))
    ok_earn = PageResult(path="/earn", ok=True, status=200,
                         html=EARN_HTML, kind="ok",
                         signin_page=is_sign_in_page(EARN_HTML))
    target = _run_capture(monkeypatch, tmp_path, ok_dash, ok_earn)
    with zipfile.ZipFile(target) as z:
        names = set(z.namelist())
        assert names == {"dashboard.html", "earn.html", "README.txt"}, names
        assert z.read("earn.html").decode("utf-8") == EARN_HTML
        readme = z.read("README.txt").decode("utf-8")
        assert "2 个页面条目" in readme and "2 个是已登录可用页面" in readme
        assert "/dashboard" in readme and "/earn" in readme
        assert "activityCards" not in readme        # 不泄漏内部字段名


def test_capture_zip_keeps_unusable_dashboard_with_reason(tmp_path, monkeypatch):
    """某页真不可用时也写条目，README 如实说明，不再谎称抓了两页可用源码。"""
    bad_dash = PageResult(path="/dashboard", ok=True, status=200,
                          html=ABOUT, kind="ok", signin_page=True)
    ok_earn = PageResult(path="/earn", ok=True, status=200,
                         html=EARN_HTML, kind="ok", signin_page=False)
    target = _run_capture(monkeypatch, tmp_path, bad_dash, ok_earn)
    with zipfile.ZipFile(target) as z:
        assert set(z.namelist()) == {"dashboard.html", "earn.html", "README.txt"}
        assert z.read("dashboard.html").decode("utf-8") == ABOUT
        readme = z.read("README.txt").decode("utf-8")
        assert "1 个是已登录可用页面" in readme
        assert "登录页 / 公开页" in readme
        assert "少于 2" in readme


def test_capture_zip_writes_placeholder_when_page_has_no_html(tmp_path, monkeypatch):
    empty = PageResult(path="/dashboard", ok=False, status=503, html="",
                       kind="transient", error="网络暂时不可用")
    ok_earn = PageResult(path="/earn", ok=True, status=200,
                         html=EARN_HTML, kind="ok", signin_page=False)
    target = _run_capture(monkeypatch, tmp_path, empty, ok_earn)
    with zipfile.ZipFile(target) as z:
        assert set(z.namelist()) == {"dashboard.html", "earn.html", "README.txt"}
        assert "未读到页面源码" in z.read("dashboard.html").decode("utf-8")
        assert "空内容" in z.read("README.txt").decode("utf-8")


def test_capture_runner_no_longer_filters_by_usable():
    src = (ROOT / "rewards_assistant" / "ui" / "modern" /
           "main_window.py").read_text("utf-8")
    block = src.split("class CaptureRunner")[1].split("class HomePage")[0]
    assert "if r.usable and r.html" not in block
    assert "for path in ALLOWED_PATHS:" in block


# ---------------------------------------------------------------- 间歇性 / 双账号

def _earn_page(cards, *, counter_max=30, counter_progress=30) -> str:
    return (_rsc_array("activityCards", cards)
            + _rsc_array("dailySetItems", DAILY_ITEMS)
            + f'"pointsCounters":{{"pc":{{"max":{counter_max},'
              f'"progress":{counter_progress}}}}}'
            + DOM_CARDS)


def _two_page_fetcher(dash_html: str, earn_html: str):
    class F:
        def fetch_many(self, paths):
            out = {}
            for p in paths:
                h = dash_html if p == "/dashboard" else earn_html
                out[p] = PageResult(path=p, ok=True, status=200, html=h,
                                    kind="ok", signin_page=is_sign_in_page(h))
            return out

    return F()


def test_two_accounts_keep_their_own_activity_card_counts():
    """截图左右两个账号：60 分搜索 / 4 张日常任务 = 110，30 分 / 3 张 = 75。

    活动卡数量本来就会随账号 / 等级 / 地区 / A-B 下发而不同，这不是 bug；
    关键是每个账号都按自己那份 ``activityCards`` 完整显示，且两次解析互不串味。
    """
    left = build_report(DASH_LIKE,
                        _earn_page(ACTIVITY_CARDS,
                                   counter_max=60, counter_progress=60))
    right_cards = [c for c in ACTIVITY_CARDS
                   if c["offerId"] != QUIZ_TUE[0]]      # 右侧账号少下发一张
    right = build_report(DASH_LIKE,
                         _earn_page(right_cards,
                                    counter_max=30, counter_progress=30))
    lp = [i for i in left.cards if i.section == "promo"]
    rp = [i for i in right.cards if i.section == "promo"]
    assert len(lp) == 4 and sum(i.points for i in lp) == 20
    assert len(rp) == 3 and sum(i.points for i in rp) == 15
    assert left.total == 110 and right.total == 75, (left.total, right.total)
    # 两个账号都各有一张未完成的「你是否知道答案？ +5」→ 不该假报 100%
    for rep in (left, right):
        assert rep.percent < 100 and rep.earned < rep.total
        assert any(i.name == "你是否知道答案？" and not i.completed
                   for i in rep.cards)
    # 再解析一次左侧，确认没有跨调用的共享状态把卡数改掉
    again = build_report(DASH_LIKE,
                         _earn_page(ACTIVITY_CARDS,
                                    counter_max=60, counter_progress=60))
    assert len([i for i in again.cards if i.section == "promo"]) == 4


def test_dashboard_promo_footer_does_not_cause_intermittent_task_loss(tmp_path,
                                                                      monkeypatch):
    """「有时候显示不全」的根因：已登录 /dashboard 页脚偶尔带推广文案。

    旧版会把它判成登录页 → 两页只采用一页 → DEGRADED → 本次不替换当日任务
    集合，只沿用上次快照，于是任务时多时少。现在带不带文案都必须 SIGNED_IN，
    两次同步落库结果完全一致。
    """
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    from rewards_assistant import sync as sync_mod
    from rewards_assistant.storage import Store

    day = "2026-11-21"
    earn = _earn_page(ACTIVITY_CARDS, counter_max=60, counter_progress=60)
    plain_dash = (_rsc_array("dailySetItems", DAILY_ITEMS)
                  + '"pointsCounters":{"pc":{"max":60,"progress":60}}')
    promo_dash = DASH_LIKE        # 带「登录 / 创建帐户 / 了解微软积分商城的优势」

    st = Store()
    out1 = sync_mod.sync_once(st, _two_page_fetcher(promo_dash, earn),
                              day=day, force=True)
    rows1 = list(st.tasks_of_day(day))
    keys1 = {r["key"] for r in rows1}

    out2 = sync_mod.sync_once(st, _two_page_fetcher(plain_dash, earn),
                              day=day, force=True)
    rows2 = list(st.tasks_of_day(day))

    assert out1["state"] == out2["state"] == SIGNED_IN
    assert {r["key"] for r in rows2} == keys1
    assert len(rows1) == len(rows2) == 8      # 搜索1 + 每日3 + 日常4
    assert sum(1 for r in rows2 if r["name"] == "你是否知道答案？") == 2
    assert any(r["name"] == "你是否知道答案？" and not r["complete"]
               and r["points_max"] == 5 for r in rows2)
    assert st.daily(day)["all_done"] == 0


def test_cross_page_same_offerid_card_is_shown_once():
    """/dashboard 与 /earn 都下发同一张每日活动卡时，只显示一次。"""
    dash = _rsc_array("dailySetItems", DAILY_ITEMS)
    rep = build_report(dash, _earn_page(ACTIVITY_CARDS))
    names = [i.name for i in rep.cards]
    for title in ("开普敦春夏", "贝多芬的才华", "盆景养护建议"):
        assert names.count(title) == 1, (title, names.count(title))


def test_cross_page_card_without_offerid_is_a_mirror():
    """一侧 payload 不带 offerId 时，它是带 offerId 真卡的镜像，不重复显示。"""
    plain = ('"dailySetItems":[' + ",".join(
        json.dumps({"title": d["title"], "description": d["description"],
                    "points": d["points"], "isCompleted": d["isCompleted"]},
                   ensure_ascii=False) for d in DAILY_ITEMS) + "]")
    rep = build_report(plain, _earn_page(ACTIVITY_CARDS))
    names = [i.name for i in rep.cards]
    assert names.count("开普敦春夏") == 1
    assert names.count("贝多芬的才华") == 1
    assert names.count("盆景养护建议") == 1


def test_same_title_same_desc_different_offer_survive_build_report():
    """同名同说明同分值、只有 offerId 不同的两张卡必须都保留。"""
    cards = [_activity("offer-A", "同名活动", "同一句说明", False),
             _activity("offer-B", "同名活动", "同一句说明", True)]
    rep = build_report(None, _rsc_array("activityCards", cards))
    got = [i for i in rep.cards if i.name == "同名活动"]
    assert len(got) == 2
    assert sorted(i.completed for i in got) == [False, True]

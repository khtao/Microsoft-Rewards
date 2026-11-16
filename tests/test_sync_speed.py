# -*- coding: utf-8 -*-
"""同步速度回归：同步链路不启动浏览器、两页并发、解析器单次扫描、落库单次提交。

根本优化：凭证与浏览器解耦。``/dashboard`` 与 ``/earn`` 是服务端渲染的
HTML，需要的只是带 Cookie 的两个 GET；现在由 ``HttpFetcher`` 用
``requests.Session`` 并发完成，Chromium 冷启动（数秒）从同步路径彻底消失。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from rewards_assistant import session, sync as sync_mod
from rewards_assistant.credentials import CookieStore
from rewards_assistant.storage import Store

ROOT = Path(__file__).resolve().parents[1]


def _store(tmp_path, name="cred.bin"):
    store = CookieStore(tmp_path / name)
    store.save([{"name": "MSPAuth", "value": "v", "domain": ".bing.com",
                 "path": "/", "secure": True, "expires": -1}])
    return store


class _Resp:
    def __init__(self, status_code=200, text="<html><h2>每日活动</h2><p>ok</p></html>"):
        self.status_code = status_code
        self.text = text


class _Jar:
    def set(self, name, value, domain="", path="/", secure=False):
        pass


def _fake_requests(monkeypatch, state, *, status=200, text=None, delay=0.05):
    """替换 requests.Session，用「同时在途数」证明两页并发。"""
    import requests

    lock = state.setdefault("lock", threading.Lock())

    class FakeSession:
        def __init__(self):
            self.cookies = _Jar()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None, timeout=None, allow_redirects=True):
            with lock:
                state["urls"] = state.get("urls", []) + [url]
                state["active"] = state.get("active", 0) + 1
                state["max_active"] = max(state.get("max_active", 0), state["active"])
            time.sleep(delay)
            with lock:
                state["active"] -= 1
            return _Resp(status, text if text is not None
                         else "<html><h2>每日活动</h2><p>ok</p></html>")

    monkeypatch.setattr(requests, "Session", FakeSession)
    return state


def test_http_fetcher_downloads_two_pages_in_parallel(tmp_path, monkeypatch):
    state = _fake_requests(monkeypatch, {}, delay=0.12)
    fetcher = session.HttpFetcher(_store(tmp_path))
    out = fetcher.fetch_many(["/dashboard", "/earn"])

    assert set(out) == {"/dashboard", "/earn"}
    assert all(r.ok and r.html and r.kind == "ok" for r in out.values())
    assert state["max_active"] == 2, "两页必须并发，而不是一页等完再发下一页"
    assert state["active"] == 0
    assert sorted(state["urls"]) == [
        "https://rewards.bing.com/dashboard", "https://rewards.bing.com/earn"]


def test_no_credentials_returns_signed_out_without_network(tmp_path, monkeypatch):
    """没保存会话就直接判未登录，不发无意义的网络往返。"""
    import requests

    def forbidden(*_a, **_kw):
        raise AssertionError("无凭证时不应发出任何请求")

    monkeypatch.setattr(requests, "Session", forbidden)
    fetcher = session.HttpFetcher(CookieStore(tmp_path / "empty.bin"))
    out = fetcher.fetch_many(["/dashboard", "/earn"])
    assert all(r.kind == "signed_out" for r in out.values())
    st = session.probe(fetcher)
    assert st.state == session.SIGNED_OUT and not st.can_sync
    assert "尚未保存登录会话" in st.reason


def test_expired_cookie_is_treated_as_no_credentials(tmp_path, monkeypatch):
    import requests

    def forbidden(*_a, **_kw):
        raise AssertionError("凭证已过期时不应发请求")

    monkeypatch.setattr(requests, "Session", forbidden)
    store = CookieStore(tmp_path / "expired.bin")
    store.save([{"name": "A", "value": "1", "expires": 1}])
    assert store.load() == []
    out = session.HttpFetcher(store).fetch_many(["/dashboard"])
    assert out["/dashboard"].kind == "signed_out"


def test_http_statuses_keep_five_state_semantics(tmp_path, monkeypatch):
    for status, expected in ((401, "signed_out"), (403, "signed_out"),
                             (429, "transient"), (503, "transient")):
        s = CookieStore(tmp_path / f"s{status}.bin")
        s.save([{"name": "A", "value": "1", "expires": -1}])
        _fake_requests(monkeypatch, {}, status=status, delay=0)
        r = session.HttpFetcher(s).fetch_many(["/dashboard"])["/dashboard"]
        assert r.kind == expected, (status, r.kind)


def test_login_page_html_marks_signin_page(tmp_path, monkeypatch):
    s = CookieStore(tmp_path / "signin.bin")
    s.save([{"name": "A", "value": "1", "expires": -1}])
    _fake_requests(monkeypatch, {}, text=(
        "<html><h1>使用你的 Microsoft 帐户</h1><p>创建帐户</p></html>"), delay=0)
    r = session.HttpFetcher(s).fetch_many(["/dashboard"])["/dashboard"]
    assert r.signin_page and not r.usable


def test_default_fetcher_is_http_and_never_browser():
    fetcher = session.default_fetcher()
    assert isinstance(fetcher, session.HttpFetcher)
    src = (ROOT / "rewards_assistant" / "session.py").read_text(encoding="utf-8")
    assert "return HttpFetcher(store)" in src
    assert "BrowserSessionFetcher(headless=True)" not in src
    for dead in ("_SharedBrowserSession", "release_shared_session",
                 "LegacyCookieFetcher", "UnavailableFetcher",
                 "playwright_available"):
        assert dead not in src, dead


def test_http_sync_has_no_browser_cold_start(tmp_path, monkeypatch):
    """两轮同步：零次浏览器启动，耗时约等于两次网络往返。"""
    state = _fake_requests(monkeypatch, {}, delay=0.15)
    fetcher = session.HttpFetcher(_store(tmp_path))
    t0 = time.monotonic()
    for _ in range(2):
        out = fetcher.fetch_many(["/dashboard", "/earn"])
        assert all(r.ok for r in out.values())
    elapsed = time.monotonic() - t0
    assert elapsed < 0.8, f"两轮纯 HTTP 同步耗时 {elapsed:.2f}s，异常"
    assert state["max_active"] == 2


# ------------------------------------------------------------------ 解析器

class _CountingH2:
    def __init__(self, pattern):
        self._pattern = pattern
        self.calls = 0

    def finditer(self, text):
        self.calls += 1
        return self._pattern.finditer(text)


def test_heading_lookup_scans_html_only_once():
    """旧版每张卡都 finditer(html[:index])；现在整页只扫一次。"""
    from rewards_assistant.readonly_report import _H2_RE, visible_cards_from

    html = ("<h2>每日活动</h2>" +
            "".join('<a><p class="bg-statusSuccessRewardsBg"></p>'
                    '<p class="text-metadata text-fgCtrlOnImage">10</p>'
                    '<p class="line-clamp-3 text-globalBody2Strong">任务%d</p></a>' % i
                    for i in range(60)))
    counter = _CountingH2(_H2_RE)
    import rewards_assistant.readonly_report as mod
    old = mod._H2_RE
    mod._H2_RE = counter
    try:
        items, stats = visible_cards_from(html)
    finally:
        mod._H2_RE = old
    assert len(items) == 60 and stats["domCards"] == 60
    assert counter.calls == 1, f"h2 扫描次数 {counter.calls}，应为 1"


def test_build_report_decodes_each_page_only_once(monkeypatch):
    import rewards_assistant.readonly_report as mod

    calls = []
    orig = mod.decode_payload

    def counting(html):
        calls.append(html)
        return orig(html)

    monkeypatch.setattr(mod, "decode_payload", counting)
    dash = '<p class="text-labelControl">可用积分</p><p class="text-pageHeader">744</p>'
    earn = '<p class="text-labelControl">今日积分</p><p class="text-pageHeader">30</p>'
    rep = mod.build_report(dash, earn)
    assert rep.points["available"] == 744 and rep.points["today"] == 30
    assert len(calls) == 2, f"每页只应解码一次，实际 {len(calls)} 次"


# ------------------------------------------------------------------ 落库

def _rows(n: int = 12):
    return [{"key": f"daily:{i}", "name": f"任务{i}", "category": "每日任务",
             "points_max": 10, "points_earned": i % 10, "complete": i % 2 == 0,
             "manual": False, "link": "", "title_source": "guess",
             "offer_id": f"o{i}", "raw_name": f"raw{i}"} for i in range(n)]


def test_bulk_upsert_matches_repeated_single_upsert(tmp_path):
    a, b = Store(str(tmp_path / "a.db")), Store(str(tmp_path / "b.db"))
    day, rows = "2026-11-16", _rows()
    for r in rows:
        a.upsert_task(day, r["key"], r["name"], r["category"], r["points_max"],
                      r["points_earned"], r["complete"], manual=r["manual"],
                      link=r["link"], title_source=r["title_source"],
                      offer_id=r["offer_id"], raw_name=r["raw_name"])
    assert b.upsert_tasks(day, rows) == len(rows)
    norm = lambda rs: [dict(x) for x in rs]
    assert norm(a.tasks_of_day(day)) == norm(b.tasks_of_day(day))


class _ConnProxy:
    """只统计 commit 次数，其余原样转发给真实 sqlite3 连接。"""

    def __init__(self, conn):
        self._conn = conn
        self.commits = 0

    def commit(self):
        self.commits += 1
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def execute(self, *a, **kw):
        return self._conn.execute(*a, **kw)

    def executemany(self, *a, **kw):
        return self._conn.executemany(*a, **kw)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_tx_is_reentrant_and_sync_commits_once(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "speed.db"))
    proxy = _ConnProxy(store._connect())
    monkeypatch.setattr(store, "_connect", lambda: proxy)

    with store.tx():
        store.upsert_tasks("2026-11-16", _rows(3))
        store.upsert_available("2026-11-16", 744, 2)
    assert proxy.commits == 1

    proxy.commits = 0

    class Fetcher(session.ReadOnlyFetcher):
        def fetch_many(self, paths):
            html = ('<p class="text-labelControl">可用积分</p>'
                    '<p class="text-pageHeader">744</p>'
                    '<h2>每日活动</h2>'
                    '<a><p class="bg-statusSuccessRewardsBg"></p>'
                    '<p class="text-metadata text-fgCtrlOnImage">10</p>'
                    '<p class="line-clamp-3 text-globalBody2Strong">活动</p></a>')
            return {p: session.PageResult(path=p, ok=True, status=200,
                                          html=html, kind="ok")
                    for p in paths}

    out = sync_mod.sync_once(store, Fetcher(), day="2026-11-16", force=True)
    assert out["ok"] and out["summary"] is not None
    assert proxy.commits == 1, f"整轮同步应只提交一次，实际 {proxy.commits} 次"


def test_sync_once_twice_uses_no_browser(tmp_path, monkeypatch):
    """从 sync_once 入口验证：连续两轮同步完全不接触 Playwright。"""
    state = _fake_requests(monkeypatch, {}, delay=0.02)
    fetcher = session.HttpFetcher(_store(tmp_path))
    store = Store(str(tmp_path / "http.db"))
    for _ in range(2):
        out = sync_mod.sync_once(store, fetcher, day="2026-11-16", force=True)
        assert out["ok"]
    assert len(state["urls"]) == 4         # 两轮 × 两页，全是 HTTP
    store.close_thread_connection()


def test_startup_first_sync_has_no_delay():
    """纯 HTTP 没有冷启动，窗口一显示就立即取数。"""
    src = (ROOT / "rewards_assistant" / "app.py").read_text(encoding="utf-8")
    assert "QTimer.singleShot(0, lambda: self.sync_now(auto=True))" in src
    assert "QTimer.singleShot(250, lambda" not in src
    assert "QTimer.singleShot(1200" not in src
    assert "QTimer.singleShot(3000" not in src


def test_login_exports_cookies_and_logout_clears_them():
    login = (ROOT / "rewards_assistant" / "ui" / "login_dialog.py").read_text("utf-8")
    sess = (ROOT / "rewards_assistant" / "session.py").read_text("utf-8")
    assert "CookieStore().save(cookies)" in login
    assert "CookieStore().clear()" in login
    assert "CookieStore().clear()" in sess
    assert "migrate_legacy()" in sess
    assert "BrowserSessionFetcher().clear_session()" in sess

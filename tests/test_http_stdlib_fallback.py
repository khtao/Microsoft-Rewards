# -*- coding: utf-8 -*-
"""回归：系统环境里 ``requests.Session`` 被 ``lazy_import`` 代理污染时，
同步不再失败，自动切到标准库 ``urllib`` 后端。

真机报错：``同步失败: 'lazy_import' object is not callable``，积分卡停留在
上次快照。根因是 ``HttpFetcher`` 直接 ``requests.Session()``，而某些系统
Python 环境（或 PyInstaller 冻结导入器与系统 site-packages 混用时）会把
``Session`` 换成类型名为 ``lazy_import`` 的不可调用代理对象。
"""
from __future__ import annotations

import threading
import time
import urllib.request
from types import SimpleNamespace


from rewards_assistant import session
from rewards_assistant.credentials import CookieStore

HTML = '<html><h2>每日活动</h2><p>可用积分 22719</p></html>'


class lazy_import:
    """类型名与真机报错完全一致：'lazy_import' object is not callable。"""


class _Headers:
    def get_content_charset(self):
        return "utf-8"


class _Resp:
    def __init__(self, text=HTML, status=200):
        self.status = status
        self.headers = _Headers()
        self._text = text

    def read(self):
        return self._text.encode("utf-8")

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _store(tmp_path):
    store = CookieStore(tmp_path / "cookies.bin")
    store.save([
        {"name": "MSPAuth", "value": "A1", "domain": ".bing.com", "path": "/",
         "secure": True},
        {"name": "SRCHD", "value": "B2", "domain": "rewards.bing.com",
         "path": "/", "secure": False},
        # 无关站点 Cookie 必须被过滤，绝不随重定向发到别站
        {"name": "OTHER", "value": "C3", "domain": ".google.com", "path": "/"},
    ])
    return store


def _fake_opener(monkeypatch, state, *, status=200, text=HTML, delay=0.0):
    lock = state.setdefault("lock", threading.Lock())

    class FakeOpener:
        def open(self, req, timeout=None):
            with lock:
                state["urls"] = state.get("urls", []) + [req.full_url]
                state["timeouts"] = state.get("timeouts", []) + [timeout]
                state["active"] = state.get("active", 0) + 1
                state["max_active"] = max(state.get("max_active", 0), state["active"])
            if delay:
                time.sleep(delay)
            with lock:
                state["active"] -= 1
            if 400 <= status < 600:
                import urllib.error
                err = urllib.error.HTTPError(req.full_url, status, "err",
                                             _Headers(), None)
                err.headers = _Headers()
                err.read = lambda: text.encode("utf-8")
                raise err
            return _Resp(text, status)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a, **k: FakeOpener())
    return state


# ---------------------------------------------------------------- 预检

def test_requests_session_factory_rejects_lazy_import_proxy(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "Session", lazy_import())
    assert session._requests_session_factory() is None


def test_requests_session_factory_rejects_noncallable_get(monkeypatch):
    import requests

    class Broken:
        def __init__(self):
            self.get = lazy_import()

        def close(self):
            pass

    monkeypatch.setattr(requests, "Session", Broken)
    assert session._requests_session_factory() is None


def test_requests_session_factory_accepts_healthy_requests():
    assert callable(session._requests_session_factory())


# ---------------------------------------------------------------- 自动回退

def test_polluted_requests_falls_back_to_stdlib_and_sync_still_works(tmp_path, monkeypatch):
    import requests
    monkeypatch.setattr(requests, "Session", lazy_import())
    state = _fake_opener(monkeypatch, {}, delay=0.08)

    fetcher = session.HttpFetcher(_store(tmp_path))
    out = fetcher.fetch_many(["/dashboard", "/earn"])

    assert all(r.ok and r.html == HTML and r.kind == "ok" for r in out.values())
    assert state["max_active"] == 2, "标准库后端也必须两页并发"
    assert sorted(state["urls"]) == [
        "https://rewards.bing.com/dashboard", "https://rewards.bing.com/earn"]
    # 不会再把 'lazy_import' object is not callable 抛给同步层
    st = session.probe(fetcher)
    assert st.state == session.SIGNED_IN and st.can_sync


def test_runtime_not_callable_also_retries_with_stdlib(tmp_path, monkeypatch):
    """预检通过但 s.get() 内部仍是 lazy 代理：该页自动改用标准库重试。"""
    import requests

    class SneakySession:
        def __init__(self):
            self.cookies = SimpleNamespace(set=lambda *a, **k: None)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return lazy_import()()          # 'lazy_import' object is not callable

    monkeypatch.setattr(requests, "Session", SneakySession)
    state = _fake_opener(monkeypatch, {})
    out = session.HttpFetcher(_store(tmp_path)).fetch_many(["/dashboard"])
    assert out["/dashboard"].ok and out["/dashboard"].html == HTML
    assert state["urls"] == ["https://rewards.bing.com/dashboard"]


# ---------------------------------------------------------------- CookieJar

def test_stdlib_opener_keeps_only_bing_cookies_and_respects_domain():
    opener = session._stdlib_opener([
        {"name": "MSPAuth", "value": "A1", "domain": ".bing.com", "path": "/",
         "secure": True},
        {"name": "SRCHD", "value": "B2", "domain": "rewards.bing.com",
         "path": "/"},
        {"name": "OTHER", "value": "C3", "domain": ".google.com", "path": "/"},
        {"name": "NOVALUE", "value": "", "domain": "bing.com", "path": "/"},
    ])
    jar = next(h.cookiejar for h in opener.handlers
               if isinstance(h, urllib.request.HTTPCookieProcessor))
    names = {c.name for c in jar}
    assert names == {"MSPAuth", "SRCHD"}, names
    msp = next(c for c in jar if c.name == "MSPAuth")
    assert msp.domain_initial_dot and msp.secure and msp.domain == "bing.com"


def test_stdlib_request_has_no_store_headers_and_browser_ua(tmp_path, monkeypatch):
    import requests
    monkeypatch.setattr(requests, "Session", lazy_import())
    seen = {}

    class Opener:
        def open(self, req, timeout=None):
            seen["req"] = req
            seen["timeout"] = timeout
            return _Resp()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a, **k: Opener())
    session.HttpFetcher(_store(tmp_path), timeout=(4, 12)).fetch_many(["/dashboard"])
    req = seen["req"]
    assert req.get_method() == "GET"
    assert req.full_url == "https://rewards.bing.com/dashboard"
    # urllib 把 header 名规范化成首字母大写形式
    hdr = {k.lower(): v for k, v in req.header_items()}
    assert hdr["cache-control"] == "no-cache"
    assert "Chrome/124" in hdr["user-agent"]
    assert "cookie" not in hdr, "Cookie 必须由 CookieJar 按域注入，不能手拼"
    assert seen["timeout"] == 12.0


# ---------------------------------------------------------------- HTTP 状态

def test_stdlib_http_error_keeps_five_state_semantics(tmp_path, monkeypatch):
    import requests
    monkeypatch.setattr(requests, "Session", lazy_import())
    for status, expected in ((401, "signed_out"), (403, "signed_out"),
                             (429, "transient"), (503, "transient")):
        _fake_opener(monkeypatch, {}, status=status, text="<html>sign in</html>")
        r = session.HttpFetcher(_store(tmp_path)).fetch_many(["/dashboard"])["/dashboard"]
        assert r.status == status and r.kind == expected, (status, r.kind)


def test_stdlib_network_error_is_transient(tmp_path, monkeypatch):
    import requests
    monkeypatch.setattr(requests, "Session", lazy_import())

    class Opener:
        def open(self, req, timeout=None):
            raise OSError("network down")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a, **k: Opener())
    r = session.HttpFetcher(_store(tmp_path)).fetch_many(["/dashboard"])["/dashboard"]
    assert r.kind == "transient" and "network down" in r.error


def test_healthy_requests_path_is_unchanged(tmp_path, monkeypatch):
    """requests 正常时仍走原路径，老回归测试完全兼容。"""
    import requests

    class S:
        def __init__(self):
            self.cookies = SimpleNamespace(set=lambda *a, **k: None)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **k):
            return SimpleNamespace(status_code=200, text=HTML)

    monkeypatch.setattr(requests, "Session", S)

    def forbidden(*a, **k):
        raise AssertionError("requests 可用时不应切到标准库")

    monkeypatch.setattr(urllib.request, "build_opener", forbidden)
    r = session.HttpFetcher(_store(tmp_path)).fetch_many(["/dashboard"])["/dashboard"]
    assert r.ok and r.html == HTML

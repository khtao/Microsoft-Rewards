# -*- coding: utf-8 -*-
"""「可领取」卡：闸门 / 点击 / 领取页 / 多实例账号隔离的离线回归。

不访问网络、不启动真实浏览器：Playwright、HttpFetcher、CookieStore 全部用
可替换的桩。重点覆盖用户要求：

* 有可领取积分且条件齐全 → 点击打开官网领取页；
* 无可领取 / 未登录 / 同步或自动运行中 / 凭证失效 / 已有领取窗口 → 卡片
  **完全无动作**（不发信号、不开浏览器、不弹提示）；
* 多开时绝不用系统默认浏览器（可能登录着另一个账号），而是把**当前实例**
  的加密凭证注入该实例自己的空 Edge profile。
"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from rewards_assistant import app as app_mod
from rewards_assistant import browser, claim
from rewards_assistant.claim import ClaimUnavailable
from rewards_assistant.config import ENV_INSTANCE
from rewards_assistant.credentials import CookieStore
from rewards_assistant.session import PageResult
from rewards_assistant.ui.modern.widgets import StatCard


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


class FakeStore:
    """只实现 claim 层用到的 load()。"""

    def __init__(self, cookies=None, path=None) -> None:
        self.cookies = cookies if cookies is not None else [
            {"name": "MSPAuth", "value": "token", "domain": ".bing.com",
             "path": "/", "secure": True, "expires": -1}]
        self.path = Path(path or "/tmp/fake-session_cookies.bin")

    def load(self, now=None):
        return list(self.cookies)


class FakeFetcher:
    result: PageResult
    seen_store = None

    def __init__(self, store=None) -> None:
        FakeFetcher.seen_store = store

    def fetch_many(self, paths):
        return {p: self.result for p in paths}


def ok_dashboard(claimable=60) -> PageResult:
    return PageResult(path="/dashboard", ok=True, status=200,
                      html=f'<div>"claimablePoints":{claimable}</div>',
                      kind="ok", signin_page=False)


# ---------------------------------------------------------------- 闸门

def test_claim_ready_requires_every_condition():
    assert claim.claim_ready(60, True, True) is True
    assert claim.claim_ready(1, True, True) is True
    cases = [
        (None, True, True, {}), (0, True, True, {}), ("x", True, True, {}),
        (-5, True, True, {}), (60, None, True, {}), (60, False, True, {}),
        (60, True, False, {}),
        (60, True, True, {"syncing": True}),
        (60, True, True, {"running": True}),
        (60, True, True, {"opening": True}),
        (60, True, True, {"last_sync_ok": False}),
    ]
    for value, login, cookies, kw in cases:
        assert claim.claim_ready(value, login, cookies, **kw) is False, (
            value, login, cookies, kw)


def test_verify_claim_session_accepts_live_claimable(monkeypatch):
    FakeFetcher.result = ok_dashboard(60)
    monkeypatch.setattr(claim, "HttpFetcher", FakeFetcher)
    store = FakeStore()
    assert claim.verify_claim_session(store) == 60
    assert FakeFetcher.seen_store is store


@pytest.mark.parametrize("result", [
    ok_dashboard(0),
    PageResult(path="/dashboard", ok=True, status=200,
               html="<h1>登录</h1>", kind="ok", signin_page=True),
    PageResult(path="/dashboard", ok=False, status=503, html="",
               kind="transient", error="网络异常"),
])
def test_verify_claim_session_refuses_unavailable(result, monkeypatch):
    FakeFetcher.result = result
    monkeypatch.setattr(claim, "HttpFetcher", FakeFetcher)
    with pytest.raises(ClaimUnavailable):
        claim.verify_claim_session(FakeStore())


def test_verify_claim_session_refuses_empty_credentials(monkeypatch):
    called = []
    monkeypatch.setattr(claim, "HttpFetcher",
                        lambda store=None: called.append(store))
    with pytest.raises(ClaimUnavailable, match="会话"):
        claim.verify_claim_session(FakeStore([]))
    assert called == []


# ---------------------------------------------------------------- 打开领取页

class FakePage:
    def __init__(self, calls) -> None:
        self.calls = calls

    def goto(self, url, **kw):
        self.calls.append(("goto", url, kw))

    def is_closed(self):
        self.calls.append(("is_closed",))
        return True                      # 离线桩：用户已关窗


class FakeCtx:
    def __init__(self, calls) -> None:
        self.calls = calls
        self.pages = [FakePage(calls)]

    def new_page(self):
        return FakePage(self.calls)

    def close(self):
        self.calls.append(("close",))


class FakePW:
    def __enter__(self):
        return "pw"

    def __exit__(self, *args):
        return False


def _install_open_stubs(monkeypatch, tmp_path, launch_exc=None):
    calls = []
    snap = tmp_path / "claim_edge_profile_fake"
    snap.mkdir()

    def launch(pw, **kw):
        calls.append(("launch", kw))
        if launch_exc:
            raise launch_exc
        return FakeCtx(calls), snap

    def cleanup(path, prefix=None):
        calls.append(("cleanup", Path(path), prefix))

    monkeypatch.setattr(claim, "HttpFetcher", FakeFetcher)
    monkeypatch.setattr(claim, "_playwright", lambda: FakePW())
    monkeypatch.setattr(claim, "launch_automation_edge", launch)
    monkeypatch.setattr(claim, "cleanup_automation_profile", cleanup)
    return calls, snap


def test_open_claim_window_opens_current_account_page(monkeypatch, tmp_path):
    FakeFetcher.result = ok_dashboard(60)
    calls, snap = _install_open_stubs(monkeypatch, tmp_path)
    store = FakeStore()
    notes = []
    assert claim.open_claim_window(store=store, wait_for_close=False,
                                   log=lambda a, d: notes.append((a, d))) is True
    launch_kw = next(kw for kind, kw in calls if kind == "launch")
    assert launch_kw["store"] is store
    assert launch_kw["profile_prefix"] == claim.CLAIM_PROFILE_DIRNAME
    assert launch_kw["headless"] is False
    assert ("goto", claim.CLAIM_URL,
            {"wait_until": "domcontentloaded",
             "timeout": claim.CLAIM_NAV_TIMEOUT_MS}) in calls
    assert ("cleanup", snap, claim.CLAIM_PROFILE_DIRNAME) in calls
    assert any(a == "claim_open" and claim.CLAIM_URL in d for a, d in notes)


def test_open_claim_window_keeps_window_until_closed(monkeypatch, tmp_path):
    FakeFetcher.result = ok_dashboard(60)
    calls, _ = _install_open_stubs(monkeypatch, tmp_path)
    claim.open_claim_window(store=FakeStore(), wait_for_close=True)
    assert ("is_closed",) in calls
    assert ("close",) in calls


def test_open_claim_window_honours_stop_event(monkeypatch, tmp_path):
    """退出时 set：不轮询、直接收尾并清理 profile。"""
    FakeFetcher.result = ok_dashboard(60)
    calls, snap = _install_open_stubs(monkeypatch, tmp_path)
    stop = SimpleNamespace(is_set=lambda: True)
    notes = []
    claim.open_claim_window(store=FakeStore(), wait_for_close=True,
                            stop_event=stop, log=lambda a, d: notes.append((a, d)))
    assert ("is_closed",) not in calls
    assert ("close",) in calls
    assert ("cleanup", snap, claim.CLAIM_PROFILE_DIRNAME) in calls
    assert any(a == "claim_close" for a, _ in notes)


def test_open_claim_window_no_browser_when_official_says_zero(monkeypatch,
                                                             tmp_path):
    FakeFetcher.result = ok_dashboard(0)
    started = []
    monkeypatch.setattr(claim, "HttpFetcher", FakeFetcher)
    monkeypatch.setattr(claim, "_playwright",
                        lambda: started.append(True) or FakePW())
    with pytest.raises(ClaimUnavailable, match="没有可领取"):
        claim.open_claim_window(store=FakeStore(), wait_for_close=False)
    assert started == []                      # 完全无动作，连浏览器都不启动


def test_open_claim_window_launch_failure_still_cleans_up(monkeypatch, tmp_path):
    FakeFetcher.result = ok_dashboard(60)
    calls, _ = _install_open_stubs(monkeypatch, tmp_path,
                                   launch_exc=RuntimeError("Edge exploded"))
    with pytest.raises(RuntimeError):
        claim.open_claim_window(store=FakeStore(), wait_for_close=False)
    assert any(kind == "launch" for kind, *_ in calls)


def test_claim_module_never_uses_system_default_browser():
    src = Path(claim.__file__).read_text("utf-8")
    for forbidden in ("local_browser", "QDesktopServices", "os.startfile",
                      "xdg-open", "webbrowser.open"):
        assert forbidden not in src, forbidden


def test_claim_url_is_official_rewards_dashboard():
    assert claim.CLAIM_URL == "https://rewards.bing.com/dashboard"


# ---------------------------------------------------------------- 多实例

def test_claim_profile_and_credentials_are_instance_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    monkeypatch.setenv(ENV_INSTANCE, "work-account")
    work_store = CookieStore()
    work_profile = browser.new_automation_profile_dir(
        prefix=claim.CLAIM_PROFILE_DIRNAME)
    assert "instances/work-account" in str(work_store.path)
    assert "instances/work-account" in str(work_profile)
    assert work_profile.name.startswith(claim.CLAIM_PROFILE_DIRNAME + "_")

    monkeypatch.setenv(ENV_INSTANCE, "personal")
    other_store = CookieStore()
    other_profile = browser.new_automation_profile_dir(
        prefix=claim.CLAIM_PROFILE_DIRNAME)
    assert other_store.path != work_store.path
    assert other_profile != work_profile
    assert "instances/personal" in str(other_profile)


def test_claim_prune_does_not_touch_automation_profiles(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    monkeypatch.setenv(ENV_INSTANCE, "isolated")
    auto = browser.new_automation_profile_dir()
    claim_dir = browser.new_automation_profile_dir(
        prefix=claim.CLAIM_PROFILE_DIRNAME)
    browser.prune_automation_profiles(keep=0,
                                      prefix=claim.CLAIM_PROFILE_DIRNAME)
    assert auto.exists() and not claim_dir.exists()
    browser.cleanup_automation_profile(auto)


def test_other_instance_cannot_reuse_first_account_session(tmp_path, monkeypatch):
    """A 环境登录、B 环境点击：B 的凭证为空，校验直接拒绝，不发网络请求。"""
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    monkeypatch.setenv(ENV_INSTANCE, "account-a")
    CookieStore().save([{"name": "MSPAuth", "value": "secret",
                         "domain": ".bing.com", "path": "/",
                         "secure": True, "expires": -1}])
    monkeypatch.setenv(ENV_INSTANCE, "account-b")
    assert CookieStore().load() == []
    requested = []
    monkeypatch.setattr(claim, "HttpFetcher",
                        lambda store=None: requested.append(store))
    with pytest.raises(ClaimUnavailable):
        claim.verify_claim_session(CookieStore())
    assert requested == []


def test_verify_passes_current_instance_store_to_http_fetcher(monkeypatch,
                                                            tmp_path):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    monkeypatch.setenv(ENV_INSTANCE, "work")
    FakeFetcher.result = ok_dashboard(60)
    monkeypatch.setattr(claim, "HttpFetcher", FakeFetcher)
    # 用真实实例路径 + 桩凭证，既验证隔离又不依赖本机加密文件。
    store = FakeStore(path=CookieStore().path)
    claim.verify_claim_session(store)
    assert FakeFetcher.seen_store is store
    assert "instances/work" in str(store.path)
    assert "work" in claim.instance_tag()


# ---------------------------------------------------------------- 卡片点击

def test_stat_card_is_inert_until_clickable(qapp):
    card = StatCard("可领取")
    hits = []
    card.clicked.connect(lambda: hits.append(1))
    assert card.is_clickable() is False
    assert card.cursor().shape() == Qt.CursorShape.ArrowCursor
    QTest.mouseClick(card, Qt.MouseButton.LeftButton)
    assert hits == []                                # 无动作

    card.set_clickable(True)
    assert card.cursor().shape() == Qt.CursorShape.PointingHandCursor
    QTest.mouseClick(card, Qt.MouseButton.LeftButton)
    assert hits == [1]

    card.set_clickable(False)
    QTest.mouseClick(card, Qt.MouseButton.LeftButton)
    assert hits == [1]                               # 再次无动作


def test_other_stat_cards_never_become_clickable(qapp):
    from rewards_assistant.ui.modern.main_window import HomePage

    class Stub:
        lang = "zh"

        def open_login(self): pass
        def show_settings(self): pass
        def sync_now(self, *a, **kw): pass
        def auto_running(self): return False
        def claim_available(self): return False
        def open_claim_page(self): pass

    home = HomePage(Stub())
    hits = []
    home.claim_requested.connect(lambda: hits.append(1))
    assert not home.card_available.is_clickable()
    assert not home.card_today.is_clickable()
    assert not home.card_claimable.is_clickable()
    QTest.mouseClick(home.card_claimable, Qt.MouseButton.LeftButton)
    assert hits == []
    home.set_claim_available(True)
    assert home.card_claimable.is_clickable()
    QTest.mouseClick(home.card_claimable, Qt.MouseButton.LeftButton)
    assert hits == [1]


def test_window_wiring_static_contract():
    from rewards_assistant.ui.modern import main_window
    src = Path(main_window.__file__).read_text("utf-8")
    assert "self.card_claimable.clicked.connect(self.claim_requested.emit)" in src
    assert "def _on_claim_requested(self)" in src
    assert "def sync_claim_state(self)" in src
    assert "self.sync_claim_state()" in src
    assert "open_claim_page" in src


# ---------------------------------------------------------------- Application

def _application(monkeypatch, tmp_path, claimable=60, login_ok=True,
                 cookies=True, **state):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    QApplication.instance() or QApplication([])
    ap = app_mod.Application([])
    ap.store = SimpleNamespace(
        page_points=lambda day: {"claimable": claimable,
                                 "available": 100, "today": 20})
    ap.window = SimpleNamespace(
        _login_ok=login_ok,
        sync_claim_state=lambda: None,
        log=lambda msg: None)
    ap.auto_running = lambda: False
    ap._syncing = state.get("syncing", False)
    ap._claim_opening = state.get("opening", False)
    ap._last_sync_ok = state.get("last_sync_ok", True)
    monkeypatch.setattr(app_mod, "CookieStore",
                        lambda: FakeStore([] if not cookies else None))
    return ap


def test_application_claim_available_matrix(monkeypatch, tmp_path):
    assert _application(monkeypatch, tmp_path).claim_available() is True
    for kw in ({"claimable": 0}, {"claimable": None}, {"login_ok": None},
               {"login_ok": False}, {"cookies": False},
               {"syncing": True}, {"opening": True},
               {"last_sync_ok": False}):
        ap = _application(monkeypatch, tmp_path, **kw)
        assert ap.claim_available() is False, kw
    ap = _application(monkeypatch, tmp_path)
    ap.auto_running = lambda: True
    assert ap.claim_available() is False


def test_application_open_claim_page_is_no_op_when_unavailable(monkeypatch,
                                                              tmp_path, qapp):
    ap = _application(monkeypatch, tmp_path, claimable=0)
    called = []
    monkeypatch.setattr(app_mod, "open_claim_window",
                        lambda **kw: called.append(kw))
    ap.open_claim_page()
    assert called == []
    assert ap._claim_opening is False
    assert ap._claim_thread is None


def test_application_open_claim_page_uses_current_instance_store(monkeypatch,
                                                                tmp_path, qapp):
    ap = _application(monkeypatch, tmp_path)
    seen = {}
    done = []

    def fake_open(**kw):
        seen.update(kw)
        done.append(True)

    monkeypatch.setattr(app_mod, "open_claim_window", fake_open)
    ap.open_claim_page()
    assert ap._claim_opening is True             # 窗口开着时闸门关闭
    assert ap.claim_available() is False
    assert ap._claim_thread is not None
    ap._claim_thread.join(timeout=2)
    for _ in range(20):                          # 等待 queued signal 回主线程
        QApplication.processEvents()
        if ap._claim_opening is False:
            break
        time.sleep(0.01)
    assert done and seen["url"] == claim.CLAIM_URL
    assert seen["store"] is not None
    assert seen["stop_event"] is ap._claim_stop
    assert not ap._claim_stop.is_set()          # 新一轮开始时已 clear
    assert ap._claim_opening is False
    assert ap._claim_thread is None


def test_application_quit_signals_claim_window_and_joins(monkeypatch,
                                                         tmp_path, qapp):
    ap = _application(monkeypatch, tmp_path)
    ap.window = None                          # 走 quit() 的无窗口分支
    joined = []
    ap._claim_thread = SimpleNamespace(
        is_alive=lambda: True,
        join=lambda timeout=None: joined.append(timeout))
    assert not ap._claim_stop.is_set()
    ap.quit()
    assert ap._claim_stop.is_set()
    assert joined == [1.0]


def test_application_failure_is_silent(monkeypatch, tmp_path, qapp):
    """启动失败只写日志，不弹 toast、不改状态行。"""
    ap = _application(monkeypatch, tmp_path)
    toasts = []

    def fake_open(**kw):
        raise RuntimeError("Edge exploded at C:\\Users\\secret\\profile")

    ap._toast = lambda *a, **kw: toasts.append((a, kw))
    monkeypatch.setattr(app_mod, "open_claim_window", fake_open)
    logs = []
    ap._log = lambda msg: logs.append(msg)
    ap.window.log = lambda msg: logs.append(msg)
    ap.open_claim_page()
    ap._claim_thread.join(timeout=2)
    QApplication.processEvents()
    assert toasts == []
    assert any("未打开" in line for line in logs)
    assert not any("C:\\Users" in line for line in logs)
    assert ap._claim_opening is False

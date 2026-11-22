# -*- coding: utf-8 -*-
"""自动化链路登录态：全新空目录 + 注入加密凭证，不再复制任何 profile。"""
from __future__ import annotations

import time
from pathlib import Path

from rewards_assistant import browser
from rewards_assistant.credentials import CookieStore

ROOT = Path(__file__).resolve().parents[1]


def _seed_cookies(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    store = CookieStore()
    store.save([
        {"name": "MSPAuth", "value": "secret", "domain": ".live.com",
         "path": "/", "secure": True, "expires": -1},
        {"name": "SRCHD", "value": "x", "domain": ".bing.com",
         "path": "/", "secure": True, "expires": time.time() + 3600},
        {"name": "hostless", "value": "h", "domain": "",
         "path": "/", "secure": True, "expires": -1},
    ])
    return store


def test_automation_profile_is_fresh_empty_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    assert browser.app_profile_dir() == tmp_path / "browser_profile"
    snap = browser.new_automation_profile_dir()
    assert snap.parent == tmp_path and snap.name.startswith("automation_edge_profile_")
    assert snap != browser.app_profile_dir()
    assert list(snap.iterdir()) == [], "全新目录必须为空：不复制任何 profile 文件"
    assert not (snap / "SingletonLock").exists()
    assert not (snap / "Default").exists()


def test_playwright_cookies_conversion(tmp_path, monkeypatch):
    store = _seed_cookies(tmp_path, monkeypatch)
    cookies = browser.playwright_cookies(store)
    assert len(cookies) == 3
    sess = next(c for c in cookies if c["name"] == "MSPAuth")
    assert sess["domain"] == ".live.com" and sess["path"] == "/"
    assert sess["secure"] and sess["sameSite"] == "None"
    assert "expires" not in sess, "会话 Cookie 不能传 expires=-1（会被当成 1970 已过期）"
    future = next(c for c in cookies if c["name"] == "SRCHD")
    assert future["expires"] > time.time()
    hostless = next(c for c in cookies if c["name"] == "hostless")
    assert hostless["url"] == "https://rewards.bing.com/"
    assert "domain" not in hostless and "path" not in hostless


def test_inject_saved_cookies_uses_context(tmp_path, monkeypatch):
    store = _seed_cookies(tmp_path, monkeypatch)

    class Ctx:
        def __init__(self):
            self.got = None

        def add_cookies(self, cookies):
            self.got = cookies

    ctx = Ctx()
    assert browser.inject_saved_cookies(ctx, store) == 3
    assert len(ctx.got) == 3


def test_inject_without_credentials_fails_clearly(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))

    class Ctx:
        def add_cookies(self, cookies):
            raise AssertionError("无凭证时不应注入")

    try:
        browser.inject_saved_cookies(Ctx(), CookieStore())
    except RuntimeError as exc:
        assert "尚未保存登录会话" in str(exc)
    else:
        raise AssertionError("应当明确提示重新登录")


def test_browser_error_hint_never_leaks_local_paths():
    raw = ("Error: [('C:\\Users\\86155\\AppData\\Local\\Microsoft\\Edge\\"
           "User Data\\Profile 1\\LOCK', 'snapshot', '[Errno 13] Permission denied')]")
    hint = browser.browser_error_hint(raw)
    assert "C:" not in hint and "Users" not in hint and "AppData" not in hint
    assert "占用" in hint
    assert "ProcessSingleton" not in hint
    singleton = ("BrowserType.launch_persistent_context: Failed to create a "
                 "ProcessSingleton for your profile directory.")
    assert "无需关闭" in browser.browser_error_hint(singleton)
    assert "Edge" in browser.browser_error_hint("Executable doesn't exist at msedge.exe")
    closed = ("BrowserType.launch_persistent_context: Target page, context or "
              "browser has been closed")
    hint2 = browser.browser_error_hint(closed)
    assert "残留进程" not in hint2 and "全新配置重试" not in hint2
    assert "请重试" in hint2


def test_automation_callers_no_longer_reference_profile_copy():
    aw = (ROOT / "rewards_assistant" / "activity_worker.py").read_text(encoding="utf-8")
    api = (ROOT / "rewards_assistant" / "api.py").read_text(encoding="utf-8")
    for src in (aw, api):
        assert "launch_automation_edge" in src
        assert "cleanup_automation_profile" in src
        assert "copy_profile(" not in src
        assert "default_profile_dir()" not in src
    b = (ROOT / "rewards_assistant" / "browser.py").read_text(encoding="utf-8")
    assert "def new_automation_profile_dir" in b
    assert "def launch_automation_edge" in b
    assert "def inject_saved_cookies" in b
    assert "add_cookies(cookies)" in b
    assert "def copy_profile" not in b
    assert "def automation_source_profile" not in b
    assert "SKIP_FILE_NAMES" not in b
    assert "browser_error_hint" in (ROOT / "rewards_assistant" / "ui" / "legacy.py").read_text(encoding="utf-8")


def test_exe_icon_is_embedded_in_spec():
    spec = (ROOT / "MSRewardsAssistant.spec").read_text(encoding="utf-8")
    assert "icon=EXE_ICON" in spec
    assert "app.ico" in spec
    ico = ROOT / "assets" / "icons" / "app.ico"
    assert ico.is_file() and ico.read_bytes()[:4] == b"\x00\x00\x01\x00"
    win = (ROOT / "build_windows.bat").read_text(encoding="utf-8")
    assert "app.ico" in win


def test_main_window_width_is_fixed_and_status_wraps():
    mw = (ROOT / "rewards_assistant" / "ui" / "modern" / "main_window.py").read_text(encoding="utf-8")
    widgets = (ROOT / "rewards_assistant" / "ui" / "modern" / "widgets.py").read_text(encoding="utf-8")
    assert "setFixedWidth(392)" in mw
    assert "QEvent.Type.WindowStateChange" in mw
    assert "browser_error_hint" in mw
    assert "self._label.setWordWrap(False)" in widgets
    assert "self._label.setMaximumWidth(340)" in widgets
    assert "elidedText" in widgets


def test_activity_worker_turns_old_launch_error_into_short_hint(tmp_path, monkeypatch):
    """回归截图里的「运行失败: Error: [('C:\\Users...Permission denied')]」。"""
    from rewards_assistant import activity_worker as aw
    raw = ("Error: [('C:\\Users\\86155\\AppData\\Local\\Microsoft\\Edge\\"
           "User Data\\Profile 1\\LOCK', 'snap', '[Errno 13] Permission denied')]")
    monkeypatch.setattr(aw, "launch_automation_edge",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError(raw)))

    def fake_start():
        class Starter:
            def start(self):
                class PW:
                    def stop(self):
                        pass
                return PW()
        return Starter()

    import playwright.sync_api as pw_api
    monkeypatch.setattr(pw_api, "sync_playwright", fake_start)

    worker = aw.ActivityWorker([], browser_options={})
    seen = []
    worker.completed.connect(lambda stats: seen.append(stats))
    worker.run()          # 直接跑，不起线程、不弹浏览器

    assert len(seen) == 1
    msg = seen[0]["error"]
    assert "C:" not in msg and "Users" not in msg and "Error: [(" not in msg
    assert "占用" in msg


def test_no_credentials_fails_fast_without_launching(tmp_path, monkeypatch):
    """未登录时不该启动浏览器再报「进程意外退出」。"""
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    called = []

    def forbidden(*_a, **_kw):
        called.append(True)
        raise AssertionError("未登录时不应启动 Edge")

    monkeypatch.setattr(browser, "launch_edge", forbidden)
    try:
        browser.launch_automation_edge(object())
    except RuntimeError as exc:
        assert "尚未保存登录会话" in str(exc)
    else:
        raise AssertionError("应当直接给出重新登录提示")
    assert not called
    assert not list(tmp_path.glob("automation_edge_profile_*")), "不应创建任何配置目录"


def test_inject_skips_one_bad_cookie_instead_of_failing_everything(tmp_path, monkeypatch):
    """迁移来的畸形 Cookie 不能让整批注入失败。"""
    store = _seed_cookies(tmp_path, monkeypatch)
    mixed = [
        {"name": "bad", "value": "v", "domain": "not a domain!", "path": "/"},
        {"name": "good", "value": "v", "url": "https://rewards.bing.com/"},
    ]
    monkeypatch.setattr(browser, "playwright_cookies", lambda store=None: mixed)

    class Ctx:
        def __init__(self):
            self.got = []

        def add_cookies(self, cookies):
            if any(c.get("domain") == "not a domain!" for c in cookies):
                raise ValueError("invalid domain")
            self.got.extend(cookies)

    ctx = Ctx()
    assert browser.inject_saved_cookies(ctx, store) == 1
    assert [c["name"] for c in ctx.got] == ["good"]

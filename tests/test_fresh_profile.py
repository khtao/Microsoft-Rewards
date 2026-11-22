# -*- coding: utf-8 -*-
"""回归：启动期「浏览器进程意外退出」→ 全新空目录 + 注入加密凭证。

旧链路复制整个 ``browser_profile`` 快照，SingletonLock / handoff / 残留进程
会让 Chromium 起来又退出。现在不复制任何 profile，登录态由 ``add_cookies``
注入，冲突只剩「瞬时目录被锁」这一种，换一次全新空目录即可。
"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from rewards_assistant import browser
from rewards_assistant.credentials import CookieStore

ROOT = Path(__file__).resolve().parents[1]


def _seed(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    CookieStore().save([{"name": "MSPAuth", "value": "secret",
                         "domain": ".live.com", "path": "/",
                         "secure": True, "expires": -1}])
    # 模拟「应用独立 profile 仍存在」：新版必须完全不读它
    prof = tmp_path / "browser_profile" / "Default"
    prof.mkdir(parents=True)
    (prof / "Preferences").write_text("{}", encoding="utf-8")
    (tmp_path / "browser_profile" / "SingletonLock").write_text("stale", encoding="utf-8")
    return prof


class _Ctx:
    def __init__(self, sink=None):
        self.sink = sink if sink is not None else []
        self.cookies = None
        self.closed = False

    def add_cookies(self, cookies):
        self.cookies = cookies
        self.sink.append(cookies)

    def close(self):
        self.closed = True


def test_new_automation_profile_dir_is_unique_and_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    a = browser.new_automation_profile_dir()
    b = browser.new_automation_profile_dir()
    assert a != b
    assert a.name.startswith("automation_edge_profile_")
    assert a.is_dir() and b.is_dir()
    assert a.name != "automation_edge_profile"
    assert list(a.iterdir()) == [] and list(b.iterdir()) == []


def test_prune_keeps_only_recent_snapshots(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    made = [browser.new_automation_profile_dir() for _ in range(6)]
    browser.prune_automation_profiles(keep=3)
    left = [d for d in tmp_path.iterdir()
            if d.is_dir() and d.name.startswith("automation_edge_profile_")]
    assert len(left) == 3
    assert all(d in made for d in left)


def test_launch_injects_credentials_and_never_copies_profile(tmp_path, monkeypatch):
    prof = _seed(tmp_path, monkeypatch)
    used, injected = [], []

    def fake_launch(pw, *, user_data_dir, **kw):
        used.append(user_data_dir)
        assert list(Path(user_data_dir).iterdir()) == [], "启动目录必须是全新空的"
        assert not (Path(user_data_dir) / "SingletonLock").exists()
        return _Ctx(injected)

    monkeypatch.setattr(browser, "launch_edge", fake_launch)
    ctx, snap = browser.launch_automation_edge(object())

    assert len(used) == 1 and ctx.cookies and len(ctx.cookies) == 1
    assert ctx.cookies[0]["name"] == "MSPAuth"
    assert "expires" not in ctx.cookies[0]
    assert snap == Path(used[0])
    # 应用独立 profile 里的陈旧锁完全没被碰过
    assert (prof.parent / "SingletonLock").read_text(encoding="utf-8") == "stale"
    assert not list(snap.glob("Default")), "不应复制 Default 目录"
    browser.cleanup_automation_profile(snap)


def test_launch_retries_with_a_brand_new_dir_on_closed_browser(tmp_path, monkeypatch):
    """截图日志：launch_persistent_context 报 browser has been closed。"""
    from playwright.sync_api import Error as PWError

    _seed(tmp_path, monkeypatch)
    used = []

    def fake_launch(pw, *, user_data_dir, **kw):
        used.append(user_data_dir)
        if len(used) == 1:
            raise PWError("BrowserType.launch_persistent_context: "
                          "Target page, context or browser has been closed")
        return _Ctx()

    monkeypatch.setattr(browser, "launch_edge", fake_launch)
    ctx, snap = browser.launch_automation_edge(object())

    assert len(used) == 2
    assert used[0] != used[1]
    assert not Path(used[0]).exists()          # 失败快照已清理
    assert ctx is not None and snap == Path(used[1])
    assert browser.is_profile_conflict_error(PWError("browser has been closed"))
    browser.cleanup_automation_profile(snap)


def test_launch_does_not_retry_unrelated_errors(tmp_path, monkeypatch):
    from playwright.sync_api import Error as PWError

    _seed(tmp_path, monkeypatch)
    used = []

    def fake_launch(pw, *, user_data_dir, **kw):
        used.append(user_data_dir)
        raise PWError("Executable doesn't exist at msedge.exe")

    monkeypatch.setattr(browser, "launch_edge", fake_launch)
    try:
        browser.launch_automation_edge(object())
    except PWError:
        pass
    else:
        raise AssertionError("应当直接抛出，不应重试")
    assert len(used) == 1
    assert not Path(used[0]).exists()


def test_closed_browser_hint_is_actionable():
    hint = browser.browser_error_hint(
        "BrowserType.launch_persistent_context: Target page, context or "
        "browser has been closed")
    assert "请重试" in hint
    assert "残留进程" not in hint and "全新配置重试" not in hint
    assert "无需关闭日常 Edge" in hint


def test_geo_describe_masks_public_ip():
    from rewards_assistant.geo import describe, mask_ip

    geo = SimpleNamespace(city="Guangzhou", region="", country="China",
                          country_code="CN", timezone="Asia/Shanghai",
                          locale="zh-CN", market="zh-CN", detected=True,
                          ip="183.62.48.142")
    out = describe(geo)
    assert "183.62.*.*" in out
    assert "183.62.48.142" not in out
    assert mask_ip("2001:db8:85a3::8a2e:370:7334") == "2001:db8:*:*"
    assert mask_ip("") == ""
    geo_src = (ROOT / "rewards_assistant" / "geo.py").read_text(encoding="utf-8")
    assert "mask_ip(p.ip)" in geo_src
    assert "（{p.ip}）" not in geo_src


def test_callers_use_unique_snapshot_and_cleanup():
    for rel in ("rewards_assistant/api.py", "rewards_assistant/activity_worker.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "launch_automation_edge" in src, rel
        assert "cleanup_automation_profile" in src, rel
        assert "copy_profile(" not in src, rel
    app = (ROOT / "rewards_assistant/api.py").read_text(encoding="utf-8")
    assert "use_snapshot" not in app
    assert "automation_profile_dir()" not in app


def test_credentials_expiry_is_kept_for_persistent_cookies(tmp_path, monkeypatch):
    """有真实过期时间的 Cookie 不应被当成会话 Cookie 丢掉 expires。"""
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    exp = time.time() + 7200
    CookieStore().save([{"name": "A", "value": "1", "domain": ".bing.com",
                         "path": "/", "secure": True, "expires": exp}])
    cookies = browser.playwright_cookies()
    assert len(cookies) == 1 and abs(cookies[0]["expires"] - exp) < 1


def test_arg_incompatibility_retries_with_minimal_launch(tmp_path, monkeypatch):
    """Playwright/Edge 版本差异拒绝可选参数时，降级用最小参数再启动一次。"""
    from playwright.sync_api import Error as PWError

    _seed(tmp_path, monkeypatch)
    calls = []

    def fake_launch(pw, *, user_data_dir, minimal=False, **kw):
        calls.append((user_data_dir, minimal))
        if len(calls) == 1:
            raise PWError("BrowserType.launch_persistent_context: "
                          "Unexpected option: no_viewport")
        return _Ctx()

    monkeypatch.setattr(browser, "launch_edge", fake_launch)
    ctx, snap = browser.launch_automation_edge(object())

    assert ctx is not None
    assert len(calls) == 2
    assert calls[0][1] is False and calls[1][1] is True
    assert calls[0][0] != calls[1][0], "降级也应换全新空目录"
    assert not Path(calls[0][0]).exists()
    browser.cleanup_automation_profile(snap)


def test_conflict_retry_keeps_full_launch_args(tmp_path, monkeypatch):
    from playwright.sync_api import Error as PWError

    _seed(tmp_path, monkeypatch)
    calls = []

    def fake_launch(pw, *, user_data_dir, minimal=False, **kw):
        calls.append((user_data_dir, minimal))
        if len(calls) == 1:
            raise PWError("Target page, context or browser has been closed")
        return _Ctx()

    monkeypatch.setattr(browser, "launch_edge", fake_launch)
    ctx, snap = browser.launch_automation_edge(object())
    assert ctx is not None
    assert [m for _, m in calls] == [False, False]   # 占用只换目录，不降级参数
    browser.cleanup_automation_profile(snap)


def test_launch_edge_falls_back_to_found_edge_binary(tmp_path, monkeypatch):
    """channel=msedge 解析不到时，自动改用 find_edge_binary() 的绝对路径。"""
    from playwright.sync_api import Error as PWError

    calls = []
    edge = str(tmp_path / "msedge.exe")
    Path(edge).write_bytes(b"")
    monkeypatch.setattr(browser, "find_edge_binary", lambda: edge)

    class FakeCtx:
        def add_init_script(self, script):
            pass

    class FakePW:
        class chromium:
            @staticmethod
            def launch_persistent_context(**kw):
                calls.append(dict(kw))
                if len(calls) == 1:
                    raise PWError("Executable doesn't exist at msedge.exe")
                return FakeCtx()

    ctx = browser.launch_edge(FakePW(), user_data_dir=str(tmp_path / "empty"),
                              headless=False, verbose=False, flag_warning="on")
    assert ctx is not None
    assert len(calls) == 2
    assert calls[0].get("channel") == "msedge"
    assert "channel" not in calls[1] and calls[1].get("executable_path") == edge


def test_is_launch_arg_error_matches_only_arg_problems():
    from playwright.sync_api import Error as PWError
    assert browser.is_launch_arg_error(PWError("Unexpected option: permissions"))
    assert browser.is_launch_arg_error(PWError("no_viewport is not supported"))
    assert not browser.is_launch_arg_error(PWError("Executable doesn't exist"))
    assert not browser.is_launch_arg_error(PWError("Permission denied"))

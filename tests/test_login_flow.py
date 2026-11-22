# -*- coding: utf-8 -*-
"""登录窗口回归测试：参数错位、一闪即过、SingletonLock 占用、日志刷屏。

对应实际反馈的两个现象：
1. ``unsupported operand type(s) for //: 'LoginDialog' and 'int'``
2. 浏览器窗口刚打开就被关闭 + ``Failed to create a ProcessSingleton``

全部离线，不启动真实浏览器。
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from rewards_assistant import session
from rewards_assistant.session import (
    TRANSIENT_ERROR, PageResult, ReadOnlyFetcher, probe,
)

ABOUT = ("<title>关于 – Microsoft Rewards</title><h1>了解微软积分商城的优势</h1>"
         "<a>登录创建帐户</a><a>登录</a>")
LOGGED_IN = "<html><h2>每日活动</h2><p>可用积分 1,234</p></html>"


# ------------------------------------------------------- 参数顺序（图 2）

def test_login_worker_takes_parent_first():
    """构造签名回归：LoginWorker(self) 不得把 LoginDialog 当成 wait_seconds。"""
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QObject
    from rewards_assistant.ui.login_dialog import LoginWorker

    parent = QObject()
    worker = LoginWorker(parent)
    assert worker.parent() is parent
    assert isinstance(worker.wait_seconds, int) and worker.wait_seconds > 0
    # 关键：不会再出现 LoginDialog // 3
    assert worker.wait_seconds // 3 >= 1


def test_login_worker_default_wait_seconds_is_int():
    pytest.importorskip("PySide6")
    from rewards_assistant.ui.login_dialog import LoginWorker

    assert isinstance(LoginWorker(None).wait_seconds, int)


def test_login_dialog_source_contract():
    src = (Path(__file__).parents[1] / "rewards_assistant" / "ui" /
           "login_dialog.py").read_text(encoding="utf8")
    assert "def __init__(self, parent=None, wait_seconds" in src
    assert "LoginWorker(self)" in src
    assert "我已完成登录" in src
    assert "不在构造时自动探测" in src
    # 窗口只在用户点「我已完成登录」或超时后关闭，不因页面变化自动关闭
    assert "if not is_sign_in_page(html):" not in src


# ------------------------------------------------------- 窗口保持（一闪即过）

def test_login_poll_requires_confirmation_and_never_returns_close():
    """确认登录只是提示，绝不触发窗口关闭。"""
    assert session.login_poll_state(LOGGED_IN, 3, 1)[0] is False
    assert session.login_poll_state(LOGGED_IN, 6, 1)[0] is False
    ok, confirmed, msg = session.login_poll_state(LOGGED_IN, 9, 1)
    assert ok is True and confirmed == 2 and "我已完成登录" in msg
    # 登录页（含 MFA 中间页）重置确认计数
    detected, confirmed, msg = session.login_poll_state(ABOUT, 60, 2)
    assert detected is False and confirmed == 0 and "请在浏览器窗口" in msg


def test_login_poll_ignores_auth_host_and_about_blank():
    """短信验证 / OAuth 跳转页不算已登录，避免给出错误提示。"""
    for url in ("https://login.live.com/oauth20_authorize.srf?x=1",
                "https://account.live.com/proofs/Add",
                "about:blank", "https://www.bing.com/"):
        ok, confirmed, msg = session.login_poll_state(
            LOGGED_IN, 30, 2, url)
        assert ok is False and confirmed == 0, url
        assert "请在浏览器窗口" in msg
    ok, _, _ = session.login_poll_state(
        LOGGED_IN, 12, 1, "https://rewards.bing.com/dashboard")
    assert ok is True


# ------------------------------------------------------- Singleton 锁

def _touch_singletons(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        (p / name).write_text("stale", encoding="utf8")


def test_profile_guard_clears_stale_locks(tmp_path, monkeypatch):
    p = tmp_path / "browser_profile"
    _touch_singletons(p)
    monkeypatch.setattr(session, "_live_profile_processes", lambda _d: [])
    with session.profile_guard(p):
        pass
    assert not (p / "SingletonLock").exists()
    assert not (p / "SingletonCookie").exists()
    assert not (p / "SingletonSocket").exists()
    assert (p / ".app_profile.lock").exists()


def test_profile_guard_refuses_live_process_without_deleting(tmp_path, monkeypatch):
    p = tmp_path / "browser_profile"
    _touch_singletons(p)
    monkeypatch.setattr(session, "_live_profile_processes", lambda _d: [4242])
    monkeypatch.setattr(session.time, "sleep", lambda _s: None)  # 加速重试
    with pytest.raises(session.ProfileBusyError) as exc:
        with session.profile_guard(p):
            pass
    assert "占用" in str(exc.value) and "4242" in str(exc.value)
    assert (p / "SingletonLock").exists()  # 绝不盲目删正在使用的锁


def test_profile_guard_serializes_concurrent_access(tmp_path, monkeypatch):
    p = tmp_path / "browser_profile"
    p.mkdir()
    monkeypatch.setattr(session, "_live_profile_processes", lambda _d: [])
    events: list[tuple[str, int]] = []

    def worker(i: int) -> None:
        with session.profile_guard(p):
            events.append(("start", i))
            threading.Event().wait(0.05)
            events.append(("end", i))

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    # 不允许 start,start,end,end 这种重叠
    assert events[1] == ("end", events[0][1])
    assert events[2][0] == "start" and events[3] == ("end", events[2][1])


def test_fetch_many_reports_busy_error_briefly(tmp_path, monkeypatch):
    """profile 被占用时返回 transient + 短文案，不抛整段 Chromium 日志。"""
    from rewards_assistant.session import BrowserSessionFetcher

    p = tmp_path / "browser_profile"
    p.mkdir()
    monkeypatch.setattr(session, "_live_profile_processes", lambda _d: [777])
    monkeypatch.setattr(session.time, "sleep", lambda _s: None)
    out = BrowserSessionFetcher(str(p)).fetch_many(["/dashboard", "/earn"])
    assert set(out) == {"/dashboard", "/earn"}
    assert all(r.kind == "transient" for r in out.values())
    assert all("占用" in r.error and "[pid=" not in r.error for r in out.values())


def test_probe_keeps_busy_reason_and_never_blames_login():
    class BusyFetcher(ReadOnlyFetcher):
        def fetch_many(self, paths):
            return {p: PageResult(path=p, kind="transient",
                                  error="浏览器配置目录正被另一个窗口占用（PID 9）")
                    for p in paths}

    st = probe(BusyFetcher())
    assert st.state == TRANSIENT_ERROR and not st.can_sync
    assert "占用" in st.reason and "不是登录失效" in st.reason
    assert "无需重新登录" in st.reason


# ------------------------------------------------------- 日志可读性

def test_short_error_hides_chromium_command_line():
    err = ("BrowserType.launch_persistent_context: Failed to create a ProcessSingleton "
           "for your profile directory. This usually means that the profile is already "
           "in use by another instance of Chromium. [pid=564369] <launching> "
           "/opt/microsoft/msedge/msedge --disable-field-trial-config "
           "--disable-features=Translate,BackForwardCache,DestroyProfileOnBrowserClose "
           "--user-data-dir=/home/khtao/.local/share/msrewardsassistant/browser_profile "
           "--remote-debugging-pipe about:blank")
    msg = session.short_error(err)
    assert "[pid=" not in msg and "--disable-features" not in msg
    assert "已自动清理陈旧锁" in msg and len(msg) < 120


def test_redact_keeps_chromium_flags_readable():
    text = ("--disable-features=Translate,BackForwardCache,DestroyProfileOnBrowserClose "
            "--disable-background-timer-throttling --user-data-dir=/tmp/profile "
            "Cookie: MUID=ABCDEF; Authorization: Bearer abcdef0123456789")
    out = session.redact(text)
    assert "[长令牌已脱敏]" not in out          # 不再把命令行参数切碎
    assert "Translate" in out and "BackForwardCache" in out
    assert "MUID=ABCDEF" not in out and "abcdef0123456789" not in out

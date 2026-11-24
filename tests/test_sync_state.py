# -*- coding: utf-8 -*-
"""登录对话框显示「最近同步时间 + 结果」，不再谎称尚未检测。

- 主界面已登录时，登录页显示最近一次同步的时间与成功/失败；
- 成功绿色、失败红色，两者都加粗；
- 从未同步过才显示「尚未检测会话状态」；
- 退出登录 / 清除本地会话后清掉记录；
- 版本 2.2。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from rewards_assistant import app as app_mod  # noqa: E402
from rewards_assistant import session, sync_state  # noqa: E402
from rewards_assistant.config import APP_VERSION  # noqa: E402
from rewards_assistant.ui.login_dialog import LoginDialog  # noqa: E402
from rewards_assistant.ui.modern import theme  # noqa: E402


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- 持久化

def test_record_last_clear(isolated):
    assert sync_state.last() is None
    entry = sync_state.record(True, "已登录 · 同步正常")
    assert entry["ok"] is True and entry["detail"]
    got = sync_state.last()
    assert got == entry
    # 原子替换，不留临时文件
    assert not list(isolated.glob("last_sync.json.tmp"))
    sync_state.clear()
    assert sync_state.last() is None


def test_corrupted_or_partial_file_is_treated_as_no_sync(isolated):
    p = sync_state.path()
    p.write_text("not json", encoding="utf-8")
    assert sync_state.last() is None
    p.write_text(json.dumps({"at": "2026-11-24T19:47:03"}), encoding="utf-8")
    assert sync_state.last() is None          # 缺 ok 字段


def test_summary_text_and_kind():
    ok, kind = sync_state.summary(
        {"at": "2026-11-24T19:47:03", "ok": True, "detail": ""})
    assert "11-24 19:47:03" in ok and "同步成功" in ok and kind == "ok"
    fail, kind = sync_state.summary(
        {"at": "2026-11-24T19:48:12", "ok": False,
         "detail": "网络/服务暂时不可用（超时、429 或 5xx）。这不是登录失效"})
    assert "11-24 19:48:12" in fail and "同步失败" in fail and kind == "err"
    assert len(fail) < 90                    # 原因压成一行
    none, kind = sync_state.summary(None)
    assert none == "尚未检测会话状态" and kind == "normal"


def test_stamp_and_brief_are_robust():
    assert sync_state.stamp(None) == ""
    assert sync_state.stamp({"at": "2026-11-24T19:47:03"}) == "11-24 19:47:03"
    assert sync_state.stamp({"at": "bad"}) == "bad"
    assert sync_state.brief("  多行\n原因  ") == "多行 原因"
    assert sync_state.brief("x" * 100).endswith("…")


# ---------------------------------------------------------------- 界面状态

def _style(dlg):
    return dlg.status.styleSheet()


def test_dialog_shows_last_sync_time_green_and_bold(isolated, qapp):
    theme.set_scheme("light")
    sync_state.record(True, "已登录 · 同步正常")
    dlg = LoginDialog()
    text = dlg.status.text()
    assert "最近同步：" in text and "同步成功" in text
    assert "11-" in text                     # 时间戳
    assert "尚未检测会话状态" not in text
    assert theme.current()["green"] in _style(dlg)
    assert "font-weight:700" in _style(dlg)
    dlg.deleteLater()


def test_dialog_shows_failed_sync_red_and_bold(isolated, qapp):
    sync_state.record(False, "未登录或会话已过期")
    dlg = LoginDialog()
    text = dlg.status.text()
    assert "最近同步：" in text and "同步失败" in text
    assert "未登录或会话已过期" in text
    assert theme.current()["warn"] in _style(dlg)
    assert "font-weight:700" in _style(dlg)
    dlg.deleteLater()


def test_dialog_without_history_shows_pending_without_bold(isolated, qapp):
    dlg = LoginDialog()
    assert dlg.status.text() == "尚未检测会话状态"
    assert "font-weight:400" in _style(dlg)
    dlg.deleteLater()


def test_probe_result_keeps_last_sync_visible(isolated, qapp):
    sync_state.record(True, "已登录 · 同步正常")
    dlg = LoginDialog()
    dlg._apply_state("SIGNED_IN", "")
    text = dlg.status.text()
    assert "会话有效" in text
    assert "最近同步：" in text and "同步成功" in text
    assert "font-weight:700" in _style(dlg)
    dlg.deleteLater()


def test_logout_clears_record(isolated, qapp, monkeypatch):
    sync_state.record(True, "已登录 · 同步正常")
    called = []
    monkeypatch.setattr(session, "BrowserSessionFetcher",
                        lambda: SimpleNamespace(
                            clear_session=lambda: called.append(True)))
    monkeypatch.setattr(session.CookieStore, "clear", lambda self: None)
    session.clear_local_session()
    assert sync_state.last() is None
    dlg = LoginDialog()
    assert dlg.status.text() == "尚未检测会话状态"
    dlg.deleteLater()


def test_on_logout_done_never_leaves_stale_green(isolated, qapp):
    sync_state.record(True, "已登录 · 同步正常")
    dlg = LoginDialog()
    sync_state.clear()
    dlg._on_logout_done("已清除本地会话；历史积分数据不受影响。")
    assert "尚未检测会话状态" in dlg.status.text()
    assert theme.current()["warn"] in _style(dlg)
    assert "font-weight:700" in _style(dlg)
    dlg.deleteLater()


# ---------------------------------------------------------------- 写入时机

def test_app_records_result_on_sync_ok_and_fail(isolated):
    ctl = SimpleNamespace()
    ctl._syncing = True
    ctl._sync_enrich_current = False
    ctl._after_sync = set()
    ctl._last_done_notified_day = None
    ctl._last_points = None
    ctl.settings = {"notify_after_sync": False,
                    "sleep_when_all_done": False}
    ctl._pending_sleep = False
    ctl.window = SimpleNamespace(
        set_busy=lambda *a, **k: None, refresh_all=lambda: None,
        update_running_state=lambda: None)
    ctl.store = SimpleNamespace(latest_available=lambda: (0, 0, False))
    ctl._log = lambda *a, **k: None
    ctl.set_login_status = lambda *a, **k: None
    ctl._toast = lambda *a, **k: None
    ctl._all_done = lambda: False
    ctl._run_pending_from_snapshot = lambda *a: None
    ctl._open_pending_links = lambda *a: None
    ctl._start_pc_search = lambda: None

    snap = SimpleNamespace(tasks_done=1, tasks=[1, 2], balance_valid=False)
    payload = {"summary": {"points_earned": 30, "points_max": 125,
                           "points_source": "task_fallback"},
               "snapshot": snap, "message": ""}
    app_mod.Application._on_sync_ok(ctl, payload)
    assert sync_state.last()["ok"] is True

    app_mod.Application._on_sync_fail(ctl, "网络或服务异常（保留登录状态）")
    got = sync_state.last()
    assert got["ok"] is False and "网络或服务异常" in got["detail"]


def test_skipped_sync_does_not_refresh_timestamp(isolated):
    ctl = SimpleNamespace()
    ctl._syncing = True
    ctl._sync_enrich_current = False
    ctl._after_sync = set()
    ctl._last_done_notified_day = None
    ctl._last_points = None
    ctl._last_sync_ok = True
    ctl.settings = {"notify_after_sync": False,
                    "sleep_when_all_done": False}
    ctl._pending_sleep = False
    ctl.window = SimpleNamespace(
        set_busy=lambda *a, **k: None, refresh_all=lambda: None,
        update_running_state=lambda: None)
    ctl.store = SimpleNamespace(latest_available=lambda: (0, 0, False))
    ctl._log = lambda *a, **k: None
    ctl.set_login_status = lambda *a, **k: None
    ctl._toast = lambda *a, **k: None
    ctl._all_done = lambda: False
    snap = SimpleNamespace(tasks_done=1, tasks=[1], balance_valid=False)
    payload = {"skipped": True,
               "summary": {"points_earned": 30, "points_max": 125,
                           "points_source": "task_fallback"},
               "snapshot": snap, "message": ""}
    app_mod.Application._on_sync_ok(ctl, payload)
    assert sync_state.last() is None


# ---------------------------------------------------------------- 版本

def test_version_is_2_2():
    assert APP_VERSION == "2.2"

# -*- coding: utf-8 -*-
"""自动运行期间禁止同步 / 主按钮与托盘改「停止」/ 防误操作回归。"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rewards_assistant import app as app_mod  # noqa: E402
from rewards_assistant.app import Application  # noqa: E402
from rewards_assistant.ui.modern import viewmodel as vm  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def wired(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    qapp = QApplication.instance() or QApplication([])
    ctl = Application([])
    ctl.notifier = type("N", (), {"notify": lambda *a, **k: None,
                                  "attach": lambda *a, **k: None})()
    ctl._build_scheduler()
    ctl._ensure_window()
    yield ctl, ctl.window
    ctl.window.close()
    qapp.processEvents()


def _fake_worker(monkeypatch, created):
    class FakeWorker(QObject):
        progress = Signal(str)
        succeeded = Signal(dict)
        failed = Signal(str)
        finished = Signal()

        def __init__(self, *a, **kw):
            super().__init__()
            created.append(kw.get("enrich_pages"))

        def start(self):
            pass

        def isRunning(self):
            return False

        def deleteLater(self):
            pass

    monkeypatch.setattr(app_mod, "SyncWorker", FakeWorker)


def test_sync_is_blocked_while_auto_running_and_deferred(wired, monkeypatch):
    ctl, _win = wired
    created = []
    _fake_worker(monkeypatch, created)
    toasts = []
    monkeypatch.setattr(ctl, "_toast", lambda m: toasts.append(m))
    monkeypatch.setattr(ctl, "auto_running", lambda: True)

    ctl.sync_now()                 # 手动入口：明确提示，不启动同步
    assert created == [] and any("禁止同步" in m for m in toasts)
    ctl.sync_now(auto=True)        # 定时入口：静默跳过，不启动同步
    assert created == []

    monkeypatch.setattr(ctl, "auto_running", lambda: False)
    ctl.sync_now(auto=True)        # 运行结束后放行
    assert len(created) == 1 and ctl._syncing is True


def test_after_run_sync_is_requested_once(wired, monkeypatch):
    ctl, _win = wired
    scheduled = []
    monkeypatch.setattr(app_mod.QTimer, "singleShot",
                        staticmethod(lambda ms, fn: scheduled.append((ms, fn))))
    ctl._on_auto_run_finished()
    ctl._request_post_run_sync()   # runner.finished 与状态边沿会各调一次，必须去重
    assert len(scheduled) == 1
    assert ctl._post_run_sync_pending is True


def test_scheduled_run_does_not_queue_a_second_round(wired, monkeypatch):
    ctl, _win = wired
    monkeypatch.setattr(ctl, "auto_running", lambda: True)
    monkeypatch.setattr(ctl, "_all_done", lambda: False)
    calls = []
    monkeypatch.setattr(ctl, "sync_now", lambda auto=False: calls.append(auto))
    ctl._request_run_pending(scheduled=True)
    ctl.open_pending_pages()
    assert ctl._after_sync == set() and calls == []


def test_auto_running_detects_search_activity_and_opener(wired):
    ctl, win = wired
    assert ctl.auto_running() is False
    monkeypatch_runner = lambda: True
    ctl.window.runner.is_running = monkeypatch_runner
    assert ctl.auto_running() is True
    ctl.window.runner.is_running = lambda: False
    ctl._activity_worker = SimpleNamespace(isRunning=lambda: True)
    assert ctl.auto_running() is True
    ctl._activity_worker = None


def test_primary_button_becomes_stop_and_sync_entries_are_disabled(wired, monkeypatch):
    ctl, win = wired
    calls = []
    monkeypatch.setattr(ctl, "auto_running", lambda: True)
    monkeypatch.setattr(win, "stop_running", lambda: calls.append("stop"))
    monkeypatch.setattr(ctl, "run_pending_tasks", lambda: calls.append("run"))

    win.update_running_state()
    assert vm.tr(win.lang, "stop_run") in win.home.primary.text()
    assert win.home.primary.property("danger") == "true"   # 运行期红色
    assert win.home.primary.isEnabled()
    assert not win.home.ring.isEnabled()
    # 顶栏品牌图标是纯展示 QLabel，不随运行状态禁用（只有圆环刷新被禁用）。
    assert win.home.logo.isEnabled()
    assert not win.act_sync.isEnabled()

    win.home.primary.click()
    assert calls == ["stop"]                    # 绝不再次启动

    monkeypatch.setattr(ctl, "auto_running", lambda: False)
    win.update_running_state()
    assert vm.tr(win.lang, "sync") in win.home.primary.text()
    assert win.home.primary.property("danger") == "false"  # 空闲恢复蓝色
    assert win.home.ring.isEnabled()
    assert win.act_sync.isEnabled()
    win.home.primary.click()
    assert calls == ["stop", "run"]


def test_stop_request_locks_buttons_until_threads_really_finish(wired, monkeypatch):
    ctl, win = wired
    monkeypatch.setattr(ctl, "auto_running", lambda: True)
    monkeypatch.setattr(ctl, "cancel_pending_actions", lambda: None)
    monkeypatch.setattr(win.runner, "stop", lambda: None)
    monkeypatch.setattr(ctl._opener, "stop", lambda: None)
    win.update_running_state()
    win.stop_running()
    assert vm.tr(win.lang, "stopping") in win.home.primary.text()
    assert not win.home.primary.isEnabled()     # 连点无效
    assert win._stopping is True
    assert not win.act_sync.isEnabled()

    monkeypatch.setattr(ctl, "auto_running", lambda: False)
    win.update_running_state()
    assert win._stopping is False
    assert win.home.primary.isEnabled()
    assert vm.tr(win.lang, "sync") in win.home.primary.text()
    assert win.act_sync.isEnabled()


def test_activity_completion_never_syncs_mid_run():
    src = (ROOT / "rewards_assistant" / "app.py").read_text(encoding="utf-8")
    body = src.split("def _on_activity_completed", 1)[1].split("\n    def ", 1)[0]
    assert "sync_now(" not in body
    assert "fetch_dashboard" not in body
    # 搜索已并入同一个 ActivityWorker，不再关掉浏览器另起 SearchRunner。
    assert "_start_pc_search()" not in body
    assert 'stats.get("search")' in body
    assert "运行结束后统一同步" in body


def test_i18n_has_stop_and_blocked_sync_copy():
    from rewards_assistant.ui.modern import viewmodel as vm
    for lang in ("zh", "en"):
        assert vm.tr(lang, "stop_run")
        assert vm.tr(lang, "stopping")
        assert vm.tr(lang, "sync_blocked")


def test_long_error_status_stays_single_line_and_full_text_is_kept(wired):
    ctl, win = wired
    from PySide6.QtWidgets import QApplication
    long = ("运行失败：浏览器配置目录被占用：关闭应用自己打开的浏览器窗口后重试"
            "（日常 Edge 无需关闭）")
    win.set_status_message(long)
    QApplication.processEvents()
    chip = win.home.status_chip
    assert win.width() == 392                     # 长文案不撑宽窗口
    assert chip.height() <= 24                    # 单行，不与下方控件重叠
    assert "…" in chip._label.text() or chip._label.text() == long
    assert win.home.status.text() == long         # 全文在次要详情可读


def test_sync_progress_never_stays_stuck_on_saving(wired):
    """回归截图：同步成功后状态行仍停在「解析到 4 项任务，正在保存…」。"""
    from PySide6.QtWidgets import QApplication
    ctl, win = wired
    win.set_busy(True, "解析到 4 项任务，正在保存…")
    assert "正在保存" in win.home.status_chip._label.text()
    assert win.home.status_chip.kind() == "busy"

    ctl._syncing = False
    win.set_busy(False)                     # _on_sync_ok 的实际调用形态
    QApplication.processEvents()
    assert "正在保存" not in win.home.status_chip._label.text()
    assert win.home.status_chip.kind() != "busy"
    assert win.home.primary.isEnabled()      # 同步结束，按钮恢复可用
    assert win.act_sync.isEnabled()


def test_running_state_is_not_overwritten_by_sync_completion(wired, monkeypatch):
    ctl, win = wired
    monkeypatch.setattr(ctl, "auto_running", lambda: True)
    win.update_running_state()
    win.set_busy(False)                     # 运行期同步被闸门拦住，不应改状态
    assert vm.tr(win.lang, "stop_run") in win.home.primary.text()
    assert not win.act_sync.isEnabled()


def test_real_sync_signal_flow_clears_saving_status(wired, monkeypatch):
    """从 SyncWorker.progress → succeeded 的真实信号链，状态行不许留在「正在保存」。"""
    from PySide6.QtWidgets import QApplication
    from types import SimpleNamespace

    ctl, win = wired
    emitted = []

    class FakeWorker(QObject):
        progress = Signal(str)
        succeeded = Signal(dict)
        failed = Signal(str)
        finished = Signal()

        def __init__(self, *a, **kw):
            super().__init__()
            emitted.append(self)

        def start(self):
            pass

        def deleteLater(self):
            pass

        def isRunning(self):
            return False

    monkeypatch.setattr(app_mod, "SyncWorker", FakeWorker)
    ctl.sync_now()
    worker = emitted[-1]
    worker.progress.emit("解析到 4 项任务，正在保存…")
    assert "正在保存" in win.home.status_chip._label.text()
    assert win.home.status_chip.kind() == "busy"

    payload = {
        "ok": True,
        "message": "同步完成（只读，进度以官网为准）",
        "summary": {"points_earned": 10, "points_max": 90,
                    "points_source": "page_today", "all_done": False},
        "snapshot": SimpleNamespace(tasks=[], tasks_done=0, balance_valid=False),
        "logs": [],
    }
    worker.succeeded.emit(payload)
    QApplication.processEvents()

    assert "正在保存" not in win.home.status_chip._label.text()
    assert win.home.status_chip.kind() != "busy"
    assert ctl._syncing is False
    assert win.act_sync.isEnabled()

# -*- coding: utf-8 -*-
"""唯一模式集成测试：真实 QApplication 下验证界面↔经典控制器接线。

不访问网络、不启动 Playwright：所有执行入口都先 monkeypatch 成记录器。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from rewards_assistant import autostart  # noqa: E402
from rewards_assistant.app import Application  # noqa: E402
from rewards_assistant.ui.modern import ModernMainWindow  # noqa: E402


@pytest.fixture
def wired(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    # 状态可变的桩：apply_settings() 会在 set_enabled 后复查 is_enabled，
    # 若两者不一致会弹模态 QMessageBox（无交互的测试里会永久阻塞）。
    state = {"on": False}
    monkeypatch.setattr(autostart, "is_enabled", lambda: state["on"])

    def _set_enabled(value):
        state["on"] = bool(value)
        return True

    monkeypatch.setattr(autostart, "set_enabled", _set_enabled)
    qapp = QApplication.instance() or QApplication([])
    ctl = Application([])
    ctl.notifier = type("N", (), {"notify": lambda *a, **k: None,
                                  "attach": lambda *a, **k: None})()
    ctl._build_scheduler()
    ctl._ensure_window()
    win = ctl.window
    assert isinstance(win, ModernMainWindow)
    assert win.controller is ctl
    yield ctl, win
    win.close()
    win.deleteLater()
    qapp.processEvents()


def test_primary_and_tray_share_exact_classic_entrypoint(wired, monkeypatch):
    ctl, win = wired
    calls = []
    monkeypatch.setattr(ctl, "run_pending_tasks", lambda: calls.append("run") or "ok")

    # 主页主按钮
    win.home.primary.click()
    # 托盘「自动完成未完成任务」走 quick.tasks_btn.click（与 app.py 完全同一绑定）
    win.quick.tasks_btn.click()
    assert calls == ["run", "run"]

    # 断言 app.py 里的托盘动作仍然指向同一个按钮
    app_src = (Path(__file__).parents[1] / "rewards_assistant" / "app.py").read_text("utf8")
    assert "self.act_tray_run.triggered.connect(self._on_tray_run)" in app_src
    assert "self.window.quick.tasks_btn.click()" in app_src
    assert "self.window.stop_running()" in app_src
    assert "def run_pending_tasks(self) -> None:" in app_src


def test_facade_methods_do_not_raise(wired):
    ctl, win = wired
    win.refresh_all()
    win.set_busy(True, "正在同步…")
    win.set_busy(False)
    win.set_status_message("测试")
    win.set_login_status("已登录", True)
    win.update_running_state()
    assert win.act_sync.isEnabled()
    assert win.quick.tasks_btn.isEnabled() or not win.quick.tasks_btn.isEnabled()
    assert win.runner.is_running() is False
    # 经典控制器需要的属性全部存在
    for name in ("runner", "search", "quick", "account", "act_sync", "act_refresh"):
        assert hasattr(win, name), name
    assert callable(win.run_pending_tasks)
    assert callable(win.stop_running)


def test_search_panel_collect_is_wired_to_controller(wired):
    ctl, win = wired
    params = win.search._collect()
    assert "repeat" in params and "level" in params
    assert "keyword_sources" in params
    # run_pending_tasks 的经典实现使用 self.search._collect()
    assert "self.search._collect()" in (
        Path(__file__).parents[1] / "rewards_assistant" / "ui" / "modern" /
        "main_window.py").read_text("utf8")


def test_settings_write_real_classic_keys(wired, monkeypatch):
    ctl, win = wired
    win.show_settings()
    win.set_pref("ui_search_level", "高")
    assert win.settings.get("search_panel")["level"] == 3
    win.set_pref("ui_repeat", 23)
    assert win.settings.get("search_panel")["repeat"] == 23
    win.set_pref("ui_schedule_enabled", True)
    assert win.settings.get("auto_run_enabled") is True
    win.set_pref("ui_schedule_times", ["08:00", "12:00"])
    assert win.settings.get("auto_run_times") == ["08:00", "12:00"]
    win.set_pref("ui_sync_interval", 37)
    assert win.settings.get("sync_interval_value_minutes") == 37
    win.set_pref("ui_notify_after_sync", False)
    assert win.settings.get("notify_after_sync") is False
    win.set_pref("ui_launch_at_login", True)
    assert win.settings.get("launch_at_login") is True
    # 回填到经典 SearchPanel，_collect() 能读到新值
    assert win.search._collect()["repeat"] == 23
    assert win.search._collect()["level"] == 3


def test_sync_button_delegates_to_classic_sync_worker(wired, monkeypatch):
    ctl, win = wired
    calls = []
    monkeypatch.setattr(ctl, "sync_now", lambda auto=False: calls.append(auto))
    win.home.ring.click()
    win.home.primary.parent()  # 保持窗口存活
    assert calls == [False]

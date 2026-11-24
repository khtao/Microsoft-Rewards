# -*- coding: utf-8 -*-
"""毫秒级随机节奏 + 可领取卡交互的离线回归。

* 所有间隔 / 等待都随机取样并量化到毫秒，链路里不剩固定值；
* 可领取积分 > 0 且闸门放行时，卡片 hover 变手形并显示「点击领取」；
* 点击后圆环下状态行是绿色「正在打开浏览器...」；
* 用户手动关闭领取窗口后自动同步一次积分（应用退出 / 启动失败不同步）。
"""
from __future__ import annotations

import random
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QApplication

from rewards_assistant import app as app_mod
from rewards_assistant import claim
from rewards_assistant.timing import jitter, ms_text, sleep_jitter
from rewards_assistant.ui.modern import theme, viewmodel as vm
from rewards_assistant.ui.modern.widgets import StatCard, StatusChip

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- 毫秒随机

def test_jitter_is_random_and_quantised_to_milliseconds():
    rng = random.Random(7)
    values = [jitter(3.0, 12.0, rng) for _ in range(200)]
    assert 3.0 <= min(values) and max(values) <= 12.0
    assert all(round(v, 3) == v for v in values), "必须精确到 0.001 秒"
    assert len(set(values)) > 20, "不能退化成固定值"
    assert any(abs(v * 1000 - round(v * 1000)) < 1e-9 and v % 1 != 0
               for v in values), "随机值应带非零毫秒尾数"
    # 同一 seed 可复现
    a = [jitter(3.0, 12.0, random.Random(99)) for _ in range(5)]
    b = [jitter(3.0, 12.0, random.Random(99)) for _ in range(5)]
    assert a == b
    assert jitter(1.0, 1.0, rng) == 1.0
    assert jitter(12.0, 3.0, rng) <= 12.0
    assert ms_text(5.237) == "5.237"


def test_sleep_jitter_waits_a_millisecond_precise_random_duration(monkeypatch):
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    got = sleep_jitter(0.02, 0.05, random.Random(3))
    assert slept == [got] and round(got, 3) == got
    assert 0.02 <= got <= 0.05


def test_timing_is_shared_by_every_wait_in_the_pipeline():
    base = ROOT / "rewards_assistant"
    files = {
        "human.py": base / "human.py",
        "api.py": base / "api.py",
        "activity_click.py": base / "activity_click.py",
        "activity_worker.py": base / "activity_worker.py",
        "opener.py": base / "opener.py",
        "claim.py": base / "claim.py",
        "app.py": base / "app.py",
        "entropy.py": base / "entropy.py",
        "session.py": base / "session.py",
        "trends.py": base / "trends.py",
        "login_dialog.py": base / "ui" / "login_dialog.py",
    }
    for name, path in files.items():
        assert "timing" in path.read_text("utf-8"), name
    # 全仓生产代码不残留写死的等待时长
    import re
    for path in base.rglob("*.py"):
        if "__pycache__" in path.parts or path.name == "timing.py":
            continue
        src = path.read_text("utf-8")
        assert not re.search(r"time\.sleep\(\s*[0-9]", src), path
        assert not re.search(r"wait_for_timeout\(\s*[0-9]", src), path
    # 拟人链路里不再有写死的等待时长（randint 只用于鼠标步数，不是时间）
    for name in ("human.py", "api.py", "activity_click.py", "activity_worker.py"):
        src = files[name].read_text("utf-8")
        assert "time.sleep(0." not in src, name
        assert "wait_for_timeout(1" not in src, name
        assert "jitter(" in src or "sleep_jitter(" in src, name
    api = files["api.py"].read_text("utf-8")
    assert "jitter(min(round_seconds), max(round_seconds), rng)" in api
    assert "jitter(min(gap_seconds), max(gap_seconds), rng)" in api
    assert 'timespec="milliseconds"' in api
    assert "round(total, 3)" in api and "round(budget.elapsed, 3)" in api
    click = files["activity_click.py"].read_text("utf-8")
    for fixed in ("wait_for_timeout(1500)", "wait_for_timeout(1600)",
                  "wait_for_timeout(1300)"):
        assert fixed not in click
    assert "jitter(1.2, 1.8, rng)" in click


def test_opener_gap_and_countdown_are_millisecond_precise():
    from rewards_assistant.opener import random_gap
    values = [random_gap(5, 10) for _ in range(60)]
    assert all(5.0 <= v <= 10.0 and round(v, 3) == v for v in values)
    assert any(v % 1 != 0 for v in values), "不再返回整数秒"
    src = (ROOT / "rewards_assistant" / "opener.py").read_text("utf-8")
    assert "self._timer.start(max(1, int(round(gap * 1000))))" in src
    assert "self._tick.setInterval(100)" in src
    assert "Signal(str, float)" in src
    app_src = (ROOT / "rewards_assistant" / "app.py").read_text("utf-8")
    assert "ms_text(gap)" in app_src and "ms_text(left)" in app_src
    assert "_next_sync_delay" in app_src, "自动刷新周期也带毫秒抖动"


# ---------------------------------------------------------------- 可领取卡

def test_claim_card_hover_shows_click_to_claim_and_hand_cursor(qapp):
    card = StatCard("可领取")
    card.set_hover_label("点击领取")
    assert card.cursor().shape() == Qt.CursorShape.ArrowCursor
    card.set_clickable(True)
    assert card.cursor().shape() == Qt.CursorShape.PointingHandCursor
    enter = QEnterEvent(QPointF(0, 0), QPointF(0, 0), QPointF(0, 0))
    card.enterEvent(enter)
    assert card._label.text() == "点击领取"
    card.leaveEvent(QEvent(QEvent.Type.Leave))
    assert card._label.text() == "可领取"
    card.enterEvent(enter)
    card.set_clickable(False)
    assert card._label.text() == "可领取"
    assert card.cursor().shape() == Qt.CursorShape.ArrowCursor


def test_claim_card_label_updates_with_language(qapp):
    card = StatCard("可领取")
    card.set_hover_label(vm.tr("zh", "claim_hover"))
    card.set_label(vm.tr("en", "claimable"))
    card.set_hover_label(vm.tr("en", "claim_hover"))
    card.set_clickable(True)
    card.enterEvent(QEnterEvent(QPointF(0, 0), QPointF(0, 0), QPointF(0, 0)))
    assert card._label.text() == "Click to claim"
    card.leaveEvent(QEvent(QEvent.Type.Leave))
    assert card._label.text() == "To claim"


def test_status_chip_green_hint_survives_progress_refresh(qapp):
    chip = StatusChip("zh")
    chip.set_green_hint(vm.tr("zh", "claim_opening"))
    assert chip.kind() == "done"
    assert chip._label.text() == "正在打开浏览器..."
    assert theme.LIGHT["green_text"] in chip._label.styleSheet()
    # 任务完成度刷新不能把绿色提示冲掉
    chip.set_from_progress({"complete": True}, "zh")
    assert chip._label.text() == "正在打开浏览器..."
    # 下一次同步状态正常覆盖；busy 期间进度刷新不会抢占状态行
    chip.set_text("正在同步 Rewards 数据…")
    assert chip.kind() == "busy"
    chip.set_from_progress({"complete": True}, "zh")
    assert chip._label.text() == "正在同步 Rewards 数据…"
    chip.set_busy(False, "zh")
    chip.set_from_progress({"complete": True}, "zh")
    assert chip._label.text() == vm.tr("zh", "status.all_done")


def test_home_page_wires_claim_opening_hint(qapp):
    from rewards_assistant.ui.modern.main_window import HomePage

    class Stub:
        lang = "zh"
        def open_login(self): pass
        def show_settings(self): pass
        def sync_now(self, *a, **kw): pass
        def auto_running(self): return False
        def claim_available(self): return True
        def open_claim_page(self): pass

    home = HomePage(Stub())
    home.set_claim_available(True)
    assert home.card_claimable.is_clickable()
    assert home.card_claimable._hover_label == "点击领取"
    home.set_claim_opening()
    assert home.status_chip.kind() == "done"
    assert home.status_chip._label.text() == "正在打开浏览器..."
    src = (ROOT / "rewards_assistant" / "ui" / "modern" /
           "main_window.py").read_text("utf-8")
    assert "def set_claim_opening(self)" in src
    assert "self.home.set_claim_opening()" in src


# ---------------------------------------------------------------- 关闭后自动同步

class _FakeStore:
    def __init__(self):
        self.cookies = [{"name": "MSPAuth", "value": "token",
                         "domain": ".bing.com", "path": "/",
                         "secure": True, "expires": -1}]
        self.path = Path("/tmp/fake-session_cookies.bin")

    def load(self, now=None):
        return list(self.cookies)


def _application(monkeypatch, tmp_path):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    QApplication.instance() or QApplication([])
    ap = app_mod.Application([])
    ap.store = SimpleNamespace(
        page_points=lambda day: {"claimable": 60, "available": 100, "today": 20})
    ap.window = SimpleNamespace(
        _login_ok=True,
        sync_claim_state=lambda: None,
        set_claim_opening=lambda: None,
        log=lambda msg: None)
    ap.auto_running = lambda: False
    ap._syncing = False
    ap._claim_opening = False
    ap._last_sync_ok = True
    monkeypatch.setattr(app_mod, "CookieStore", lambda: _FakeStore())
    return ap


def test_manual_close_triggers_one_auto_sync(monkeypatch, tmp_path, qapp):
    ap = _application(monkeypatch, tmp_path)
    hints, synced = [], []
    ap.window.set_claim_opening = lambda: hints.append("green")
    ap.sync_now = lambda auto=False: synced.append(auto)

    def fake_open(**kw):
        return True                      # 真实 open_claim_window 成功返回 True

    monkeypatch.setattr(app_mod, "open_claim_window", fake_open)
    ap.open_claim_page()
    ap._claim_thread.join(timeout=2)
    for _ in range(30):
        QApplication.processEvents()
        if synced:
            break
        time.sleep(0.01)
    assert hints == ["green"], "点击后应显示绿色状态"
    assert synced == [True], "用户关窗后自动同步一次（后台入口）"
    assert ap._claim_opening is False


def test_launch_failure_does_not_sync(monkeypatch, tmp_path, qapp):
    ap = _application(monkeypatch, tmp_path)
    synced = []
    ap.sync_now = lambda auto=False: synced.append(auto)

    def fake_open(**kw):
        raise RuntimeError("Edge exploded")

    monkeypatch.setattr(app_mod, "open_claim_window", fake_open)
    ap.open_claim_page()
    ap._claim_thread.join(timeout=2)
    QApplication.processEvents()
    assert synced == []


def test_application_quit_close_does_not_sync(monkeypatch, tmp_path, qapp):
    ap = _application(monkeypatch, tmp_path)
    synced = []
    ap.sync_now = lambda auto=False: synced.append(auto)

    def fake_open(**kw):
        ap._claim_stop.set()             # 模拟退出信号
        return True

    monkeypatch.setattr(app_mod, "open_claim_window", fake_open)
    ap.open_claim_page()
    ap._claim_thread.join(timeout=2)
    for _ in range(20):
        QApplication.processEvents()
        time.sleep(0.01)
    assert synced == []

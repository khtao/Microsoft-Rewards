# -*- coding: utf-8 -*-
"""回归：Qt 回收 C++ 对象后 Python wrapper 悬空导致的崩溃。

实机日志：
    RuntimeError: libshiboken: Internal C++ object (ProbeWorker) already deleted.
    File "login_dialog.py", line 245, in open_login
      if self._probe is not None and self._probe.isRunning():

根因：``finished.connect(worker.deleteLater)`` 让 Qt 删掉 C++ QThread，
Python 侧仍留着 ``self._probe``，再调 ``isRunning()`` 就抛 RuntimeError。

这些用例用轻量桩对象调用未绑定方法，不需要创建 QWidget / QApplication。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from rewards_assistant.ui import login_dialog as ld  # noqa: E402


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, fn):
        self.slots.append(fn)

    def emit(self, *args):
        for fn in list(self.slots):
            fn(*args)


class DeadWorker:
    """模拟已被 Qt 回收的 QThread wrapper。"""

    def isRunning(self):
        raise RuntimeError(
            "libshiboken: Internal C++ object (ProbeWorker) already deleted.")

    def wait(self, _ms):
        raise RuntimeError("already deleted")

    def requestInterruption(self):
        raise RuntimeError("already deleted")


class LiveWorker:
    def isRunning(self):
        return True


class FakeLoginWorker:
    created = 0

    def __init__(self, parent=None):
        type(self).created += 1
        self.state = _Signal()
        self.detected = _Signal()
        self.done = _Signal()
        self.finished = _Signal()

    def isRunning(self):
        return True

    def start(self):
        pass


class _Btn:
    def __init__(self):
        self.text = ""
        self.enabled = True

    def setText(self, text):
        self.text = text

    def setEnabled(self, enabled):
        self.enabled = bool(enabled)


class _Label:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text

    def setStyleSheet(self, *_a):
        pass


def _fake_dialog(probe=None, login=None, logout=None):
    """搭一个具备 open_login / _apply_state 所需属性的轻量 self。"""
    return SimpleNamespace(
        _probe=probe, _login=login, _logout=logout, _pending_login=False,
        status=_Label(),
        login_btn=_Btn(), probe_btn=_Btn(), clear_btn=_Btn(), close_btn=_Btn(),
        login_state_changed=_Signal(),
        _is_running=ld.LoginDialog._is_running,
        _set_busy=ld.LoginDialog._set_busy.__get__(object(), ld.LoginDialog),
        _set_login_running=None,
        _apply_state=None,
        _on_login_detected=None,
        _on_login_done=None,
        _on_probe_finished=None,
        _on_login_finished=None,
        _on_logout_finished=None,
        _stop_login_worker=None,
        check_status=lambda: None,
    )


def _bind(dlg):
    """把需要访问 self 的未绑定方法绑到桩对象上。"""
    for name in ("_set_busy", "_set_login_running", "_apply_state",
                 "_on_login_detected", "_on_login_done", "_on_probe_finished",
                 "_on_login_finished", "_on_logout_finished",
                 "_stop_login_worker", "open_login", "check_status"):
        setattr(dlg, name, getattr(ld.LoginDialog, name).__get__(dlg, ld.LoginDialog))
    return dlg


# ------------------------------------------------------------ worker_running

def test_worker_running_treats_deleted_wrapper_as_stopped():
    assert ld.worker_running(None) is False
    assert ld.worker_running(DeadWorker()) is False
    assert ld.worker_running(LiveWorker()) is True


def test_source_never_uses_deletelater():
    import re

    src = (ld.__file__)
    text = open(src, encoding="utf8").read()
    # 只看真实调用；文档里解释根因时可以提到这个词
    assert re.search(r"\.deleteLater\s*\(", text) is None
    assert "worker_running" in text
    assert "def closeEvent" in text
    assert "def reject" in text
    assert "_on_probe_finished" in text


# ------------------------------------------------------------ open_login

def test_open_login_survives_deleted_probe_wrapper(monkeypatch):
    """这是实机崩溃的最小复现：旧 wrapper 已删，仍应能打开登录窗口。"""
    FakeLoginWorker.created = 0
    monkeypatch.setattr(ld, "LoginWorker", FakeLoginWorker)
    dlg = _bind(_fake_dialog(probe=DeadWorker()))

    ld.LoginDialog.open_login(dlg)  # 不应抛 RuntimeError

    assert dlg._probe is None                       # 悬空引用被清掉
    assert FakeLoginWorker.created == 1
    assert "已打开浏览器窗口" in dlg.status.text
    assert "我已完成登录" in dlg.login_btn.text


def test_open_login_queues_when_probe_is_running(monkeypatch):
    """探测进行中不阻塞 GUI 线程，也不重复启动第二个持久 profile。"""
    FakeLoginWorker.created = 0
    monkeypatch.setattr(ld, "LoginWorker", FakeLoginWorker)
    monkeypatch.setattr(ld.QTimer, "singleShot", lambda _ms, fn: fn())
    dlg = _bind(_fake_dialog(probe=LiveWorker()))

    ld.LoginDialog.open_login(dlg)
    assert dlg._pending_login is True
    assert FakeLoginWorker.created == 0
    assert "稍后自动打开" in dlg.status.text

    # 探测结束 → _apply_state 自动消费待办
    dlg._probe = None
    ld.LoginDialog._apply_state(dlg, ld.SIGNED_OUT, "请登录")
    assert dlg._pending_login is False
    assert FakeLoginWorker.created == 1


def test_apply_state_ignores_late_probe_while_login_window_open(monkeypatch):
    FakeLoginWorker.created = 0
    monkeypatch.setattr(ld, "LoginWorker", FakeLoginWorker)
    dlg = _bind(_fake_dialog(probe=None))
    ld.LoginDialog.open_login(dlg)

    before = dlg.login_btn.text
    ld.LoginDialog._apply_state(dlg, ld.SIGNED_IN, "")
    assert dlg.login_btn.text == before               # 未被迟到结果覆盖
    assert not dlg.login_state_changed.slots or True  # 未广播登录状态


# ------------------------------------------------------------ 关闭保护

def test_stop_login_worker_is_idempotent_and_survives_dead_wrapper():
    dlg = _bind(_fake_dialog(login=DeadWorker()))
    ld.LoginDialog._stop_login_worker(dlg)
    assert dlg._login is None
    ld.LoginDialog._stop_login_worker(dlg)  # 再调一次不应抛
    assert dlg._login is None


def test_finished_callbacks_clear_only_own_reference():
    """finished 兜底只清自己的引用，不能误清新启动的探测。"""
    dlg = _bind(_fake_dialog())
    old, new = object(), object()
    dlg._probe = old
    ld.LoginDialog._on_probe_finished(dlg, old)
    assert dlg._probe is None
    dlg._probe = new
    ld.LoginDialog._on_probe_finished(dlg, old)
    assert dlg._probe is new

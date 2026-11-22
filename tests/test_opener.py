"""队列打开成功、失败、停止的回归测试（无 Qt 环境也可运行）。"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture
def opener_module(monkeypatch):
    """使用最小 Qt 信号/定时器替身来检查纯业务调度和信号声明。"""
    class BoundSignal:
        def __init__(self):
            self.slots = []

        def connect(self, callback):
            self.slots.append(callback)

        def emit(self, *args):
            for callback in tuple(self.slots):
                callback(*args)

    class Signal:
        def __init__(self, *_types):
            self.name = None

        def __set_name__(self, owner, name):
            self.name = name

        def __get__(self, instance, owner):
            if instance is None:
                return self
            return instance.__dict__.setdefault(self.name, BoundSignal())

    class QObject:
        def __init__(self, parent=None):
            self.parent = parent

    class QTimer:
        def __init__(self, parent=None):
            self.timeout = BoundSignal()
            self.active = False

        def setSingleShot(self, value):
            pass

        def setInterval(self, value):
            pass

        def start(self, *args):
            self.active = True

        def stop(self):
            self.active = False

        def deleteLater(self):
            self.active = False

    qt = types.ModuleType("PySide6")
    core = types.ModuleType("PySide6.QtCore")
    core.QObject = QObject
    core.QTimer = QTimer
    core.Signal = Signal
    monkeypatch.setitem(sys.modules, "PySide6", qt)
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", core)
    path = Path(__file__).resolve().parents[1] / "rewards_assistant" / "opener.py"
    spec = importlib.util.spec_from_file_location("_opener_isolated", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_failed_url_emits_failed_then_finishes_without_startup_crash(opener_module):
    op = opener_module.PendingOpener(lambda: 5, lambda _url: False)
    failed = []
    completed = []
    op.failed.connect(lambda name, url: failed.append((name, url)))
    op.finished.connect(completed.append)
    op.start([("坏链接", "https://example.invalid")])
    assert failed == [("坏链接", "https://example.invalid")]
    assert completed == [0]
    assert op.remaining() == 0
    assert not op.running


def test_partial_failures_do_not_increment_open_count(opener_module):
    op = opener_module.PendingOpener(lambda: 5, lambda url: url.endswith("good"))
    opened, failed, completed = [], [], []
    op.opened.connect(lambda *args: opened.append(args))
    op.failed.connect(lambda *args: failed.append(args))
    op.finished.connect(completed.append)
    op.start([("坏任务", "https://example.com/bad"),
              ("好任务", "https://example.com/good")])
    assert op.running and op.remaining() == 1
    op._open_one()  # 模拟定时器触发第二个任务
    assert failed == [("坏任务", "https://example.com/bad")]
    assert opened == [("好任务", 1, 2)]
    assert completed == [1]
    assert not op.running


def test_exception_from_system_browser_is_reported(opener_module):
    def failing(_url):
        raise OSError("xdg-open missing")

    op = opener_module.PendingOpener(lambda: 1, failing)
    errors = []
    op.failed.connect(lambda *args: errors.append(args))
    op.start([("任务", "https://example.com")])
    assert errors == [("任务", "https://example.com")]


def test_stopping_queue_cancels_next_page(opener_module):
    opened_urls = []
    op = opener_module.PendingOpener(lambda: 3, lambda url: opened_urls.append(url) or True)
    stopped = []
    op.stopped.connect(stopped.append)
    op.start([("1", "https://example.com/1"), ("2", "https://example.com/2")])
    op.stop()
    assert opened_urls == ["https://example.com/1"]
    assert stopped == [1]
    assert not op.running

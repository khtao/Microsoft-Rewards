"""逐个打开任务页的队列调度器。

为什么不用 for 循环一把梭：一次弹出 5~10 个标签页既打扰人，行为上也明显不像
正常浏览。这里按**随机间隔**一个一个开，节奏接近人手动点开的情形。

本模块只负责"打开网址"与"控制节奏"，不注入脚本、不模拟键鼠、不自动完成任何任务。
"""
from __future__ import annotations

import random
from typing import Callable, List, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, QTimer, Signal


class PendingOpener(QObject):
    """按随机间隔依次打开一组链接。

    gap_fn  : () -> int，返回下一次打开的间隔秒数
    open_fn : (url) -> bool，真正打开网页的函数
    """

    opened = Signal(str, int, int)    # 任务名, 第几个(从1开始), 总数
    failed = Signal(str, str)        # 任务名, 无法打开的 URL
    waiting = Signal(str, int)        # 下一个任务名, 间隔秒数
    countdown = Signal(str, int, int, int)  # 下一个任务名, 剩余秒, 已打开, 总数
    finished = Signal(int)            # 总数
    stopped = Signal(int)             # 已打开数

    def __init__(self, gap_fn: Callable[[], int], open_fn: Callable[[str], bool],
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._gap_fn = gap_fn
        self._open_fn = open_fn
        self._items: List[Tuple[str, str]] = []
        self._index = 0
        self._opened_count = 0
        self._timer: Optional[QTimer] = None
        self._tick: Optional[QTimer] = None
        self._left = 0

    # ------------------------------------------------------------------ 状态

    @property
    def running(self) -> bool:
        return bool(self._items) and self._index < len(self._items)

    @property
    def total(self) -> int:
        return len(self._items)

    @property
    def opened_count(self) -> int:
        return self._opened_count

    def remaining(self) -> int:
        return max(0, len(self._items) - self._index)

    # ------------------------------------------------------------------ 控制

    def start(self, items: Sequence[Tuple[str, str]]) -> None:
        """items 为 [(任务名, 链接)]。第一个立即打开，其余按随机间隔。"""
        self.stop()
        self._items = [(str(n), str(u)) for n, u in items if u]
        self._index = 0
        self._opened_count = 0
        if not self._items:
            self.finished.emit(0)
            return
        self._open_one()

    def stop(self) -> None:
        """停止后续打开；已打开的保持不动。"""
        for timer_attr in ("_timer", "_tick"):
            timer = getattr(self, timer_attr, None)
            if timer is not None:
                timer.stop()
                timer.deleteLater()
                setattr(self, timer_attr, None)
        was_active = bool(self._items)
        count = self._opened_count
        self._items = []
        self._index = 0
        self._opened_count = 0
        if was_active:
            self.stopped.emit(count)

    # ------------------------------------------------------------------ 内部

    def _open_one(self) -> None:
        # 到点了，先停掉倒计时刷新（主定时器由 timeout 自然结束，也要清掉引用）
        if self._tick is not None:
            self._tick.stop()
            self._tick.deleteLater()
            self._tick = None
        if self._timer is not None:
            self._timer.deleteLater()
            self._timer = None

        if self._index >= len(self._items):
            opened = self._opened_count
            self._items = []
            self._index = 0
            self._opened_count = 0
            self.finished.emit(opened)
            return

        name, url = self._items[self._index]
        try:
            opened = bool(self._open_fn(url))
        except Exception:
            opened = False
        self._index += 1
        if opened:
            self._opened_count += 1
            self.opened.emit(name, self._opened_count, len(self._items))
        else:
            self.failed.emit(name, url)

        if self._index >= len(self._items):
            opened = self._opened_count
            self._items = []
            self._index = 0
            self._opened_count = 0
            self.finished.emit(opened)
            return

        gap = max(1, int(self._gap_fn()))
        self.waiting.emit(self._items[self._index][0], gap)
        self._start_countdown(gap)

    def _start_countdown(self, gap: int) -> None:
        """起两个定时器：一个负责到点打开，另一个每秒刷新倒计时。"""
        self._left = gap
        self._emit_countdown()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._open_one)
        self._timer.start(gap * 1000)

        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()

    def _on_tick(self) -> None:
        self._left = max(0, self._left - 1)
        self._emit_countdown()

    def _emit_countdown(self) -> None:
        if self._index < len(self._items):
            self.countdown.emit(self._items[self._index][0], self._left,
                                self._opened_count, len(self._items))


def random_gap(lo: int = 5, hi: int = 10) -> int:
    """返回 [lo, hi] 区间内的随机秒数（默认 5~10 秒）。"""
    if hi < lo:
        lo, hi = hi, lo
    return random.randint(max(1, lo), max(1, hi))

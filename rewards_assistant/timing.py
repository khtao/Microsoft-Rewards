# -*- coding: utf-8 -*-
"""拟人节奏：所有间隔 / 等待都随机取样，并量化到毫秒（0.001 秒）。

自动完成任务链路上没有任何固定等待：搜索轮时长、轮间隔、活动卡片点击
间隔、任务页打开间隔、鼠标动作停顿、浏览器窗口轮询，都经 ``jitter``
取一个 [lo, hi] 内的随机秒数，保留三位小数。同一条链路可传入同一个
``random.Random`` 实例，保证 seed 可复现。
"""
from __future__ import annotations

import random
import time
from typing import Any

MS = 0.001


def jitter(lo: float, hi: float, rng: Any = None) -> float:
    """返回 [lo, hi] 内的随机秒数，精确到毫秒。

    rng 可以是 ``random`` 模块或 ``random.Random`` 实例；不传则用全局随机源。
    量化到三位小数后仍夹在区间内，避免 round 把端点推出范围。
    """
    r = rng if rng is not None else random
    low, high = sorted((float(lo), float(hi)))
    if high - low < MS:
        return round(low, 3)
    value = round(r.uniform(low, high), 3)
    return round(min(high, max(low, value)), 3)


def sleep_jitter(lo: float, hi: float, rng: Any = None) -> float:
    """随机等待一段毫秒精度的时间，返回实际等待秒数（便于记日志）。"""
    seconds = jitter(lo, hi, rng)
    if seconds > 0:
        time.sleep(seconds)
    return seconds


def ms_text(seconds: float) -> str:
    """日志 / 界面里统一用三位小数，不把随机值显示成整数秒。"""
    return f"{float(seconds):.3f}"

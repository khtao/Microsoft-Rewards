# -*- coding: utf-8 -*-
"""每日积分口径：页面显示的「今日积分」优先，其余沿用原有回退规则。

优先级
------
1. ``page_today``：/dashboard、/earn 页面上用户真正看到的今日积分（真值）；
2. ``balance_delta``：今日账户余额 − 昨日余额（**净变化**，兑换扣分时为负）；
3. ``task_fallback``：缺昨日快照时的任务积分合计（估算，可能漏掉周任务/额外奖励）。

余额日差和任务合计都不是「今日已赚取积分」本身，只是过去缺数据时的近似。
新同步链路把页面值写进 ``daily.today_points``，这里读取它作为第一优先级，
并且保持 ``calculate_daily_points(a, b, c)`` 三参数调用的行为完全不变
（历史测试与老库迁移都依赖它）。
"""
from __future__ import annotations

from typing import Optional

BALANCE_DELTA = "balance_delta"
TASK_FALLBACK = "task_fallback"
PAGE_TODAY = "page_today"          # 页面显示的今日积分（只读同步写入）

ALL_SOURCES = (BALANCE_DELTA, TASK_FALLBACK, PAGE_TODAY)


def calculate_daily_points(today_balance: Optional[int], yesterday_balance: Optional[int],
                           task_points: int,
                           page_today: Optional[int] = None) -> tuple[int, str, Optional[int]]:
    """返回 (积分, 来源, 昨日基准余额)。0 是有效快照，不等于缺失。"""
    if page_today is not None:
        return int(page_today), PAGE_TODAY, None
    if today_balance is not None and yesterday_balance is not None:
        return int(today_balance) - int(yesterday_balance), BALANCE_DELTA, int(yesterday_balance)
    return int(task_points), TASK_FALLBACK, None


def points_source_label(source: str) -> str:
    if source == BALANCE_DELTA:
        return "账户余额日差"
    if source == PAGE_TODAY:
        return "页面显示的今日积分"
    return "任务积分合计（回退）"


def points_display(source: str, points: int) -> str:
    if source == BALANCE_DELTA:
        return f"今日余额净变化 {points:+d} 分"
    if source == PAGE_TODAY:
        return f"今日积分 {points} 分（页面显示）"
    return f"今日任务积分 {points} 分（无昨日余额）"

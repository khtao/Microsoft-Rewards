"""每日积分的唯一计算规则：今日账户余额 - 昨日账户余额。

仅当两天都有真实的账户余额快照时采用差值；否则使用本日任务积分和。
余额变化是净变化：兑换奖品/扣分时可能为负，不能宣称它等于实际赚取积分。
"""
from __future__ import annotations

from typing import Optional

BALANCE_DELTA = "balance_delta"
TASK_FALLBACK = "task_fallback"


def calculate_daily_points(today_balance: Optional[int], yesterday_balance: Optional[int],
                           task_points: int) -> tuple[int, str, Optional[int]]:
    """返回 (积分, 来源, 昨日基准余额)。0 是有效快照，不等于缺失。"""
    if today_balance is not None and yesterday_balance is not None:
        return int(today_balance) - int(yesterday_balance), BALANCE_DELTA, int(yesterday_balance)
    return int(task_points), TASK_FALLBACK, None


def points_source_label(source: str) -> str:
    return "账户余额日差" if source == BALANCE_DELTA else "任务积分合计（回退）"


def points_display(source: str, points: int) -> str:
    if source == BALANCE_DELTA:
        return f"今日余额净变化 {points:+d} 分"
    return f"今日任务积分 {points} 分（无昨日余额）"

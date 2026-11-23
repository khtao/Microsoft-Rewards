"""估算剩余搜索次数（经典链路保留入口，纯函数）。"""
from __future__ import annotations

# 电脑端搜索每次计 3 分。这是「剩余积分 → 还需几次搜索」的唯一折算口径，
# 由 pending.pending_desktop_rounds() 显式传入，不在别处另设除数。
POINTS_PER_SEARCH = 3


def remaining_searches(points_left: int,
                       points_per_search: int = POINTS_PER_SEARCH) -> int:
    """按“每次搜索 3 分”估算还差多少次搜索，向上取整。

    负的剩余积分按 0 次处理；除数为 0 或负数时不推算，直接返回 0。
    """
    if points_per_search <= 0:
        return 0
    return max(0, -(-points_left // points_per_search))

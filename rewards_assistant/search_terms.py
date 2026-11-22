"""估算剩余搜索次数（经典链路保留入口，纯函数）。"""
from __future__ import annotations


def remaining_searches(points_left: int, points_per_search: int = 5) -> int:
    """按“每次搜索 5 分”估算还差多少次搜索。"""
    if points_per_search <= 0:
        return 0
    return max(0, -(-points_left // points_per_search))

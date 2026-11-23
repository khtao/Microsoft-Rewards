"""基于官方同步的当日奖励任务快照决定运行哪些操作（不依赖 Qt）。"""
from __future__ import annotations

from .search_terms import POINTS_PER_SEARCH, remaining_searches


def outstanding(task) -> bool:
    """零积分占位项不构成待办；已完成任务也不应再次触发。"""
    return (not bool(task["complete"])
            and int(task["points_max"]) > int(task["points_earned"]))


def all_reward_tasks_done(tasks) -> bool:
    """至少有一项有积分上限，且所有这类任务没有剩余积分。"""
    rewarded = [t for t in tasks if int(t["points_max"]) > 0]
    return bool(rewarded) and not any(outstanding(t) for t in rewarded)


def pending_activity_links(tasks) -> list:
    """排除搜索类链接：PC 由拟人搜索处理，移动端不伪装完成。"""
    return [t for t in tasks if t["key"] not in ("pc_search", "mobile_search")
            and outstanding(t) and t["link"]]


def pending_desktop_rounds(tasks) -> int:
    """只按尚缺的电脑搜索积分估算搜索轮数，不混入移动搜索。

    官方快照里 ``points_max - points_earned`` 是剩余积分，按每次搜索 3 分
    向上取整折算成次数（51 分 → 17 次，6 分 → 2 次）。
    """
    return sum(remaining_searches(
        int(t["points_max"]) - int(t["points_earned"]), POINTS_PER_SEARCH)
        for t in tasks if t["key"] == "pc_search" and outstanding(t))


def planned_rounds(tasks, cap: int) -> int:
    """本轮实际搜索次数 = min(任务还需要几次, 强度档位的最多次数)。

    * ``need``：官方同步快照里尚缺的电脑端搜索积分 ÷ 3（向上取整）；
    * ``cap``：拟人强度档位写入 ``search_panel.repeat`` 的最多次数
      （低 / 中 / 高 = 20 / 16 / 12）。

    除此之外不叠加任何上限：没有全局硬顶、没有按会员等级的额外封顶、
    没有「至少 1 轮」的保底。need 为 0 或 cap 为 0 时返回 0，由调用方
    决定不启动浏览器。负数 / 非整数的 cap 归零，不抛异常。

    设置页摘要「至多 N 轮，根据任务进度，目前搜索 M 轮」与
    ``SearchRunner.for_pending()`` 共用这一个函数，显示值即执行值。
    """
    need = pending_desktop_rounds(tasks)
    try:
        limit = max(0, int(cap or 0))
    except (TypeError, ValueError, OverflowError):
        limit = 0
    return max(0, min(need, limit))


def pending_activity_items(tasks) -> list:
    """Playwright 活动卡片的候选集合：无需 API 提供落地 URL。"""
    return [t for t in tasks
            if t.key not in ("pc_search", "mobile_search")
            and not t.complete
            and int(t.points_max) > int(t.points_earned)
            and (t.offer_id or t.link or t.name)]

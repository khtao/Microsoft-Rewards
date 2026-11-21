"""基于官方同步的当日奖励任务快照决定运行哪些操作（不依赖 Qt）。"""
from __future__ import annotations

from .search_terms import remaining_searches


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
    """只按尚缺的电脑搜索积分估算搜索轮数，不混入移动搜索。"""
    return sum(remaining_searches(int(t["points_max"]) - int(t["points_earned"]))
               for t in tasks if t["key"] == "pc_search" and outstanding(t))


def planned_rounds(tasks, cap: int, *, hard_limit: int = 200) -> int:
    """本轮实际计划轮数 = min(任务剩余搜索次数, 强度档位上限)。

    设置页第一行写「至多 N 轮，根据任务进度，目前搜索 M 轮」：

    * ``cap``（N）来自拟人强度档位写入 ``search_panel.repeat`` 的上限；
    * ``M`` 是官方同步快照里尚缺的电脑端搜索次数；
    * 两者取小，所以「至多」是硬约束，而「目前」就是本轮真正会跑的次数。

    这是设置页摘要与 ``SearchRunner.for_pending()`` 共用的唯一口径，
    避免显示 16 轮、实际按任务进度只跑 12 轮（或反过来跑超上限）。
    """
    need = pending_desktop_rounds(tasks)
    try:
        limit = max(0, min(int(cap or 0), int(hard_limit)))
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

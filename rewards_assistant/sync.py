"""后台同步：官方状态始终实时读取；页面标题与连续天数按需限时补全。"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QThread, Signal

from .auth import AuthError, AuthManager
from .page_titles import fetch_real_titles, merge_titles
from .rewards_client import Snapshot, extract_streak, parse_dashboard
from .storage import Store, day_str


class StatusWorker(QThread):
    """探测当前 Cookie 是否有效。"""

    done = Signal(bool, str)

    def run(self) -> None:
        try:
            ok = AuthManager().is_signed_in()
            self.done.emit(bool(ok), "" if ok else "Cookie 无效或已过期，请重新粘贴")
        except Exception as exc:
            self.done.emit(False, f"无法检测登录状态：{exc}")


class SyncWorker(QThread):
    """官方 API 是权威数据源，HTML 只用于可选标题/连续天数补充。"""

    progress = Signal(str)
    succeeded = Signal(dict)
    failed = Signal(str)

    def __init__(self, store: Store, day: Optional[str] = None, parent=None,
                 enrich_pages: bool = True) -> None:
        super().__init__(parent)
        self.store = store
        self.day = day or day_str()
        self.enrich_pages = enrich_pages

    def run(self) -> None:
        try:
            manager = AuthManager(logger=self.progress.emit)

            self.progress.emit("正在获取官方任务进度…")
            # 缩短连接与读取等待；错误时不得使用旧缓存伪装同步成功。
            data = manager.fetch_dashboard(timeout=(4, 12))
            snap: Snapshot = parse_dashboard(data, self.day)

            # 保存当日先前从页面获取的可靠中文标题，避免快速同步时被内部 ID 覆盖。
            # 只有链接、任务类别、分值均未改变才认为仍是同一活动。
            previous = {row["key"]: row for row in self.store.tasks_of_day(self.day)}
            for task in snap.tasks:
                row = previous.get(task.key)
                if (row is not None and task.title_source != "api"
                        and row["title_source"] == "page"
                        and row["category"] == task.category
                        and int(row["points_max"]) == task.points_max
                        and ((task.offer_id and row["offer_id"] == task.offer_id)
                             or (task.raw_name and row["raw_name"] == task.raw_name)
                             or (task.link and row["link"] == task.link))):
                    task.name = row["name"]
                    task.title_source = "page"

            official = None
            if self.enrich_pages:
                # 只请求确实需要补标题的页面（两个页面并发）；
                # dashboard HTML 一并用于解析连续天数，不再重复拉取。
                pages = set()
                for task in snap.tasks:
                    if task.title_source not in ("api", "page"):
                        if task.category == "每日任务":
                            pages.add("daily")
                        elif task.category == "更多活动":
                            pages.add("routine")
                previous_day = self.store.daily(self.day)
                if not previous_day or not int(previous_day["official_streak"] or 0):
                    pages.add("daily")
                if pages:
                    try:
                        self.progress.emit("正在限时补全活动名称（不会重复抓取进度）…")
                        titles = fetch_real_titles(
                            manager.jar(), timeout=(3, 5), include=pages,
                            with_dashboard_html=("daily" in pages),
                        )
                        merge_titles(snap.tasks, titles["daily"], titles["routine"],
                                     logger=self.progress.emit)
                        if titles.get("dashboard_html"):
                            official = extract_streak(titles["dashboard_html"])
                    except Exception as exc:
                        self.progress.emit(f"任务名称补全失败，继续使用官方数据（{exc}）")

            # 状态与积分必须来自本次官方 API；页面及历史缓存仅参与标题显示。
            self.progress.emit(f"官方返回 {len(snap.tasks)} 项任务，正在保存…")
            for task in snap.tasks:
                self.store.upsert_task(
                    day=self.day,
                    key=task.key,
                    name=task.name,
                    category=task.category,
                    points_max=task.points_max,
                    points_earned=task.points_earned,
                    complete=task.complete,
                    manual=False,
                    link=task.link,
                    title_source=task.title_source,
                    offer_id=task.offer_id,
                    raw_name=task.raw_name,
                )
            if snap.tasks:
                self.store.remove_missing_official_tasks(self.day, (t.key for t in snap.tasks))
            self.store.upsert_available(self.day, snap.available_points, snap.level)
            summary = self.store.refresh_day(self.day)

            if official is not None:
                self.store.save_official_streak(self.day, official)

            self.progress.emit("同步完成（进度来自官方实时接口）")
            self.succeeded.emit({
                "day": self.day,
                "summary": dict(summary),
                "snapshot": snap,
                "official_streak": official,
            })
        except AuthError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"同步失败：{exc}")


def logout() -> None:
    """清除本地登录态（只删 Cookie，不动历史数据）。"""
    AuthManager().logout()

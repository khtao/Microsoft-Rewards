"""后台同步：把 Rewards 的只读数据写入本地 SQLite。

为什么放线程：网络请求通常要几秒，放在 GUI 线程会让窗口卡死。

同步分三步：
1. 调官方接口拿积分与任务骨架
2. 抓 /dashboard（每日活动）与 /earn（日常任务）两个页面，取**页面真实显示的名字**
3. 回填名称后入库
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QThread, Signal

from .auth import AuthError, AuthManager
from .config import REWARDS_DASHBOARD_URL
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
    """拉取 dashboard + 两个页面，合并后入库。"""

    progress = Signal(str)
    succeeded = Signal(dict)
    failed = Signal(str)

    def __init__(self, store: Store, day: Optional[str] = None, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.day = day or day_str()

    def run(self) -> None:
        try:
            manager = AuthManager(logger=self.progress.emit)

            self.progress.emit("正在读取 Rewards 数据…")
            data = manager.fetch_dashboard()
            snap: Snapshot = parse_dashboard(data, self.day)

            # ---- 页面真实名称：dashboard = 每日活动，earn = 日常任务
            try:
                self.progress.emit("正在读取页面上的任务名称…")
                titles = fetch_real_titles(manager.jar(), timeout=20)
                merge_titles(snap.tasks, titles["daily"], titles["routine"],
                             logger=self.progress.emit)
            except Exception as exc:
                # 抓不到就用接口名兜底，不影响主流程
                self.progress.emit(f"页面名称读取失败，回退为接口名称（{exc}）")

            self.progress.emit(f"解析到 {len(snap.tasks)} 项任务，正在入库…")
            for t in snap.tasks:
                self.store.upsert_task(
                    day=self.day,
                    key=t.key,
                    name=t.name,
                    category=t.category,
                    points_max=t.points_max,
                    points_earned=t.points_earned,
                    complete=t.complete,
                    manual=False,
                    link=t.link,
                    title_source=t.title_source,
                )
            self.store.upsert_available(self.day, snap.available_points, snap.level)
            summary = self.store.refresh_day(self.day)

            # 官方连续天数：从 dashboard 页面里尽力提取，抓不到也不影响
            official = None
            try:
                html = manager.fetch_page_html(REWARDS_DASHBOARD_URL, timeout=20)
                official = extract_streak(html)
                if official is not None:
                    self.store.save_official_streak(self.day, official)
            except Exception:
                official = None

            self.progress.emit("同步完成")
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

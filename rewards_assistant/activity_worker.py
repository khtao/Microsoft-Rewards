"""使用真实 Edge 点击 Bing Rewards 日常卡片，随后读取官方状态核验。"""
from __future__ import annotations

import random
import threading
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .activity_click import click_activity, confirmed_completed, locate_activity, open_rewards_panel
from .auth import AuthManager
from .browser import (copy_profile, default_profile_dir, edge_is_running,
                      find_edge_binary, launch_edge, profile_locked)
from .config import data_dir
from .rewards_client import parse_dashboard


class ActivityWorker(QThread):
    progress = Signal(str)
    completed = Signal(dict)

    def __init__(self, tasks, browser_options=None, gap_range=(5, 10), parent=None):
        super().__init__(parent)
        self.tasks = list(tasks)
        self.options = dict(browser_options or {})
        self.gap_range = gap_range
        self._stop = threading.Event()

    def request_stop(self):
        self._stop.set()

    def _pause(self, seconds: float):
        # 停止操作可打断任务间等待，不依赖 GUI 线程。
        self._stop.wait(max(0, seconds))

    def run(self):
        stats = {"total": len(self.tasks), "clicked": 0, "verified": 0,
                 "missing": [], "unconfirmed": [], "stopped": False, "error": ""}
        pw = context = None
        try:
            from playwright.sync_api import sync_playwright
            options = self.options
            profile = Path(options.get("user_data_dir") or default_profile_dir()).expanduser()
            if not profile.exists():
                raise FileNotFoundError(f"未找到 Edge 用户目录：{profile}")
            # 不和已经打开的用户 Edge 争用 profile。活动与搜索分阶段串行执行。
            if options.get("copy_profile") or edge_is_running() or profile_locked(profile):
                snapshot = data_dir() / "activity_edge_profile_snapshot"
                if profile.resolve() == snapshot.resolve():
                    raise ValueError("活动快照目录不能作为源用户目录")
                self.progress.emit("检测到 Edge 已运行，正在复制登录配置（不关闭当前浏览器）")
                profile = copy_profile(profile, snapshot)
            if self._stop.is_set():
                return
            pw = sync_playwright().start()
            context = launch_edge(
                pw, user_data_dir=str(profile), headless=False,
                edge_path=options.get("edge_path") or find_edge_binary(),
                proxy=options.get("proxy"), sandbox=options.get("sandbox"),
                flag_warning="on", verbose=False,
            )
            page = context.pages[0] if context.pages else context.new_page()
            self.progress.emit("正在打开 Bing Rewards 侧栏，识别未完成活动卡片…")
            if not open_rewards_panel(page, self.tasks, stop=self._stop.is_set,
                                      log=self.progress.emit):
                stats["error"] = "Bing 侧栏、Rewards 面板和官方任务页均未找到匹配的活动卡片；请确认 Edge 已登录相同微软账户"
                return
            auth = AuthManager()
            rng = random.Random()
            for i, task in enumerate(self.tasks, 1):
                if self._stop.is_set():
                    break
                name = task.name
                # 卡片可能在点击前一项后刷新或被导航走，重新进入官方界面查找。
                if locate_activity(page, task) is None:
                    open_rewards_panel(page, [task], stop=self._stop.is_set,
                                       log=self.progress.emit)
                if self._stop.is_set():
                    break
                try:
                    if not click_activity(page, task, rng=rng):
                        stats["missing"].append(name)
                        self.progress.emit(f"[{i}/{len(self.tasks)}] 未找到可点击的活动卡片：{name}")
                        continue
                    stats["clicked"] += 1
                    self.progress.emit(f"[{i}/{len(self.tasks)}] 已模拟鼠标点击：{name}；等待官方状态核验")
                    verified = False
                    for pause in (3, 4, 5):
                        self._pause(pause)
                        if self._stop.is_set():
                            break
                        try:
                            fresh = parse_dashboard(auth.fetch_dashboard(timeout=18))
                            if confirmed_completed(task, fresh):
                                verified = True
                                stats["verified"] += 1
                                self.progress.emit(f"官方已确认完成：{name}")
                                break
                        except Exception as exc:
                            self.progress.emit(f"核验失败（稍后将整体同步）：{type(exc).__name__}")
                    if not verified:
                        stats["unconfirmed"].append(name)
                        self.progress.emit(f"点击后仍未确认积分：{name}（可能需要答题或进一步互动）")
                except Exception as exc:
                    stats["unconfirmed"].append(name)
                    self.progress.emit(f"活动操作失败：{name}（{type(exc).__name__}: {exc}）")
                if i < len(self.tasks) and not self._stop.is_set():
                    self._pause(rng.uniform(*sorted(self.gap_range)))
        except Exception as exc:
            stats["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            stats["stopped"] = self._stop.is_set()
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            if pw is not None:
                try:
                    pw.stop()
                except Exception:
                    pass
            self.completed.emit(stats)

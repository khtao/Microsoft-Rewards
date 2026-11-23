"""使用真实 Edge 点击 Bing Rewards 日常卡片。

运行期间**不做任何官方状态核验 / API 请求**：点击即记为已操作，整轮活动 +
搜索全部结束后由 Application 统一同步一次并刷新界面。这既避免运行中与只读
同步争用会话，也避免把在线核验的中间态提示弹到主界面上。
"""
from __future__ import annotations

import random
import threading

from PySide6.QtCore import QThread, Signal

from .timing import jitter
from .activity_click import click_activity, locate_activity, open_rewards_panel
from .browser import (browser_error_hint, cleanup_automation_profile,
                      find_edge_binary, launch_automation_edge)


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
        automation_snapshot = None
        try:
            from playwright.sync_api import sync_playwright
            options = self.options
            self.progress.emit("正在准备全新浏览器配置（不影响已打开的 Edge）…")
            if self._stop.is_set():
                return
            pw = sync_playwright().start()
            context, automation_snapshot = launch_automation_edge(
                pw, user_data_dir=options.get("user_data_dir"), headless=False,
                edge_path=options.get("edge_path") or find_edge_binary(),
                proxy=options.get("proxy"), sandbox=options.get("sandbox"),
                flag_warning="on", verbose=False,
                log=lambda a, d: self.progress.emit(f"{a} {d}"),
            )
            page = context.pages[0] if context.pages else context.new_page()
            self.progress.emit("正在打开 Bing Rewards 侧栏，识别未完成活动卡片…")
            if not open_rewards_panel(page, self.tasks, stop=self._stop.is_set,
                                      log=self.progress.emit):
                stats["error"] = "Bing 侧栏、Rewards 面板和官方任务页均未找到匹配的活动卡片；请确认 Edge 已登录相同微软账户"
                return
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
                    # 不做逐项核验：完成后统一同步一次再刷新积分与任务状态。
                    self.progress.emit(f"[{i}/{len(self.tasks)}] 已点击：{name}")
                except Exception as exc:
                    stats["unconfirmed"].append(name)
                    self.progress.emit(
                        f"活动操作失败：{name}（{browser_error_hint(type(exc).__name__ + ': ' + str(exc))}）")
                if i < len(self.tasks) and not self._stop.is_set():
                    lo, hi = sorted(self.gap_range)
                    self._pause(jitter(lo, hi, rng))
        except Exception as exc:
            stats["error"] = browser_error_hint(f"{type(exc).__name__}: {exc}")
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
            if automation_snapshot is not None:
                cleanup_automation_profile(automation_snapshot)
            self.completed.emit(stats)

"""在**同一个真实 Edge** 里完成一整轮自动任务：活动卡片点击 + 拟人搜索。

运行期间不做任何官方状态核验 / API 请求：点击即记为已操作，整轮活动 +
搜索全部结束后由 Application 统一同步一次并刷新界面。这既避免运行中与只读
同步争用会话，也避免把在线核验的中间态提示弹到主界面上。

为什么搜索必须留在这个线程
--------------------------
Playwright 同步 API 的对象绑定创建它的线程/greenlet，跨线程复用会直接报
"Cannot switch to a different thread"。旧实现里 ``ActivityWorker`` 点完卡片就关闭浏览器上下文并停止 Playwright，
随后 ``SearchWorker`` 再起一个全新的 Playwright 和 Edge —— 用户看到的就是
「浏览器关掉又重新打开」。

现在 ``pw`` / ``context`` / ``page`` 由本 worker 全程持有：点击阶段结束后把
同一个对象注入 ``api.run_human_search(context=..., page=...,
close_browser=False)``，在同一线程里续跑搜索；``finally`` 里才统一关闭浏览器
并清理临时 profile。整轮只有一次 Edge 启动、一次关闭。
"""
from __future__ import annotations

import random
import re
import threading

from PySide6.QtCore import QThread, Signal

from . import debug_log
from .timing import jitter
from .activity_click import click_activity, locate_activity, open_rewards_panel
from .browser import (browser_error_hint, cleanup_automation_profile,
                      find_edge_binary, launch_automation_edge)


class ActivityWorker(QThread):
    progress = Signal(str)
    completed = Signal(dict)

    def __init__(self, tasks, browser_options=None, gap_range=(5, 10),
                 search_params=None, parent=None):
        super().__init__(parent)
        self.tasks = list(tasks)
        self.options = dict(browser_options or {})
        self.gap_range = gap_range
        # 非 None 时：点击阶段结束后在同一 context/page 内续跑拟人搜索。
        self.search_params = dict(search_params) if search_params else None
        self._stop = threading.Event()

    def request_stop(self):
        self._stop.set()

    def _pause(self, seconds: float):
        # 停止操作可打断任务间等待，不依赖 GUI 线程。
        self._stop.wait(max(0, seconds))

    def _search_log(self, rec: dict) -> None:
        """把 run_human_search 的结构化日志转成界面进度行。"""
        action = str(rec.get("action", ""))
        detail = str(rec.get("detail", ""))
        if action == "search_start":
            m = re.search(r"第\s*(\d+)\s*/\s*(\d+)\s*次", detail)
            if m:
                self.progress.emit(f"搜索进度 {m.group(1)}/{m.group(2)}")
        self.progress.emit(f"{action} {detail}".strip()[:160])
        if action in ("error", "run_error", "browser_error"):
            debug_log.error("search", "拟人搜索运行异常", detail=detail)

    def run(self):
        stats = {"total": len(self.tasks), "clicked": 0, "verified": 0,
                 "missing": [], "unconfirmed": [], "stopped": False,
                 "error": "", "search": None, "search_error": ""}
        pw = context = page = None
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
            debug_log.debug("activity", "自动任务浏览器已启动",
                            tasks=len(self.tasks),
                            with_search=self.search_params is not None)
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
                        debug_log.warn("activity", "未找到可点击的活动卡片",
                                       task=name, index=i)
                        continue
                    stats["clicked"] += 1
                    # 不做逐项核验：完成后统一同步一次再刷新积分与任务状态。
                    self.progress.emit(f"[{i}/{len(self.tasks)}] 已点击：{name}")
                    debug_log.debug("activity", "活动卡片已点击", task=name,
                                    index=i)
                except Exception as exc:
                    stats["unconfirmed"].append(name)
                    self.progress.emit(
                        f"活动操作失败：{name}（{browser_error_hint(type(exc).__name__ + ': ' + str(exc))}）")
                    debug_log.error("activity", "活动卡片点击异常", exc=exc,
                                    task=name, index=i)
                if i < len(self.tasks) and not self._stop.is_set():
                    lo, hi = sorted(self.gap_range)
                    self._pause(jitter(lo, hi, rng))

            # ---- 拟人搜索：复用同一个 context/page，绝不关掉再重开浏览器 ----
            if self.search_params is not None and not self._stop.is_set():
                self.progress.emit("活动卡片已处理，保持同一个浏览器继续拟人搜索…")
                debug_log.debug("activity", "在同一浏览器内续跑拟人搜索",
                                repeat=self.search_params.get("repeat"))
                from .api import run_human_search
                params = dict(self.search_params)
                params.update(
                    page=page, context=context, close_browser=False,
                    log_callback=self._search_log, stop_check=self._stop.is_set,
                    verbose=False,
                )
                try:
                    report = run_human_search(**params)
                    stats["search"] = {
                        "ok": report.ok_count, "fail": report.fail_count,
                        "clicked": report.clicked_count,
                        "seconds": report.total_seconds,
                    }
                    self.progress.emit(
                        f"拟人搜索完成：成功 {report.ok_count} / "
                        f"失败 {report.fail_count}，点击 {report.clicked_count} 次")
                    debug_log.debug("activity", "拟人搜索完成",
                                    ok=report.ok_count, fail=report.fail_count,
                                    seconds=report.total_seconds)
                except Exception as exc:
                    stats["search_error"] = browser_error_hint(
                        f"{type(exc).__name__}: {exc}")
                    self.progress.emit("拟人搜索失败：" + stats["search_error"])
                    debug_log.error("activity", "拟人搜索阶段异常", exc=exc)
        except Exception as exc:
            stats["error"] = browser_error_hint(f"{type(exc).__name__}: {exc}")
            debug_log.error("activity", "自动任务运行异常", exc=exc,
                            tasks=len(self.tasks))
        finally:
            stats["stopped"] = self._stop.is_set()
            # 整轮（点击 + 搜索）结束后才关闭唯一的那份浏览器，并清理 profile。
            if context is not None:
                try:
                    context.close()
                except Exception as exc:
                    debug_log.warn("activity", "关闭浏览器 context 失败", exc=exc)
            if pw is not None:
                try:
                    pw.stop()
                except Exception as exc:
                    debug_log.warn("activity", "停止 Playwright 驱动失败", exc=exc)
            if automation_snapshot is not None:
                cleanup_automation_profile(automation_snapshot)
            self.completed.emit(stats)

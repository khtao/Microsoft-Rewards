"""应用控制器：托盘、调度器、同步线程与窗口生命周期。"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from typing import Optional

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import autostart
from .activity_worker import ActivityWorker
from .auth import AuthManager
from .config import APP_TITLE, APP_VERSION, Settings
from .fonts import bootstrap, pick_qt_font_family
from .local_browser import open_url as open_in_browser
from .notifier import Notifier
from .opener import PendingOpener, random_gap
from .pending import all_reward_tasks_done, pending_activity_links, pending_activity_items
from .storage import Store, day_str
from .sync import StatusWorker, SyncWorker
from .ui.icons import app_icon
from .ui.login_dialog import LoginDialog
from .ui.main_window import MainWindow
SINGLE_INSTANCE_KEY = "MSRewardsAssistant_SingleInstance_v1"


def _set_win_app_id() -> None:
    """Windows 下给进程设置 AppUserModelID，通知才会带正确图标与来源名。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Independent.RewardsActivityCompanion")
    except Exception:
        pass


class Application(QObject):
    """把数据层、界面层和系统集成粘在一起的控制器。"""

    def __init__(self, argv: list) -> None:
        super().__init__()
        self.argv = argv
        self.start_minimized = "--minimized" in argv
        self.settings = Settings()
        self.store = Store()
        self.notifier: Optional[Notifier] = None
        self.window: Optional[MainWindow] = None
        self.tray: Optional[QSystemTrayIcon] = None
        self._workers: list = []
        self._last_points: Optional[int] = None
        self._after_sync: set[str] = set()
        self._last_page_enrichment: Optional[datetime] = None
        self._sync_enrich_current = False
        self._activity_worker: Optional[ActivityWorker] = None
        self._activity_then_search = False

        # 逐个打开任务页的调度器（第一个立即，之后随机间隔）
        self._opener = PendingOpener(self._gap_seconds,
                                     open_in_browser)
        self._opener.opened.connect(self._on_opened)
        self._opener.failed.connect(self._on_open_failed)
        self._opener.waiting.connect(self._on_waiting)
        self._opener.countdown.connect(self._on_countdown)
        self._opener.finished.connect(self._on_open_finished)
        self._opener.stopped.connect(self._on_open_stopped)

    # ------------------------------------------------------------------ 启动

    def run(self) -> int:
        app = QApplication(self.argv)
        app.setApplicationName(APP_TITLE)
        # Wayland/X11 下用此 desktop id 将正在运行的窗口绑定到启动器/任务栏图标。
        # 对应 /usr/share/applications/ms-rewards-assistant.desktop。
        if sys.platform.startswith("linux"):
            app.setDesktopFileName("ms-rewards-assistant")
        app.setApplicationVersion(APP_VERSION)
        app.setQuitOnLastWindowClosed(False)
        app.setWindowIcon(app_icon())
        _set_win_app_id()
        self._setup_fonts(app)
        self.app = app

        if self._already_running():
            return 0

        self.notifier = Notifier(logger=self._log)
        self.window = MainWindow(self)
        # 搜索结束后刷新官方进度，否则界面会一直显示启动前的剩余积分。
        self.window.runner.finished.connect(
            lambda _report: QTimer.singleShot(3000, lambda: self.sync_now(auto=True)))
        self._build_tray()
        self._build_scheduler()

        if not self.start_minimized or self.tray is None:
            self.window.show()
        else:
            self._log("已随系统启动，隐藏到托盘")

        self._migrate_labels()
        self._prune_zero_tasks()
        if self.settings.get("sync_interval_minutes", 0):
            # 初次自动同步本身就会校验 Cookie，避免再发一次重复 API 请求。
            self.set_login_status("等待首次同步验证…", None)
            QTimer.singleShot(3000, lambda: self.sync_now(auto=True))
        else:
            self.check_login_status()
        return app.exec()

    def _setup_fonts(self, app) -> None:
        """打包后目标机可能没有中文字体，这里把内置字体注册上。"""
        try:
            info = bootstrap()
        except Exception as exc:
            self._log(f"字体初始化失败（不影响启动）：{exc}")
            return
        family = info.get("qt_family") or pick_qt_font_family()
        if family:
            try:
                from PySide6.QtGui import QFont  # noqa: PLC0415

                app.setFont(QFont(family))
            except Exception:
                pass
        if info.get("frozen"):
            self._log(
                f"打包模式：内置字体 {info.get('bundle_fonts', 0)} 个，"
                f"界面 {info.get('qt_family') or '系统默认'}")

    def _prune_zero_tasks(self) -> None:
        """启动时清掉历史库里不给积分的任务，只跑一次。"""
        if int(self.settings.get("prune_version", 0) or 0) >= 1:
            return
        try:
            n = self.store.prune_zero_point_tasks()
            if n:
                self._log(f"已过滤 {n} 条不给积分的任务")
                self.window.refresh_all()
        except Exception as exc:
            self._log(f"过滤零积分任务失败：{exc}")
        self.settings.set("prune_version", 1)

    def _migrate_labels(self) -> None:
        """升级后把库里的旧活动 ID 刷成中文名，只跑一次。"""
        if int(self.settings.get("labels_version", 0) or 0) >= 2:
            return
        try:
            n = self.store.retranslate_names()
            if n:
                self._log(f"已把 {n} 条历史任务名刷新为中文")
                self.window.refresh_all()
        except Exception as exc:
            self._log(f"任务名刷新失败：{exc}")
        self.settings.set("labels_version", 2)

    def _already_running(self) -> bool:
        """单实例：已有进程在跑就把它的窗口唤出来。"""
        socket = QLocalSocket()
        socket.connectToServer(SINGLE_INSTANCE_KEY)
        if socket.waitForConnected(300):
            socket.write(b"show")
            socket.flush()
            socket.waitForBytesWritten(300)
            socket.disconnectFromServer()
            return True
        QLocalServer.removeServer(SINGLE_INSTANCE_KEY)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_new_connection)
        self.server.listen(SINGLE_INSTANCE_KEY)
        return False

    def _on_new_connection(self) -> None:
        conn = self.server.nextPendingConnection()
        if conn is None:
            return
        conn.readyRead.connect(lambda: self._handle_ipc(conn))

    def _handle_ipc(self, conn) -> None:
        data = bytes(conn.readAll()).decode("utf-8", "ignore")
        conn.disconnected.connect(conn.deleteLater)
        if "show" in data:
            self.show_window()
        elif "quit" in data:
            self.quit()

    # ------------------------------------------------------------------ 托盘

    def _build_tray(self) -> None:
        """创建托盘；登录桌面较慢时延后重试，恢复后采用内置应用图标。"""
        if self.tray is not None:
            return
        if not QSystemTrayIcon.isSystemTrayAvailable():
            if not hasattr(self, "_tray_retry"):
                self._log("系统托盘尚未就绪，窗口照常显示；将定期检测托盘服务")
                self._tray_retry = QTimer(self)
                self._tray_retry.setInterval(8000)
                self._tray_retry.timeout.connect(self._build_tray)
                self._tray_retry.start()
            return
        if hasattr(self, "_tray_retry"):
            self._tray_retry.stop()
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(APP_TITLE)
        self.notifier.attach(self.tray)

        # 设定父窗口并保存引用，避免无父级 QMenu 在函数结束后被释放。
        self._tray_menu = QMenu(self.window)
        menu = self._tray_menu
        menu.addAction("显示主窗口").triggered.connect(self.show_window)
        menu.addSeparator()
        self.act_tray_sync = menu.addAction("立即同步")
        self.act_tray_sync.triggered.connect(self.window.act_sync.trigger)
        menu.addAction("登录微软账号").triggered.connect(self.window.act_login.trigger)
        menu.addAction("刷新界面").triggered.connect(self.window.act_refresh.trigger)
        menu.addSeparator()
        self.act_tray_run = menu.addAction("自动完成未完成任务")
        self.act_tray_run.triggered.connect(self.window.quick.tasks_btn.click)
        self.act_tray_stop = menu.addAction("停止")
        self.act_tray_stop.triggered.connect(self.window.quick.stop_btn.click)
        menu.addSeparator()
        menu.addAction("退出程序").triggered.connect(self.quit)
        menu.aboutToShow.connect(self.update_tray_actions)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()
        self.update_tray_tooltip()
        self.update_tray_actions()

    def update_tray_actions(self) -> None:
        """菜单可用状态与主窗口按钮一致（包括从搜索页启动的任务）。"""
        if self.tray is None or self.window is None:
            return
        self.act_tray_sync.setEnabled(self.window.act_sync.isEnabled())
        self.act_tray_run.setEnabled(self.window.quick.tasks_btn.isEnabled())
        self.act_tray_stop.setEnabled(self.window.quick.stop_btn.isEnabled())

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.DoubleClick,
                      QSystemTrayIcon.ActivationReason.Trigger):
            self.show_window()

    def show_window(self) -> None:
        if self.window is None:
            return
        if self.window.isVisible():
            self.window.raise_()
            self.window.activateWindow()
        else:
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()

    def update_tray_tooltip(self) -> None:
        if self.tray is None:
            return
        row = self.store.daily(day_str())
        if row is None:
            self.tray.setToolTip(f"{APP_TITLE}\n今天还没有记录")
            return
        self.tray.setToolTip(
            f"{APP_TITLE}\n今日 {row['points_earned']}/{row['points_max']} 分"
            f" · 任务 {row['tasks_done']}/{row['tasks_total']} · 连续 {row['streak']} 天"
        )

    # ------------------------------------------------------------------ 调度

    def _build_scheduler(self) -> None:
        self.timer = QTimer(self)
        self.timer.setInterval(30_000)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._today = datetime.now().date()
        self._fired: set = set()
        if self.settings.get("auto_run_fired_day") == self._today.isoformat():
            saved = self.settings.get("auto_run_fired_times", [])
            if isinstance(saved, list):
                self._fired.update(f"run:{t}" for t in saved if isinstance(t, str))
        self._next_sync: Optional[datetime] = None
        interval = int(self.settings.get("sync_interval_minutes") or 0)
        if interval:
            self._next_sync = datetime.now() + timedelta(minutes=interval)

    def _tick(self) -> None:
        now = datetime.now()

        # 跨天：重置提醒与已触发标记、刷新界面
        if now.date() != self._today:
            self._today = now.date()
            self._fired.clear()
            self._remember_run_day(now)
            self.window.refresh_all()
            self.update_tray_tooltip()
            self.notifier.notify(APP_TITLE, "新的一天，今日任务已重置，加油 💪")

        # 自动同步
        interval = int(self.settings.get("sync_interval_minutes") or 0)
        if interval and self._next_sync and now >= self._next_sync:
            self._next_sync = now + timedelta(minutes=interval)
            self.sync_now(auto=True)
        elif interval and self._next_sync is None:
            self._next_sync = now + timedelta(minutes=interval)

        clock = now.strftime("%H:%M")
        # 定时自动打开任务页与定时自动完成各自独立。
        when = self.settings.get("auto_open_time") or ""
        if when and "autoopen" not in self._fired and clock == when:
            self._fired.add("autoopen")
            self._auto_open_morning()

        # 各时刻每天只触发一次，最多 10 个且由 Settings 保证去重。
        if self.settings.get("auto_run_enabled", False):
            for time_text in self.settings.get("auto_run_times", []):
                marker = f"run:{time_text}"
                if clock == time_text and marker not in self._fired:
                    self._fired.add(marker)
                    self._remember_run_day(now)
                    self._request_run_pending(scheduled=True)

        for key, setting_key in (("morning", "remind_time"),
                                 ("evening", "evening_remind_time")):
            when = self.settings.get(setting_key) or ""
            if when and key not in self._fired and clock == when:
                self._fired.add(key)
                self._remind(key)

    def _remember_run_day(self, now: datetime) -> None:
        """记录已触发的时间；程序重启也不重复执行当天同一个时间点。"""
        data = {"auto_run_fired_day": now.date().isoformat(),
                "auto_run_fired_times": sorted(m[4:] for m in self._fired if m.startswith("run:"))}
        try:
            self.settings.update(data)
        except (OSError, ValueError) as exc:
            self._log(f"持久化定时触发记录失败：{exc}")

    def _all_done(self) -> bool:
        day = self.store.daily(day_str())
        if day is None:
            return False
        tasks = self.store.tasks_of_day(day_str())
        return bool(day["all_done"]) or all_reward_tasks_done(tasks)

    def _auto_open_morning(self) -> None:
        """保留原先的定时打开功能，与新自动运行的 10 个时间点互不影响。"""
        if self._all_done():
            self._log("任务已全部完成，跳过定时打开")
            return
        # 活动卡片可能在当天发生变化，定时点击前始终同步官方任务列表。
        self._after_sync.add("open")
        self.window.update_running_state()
        self.sync_now(auto=True)

    def run_pending_tasks(self) -> None:
        """今日页及托盘的统一入口：先同步官方状态，再针对待办执行。"""
        self._request_run_pending(scheduled=False)

    def _request_run_pending(self, *, scheduled: bool) -> None:
        if self._all_done():
            self._log("今日全部任务已完成，忽略启动时间点")
            if not scheduled:
                self._toast("今日任务已全部完成，无需运行")
            return
        if self.window is None:
            return
        if self.window.runner.is_running() or self.activity_running():
            self._log("已有电脑端搜索或活动点击任务在执行，跳过重复启动")
            return
        # 同步期间不丢弃启动意图；同步成功后再次核对完成状态。
        self._after_sync.add("run")
        self.window.update_running_state()
        if getattr(self, "_syncing", False):
            self._log("同步尚未完成，自动运行将在同步后继续判断")
            return
        self._log("定时任务开始同步" if scheduled else "开始同步未完成任务")
        self.sync_now(auto=True)

    def cancel_pending_actions(self) -> None:
        """“停止”同时取消等待同步完成后再执行的动作。"""
        if self._after_sync:
            self._after_sync.clear()
            self._log("已取消同步后排队的自动任务")
        if self.activity_running():
            self._activity_worker.request_stop()
            self._activity_then_search = False
            self._log("正在停止活动卡片点击任务…")
        if self.window is not None:
            self.window.update_running_state()

    def activity_running(self) -> bool:
        return self._activity_worker is not None and self._activity_worker.isRunning()

    def _start_activity_clicks(self, tasks: list, *, then_search: bool = False) -> None:
        """使用真实 Edge 鼠标点击侧栏卡片；搜索等活动核验、同步后再运行。"""
        if self.activity_running() or self.window.runner.is_running():
            self._log("活动点击或电脑端搜索正在执行，不重复启动")
            return
        if not tasks:
            if then_search:
                self._start_pc_search()
            else:
                self._set_open_progress("没有需要点击的活动卡片")
            return
        options = dict(self.settings.get("search_panel", {}) or {})
        try:
            # 优先使用账户设置当前表单值，和电脑端搜索共用 profile 等配置。
            options.update(self.window.search._collect())
        except Exception:
            pass
        worker = ActivityWorker(
            tasks, browser_options=options,
            gap_range=(int(self.settings.get("auto_open_gap_min", 5)),
                       int(self.settings.get("auto_open_gap_max", 10))),
            parent=self,
        )
        self._activity_worker = worker
        self._activity_then_search = then_search
        worker.progress.connect(self._on_activity_progress)
        worker.completed.connect(self._on_activity_completed)
        worker.finished.connect(lambda: self._on_activity_worker_exited(worker))
        worker.finished.connect(worker.deleteLater)
        worker.start()
        self.window.update_running_state()

    def _on_activity_progress(self, message: str) -> None:
        self._log(message)
        self._set_open_progress(message)

    def _on_activity_completed(self, stats: dict) -> None:
        self._log(f"Rewards 活动点击结束：点击 {stats['clicked']}/{stats['total']}，"
                  f"官方确认 {stats['verified']}；未确认 {len(stats['unconfirmed'])}，"
                  f"未找到 {len(stats['missing'])}")
        if stats.get("error"):
            self._log("活动点击错误：" + stats["error"])
        if stats.get("stopped"):
            self._activity_then_search = False
            self._set_open_progress("已停止活动自动点击")
        else:
            self._set_open_progress(
                f"活动点击 {stats['clicked']}/{stats['total']}；官方确认 "
                f"{stats['verified']}。正在同步核对最终状态…")
            if self._activity_then_search:
                self._after_sync.add("search")
            self._activity_then_search = False
            self.sync_now(auto=True)
        self.window.update_running_state()

    def _on_activity_worker_exited(self, worker) -> None:
        if self._activity_worker is worker:
            self._activity_worker = None
        if self.window is not None:
            self.window.update_running_state()

    def _open_pending_links(self, snap=None) -> None:
        """兼容旧“定时打开”设置：现在实际执行 Rewards 活动卡片点击。"""
        if self._all_done():
            self._log("全部完成，跳过 Rewards 活动点击")
            return
        if snap is None:
            self._log("缺少同步后的任务快照，跳过自动点击")
            return
        self._start_activity_clicks(pending_activity_items(snap.tasks))

    def _start_pc_search(self) -> None:
        # 电脑端搜索仍由拟人搜索运行器负责，移动端不在桌面伪造完成。
        if not self.window.run_pending_tasks():
            tasks = self.store.tasks_of_day(day_str())
            if any(t["key"] == "mobile_search" and not t["complete"]
                   and int(t["points_max"]) > int(t["points_earned"]) for t in tasks):
                self._log("移动端搜索仍未完成，需要在移动设备上操作")

    def _run_pending_from_snapshot(self, snap=None) -> None:
        if self._all_done():
            self._log("同步确认全部完成，忽略本次启动")
            return
        if snap is None:
            self._log("缺少官方任务快照，无法定位活动卡片")
            self._start_pc_search()
            return
        activities = pending_activity_items(snap.tasks)
        if activities:
            self._start_activity_clicks(activities, then_search=True)
        else:
            self._start_pc_search()

    def _remind(self, key: str) -> None:
        row = self.store.daily(day_str())
        if self._all_done() and self.settings.get("silent_when_all_done", True):
            self._log("今日任务已完成，跳过提醒")
            return
        if row is None:
            self.notifier.notify(APP_TITLE, "今天的 Rewards 还没开始，点托盘图标同步一下吧")
            return
        left_points = int(row["points_max"]) - int(row["points_earned"])
        left_tasks = int(row["tasks_total"]) - int(row["tasks_done"])
        title = "早间打卡提醒" if key == "morning" else "晚间补做提醒"
        self.notifier.notify(APP_TITLE, f"{title}：还剩 {left_tasks} 项 / 约 {left_points} 分")

    # ------------------------------------------------------------------ 同步

    def sync_now(self, auto: bool = False) -> None:
        if getattr(self, "_syncing", False):
            if not auto:
                self._toast("同步正在进行中，请稍候…")
            return
        self._syncing = True
        self.window.set_busy(True, "正在同步 Rewards 数据…")
        # 页面辅助数据最多约 45 分钟更新一次；中间的同步只调用官方 API。
        # 活动点击前后虽会连续同步，但并不需要重复拉取两个 HTML 页面。
        now = datetime.now()
        last = getattr(self, "_last_page_enrichment", None)
        enrich = last is None or (now - last) >= timedelta(minutes=45)
        # 等官方 API 确认成功后才启动冷却计时；网络失败可立即重试。
        self._sync_enrich_current = enrich
        worker = SyncWorker(self.store, day_str(), self, enrich_pages=enrich)
        worker.progress.connect(lambda msg: self.window.set_busy(True, msg))
        worker.succeeded.connect(self._on_sync_ok)
        worker.failed.connect(self._on_sync_fail)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda: self._forget_worker(worker))
        worker.start()
        self._workers.append(worker)

    def _on_sync_ok(self, payload: dict) -> None:
        self._syncing = False
        if getattr(self, "_sync_enrich_current", False):
            self._last_page_enrichment = datetime.now()
        self._sync_enrich_current = False
        summary = payload["summary"]
        snap = payload["snapshot"]
        self.window.set_busy(False)
        self.window.refresh_all()
        self.update_tray_tooltip()

        earned = int(summary.get("points_earned") or 0)
        total = int(summary.get("points_max") or 0)
        available, _level, _official = self.store.latest_available()
        text = f"今日 {earned}/{total} 分 · 任务 {snap.tasks_done}/{len(snap.tasks)}"
        if available:
            text += f" · 账户可用 {available:,}"
        self._log(text)

        if self.settings.get("notify_after_sync", True):
            if summary.get("all_done"):
                self.notifier.notify(APP_TITLE, f"今日任务已全部完成 🎉  {text}")
            elif self._last_points is not None and earned > self._last_points:
                self.notifier.notify(APP_TITLE, f"积分有更新：{text}")
        self._last_points = earned
        self.set_login_status("已登录 · 同步正常", True)

        actions = self._after_sync.copy()
        self._after_sync.clear()
        if "run" in actions:
            self._run_pending_from_snapshot(snap)
        elif "open" in actions:
            self._open_pending_links(snap)
        elif "search" in actions:
            self._start_pc_search()
        self.window.update_running_state()

    def _on_sync_fail(self, message: str) -> None:
        self._syncing = False
        self._sync_enrich_current = False
        self._after_sync.clear()
        self.window.update_running_state()
        self.window.set_busy(False, message)
        self._log(message)
        self.set_login_status("未登录或会话已过期", False)
        if self.window.isVisible():
            self.notifier.notify(APP_TITLE, message, timeout_ms=10000)
        else:
            self._toast(message)

    def check_login_status(self) -> None:
        self.set_login_status("检测中…", None)
        worker = StatusWorker(self)
        worker.done.connect(
            lambda ok, msg: self.set_login_status("已登录，可直接同步" if ok else msg, ok))
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda: self._forget_worker(worker))
        worker.start()
        self._workers.append(worker)

    def set_login_status(self, text: str, ok: Optional[bool]) -> None:
        if self.window is not None:
            self.window.set_login_status(text, ok)

    def login(self) -> None:
        dlg = LoginDialog(self.window)

        def on_login_changed(ok):
            # 用户更换 Cookie 后，下次同步应允许重新读取页面标题。
            if ok:
                self._last_page_enrichment = None
            self.set_login_status("已登录，可直接同步" if ok else "未登录", ok)

        dlg.login_state_changed.connect(on_login_changed)
        dlg.exec()

    def logout(self) -> None:
        ret = QMessageBox.question(
            self.window, "退出登录",
            "将清除本机保存的微软会话 Cookie。\n历史记录与统计数据不受影响。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        AuthManager().logout()
        self._last_page_enrichment = None
        self.set_login_status("已退出登录", False)
        self._toast("已清除本地登录态")

    # -------------------------------------------------------------- 打开网页

    def open_task_link(self, url: str) -> None:
        """双击任务行时用本机浏览器打开该任务的落地页。"""
        if not open_in_browser(url):
            self._toast("打开浏览器失败，请检查是否安装了浏览器")

    def _pending_tasks(self) -> list:
        """今日待办：未完成 + 有落地页 + **能拿到积分**。"""
        day = day_str()
        return pending_activity_links(self.store.tasks_of_day(day))

    def _start_open_queue(self, items) -> None:
        self._opener.start(list(items))
        self.window.update_running_state()

    def _gap_seconds(self) -> int:
        return random_gap(int(self.settings.get("auto_open_gap_min", 5)),
                          int(self.settings.get("auto_open_gap_max", 10)))

    def _on_waiting(self, next_name: str, gap: int) -> None:
        """刚排上队，先给一条带总间隔的记录（随后倒计时每秒覆盖它）。"""
        total = self._opener.total
        done = self._opener.opened_count
        self._set_open_progress(
            f"已打开 {done}/{total} · {gap} 秒后打开下一个：{next_name}")
        self.window.update_running_state()

    def _on_opened(self, name: str, index: int, total: int) -> None:
        self._log(f"已打开 {index}/{total}：{name}")

    def _on_open_failed(self, name: str, url: str) -> None:
        self._log(f"打开任务页失败：{name}（{url}）")

    def _on_countdown(self, next_name: str, left: int, done: int, total: int) -> None:
        """每秒刷新一次状态栏，显示真实倒计时而不是写死的秒数。"""
        self._set_open_progress(
            f"已打开 {done}/{total} · {left} 秒后打开下一个：{next_name}")

    def _on_open_finished(self, total: int) -> None:
        self.window.update_running_state()
        if total:
            self._set_open_progress(f"已逐个打开 {total} 个任务页，请手动完成")
            self._toast(f"已逐个打开 {total} 个任务页，请在浏览器里手动完成")
        else:
            self._set_open_progress("任务页打开失败，请检查系统默认浏览器")
            self._toast("任务页打开失败，请检查系统默认浏览器设置")

    def _on_open_stopped(self, count: int) -> None:
        self.window.update_running_state()
        self._set_open_progress(f"已停止，共打开 {count} 个任务页")
        self._toast(f"已停止打开，共打开 {count} 个")

    def _set_open_progress(self, text: str) -> None:
        if self.window is not None:
            self.window.set_status_message(text)

    # ------------------------------------------------------------------ 杂项

    def _toast(self, message: str) -> None:
        self.notifier.notify(APP_TITLE, message)

    def _log(self, message: str) -> None:
        print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)

    def _forget_worker(self, worker) -> None:
        if worker in self._workers:
            self._workers.remove(worker)

    def save_settings(self, data: dict) -> bool:
        """集中处理持久化错误，防止界面误报“保存成功”。"""
        try:
            return self.settings.update(data)
        except (OSError, TypeError, ValueError) as exc:
            self._log(f"保存设置失败：{exc}")
            if self.window is not None:
                QMessageBox.warning(self.window, "设置未保存", f"无法保存设置：\n{exc}")
            return False

    def apply_settings(self, data=None) -> None:
        """界面变化后立即落盘；仅重排受影响的计时器。"""
        if self.window is None:
            return
        manual = not isinstance(data, dict)
        if manual:  # “保存设置”按钮也使用同一条逻辑
            data = self.window.account.collect()
        data = dict(data)
        previous = self.settings.as_dict()
        old_autostart = autostart.is_enabled()
        requested_autostart = bool(data.get("launch_at_login", old_autostart))
        autostart_changed = requested_autostart != old_autostart
        if autostart_changed:
            try:
                if not autostart.set_enabled(requested_autostart):
                    raise OSError("系统不允许更新自启动项")
                if autostart.is_enabled() != requested_autostart:
                    raise OSError("系统未确认自启动更改")
            except Exception as exc:
                self._log(f"设置开机自启动失败：{exc}")
                actual = autostart.is_enabled()
                data["launch_at_login"] = actual
                self.window.account.set_autostart(actual)
                QMessageBox.warning(self.window, "开机自启动未生效", str(exc))
        changed = self.save_settings(data)
        if not changed:
            # 保存失败后把系统自启动还原，避免设置与 OS 状态不一致。
            if autostart_changed and self.settings.get("launch_at_login") != autostart.is_enabled():
                autostart.set_enabled(old_autostart)
                self.window.account.set_autostart(old_autostart)
            if manual and all(previous.get(k) == value for k, value in data.items()):
                self._toast("设置已保存")
            return
        interval = int(data.get("sync_interval_minutes") or 0)
        if interval != previous.get("sync_interval_minutes"):
            self._next_sync = (datetime.now() + timedelta(minutes=interval)
                               if interval else None)
        for key, marker in (("auto_open_time", "autoopen"),
                            ("remind_time", "morning"),
                            ("evening_remind_time", "evening")):
            if data.get(key) != previous.get(key):
                self._fired.discard(marker)
        # 已触发的时间保留到次日，即使用户调整设置也不能重复执行同一时刻。
        # 其他设置均由使用方实时读取，无需重启或重新创建定时器。
        if manual:
            self._toast("设置已保存")

    def quit(self) -> None:
        """等待正在运行的线程完成，避免退出时销毁运行中的 QThread。"""
        self._opener.stop()
        if self.activity_running():
            self._activity_worker.request_stop()
        if self.window is not None:
            self.window.runner.stop()
            search_busy = self.window.runner.is_running()
        else:
            search_busy = False
        if search_busy or self.activity_running() or any(w.isRunning() for w in self._workers):
            QTimer.singleShot(500, self.quit)
            return
        if self.tray is not None:
            self.tray.hide()
        self.app.quit()


def main(argv: Optional[list] = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    if "--quit" in argv:
        socket = QLocalSocket()
        socket.connectToServer(SINGLE_INSTANCE_KEY)
        if socket.waitForConnected(300):
            socket.write(b"quit")
            socket.flush()
            socket.waitForBytesWritten(300)
            return 0
    return Application(argv).run()

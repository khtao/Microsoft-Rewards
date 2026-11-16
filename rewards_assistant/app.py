"""应用控制器：托盘、调度器、同步线程与窗口生命周期。

唯一界面的控制器：``_ensure_window()`` 创建 ``ui.modern.ModernMainWindow``，
托盘「自动完成未完成任务」与主界面主按钮都落到 ``run_pending_tasks()``，
执行链路与 1.3.0 经典界面完全一致。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .ui.modern import ModernMainWindow

from PySide6.QtCore import QObject, QTimer, Qt
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
        self._sleep_day = str(self.settings.get("sleep_until_day", "") or "")
        self._pending_sleep = False
        self.notifier: Optional[Notifier] = None
        self.window: Optional["ModernMainWindow"] = None
        self.tray: Optional[QSystemTrayIcon] = None
        # run() 里才会设为真正的 QApplication；提前关闭窗口时 quit() 也能安全兜底。
        self.app: Optional[QApplication] = None
        self._workers: list = []
        # 窗口可能在调度器建好前就保存设置；先给安全默认值。
        self._fired: set = set()
        self._next_sync: Optional[datetime] = None
        self._last_points: Optional[int] = None
        self._last_done_notified_day = ""
        self._after_sync: set[str] = set()
        # 整轮运行结束后无条件补一次同步；仅用此标志去重，避免连发两次。
        self._post_run_sync_pending = False
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
        # X11 下用此 desktop id 把运行中的窗口绑定到启动器/任务栏图标。
        # Wayland 由 compositor 按 app_id 自行注册，重复调用会让 xdg portal 报
        # "Connection already associated with an application ID"，故跳过。
        if (sys.platform.startswith("linux")
                and str(app.platformName()).lower() != "wayland"):
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
        self._build_scheduler()
        if self._sleep_day == datetime.now().date().isoformat():
            # 真正释放 QWidget / Qt 图表；保留一个极轻量的 QTimer 和单实例 IPC。
            self._log("今日任务已经完成：低内存守护中，跨天自动恢复托盘")
            return app.exec()
        self._sleep_day = ""
        self.settings.set("sleep_until_day", "")
        self._ensure_window()
        self._build_tray()
        if not self.start_minimized or self.tray is None:
            self.window.show()
        else:
            self._log("已随系统启动，隐藏到托盘")

        self._migrate_labels()
        self._prune_zero_tasks()
        if self.settings.get("sync_interval_minutes", 0):
            # 初次自动同步本身就会校验登录态，避免再发一次重复请求；
            # 两页并发下载后首屏可更快拿到真值，无需再等 3 秒。
            self.set_login_status("等待首次同步验证…", None)
            # 纯 HTTP 同步没有冷启动，窗口一显示就立即取数。
            QTimer.singleShot(0, lambda: self.sync_now(auto=True))
        else:
            self.check_login_status()
        if self.settings.get("sleep_when_all_done", False) and self._all_done():
            QTimer.singleShot(0, self.sleep_today)
        return app.exec()

    def _ensure_window(self) -> None:
        if self.window is not None:
            return
        # 唯一界面：截图复刻的单列窗口。它实现了 refresh_all / set_busy /
        # quick / runner / search / account 等 MainWindow 的门面接口，
        # 因此本控制器（含 run_pending_tasks 自动链路）逐字无需改动。
        from .ui.modern import ModernMainWindow
        self.window = ModernMainWindow(self)
        self.window.runner.finished.connect(
            lambda _report: self._on_auto_run_finished())

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
        self.notifier.attach(self.tray)

        # 设定父窗口并保存引用，避免无父级 QMenu 在函数结束后被释放。
        # 按需求只保留 4 项，且不再显示任何鼠标悬浮提示（托盘 tooltip 已移除）。
        self._tray_menu = QMenu(self.window)
        menu = self._tray_menu
        menu.addAction("显示主页面").triggered.connect(self.show_window)
        self.act_tray_sync = menu.addAction("立即同步")
        self.act_tray_sync.triggered.connect(self.window.act_sync.trigger)
        self.act_tray_run = menu.addAction("自动完成任务")
        self.act_tray_run.triggered.connect(self._on_tray_run)
        menu.addAction("退出").triggered.connect(self.quit)
        menu.aboutToShow.connect(self.update_tray_actions)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()
        self.update_tray_actions()

    def auto_running(self) -> bool:
        """自动运行期间：浏览器任务（搜索 / 活动点击 / 逐个打开）正在执行。"""
        runner = getattr(getattr(self, "window", None), "runner", None)
        if runner is not None and bool(runner.is_running()):
            return True
        activity = getattr(self, "activity_running", None)
        if activity is not None and activity():
            return True
        if bool(getattr(getattr(self, "_opener", None), "running", False)):
            return True
        return False

    def _on_tray_run(self) -> None:
        """托盘同一入口：空闲启动、运行期停止，绝不误触发第二次运行。"""
        if self.window is None:
            return
        if self.auto_running():
            self.window.stop_running()
        else:
            self.window.quick.tasks_btn.click()

    def update_tray_actions(self) -> None:
        """运行期：立即同步禁用，自动完成任务改「停止自动运行」。"""
        if self.tray is None or self.window is None:
            return
        running = self.auto_running()
        self.act_tray_sync.setEnabled(self.window.act_sync.isEnabled() and not running)
        self.act_tray_run.setText("停止自动运行" if running else "自动完成任务")
        # 停止请求已发出但线程尚未收尾时禁用，防止连点；其余时刻始终可点。
        self.act_tray_run.setEnabled(
            not getattr(self.window, "_stopping", False)
            and (running or self.window.act_sync.isEnabled()))

    def _on_auto_run_finished(self) -> None:
        """所有浏览器任务结束：解除同步限制并补一次同步。"""
        self._log("自动运行已结束，解除同步限制")
        self._request_post_run_sync()

    def _request_post_run_sync(self) -> None:
        """运行结束后统一同步一次；多处结束信号共用，去重避免连发两次。"""
        if getattr(self, "_post_run_sync_pending", False):
            return
        self._post_run_sync_pending = True
        QTimer.singleShot(2500, self._do_post_run_sync)

    def _do_post_run_sync(self) -> None:
        self._post_run_sync_pending = False
        self.sync_now(auto=True)

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.DoubleClick,
                      QSystemTrayIcon.ActivationReason.Trigger):
            self.show_window()

    def show_window(self) -> None:
        if self._sleep_day:
            self._wake_from_sleep(show=True)
            return
        self._ensure_window()
        if self.tray is None:
            self._build_tray()
        if self.window.isVisible():
            self.window.raise_()
            self.window.activateWindow()
        else:
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()

    # ------------------------------------------------------------------ 调度

    def _build_scheduler(self) -> None:
        self.timer = QTimer(self)
        self.timer.setInterval(30_000)
        self.timer.setTimerType(Qt.TimerType.VeryCoarseTimer)
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
        if getattr(self, "_sleep_day", ""):
            if now.date().isoformat() != self._sleep_day:
                self._wake_from_sleep(show=False)
            else:
                return

        # 跨天：重置提醒与已触发标记、刷新界面
        if now.date() != self._today:
            self._today = now.date()
            self._fired.clear()
            self._remember_run_day(now)
            self.window.refresh_all()
            self.notifier.notify(APP_TITLE, "新的一天，今日任务已重置，加油 💪")

        # 自动同步
        interval = int(self.settings.get("sync_interval_minutes") or 0)
        if interval and self._next_sync and now >= self._next_sync:
            self._next_sync = now + timedelta(minutes=interval)
            self.sync_now(auto=True)
        elif interval and self._next_sync is None:
            self._next_sync = now + timedelta(minutes=interval)

        clock = now.strftime("%H:%M")
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

    def open_pending_pages(self) -> None:
        """手动逐个打开未完成任务页；不再与定时自动运行重复调度。"""
        if self.auto_running():
            self._log("自动运行正在执行，忽略打开任务页请求")
            return
        if self._all_done():
            self._log("任务已全部完成，无需再打开任务页")
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
        # 运行期一律忽略新的启动意图，且不排队 _after_sync —— 否则定时时间点
        # 会在本轮结束后又拉起一轮，属于典型的误操作放大。
        if self.auto_running():
            self._log("自动运行正在执行，忽略本次启动请求")
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
        """使用真实 Edge 鼠标点击侧栏卡片；整轮结束后统一同步核验。"""
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
            gap_range=(5, 10),
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
                  f"操作失败 {len(stats['unconfirmed'])}，未找到 {len(stats['missing'])}；"
                  f"状态将在运行结束后统一同步核验")
        if stats.get("error"):
            self._log("活动点击错误：" + stats["error"])
        if stats.get("stopped"):
            self._activity_then_search = False
            self._set_open_progress("已停止活动自动点击")
        else:
            self._set_open_progress(
                f"活动点击 {stats['clicked']}/{stats['total']}；"
                f"运行结束后统一同步核验")
            if self._activity_then_search:
                # 不再插一次同步：直接续跑电脑端搜索，整轮结束才同步。
                self._activity_then_search = False
                self._start_pc_search()
        self.window.update_running_state()

    def _on_activity_worker_exited(self, worker) -> None:
        if self._activity_worker is worker:
            self._activity_worker = None
        if self.window is not None:
            self.window.update_running_state()
        if self._pending_sleep and not self._is_busy():
            QTimer.singleShot(0, self.sleep_today)

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
        # 任务剩余分与账户余额净变化是不同指标，必须按任务逐项计算。
        tasks = self.store.tasks_of_day(day_str())
        left_points = sum(max(0, int(t["points_max"]) - int(t["points_earned"]))
                          for t in tasks)
        left_tasks = max(0, int(row["tasks_total"]) - int(row["tasks_done"]))
        title = "早间打卡提醒" if key == "morning" else "晚间补做提醒"
        self.notifier.notify(APP_TITLE, f"{title}：还剩 {left_tasks} 项 / 约 {left_points} 分")

    # ------------------------------------------------------------------ 同步

    def sync_now(self, auto: bool = False) -> None:
        if getattr(self, "_sleep_day", ""):
            if auto:
                return
            self._wake_from_sleep(show=False)
        if getattr(self, "_syncing", False):
            if not auto:
                self._toast("同步正在进行中，请稍候…")
            return
        # 自动运行期间禁止同步：手动入口给明确提示，定时/后台入口静默跳过，
        # 等本轮运行结束后由 _request_post_run_sync() 统一补一次。
        if self.auto_running():
            if not auto:
                self._toast("自动运行期间禁止同步，结束后会自动同步一次")
            return
        self._syncing = True
        self.window.set_busy(True, "正在同步 Rewards 数据…")
        # 兼容旧版「页面补充」冷却字段；两页 HTML 现在每次同步都会并发读取，
        # 该开关不再触发任何额外网络请求。
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

        earned = int(summary.get("points_earned") or 0)
        from .points import points_display
        total = int(summary.get("points_max") or 0)
        available, _level, _official = self.store.latest_available()
        earned_text = points_display(summary.get("points_source", "task_fallback"), earned)
        text = f"{earned_text} · 任务 {snap.tasks_done}/{len(snap.tasks)}（任务上限约 {total}）"
        if snap.balance_valid:
            text += f" · 账户可用 {available:,}"
        self._log(text)
        # 页面「可领取」只提醒、不代领；降级 / 结构变化提示如实展示
        extra = payload.get("message") or ""
        if extra and extra != "同步完成（只读，进度以官网为准）":
            self._log(extra)

        if self.settings.get("notify_after_sync", True):
            if summary.get("all_done"):
                # 自动同步可能每小时运行，完成提醒一天只发送一次。
                today = day_str()
                if self._last_done_notified_day != today:
                    self.notifier.notify(APP_TITLE, f"今日任务已全部完成 🎉  {text}")
                    self._last_done_notified_day = today
            elif self._last_points is not None and earned > self._last_points:
                self.notifier.notify(APP_TITLE, f"今日积分变化：{text}")
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
        if self._all_done() and (self._pending_sleep or
                self.settings.get("sleep_when_all_done", False)):
            QTimer.singleShot(0, self.sleep_today)

    def _on_sync_fail(self, message: str) -> None:
        self._syncing = False
        self._sync_enrich_current = False
        self._after_sync.clear()
        self.window.update_running_state()
        self.window.set_busy(False, message)
        self._log(message)
        if "Cookie" in message or "未登录" in message or "会话" in message:
            self.set_login_status("未登录或会话已过期", False)
        else:
            self.set_login_status("网络或服务异常（保留登录状态）", None)
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
            # 会话更新后，下次同步应重新完整读取两页。
            if ok:
                self._last_page_enrichment = None
            self.set_login_status("已登录，可直接同步" if ok else "未登录", ok)

        dlg.login_state_changed.connect(on_login_changed)
        dlg.exec()

    def logout(self) -> None:
        ret = QMessageBox.question(
            self.window, "退出登录",
            "将清除本应用独立浏览器配置中的登录会话（Cookie 与本地存储），"
            "并删除旧版明文 Cookie 文件。\n"
            "不会改动你日常使用的 Edge 配置，历史记录与统计数据不受影响。",
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
        return random_gap(5, 10)

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
        if self._pending_sleep and not self._is_busy():
            QTimer.singleShot(0, self.sleep_today)

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
        for key, marker in (("remind_time", "morning"),
                            ("evening_remind_time", "evening")):
            if data.get(key) != previous.get(key):
                self._fired.discard(marker)
        # 已触发的时间保留到次日，即使用户调整设置也不能重复执行同一时刻。
        # 其他设置均由使用方实时读取，无需重启或重新创建定时器。
        if manual:
            self._toast("设置已保存")
        if (data.get("sleep_when_all_done") and not previous.get("sleep_when_all_done")
                and self._all_done()):
            QTimer.singleShot(0, self.sleep_today)

    def _is_busy(self) -> bool:
        return bool((self.window is not None and self.window.runner.is_running())
                    or self.activity_running() or getattr(self, "_syncing", False)
                    or self._opener.running
                    or any(worker.isRunning() for worker in self._workers))

    def sleep_today(self) -> None:
        """不是退出进程：回收窗口/托盘，保留跨天定时器与单实例 IPC。"""
        if not self._all_done():
            self._toast("只有官方任务全部完成后才能休眠；请先同步核验任务")
            return
        if self._is_busy():
            self._pending_sleep = True
            self._log("等待正在运行的操作安全结束后休眠")
            return
        self._pending_sleep = False
        today = datetime.now().date().isoformat()
        if not self.save_settings({"sleep_until_day": today}):
            if self.settings.get("sleep_until_day") != today:
                return
        self._sleep_day = today
        if self.tray is not None:
            self.tray.hide()
            self.tray.setContextMenu(None)
            self.notifier.attach(None)
            self.tray.deleteLater()
            self.tray = None
        if self.window is not None:
            window = self.window
            window.hide()
            self.window = None
            window.deleteLater()
        self._tray_menu = None
        self._after_sync.clear()
        self._log("已进入低内存休眠，进程继续值守，次日自动恢复托盘")

    def _wake_from_sleep(self, *, show: bool) -> None:
        self._sleep_day = ""
        self._pending_sleep = False
        self._last_points = None
        self.save_settings({"sleep_until_day": ""})
        self._today = datetime.now().date()
        self._fired.clear()
        self._next_sync = None
        interval = int(self.settings.get("sync_interval_minutes") or 0)
        if interval:
            self._next_sync = datetime.now() + timedelta(minutes=interval)
        self._ensure_window()
        self._build_tray()
        self._migrate_labels()
        self._prune_zero_tasks()
        self.window.refresh_all()
        if show or self.tray is None:
            self.window.show()
        # 次日恢复后同步一次当前积分，无须等待定时间隔。
        QTimer.singleShot(500, lambda: self.sync_now(auto=True))
        self._log("已跨天唤醒并恢复托盘")

    def quit(self) -> None:
        """等待正在运行的线程完成，避免退出时销毁运行中的 QThread。"""
        if self.auto_running():
            self._log("退出前先停止自动运行，等待线程安全收尾…")
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
        app = self.app or QApplication.instance()
        if app is not None:
            app.quit()


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

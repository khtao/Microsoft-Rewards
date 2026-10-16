"""应用控制器：托盘、调度器、同步线程与窗口生命周期。"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from typing import Optional

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import autostart
from .auth import AuthManager
from .config import APP_TITLE, APP_VERSION, Settings
from .fonts import bootstrap, pick_qt_font_family
from .local_browser import open_url as open_in_browser
from .notifier import Notifier
from .opener import PendingOpener, random_gap
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
            "Tencent.MSRewardsAssistant")
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
        self._open_after_sync = False

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
        self._build_tray()
        self._build_scheduler()

        if not self.start_minimized or self.tray is None:
            self.window.show()
        else:
            self._log("已随系统启动，隐藏到托盘")

        self._migrate_labels()
        self._prune_zero_tasks()
        self.check_login_status()
        if self.settings.get("sync_interval_minutes", 0):
            QTimer.singleShot(3000, lambda: self.sync_now(auto=True))
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
        """托盘仅暴露主窗口已有的业务操作；操作绑定同一 UI 入口。"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._log("当前环境没有系统托盘，改为显示主窗口，通知将回落到系统命令行工具")
            return
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

        # 定时自动打开未完成任务页
        when = self.settings.get("auto_open_time") or ""
        if when and "autoopen" not in self._fired and now.strftime("%H:%M") == when:
            self._fired.add("autoopen")
            self._auto_open_morning()

        # 定点提醒
        for key, setting_key in (("morning", "remind_time"),
                                 ("evening", "evening_remind_time")):
            when = self.settings.get(setting_key) or ""
            if not when or key in self._fired:
                continue
            if now.strftime("%H:%M") == when:
                self._fired.add(key)
                self._remind(key)

    def _auto_open_morning(self) -> None:
        """到点自动打开未完成任务页；今天还没数据就先同步再打开。"""
        pending = self._pending_tasks()
        if self.store.daily(day_str()) is None or not pending:
            self._log("到点处理任务：先同步一次今日数据")
            self._open_after_sync = True
            self.sync_now(auto=True)
            return
        self._log(f"到点自动打开：{len(pending)} 个待办")
        self._start_open_queue([(r["name"], r["link"]) for r in pending])
        self._auto_run_searches()

    def _auto_run_searches(self) -> None:
        """仅在开关开启且确有搜索积分未完成时启动搜索。"""
        if not self.settings.get("auto_run_pending", False) or self.window is None:
            return
        tasks = self.store.tasks_of_day(day_str())
        if any(t["key"] in ("pc_search", "mobile_search") and not t["complete"]
               and int(t["points_max"]) > int(t["points_earned"]) for t in tasks):
            self.window.run_pending_tasks()

    def _remind(self, key: str) -> None:
        row = self.store.daily(day_str())
        if row is not None and row["all_done"] and self.settings.get("silent_when_all_done", True):
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
        worker = SyncWorker(self.store, day_str(), self)
        worker.progress.connect(lambda msg: self.window.set_busy(True, msg))
        worker.succeeded.connect(self._on_sync_ok)
        worker.failed.connect(self._on_sync_fail)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda: self._forget_worker(worker))
        worker.start()
        self._workers.append(worker)

    def _on_sync_ok(self, payload: dict) -> None:
        self._syncing = False
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

        if self._open_after_sync:
            self._open_after_sync = False
            pending = self._pending_tasks()
            if pending:
                self._start_open_queue([(r["name"], r["link"]) for r in pending])
            else:
                self._log("同步后没有待办任务，无需打开")
            self._auto_run_searches()

    def _on_sync_fail(self, message: str) -> None:
        self._syncing = False
        self._open_after_sync = False
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
        dlg.login_state_changed.connect(
            lambda ok: self.set_login_status("已登录，可直接同步" if ok else "未登录", ok))
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
        return [r for r in self.store.tasks_of_day(day)
                if not r["complete"] and r["link"]
                and (int(r["points_max"]) > 0 or int(r["points_earned"]) > 0)]

    def _start_open_queue(self, items) -> None:
        self._opener.start(list(items))

    def _gap_seconds(self) -> int:
        return random_gap(int(self.settings.get("auto_open_gap_min", 5)),
                          int(self.settings.get("auto_open_gap_max", 10)))

    def _on_waiting(self, next_name: str, gap: int) -> None:
        """刚排上队，先给一条带总间隔的记录（随后倒计时每秒覆盖它）。"""
        total = self._opener.total
        done = self._opener.opened_count
        self._set_open_progress(
            f"已打开 {done}/{total} · {gap} 秒后打开下一个：{next_name}")

    def _on_opened(self, name: str, index: int, total: int) -> None:
        self._log(f"已打开 {index}/{total}：{name}")

    def _on_open_failed(self, name: str, url: str) -> None:
        self._log(f"打开任务页失败：{name}（{url}）")

    def _on_countdown(self, next_name: str, left: int, done: int, total: int) -> None:
        """每秒刷新一次状态栏，显示真实倒计时而不是写死的秒数。"""
        self._set_open_progress(
            f"已打开 {done}/{total} · {left} 秒后打开下一个：{next_name}")

    def _on_open_finished(self, total: int) -> None:
        if total:
            self._set_open_progress(f"已逐个打开 {total} 个任务页，请手动完成")
            self._toast(f"已逐个打开 {total} 个任务页，请在浏览器里手动完成")
        else:
            self._set_open_progress("任务页打开失败，请检查系统默认浏览器")
            self._toast("任务页打开失败，请检查系统默认浏览器设置")

    def _on_open_stopped(self, count: int) -> None:
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
            if manual and self.settings.as_dict() == previous:
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
        # 其他设置均由使用方实时读取，无需重启或重新创建定时器。
        if manual:
            self._toast("设置已保存")

    def quit(self) -> None:
        """等待正在运行的线程完成，避免退出时销毁运行中的 QThread。"""
        self._opener.stop()
        if self.window is not None:
            self.window.runner.stop()
            search_busy = self.window.runner.is_running()
        else:
            search_busy = False
        if search_busy or any(w.isRunning() for w in self._workers):
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

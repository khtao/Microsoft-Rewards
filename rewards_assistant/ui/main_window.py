#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主窗口：今日 / 趋势 / 历史 / 账户与设置。

拟人搜索的运行参数、启动按钮和日志整合到账户与设置；
今日页的电脑端搜索与定时自动运行共用同一个 SearchRunner。
"""
from __future__ import annotations

import re
import traceback
from datetime import date
from typing import Optional

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QFont, QKeySequence, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton, QSystemTrayIcon,
    QStatusBar, QTabWidget, QTextEdit, QToolBar, QVBoxLayout, QWidget,
)

from ..config import APP_TITLE, APP_VERSION
from .chart_view import ChartView
from .controls import NoWheelComboBox, NoWheelSpinBox
from .icons import app_icon
from .panels import AccountPanel, HistoryPanel, QuickPanel, StatsRow
from .task_panel import TaskPanel

# 拟人搜索运行能力
from ..api import DEFAULT_LOG, run_human_search
from ..browser import find_edge_binary, is_root
from ..trends import HTTP_SOURCES
from ..pending import pending_desktop_rounds

ALL_SOURCES = tuple(HTTP_SOURCES.keys()) if isinstance(HTTP_SOURCES, dict) else \
    ("baidu", "weibo", "toutiao", "zhihu", "bing", "sina")

LEVEL_TIP = {1: "1 机械（对照基线）", 2: "2 普通用户（默认）", 3: "3 高拟人（慢、抖动大）"}


# ============================================================ 拟人搜索运行内核
class SearchSignals(QObject):
    log = Signal(str, str)
    progress = Signal(int, int)
    failed = Signal(str)
    finished = Signal(object)


class SearchWorker(QThread):
    """在子线程跑 run_human_search，不阻塞界面。"""

    def __init__(self, params: dict, parent=None):
        super().__init__(parent)
        self.params = params
        self.sig = SearchSignals()
        self._stop = False
        self.report = None

    def request_stop(self):
        self._stop = True

    def _on_log(self, rec: dict):
        action = str(rec.get("action", ""))
        detail = str(rec.get("detail", ""))
        self.sig.log.emit(action, detail)
        if action == "search_start":
            m = re.search(r"第\s*(\d+)\s*/\s*(\d+)\s*次", detail)
            if m:
                self.sig.progress.emit(int(m.group(1)), int(m.group(2)))

    def run(self):
        try:
            self.report = run_human_search(
                log_callback=self._on_log,
                stop_check=lambda: self._stop,
                verbose=False,
                **self.params,
            )
            self.sig.finished.emit(self.report)
        except Exception as e:
            self.sig.failed.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=4)}")


class SearchRunner(QObject):
    """拟人搜索运行内核，电脑端搜索与手动启动共用。"""

    status = Signal(str)
    log = Signal(str, str)
    progress = Signal(int, int)
    finished = Signal(object)
    failed = Signal(str)
    running_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None

    @staticmethod
    def defaults() -> dict:
        return dict(
            repeat=5, level=2,
            round_seconds=(16.0, 29.0),
            gap_seconds=(3.0, 12.0),
            click_prob=0.35,
            seed=None,
            keywords=None,
            keyword_sources=ALL_SOURCES,
            headless=False,
            copy_profile=False,
            keep_open=0.0,
            log_file=str(DEFAULT_LOG),
        )

    @staticmethod
    def for_pending(store, settings: dict) -> dict:
        """根据「未完成任务」推算本轮参数：轮数 = 还差几次搜索。"""
        p = dict(SearchRunner.defaults())
        p.update(settings)
        day = date.today().isoformat()
        row = store.daily(day)
        if row is None or row["all_done"]:
            return None
        tasks = store.tasks_of_day(day)
        # 只运行电脑端搜索：Edge 桌面浏览器不能冒充移动搜索进度。
        need = pending_desktop_rounds(tasks)
        if need <= 0:
            return None
        p["repeat"] = min(need, 200)
        return p

    def is_running(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def start(self, params: dict) -> bool:
        if self.is_running():
            return False
        self.worker = SearchWorker(params, self)
        w = self.worker
        w.sig.log.connect(lambda a, d: self.status.emit(f"{a} {d}".strip()[:120]))
        w.sig.log.connect(self.log.emit)
        w.sig.progress.connect(self.progress.emit)
        w.sig.finished.connect(self._on_finished)
        w.sig.failed.connect(self.failed.emit)
        w.finished.connect(lambda: self._clear_worker(w))
        w.finished.connect(w.deleteLater)
        self.status.emit(f"启动浏览器…共 {params.get('repeat', '?')} 轮")
        w.start()
        self.running_changed.emit(True)
        return True

    def _clear_worker(self, worker):
        if self.worker is worker:
            self.worker = None
            self.running_changed.emit(False)

    def stop(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.request_stop()
            self.status.emit("正在停止，等当前这一轮结束…")

    def _on_finished(self, report):
        self.finished.emit(report)
        self.status.emit(
            f"完成：成功 {report.ok_count} / 失败 {report.fail_count}，"
            f"点击 {report.clicked_count} 次，用时 {report.total_seconds:.1f}s")

    def shutdown(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.request_stop()
            self.worker.wait(5000)


# ================================================================ 主窗口
class MainWindow(QMainWindow):
    """程序主界面。业务动作转发给 app（Application 控制器）。"""

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self.store = app.store
        self.setWindowTitle(f"{APP_TITLE}  v{APP_VERSION}")
        self.setWindowIcon(app_icon())
        self.resize(1040, 720)
        self.setMinimumSize(880, 600)

        # 拟人搜索内核：今日、计划任务、手动启动均共用。
        self.runner = SearchRunner(self)

        self.stats = StatsRow()
        self.tasks = TaskPanel(self.store, date.today().isoformat())
        self.tasks.changed.connect(self.on_data_changed)
        self.tasks.open_link.connect(self.app.open_task_link)
        self.quick = QuickPanel()
        self.chart = ChartView(self.store, settings=app.settings, save_settings=app.save_settings)
        self.history = HistoryPanel(self.store)
        self.search = SearchPanel(self, self.runner, app.settings, app.save_settings)
        self.account = AccountPanel(app, search_panel=self.search)

        today_tab = QWidget()
        lay = QVBoxLayout(today_tab)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stats)
        lay.addWidget(self.quick)
        lay.addWidget(self.tasks, 1)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(today_tab, "今日")
        self.tabs.addTab(self.chart, "趋势")
        self.tabs.addTab(self.history, "历史")
        self.tabs.addTab(self.account, "账户与设置")
        self.setCentralWidget(self.tabs)
        self._detail_dirty = {"chart": True, "history": True}
        self.tabs.currentChanged.connect(self._refresh_active_detail)

        # 今日页「自动完成」按钮 → 主窗口入口
        self.quick.run_pending_requested.connect(self.app.run_pending_tasks)
        self.account.settings_changed.connect(self.app.apply_settings)
        self.quick.stop_requested.connect(self.stop_running)

        self._build_toolbar()
        self._build_statusbar()
        self._wire_runner()
        self.refresh_all()

    # ------------------------------------------------------------------ 组装

    def _build_toolbar(self) -> None:
        bar = QToolBar("主工具栏")
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(bar)

        act_sync = QAction("立即同步", self)
        act_sync.setShortcut(QKeySequence("F5"))
        act_sync.triggered.connect(lambda: self.app.sync_now())
        act_refresh = QAction("刷新界面", self)
        act_refresh.triggered.connect(self.refresh_all)

        # 主界面不展示“立即同步 / 刷新界面”。保留隐藏 QAction：
        # F5、系统托盘菜单及同步完成后的后台刷新不受影响。
        self.addAction(act_sync)
        self.addAction(act_refresh)
        self.act_sync = act_sync
        self.act_refresh = act_refresh

    def _build_statusbar(self) -> None:
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status_left = QLabel("就绪")
        self.status.addWidget(self.status_left, 1)
        self.run_bar = QProgressBar()
        self.run_bar.setRange(0, 100)
        self.run_bar.setValue(0)
        self.run_bar.setFixedWidth(160)
        self.run_bar.setTextVisible(True)
        self.run_bar.setFormat("")
        self.run_bar.hide()
        self.status.addPermanentWidget(self.run_bar)

    def _wire_runner(self):
        """把运行内核的信号接到主窗口状态栏。"""
        self.runner.status.connect(self.status_left.setText)
        self.runner.progress.connect(self._on_run_progress)
        self.runner.finished.connect(self._on_run_finished)
        self.runner.failed.connect(self._on_run_failed)
        self.runner.running_changed.connect(self._on_runner_activity)

    def _on_runner_activity(self, active: bool) -> None:
        self.update_running_state()

    def update_running_state(self):
        busy = (self.runner.is_running() or self.app._opener.running
                or self.app.activity_running() or bool(self.app._after_sync))
        self.quick.set_busy(busy)
        self.app.update_tray_actions()

    def _on_run_progress(self, cur: int, total: int):
        self.run_bar.show()
        self.run_bar.setRange(0, max(1, total))
        self.run_bar.setValue(cur)
        self.run_bar.setFormat(f"{cur}/{total}")

    def _on_run_finished(self, report):
        self.run_bar.hide()

    def _on_run_failed(self, msg: str):
        self.run_bar.hide()
        self.status_left.setText(f"运行失败：{msg[:80]}")

    # ------------------------------------------------------------------ 刷新

    def refresh_all(self) -> None:
        self.stats.update(self.store)
        self.tasks.set_day(date.today().isoformat())
        self.quick.update(self.store)
        self._detail_dirty = {"chart": True, "history": True}
        self._refresh_active_detail()
        self._update_status()

    def _refresh_active_detail(self, *_args) -> None:
        if self.tabs.currentWidget() is self.chart and self._detail_dirty.get("chart"):
            self.chart.refresh()
            self._detail_dirty["chart"] = False
        elif self.tabs.currentWidget() is self.history and self._detail_dirty.get("history"):
            self.history.reload()
            self._detail_dirty["history"] = False

    def on_data_changed(self) -> None:
        self.stats.update(self.store)
        self.quick.update(self.store)
        self._detail_dirty = {"chart": True, "history": True}
        self._refresh_active_detail()
        self._update_status()
        self.app.update_tray_tooltip()

    def _update_status(self) -> None:
        row = self.store.daily(date.today().isoformat())
        if row is None:
            self.status_left.setText("今天还没有记录 —— 点\"立即同步\"拉取官方数据")
            return
        from ..points import points_display
        today = points_display(row["points_source"], int(row["points_earned"]))
        self.status_left.setText(
            f"{today} · 任务 {row['tasks_done']}/{row['tasks_total']} · "
            f"连续 {row['streak']} 天 · "
            f"上次同步 {(row['synced_at'] or '手动').replace('T', ' ')}"
        )

    def set_busy(self, busy: bool, text: str = "") -> None:
        self.act_sync.setEnabled(not busy)
        self.app.update_tray_actions()
        self.set_status_message(text)

    def set_status_message(self, text: str) -> None:
        """仅更新提示，不意外结束另一操作的忙碌状态。"""
        self.status_left.setText(text or "就绪")

    def set_login_status(self, text: str, ok: Optional[bool] = None) -> None:
        self.account.set_status(text, ok)

    # ------------------------------------------------------------- 对外入口

    def run_pending_tasks(self, settings: Optional[dict] = None) -> bool:
        """仅执行未完成的电脑端搜索；遵守统一的拟人搜索参数。"""
        if self.runner.is_running():
            self.set_status_message("电脑端拟人搜索正在执行，跳过重复启动")
            return False
        config = settings if settings is not None else self.search._collect()
        params = SearchRunner.for_pending(self.store, config)
        if params is not None and not params["keyword_sources"] and not params["keywords"]:
            self.set_status_message("搜索词来源为空，请在账户与设置中选择热搜来源或填写关键词")
            return False
        if params is None:
            self.set_status_message("当前没有未完成的电脑端搜索，不启动拟人搜索")
            return False
        return self.runner.start(params)

    def stop_running(self):
        self.app.cancel_pending_actions()
        self.runner.stop()
        self.app._opener.stop()
        self.update_running_state()

    # ------------------------------------------------------------------ 关闭

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.app.settings.get("close_to_tray", True) and QSystemTrayIcon.isSystemTrayAvailable():
            event.ignore()
            self.hide()
            if not getattr(self, "_tray_hint_shown", False):
                self._tray_hint_shown = True
                self.app.notifier.notify(
                    APP_TITLE, "已最小化到系统托盘，双击图标可重新打开", timeout_ms=4000)
        else:
            event.accept()
            QTimer.singleShot(0, self.app.quit)

    def really_quit(self) -> None:
        self.runner.shutdown()
        self.app.quit()


# ============================================================ 账户页内的拟人搜索设置
class SearchPanel(QWidget):
    """电脑端搜索的统一参数设置和运行日志，嵌入账户与设置页。"""

    def __init__(self, parent=None, runner: Optional[SearchRunner] = None,
                 settings=None, save_settings=None):
        super().__init__(parent)
        self.runner = runner
        self.settings = settings
        self.save_settings = save_settings

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 10)
        root.setSpacing(10)

        root.addWidget(self._build_params())
        root.addLayout(self._build_button_row())
        root.addWidget(self._build_log(), 1)

        self._load_settings()
        self._wire_settings()
        self._refresh_env()
        self._on_idle()

        if self.runner is not None:
            # 状态与进度统一显示在主窗口状态栏（MainWindow._wire_runner 已连接）
            self.runner.log.connect(self._append_log)
            self.runner.finished.connect(self._on_finished)
            self.runner.failed.connect(self._on_failed)
            self.runner.running_changed.connect(self._on_runner_state)

    # ---------- 参数区
    def _build_params(self) -> QGroupBox:
        """只保留日常确有必要调整的参数；浏览器安全/时序参数固定。"""
        box = QGroupBox("电脑端搜索（简洁设置）")
        g = QGridLayout(box)
        g.setVerticalSpacing(8)
        self.spin_repeat = NoWheelSpinBox()
        self.spin_repeat.setRange(1, 200)
        self.spin_repeat.setValue(5)
        self.spin_repeat.setSuffix(" 轮")
        self.cmb_level = NoWheelComboBox()
        self.cmb_level.addItems([LEVEL_TIP[i] for i in (1, 2, 3)])
        self.cmb_level.setCurrentIndex(1)
        self.edt_keywords = QLineEdit()
        self.edt_keywords.setPlaceholderText("可选：额外关键词，用逗号分隔；留空则使用热搜")
        g.addWidget(QLabel("搜索轮数"), 0, 0)
        g.addWidget(self.spin_repeat, 0, 1)
        g.addWidget(QLabel("搜索节奏"), 0, 2)
        g.addWidget(self.cmb_level, 0, 3)
        g.addWidget(QLabel("额外关键词"), 1, 0)
        g.addWidget(self.edt_keywords, 1, 1, 1, 3)
        src = QHBoxLayout()
        src.addWidget(QLabel("热搜来源"))
        self.chk_sources = {}
        for name in ALL_SOURCES:
            cb = QCheckBox(name)
            cb.setChecked(True)
            self.chk_sources[name] = cb
            src.addWidget(cb)
        src.addStretch(1)
        g.addLayout(src, 2, 0, 1, 4)
        note = QLabel("浏览器在运行结束后自动关闭；其余高级参数采用固定默认值，避免误操作。")
        note.setWordWrap(True)
        g.addWidget(note, 3, 0, 1, 4)
        return box

    def _view_state(self) -> dict:
        return {
            "repeat": self.spin_repeat.value(),
            "level": self.cmb_level.currentIndex() + 1,
            "keywords": self.edt_keywords.text(),
            "keyword_sources": [name for name, cb in self.chk_sources.items() if cb.isChecked()],
        }

    def _load_settings(self) -> None:
        values = self.settings.get("search_panel", {}) if self.settings else {}
        if not isinstance(values, dict):
            return
        try:
            self.spin_repeat.setValue(int(values.get("repeat", 5)))
            level = int(values.get("level", 2))
        except (TypeError, ValueError, OverflowError):
            level = 2
        self.cmb_level.setCurrentIndex(max(0, min(2, level - 1)))
        if isinstance(values.get("keywords"), str):
            self.edt_keywords.setText(values["keywords"])
        sources = values.get("keyword_sources")
        if isinstance(sources, (list, tuple)):
            for name, cb in self.chk_sources.items():
                cb.setChecked(name in sources)

    def _wire_settings(self) -> None:
        self.spin_repeat.valueChanged.connect(self._save_settings)
        self.cmb_level.currentIndexChanged.connect(self._save_settings)
        self.edt_keywords.textChanged.connect(self._save_settings)
        for cb in self.chk_sources.values():
            cb.toggled.connect(self._save_settings)

    def _save_settings(self, *_args) -> None:
        if self.settings is not None:
            if self.save_settings is not None:
                self.save_settings({"search_panel": self._view_state()})
            else:
                self.settings.set("search_panel", self._view_state())

    # ---------- 按钮行
    def _build_button_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.btn_run = QPushButton("运行")
        self.btn_run.setFixedHeight(40)
        self.btn_run.setMinimumWidth(140)
        f = QFont()
        f.setPointSize(12)
        f.setBold(True)
        self.btn_run.setFont(f)
        self.btn_run.clicked.connect(self._on_run_clicked)

        self.btn_clear = QPushButton("清空日志")
        self.btn_clear.clicked.connect(lambda: self.txt_log.clear())

        row.addWidget(self.btn_run)
        row.addWidget(self.btn_clear)
        row.addStretch(1)
        return row

    # ---------- 日志
    def _build_log(self) -> QWidget:
        box = QGroupBox("运行日志")
        lay = QVBoxLayout(box)
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        # QTextEdit 默认无限追加；长期运行将导致文档与 QTextLayout 常驻膨胀。
        self.txt_log.document().setMaximumBlockCount(350)
        try:
            self.txt_log.setFont(QFont("WenQuanYi Micro Hei", 10))
        except Exception:
            pass
        self.txt_log.setLineWrapMode(QTextEdit.NoWrap)
        self.txt_log.setMinimumHeight(150)
        lay.addWidget(self.txt_log)
        return box

    # ---------- 环境提示（写入日志，不再占用状态栏）
    def _refresh_env(self):
        edge = find_edge_binary()
        parts = []
        if edge:
            parts.append("自动搜索：Edge")
        else:
            parts.append("未找到 Edge")
        parts.append("root（可能有 --no-sandbox 警告）" if is_root() else "普通用户")
        self.txt_log.append(f"[环境] {' · '.join(parts)}")
        if not edge:
            self.txt_log.append("[环境] 自动搜索需要 Microsoft Edge；"
                                "打开任务链接仍可使用系统默认浏览器")

    # ---------- 状态切换
    def _on_idle(self):
        self.btn_run.setText("运行")
        self.btn_run.setEnabled(True)

    def _on_running(self):
        self.btn_run.setText("停止")
        self.btn_run.setEnabled(True)

    def _on_runner_state(self, active: bool):
        # 搜索可能从「今日」页或定时任务启动；两个页面显示相同的运行状态。
        self.btn_run.setText("停止" if active else "运行")
        self.btn_run.setEnabled(True)

    def _collect(self) -> dict:
        """与定时任务共用一套固定默认参数，不再从旧设置继承危险选项。"""
        kws = [k.strip() for k in self.edt_keywords.text().split(",") if k.strip()]
        params = SearchRunner.defaults()
        params.update(
            repeat=self.spin_repeat.value(),
            level=self.cmb_level.currentIndex() + 1,
            keywords=kws or None,
            keyword_sources=tuple(n for n, cb in self.chk_sources.items() if cb.isChecked()),
        )
        return params

    # ---------- 运行 / 停止
    def _on_run_clicked(self):
        # 避免手动拟人搜索与自动点击 Rewards 卡片同时抢占 Edge 会话。
        parent = self.parent()
        if parent is not None and getattr(parent, "app", None) is not None:
            if parent.app.activity_running():
                QMessageBox.information(self, "活动任务正在运行",
                                        "请等待活动卡片点击和官方同步结束，或先点击“停止”。")
                return
        if self.runner is not None and self.runner.is_running():
            self.runner.stop()
            self.btn_run.setEnabled(False)
            self.btn_run.setText("停止中…")
            return

        params = self._collect()
        if not params["keyword_sources"] and not params["keywords"]:
            QMessageBox.warning(self, "搜索词来源为空", "请至少选择一个热搜来源或填写额外关键词。")
            return
        self.txt_log.clear()
        self._on_running()
        self.txt_log.append(f"[启动] 共 {params['repeat']} 轮")
        if self.runner is not None:
            self.runner.start(params)

    def _append_log(self, action: str, detail: str):
        line = f"{action:<14} {detail}".rstrip()
        self.txt_log.append(line)
        self.txt_log.moveCursor(QTextCursor.End)

    def _on_finished(self, report):
        self._on_idle()
        self.txt_log.append(f"\n=== 完成：成功 {report.ok_count} 失败 {report.fail_count} "
                            f"点击 {report.clicked_count} 用时 {report.total_seconds:.1f}s ===")

    def _on_failed(self, msg: str):
        self._on_idle()
        self.txt_log.append(f"[错误] {msg}")
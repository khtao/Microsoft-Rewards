#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主窗口：今日 / 趋势 / 历史 / 账户与设置 / 拟人搜索。

拟人搜索运行内核（SearchRunner）被三个入口共用：
  1) 今日页「自动完成未完成任务」
  2) 账户页「每天定时自动完成未完成任务」
  3) 拟人搜索独立选项卡（手动配置参数运行）
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
from .controls import NoWheelComboBox, NoWheelDoubleSpinBox, NoWheelSpinBox
from .icons import app_icon
from .panels import AccountPanel, HistoryPanel, QuickPanel, StatsRow
from .task_panel import TaskPanel

# 拟人搜索运行能力
from ..api import DEFAULT_LOG, run_human_search
from ..browser import find_edge_binary, is_root
from ..trends import HTTP_SOURCES
from ..search_terms import remaining_searches

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
    """拟人搜索运行内核，三个入口共用。"""

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
            copy_profile=True,
            keep_open=0.0,
            log_file=str(DEFAULT_LOG),
        )

    @staticmethod
    def for_pending(store, settings: dict) -> dict:
        """根据「未完成任务」推算本轮参数：轮数 = 还差几次搜索。"""
        p = dict(SearchRunner.defaults())
        p.update(settings)
        row = store.daily(date.today().isoformat())
        if row is not None:
            tasks = store.tasks_of_day(date.today().isoformat())
            need = 0
            for t in tasks:
                if t["key"] in ("pc_search", "mobile_search") and not t["complete"]:
                    left = max(0, int(t["points_max"]) - int(t["points_earned"]))
                    need += remaining_searches(left)
            if need > 0:
                p["repeat"] = min(need, 200)
        return p

    def is_running(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def start(self, params: dict):
        if self.is_running():
            return
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

        # 拟人搜索运行内核（三个入口共用）
        self.runner = SearchRunner(self)

        self.stats = StatsRow()
        self.tasks = TaskPanel(self.store, date.today().isoformat())
        self.tasks.changed.connect(self.on_data_changed)
        self.tasks.open_link.connect(self.app.open_task_link)
        self.quick = QuickPanel()
        self.chart = ChartView(self.store, settings=app.settings, save_settings=app.save_settings)
        self.history = HistoryPanel(self.store)
        self.account = AccountPanel(app)
        self.search = SearchPanel(self, self.runner, app.settings, app.save_settings)

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
        self.tabs.addTab(self.search, "拟人搜索")
        self.setCentralWidget(self.tabs)

        # 今日页「自动完成」按钮 → 主窗口入口
        self.quick.run_pending_requested.connect(self.run_pending_tasks)
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
        act_sync.triggered.connect(self.app.sync_now)
        act_login = QAction("登录微软账号", self)
        act_login.triggered.connect(self.app.login)
        act_refresh = QAction("刷新界面", self)
        act_refresh.triggered.connect(self.refresh_all)

        bar.addAction(act_sync)
        bar.addAction(act_login)
        bar.addSeparator()
        bar.addAction(act_refresh)
        self.act_sync = act_sync
        self.act_login = act_login
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
        self.quick.set_busy(active)
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
        self.chart.refresh()
        self.history.reload()
        self._update_status()

    def on_data_changed(self) -> None:
        self.stats.update(self.store)
        self.quick.update(self.store)
        self.chart.refresh()
        self.history.reload()
        self._update_status()
        self.app.update_tray_tooltip()

    def _update_status(self) -> None:
        row = self.store.daily(date.today().isoformat())
        if row is None:
            self.status_left.setText("今天还没有记录 —— 点\"立即同步\"拉取官方数据")
            return
        self.status_left.setText(
            f"今日 {row['points_earned']}/{row['points_max']} 分 · "
            f"任务 {row['tasks_done']}/{row['tasks_total']} · 连续 {row['streak']} 天 · "
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

    def run_pending_tasks(self, settings: Optional[dict] = None):
        """今日页「自动完成未完成任务」/ 定时任务的统一入口。"""
        if self.runner.is_running():
            return
        params = SearchRunner.for_pending(self.store, settings if settings is not None else self.search._collect())
        self.runner.start(params)

    def stop_running(self):
        self.runner.stop()

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


# ============================================================ 拟人搜索选项卡面板
class SearchPanel(QWidget):
    """拟人搜索测试面板，可独立配置参数运行。"""

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
        root.addLayout(self._build_status_row())

        self._load_settings()
        self._wire_settings()
        self._refresh_env()
        self._on_idle()

        if self.runner is not None:
            self.runner.status.connect(self._status)
            self.runner.log.connect(self._append_log)
            self.runner.progress.connect(self._on_progress)
            self.runner.finished.connect(self._on_finished)
            self.runner.failed.connect(self._on_failed)
            self.runner.running_changed.connect(self._on_runner_state)

    # ---------- 参数区
    def _build_params(self) -> QGroupBox:
        box = QGroupBox("参数")
        g = QGridLayout(box)
        g.setVerticalSpacing(8)
        g.setHorizontalSpacing(10)

        self.spin_repeat = NoWheelSpinBox()
        self.spin_repeat.setRange(1, 200)
        self.spin_repeat.setValue(5)
        self.spin_repeat.setSuffix(" 轮")

        self.cmb_level = NoWheelComboBox()
        self.cmb_level.addItems([LEVEL_TIP[i] for i in (1, 2, 3)])
        self.cmb_level.setCurrentIndex(1)

        self.spin_min = NoWheelDoubleSpinBox()
        self.spin_min.setRange(3, 600)
        self.spin_min.setValue(16.0)
        self.spin_min.setSuffix(" s")
        self.spin_max = NoWheelDoubleSpinBox()
        self.spin_max.setRange(3, 600)
        self.spin_max.setValue(29.0)
        self.spin_max.setSuffix(" s")

        self.spin_click = NoWheelDoubleSpinBox()
        self.spin_click.setRange(0.0, 1.0)
        self.spin_click.setSingleStep(0.05)
        self.spin_click.setValue(0.35)

        self.spin_gap_min = NoWheelDoubleSpinBox()
        self.spin_gap_min.setRange(0, 600)
        self.spin_gap_min.setValue(3.0)
        self.spin_gap_max = NoWheelDoubleSpinBox()
        self.spin_gap_max.setRange(0, 600)
        self.spin_gap_max.setValue(12.0)

        self.spin_seed = NoWheelSpinBox()
        self.spin_seed.setRange(-1, 2_000_000_000)
        self.spin_seed.setValue(-1)
        self.spin_seed.setSpecialValueText("随机")

        self.edt_keywords = QLineEdit()
        self.edt_keywords.setPlaceholderText("可选：额外关键词，用逗号分隔，留空则用实时热搜")

        self.edt_log = QLineEdit(str(DEFAULT_LOG))
        self.edt_log.setPlaceholderText("行为日志（JSONL）")

        self.chk_headless = QCheckBox("无头模式（不建议，特征明显）")
        self.chk_copy = QCheckBox("复制 profile 快照")
        self.chk_open = QCheckBox("跑完保持窗口")
        self.spin_keep = NoWheelDoubleSpinBox()
        self.spin_keep.setRange(0, 3600)
        self.spin_keep.setValue(30.0)
        self.spin_keep.setSuffix(" s")

        rows = [
            ("搜索轮数", self.spin_repeat, "拟人等级", self.cmb_level),
            ("每轮时长", self._pair(self.spin_min, self.spin_max), "点击结果概率", self.spin_click),
            ("轮间隔", self._pair(self.spin_gap_min, self.spin_gap_max), "随机种子", self.spin_seed),
        ]
        for r, (l1, w1, l2, w2) in enumerate(rows):
            g.addWidget(QLabel(l1), r, 0)
            g.addWidget(w1, r, 1)
            g.addWidget(QLabel(l2), r, 2)
            g.addWidget(w2, r, 3)

        g.addWidget(QLabel("额外关键词"), 3, 0)
        g.addWidget(self.edt_keywords, 3, 1, 1, 3)
        g.addWidget(QLabel("日志文件"), 4, 0)
        g.addWidget(self.edt_log, 4, 1, 1, 3)

        opts = QHBoxLayout()
        opts.addWidget(self.chk_headless)
        opts.addWidget(self.chk_copy)
        opts.addWidget(self.chk_open)
        opts.addWidget(self.spin_keep)
        opts.addStretch(1)
        g.addLayout(opts, 5, 0, 1, 4)

        src = QHBoxLayout()
        src.addWidget(QLabel("热搜来源"))
        self.chk_sources = {}
        for name in ALL_SOURCES:
            cb = QCheckBox(name)
            cb.setChecked(True)
            self.chk_sources[name] = cb
            src.addWidget(cb)
        src.addStretch(1)
        g.addLayout(src, 6, 0, 1, 4)
        return box

    @staticmethod
    def _pair(a: QWidget, b: QWidget) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(a)
        h.addWidget(QLabel("~"))
        h.addWidget(b)
        return w

    def _view_state(self) -> dict:
        """完整表单状态（与最终运行参数区分，以保存已禁用控件中的值）。"""
        return {
            "repeat": self.spin_repeat.value(),
            "level": self.cmb_level.currentIndex() + 1,
            "round_min": self.spin_min.value(),
            "round_max": self.spin_max.value(),
            "gap_min": self.spin_gap_min.value(),
            "gap_max": self.spin_gap_max.value(),
            "click_prob": self.spin_click.value(),
            "seed": self.spin_seed.value(),
            "keywords": self.edt_keywords.text(),
            "log_file": self.edt_log.text(),
            "headless": self.chk_headless.isChecked(),
            "copy_profile": self.chk_copy.isChecked(),
            "keep_open_enabled": self.chk_open.isChecked(),
            "keep_open_seconds": self.spin_keep.value(),
            "keyword_sources": [name for name, cb in self.chk_sources.items() if cb.isChecked()],
        }

    def _load_settings(self) -> None:
        values = self.settings.get("search_panel", {}) if self.settings else {}
        if not isinstance(values, dict):
            return
        number_controls = {
            "repeat": self.spin_repeat, "round_min": self.spin_min,
            "round_max": self.spin_max, "gap_min": self.spin_gap_min,
            "gap_max": self.spin_gap_max, "click_prob": self.spin_click,
            "seed": self.spin_seed, "keep_open_seconds": self.spin_keep,
        }
        for key, control in number_controls.items():
            if key in values:
                try:
                    control.setValue(float(values[key]) if isinstance(control, NoWheelDoubleSpinBox)
                                     else int(values[key]))
                except (TypeError, ValueError, OverflowError):
                    pass
        try:
            level = int(values.get("level", 2))
        except (ValueError, TypeError):
            level = 2
        self.cmb_level.setCurrentIndex(max(0, min(2, level - 1)))
        for key, control in (("keywords", self.edt_keywords), ("log_file", self.edt_log)):
            if isinstance(values.get(key), str):
                control.setText(values[key])
        for key, control in (("headless", self.chk_headless),
                             ("copy_profile", self.chk_copy),
                             ("keep_open_enabled", self.chk_open)):
            if key in values:
                control.setChecked(bool(values[key]))
        sources = values.get("keyword_sources")
        if isinstance(sources, (list, tuple)):
            for name, cb in self.chk_sources.items():
                cb.setChecked(name in sources)

    def _wire_settings(self) -> None:
        for spin in (self.spin_repeat, self.spin_min, self.spin_max, self.spin_click,
                     self.spin_gap_min, self.spin_gap_max, self.spin_seed, self.spin_keep):
            spin.valueChanged.connect(self._save_settings)
        for control in (self.cmb_level,):
            control.currentIndexChanged.connect(self._save_settings)
        for edit in (self.edt_keywords, self.edt_log):
            edit.textChanged.connect(self._save_settings)
        for checkbox in (self.chk_headless, self.chk_copy, self.chk_open,
                         *self.chk_sources.values()):
            checkbox.toggled.connect(self._save_settings)

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
        try:
            self.txt_log.setFont(QFont("WenQuanYi Micro Hei", 10))
        except Exception:
            pass
        self.txt_log.setLineWrapMode(QTextEdit.NoWrap)
        lay.addWidget(self.txt_log)
        return box

    # ---------- 状态行
    def _build_status_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.lbl_status = QLabel("就绪")
        self.lbl_status.setMinimumWidth(320)
        row.addWidget(self.lbl_status, 1)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setFixedWidth(180)
        self.bar.setTextVisible(True)
        self.bar.setFormat("0 / 0")
        row.addWidget(self.bar)

        self.lbl_env = QLabel("")
        row.addWidget(self.lbl_env)
        return row

    def _refresh_env(self):
        edge = find_edge_binary()
        parts = []
        if edge:
            parts.append("自动搜索：Edge")
        else:
            parts.append("未找到 Edge")
        parts.append("root（可能有 --no-sandbox 警告）" if is_root() else "普通用户")
        self.lbl_env.setText(" · ".join(parts))
        if not edge:
            self._status("自动搜索需要 Microsoft Edge；打开任务链接仍可使用系统默认浏览器")

    # ---------- 状态切换
    def _on_idle(self):
        self.btn_run.setText("运行")
        self.btn_run.setEnabled(True)
        self.bar.setFormat("0 / 0")
        self.bar.setValue(0)

    def _on_running(self):
        self.btn_run.setText("停止")
        self.btn_run.setEnabled(True)

    def _status(self, text: str):
        self.lbl_status.setText(text)

    def _on_runner_state(self, active: bool):
        # 搜索可能从「今日」页或定时任务启动；两个页面显示相同的运行状态。
        self.btn_run.setText("停止" if active else "运行")
        self.btn_run.setEnabled(True)

    def _collect(self) -> dict:
        kws = [k.strip() for k in self.edt_keywords.text().split(",") if k.strip()]
        sources = tuple(n for n, cb in self.chk_sources.items() if cb.isChecked())
        seed = self.spin_seed.value()
        lo, hi = self.spin_min.value(), self.spin_max.value()
        if lo > hi:
            lo, hi = hi, lo
        gmin, gmax = self.spin_gap_min.value(), self.spin_gap_max.value()
        if gmin > gmax:
            gmin, gmax = gmax, gmin
        return dict(
            repeat=self.spin_repeat.value(),
            level=self.cmb_level.currentIndex() + 1,
            round_seconds=(lo, hi),
            gap_seconds=(gmin, gmax),
            click_prob=self.spin_click.value(),
            seed=None if seed < 0 else seed,
            keywords=kws or None,
            keyword_sources=sources or ALL_SOURCES,
            headless=self.chk_headless.isChecked(),
            copy_profile=self.chk_copy.isChecked(),
            keep_open=self.spin_keep.value() if self.chk_open.isChecked() else 0.0,
            log_file=self.edt_log.text().strip() or None,
        )

    # ---------- 运行 / 停止
    def _on_run_clicked(self):
        if self.runner is not None and self.runner.is_running():
            self.runner.stop()
            self.btn_run.setEnabled(False)
            self.btn_run.setText("停止中…")
            self._status("正在停止，等当前这一轮结束…")
            return

        params = self._collect()
        self.txt_log.clear()
        self._on_running()
        self._status(f"启动浏览器…共 {params['repeat']} 轮")
        self.bar.setRange(0, max(1, params["repeat"]))
        self.bar.setValue(0)
        self.bar.setFormat(f"0 / {params['repeat']}")
        if self.runner is not None:
            self.runner.start(params)

    def _append_log(self, action: str, detail: str):
        line = f"{action:<14} {detail}".rstrip()
        self.txt_log.append(line)
        self.txt_log.moveCursor(QTextCursor.End)

    def _on_progress(self, cur: int, total: int):
        self.bar.setRange(0, max(1, total))
        self.bar.setValue(cur)
        self.bar.setFormat(f"{cur} / {total}")

    def _on_finished(self, report):
        self._on_idle()
        self.bar.setRange(0, max(1, report.ok_count + report.fail_count))
        self.bar.setValue(report.ok_count + report.fail_count)
        self.bar.setFormat(f"{report.ok_count + report.fail_count} 轮完成")
        self._status(
            f"完成：成功 {report.ok_count} / 失败 {report.fail_count}，"
            f"点击结果 {report.clicked_count} 次，用时 {report.total_seconds:.1f}s")
        self.txt_log.append(f"\n=== 完成：成功 {report.ok_count} 失败 {report.fail_count} "
                            f"点击 {report.clicked_count} 用时 {report.total_seconds:.1f}s ===")

    def _on_failed(self, msg: str):
        self._on_idle()
        self._status("启动/运行失败，见日志")
        self.txt_log.append(f"[错误] {msg}")
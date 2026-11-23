# -*- coding: utf-8 -*-
"""经典「自动完成未完成任务」的执行内核，供唯一界面复用。

包含 ``SearchRunner`` / ``SearchWorker`` / ``SearchPanel`` 与 ``QuickPanel``：
它们是 ``Application.run_pending_tasks()`` 链路真正调用的代码。本模块只保留
执行与设置所需部分，界面统一到 ``ui/modern``。
"""
from __future__ import annotations

import re
import traceback
from datetime import date
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from ..api import default_log_path, run_human_search
from ..browser import browser_error_hint, find_edge_binary, is_root
from ..pending import planned_rounds
from ..trends import HTTP_SOURCES
from .controls import NoWheelComboBox, NoWheelSpinBox

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
            raw = f"{type(e).__name__}: {e}"
            # 先发一条单行完整原因，再把堆栈压成一行短帧，保证任何截断
            # 长度下都留得下有用信息。
            from ..session import redact
            reason = redact(raw).replace("\n", " ").replace("\r", " ")[:300]
            self.sig.log.emit("error", reason)
            frames = [ln.strip() for ln in traceback.format_exc(limit=6).splitlines()
                      if ln.strip().startswith("File ")]
            self.sig.log.emit("trace", " | ".join(frames)[:300])
            self.sig.failed.emit(browser_error_hint(raw))


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
            repeat=16, level=2,
            round_seconds=(16.0, 29.0),
            gap_seconds=(3.0, 12.0),
            click_prob=0.35,
            seed=None,
            keywords=None,
            keyword_sources=ALL_SOURCES,
            headless=False,
            copy_profile=False,
            keep_open=0.0,
            log_file=default_log_path(),
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
        # 次数 = min(剩余积分÷3 的次数, 强度档位的最多次数)，除此之外不设
        # 任何上限或保底。与设置页摘要「目前搜索 M 轮」共用 planned_rounds()。
        repeat = planned_rounds(tasks, p.get("repeat", 16))
        if repeat <= 0:
            return None
        p["repeat"] = repeat
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
        # 控件只做输入合法性约束；业务上限唯一来源是强度档位，不在这里夹逼。
        self.spin_repeat.setRange(0, 1000000)
        self.spin_repeat.setValue(16)
        self.spin_repeat.setSuffix(" 轮")
        self.cmb_level = NoWheelComboBox()
        self.cmb_level.addItems([LEVEL_TIP[i] for i in (1, 2, 3)])
        self.cmb_level.setCurrentIndex(1)
        self.edt_keywords = QLineEdit()
        self.edt_keywords.setPlaceholderText("可选：额外关键词，用逗号分隔；留空则使用热搜")
        g.addWidget(QLabel("搜索轮数上限"), 0, 0)
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
        state = {
            "repeat": self.spin_repeat.value(),
            "level": self.cmb_level.currentIndex() + 1,
            "keywords": self.edt_keywords.text(),
            "keyword_sources": [name for name, cb in self.chk_sources.items() if cb.isChecked()],
        }
        # 拟人强度档位的 6 项参数不在这张表单上，但 _load_settings() 回填控件
        # 会触发 valueChanged → _save_settings()。必须原样带上，否则整组参数
        # 会在「切换强度」时被只有 4 个键的表单值覆盖丢失。
        sp = self.settings.get("search_panel", {}) if self.settings else {}
        if isinstance(sp, dict):
            for key in ("round_seconds", "max_round_seconds", "gap_seconds",
                        "enter_rate", "click_prob", "scroll_screens"):
                if key in sp and sp[key] is not None:
                    state[key] = sp[key]
        return state

    def _load_settings(self) -> None:
        values = self.settings.get("search_panel", {}) if self.settings else {}
        if not isinstance(values, dict):
            return
        try:
            self.spin_repeat.setValue(int(values.get("repeat", 16)))
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
        """与定时任务共用一套固定默认参数，不再从旧设置继承危险选项。

        拟人强度档位统一控制的 6 项参数（搜索轮数 / 单轮耗时 / 回车提交 / 结果
        点击 / 滚动屏数）从经典 ``search_panel`` 透传给
        api.run_human_search，保证设置页显示的值就是真实执行值。
        """
        kws = [k.strip() for k in self.edt_keywords.text().split(",") if k.strip()]
        params = SearchRunner.defaults()
        params.update(
            repeat=self.spin_repeat.value(),
            level=self.cmb_level.currentIndex() + 1,
            keywords=kws or None,
            keyword_sources=tuple(n for n, cb in self.chk_sources.items() if cb.isChecked()),
        )
        sp = self.settings.get("search_panel", {}) if self.settings else {}
        if isinstance(sp, dict):
            for key in ("round_seconds", "max_round_seconds", "gap_seconds",
                        "enter_rate", "click_prob", "scroll_screens"):
                if key in sp and sp[key] is not None:
                    params[key] = tuple(sp[key]) if isinstance(sp[key], (list, tuple)) \
                        and key in ("round_seconds", "gap_seconds") else sp[key]
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

# ---------------------------------------------------------------- 动作代理

class QuickPanel(QWidget):
    """经典「自动完成未完成任务 / 停止」按钮。

    唯一界面把它作为隐藏代理：托盘菜单的 ``quick.tasks_btn.click`` 与主页主按钮
    最终都落到 ``Application.run_pending_tasks()``，与经典链路完全一致。
    """

    run_pending_requested = Signal()
    stop_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.tasks_btn = QPushButton("自动完成未完成任务", self)
        self.stop_btn = QPushButton("停止", self)
        self.stop_btn.setEnabled(False)
        self.tasks_btn.clicked.connect(self.run_pending_requested.emit)
        self.stop_btn.clicked.connect(self.stop_requested.emit)

    def set_busy(self, busy: bool) -> None:
        self.tasks_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)

    def update(self, store) -> None:
        """保留接口：状态文本由主页状态行显示，这里不需要额外控件。"""
        return None


"""主窗口里的各个面板：统计卡片、历史记录、账户与设置。"""
from __future__ import annotations

import csv
from datetime import date
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFileDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPushButton, QSizePolicy, QScrollArea,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import autostart
from ..search_terms import remaining_searches
from ..config import normalize_run_times
from .controls import NoWheelSpinBox, NoWheelTimeEdit


class StatCard(QFrame):
    def __init__(self, title: str, value: str = "-", sub: str = "", accent: str = "#2f6fed",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(84)
        self.title = QLabel(title)
        self.title.setStyleSheet("color:#868e96;font-size:12px;")
        self.value = QLabel(value)
        self.value.setStyleSheet(f"color:{accent};font-size:24px;font-weight:700;")
        self.sub = QLabel(sub)
        self.sub.setStyleSheet("color:#adb5bd;font-size:11px;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.addWidget(self.title)
        lay.addWidget(self.value)
        lay.addWidget(self.sub)

    def set(self, value: str, sub: str = "") -> None:
        self.value.setText(value)
        self.sub.setText(sub)


class StatsRow(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.cards = {
            "today": StatCard("今日获得", "0", "共 0 分可拿", "#2f6fed"),
            "streak": StatCard("连续完成", "0 天", "历史最佳 0 天", "#12b3a8"),
            "available": StatCard("账户可用积分", "-", "等级 Level -", "#845ef7"),
            "month": StatCard("本月累计", "0", "已记录 0 天", "#f59f00"),
        }
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 0)
        lay.setSpacing(8)
        for c in self.cards.values():
            lay.addWidget(c, 1)

    def update(self, store) -> None:
        stats = store.stats()
        today = store.daily(date.today().isoformat())
        available, level, official = store.latest_available()
        earned = int(today["points_earned"]) if today else 0
        pmax = int(today["points_max"]) if today else 0
        self.cards["today"].set(f"{earned}", f"今日上限 {pmax} 分")
        cur = int(today["streak"]) if today else 0
        self.cards["streak"].set(f"{cur} 天",
                                 f"官方显示 {official} 天" if official else f"历史最佳 {stats['best_streak']} 天")
        self.cards["available"].set(f"{available:,}", f"Level {level}" if level else "未同步")
        self.cards["month"].set(f"{stats['month_points']:,}", f"已记录 {stats['tracked_days']} 天")


class QuickPanel(QFrame):
    """今日搜索进度与自动完成操作。"""

    run_pending_requested = Signal()
    stop_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("QuickPanel")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self.info = QLabel("同步后自动更新今日剩余搜索次数")
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color:#495057;font-size:12px;")

        self.tasks_btn = QPushButton("自动完成未完成任务")
        self.tasks_btn.clicked.connect(self.run_pending_requested.emit)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        self.stop_btn.setEnabled(False)

        grid = QGridLayout()
        grid.addWidget(self.tasks_btn, 0, 0)
        grid.addWidget(self.stop_btn, 0, 1)
        grid.setColumnStretch(1, 0)


        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        browser_note = QLabel("自动任务使用 Edge 点击 Rewards 活动卡片；任务行双击仍打开链接")
        browser_note.setStyleSheet("color:#868e96;font-size:12px;")
        lay.addWidget(browser_note)
        lay.addWidget(self.info)
        lay.addLayout(grid)

    def set_busy(self, busy: bool) -> None:
        self.tasks_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)

    def update(self, store) -> None:
        row = store.daily(date.today().isoformat())
        if row is None:
            self.info.setText("今天还没有记录，先同步一次")
            return
        tasks = store.tasks_of_day(date.today().isoformat())
        pc = next((t for t in tasks if t["key"] == "pc_search"), None)
        mb = next((t for t in tasks if t["key"] == "mobile_search"), None)
        parts = []
        if pc is not None:
            left = int(pc["points_max"]) - int(pc["points_earned"])
            parts.append(f"电脑搜索还差 {remaining_searches(left)} 次（{left} 分）")
        if mb is not None:
            left = int(mb["points_max"]) - int(mb["points_earned"])
            parts.append(f"移动搜索还差 {remaining_searches(left)} 次（{left} 分）")
        pending = [t for t in tasks if not t["complete"] and t["link"]
                   and (int(t["points_max"]) > 0 or int(t["points_earned"]) > 0)]
        parts.append(f"{len(pending)} 个任务有活动链接（自动模式改为点击卡片）")
        self.info.setText(" · ".join(parts))


class HistoryPanel(QWidget):
    def __init__(self, store, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.store = store
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["日期", "获得", "上限", "完成", "连续", "可用积分", "同步时间"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.export_btn = QPushButton("导出 CSV")
        self.export_btn.clicked.connect(self.export_csv)
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 8, 8, 0)
        bar.addStretch(1)
        bar.addWidget(self.export_btn)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 8, 8)
        lay.addLayout(bar)
        lay.addWidget(self.table, 1)

    def reload(self) -> None:
        rows = list(reversed(self.store.recent_days(365)))
        self.table.setRowCount(0)
        for r in rows:
            i = self.table.rowCount()
            self.table.insertRow(i)
            values = [r["day"], str(r["points_earned"]), str(r["points_max"]),
                      f"{r['tasks_done']}/{r['tasks_total']}", str(r["streak"]),
                      str(r["available_points"]), (r["synced_at"] or "").replace("T", " ")]
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if r["all_done"] and c == 0:
                    item.setForeground(Qt.GlobalColor.darkGreen)
                self.table.setItem(i, c, item)

    def export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出历史记录", f"rewards_history_{date.today().isoformat()}.csv", "CSV 文件 (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["日期", "获得积分", "积分上限", "完成任务数", "任务总数", "连续天数", "账户可用积分", "等级", "同步时间"])
                for r in self.store.all_days():
                    w.writerow([r["day"], r["points_earned"], r["points_max"], r["tasks_done"],
                                r["tasks_total"], r["streak"], r["available_points"],
                                r["level"], r["synced_at"] or ""])
            QMessageBox.information(self, "导出完成", f"已保存到：\n{path}")
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))


class AccountPanel(QWidget):
    settings_changed = Signal(dict)

    def __init__(self, app, parent: Optional[QWidget] = None, search_panel=None) -> None:
        super().__init__(parent)
        self.app = app
        settings = app.settings

        self.status_label = QLabel("登录状态：检测中…")
        self.status_label.setStyleSheet("font-size:13px;font-weight:600;")
        self.login_btn = QPushButton("登录微软账号")
        self.login_btn.clicked.connect(app.login)
        self.logout_btn = QPushButton("退出登录（仅清除本地会话）")
        self.logout_btn.clicked.connect(app.logout)
        self.sync_btn = QPushButton("立即同步")
        self.sync_btn.clicked.connect(lambda: app.sync_now())

        acc = QGroupBox("微软账号")
        acc_lay = QVBoxLayout(acc)
        row1 = QHBoxLayout()
        row1.addWidget(self.login_btn)
        row1.addWidget(self.logout_btn)
        acc_lay.addWidget(self.status_label)
        acc_lay.addLayout(row1)
        acc_lay.addWidget(self.sync_btn)
        note = QLabel("登录只需粘贴浏览器里的 Cookie，不启动浏览器、不接触密码；Cookie 保存在本机数据目录（权限 600），随时可一键清除。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#868e96;font-size:12px;")
        acc_lay.addWidget(note)

        self.auto_sync = QCheckBox("开启自动同步")
        self.auto_sync.setChecked(int(settings.get("sync_interval_minutes", 0)) > 0)
        self.interval = NoWheelSpinBox()
        self.interval.setRange(5, 720)
        self.interval.setSuffix(" 分钟")
        self.interval.setValue(int(settings.get("sync_interval_minutes") or
                                   settings.get("sync_interval_value_minutes", 60)))
        self.interval.setEnabled(self.auto_sync.isChecked())
        self.auto_sync.toggled.connect(self.interval.setEnabled)

        self.remind = QCheckBox("每日提醒")
        self.remind.setChecked(bool(settings.get("remind_time")))
        self.remind_time = NoWheelTimeEdit()
        self.remind_time.setDisplayFormat("HH:mm")
        self.remind_time.setTime(self._parse_time(
            settings.get("remind_time") or settings.get("remind_time_value", "09:00")))
        self.remind_time.setEnabled(self.remind.isChecked())
        self.remind.toggled.connect(self.remind_time.setEnabled)

        self.evening = QCheckBox("晚间补做提醒")
        self.evening.setChecked(bool(settings.get("evening_remind_time")))
        self.evening_time = NoWheelTimeEdit()
        self.evening_time.setDisplayFormat("HH:mm")
        self.evening_time.setTime(self._parse_time(
            settings.get("evening_remind_time") or settings.get("evening_remind_time_value", "20:30")))
        self.evening_time.setEnabled(self.evening.isChecked())
        self.evening.toggled.connect(self.evening_time.setEnabled)

        self.auto_open = QCheckBox("每天定时点击未完成的 Rewards 活动卡片")
        self.auto_open.setChecked(bool(settings.get("auto_open_time")))
        self.auto_open_time = NoWheelTimeEdit()
        self.auto_open_time.setDisplayFormat("HH:mm")
        self.auto_open_time.setTime(self._parse_time(
            settings.get("auto_open_time") or settings.get("auto_open_time_value", "09:00")))
        self.auto_open_time.setEnabled(self.auto_open.isChecked())
        self.auto_open.toggled.connect(self.auto_open_time.setEnabled)
        gap_min = int(settings.get("auto_open_gap_min", 5))
        gap_max = int(settings.get("auto_open_gap_max", 10))
        self.gap_min = NoWheelSpinBox()
        self.gap_min.setRange(1, 600)
        self.gap_min.setSuffix(" 秒")
        self.gap_min.setValue(gap_min)
        self.gap_max = NoWheelSpinBox()
        self.gap_max.setRange(1, 600)
        self.gap_max.setSuffix(" 秒")
        self.gap_max.setValue(gap_max)
        self.gap_min.valueChanged.connect(
            lambda v: self.gap_max.setValue(max(v, self.gap_max.value())))
        self.gap_max.valueChanged.connect(
            lambda v: self.gap_min.setValue(min(v, self.gap_min.value())))

        # 搜索定时启动独立于“打开活动页面”的时间，不再只支持一个时间点。
        self.auto_run_enabled = QCheckBox("启用定时自动完成未完成任务")
        self.auto_run_enabled.setChecked(bool(settings.get("auto_run_enabled", False)))
        self._time_rows = []
        self.time_list = QVBoxLayout()
        self.time_list.setSpacing(4)
        self.add_time_btn = QPushButton("+ 添加启动时间（最多 10 个）")
        self.add_time_btn.clicked.connect(self._add_time)
        for time_text in settings.get("auto_run_times", ["09:00"]):
            self._add_time(time_text, save=False)
        self._update_time_controls()
        self.auto_run_enabled.toggled.connect(self._update_time_controls)

        self.silent_cb = QCheckBox("任务全部完成后不再提醒")
        self.silent_cb.setChecked(bool(settings.get("silent_when_all_done", True)))
        self.notify_cb = QCheckBox("同步完成后弹出通知")
        self.notify_cb.setChecked(bool(settings.get("notify_after_sync", True)))
        self.close_cb = QCheckBox("关闭窗口时最小化到托盘")
        self.close_cb.setChecked(bool(settings.get("close_to_tray", True)))
        self.autostart_cb = QCheckBox("随系统启动")
        self.autostart_cb.setChecked(autostart.is_enabled())

        grid = QGridLayout()
        grid.addWidget(self.auto_sync, 0, 0)
        grid.addWidget(self.interval, 0, 1)
        grid.addWidget(self.remind, 1, 0)
        grid.addWidget(self.remind_time, 1, 1)
        grid.addWidget(self.evening, 2, 0)
        grid.addWidget(self.evening_time, 2, 1)
        grid.addWidget(self.auto_open, 3, 0)
        grid.addWidget(self.auto_open_time, 3, 1)
        gap_row = QHBoxLayout()
        gap_row.addWidget(QLabel("间隔"))
        gap_row.addWidget(self.gap_min)
        gap_row.addWidget(QLabel("~"))
        gap_row.addWidget(self.gap_max)
        grid.addLayout(gap_row, 4, 0)
        grid.addWidget(QLabel("活动点击间隔（默认 5~10 秒）；点击后同步核验是否完成"), 4, 1)
        grid.addWidget(self.silent_cb, 5, 0)
        grid.addWidget(self.notify_cb, 5, 1)
        grid.addWidget(self.close_cb, 6, 0)
        grid.addWidget(self.autostart_cb, 6, 1)

        self.save_btn = QPushButton("保存设置")
        self.save_btn.clicked.connect(app.apply_settings)
        self.save_btn.setFixedWidth(120)

        auto = QGroupBox("自动化与提醒")
        auto_lay = QVBoxLayout(auto)
        auto_lay.addLayout(grid)
        row_save = QHBoxLayout()
        row_save.addStretch(1)
        row_save.addWidget(self.save_btn)
        row_save.addWidget(QLabel("修改后自动保存并立即生效"))
        auto_lay.addLayout(row_save)

        # 统一放入账户页的滚动区域，替代原“拟人搜索”选项卡。
        self.scroller = QScrollArea(self)
        self.scroller.setWidgetResizable(True)
        self.scroller.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(12)
        lay.addWidget(acc)
        lay.addWidget(auto)

        schedule_box = QGroupBox("定时自动完成未完成任务")
        schedule_lay = QVBoxLayout(schedule_box)
        schedule_lay.addWidget(self.auto_run_enabled)
        schedule_lay.addWidget(QLabel("设置 0~10 个不同的每日启动时间（本地时间）；全部完成时自动跳过。"))
        schedule_lay.addLayout(self.time_list)
        schedule_lay.addWidget(self.add_time_btn)
        note = QLabel("定时任务先同步官方进度；电脑搜索使用下方拟人搜索参数。"
                      "其他活动仅可打开任务页供手动完成；移动搜索不会被误当作电脑搜索完成。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#868e96;font-size:12px;")
        schedule_lay.addWidget(note)
        lay.addWidget(schedule_box)
        if search_panel is not None:
            lay.addWidget(search_panel)
        lay.addStretch(1)
        self.scroller.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.scroller)

        # 初始化结束后再连接：避免加载保存值时产生写盘及系统操作。
        for checkbox in (self.auto_sync, self.remind, self.evening, self.auto_open,
                         self.auto_run_enabled, self.silent_cb, self.notify_cb,
                         self.close_cb, self.autostart_cb):
            checkbox.toggled.connect(self._changed)
        for spin in (self.interval, self.gap_min, self.gap_max):
            spin.valueChanged.connect(self._changed)
        for time_edit in (self.remind_time, self.evening_time, self.auto_open_time):
            time_edit.timeChanged.connect(self._changed)

    def _add_time(self, text=None, *, save=True) -> None:
        if len(self._time_rows) >= 10:
            QMessageBox.information(self, "数量已达上限", "最多支持 10 个不同的启动时间点。")
            return
        existing = {edit.time().toString("HH:mm") for edit, _, _ in self._time_rows}
        if not isinstance(text, str):
            # 默认以半小时为单位选取一个可用时刻。
            start = (int(self._time_rows[-1][0].time().hour()) * 2
                     + int(self._time_rows[-1][0].time().minute() >= 30) + 1) if self._time_rows else 18
            text = next((f"{((start + i) % 48) // 2:02d}:{((start + i) % 2) * 30:02d}"
                         for i in range(48)
                         if f"{((start + i) % 48) // 2:02d}:{((start + i) % 2) * 30:02d}" not in existing), "09:00")
        if text in existing:
            if save:
                QMessageBox.warning(self, "启动时间重复", "每个启动时间点必须不同。")
            return
        edit = NoWheelTimeEdit()
        edit.setDisplayFormat("HH:mm")
        edit.setTime(self._parse_time(text))
        edit.setProperty("last_time", edit.time().toString("HH:mm"))
        btn = QPushButton("删除")
        row_widget = QWidget()
        row_lay = QHBoxLayout(row_widget)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.addWidget(QLabel(f"启动时间 {len(self._time_rows) + 1}"))
        row_lay.addWidget(edit)
        row_lay.addWidget(btn)
        row_lay.addStretch(1)
        self.time_list.addWidget(row_widget)
        self._time_rows.append((edit, btn, row_widget))
        edit.timeChanged.connect(lambda _, control=edit: self._time_changed(control))
        btn.clicked.connect(lambda _, control=edit: self._remove_time(control))
        self._update_time_controls()
        if save:
            self._changed()

    def _remove_time(self, control) -> None:
        for i, (edit, _, widget) in enumerate(self._time_rows):
            if edit is control:
                self._time_rows.pop(i)
                self.time_list.removeWidget(widget)
                widget.deleteLater()
                break
        for i, (_, _, widget) in enumerate(self._time_rows, 1):
            widget.layout().itemAt(0).widget().setText(f"启动时间 {i}")
        self._update_time_controls()
        self._changed()

    def _time_changed(self, control) -> None:
        value = control.time().toString("HH:mm")
        other = {edit.time().toString("HH:mm") for edit, _, _ in self._time_rows if edit is not control}
        if value in other:
            control.blockSignals(True)
            control.setTime(self._parse_time(control.property("last_time")))
            control.blockSignals(False)
            QMessageBox.warning(self, "启动时间重复", "每个启动时间点必须不同。")
            return
        control.setProperty("last_time", value)
        self._changed()

    def _update_time_controls(self, *_args) -> None:
        self.add_time_btn.setEnabled(self.auto_run_enabled.isChecked() and len(self._time_rows) < 10)
        for edit, btn, _ in self._time_rows:
            edit.setEnabled(self.auto_run_enabled.isChecked())
            btn.setEnabled(self.auto_run_enabled.isChecked())

    def _changed(self, *_args) -> None:
        self.settings_changed.emit(self.collect())

    def set_autostart(self, enabled: bool) -> None:
        """操作系统拒绝自启动变更时恢复开关，不重复触发保存。"""
        self.autostart_cb.blockSignals(True)
        try:
            self.autostart_cb.setChecked(enabled)
        finally:
            self.autostart_cb.blockSignals(False)

    @staticmethod
    def _parse_time(text: str):
        from PySide6.QtCore import QTime
        t = QTime.fromString(text or "09:00", "HH:mm")
        return t if t.isValid() else QTime(9, 0)

    def set_status(self, text: str, ok: Optional[bool] = None) -> None:
        self.status_label.setText(f"登录状态：{text}")
        color = "#2f9e44" if ok else ("#c92a2a" if ok is False else "#495057")
        self.status_label.setStyleSheet(f"font-size:13px;font-weight:600;color:{color};")

    def collect(self) -> dict:
        return {
            "sync_interval_minutes": int(self.interval.value()) if self.auto_sync.isChecked() else 0,
            "sync_interval_value_minutes": int(self.interval.value()),
            "remind_time": self.remind_time.time().toString("HH:mm") if self.remind.isChecked() else "",
            "remind_time_value": self.remind_time.time().toString("HH:mm"),
            "evening_remind_time": self.evening_time.time().toString("HH:mm") if self.evening.isChecked() else "",
            "evening_remind_time_value": self.evening_time.time().toString("HH:mm"),
            "auto_open_time": self.auto_open_time.time().toString("HH:mm") if self.auto_open.isChecked() else "",
            "auto_open_time_value": self.auto_open_time.time().toString("HH:mm"),
            "auto_open_gap_min": int(self.gap_min.value()),
            "auto_open_gap_max": int(self.gap_max.value()),
            "auto_run_enabled": self.auto_run_enabled.isChecked(),
            "auto_run_times": normalize_run_times(
                [edit.time().toString("HH:mm") for edit, _, _ in self._time_rows]),
            "silent_when_all_done": self.silent_cb.isChecked(),
            "notify_after_sync": self.notify_cb.isChecked(),
            "close_to_tray": self.close_cb.isChecked(),
            "launch_at_login": self.autostart_cb.isChecked(),
        }
"""今日任务清单：勾选打卡、进度条、双击打开任务页。

任务全部来自微软 dashboard 同步（积分与完成状态以官方数据为准）。
名称优先用接口/页面上的真实显示名；取不到时显示清理后的原始活动 ID。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel,
    QProgressBar, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .icons import category_color

COL_DONE, COL_NAME, COL_CAT, COL_POINTS = range(4)


class TaskPanel(QWidget):
    """一天的任务表。勾选即写入数据库并重算连续天数。"""

    changed = Signal()
    open_link = Signal(str)

    def __init__(self, store, day: Optional[str] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.store = store
        self.day = day
        self._loading = False

        self.progress = QProgressBar()
        self.progress.setFormat("今日进度 %p%  (%v / %m 分)")
        self.progress.setTextVisible(True)
        self.progress.setFixedHeight(22)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["✓", "任务", "分类", "积分"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setColumnWidth(COL_DONE, 36)
        self.table.horizontalHeader().setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(COL_CAT, 90)
        self.table.setColumnWidth(COL_POINTS, 90)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.doubleClicked.connect(self._on_double_click)

        self.hint = QLabel()
        self.hint.setStyleSheet("color:#868e96;font-size:12px;")
        self.hint.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.progress)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.hint)

    @staticmethod
    def _display_name(row) -> str:
        """名称显示：拿不到官方名时加个标记，避免用户误以为是官方名字。"""
        name = row["name"] or ""
        if row["title_source"] == "guess":
            return f"{name} ·未识别"
        return name

    # ------------------------------------------------------------------ 载入

    def set_day(self, day: str) -> None:
        self.day = day
        self.reload()

    def reload(self) -> None:
        if not self.day:
            return
        self._loading = True
        self.table.setRowCount(0)
        done = max_total = 0
        for r in self.store.tasks_of_day(self.day):
            i = self.table.rowCount()
            self.table.insertRow(i)

            check = QTableWidgetItem("")
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                           | Qt.ItemFlag.ItemIsSelectable)
            check.setCheckState(Qt.CheckState.Checked if r["complete"] else Qt.CheckState.Unchecked)
            check.setData(Qt.ItemDataRole.UserRole, r["key"])
            self.table.setItem(i, COL_DONE, check)

            name = QTableWidgetItem(self._display_name(r))
            name.setData(Qt.ItemDataRole.UserRole, r["key"])
            if r["title_source"] == "guess":
                name.setForeground(QColor("#adb5bd"))
                name.setToolTip("没取到这个任务的官方名称，显示的是活动 ID 的简化形式；"
                                "名称来源：接口/页面均未返回可读标题。")
            if r["complete"]:
                f = name.font()
                f.setStrikeOut(True)
                name.setFont(f)
            self.table.setItem(i, COL_NAME, name)

            cat = QTableWidgetItem(r["category"])
            cat.setForeground(QColor(category_color(r["category"])))
            self.table.setItem(i, COL_CAT, cat)

            pts = QTableWidgetItem(f"{r['points_earned']} / {r['points_max']}")
            pts.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, COL_POINTS, pts)

            max_total += int(r["points_max"])
            done += int(r["points_earned"])

        self.progress.setMaximum(max(1, max_total))
        self.progress.setValue(min(done, max_total))
        self._loading = False
        self._update_hint()

    def _update_hint(self) -> None:
        if not self.day:
            return
        row = self.store.daily(self.day)
        if row is None:
            self.hint.setText("今天还没有记录，点“立即同步”拉取官方数据。")
            return
        left = int(row["points_max"]) - int(row["points_earned"])
        if row["all_done"]:
            self.hint.setText(f"今日任务已全部完成 🎉  累计 {row['points_earned']} 分，"
                              f"连续 {row['streak']} 天。")
        else:
            self.hint.setText(
                f"还剩 {row['tasks_total'] - row['tasks_done']} 项 / 约 {left} 分未完成。"
                f"  双击任务行可在浏览器里打开该任务页面。"
            )

    # ------------------------------------------------------------------ 交互

    def _on_double_click(self, index) -> None:
        """双击任务行 → 在真实浏览器里打开该任务的落地页。"""
        row = index.row()
        key_item = self.table.item(row, COL_DONE)
        if key_item is None:
            return
        key = key_item.data(Qt.ItemDataRole.UserRole)
        for r in self.store.tasks_of_day(self.day):
            if r["key"] == key and r["link"]:
                self.open_link.emit(r["link"])
                return

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != COL_DONE:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if not key:
            return
        complete = item.checkState() == Qt.CheckState.Checked
        self.store.set_task_complete(self.day, key, complete)
        self.store.refresh_day(self.day)
        self.reload()
        self.changed.emit()

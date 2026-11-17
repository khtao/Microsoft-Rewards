# -*- coding: utf-8 -*-
"""唯一界面：按截图复刻的单列窗口，直接驱动 1.3.0 的经典应用控制器。

架构
----
* ``Application``（``app.py``）是唯一的控制器：托盘、调度器、
  ``SyncWorker`` / ``ActivityWorker`` / ``SearchRunner``、``run_pending_tasks()``
  全部由它编排；
* 本模块是 ``Application.window``：实现控制器所需的窗口接口，并把主页 /
  设置页接到控制器上，不再有第二套同步或调度代码；
* 主页「自动完成任务」与托盘「自动完成未完成任务」最终都调用
  ``Application.run_pending_tasks()`` — **与经典界面完全同一份 Python 代码**：
  先同步官方状态 → 点击未完成活动卡片（ActivityWorker，运行期不核验）→
  未完成的电脑端搜索（SearchRunner.for_pending → run_human_search）→
  整轮结束后统一同步一次核验；
* 设置项直接写入经典控制器读取的 ``Settings`` 键（见 ``viewmodel.CLASSIC_BINDING``），
  不再是纯展示的假偏好。

合规：自动搜索 / 点击活动卡属于仓库自带的实验性功能，Microsoft Rewards 条款
要求奖励搜索由用户本人手动进行；使用前请阅读 README 的「合规与使用风险」。
"""
from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from PySide6.QtCore import QEvent, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QScrollArea, QStackedWidget, QSystemTrayIcon,
    QVBoxLayout, QWidget,
)

from ...browser import browser_error_hint
from ...config import (APP_TITLE, Settings, data_dir, normalize_run_times)
from ...notifier import Notifier
from ...session import ALLOWED_PATHS, default_fetcher, redact
from ...storage import Store, day_str
from ...sync import sync_once
from ...ui.legacy import QuickPanel, SearchPanel, SearchRunner
from ...ui.login_dialog import LoginDialog
from . import effects, theme, viewmodel as vm
from .viewmodel import (calendar_days, calendar_months,
                        calendar_summary, calendar_week, group_tasks,
                        month_total, points_text, progress_of, resolve_language,
                        settings_values, tr)
from .widgets import (CalendarGrid, Card, GroupBlock, RingProgress, Row,
                      Segmented, StatCard, StatusChip, Switch, TimeChip)


def _clear_layout(layout) -> None:
    """移除布局里的控件并立刻脱离父级，避免 deleteLater 排队期间仍被绘制。

    ``QLayout.takeAt()`` 只把控件移出布局，QWidget 仍是父控件的子对象；只调
    ``deleteLater()`` 时，在事件循环真正删除它之前，旧控件会和新控件叠在
    一起被画出来（设置页连续切强度档位时右列文字重影就是这个原因）。
    """
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        w = item.widget()
        if w is not None:
            try:
                w.hide()
                w.setParent(None)
                w.deleteLater()
            except RuntimeError:
                pass
        sub = item.layout()
        if sub is not None:
            _clear_layout(sub)


# ---------------------------------------------------------------- 后台线程

class SyncRunner(QThread):
    """无控制器时的预览模式只读同步；正常启动一律走控制器的 SyncWorker。"""

    progress = Signal(str)
    ok = Signal(dict)
    fail = Signal(str)

    def __init__(self, store, day: str, parent=None) -> None:
        super().__init__(parent)
        self.store, self.day = store, day

    def run(self) -> None:
        try:
            out = sync_once(self.store, day=self.day, force=True,
                            progress=self.progress.emit)
            if out.get("ok"):
                self.ok.emit(out)
            else:
                self.fail.emit(out.get("message", "同步未完成"))
        except Exception as exc:
            self.fail.emit(redact(str(exc))[:220])
        finally:
            try:
                self.store.close_thread_connection()
            except Exception:
                pass


class CaptureRunner(QThread):
    """按需只读取两页 HTML 并打包；绝不抓取白名单之外的地址。"""

    done = Signal(str)
    fail = Signal(str)

    def __init__(self, target: Path, parent=None) -> None:
        super().__init__(parent)
        self.target = target

    def run(self) -> None:
        try:
            pages = default_fetcher().fetch_many(ALLOWED_PATHS)
            html = {p: r.html for p, r in pages.items() if r.usable and r.html}
            if not html:
                self.fail.emit("两页都没读到可用内容（可能未登录或网络异常），未生成文件。")
                return
            with zipfile.ZipFile(self.target, "w", zipfile.ZIP_DEFLATED) as z:
                for path, text in html.items():
                    z.writestr(path.strip("/").replace("/", "_") + ".html", text)
                z.writestr("README.txt",
                           "只读抓取 /dashboard 与 /earn 的页面源码。"
                           "可能含账户信息，请勿公开分享。")
            self.done.emit(str(self.target))
        except Exception as exc:
            self.fail.emit(redact(str(exc))[:220])


# ---------------------------------------------------------------- 主页

class HomePage(QWidget):
    """截图 1：顶部栏、圆环、三张积分卡、主按钮、今日任务卡。"""

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self._lang = app.lang
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(14, 10, 14, 22)
        lay.setSpacing(12)

        top = QHBoxLayout()
        self.logo = QPushButton("🔍")
        self.logo.setObjectName("icon")
        self.logo.clicked.connect(self._on_refresh_clicked)
        top.addWidget(self.logo)
        self.title = QLabel(tr(self._lang, "app_title"))
        self.title.setStyleSheet("font-size:16px;font-weight:700;")
        top.addWidget(self.title)
        top.addStretch(1)
        self.login_badge = QPushButton("🏅 " + tr(self._lang, "unknown"))
        self.login_badge.setObjectName("icon")
        self.login_badge.setStyleSheet("color:" + theme.current()["orange"] + ";font-size:12px;")
        self.login_badge.clicked.connect(app.open_login)
        top.addWidget(self.login_badge)
        gear = QPushButton("⚙")
        gear.setObjectName("icon")
        gear.clicked.connect(app.show_settings)
        top.addWidget(gear)
        lay.addLayout(top)

        center = QHBoxLayout()
        center.addStretch(1)
        self.ring = RingProgress()
        self.ring.set_refresh_caption(tr(self._lang, "ring_refresh"))
        self.ring.clicked.connect(self._on_refresh_clicked)
        center.addWidget(self.ring)
        center.addStretch(1)
        lay.addLayout(center)
        # 对齐插件版 popup.html 的 #status：圆点 + 运行状态文案，
        # 不再是「待完成也显示绿色」的固定标签。
        self.status_chip = StatusChip(self._lang)
        lay.addWidget(self.status_chip)

        cards = QHBoxLayout()
        cards.setSpacing(9)
        self.card_available = StatCard(tr(self._lang, "available"))
        self.card_today = StatCard(tr(self._lang, "today"))
        self.card_claimable = StatCard(tr(self._lang, "claimable"))
        for c in (self.card_available, self.card_today, self.card_claimable):
            cards.addWidget(c, 1)
        lay.addLayout(cards)

        # 主按钮：空闲 = 自动完成任务（经典入口）；运行期 = 停止自动运行。
        # 用单一槽按模式分发，避免重复 connect 造成一次点击触发两次动作。
        self.primary = QPushButton("▶  " + tr(self._lang, "sync"))
        self.primary.setObjectName("primary")
        self._running = False
        self._stopping = False
        self._sync_busy = False
        self.primary.clicked.connect(self._on_primary_clicked)
        self._apply_primary_style()
        lay.addWidget(self.primary)
        self.note = QLabel(tr(self._lang, "readonly_note"))
        self.note.setObjectName("tiny")
        self.note.setWordWrap(True)
        self.note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.note)

        self.task_card = Card()
        head = QHBoxLayout()
        self.task_title = QLabel(tr(self._lang, "today_tasks"))
        self.task_title.setObjectName("cardTitle")
        head.addWidget(self.task_title)
        head.addStretch(1)
        # 「刷新」已移至中央进度圆环：圆环 hover 有呼吸动效并显示「点击刷新」，
        # 点击即同步。这里不再放第二个文字按钮，减少操作复杂度。
        self.task_card.add_layout(head)
        hero = QFrame()
        hero.setObjectName("heroBar")
        hero.setFixedHeight(28)
        hl = QHBoxLayout(hero)
        hl.setContentsMargins(6, 0, 6, 0)
        self.hero_label = QLabel("")
        self.hero_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hero_label.setStyleSheet(
            f"color:{theme.current()['green_text']};font-size:12px;font-weight:600;")
        hl.addWidget(self.hero_label)
        self.task_card.add(hero)
        self.groups_host = QWidget()
        self.groups_lay = QVBoxLayout(self.groups_host)
        self.groups_lay.setContentsMargins(0, 0, 0, 0)
        self.groups_lay.setSpacing(12)
        self.task_card.add(self.groups_host)
        lay.addWidget(self.task_card)

        self.status = QLabel("")
        self.status.setObjectName("tiny")
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.status)
        lay.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        # 按需求不再有任何鼠标悬浮提示：说明一律用页面上的常驻文字呈现。
        self._enter_widgets = [self.ring, self.card_available, self.card_today,
                               self.card_claimable, self.primary, self.task_card]

    def enter_widgets(self):
        return list(getattr(self, "_enter_widgets", []))

    def _on_refresh_clicked(self) -> None:
        """圆环 / 顶部放大镜共用：运行期与同步期一律拒绝，防止误操作。"""
        if self._running or self._sync_busy or not self.ring.isEnabled():
            return
        self.app.sync_now()

    def _apply_primary_style(self) -> None:
        """运行期主按钮切到红色（停止态），空闲期恢复蓝色。"""
        danger = "true" if self._running else "false"
        if self.primary.property("danger") != danger:
            self.primary.setProperty("danger", danger)
            style = self.primary.style()
            style.unpolish(self.primary)
            style.polish(self.primary)
        self.primary.update()

    def _on_primary_clicked(self) -> None:
        """运行期唯一动作是停止；空闲期启动自动完成（与托盘同一分发逻辑）。"""
        if self._running:
            self.app.stop_running()
        else:
            self.app.primary_clicked()

    def _primary_text(self, lang: str) -> str:
        if self._stopping:
            return "■  " + tr(lang, "stopping")
        if self._running:
            return "■  " + tr(lang, "stop_run")
        return "▶  " + tr(lang, "sync")

    def set_running(self, running: bool, stopping: bool = False) -> None:
        """自动运行期间：主按钮变「停止」，同步入口全部禁用，防止误操作。"""
        self._running = bool(running)
        self._stopping = bool(stopping) and self._running
        self.primary.setText(self._primary_text(self.app.lang))
        self._apply_primary_style()
        # 停止请求发出后立刻禁用按钮，避免连点造成重复停止/误触。
        self.primary.setEnabled(not self._stopping and not self._sync_busy)
        self.ring.setEnabled(not self._running and not self._sync_busy)
        self.logo.setEnabled(not self._running and not self._sync_busy)

    def set_busy(self, busy: bool) -> None:
        """同步进行中：禁用主按钮与刷新入口；运行期主按钮仍可点（作为停止）。"""
        self._sync_busy = bool(busy)
        self._apply_primary_style()
        self.primary.setEnabled(not self._sync_busy and not self._stopping)
        self.ring.setEnabled(not self._sync_busy and not self._running)
        self.logo.setEnabled(not self._sync_busy and not self._running)

    def set_language(self, lang: str) -> None:
        self.title.setText(tr(lang, "app_title"))
        self.card_available.set_label(tr(lang, "available"))
        self.card_today.set_label(tr(lang, "today"))
        self.card_claimable.set_label(tr(lang, "claimable"))
        self.primary.setText(self._primary_text(lang))
        self.note.setText(tr(lang, "readonly_note"))
        self.task_title.setText(tr(lang, "today_tasks"))
        self.ring.set_caption(tr(lang, "today_tasks"))
        self.ring.set_refresh_caption(tr(lang, "ring_refresh"))
        self.status_chip.set_language(lang)

    def set_login_badge(self, ok: Optional[bool]) -> None:
        key = "unknown" if ok is None else ("signed_in" if ok else "signed_out")
        self.login_badge.setText("🏅  " + tr(self.app.lang, key))

    def update_data(self, points: dict, tasks: list, prog: dict) -> None:
        self.card_available.set_value(points_text(points.get("available")))
        self.card_today.set_value(points_text(points.get("today")))
        self.card_claimable.set_value(points_text(points.get("claimable")))
        self.ring.set_value(prog["percent"], animate=True)
        self.status_chip.set_from_progress(prog, self._lang)
        self.hero_label.setText(
            f"{tr(self.app.lang, 'today_tasks')} {prog['percent']}%"
            f"（{prog['earned']} / {prog['total']} 分）")
        _clear_layout(self.groups_lay)
        groups = group_tasks(tasks)
        for group in groups:
            self.groups_lay.addWidget(GroupBlock(group))
        if not groups:
            empty = QLabel(tr(self.app.lang, "empty_tasks"))
            empty.setObjectName("muted")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.groups_lay.addWidget(empty)

    def set_status(self, text: str) -> None:
        """主状态行单行显示；过长文案的全文放到页面底部次要详情。"""
        clean = text or tr(self._lang, "status.ready")
        self.status_chip.set_text(clean)
        self.status.setText(clean if len(clean) > 24 else "")

    def set_detail(self, text: str) -> None:
        """次要详情（导出路径、抓取结果等），不抢占主状态行。"""
        self.status.setText(text)


# ---------------------------------------------------------------- 设置页

class SettingsPage(QWidget):
    """精简设置：4 张卡 + 工具行，共 9 个控件。

    * 「一般不会改 / 不推荐改」的项由 ``vm.defaults_patch()`` 一次写成推荐默认，
      不再显示控件；
    * 可统一管理的合并成一个控件，一次写齐一组经典 ``Settings`` 键
      （自动刷新 / 任务提醒 / 后台与通知）；
    * 每个可见控件仍通过 ``vm.pref_to_settings`` 真实生效。
    """

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        v = app.prefs
        lang = app.lang
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(14, 10, 14, 22)
        lay.setSpacing(12)

        top = QHBoxLayout()
        back = QPushButton("‹")
        back.setObjectName("icon")
        back.setFixedWidth(30)
        back.clicked.connect(app.show_home)
        top.addWidget(back)
        t = QLabel(tr(lang, "settings"))
        t.setStyleSheet("font-size:17px;font-weight:700;")
        top.addWidget(t)
        top.addStretch(1)
        sub = QLabel(tr(lang, "settings_sub"))
        sub.setObjectName("tiny")
        top.addWidget(sub)
        lay.addLayout(top)

        def add_row(card, label, control):
            """设置页不使用悬浮提示：标签自解释，默认值统一写在下方只读区。"""
            r = Row(label, control)
            card.add(r)
            return r

        # 01 外观（标签自解释，不挂提示）
        c1 = Card(number="01", title=tr(lang, "appearance"),
                  color=theme.current()["purple"])
        self.lang_seg = Segmented(["中文", "English", "跟随系统"], v["ui_language"])
        add_row(c1, tr(lang, "language"), self.lang_seg)
        self.theme_seg = Segmented(["浅色", "深色", "跟随系统"], v["ui_theme"])
        add_row(c1, tr(lang, "theme"), self.theme_seg)
        lay.addWidget(c1)

        # 02 同步与桌面：3 个「统一管理」组，一次改动写齐一组经典键。
        #    拟人强度移到下方「拟人搜索默认设置」区，与其余拟人参数放在一起。
        c2 = Card(number="02", title=tr(lang, "sync_desktop"),
                  color=theme.current()["primary"])
        self.sync_seg = Segmented([tr(lang, "sync_off"), "15", "30", "60"],
                                  str(v["ui_sync_mode"]))
        add_row(c2, tr(lang, "sync_interval"), self.sync_seg)
        self.remind_sw = Switch(bool(v["ui_remind_mode"]))
        add_row(c2, tr(lang, "remind_group"), self.remind_sw)
        self.background_sw = Switch(bool(v["ui_background_mode"]))
        add_row(c2, tr(lang, "background_group"), self.background_sw)
        self.autostart_sw = Switch(bool(v["ui_launch_at_login"]))
        add_row(c2, tr(lang, "autostart"), self.autostart_sw)
        lay.addWidget(c2)

        # 03 定时运行：一个开关 + 时间胶囊（搜索轮数等已固定为默认）
        c3 = Card(number="03", title=tr(lang, "schedule"),
                  subtitle=tr(lang, "schedule_hint"),
                  color=theme.current()["orange"])
        self.schedule_sw = Switch(bool(v["ui_schedule_enabled"]))
        add_row(c3, tr(lang, "schedule_on"), self.schedule_sw)
        times_head = QHBoxLayout()
        tl = QLabel(tr(lang, "start_times"))
        tl.setStyleSheet("font-weight:600;font-size:12px;")
        times_head.addWidget(tl)
        times_head.addStretch(1)
        add = QPushButton("+")
        add.setFixedWidth(30)
        add.clicked.connect(app.add_schedule_time)
        times_head.addWidget(add)
        c3.add_layout(times_head)
        self.times_host = QWidget()
        self.times_grid = QGridLayout(self.times_host)
        self.times_grid.setContentsMargins(0, 0, 0, 0)
        self.times_grid.setSpacing(6)
        c3.add(self.times_host)
        lay.addWidget(c3)

        # 04 积分日历：只显示有积分的时间；日=一排七个，周=一排一周
        c4 = Card(number="04", title=tr(lang, "calendar"),
                  color=theme.current()["green"])
        self.cal_seg = Segmented(["日", "周", "月"], v["ui_calendar_view"])
        c4.add(self.cal_seg)
        self.calendar = CalendarGrid()
        c4.add(self.calendar)
        lay.addWidget(c4)

        # 拟人搜索默认设置：可编辑的「拟人强度」与全部拟人搜索默认参数
        # 放在同一张卡里；只读值常驻显示，不弹任何鼠标悬浮提示。
        ds = QFrame()
        ds.setObjectName("flat")
        dsl = QVBoxLayout(ds)
        dsl.setContentsMargins(12, 10, 12, 10)
        dsl.setSpacing(6)
        ds_title = QLabel(tr(lang, "default_summary"))
        ds_title.setStyleSheet("font-size:13px;font-weight:700;")
        dsl.addWidget(ds_title)
        ds_hint = QLabel(tr(lang, "default_summary_hint"))
        ds_hint.setObjectName("tiny")
        dsl.addWidget(ds_hint)
        self.level_seg = Segmented(["低", "中", "高"], v["ui_search_level"])
        dsl.addWidget(Row(tr(lang, "intensity"), self.level_seg))
        self.defaults_grid = QGridLayout()
        self.defaults_grid.setSpacing(5)
        dsl.addLayout(self.defaults_grid)
        lay.addWidget(ds)

        # 工具：导出 / 抓取 / 恢复默认，都是一次点击的操作，不再是设置项
        tools = QHBoxLayout()
        b1 = QPushButton(tr(lang, "export_logs"))
        b1.clicked.connect(app.export_logs)
        b2 = QPushButton(tr(lang, "capture_html"))
        b2.clicked.connect(app.capture_html)
        tools.addWidget(b1, 1)
        tools.addWidget(b2, 1)
        lay.addLayout(tools)
        reset = QPushButton(tr(lang, "reset"))
        reset.setFlat(True)
        reset.clicked.connect(app.reset_defaults)
        lay.addWidget(reset)
        lay.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        self._connect_signals()
        self._enter_widgets = [c1, c2, c3, c4, ds]
        # 全界面不安装任何悬浮提示管理器，说明一律用常驻只读文字呈现。
        self.refresh_times()
        self.refresh_defaults_summary()

    def enter_widgets(self):
        return list(getattr(self, "_enter_widgets", []))

    def _connect_signals(self) -> None:
        b = self.app.set_pref
        self.lang_seg.changed.connect(lambda t: b("ui_language", t))
        self.theme_seg.changed.connect(lambda t: b("ui_theme", t))
        self.level_seg.changed.connect(lambda t: b("ui_search_level", t))
        self.sync_seg.changed.connect(lambda t: b("ui_sync_mode", t))
        self.remind_sw.toggled.connect(lambda x: b("ui_remind_mode", bool(x)))
        self.background_sw.toggled.connect(
            lambda x: b("ui_background_mode", bool(x)))
        self.autostart_sw.toggled.connect(
            lambda x: b("ui_launch_at_login", bool(x)))
        self.schedule_sw.toggled.connect(
            lambda x: b("ui_schedule_enabled", bool(x)))
        self.cal_seg.changed.connect(lambda t: b("ui_calendar_view", t))

    def sync_from_settings(self) -> None:
        """落盘后回填 9 个控件，全程阻断信号避免回环。"""
        v = self.app.prefs
        widgets = [self.lang_seg, self.theme_seg, self.level_seg,
                   self.sync_seg, self.remind_sw, self.background_sw,
                   self.autostart_sw, self.schedule_sw, self.cal_seg]
        for w in widgets:
            w.blockSignals(True)
        try:
            self.lang_seg.set_value(v["ui_language"])
            self.theme_seg.set_value(v["ui_theme"])
            self.level_seg.set_value(v["ui_search_level"])
            self.sync_seg.set_value(str(v["ui_sync_mode"]))
            self.remind_sw.setChecked(bool(v["ui_remind_mode"]))
            self.background_sw.setChecked(bool(v["ui_background_mode"]))
            self.autostart_sw.setChecked(bool(v["ui_launch_at_login"]))
            self.schedule_sw.setChecked(bool(v["ui_schedule_enabled"]))
            self.cal_seg.set_value(v["ui_calendar_view"])
        finally:
            for w in widgets:
                w.blockSignals(False)
        self.refresh_times()
        self.refresh_defaults_summary()

    def refresh_defaults_summary(self) -> None:
        """5 行拟人参数只读展示；强度切换后由 sync_from_settings 调用。

        旧 QLabel 必须先 setParent(None)：deleteLater() 只是排队删除，控件在
        事件循环处理前仍挂在父级上被绘制，连续切档会让新旧两行文字叠在一起
        （设置页长图里「16–29 秒 / 35%」的重影就是这个原因）。
        """
        _clear_layout(self.defaults_grid)
        rows = vm.default_settings_summary(self.app.prefs, self.app.lang)
        for i, (label, value) in enumerate(rows):
            cell = QLabel(f"{label} · <b>{value}</b>")
            cell.setTextFormat(Qt.TextFormat.RichText)
            cell.setObjectName("muted")
            cell.setWordWrap(True)
            self.defaults_grid.addWidget(cell, i // 2, i % 2)

    def refresh_times(self) -> None:
        _clear_layout(self.times_grid)
        for i, text in enumerate(self.app.prefs.get("ui_schedule_times", [])):
            chip = TimeChip(text)
            chip.removed.connect(self.app.remove_schedule_time)
            self.times_grid.addWidget(chip, i // 3, i % 3)

    def set_calendar(self, cells, title, summary, empty_text="") -> None:
        self.calendar.set_cells(cells, title, summary, empty_text)


# ---------------------------------------------------------------- 账户门面

class _AccountFacade:
    """``Application.apply_settings`` / 开机自启回退用到的最小接口。"""

    def __init__(self, window) -> None:
        self.window = window

    def collect(self) -> dict:
        keys = ("sync_interval_minutes", "sync_interval_value_minutes",
                "remind_time", "remind_time_value", "evening_remind_time",
                "evening_remind_time_value", "auto_run_enabled", "auto_run_times",
                "silent_when_all_done", "notify_after_sync", "close_to_tray",
                "sleep_when_all_done", "launch_at_login")
        s = self.window.settings
        return {k: s.get(k) for k in keys}

    def set_autostart(self, enabled: bool) -> None:
        sw = getattr(self.window.settings_page, "autostart_sw", None)
        if sw is not None:
            sw.blockSignals(True)
            sw.setChecked(bool(enabled))
            sw.blockSignals(False)


# ---------------------------------------------------------------- 唯一窗口

class ModernMainWindow(QWidget):
    """Application.window：单列界面 + 经典控制器门面。

    ``controller`` 为 ``Application`` 实例；为 None 时进入离线预览模式
    （只读同步可用，主按钮提示需要控制器）。
    """

    def __init__(self, controller=None, settings: Optional[Settings] = None,
                 store: Optional[Store] = None) -> None:
        super().__init__()
        self.setObjectName("modernRoot")
        self.controller = controller
        # SearchPanel._on_run_clicked 用 parent.app 读控制器；保留别名。
        self.app = controller
        self.settings = controller.settings if controller else (settings or Settings())
        self.store = controller.store if controller else (store or Store())
        ctl_notifier = getattr(controller, "notifier", None) if controller else None
        self.notifier = ctl_notifier or Notifier(logger=self.log)
        # 隐藏项与统一管理模式：首次启动 / 旧配置升级时写一次推荐默认，
        # 之后不再覆盖用户通过精简控件做出的选择。
        if not self.settings.get(vm.SIMPLIFY_MARKER):
            sp = self.settings.get("search_panel", {}) or {}
            try:
                current_level = int((sp or {}).get("level", 2) or 2)
            except (TypeError, ValueError, AttributeError):
                current_level = 2
            self.settings.update({**vm.defaults_patch(current_level),
                                  vm.SIMPLIFY_MARKER: True})
        # 旧配置里 5 轮一次性升级为默认的 12 轮（用户自定义过的值不动）。
        repeat_patch = vm.repeat_upgrade_patch(self.settings)
        if repeat_patch:
            self.settings.update(repeat_patch)
        # 旧 search_panel 只有 level/repeat：按当前档位补齐 5 项拟人参数。
        intensity_patch = vm.intensity_upgrade_patch(self.settings)
        if intensity_patch:
            self.settings.update(intensity_patch)
        self.prefs = settings_values(self.settings)
        self.lang = resolve_language(self.prefs["ui_language"])
        theme.set_scheme(theme.resolve_theme(self.prefs["ui_theme"]))
        self._workers: List[QThread] = []
        self._login_ok: Optional[bool] = None
        self._log_lines: List[str] = []
        self._syncing = False
        self._stopping = False

        self.setWindowTitle(APP_TITLE)
        # 单列弹窗式布局：固定逻辑宽度，避免长状态文案 / 最大化把窗口撑宽。
        # 392 与浏览器插件版 popup 的视觉宽度一致（高 DPI 由系统统一缩放）。
        self.setFixedWidth(392)
        self.setMinimumHeight(560)
        self.resize(392, 760)

        # ---- 经典控制器要求的窗口接口（逐字沿用 1.3.0 的类与信号） ----
        self.runner = SearchRunner(self)
        self.quick = QuickPanel(self)
        self.quick.hide()
        self.quick.run_pending_requested.connect(self._run_pending)
        self.quick.stop_requested.connect(self.stop_running)
        self.search = SearchPanel(self, self.runner, self.settings, self.save_settings)
        self.search.hide()
        self.act_sync = QAction("立即同步", self)
        self.act_sync.triggered.connect(lambda: self.sync_now())
        self.act_refresh = QAction("刷新界面", self)
        self.act_refresh.triggered.connect(self.refresh_all)
        self.addAction(self.act_sync)
        self.addAction(self.act_refresh)
        self.account = _AccountFacade(self)

        self.stack = QStackedWidget()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack)
        self.rebuild_ui()
        self.setStyleSheet(theme.stylesheet())
        self._wire_runner()
        self.refresh()

    def changeEvent(self, event) -> None:
        """最大化会把单列布局拉成一张大宽屏；回到普通尺寸。"""
        if event.type() == QEvent.Type.WindowStateChange and self.isMaximized():
            QTimer.singleShot(0, self.showNormal)
        super().changeEvent(event)

    # ------------------------------------------------------------ 界面装配

    def rebuild_ui(self) -> None:
        effects.stop_animations(self)   # 避免旧控件动画在 deleteLater 后打空目标
        index = self.stack.currentIndex() if self.stack.count() else 0
        while self.stack.count():
            w = self.stack.widget(0)
            self.stack.removeWidget(w)
            w.deleteLater()
        self.home = HomePage(self)
        self.settings_page = SettingsPage(self)
        self.stack.addWidget(self.home)
        self.stack.addWidget(self.settings_page)
        self.stack.setCurrentIndex(1 if index == 1 else 0)
        self.home.set_login_badge(self._login_ok)
        self.setStyleSheet(theme.stylesheet())
        # 对齐插件版 .enter：逐项淡入、依次错开（动画恒定开启）
        effects.stagger_enter(self.stack.currentWidget().enter_widgets())

    def show_settings(self) -> None:
        self.stack.setCurrentWidget(self.settings_page)
        self.settings_page.sync_from_settings()   # 内含默认摘要刷新
        self.refresh_calendar()
        effects.stagger_enter(self.settings_page.enter_widgets())

    def show_home(self) -> None:
        self.stack.setCurrentWidget(self.home)
        self.refresh()
        effects.stagger_enter(self.home.enter_widgets())

    # ------------------------------------------------------------ 控制器信号

    def _wire_runner(self) -> None:
        self.runner.status.connect(self.set_status_message)
        self.runner.log.connect(lambda a, d: self.log(f"{a} {d}".strip()[:500]))
        self.runner.progress.connect(
            lambda cur, total: self.set_status_message(f"搜索进度 {cur}/{total}"))
        self.runner.finished.connect(
            lambda report: self.set_status_message(
                f"完成：成功 {report.ok_count} / 失败 {report.fail_count}"))
        self.runner.failed.connect(
            lambda msg: self.set_status_message("运行失败：" + browser_error_hint(msg)))
        self.runner.running_changed.connect(lambda _a: self.update_running_state())

    # ------------------------------------------------------------ 数据绑定

    def refresh(self) -> None:
        day = day_str()
        try:
            points = self.store.page_points(day)
            tasks = list(self.store.tasks_of_day(day))
            prog = progress_of(tasks)
            self.home.update_data(points, tasks, prog)
        except Exception as exc:
            self.home.set_status("读取本地数据失败：" + redact(str(exc))[:120])
        self.refresh_calendar()

    def refresh_calendar(self) -> None:
        try:
            rows = self.store.all_days()
            today = datetime.now().date()
            mode = self.prefs.get("ui_calendar_view", "周")
            month_line = (f"{today.year} / {today.month:02d}　"
                          f"{tr(self.lang, 'cal_month_total')} "
                          f"{month_total(rows, today.year, today.month)}")
            if mode == "月":
                cells = calendar_months(rows, today)
                title = tr(self.lang, "cal_month_title")
            elif mode == "日":
                cells = calendar_days(rows, today.year, today.month)
                title = month_line
            else:
                cells = calendar_week(rows, today)
                title = month_line
            self.settings_page.set_calendar(
                cells, title, calendar_summary(cells, self.lang),
                empty_text=tr(self.lang, "cal_empty"))
        except Exception:
            pass

    # ------------------------------------------------------------ 门面方法
    # 这些方法名与 1.3.0 MainWindow 一致，Application 无需改动即可驱动本窗口。

    def refresh_all(self) -> None:
        self.refresh()
        try:
            self.quick.update(self.store)
        except Exception:
            pass

    def set_busy(self, busy: bool, text: str = "") -> None:
        """统一由 update_running_state() 重算所有入口，避免同步/运行两套状态打架。

        同步结束时必须把状态行从最后一条进度（如「解析到 N 项任务，正在保存…」）
        切走，否则界面会一直挂在「正在…」的蓝色忙碌态，看起来像卡死。
        """
        self.update_running_state()
        if text:
            self.set_status_message(text)
        elif not busy and not self.runner.is_running():
            running = self.controller is not None and self.controller.auto_running()
            if not running:
                self.set_status_message(tr(self.lang, "status.synced"))

    def set_status_message(self, text: str) -> None:
        if hasattr(self, "home"):
            self.home.set_status(text or tr(self.lang, "status.ready"))

    def set_login_status(self, text: str, ok: Optional[bool] = None) -> None:
        self._login_ok = ok
        if hasattr(self, "home"):
            self.home.set_login_badge(ok)
        self.log("登录状态：" + str(text))

    def update_running_state(self) -> None:
        """单一状态源：运行期变停止按钮 + 全面禁用同步；同步期只禁用按钮。"""
        ctl = self.controller
        running = bool(self.runner.is_running())
        if ctl is not None:
            running = bool(ctl.auto_running())
        prev_running = bool(getattr(self, "_prev_running", False))
        self._prev_running = running
        syncing = bool(getattr(self, "_syncing", False)) or (
            ctl is not None and bool(getattr(ctl, "_syncing", False)))
        # 真正收尾后自动解锁「正在停止」状态，避免按钮永久禁用。
        self._stopping = bool(self._stopping) and running
        if hasattr(self, "home"):
            self.home.set_running(running, self._stopping)
            self.home.set_busy(syncing)
        # 隐藏代理保留经典语义；托盘菜单不再依赖它的 enabled 决定能否点。
        self.quick.set_busy(syncing and not running)
        self.act_sync.setEnabled(not (running or syncing))
        if ctl is not None:
            ctl.update_tray_actions()
            # 运行 → 空闲的边沿：补上期间被拦下的同步（活动点击 / 打开任务页 /
            # 用户主动停止都走这里，不必给每种结束方式单独挂信号）。
            if prev_running and not running:
                QTimer.singleShot(0, ctl._on_auto_run_finished)

    def run_pending_tasks(self, settings=None) -> bool:
        """逐字沿用 MainWindow.run_pending_tasks：仅执行未完成的电脑端搜索。"""
        if self.runner.is_running():
            self.set_status_message("电脑端拟人搜索正在执行，跳过重复启动")
            return False
        config = settings if settings is not None else self.search._collect()
        params = SearchRunner.for_pending(self.store, config)
        if params is not None and not params["keyword_sources"] and not params["keywords"]:
            self.set_status_message("搜索词来源为空，请在设置中选择热搜来源或填写关键词")
            return False
        if params is None:
            self.set_status_message("当前没有未完成的电脑端搜索，不启动拟人搜索")
            return False
        return self.runner.start(params)

    def stop_running(self) -> None:
        """防误操作：先锁定按钮，再停止；期间不再响应任何启动/同步动作。"""
        self._stopping = True
        if hasattr(self, "home"):
            self.home.set_running(True, stopping=True)
            self.home.set_status(tr(self.lang, "status.stopping"))
        if self.controller is not None:
            self.controller.cancel_pending_actions()
            self.runner.stop()
            self.controller._opener.stop()
        else:
            self.runner.stop()
        self.update_running_state()

    # ------------------------------------------------------------ 主按钮

    def _run_pending(self):
        """与托盘「自动完成未完成任务」完全同一入口。"""
        if self.controller is not None:
            return self.controller.run_pending_tasks()
        self.set_status_message("离线预览模式：需要应用控制器才能执行自动完成任务")
        return False

    def primary_clicked(self) -> None:
        """点击「自动完成任务」→ Application.run_pending_tasks()。

        运行期若被调用（例如快捷键/外部入口），一律视为停止请求，绝不重复启动。
        """
        if getattr(self, "_stopping", False) or (
                self.controller is not None and self.controller.auto_running()):
            self.log("运行期间忽略重复启动，转为停止请求")
            self.stop_running()
            return
        self.log("点击自动完成任务（经典链路）")
        self._run_pending()

    # ------------------------------------------------------------ 同步

    def sync_now(self) -> None:
        if self.controller is not None:
            self.controller.sync_now()
            return
        # 离线预览：本地只读同步，不参与经典自动链路
        if self._syncing:
            return
        self._syncing = True
        self.home.set_status(tr(self.lang, "status_syncing"))
        runner = SyncRunner(self.store, day_str(), self)
        runner.progress.connect(lambda m: self.home.set_status(m))
        runner.ok.connect(self._on_preview_sync_ok)
        runner.fail.connect(self._on_preview_sync_fail)
        runner.finished.connect(lambda: self._forget_worker(runner))
        self._workers.append(runner)
        runner.start()

    def _on_preview_sync_ok(self, out: dict) -> None:
        self._syncing = False
        self._login_ok = True
        self.home.set_login_badge(True)
        self.refresh()
        self.home.set_status(tr(self.lang, "status_ok"))
        self.log("预览同步成功")

    def _on_preview_sync_fail(self, message: str) -> None:
        self._syncing = False
        if "未登录" in message or "登录页" in message:
            self._login_ok = False
            self.home.set_login_badge(False)
        self.home.set_status(message)
        self.log("预览同步失败（保留上次快照）：" + message)

    def _forget_worker(self, worker) -> None:
        self._workers = [w for w in self._workers if w is not worker]

    # ------------------------------------------------------------ 登录

    def open_login(self) -> None:
        if self.controller is not None:
            self.controller.login()
            return
        dlg = LoginDialog(self)
        dlg.login_state_changed.connect(self._set_login_state)
        QTimer.singleShot(0, dlg.check_status)
        dlg.exec()
        self.refresh()

    def _set_login_state(self, ok: bool) -> None:
        self._login_ok = bool(ok)
        self.home.set_login_badge(self._login_ok)

    # ------------------------------------------------------------ 设置

    def save_settings(self, data: dict) -> bool:
        if self.controller is not None:
            return self.controller.save_settings(data)
        return self.settings.update(data)

    def set_pref(self, key: str, value: Any) -> None:
        patch = vm.pref_to_settings(self.prefs, key, value)
        if self.controller is not None:
            self.controller.apply_settings(patch)
        else:
            self.settings.update(patch)
        self.prefs = vm.settings_values(self.settings)
        try:
            self.search._load_settings()      # 经典 SearchPanel 同步回填
        except Exception:
            pass
        self.log(f"设置 {key} = {value} → {patch}")

        if key == "ui_theme":
            theme.set_scheme(theme.resolve_theme(value))
            self.rebuild_ui()
            self.refresh()
            effects.fade_in(self)   # 对齐 View Transition
        elif key == "ui_language":
            self.lang = resolve_language(value)
            self.rebuild_ui()
            self.refresh()
            effects.fade_in(self)
        elif key == "ui_calendar_view":
            self.refresh_calendar()
        self.settings_page.sync_from_settings()

    def add_schedule_time(self) -> None:
        """点一次 + 就追加一个时间点，无需输入 HH:MM。"""
        times = list(self.prefs.get("ui_schedule_times", []))
        candidate = vm.next_schedule_time(times)
        if candidate is None:
            msg = ("启动时间最多 10 个" if len(times) >= vm.SCHEDULE_MAX
                   else "08:00–23:30 已无可用时段")
            self.set_status_message(msg)
            self.log("未追加启动时间：" + msg)
            return
        try:
            new_times = normalize_run_times(times + [candidate])
        except ValueError as exc:
            self.set_status_message(str(exc))
            return
        self.set_pref("ui_schedule_times", new_times)

    def remove_schedule_time(self, value: str) -> None:
        times = [t for t in self.prefs.get("ui_schedule_times", []) if t != value]
        self.set_pref("ui_schedule_times", times)

    def reset_defaults(self) -> None:
        ret = QMessageBox.question(
            self, tr(self.lang, "reset"),
            "恢复全部设置与隐藏项的默认值（历史记录和登录会话不受影响）。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        patch = vm.reset_patch()
        if self.controller is not None:
            self.controller.apply_settings(patch)
        else:
            self.settings.update(patch)
        self.prefs = vm.settings_values(self.settings)
        self.lang = resolve_language(self.prefs["ui_language"])
        theme.set_scheme(theme.resolve_theme(self.prefs["ui_theme"]))
        try:
            self.search._load_settings()
        except Exception:
            pass
        self.rebuild_ui()
        self.refresh()
        self.log("已恢复默认设置（经典 Settings 键）")

    # ------------------------------------------------------------ 日志 / 抓取

    def log(self, message: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {redact(message)}"
        self._log_lines.append(line)
        self._log_lines = self._log_lines[-500:]
        if self.prefs.get("ui_log_enabled", True):
            try:
                with (data_dir() / "run.log").open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass

    def export_logs(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr(self.lang, "export_logs"),
            str(data_dir() / "run-log.zip"), "Zip (*.zip)")
        if not path:
            return
        try:
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("run.log", "\n".join(self._log_lines) + "\n")
                legacy = data_dir() / "run.log"
                if legacy.exists():
                    z.write(legacy, arcname="run.log")
                z.writestr("README.txt", "运行日志已脱敏；分享前请再自行检查。")
            self.home.set_detail("运行日志已导出：" + path)
        except Exception as exc:
            self.home.set_detail("导出失败：" + redact(str(exc))[:100])

    def capture_html(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr(self.lang, "capture_html"),
            str(data_dir() / "rewards-pages.zip"), "Zip (*.zip)")
        if not path:
            return
        self.home.set_detail("正在只读抓取 /dashboard 与 /earn …")
        runner = CaptureRunner(Path(path), self)
        runner.done.connect(lambda p: (self.home.set_detail("已保存：" + p),
                                       self.log("已导出关键网页 HTML zip")))
        runner.fail.connect(lambda m: self.home.set_detail(m))
        runner.finished.connect(lambda: self._forget_worker(runner))
        self._workers.append(runner)
        runner.start()

    # ------------------------------------------------------------ 关闭

    def closeEvent(self, event) -> None:
        for w in list(self._workers):
            try:
                if w.isRunning():
                    w.requestInterruption()
                    w.wait(5000)
            except RuntimeError:
                pass
        ctl = self.controller
        if (ctl is not None and self.settings.get("close_to_tray", True)
                and QSystemTrayIcon.isSystemTrayAvailable()):
            event.ignore()
            self.hide()
            return
        event.accept()
        if ctl is not None:
            QTimer.singleShot(0, ctl.quit)

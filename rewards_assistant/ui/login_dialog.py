# -*- coding: utf-8 -*-
"""登录对话框：由用户本人在浏览器里登录，应用不接触任何 Cookie 文本。

旧版让用户在 F12 里复制整行 Cookie 粘贴到文本框，凭证经过剪贴板 / 文本框 /
磁盘 / 日志；本版改成：

* 「打开浏览器登录」→ 应用用独立持久化 profile 打开可见 Edge 窗口；
* 账号、密码、MFA 全部由用户本人完成，应用不代填、不读取、不保存凭证；
* **窗口不会自动关闭**：用户点「我已完成登录」才结束并复查会话，
  避免页面刚加载 / MFA 中间页被误判成「已登录」而一闪即过；
* 所有使用同一 profile 的动作（登录窗口 / 检测会话 / 清除会话）经
  ``profile_guard`` 串行，并自动清理上次崩溃残留的 Singleton 锁；
* 后台线程**由对话框自己持有 Python 引用**，不用 ``deleteLater``：
  Qt 把 C++ 对象删掉后，Python wrapper 会变成悬空对象，再调
  ``isRunning()`` 就抛 ``RuntimeError: Internal C++ object already deleted``。

对外仍保留 ``LoginDialog`` 与 ``login_state_changed`` 信号，``app.py`` 无需改动。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from ..auth import AuthManager
from ..config import LOGIN_WINDOW_SECONDS
from ..session import (
    DEGRADED, SIGNED_IN, SIGNED_OUT, TRANSIENT_ERROR, ProfileBusyError,
    legacy_cookie_notice, login_poll_state, profile_guard, short_error,
)

BASE = "https://rewards.bing.com"


def worker_running(worker) -> bool:
    """安全地判断 QThread 是否仍在跑。

    ``deleteLater`` / Qt 内部回收会让 PySide6 的 Python wrapper 变成悬空对象，
    此时访问任何方法都抛 ``RuntimeError``。统一在这里吞掉并当作「不在运行」，
    调用方随后把引用置 None，避免再次触碰。
    """
    if worker is None:
        return False
    try:
        return bool(worker.isRunning())
    except RuntimeError:
        return False


class ProbeWorker(QThread):
    """后台只读探测，避免界面卡住。"""

    done = Signal(str, str)

    def run(self) -> None:
        try:
            st = AuthManager().status()
            self.done.emit(st.state, st.reason)
        except Exception as exc:
            self.done.emit(TRANSIENT_ERROR, short_error(exc))


class LoginWorker(QThread):
    """打开可见窗口并**保持**它，直到用户点「我已完成登录」或超时。

    注意构造签名：``parent`` 是第一个参数（与 QThread 惯例一致）。
    旧版写成 ``__init__(wait_seconds, parent)``，而对话框调用 ``LoginWorker(self)``，
    把 LoginDialog 当成 wait_seconds 传进去，随后 ``self.wait_seconds // 3``
    直接抛 ``unsupported operand type(s) for //: 'LoginDialog' and 'int'``。
    """

    done = Signal(bool, str)
    state = Signal(str)
    detected = Signal(bool)

    def __init__(self, parent=None, wait_seconds: int = LOGIN_WINDOW_SECONDS) -> None:
        super().__init__(parent)
        self.wait_seconds = int(wait_seconds)
        self._detected = False

    def run(self) -> None:
        self.state.emit("正在准备浏览器配置目录（可能需等待上一次会话操作结束）…")
        try:
            from playwright.sync_api import sync_playwright

            from ..config import profile_dir

            with profile_guard(profile_dir()):
                with sync_playwright() as pw:
                    ctx = pw.chromium.launch_persistent_context(
                        user_data_dir=str(profile_dir()),
                        channel="msedge", headless=False,
                    )
                    try:
                        page = ctx.pages[0] if ctx.pages else ctx.new_page()
                        page.goto(BASE + "/", wait_until="domcontentloaded")
                        elapsed, confirmed = 0, 0
                        while (elapsed < self.wait_seconds
                               and not self.isInterruptionRequested()):
                            page.wait_for_timeout(3000)
                            elapsed += 3
                            if self.isInterruptionRequested():
                                break
                            try:
                                html = page.content()
                                current_url = page.url
                            except Exception:
                                continue
                            ok, confirmed, message = login_poll_state(
                                html, elapsed, confirmed, current_url)
                            self.state.emit(message)
                            if ok and not self._detected:
                                self._detected = True
                                self.detected.emit(True)
                        # 不因「检测到登录」提前 return：窗口必须由用户收尾
                        self.done.emit(self._detected,
                                       "登录窗口已关闭，请点「检测会话」复查。")
                    finally:
                        ctx.close()  # 释放 profile，之后才能跑只读探测
        except ProfileBusyError as exc:
            self.done.emit(False, str(exc))
        except Exception as exc:
            self.done.emit(False, "无法打开浏览器：" + short_error(exc))


class LogoutWorker(QThread):
    """后台清除独立 profile 登录态。"""

    done = Signal(str)

    def run(self) -> None:
        try:
            from ..session import clear_local_session

            clear_local_session()
            self.done.emit("已清除本地会话；历史积分数据不受影响。")
        except ProfileBusyError as exc:
            self.done.emit(str(exc))
        except Exception as exc:
            self.done.emit("清除失败：" + short_error(exc))


class LoginDialog(QDialog):
    """浏览器会话登录 / 五态检测 / 清除本地会话。"""

    login_state_changed = Signal(bool)

    def __init__(self, parent: Optional[QDialog] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("登录 Microsoft Rewards")
        self.setMinimumWidth(580)
        self._probe: Optional[ProbeWorker] = None
        self._login: Optional[LoginWorker] = None
        self._logout: Optional[LogoutWorker] = None
        self._pending_login = False

        tip = QLabel(
            "<h3>浏览器会话登录</h3>"
            "<ol>"
            "<li>点「打开浏览器登录」，应用会打开一个独立的 Edge 窗口</li>"
            "<li>在该窗口里<b>由你本人</b>完成账号登录（含短信 / MFA 验证）</li>"
            "<li>回到本对话框点「<b>我已完成登录</b>」，窗口才会关闭并自动检测</li>"
            "</ol>"
            "<p>登录窗口<b>不会自动关闭</b>；应用不代填密码、不读取、不导出、"
            "不打印 Cookie，会话由浏览器保存在本机独立配置目录。</p>"
            "<p style='color:#c92a2a'>同步只读取 /dashboard 与 /earn 两个页面，"
            "不搜索、不点击活动卡、不代领积分；奖励搜索与领取请在官网手动完成。</p>"
        )
        tip.setWordWrap(True)
        tip.setOpenExternalLinks(True)

        self.status = QLabel("尚未检测会话状态；点「检测会话」可随时复查。")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#495057;font-size:12px;")

        self.notice = QLabel("")
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet("color:#c92a2a;font-size:12px;")
        note = legacy_cookie_notice()
        if note:
            self.notice.setText(note)

        self.login_btn = QPushButton("打开浏览器登录")
        self.login_btn.setDefault(True)
        self.login_btn.clicked.connect(self._on_login_button)
        self.probe_btn = QPushButton("检测会话")
        self.probe_btn.clicked.connect(self.check_status)
        self.clear_btn = QPushButton("清除本地会话")
        self.clear_btn.clicked.connect(self.clear_session)
        self.close_btn = QPushButton("关闭")
        self.close_btn.clicked.connect(self.close)

        row = QHBoxLayout()
        row.addWidget(self.login_btn)
        row.addWidget(self.probe_btn)
        row.addWidget(self.clear_btn)
        row.addStretch(1)
        row.addWidget(self.close_btn)

        lay = QVBoxLayout(self)
        lay.addWidget(tip)
        lay.addWidget(self.notice)
        lay.addWidget(self.status)
        lay.addStretch(1)          # 状态紧贴说明，不再被拉到对话框中间
        lay.addLayout(row)

        # 不在构造时自动探测：它会打开第二个持久 profile，与登录窗口抢
        # SingletonLock（Chromium 报 "profile is already in use"）。

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _is_running(worker) -> bool:
        return worker_running(worker)

    def _set_busy(self, busy: bool, text: str = "", color: str = "#2f6fed") -> None:
        for btn in (self.login_btn, self.probe_btn, self.clear_btn):
            btn.setEnabled(not busy)
        if text:
            self.status.setText(text)
        self.status.setStyleSheet(f"color:{color if busy else '#495057'};font-size:12px;")

    def _set_login_running(self, running: bool, text: str = "") -> None:
        self.login_btn.setEnabled(True)   # 这个按钮要用来「完成登录」
        self.login_btn.setText(
            ("我已完成登录" + ("（已检测到会话）" if text else ""))
            if running else "打开浏览器登录")
        self.probe_btn.setEnabled(not running)
        self.clear_btn.setEnabled(not running)
        self.close_btn.setEnabled(not running)

    def _apply_state(self, state: str, reason: str) -> None:
        # 登录窗口开着时，迟到的旧探测结果不能覆盖窗口状态
        if self._is_running(self._login):
            return
        ok = state in (SIGNED_IN, DEGRADED)
        if state == SIGNED_IN:
            label, color = "✅ 会话有效，可以只读同步。", "#2b8a3e"
        elif state == DEGRADED:
            label, color = "⚠ 会话可用但部分页面未读到，将降级同步。", "#e67700"
        elif state == SIGNED_OUT:
            label, color = "❌ 未登录：请点「打开浏览器登录」由本人完成登录。", "#c92a2a"
        elif state == TRANSIENT_ERROR:
            label, color = "⚠ " + (reason or "服务暂时不可用"), "#e67700"
        else:
            label, color = "❓ 会话状态未知，请点「检测会话」复查。", "#495057"
        self._set_busy(False, label, color)
        self.status.setStyleSheet(f"color:{color};font-size:12px;")
        self.login_state_changed.emit(ok)
        # 之前因「正在检测会话」而排队的登录请求，现在可以安全启动
        if getattr(self, "_pending_login", False):
            self._pending_login = False
            QTimer.singleShot(0, self.open_login)

    # ------------------------------------------------------------ 线程收尾
    # 不接 deleteLater：让 Python 一直持有 QThread，直到我们主动置 None。
    # 否则 C++ 对象被 Qt 回收后，再调 isRunning() 会抛 RuntimeError。

    def _on_probe_finished(self, worker) -> None:
        if self._probe is worker:
            self._probe = None

    def _on_login_finished(self, worker) -> None:
        # done 已处理过就别重复；这里只兜底异常退出时恢复按钮
        if self._login is worker:
            self._login = None
            self._set_login_running(False)

    def _on_logout_finished(self, worker) -> None:
        if self._logout is worker:
            self._logout = None

    def _stop_login_worker(self) -> None:
        """关闭对话框 / 超时兜底：中断并等线程释放 profile，幂等。"""
        worker = self._login
        if worker is None:
            return
        self._login = None
        try:
            worker.requestInterruption()
            worker.wait(20000)
        except RuntimeError:
            pass  # wrapper 已失效，线程本身已结束

    # ------------------------------------------------------------------ 操作

    def check_status(self) -> None:
        if self._is_running(self._probe):
            return  # 已有探测在跑，避免重复启动
        self._set_busy(True, "正在只读检测会话…")
        worker = ProbeWorker(self)
        self._probe = worker
        worker.done.connect(self._apply_state)
        worker.finished.connect(lambda w=worker: self._on_probe_finished(w))
        worker.start()

    def _on_login_button(self) -> None:
        # 必须用安全判断：旧 wrapper 可能已被 Qt 回收
        if self._is_running(self._login):
            self._finish_login()
        else:
            self.open_login()

    def open_login(self) -> None:
        # 正在探测时不要阻塞 GUI 线程 wait()，登记成待办，探测结束后自动打开
        if self._is_running(self._probe):
            self._pending_login = True
            self.status.setText("正在结束会话检测，稍后自动打开登录窗口…")
            self.status.setStyleSheet("color:#2f6fed;font-size:12px;")
            return
        # wrapper 悬空（isRunning 抛 RuntimeError）时清掉引用，照常启动
        if self._probe is not None and not self._is_running(self._probe):
            self._probe = None
        if self._logout is not None and not self._is_running(self._logout):
            self._logout = None

        self._set_login_running(True)
        self.status.setText("已打开浏览器窗口，请在窗口中完成登录…")
        self.status.setStyleSheet("color:#2f6fed;font-size:12px;")
        worker = LoginWorker(self)          # parent 位置正确
        self._login = worker
        worker.state.connect(self.status.setText)
        worker.detected.connect(self._on_login_detected)
        worker.done.connect(self._on_login_done)
        worker.finished.connect(lambda w=worker: self._on_login_finished(w))
        worker.start()

    def _on_login_detected(self, _ok: bool) -> None:
        # 线程结束后迟到的信号不能把按钮又改回「运行中」
        if self._login is not None:
            self._set_login_running(True, "已检测到会话")

    def _finish_login(self) -> None:
        """用户确认完成：中断线程 → 等 ctx.close() 释放 profile → 再只读探测。"""
        self.status.setText("正在关闭登录窗口并复查会话…")
        self.status.setStyleSheet("color:#2f6fed;font-size:12px;")
        worker = self._login
        if worker is None:
            self.check_status()
            return
        closed = True
        try:
            worker.requestInterruption()
            closed = worker.wait(20000)
        except RuntimeError:
            pass
        self._login = None
        self._set_login_running(False)
        if closed:
            self.check_status()
        else:
            self.status.setText("⚠ 窗口关闭超时，请稍后点「检测会话」复查。")
            self.status.setStyleSheet("color:#e67700;font-size:12px;")

    def _on_login_done(self, ok: bool, message: str) -> None:
        # _finish_login 已把 _login 置空，此时状态由 check_status 接管，不覆盖
        if self._login is None:
            return
        self._login = None
        self._set_login_running(False)
        if ok:
            self.status.setText("✅ " + message)
            self.status.setStyleSheet("color:#2b8a3e;font-size:12px;")
        else:
            self.status.setText("⚠ " + message)
            self.status.setStyleSheet("color:#e67700;font-size:12px;")

    def clear_session(self) -> None:
        self._set_busy(True, "正在清除本地会话…")
        worker = LogoutWorker(self)
        self._logout = worker
        worker.done.connect(self._on_logout_done)
        worker.finished.connect(lambda w=worker: self._on_logout_finished(w))
        worker.start()

    def _on_logout_done(self, message: str) -> None:
        self._set_busy(False)
        self.status.setText(message)
        self.status.setStyleSheet("color:#495057;font-size:12px;")
        self.login_state_changed.emit(False)

    # ------------------------------------------------------------ 关闭保护

    def reject(self) -> None:
        """Esc / 关闭：先停掉登录线程，避免 QThread 被析构时仍在跑。"""
        self._stop_login_worker()
        super().reject()

    def closeEvent(self, event) -> None:
        self._stop_login_worker()
        super().closeEvent(event)

# -*- coding: utf-8 -*-
"""登录对话框：由用户本人在浏览器里登录，凭证只加密保存在本机。

登录流程：

* 「打开浏览器登录」→ 应用用独立持久化 profile 打开可见 Edge 窗口；
* 账号、密码、MFA 全部由用户本人完成，应用不代填、不读取账号密码；
* 窗口关闭前把 Microsoft 相关域的 Cookie 交给 ``CookieStore`` 加密落盘，
  之后同步走纯 HTTP，不再启动浏览器；应用不上传、不打印、不写明文；
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
from ..timing import jitter
from ..credentials import CookieStore
from ..session import (
    DEGRADED, SIGNED_IN, SIGNED_OUT, TRANSIENT_ERROR, ProfileBusyError,
    login_poll_state, profile_guard, short_error,
)
from .login_guide import LoginGuideWidget, guide_palette

BASE = "https://rewards.bing.com"


def _dialog_qss(pal: dict) -> str:
    """独立 QDialog 不继承主窗口的 widget 级样式，这里按当前主题配一套。"""
    hover = pal.get("primary_hover", pal["primary"])
    return f"""
QDialog {{ background: {pal['window']}; }}
QLabel#loginTitle {{
    color: {pal['text']}; font-size: 15px; font-weight: 600;
}}
QLabel#loginStatus {{ color: {pal['text_sub']}; font-size: 12px; }}
QPushButton {{
    background: {pal['card']}; color: {pal['text']};
    border: 1px solid {pal['border']}; border-radius: 8px;
    padding: 6px 14px; font-size: 13px; min-height: 18px;
}}
QPushButton:hover {{ border-color: {pal['primary']}; }}
QPushButton:disabled {{ color: {pal['text_muted']}; }}
QPushButton#loginPrimary {{
    background: {pal['primary']}; color: #ffffff; border: none;
}}
QPushButton#loginPrimary:hover {{ background: {hover}; }}
"""


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

    注意构造签名：``parent`` 是第一个参数（与 QThread 惯例一致），
    对话框按 ``LoginWorker(self)`` 调用。
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
                            nap = jitter(2.4, 3.6)
                            page.wait_for_timeout(int(nap * 1000))
                            elapsed += nap
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
                        # 登录窗口是唯一接触浏览器的地方：在关闭前把会话导出为
                        # 本机加密凭证。之后同步走纯 HTTP，不再碰浏览器 profile。
                        try:
                            cookies = ctx.cookies()
                            if cookies:
                                CookieStore().save(cookies)
                        except Exception:
                            pass
                        ctx.close()
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
        self.setMinimumWidth(604)
        self._probe: Optional[ProbeWorker] = None
        self._login: Optional[LoginWorker] = None
        self._logout: Optional[LogoutWorker] = None
        self._pending_login = False

        # 流程用 5 帧循环插画演示（含淡入、流动光点与光标微动），
        # 说明文字压成画板底部随帧切换的一行短句；不再堆整段有序列表与附注。
        self._pal = guide_palette()
        self.setStyleSheet(_dialog_qss(self._pal))

        title = QLabel("登录 Microsoft Rewards")
        title.setObjectName("loginTitle")

        self.guide = LoginGuideWidget()

        self.status = QLabel("尚未检测会话状态")
        self.status.setObjectName("loginStatus")
        self.status.setWordWrap(True)

        self.login_btn = QPushButton("打开浏览器登录")
        self.login_btn.setObjectName("loginPrimary")
        self.login_btn.setDefault(True)
        self.login_btn.clicked.connect(self._on_login_button)
        self.probe_btn = QPushButton("检测会话")
        self.probe_btn.clicked.connect(self.check_status)
        self.clear_btn = QPushButton("清除本地会话")
        self.clear_btn.clicked.connect(self.clear_session)
        self.close_btn = QPushButton("关闭")
        self.close_btn.clicked.connect(self.close)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.login_btn)
        row.addWidget(self.probe_btn)
        row.addWidget(self.clear_btn)
        row.addStretch(1)
        row.addWidget(self.close_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)
        lay.addWidget(title)
        lay.addWidget(self.guide)
        lay.addWidget(self.status)
        lay.addLayout(row)

        # 不在构造时自动探测：它会打开第二个持久 profile，与登录窗口抢
        # SingletonLock（Chromium 报 "profile is already in use"）。

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _is_running(worker) -> bool:
        return worker_running(worker)

    _STATUS_KEYS = {
        "normal": "text_sub", "busy": "primary", "ok": "green",
        "warn": "orange", "err": "warn",
    }

    def _set_status(self, text: str, kind: str = "normal") -> None:
        """状态行统一走主题色，深浅色主题下都可读。"""
        key = self._STATUS_KEYS.get(kind, "text_sub")
        self.status.setText(text)
        self.status.setStyleSheet(
            f"color:{self._pal.get(key, '#7b8798')};font-size:12px;")

    def _set_busy(self, busy: bool, text: str = "",
                  kind: str = "normal") -> None:
        for btn in (self.login_btn, self.probe_btn, self.clear_btn):
            btn.setEnabled(not busy)
        if text:
            self._set_status(text, kind)

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
            label, kind = "✅ 会话有效，可以只读同步。", "ok"
        elif state == DEGRADED:
            label, kind = "⚠ 会话可用但部分页面未读到，将降级同步。", "warn"
        elif state == SIGNED_OUT:
            label, kind = "❌ 未登录：请点「打开浏览器登录」由本人完成登录。", "err"
        elif state == TRANSIENT_ERROR:
            label, kind = "⚠ " + (reason or "服务暂时不可用"), "warn"
        else:
            label, kind = "❓ 会话状态未知，请点「检测会话」复查。", "normal"
        self._set_busy(False, label, kind)
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
        self._set_busy(True, "正在只读检测会话…", "busy")
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
            self._set_status("正在结束会话检测，稍后自动打开登录窗口…", "busy")
            return
        # wrapper 悬空（isRunning 抛 RuntimeError）时清掉引用，照常启动
        if self._probe is not None and not self._is_running(self._probe):
            self._probe = None
        if self._logout is not None and not self._is_running(self._logout):
            self._logout = None

        self._set_login_running(True)
        self._set_status("已打开浏览器窗口，请在窗口中完成登录…", "busy")
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
        self._set_status("正在关闭登录窗口并复查会话…", "busy")
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
            self._set_status("⚠ 窗口关闭超时，请稍后点「检测会话」复查。", "warn")

    def _on_login_done(self, ok: bool, message: str) -> None:
        # _finish_login 已把 _login 置空，此时状态由 check_status 接管，不覆盖
        if self._login is None:
            return
        self._login = None
        self._set_login_running(False)
        if ok:
            self._set_status("✅ " + message, "ok")
        else:
            self._set_status("⚠ " + message, "warn")

    def clear_session(self) -> None:
        CookieStore().clear()
        self._set_busy(True, "正在清除本地会话…", "busy")
        worker = LogoutWorker(self)
        self._logout = worker
        worker.done.connect(self._on_logout_done)
        worker.finished.connect(lambda w=worker: self._on_logout_finished(w))
        worker.start()

    def _on_logout_done(self, message: str) -> None:
        self._set_busy(False)
        self._set_status(message)
        self.login_state_changed.emit(False)

    # ------------------------------------------------------------ 关闭保护

    def reject(self) -> None:
        """Esc / 关闭：先停掉登录线程，避免 QThread 被析构时仍在跑。"""
        self._stop_login_worker()
        super().reject()

    def closeEvent(self, event) -> None:
        self._stop_login_worker()
        super().closeEvent(event)

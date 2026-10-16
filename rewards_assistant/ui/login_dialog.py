"""登录对话框：粘贴 Cookie（不使用浏览器内核，无 Playwright 依赖）。"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from ..auth import AuthManager, CookieSession


class CookieTestWorker(QThread):
    """后台验证 Cookie 是否有效，避免界面卡住。"""

    done = Signal(bool, str)

    def __init__(self, raw: str, parent=None) -> None:
        super().__init__(parent)
        self.raw = raw

    def run(self) -> None:
        try:
            ok = AuthManager().is_signed_in() if not self.raw else _test(self.raw)
            self.done.emit(bool(ok), "" if ok else "验证失败：Cookie 无效或已过期")
        except Exception as exc:
            self.done.emit(False, f"验证出错：{exc}")


def _test(raw: str) -> bool:
    from ..auth import fetch_with_cookies

    try:
        fetch_with_cookies(raw, timeout=20)
        return True
    except Exception:
        return False


class LoginDialog(QDialog):
    """输入并验证 Cookie。"""

    login_state_changed = Signal(bool)

    def __init__(self, parent: Optional[QDialog] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("登录 Microsoft Rewards")
        self.setMinimumWidth(560)
        self._worker: Optional[CookieTestWorker] = None

        tip = QLabel(
            "<h3>粘贴 Cookie 登录</h3>"
            "<ol>"
            "<li>用浏览器打开 <code>rewards.bing.com</code> 并完成登录</li>"
            "<li>按 <b>F12</b> 打开开发者工具 → <b>Network（网络）</b></li>"
            "<li>刷新页面，点任意一个请求 → <b>Request Headers</b></li>"
            "<li>复制 <code>Cookie:</code> 后面的<b>整行</b>内容，粘贴到下面</li>"
            "</ol>"
            "<p style='color:#c92a2a'>Cookie 等同登录凭证，只保存在本机数据目录（权限 600），"
            "不会上传到任何地方。</p>"
        )
        tip.setWordWrap(True)
        tip.setOpenExternalLinks(True)

        self.cookie_edit = QPlainTextEdit()
        self.cookie_edit.setPlaceholderText("在此粘贴 Cookie 内容（形如：a=1; b=2; ...）")
        saved = CookieSession().load()
        if saved:
            self.cookie_edit.setPlainText(saved)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#495057;font-size:12px;")

        self.test_btn = QPushButton("验证并保存")
        self.test_btn.setDefault(True)
        self.test_btn.clicked.connect(self.verify_cookie)
        self.clear_btn = QPushButton("清除已保存 Cookie")
        self.clear_btn.clicked.connect(self.clear_cookie)
        self.close_btn = QPushButton("关闭")
        self.close_btn.clicked.connect(self.accept)

        row = QHBoxLayout()
        row.addWidget(self.test_btn)
        row.addWidget(self.clear_btn)
        row.addStretch(1)
        row.addWidget(self.close_btn)

        lay = QVBoxLayout(self)
        lay.addWidget(tip)
        lay.addWidget(self.cookie_edit, 1)
        lay.addWidget(self.status)
        lay.addLayout(row)

        self._refresh_status()

    # ------------------------------------------------------------------ 内部

    def _refresh_status(self) -> None:
        if CookieSession().load():
            self.status.setText("当前已保存 Cookie，可直接同步；也可重新粘贴覆盖。")
        else:
            self.status.setText("尚未设置 Cookie。")

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self.test_btn.setEnabled(not busy)
        self.status.setText(text)
        if busy:
            self.status.setStyleSheet("color:#2f6fed;font-size:12px;")
        else:
            self.status.setStyleSheet("color:#495057;font-size:12px;")

    # ------------------------------------------------------------------ Cookie

    def verify_cookie(self) -> None:
        raw = self.cookie_edit.toPlainText().strip()
        if not raw:
            self.status.setText("请先粘贴 Cookie 内容")
            return
        self._set_busy(True, "正在验证 Cookie…")
        self._worker = CookieTestWorker(raw, self)
        self._worker.done.connect(self._on_tested)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_tested(self, ok: bool, message: str) -> None:
        self._set_busy(False)
        if ok:
            AuthManager().set_cookies(self.cookie_edit.toPlainText().strip())
            self.status.setText("✅ 验证成功，Cookie 已保存，可以开始同步了。")
            self.status.setStyleSheet("color:#2b8a3e;font-size:12px;")
            self.login_state_changed.emit(True)
        else:
            self.status.setText(f"❌ {message or 'Cookie 无效或已过期，请重新复制。'}")
            self.status.setStyleSheet("color:#c92a2a;font-size:12px;")
            self.login_state_changed.emit(False)

    def clear_cookie(self) -> None:
        AuthManager().logout()
        self.cookie_edit.clear()
        self.status.setText("已清除本地 Cookie。")
        self.status.setStyleSheet("color:#495057;font-size:12px;")
        self.login_state_changed.emit(False)

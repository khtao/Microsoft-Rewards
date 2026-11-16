# -*- coding: utf-8 -*-
"""登录态门面：会话保存在浏览器 profile，应用只做两页只读读取。

应用不读取、不导出、不写入 Cookie 文本。``AuthManager`` 提供给控制器、
同步层与兼容层共用的三个动作：``status()`` / ``fetch_dashboard()`` /
``logout()``。``fetch_dashboard()`` 由 ``/dashboard`` 与 ``/earn`` 的 HTML
合成兼容 dashboard JSON，供 ``rewards_client.parse_dashboard()`` 解析。

只读边界：不 POST、不点击、不搜索、不领取、不注入脚本。
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional

from .readonly_report import build_report, to_legacy_dashboard
from .session import (
    ALLOWED_PATHS, ReadOnlyFetcher, SessionStatus,
    clear_local_session, default_fetcher, probe, redact,
)


class AuthError(RuntimeError):
    """登录 / 会话相关错误（UI 按这个类型捕获）。"""


def _pages_from(status: SessionStatus) -> tuple[str, str]:
    dash = status.pages.get(ALLOWED_PATHS[0])
    earn = status.pages.get(ALLOWED_PATHS[1])
    return ((dash.html if dash and dash.usable else ""),
            (earn.html if earn and earn.usable else ""))


class AuthManager:
    """会话管理门面：探测 / 只读取数 / 退出。"""

    def __init__(self, logger=None, fetcher: Optional[ReadOnlyFetcher] = None) -> None:
        self.log = logger or (lambda msg: None)
        self._fetcher = fetcher

    @property
    def fetcher(self) -> ReadOnlyFetcher:
        if self._fetcher is None:
            self._fetcher = default_fetcher()
        return self._fetcher

    def status(self) -> SessionStatus:
        """五态会话状态（已登录 / 未登录 / 部分可用 / 网络错误 / 未知）。"""
        return probe(self.fetcher)

    def fetch_dashboard(self, timeout: int | tuple = 25) -> Dict[str, Any]:
        """只读两页 → 兼容 dashboard JSON；失败抛 ``AuthError``。"""
        st = self.status()
        if not st.can_sync:
            raise AuthError(redact(st.reason))
        dash_html, earn_html = _pages_from(st)
        self.log("只读读取 /dashboard 与 /earn …")
        report = build_report(dash_html, earn_html)
        if report.signed_out:
            raise AuthError("识别为登录页 / 公开「关于」页：不写假的 0 分，请先登录")
        return to_legacy_dashboard(report, date.today().isoformat(), 0)

    def logout(self) -> None:
        """清除本机登录态（独立 profile 与旧版遗留文件），不动积分历史。"""
        clear_local_session()
        self.log("已清除本地登录态；历史积分数据不受影响")

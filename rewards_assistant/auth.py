"""微软账号登录：仅 Cookie 通道（不依赖 Playwright / 不启动浏览器）。

为什么只留 Cookie
----------------
浏览器登录通道需要 Playwright + 上百 MB 的 Chromium 内核，是打包体积与
依赖数量的主要来源。Cookie 通道用 requests 直接调官方接口，零额外依赖，
换设备也不会因为缺少内核而跑不起来。

Cookie 获取方式（用户自己做一次即可）：
    浏览器登录 rewards.bing.com → F12 → Network → 任选一个请求 →
    Request Headers → 复制 Cookie: 后面的整行

本模块只做**只读**请求，不点任务、不模拟搜索、不调任何写接口。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import requests

from .config import REWARDS_DASHBOARD, USER_INFO_API, data_dir


class AuthError(RuntimeError):
    """登录/会话相关错误。"""


class CookieSession:
    """把用户手动粘贴的 Cookie 存到数据目录，供 requests 直接调用接口。"""

    def __init__(self) -> None:
        self.path = data_dir() / "cookies.txt"

    def load(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def save(self, raw: str) -> None:
        raw = (raw or "").strip()
        if raw:
            self.path.write_text(raw, encoding="utf-8")
            try:
                self.path.chmod(0o600)  # 会话凭证仅本人可读
            except OSError:
                pass
        else:
            self.clear()

    def clear(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass

    @staticmethod
    def parse(raw: str) -> Dict[str, str]:
        """支持 'a=1; b=2' 与 'Cookie: a=1; b=2' 两种粘贴格式。"""
        raw = (raw or "").strip()
        if raw.lower().startswith("cookie:"):
            raw = raw.split(":", 1)[1]
        jar: Dict[str, str] = {}
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                jar[k.strip()] = v.strip()
        return jar


def _headers() -> Dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": REWARDS_DASHBOARD,
    }


def fetch_with_cookies(raw_cookie: str, timeout: int = 25) -> Dict[str, Any]:
    """用 Cookie 直接请求官方接口；失败抛 AuthError。"""
    import json

    jar = CookieSession.parse(raw_cookie)
    if not jar:
        raise AuthError("Cookie 为空或格式不对")
    try:
        resp = requests.get(USER_INFO_API, cookies=jar, headers=_headers(), timeout=timeout)
    except Exception as exc:  # 网络层异常统一转成 AuthError
        raise AuthError(f"网络请求失败：{exc}") from exc
    if resp.status_code != 200:
        raise AuthError(f"接口返回 {resp.status_code}，Cookie 可能已失效")
    try:
        data = resp.json()
    except json.JSONDecodeError:
        raise AuthError("返回内容不是 JSON，Cookie 可能已失效或未登录") from None
    dash = data.get("dashboard") if isinstance(data, dict) else None
    if not isinstance(dash, dict) or not dash.get("userStatus"):
        raise AuthError("未读到账户数据，请确认已登录并重新复制 Cookie")
    return data


class AuthManager:
    """Cookie 会话管理：保存 / 校验 / 读取。"""

    def __init__(self, logger=None) -> None:
        self.log = logger or (lambda msg: None)
        self.cookies = CookieSession()

    # ------------------------------------------------------------- Cookie

    def set_cookies(self, raw: str) -> None:
        self.cookies.save(raw)

    def has_cookies(self) -> bool:
        return bool(self.cookies.load())

    def jar(self) -> Dict[str, str]:
        return CookieSession.parse(self.cookies.load())

    def is_signed_in(self) -> bool:
        """探测 Cookie 是否仍然有效。"""
        raw = self.cookies.load()
        if not raw:
            return False
        try:
            fetch_with_cookies(raw)
            return True
        except AuthError as exc:
            self.log(str(exc))
            return False

    def fetch_dashboard(self, timeout: int = 25) -> Dict[str, Any]:
        """返回原始 dashboard JSON。未登录或过期抛 AuthError。"""
        raw = self.cookies.load()
        if not raw:
            raise AuthError("尚未设置 Cookie，请先在设置里粘贴 Cookie")
        self.log("使用已保存的 Cookie 读取…")
        return fetch_with_cookies(raw, timeout)

    def fetch_page_html(self, url: str, timeout: int = 25) -> str:
        """只读抓取 Rewards 页面 HTML（用于提取页面真实显示的任务名 / 连续天数）。"""
        raw = self.cookies.load()
        if not raw:
            raise AuthError("尚未设置 Cookie")
        try:
            resp = requests.get(url, cookies=CookieSession.parse(raw),
                                headers=_headers(), timeout=timeout)
        except Exception as exc:
            raise AuthError(f"页面读取失败：{exc}") from exc
        if resp.status_code != 200:
            raise AuthError(f"页面返回 {resp.status_code}，Cookie 可能已失效")
        resp.encoding = resp.encoding or "utf-8"
        return resp.text

    def logout(self) -> None:
        """清除本地登录态（只删 Cookie，不动历史数据）。"""
        self.cookies.clear()

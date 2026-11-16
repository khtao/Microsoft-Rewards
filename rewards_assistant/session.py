# -*- coding: utf-8 -*-
"""登录态管理：借用用户本人已登录的 Edge 会话，只读探测两个白名单页面。

会话只存在于应用独立的浏览器持久化 profile 中，由浏览器管理加密与过期；
应用不读取、不导出、不打印 Cookie 文本。只 GET ``/dashboard`` 与 ``/earn``，
不 POST、不点击、不搜索、不领取、不注入脚本。两页内容与 HTTP 状态用于
五态判定：已登录 / 未登录 / 部分可用 / 暂时性网络错误 / 未知；网络错误
绝不会被当成「凭证失效」。

奖励搜索须由用户本人手动进行，可领取积分须在 rewards.bing.com 手动领取。
"""
from __future__ import annotations

import contextlib
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from .readonly_report import is_sign_in_page

BASE = "https://rewards.bing.com"
ALLOWED_PATHS: Sequence[str] = ("/dashboard", "/earn")

SIGNED_IN = "SIGNED_IN"
SIGNED_OUT = "SIGNED_OUT"
DEGRADED = "DEGRADED"
TRANSIENT_ERROR = "TRANSIENT_ERROR"
UNKNOWN = "UNKNOWN"

_NO_STORE_HEADERS = {
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


# ---------------------------------------------------------------- 只读护栏

def assert_readonly(path: str) -> str:
    """路径白名单：同步层永远只能读这两个页面。"""
    if path not in ALLOWED_PATHS:
        raise ValueError(f"只读同步不允许访问 {path!r}；白名单：{ALLOWED_PATHS}")
    return BASE + path


def classify_status(status: Optional[int]) -> str:
    if status is None:
        return "transient"
    if 200 <= status < 300:
        return "ok"
    if status in (401, 403):
        return "signed_out"
    if status == 429 or 500 <= status < 600 or status == 408:
        return "transient"
    return "invalid"


def redact(text: str) -> str:
    """日志脱敏：绝不把会话凭证 / 邮箱 / OAuth 令牌写进运行日志。

    注意：不要对任意「40+ 字符」做通用替换——Playwright 启动日志里全是
    ``--disable-features=...`` 这类长命令行参数，通用替换会把错误日志切得
    支离破碎（旧版就这样把中文提示插进了 command line 中间）。
    """
    t = str(text or "")
    t = re.sub(r"(?i)(set-cookie|cookie|authorization)\s*[:=]\s*[^\r\n;]+",
               r"\1: [已脱敏]", t)
    t = re.sub(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+", "Bearer [已脱敏]", t)
    t = re.sub(r"(?i)\b(code|token|access_token|refresh_token|id_token)"
               r"\s*=\s*[A-Za-z0-9._\-]{16,}", r"\1=[已脱敏]", t)
    t = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[邮箱已脱敏]", t)
    return t


def short_error(exc, limit: int = 220) -> str:
    """把 Playwright / Chromium 的长堆栈压成一句可操作的话，避免刷屏。"""
    raw = redact(str(exc)).strip()
    # Chromium 的失败日志从 "[pid=" 开始附加整条命令行；界面只保留首因
    first = raw.split("[pid=", 1)[0].replace("\n", " ").strip()
    if "Failed to create a ProcessSingleton" in first or "SingletonLock" in first:
        return ("浏览器配置目录被占用或残留了锁文件；已自动清理陈旧锁，"
                "请重试（若仍失败，请先关闭已打开的登录窗口）。")
    if "Executable doesn't exist" in first or "msedge" in first.lower() and "not found" in first.lower():
        return "未找到 Microsoft Edge：请先安装 Edge，或重新执行 playwright install。"
    if "Target page" in first or "Timeout" in first:
        return "页面打开超时，请检查网络后重试。"
    return first[:limit] or "浏览器启动失败，请查看运行日志。"


class ProfileBusyError(RuntimeError):
    """同一个持久化 profile 正被另一个浏览器实例占用。"""


_PROFILE_THREAD_LOCK = threading.RLock()
_SINGLETON_FILES = ("SingletonLock", "SingletonCookie", "SingletonSocket")


def _live_profile_processes(profile_dir: Path) -> List[int]:
    """找出仍在运行、且命令行指向该 profile 的 Chromium / Edge 进程。

    Linux 用 /proc 精确判断；其他平台无法可靠枚举时返回空列表，
    此时清理函数只会删除能删掉的文件（被占用时 unlink 会自然失败）。
    """
    needle = str(profile_dir)
    pids: List[int] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return pids
    try:
        entries = list(proc.iterdir())
    except OSError:
        return pids
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().decode("utf-8", "ignore")
        except OSError:
            continue
        if needle not in cmd:
            continue
        exe = cmd.split("\x00", 1)[0].lower()
        if any(k in exe for k in ("msedge", "chrome", "chromium")):
            pids.append(int(entry.name))
    return pids


def cleanup_stale_singleton(profile_dir: Path) -> None:
    """删除 Chromium 异常退出后残留的 Singleton 锁文件。

    只在**确认没有活进程使用该 profile** 时才删；否则抛 ProfileBusyError，
    绝不盲目 unlink（那会让两个进程同时写同一份 profile，可能损坏登录态）。
    """
    live = _live_profile_processes(profile_dir)
    # 上一个 Edge 进程可能刚 close()、还在收尾，给它一点时间再判定
    for _ in range(4):
        if not live:
            break
        time.sleep(0.5)
        live = _live_profile_processes(profile_dir)
    if live:
        raise ProfileBusyError(
            "浏览器配置目录正被另一个窗口占用（PID " + ", ".join(map(str, live[:3]))
            + "）。请先关闭已打开的登录窗口 / 等待后台同步结束，再重试。")
    for name in _SINGLETON_FILES:
        f = profile_dir / name
        if f.exists() or f.is_symlink():
            try:
                f.unlink()
            except OSError:
                pass  # 被占用时保留，让 Chromium 自己报出准确原因


def _acquire_file_lock(fh, timeout: float) -> None:
    """跨进程文件锁；Windows / 无 fcntl 环境退回进程内互斥。"""
    if sys.platform == "win32":
        return  # _PROFILE_THREAD_LOCK 已保证同进程串行
    try:
        import fcntl
    except ImportError:
        return
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise ProfileBusyError(
                    "浏览器配置目录正被另一个操作占用（通常是登录窗口），请稍后重试")
            time.sleep(0.2)


def _release_file_lock(fh) -> None:
    if sys.platform == "win32":
        return
    try:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except (ImportError, OSError):
        pass


@contextlib.contextmanager
def profile_guard(profile_dir, timeout: float = 30.0):
    """串行化所有对同一持久化 profile 的访问。

    探测（headless GET 两页）、登录窗口（headed）、清除会话三者若同时启动
    ``launch_persistent_context``，Chromium 会报 ``Failed to create a
    ProcessSingleton``。这里用「进程内互斥 + 跨进程文件锁」让它们排队，
    并在每次启动前清掉上次崩溃留下的陈旧 Singleton 锁。
    """
    p = Path(profile_dir)
    p.mkdir(parents=True, exist_ok=True)
    with _PROFILE_THREAD_LOCK:
        fh = open(p / ".app_profile.lock", "a+")
        try:
            _acquire_file_lock(fh, timeout)
            cleanup_stale_singleton(p)
            yield
        finally:
            _release_file_lock(fh)
            fh.close()


# ---------------------------------------------------------------- 数据结构

@dataclass
class PageResult:
    path: str
    ok: bool = False
    status: Optional[int] = None
    html: str = ""
    error: str = ""
    kind: str = "invalid"          # ok / signed_out / transient / invalid
    signin_page: bool = False

    @property
    def usable(self) -> bool:
        return self.ok and not self.signin_page and bool(self.html)


@dataclass
class SessionStatus:
    state: str = UNKNOWN
    reason: str = ""
    pages: Dict[str, PageResult] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    @property
    def can_sync(self) -> bool:
        return self.state in (SIGNED_IN, DEGRADED)


# ---------------------------------------------------------------- 抓取器

class ReadOnlyFetcher:
    """只读抓取器接口。实现只需提供 fetch_many()。"""

    def fetch_many(self, paths: Iterable[str]) -> Dict[str, PageResult]:
        return {p: self.fetch(p) for p in paths}

    def fetch(self, path: str) -> PageResult:  # pragma: no cover - 由子类实现
        raise NotImplementedError


class BrowserSessionFetcher(ReadOnlyFetcher):
    """Playwright 持久化 Edge profile：会话由浏览器保管，应用不接触 Cookie 文本。

    与 browser.py 的自动化通道刻意保持距离：不注入反检测脚本、不携带任何
    ``--no-sandbox`` 类标志、不点击页面元素，只用 ``context.request`` 发 GET。
    """

    def __init__(self, profile_dir: Optional[str] = None, channel: str = "msedge",
                 headless: bool = False, timeout_ms: int = 15000) -> None:
        self.profile_dir = profile_dir or _default_profile_dir()
        self.channel = channel
        self.headless = headless
        self.timeout_ms = timeout_ms

    def fetch_many(self, paths: Iterable[str]) -> Dict[str, PageResult]:
        paths = list(paths)
        for p in paths:
            assert_readonly(p)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            return {p: PageResult(path=p, error=redact(str(exc)), kind="transient")
                    for p in paths}

        out: Dict[str, PageResult] = {}
        try:
            # 登录窗口最多持有锁 5 分钟；自动同步不应在后台线程里干等，
            # 快速返回「配置目录被占用」并保留上次快照即可。
            with profile_guard(self.profile_dir, timeout=3.0):
                with sync_playwright() as pw:
                    try:
                        ctx = pw.chromium.launch_persistent_context(
                            user_data_dir=str(self.profile_dir),
                            channel=self.channel,
                            headless=self.headless,
                        )
                    except Exception as exc:
                        # 内核缺失 / Edge 未安装 / profile 被占用：环境问题，不是登录失效。
                        return {p: PageResult(path=p, error=short_error(exc),
                                              kind="transient") for p in paths}
                    try:
                        for p in paths:
                            try:
                                resp = ctx.request.get(
                                    BASE + p, headers=_NO_STORE_HEADERS,
                                    timeout=self.timeout_ms)
                                status, html = resp.status, resp.text()
                                out[p] = PageResult(
                                    path=p, ok=200 <= status < 300, status=status,
                                    html=html, kind=classify_status(status),
                                    signin_page=is_sign_in_page(html),
                                )
                            except Exception as exc:
                                out[p] = PageResult(
                                    path=p, error=short_error(exc), kind="transient")
                    finally:
                        ctx.close()
        except ProfileBusyError as exc:
            return {p: PageResult(path=p, error=str(exc), kind="transient")
                    for p in paths}
        return out

    def fetch(self, path: str) -> PageResult:
        return self.fetch_many([path])[path]

    def open_for_login(self, wait_seconds: int = 300) -> None:
        """打开可见窗口让用户自己登录；不代填凭据、不读取任何 Cookie。

        窗口按固定时长保持，不根据页面内容自动关闭；调用方结束前需保证
        同一 profile 上没有别的 Playwright 实例（profile_guard 负责串行）。
        """
        from playwright.sync_api import sync_playwright

        with profile_guard(self.profile_dir):
            with sync_playwright() as pw:
                ctx = pw.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_dir),
                    channel=self.channel, headless=False,
                )
                try:
                    page = ctx.pages[0] if ctx.pages else ctx.new_page()
                    page.goto(BASE + "/", wait_until="domcontentloaded")
                    # 登录动作完全由用户完成；这里只保持窗口，不做任何自动判定。
                    page.wait_for_timeout(wait_seconds * 1000)
                finally:
                    ctx.close()

    def clear_session(self) -> None:
        """清除**本应用独立 profile** 内的登录态，不动用户日常 Edge 配置。

        同时删除旧版明文 cookies.txt。历史积分数据库不受影响。
        """
        try:
            from playwright.sync_api import sync_playwright

            with profile_guard(self.profile_dir):
                with sync_playwright() as pw:
                    ctx = pw.chromium.launch_persistent_context(
                        user_data_dir=str(self.profile_dir),
                        channel=self.channel, headless=True,
                    )
                    try:
                        ctx.clear_cookies()
                        page = ctx.pages[0] if ctx.pages else ctx.new_page()
                        try:
                            page.goto(BASE + "/", wait_until="domcontentloaded", timeout=10000)
                            page.evaluate("localStorage.clear(); sessionStorage.clear();")
                        except Exception:
                            pass  # 页面打不开也要把 Cookie 清掉
                    finally:
                        ctx.close()
        except ProfileBusyError:
            raise  # 登录窗口还开着时不能清 profile，交给 UI 提示
        except Exception:
            pass  # 环境不可用时仍继续清理旧版文件
        legacy = _default_data_dir() / "cookies.txt"
        try:
            legacy.unlink(missing_ok=True)
        except OSError:
            pass


class LegacyCookieFetcher(ReadOnlyFetcher):
    """兼容旧版 cookies.txt 的过渡实现（不推荐，仅用于平滑升级）。

    仍只读两页；但 Cookie 明文落盘的风险不变，UI 应引导用户迁移到
    BrowserSessionFetcher 并删除旧文件。
    """

    def __init__(self, cookie_path: Optional[str] = None,
                 timeout=(4, 12)) -> None:
        self.cookie_path = Path(cookie_path) if cookie_path else _default_data_dir() / "cookies.txt"
        self.timeout = timeout

    @staticmethod
    def parse(raw: str) -> Dict[str, str]:
        raw = (raw or "").strip()
        if raw.lower().startswith("cookie:"):
            raw = raw.split(":", 1)[1]
        jar = {}
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                jar[k.strip()] = v.strip()
        return jar

    def fetch_many(self, paths: Iterable[str]) -> Dict[str, PageResult]:
        import requests

        paths = list(paths)
        for p in paths:
            assert_readonly(p)
        override = getattr(self, "_raw_override", None)
        if override:
            jar = self.parse(override)          # 一次性校验，不落盘
        else:
            try:
                jar = self.parse(self.cookie_path.read_text(encoding="utf-8"))
            except OSError:
                jar = {}
        out: Dict[str, PageResult] = {}
        if not jar:
            for p in paths:
                out[p] = PageResult(path=p, error="未找到可用的本地会话", kind="signed_out")
            return out
        with requests.Session() as s:
            s.cookies.update(jar)
            for p in paths:
                try:
                    resp = s.get(BASE + p, headers=_NO_STORE_HEADERS,
                                 timeout=self.timeout, allow_redirects=True)
                    html = resp.text
                    out[p] = PageResult(
                        path=p, ok=200 <= resp.status_code < 300,
                        status=resp.status_code, html=html,
                        kind=classify_status(resp.status_code),
                        signin_page=is_sign_in_page(html),
                    )
                except Exception as exc:
                    out[p] = PageResult(path=p, error=redact(str(exc)), kind="transient")
        return out

    def fetch(self, path: str) -> PageResult:
        return self.fetch_many([path])[path]


# ---------------------------------------------------------------- 状态判定

def probe(fetcher: ReadOnlyFetcher) -> SessionStatus:
    """双页只读探测 → 五态会话状态。网络错误绝不翻译成「Cookie 失效」。"""
    pages = fetcher.fetch_many(ALLOWED_PATHS)
    warnings: List[str] = []
    usable = [r for r in pages.values() if r.usable]
    transient = [r for r in pages.values() if r.kind == "transient"]
    signed_out = [r for r in pages.values()
                  if r.kind == "signed_out" or r.signin_page]

    if not usable:
        if transient and not signed_out:
            busy = [r.error for r in transient
                    if "占用" in r.error or "ProcessSingleton" in r.error]
            reason = (busy[0] if busy else
                      "网络/服务暂时不可用（超时、429 或 5xx）")
            return SessionStatus(
                TRANSIENT_ERROR,
                reason.rstrip("。.!！") + "。这不是登录失效，无需重新登录或复制任何凭证；"
                "保留上次快照稍后重试。", pages, warnings)
        empty_ok = [r for r in pages.values() if r.ok and not r.signin_page and not r.html]
        if empty_ok:
            return SessionStatus(
                TRANSIENT_ERROR,
                "页面返回空内容（可能是结构变化或临时异常），不是登录失效；"
                "保留上次快照稍后重试。", pages, warnings)
        return SessionStatus(
            SIGNED_OUT,
            "识别为登录页 / 公开「关于」页：请先在浏览器中登录 Microsoft Rewards。",
            pages, warnings)

    if signed_out or transient or len(usable) < len(ALLOWED_PATHS):
        warnings.append("仅部分页面可用：只采用能读到的字段，缺失项保留上次快照")
        return SessionStatus(DEGRADED, "会话可用但部分页面未读到，采用降级同步", pages, warnings)
    return SessionStatus(SIGNED_IN, "会话有效，可只读同步", pages, warnings)


def legacy_cookie_notice() -> Optional[str]:
    """检测到旧版明文 Cookie 文件时给出迁移提示（不自动删除，避免破坏用户数据）。"""
    path = _default_data_dir() / "cookies.txt"
    if not path.exists():
        return None
    return (
        "检测到旧版明文 cookies.txt。建议改用「浏览器会话登录」，然后删除该文件；"
        "如曾分享过 Cookie，请在 rewards.bing.com 退出会话或重新登录使旧凭证失效。"
    )


MIN_LOGIN_CONFIRM_SEC = 9     # 至少观察这么久，避免页面刚加载就误判
LOGIN_CONFIRM_POLLS = 2       # 连续两次都像已登录才提示「可完成」

# Microsoft 账户认证域：停留在这里说明还在输密码 / 短信验证，不能算已登录
_AUTH_HOSTS = (
    "login.live.com", "account.live.com", "login.microsoftonline.com",
    "account.microsoft.com", "login.microsoft.com", "login.live-int.com",
)


def login_poll_state(html: str, elapsed_sec: int, confirmed_polls: int,
                     url: str = ""):
    """登录窗口轮询的**纯逻辑**：返回 (detected, confirmed_polls, 提示文案)。

    刻意不返回「是否关闭窗口」：旧版一发现页面不是登录页就 ``return`` +
    ``ctx.close()``，用户刚看到窗口就被关掉（MFA 中间页、已登录首页都会
    误判）。现在窗口只由用户点「我已完成登录」或超时结束。

    ``url`` 可选：认证域（login.live.com 等）或跳转中的空白页一律当「仍在登录」，
    避免短信验证中间页被当成已登录而给出错误提示。
    """
    low = str(url or "").lower()
    on_auth_host = any(host in low for host in _AUTH_HOSTS)
    off_rewards = bool(low) and "rewards.bing.com" not in low and not on_auth_host
    if on_auth_host or off_rewards or is_sign_in_page(html):
        return False, 0, "请在浏览器窗口中完成登录（含短信 / MFA 验证）…"
    confirmed = confirmed_polls + 1
    if elapsed_sec >= MIN_LOGIN_CONFIRM_SEC and confirmed >= LOGIN_CONFIRM_POLLS:
        return True, confirmed, "✅ 已检测到登录会话，请点「我已完成登录」关闭窗口并检测。"
    return False, confirmed, "正在确认登录状态…"




# ---------------------------------------------------------------- 路径兜底
# 合入仓库时优先使用 config.profile_dir()/data_dir()；独立运行/单测时自带等价实现。

def _default_data_dir() -> Path:
    try:
        from .config import data_dir  # type: ignore
        return Path(data_dir())
    except Exception:
        pass
    override = os.environ.get("MS_REWARDS_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MSRewardsAssistant"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "MSRewardsAssistant"
    else:
        base = Path.home() / ".local" / "share" / "msrewardsassistant"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _default_profile_dir() -> Path:
    try:
        from .config import profile_dir  # type: ignore
        return Path(profile_dir())
    except Exception:
        pass
    p = _default_data_dir() / "browser_profile"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------- 默认装配

class UnavailableFetcher(ReadOnlyFetcher):
    """运行环境未就绪（缺 Playwright / 未装 Edge / 未登录）时的兜底。"""

    def __init__(self, reason: str = "浏览器会话环境未就绪") -> None:
        self.reason = reason

    def fetch_many(self, paths: Iterable[str]) -> Dict[str, PageResult]:
        return {p: PageResult(path=p, error=self.reason, kind="transient")
                for p in paths}


def playwright_available() -> bool:
    from importlib.util import find_spec
    try:
        return find_spec("playwright.sync_api") is not None
    except Exception:
        return False


def default_fetcher() -> ReadOnlyFetcher:
    """默认抓取器：浏览器会话优先，旧版明文 Cookie 仅作无 Playwright 时的过渡。

    应用主路径不再调用它写 Cookie；仅当检测到旧文件且新通道不可用时才回退，
    保证老用户升级后仍能只读同步，同时在 UI 提示迁移。
    """
    if playwright_available():
        # 只读同步在后台无头运行；可见登录窗口由 LoginDialog 单独启动
        return BrowserSessionFetcher(headless=True)
    legacy = _default_data_dir() / "cookies.txt"
    if legacy.exists():
        return LegacyCookieFetcher()
    return UnavailableFetcher(
        "未检测到可用的浏览器会话环境：请安装 Microsoft Edge 与 Playwright，"
        "并在「账户与设置 → 登录」里由本人完成一次登录。")




def clear_local_session() -> None:
    """供「退出登录」调用：清除独立 profile 登录态与旧明文 Cookie 文件。"""
    BrowserSessionFetcher().clear_session()

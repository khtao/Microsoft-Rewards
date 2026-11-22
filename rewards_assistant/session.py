# -*- coding: utf-8 -*-
"""登录态管理：用本机加密凭证，纯 HTTP 只读探测两个白名单页面。

``/dashboard`` 与 ``/earn`` 是服务端渲染的 HTML，需要的只是带 Cookie 的两个
GET。凭证由 ``credentials.CookieStore`` 加密保存（Windows DPAPI / 其余平台
机器绑定密钥），**同步链路完全不启动浏览器**；浏览器只在用户本人于登录窗口
完成一次登录（含 MFA）、以及「退出登录」清理 profile 时出现。应用不上传、
不打印、不把 Cookie 写成明文文件，也不读取日常 Edge 的 Cookie 数据库。

``HttpFetcher`` 并发 GET 两页，不 POST、不点击、不搜索、不领取、不注入脚本。
两页内容与 HTTP 状态用于五态判定：已登录 / 未登录 / 部分可用 / 暂时性网络
错误 / 未知；网络错误绝不会被当成「凭证失效」。

奖励搜索须由用户本人手动进行，可领取积分须在 rewards.bing.com 手动领取。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import re
import sys
import threading
import time
from functools import lru_cache
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from .credentials import CookieStore
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
    支离破碎，也可能把中文提示插进 command line 中间。
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

@dataclass(slots=True)
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


@dataclass(slots=True)
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


def _requests_session_factory():
    """返回可用的 ``requests.Session`` 工厂；被污染时返回 None。

    某些系统 Python 环境里（pip 装过 lazy-import 类注入库、或与 PyInstaller
    的冻结导入器混用时），``requests.Session`` 会被替换成一个类型名为
    ``lazy_import`` 的代理对象，调用它就抛::

        'lazy_import' object is not callable

    这里做一次**真实构造预检**（不发网络请求），失败就整体切到标准库
    ``urllib`` 后端，绝不把导入层异常抛给同步层。
    """
    try:
        import requests
        factory = getattr(requests, "Session", None)
        if not callable(factory):
            return None
        return _probe_requests_class(factory)
    except Exception:
        return None


@lru_cache(maxsize=4)
def _probe_requests_class(factory):
    """按 ``requests.Session`` 类对象缓存构造预检（一次约 0.6 ms）。

    缓存键是类对象本身：单测把 ``requests.Session`` monkeypatch 成代理 /
    伪类时会得到不同的键，预检照常重新执行，不会被上一次结果污染；
    ``maxsize=4`` 有界，不会随替换次数增长。预检成功返回原类，失败返回
    None（调用方据此切到标准库后端）。
    """
    try:
        probe = factory()                       # 真实构造，拦住代理对象
        if not callable(getattr(probe, "get", None)):
            return None
        close = getattr(probe, "close", None)
        if callable(close):
            close()
        return factory
    except Exception:
        return None


def _stdlib_opener(cookies):
    """标准库后端：CookieJar 按域管理凭证，重定向时不会把 Cookie 发到别站。

    只保留 bing.com / rewards.bing.com 域的 Cookie；``HTTPCookieProcessor``
    会像浏览器一样按 domain / path / secure 决定是否携带，比手拼 Cookie 头
    更安全（未登录重定向到 login.live.com 时不会泄漏会话）。
    """
    import http.cookiejar
    import urllib.request

    jar = http.cookiejar.CookieJar()
    for c in cookies:
        try:
            raw_value = c.get("value")
            if raw_value is None or str(raw_value) == "":
                continue
            name, value = str(c["name"]), str(raw_value)
            if not name:
                continue
            raw_domain = str(c.get("domain") or "rewards.bing.com")
            initial_dot = raw_domain.startswith(".")
            domain = raw_domain.lstrip(".")
            if not (domain == "rewards.bing.com" or domain == "bing.com"
                    or domain.endswith(".bing.com")):
                continue
            jar.set_cookie(http.cookiejar.Cookie(
                version=0, name=name, value=value,
                port=None, port_specified=False,
                domain=domain, domain_specified=True,
                domain_initial_dot=initial_dot,
                path=str(c.get("path") or "/"), path_specified=True,
                secure=bool(c.get("secure")), expires=None, discard=True,
                comment=None, comment_url=None, rest={}, rfc2109=False))
        except Exception:
            continue
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar))


_STDLIB_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0.0.0 Safari/537.36")


# 两页并发共用一个模块级固定 2 线程池：托盘应用按小时反复同步，复用
# 常驻线程可避免反复创建/销毁线程与栈内存抖动。线程池只服务白名单里的
# 两个 GET，任务短且有超时，不会无限堆积。仅当调用方显式把 max_workers
# 调到 1、或一次请求超过 2 个路径时，才退回临时线程池以保留串行语义。
_SHARED_EXECUTOR: Optional[concurrent.futures.ThreadPoolExecutor] = None
_EXECUTOR_LOCK = threading.Lock()


def _shared_executor():
    global _SHARED_EXECUTOR
    ex = _SHARED_EXECUTOR
    if ex is None:
        with _EXECUTOR_LOCK:
            ex = _SHARED_EXECUTOR
            if ex is None:
                ex = concurrent.futures.ThreadPoolExecutor(
                    max_workers=2, thread_name_prefix="rewards-http")
                _SHARED_EXECUTOR = ex
    return ex


class HttpFetcher(ReadOnlyFetcher):
    """纯 HTTP 只读抓取器：不启动浏览器，只用加密凭证并发 GET 两页。

    ``rewards.bing.com/dashboard`` 与 ``/earn`` 是服务端渲染的 HTML，不需要 JS
    执行；要的只是带 Cookie 的两个 GET。凭证由 :class:`CookieStore` 提供，
    每页用独立的会话（Session / opener 本身不保证线程安全），两页在线程池里
    并发；单页异常只影响自己，返回 transient 而不会误判登录失效。

    **双后端**：优先 ``requests``（做构造预检），不可用时自动切到标准库
    ``urllib.request``。系统环境里即使装了会污染 ``requests`` 的 lazy-import
    代理，同步也能正常运行；运行时若仍遇到 ``not callable``，该页会再用
    标准库重试一次。
    """

    def __init__(self, store: Optional[CookieStore] = None,
                 timeout=(4, 12), max_workers: int = 2) -> None:
        self.store = store or CookieStore()
        self.timeout = timeout
        self.max_workers = max(1, int(max_workers))

    def _timeout_seconds(self) -> float:
        t = self.timeout
        if isinstance(t, (tuple, list)) and len(t) >= 2:
            return float(t[1])
        return float(t)

    def _one_requests(self, factory, path: str, cookies) -> PageResult:
        with factory() as s:
            for c in cookies:
                try:
                    s.cookies.set(c["name"], c["value"],
                                  domain=c.get("domain") or "",
                                  path=c.get("path") or "/",
                                  secure=bool(c.get("secure")))
                except (KeyError, TypeError, ValueError):
                    continue
            resp = s.get(BASE + path, headers=_NO_STORE_HEADERS,
                         timeout=self.timeout, allow_redirects=True)
            return PageResult(
                path=path, ok=200 <= resp.status_code < 300,
                status=resp.status_code, html=resp.text,
                kind=classify_status(resp.status_code),
                signin_page=is_sign_in_page(resp.text),
            )

    def _one_stdlib(self, path: str, cookies) -> PageResult:
        """零第三方依赖的等价两页 GET，异常一律转 transient。"""
        import urllib.error
        import urllib.request

        headers = dict(_NO_STORE_HEADERS)
        headers["User-Agent"] = _STDLIB_UA
        req = urllib.request.Request(BASE + path, headers=headers, method="GET")
        status, html = None, ""
        try:
            opener = _stdlib_opener(cookies)
            with opener.open(req, timeout=self._timeout_seconds()) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                raw = resp.read()
                charset = resp.headers.get_content_charset() or "utf-8"
                html = raw.decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            # 401/403/429/5xx 也是响应，读 body 才能正确判登录页
            try:
                status = exc.code
                raw = exc.read()
                charset = exc.headers.get_content_charset() or "utf-8"
                html = raw.decode(charset, errors="replace")
            except Exception:
                html = ""
        except Exception as exc:
            return PageResult(path=path, error=redact(str(exc)), kind="transient")
        return PageResult(
            path=path, ok=200 <= (status or 0) < 300, status=status, html=html,
            kind=classify_status(status), signin_page=is_sign_in_page(html))

    def fetch_many(self, paths: Iterable[str]) -> Dict[str, PageResult]:
        paths = list(paths)
        for p in paths:
            assert_readonly(p)
        cookies = self.store.load()
        if not cookies:
            # 不发起无意义的网络往返；probe() 会据此判 SIGNED_OUT。
            return {p: PageResult(
                path=p, kind="signed_out", signin_page=True,
                error="尚未保存登录会话：请在「登录」里由本人完成一次 Microsoft "
                      "Rewards 登录（含 MFA），会话会加密保存在本机。")
                for p in paths}

        factory = _requests_session_factory()

        def one(path: str) -> PageResult:
            if factory is not None:
                try:
                    return self._one_requests(factory, path, cookies)
                except Exception as exc:
                    # 预检漏过的运行时污染（如 s.get 内部才是 lazy 代理）
                    # 不走 transient，直接让标准库兜底重试一次。
                    if "not callable" not in str(exc):
                        return PageResult(path=path, error=redact(str(exc)),
                                          kind="transient")
            return self._one_stdlib(path, cookies)

        if len(paths) <= 2 and self.max_workers >= 2:
            return {r.path: r for r in _shared_executor().map(one, paths)}
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(self.max_workers, len(paths))) as ex:
            return {r.path: r for r in ex.map(one, paths)}

    def fetch(self, path: str) -> PageResult:
        return self.fetch_many([path])[path]


class BrowserSessionFetcher(ReadOnlyFetcher):
    """仅用于登录窗口 / 退出登录清理的 Playwright 持久化 profile 接口。

    **默认同步路径不经过这里**（见 ``HttpFetcher``）。保留 ``fetch_many`` 作为
    兼容接口，供诊断与回归测试使用；``clear_session`` 用于清空 profile 登录态。
    不注入反检测脚本、不携带 ``--no-sandbox`` 类标志、不点击页面元素。
    """

    def __init__(self, profile_dir: Optional[str] = None, channel: str = "msedge",
                 headless: bool = False, timeout_ms: int = 15000) -> None:
        self.profile_dir = profile_dir or _default_profile_dir()
        self.channel = channel
        self.headless = headless
        self.timeout_ms = timeout_ms

    def fetch_many(self, paths: Iterable[str]) -> Dict[str, PageResult]:
        """兼容接口：仅在测试 / 特殊诊断里使用，默认同步路径不再经过浏览器。"""
        return self._fetch_once(list(paths))

    def _fetch_once(self, paths: Sequence[str]) -> Dict[str, PageResult]:
        """一次性启动的兼容路径：常驻会话不可用 / ``reuse=False`` 时使用。"""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            async_playwright = None
        loop_running = False
        try:
            # 在某个已有 asyncio 事件循环的线程里不能直接用 asyncio.run；
            # 改在一次性辅助线程里跑，既不阻塞当前循环，也不退回同步 Playwright。
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                loop_running = True
        except Exception:
            loop_running = False

        out: Dict[str, PageResult] = {}
        try:
            # 登录窗口最多持有锁 5 分钟；自动同步不应在后台线程里干等，
            # 快速返回「配置目录被占用」并保留上次快照即可。
            with profile_guard(self.profile_dir, timeout=3.0):
                if async_playwright is None:
                    out = self._fetch_serial(paths)
                elif loop_running:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                        out = ex.submit(
                            lambda: asyncio.run(
                                self._fetch_async(paths, async_playwright))).result()
                else:
                    out = asyncio.run(self._fetch_async(paths, async_playwright))
        except ProfileBusyError as exc:
            return {p: PageResult(path=p, error=str(exc), kind="transient")
                    for p in paths}
        return out

    def _fetch_serial(self, paths: Sequence[str]) -> Dict[str, PageResult]:
        """无异步 API / 已有事件循环时的兼容路径：保持逐页 GET 行为。"""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            return {p: PageResult(path=p, error=redact(str(exc)), kind="transient")
                    for p in paths}
        out: Dict[str, PageResult] = {}
        try:
            with sync_playwright() as pw:
                try:
                    ctx = pw.chromium.launch_persistent_context(
                        user_data_dir=str(self.profile_dir),
                        channel=self.channel,
                        headless=self.headless,
                    )
                except Exception as exc:
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
        except Exception as exc:
            return {p: PageResult(path=p, error=short_error(exc), kind="transient")
                    for p in paths}
        return out

    @staticmethod
    def _page_result(path: str, status: int, html: str) -> PageResult:
        return PageResult(
            path=path, ok=200 <= status < 300, status=status, html=html,
            kind=classify_status(status), signin_page=is_sign_in_page(html))

    async def _fetch_async(self, paths: Sequence[str], async_playwright) -> Dict[str, PageResult]:
        """在一次性浏览器 context 内并发请求；单页失败不影响另一页。

        ``ctx.close()`` 必须发生在 ``async_playwright`` 退出（驱动停止）之前，
        否则会留下活着的 Edge 进程，下一次同步会被 profile 锁判为「占用」。
        """
        try:
            async with async_playwright() as pw:
                ctx = None
                try:
                    try:
                        ctx = await pw.chromium.launch_persistent_context(
                            user_data_dir=str(self.profile_dir),
                            channel=self.channel,
                            headless=self.headless,
                        )
                    except Exception as exc:
                        return {p: PageResult(path=p, error=short_error(exc),
                                              kind="transient") for p in paths}

                    async def one(path: str) -> PageResult:
                        try:
                            resp = await ctx.request.get(
                                BASE + path, headers=_NO_STORE_HEADERS,
                                timeout=self.timeout_ms)
                            html = await resp.text()
                            return self._page_result(path, resp.status, html)
                        except Exception as exc:
                            return PageResult(path=path, error=short_error(exc),
                                              kind="transient")

                    # 两页同时下载：整轮耗时 ≈ 较慢的一页，而不是两页之和。
                    results = await asyncio.gather(*(one(p) for p in paths))
                    return {r.path: r for r in results}
                finally:
                    if ctx is not None:
                        try:
                            await ctx.close()
                        except Exception:
                            pass
        except Exception as exc:
            return {p: PageResult(path=p, error=short_error(exc), kind="transient")
                    for p in paths}

    def fetch(self, path: str) -> PageResult:
        return self.fetch_many([path])[path]

    def clear_session(self) -> None:
        """清除**本应用独立 profile** 内的登录态，不动用户日常 Edge 配置。

        同时删除遗留的明文 cookies.txt。历史积分数据库不受影响。
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
            pass  # 环境不可用时仍继续清理遗留文件
        legacy = _default_data_dir() / "cookies.txt"
        try:
            legacy.unlink(missing_ok=True)
        except OSError:
            pass


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
        hint = next((r.error for r in pages.values()
                     if "尚未保存登录会话" in r.error), "")
        return SessionStatus(
            SIGNED_OUT,
            hint or "识别为登录页 / 公开「关于」页：请先在浏览器中登录 Microsoft Rewards。",
            pages, warnings)

    if signed_out or transient or len(usable) < len(ALLOWED_PATHS):
        warnings.append("仅部分页面可用：只采用能读到的字段，缺失项保留上次快照")
        return SessionStatus(DEGRADED, "会话可用但部分页面未读到，采用降级同步", pages, warnings)
    return SessionStatus(SIGNED_IN, "会话有效，可只读同步", pages, warnings)


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

    刻意不返回「是否关闭窗口」：窗口只由用户点「我已完成登录」或超时
    结束，避免 MFA 中间页、已登录首页被误判后立刻关闭。

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




# ---------------------------------------------------------------- 路径装配
# 与 credentials.data_dir() 共用 config 这一唯一真源，不再复制平台分支。

def _default_data_dir() -> Path:
    from .config import data_dir
    return Path(data_dir())


def _default_profile_dir() -> Path:
    from .config import profile_dir
    return Path(profile_dir())


# ---------------------------------------------------------------- 默认装配

def default_fetcher() -> ReadOnlyFetcher:
    """同步链路默认装配：纯 HTTP + 本机加密凭证，**不启动浏览器**。

    登录窗口是唯一还会打开 Edge 的地方（本人登录 / MFA / 导出凭证）。因此
    同步、登录态复查、HTML 导出都不受浏览器冷启动、profile 锁、Edge 版本
    和安装路径影响。遗留明文 ``cookies.txt`` 在这里一次性迁移为加密文件后删除。
    """
    store = CookieStore()
    try:
        store.migrate_legacy()
    except Exception:
        pass                     # 迁移失败不应阻断「尚未登录」的正常判定
    return HttpFetcher(store)


def clear_local_session() -> None:
    """供「退出登录」调用：删除加密凭证、明文遗留文件与 profile 登录态。"""
    CookieStore().clear()
    BrowserSessionFetcher().clear_session()

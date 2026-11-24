# -*- coding: utf-8 -*-
"""「可领取积分」卡片：只读校验 + 打开**当前账号**的官网领取页。

设计边界（刻意保守）：

* 只有「最近一次同步成功 + 登录态明确为已登录 + 本实例凭证存在 +
  快照里有可领取积分 > 0 + 不在同步 / 自动运行 / 已有领取窗口」时才可点。
  其它一切情况（未登录、无可领取、凭证失效、运行中、读取不完整……）卡片
  **完全无动作**：不弹提示、不打开浏览器、不改界面状态。
* 打开前先用当前实例的纯 HTTP 只读会话复查一次 `/dashboard`：会话确属本
  账号、且官网当前若明确给出「可领取 0 分」就直接放弃。全程只 GET，不 POST、
  不点击、不代领。
* 领取窗口用**本实例自己的加密凭证**注入一份全新空 Edge profile，绝不用
  系统默认浏览器。多开时日常 Edge 或其它实例登录的是另一个账号，用默认
  浏览器会把用户带到错账号；这里连 user-data-dir 都挂在 ``data_dir()`` 下，
  按实例隔离，目录族与自动化任务分开，互不 prune。
* 领取动作必须由本人在官网右上角「准备领取」面板完成；应用只负责把窗口
  带到那个页面，并在窗口关闭后清理临时 profile、触发一次积分同步。

关闭检测的兜底
--------------
persistent context 被用户手动关掉时，不同 Playwright / Edge 版本对
``page.is_closed()`` / ``wait_for_event("close")`` 的表现并不一致：进程整体
退出后页面对象可能既不变 closed 也不抛异常，轮询就一直挂着，关窗后的自动
同步永远不触发。因此这里同时做三件事：

1. 注册 ``page/context close`` 与 ``page crash`` 事件回调；
2. 用 ``page.wait_for_timeout()`` 泵 Playwright 事件循环（同步 API 只在调用
   期间派发事件），并轮询 ``page.is_closed()``；
3. 从**成功打开页面**起计时，若 ``CLAIM_SYNC_FALLBACK_SECONDS``（30 秒）内
   仍没捕获到关闭事件，就调用 ``on_close_undetected`` 让控制器兜底刷新一次
   积分状态。兜底只补同步、不强行关掉用户可能还在用的窗口。
"""
from __future__ import annotations

import random
import time
from typing import Any, Callable, Optional

from . import debug_log
from .browser import (cleanup_automation_profile, launch_automation_edge,
                      browser_error_hint)
from .credentials import CookieStore
from .readonly_report import parse_points_trio
from .session import HttpFetcher
from .timing import jitter, sleep_jitter

# 官方说明：可领取积分只能在 rewards.bing.com 领取，右上角「准备领取」面板。
CLAIM_URL = "https://rewards.bing.com/dashboard"
CLAIM_PROFILE_DIRNAME = "claim_edge_profile"
CLAIM_NAV_TIMEOUT_MS = 30000
# 关闭事件检测失败时，从「成功打开领取页」算起多久兜底刷新积分。
CLAIM_SYNC_FALLBACK_SECONDS = 30.0

LogFn = Optional[Callable[[str, str], None]]


class ClaimUnavailable(Exception):
    """当前不满足领取条件：调用方静默结束，不弹任何界面提示。"""


def _note(log: LogFn, action: str, detail: str = "") -> None:
    if log is None:
        return
    try:
        log(action, str(detail)[:300])
    except Exception:
        pass


def claim_ready(claimable, login_ok, has_cookies: bool, *,
                syncing: bool = False, running: bool = False,
                opening: bool = False, last_sync_ok: bool = True) -> bool:
    """纯函数：卡片此刻是否真的可点。任何一项不确定都返回 False。"""
    try:
        has_points = int(claimable) > 0
    except (TypeError, ValueError):
        has_points = False
    return bool(
        last_sync_ok and has_points and (login_ok is True) and has_cookies
        and not syncing and not running and not opening
    )


def _playwright():
    """延迟导入，便于离线测试替换。"""
    from playwright.sync_api import sync_playwright
    return sync_playwright()


def instance_tag() -> str:
    try:
        from .config import instance_id, instance_label
        return f"{instance_label()}（{instance_id()}）"
    except Exception:
        return "当前环境"


def verify_claim_session(store, log: LogFn = None):
    """用**本实例**凭证只读复查一次官网会话；不满足条件抛 ClaimUnavailable。

    返回页面给出的可领取积分（``None`` 表示这次没读到该字段，沿用本地快照）。
    """
    cookies = store.load()
    if not cookies:
        raise ClaimUnavailable("本实例没有可用的登录会话")
    res = HttpFetcher(store=store).fetch_many(["/dashboard"])["/dashboard"]
    if not res.usable:
        raise ClaimUnavailable("官网会话不可用或需重新登录")
    live = parse_points_trio(res.html).get("claimable")
    if live is not None and live <= 0:
        raise ClaimUnavailable("官网当前没有可领取积分")
    _note(log, "verify", f"{instance_tag()}：会话有效，"
                         f"可领取 {live if live is not None else '沿用本地快照'}")
    return live


def open_claim_window(store=None, url: str = CLAIM_URL, log: LogFn = None,
                      wait_for_close: bool = True,
                      stop_event: Any = None,
                      on_opened: Optional[Callable[[], None]] = None,
                      on_close_undetected: Optional[Callable[[], None]] = None,
                      fallback_seconds: float = CLAIM_SYNC_FALLBACK_SECONDS,
                      clock: Optional[Callable[[], float]] = None) -> bool:
    """打开当前账号的领取页并保持窗口，直到用户关闭或应用退出。

    这是给后台线程调用的同步函数。``ClaimUnavailable`` / 启动异常都由调用方
    捕获后静默记日志；**不弹 toast、不改任务状态、不写数据库**。
    ``wait_for_close=False`` 供离线测试使用；``stop_event`` 由控制器在退出时
    set，让轮询在 0.3 秒内收尾并关闭 Edge，避免留下孤儿窗口与临时 profile。

    ``on_opened`` 在页面 ``goto`` 成功后调用一次；若 30 秒内没检测到关闭
    事件，``on_close_undetected`` 被调用一次，控制器据此补一次积分同步。
    ``clock`` 只用于离线测试注入单调时钟。
    """
    store = store or CookieStore()
    verify_claim_session(store, log)

    monotonic = clock or time.monotonic
    rng = random.Random()

    def fire(cb: Optional[Callable[[], None]]) -> None:
        if cb is None:
            return
        try:
            cb()
        except Exception as exc:
            _note(log, "callback_error", f"{type(exc).__name__}: {exc}")
            debug_log.error("claim", "领取窗口回调失败", exc=exc)

    pw_cm = _playwright()
    with pw_cm as pw:
        ctx, snap = launch_automation_edge(
            pw, store=store, profile_prefix=CLAIM_PROFILE_DIRNAME,
            headless=False, log=log)
        try:
            pages = list(getattr(ctx, "pages", []) or [])
            page = pages[0] if pages else ctx.new_page()

            # 事件回调 + 轮询双保险：persistent context 整体退出时，单靠
            # is_closed() 可能既不返回 True 也不抛异常。
            closed = [False]

            def _mark_closed(*_a, **_k):
                closed[0] = True

            for obj, event in ((page, "close"), (ctx, "close"),
                               (page, "crash")):
                try:
                    obj.on(event, _mark_closed)
                except Exception:
                    pass                  # 离线桩 / 旧版本没有 on()

            page.goto(url, wait_until="domcontentloaded",
                      timeout=CLAIM_NAV_TIMEOUT_MS)
            opened_at = monotonic()
            _note(log, "claim_open",
                  f"{instance_tag()}：已打开领取页 {url}（请本人在官网领取）")
            debug_log.debug("claim", "领取页已打开",
                            instance=instance_tag(), url=url,
                            fallback_seconds=fallback_seconds)
            fire(on_opened)               # 控制器记录「已成功打开」

            if wait_for_close:
                fallback_fired = False
                # 窗口由用户手动关闭；期间线程常驻，不占用 UI 事件循环。
                while True:
                    if stop_event is not None and stop_event.is_set():
                        _note(log, "claim_close", "应用退出，关闭领取窗口")
                        break
                    if closed[0]:
                        _note(log, "claim_close", "已捕获领取窗口关闭事件")
                        break
                    try:
                        if page.is_closed():
                            closed[0] = True
                            _note(log, "claim_close", "领取页面已关闭")
                            break
                    except Exception as exc:
                        closed[0] = True
                        _note(log, "claim_close",
                              f"页面状态查询异常，按已关闭处理：{type(exc).__name__}")
                        debug_log.warn("claim", "领取页状态查询异常", exc=exc)
                        break

                    # Playwright 同步 API 只在调用期间派发事件；wait_for_timeout
                    # 既让出时间片又泵事件循环，close/crash 回调才会被触发。
                    wait_s = jitter(0.2, 0.45, rng)
                    try:
                        pump = getattr(page, "wait_for_timeout", None)
                        if callable(pump):
                            pump(int(wait_s * 1000))
                        else:
                            sleep_jitter(0.2, 0.45)
                    except Exception as exc:
                        closed[0] = True
                        _note(log, "claim_close",
                              f"事件泵异常，按已关闭处理：{type(exc).__name__}")
                        debug_log.warn("claim", "领取页事件泵异常", exc=exc)
                        break

                    # 兜底：关闭事件始终没来，也要在打开 30 秒后刷新积分。
                    if (not fallback_fired and fallback_seconds > 0
                            and monotonic() - opened_at >= fallback_seconds):
                        fallback_fired = True
                        _note(log, "claim_sync_fallback",
                              f"未捕获关闭事件，打开 {fallback_seconds:g} 秒后"
                              "兜底刷新积分状态")
                        debug_log.warn("claim", "关闭事件检测失败，触发兜底同步",
                                       instance=instance_tag(),
                                       fallback_seconds=fallback_seconds)
                        fire(on_close_undetected)
        except Exception as exc:
            _note(log, "claim_error", f"{type(exc).__name__}: {exc}")
            debug_log.error("claim", "领取窗口运行异常", exc=exc,
                            instance=instance_tag())
            raise
        finally:
            try:
                ctx.close()
            except Exception:
                pass
            cleanup_automation_profile(snap, prefix=CLAIM_PROFILE_DIRNAME)
    return True


def short_claim_error(exc: BaseException) -> str:
    """失败日志只写一句可操作提示，不泄漏本机路径 / Cookie。"""
    if isinstance(exc, ClaimUnavailable):
        return str(exc)
    return browser_error_hint(str(exc))

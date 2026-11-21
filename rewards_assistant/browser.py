# -*- coding: utf-8 -*-
"""启动真实 Microsoft Edge（headed）跑实验性自动化链路。

登录态与只读同步共用同一份**本机加密凭证**（``credentials.CookieStore``）：
自动化在一份**全新且空的**配置目录里启动 Edge，随后用 ``ctx.add_cookies()``
注入凭证。整个链路不复制、不读取任何已有浏览器 profile，因此：

* 没有 ``SingletonLock`` / ``ProcessSingleton`` / ``shutil.Error``；
* 不会把启动 handoff 给残留的 Edge 实例后自行退出；
* 不需要关闭日常 Edge，也不与登录窗口 / 后台同步抢同一目录；
* 同步与自动化用同一个账户会话，不会出现「已登录但自动化未登录」的分裂。

经典链路之外，本模块仍提供真实 Edge 的启动参数与沙箱/警告横幅处理。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

from .config import data_dir, profile_dir as _app_profile_dir
from .fingerprint import STEALTH_MIN_JS, STEALTH_PLUGINS_JS

# Edge on Linux 的管理策略目录（Chromium 是 /etc/chromium/policies/managed）
EDGE_POLICY_DIRS = (Path("/etc/opt/edge/policies/managed"),
                    Path("/etc/opt/microsoft/edge/policies/managed"))
# 这条策略专门用来关掉「不受支持的命令行标志」的安全警告横幅
FLAG_WARNING_POLICY = {"CommandLineFlagSecurityWarningsEnabled": False}


AUTOMATION_PROFILE_DIRNAME = "automation_edge_profile"


def app_profile_dir() -> Path:
    """应用独立的登录 profile（仅登录窗口 / 退出登录清理使用）。

    同步与自动化都改用 ``credentials.CookieStore`` 里的加密凭证，不再读取它。
    """
    return _app_profile_dir()


def prune_automation_profiles(keep: int = 3,
                              prefix: str = AUTOMATION_PROFILE_DIRNAME) -> None:
    """清理历史快照目录；正在被进程占用的删不掉就留着，不报错。

    ``prefix`` 让「自动完成任务」与「领取页」各有自己的目录族：领取窗口会
    一直开着等用户手动领取，不能被自动化新一轮的 prune 误删。
    """
    base = data_dir()
    try:
        cands = sorted(
            (d for d in base.iterdir()
             if d.is_dir() and d.name.startswith(prefix + "_")),
            key=lambda d: d.stat().st_mtime, reverse=True)
    except OSError:
        return
    for d in cands[keep:]:
        shutil.rmtree(d, ignore_errors=True)


def new_automation_profile_dir(prefix: str = AUTOMATION_PROFILE_DIRNAME) -> Path:
    """每次都用**全新且空**的配置目录，不复制任何文件。

    目录里没有从旧 profile 带过来的 ``SingletonLock``，也不会被上一次残留的
    Edge 进程 handoff；Chromium 的 process singleton 按 user-data-dir 路径
    判定，唯一路径即可彻底断开这条链。登录态改由 ``ctx.add_cookies()`` 注入。
    路径挂在 ``data_dir()`` 下，因此多开时每个实例互不共享。
    """
    prune_automation_profiles(keep=3, prefix=prefix)
    p = data_dir() / (f"{prefix}_{os.getpid()}_"
                      f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:4]}")
    p.mkdir(parents=True, exist_ok=True)
    return p


def cleanup_automation_profile(path, prefix: str = AUTOMATION_PROFILE_DIRNAME) -> None:
    """运行结束后删除本次快照；删不掉（进程未完全退出）就留给下次 prune。"""
    if path:
        shutil.rmtree(Path(path), ignore_errors=True)
    prune_automation_profiles(keep=3, prefix=prefix)


def is_root() -> bool:
    try:
        return os.geteuid() == 0
    except AttributeError:                     # Windows
        return False








def install_flag_warning_policy(policy_dir: Path | None = None, verbose: bool = True) -> bool:
    """写一条管理策略，让 Edge 不再弹「不受支持的命令行标志」警告。

    只对 --no-sandbox / --disable-setuid-sandbox 这类"不安全标志"生效，
    不影响其它行为；需要 root 才能写 /etc。成功返回 True。
    """
    dirs = [Path(policy_dir)] if policy_dir else list(EDGE_POLICY_DIRS)
    last_err = None
    ok = False
    for d in dirs:                      # 全部写一遍：不同发行版/渠道读取的目录不一样
        try:
            d.mkdir(parents=True, exist_ok=True)
            fp = d / "human_search_policy.json"
            fp.write_text(json.dumps(FLAG_WARNING_POLICY, indent=2), encoding="utf-8")
            ok = True
            if verbose:
                print(f"[信息] 已写入策略抑制警告横幅：{fp}")
        except Exception as e:
            last_err = e
    if verbose:
        if ok:
            print("[信息] 策略只在 Edge 启动时读取：请先完全退出所有 Edge 进程（pkill msedge）再运行，"
                  "打开 edge://policy 可确认是否生效")
        else:
            print(f"[警告] 无法写入管理策略（{last_err}）。root 运行或指定 --policy-dir 后可彻底消除横幅；"
                  f"当前改用 --test-type 兜底。")
    return ok


def build_args(window_size: str | None = None, proxy: str | None = None,
               lang: str = "zh-CN", suppress_flag_warning: bool = False) -> list:
    """构造启动参数。这里刻意不含 --no-sandbox —— 沙箱由 chromium_sandbox 控制。"""
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-infobars",
        "--disable-features=Translate,OptimizationHints,MediaRouter",
        f"--lang={lang}",
    ]
    if window_size:
        args.append(f"--window-size={window_size}")
    else:
        args.append("--start-maximized")
    if proxy:
        args.append(f"--proxy-server={proxy}")
    if suppress_flag_warning:
        # 兜底：策略文件写不进去时，test-type 也会跳过 bad-flags 提示条
        args.append("--test-type")
    return args


def resolve_sandbox(want: str | None = None) -> bool:
    """决定用不用 Chromium 沙箱。

    want: "on" 强制开 / "off" 强制关 / None 自动（非 root 就开，root 只能关）。
    返回 True 表示开沙箱（即不加 --no-sandbox）。
    """
    if want == "on":
        if is_root():
            print("[警告] 当前是 root，强制开沙箱可能导致 Edge 启动失败；"
                  "若失败请去掉 --sandbox 或改用普通用户运行")
        return True
    if want == "off":
        return False
    return not is_root()


def find_edge_binary() -> str | None:
    for name in ("microsoft-edge", "microsoft-edge-stable", "microsoft-edge-beta",
                 "microsoft-edge-dev", "msedge", "msedge.exe", "edge"):
        p = shutil.which(name)
        if p:
            return p
    candidates = ["/usr/bin/microsoft-edge", "/usr/bin/microsoft-edge-stable",
                  "/opt/microsoft/msedge/msedge", "/opt/microsoft/msedge-dev/msedge",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    if sys.platform == "win32":
        for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"),
                     os.environ.get("LOCALAPPDATA")):
            if base:
                candidates.append(str(Path(base) / "Microsoft" / "Edge" /
                                      "Application" / "msedge.exe"))
    return next((p for p in candidates if os.path.isfile(p)), None)


def playwright_cookies(store=None) -> list[dict]:
    """把本机加密凭证转成 Playwright ``add_cookies()`` 需要的结构。

    ``expires <= 0`` 是会话 Cookie：Playwright 会把它当成 1970 年已过期，
    必须**省略**该字段；迁移自明文 Cookie 头的 hostless 项用 ``url`` 代替
    ``domain``/``path``（两者互斥）。
    """
    from .credentials import CookieStore

    out: list[dict] = []
    for c in (store or CookieStore()).load():
        name, value = c.get("name"), c.get("value")
        if not name or value is None:
            continue
        cookie = {"name": str(name), "value": str(value),
                  "secure": bool(c.get("secure"))}
        try:
            exp = float(c.get("expires", -1))
        except (TypeError, ValueError):
            exp = -1
        if exp > 0:
            cookie["expires"] = exp
        domain = str(c.get("domain") or "")
        if domain:
            cookie["domain"] = domain if domain.startswith(".") else "." + domain
            cookie["path"] = c.get("path") or "/"
        else:
            cookie["url"] = "https://rewards.bing.com/"
        # 跨站认证 Cookie 需要 SameSite=None；仅在 Secure 时合法
        if cookie["secure"]:
            cookie["sameSite"] = "None"
        out.append(cookie)
    return out


def inject_saved_cookies(ctx, store=None) -> int:
    """把加密凭证注入刚启动的 context；返回成功注入条数。

    旧版明文 Cookie 头迁移来的项可能带畸形域；批量 ``add_cookies`` 会因一条
    不合规格而整批失败，这里退化为逐条注入并跳过坏项。只要核心认证 Cookie
    成功，自动化仍能正常读到已登录页面。
    """
    cookies = playwright_cookies(store)
    if not cookies:
        raise RuntimeError(
            "尚未保存登录会话：请在「登录」里由本人完成一次 Microsoft Rewards "
            "登录（含 MFA），会话会加密保存在本机")
    try:
        ctx.add_cookies(cookies)
        return len(cookies)
    except Exception:
        pass
    injected = 0
    for c in cookies:
        variants = [c]
        # 不同 Playwright / Chromium 版本对 sameSite、expires 的校验有差异；
        # 降级版只保留 name/value/domain(或 url)/path/secure 这些必需字段。
        if "sameSite" in c or "expires" in c:
            variants.append({k: v for k, v in c.items()
                             if k not in ("sameSite", "expires")})
        for variant in variants:
            try:
                ctx.add_cookies([variant])
                injected += 1
                break
            except Exception:
                continue
    if not injected:
        raise RuntimeError("本机会话 Cookie 无法注入浏览器，请在「登录」里重新登录一次")
    return injected


def browser_error_hint(text: str, limit: int = 90) -> str:
    """把 Playwright / Chromium 的原始异常压成一句可操作提示，不泄漏本地路径。"""
    import re

    raw = str(text or "").strip()
    first = raw.splitlines()[0] if raw else ""
    low = first.lower()
    if "尚未保存登录会话" in raw:
        return "尚未保存登录会话：请在「登录」里由本人完成一次 Microsoft Rewards 登录（含 MFA）"
    if "无法注入浏览器" in raw or "add_cookies" in low:
        return "本机会话 Cookie 无法注入浏览器，请在「登录」里重新登录一次后重试"
    if ("executable doesn't exist" in low or "browser executable" in low
            or "msedge" in low and ("not found" in low or "not exist" in low)):
        return "未找到 Microsoft Edge，请先安装 Edge 后重试"
    if "timed out" in low or "timeout" in low:
        return "浏览器启动超时，请稍后重试"
    if ("target page, context or browser has been closed" in low
            or "browser has been closed" in low):
        return "浏览器窗口被关闭或启动中断，请重试（无需关闭日常 Edge）"
    if ("error: [(" in low or "shutil" in low or "errno 13" in low
            or "permission denied" in low or "winerror 32" in low
            or "being used by another process" in low
            or "processsingleton" in low or "singletonlock" in low
            or "failed to create a" in low):
        return "浏览器启动被占用，请重试（日常 Edge 无需关闭）"
    # 兜底：Chromium 异常可能带上 user-data-dir 的绝对路径
    clean = re.sub(r"[A-Za-z]:[\\/][^\s'\"]+", "[本机路径已省略]", first)
    clean = re.sub(r"/(?:home|Users|tmp|var)/[^\s'\"]+", "[本机路径已省略]", clean)
    return clean[:limit] or "浏览器启动失败，请重试"


def launch_edge(pw, *, user_data_dir: str, headless: bool = False,
                window_size: str | None = None, proxy: str | None = None,
                edge_path: str | None = None, slow_mo: int = 0,
                sandbox: str | None = None, suppress_flag_warning: bool | None = None,
                policy_dir: str | None = None, verbose: bool = True,
                flag_warning: str = "auto", geo=None, minimal: bool = False):
    """返回一个 persistent context（页面与 Cookie 全部落在真实 profile 里）。

    sandbox              "on" 开沙箱 / "off" 关沙箱 / None 自动（root 自动关）
    flag_warning         "off" 强制抑制警告条 / "on" 保留 / "auto" 仅在关沙箱时抑制
    geo                  GeoProfile；用它统一时区 / locale，保证与 IP 归属一致
    """
    tz = getattr(geo, "timezone", None) or "Asia/Shanghai"
    loc = getattr(geo, "locale", None) or "zh-CN"
    lang = loc.split("-")[0]
    from playwright.sync_api import Error as PWError  # 延迟导入

    use_sandbox = resolve_sandbox(sandbox)
    if flag_warning == "on":
        suppress_flag_warning = False
    elif flag_warning == "off":
        suppress_flag_warning = True
    elif suppress_flag_warning is None:
        suppress_flag_warning = not use_sandbox

    if suppress_flag_warning:
        install_flag_warning_policy(
            Path(policy_dir) if policy_dir else None, verbose=verbose)

    if verbose:
        if use_sandbox:
            print("[信息] 沙箱：开（不注入 --no-sandbox，不会有警告横幅）")
        else:
            print("[信息] 沙箱：关（--no-sandbox）" +
                  ("，已抑制警告横幅（策略 + --test-type）" if suppress_flag_warning
                   else "，窗口顶部会有警告横幅"))

    if minimal:
        # 参数兼容降级：不同 Playwright / Edge 版本对 no_viewport、
        # permissions、ignore_default_args 等可选参数的接受程度不同。启动
        # 报参数类错误时，用最小参数集再试一次，保住「能打开浏览器」这条主线。
        base = dict(user_data_dir=str(user_data_dir), headless=headless,
                    chromium_sandbox=use_sandbox)
        if edge_path:
            base["executable_path"] = edge_path
        else:
            base["channel"] = "msedge"
        try:
            ctx = pw.chromium.launch_persistent_context(**base)
        except PWError:
            base["chromium_sandbox"] = False
            ctx = pw.chromium.launch_persistent_context(**base)
        ctx.add_init_script(STEALTH_MIN_JS)
        return ctx

    kwargs = dict(
        user_data_dir=str(user_data_dir),
        headless=headless,
        args=build_args(window_size, proxy, lang=lang,
                        suppress_flag_warning=suppress_flag_warning),
        chromium_sandbox=use_sandbox,   # True 时 Playwright 不会加 --no-sandbox
        ignore_default_args=["--enable-automation"],
        permissions=["clipboard-read", "clipboard-write"],
        locale=loc,                # 与 IP 归属一致
        timezone_id=tz,            # 与 IP 归属一致
        slow_mo=slow_mo,
        no_viewport=True,          # 不强制 viewport，窗口就是真实窗口大小
    )
    if edge_path:
        kwargs.pop("channel", None)
        kwargs["executable_path"] = edge_path
    else:
        kwargs["channel"] = "msedge"

    def _try(**kw):
        try:
            return pw.chromium.launch_persistent_context(**kw)
        except PWError as e:
            msg = str(e)
            low = msg.lower()
            if "no_viewport" in msg or "unexpected" in low:
                kw.pop("no_viewport", None)
                return pw.chromium.launch_persistent_context(**kw)
            # Windows 打包环境里 channel="msedge" 有时解析不到安装路径（或
            # 反过来，显式 executable_path 失效）；换另一种解析方式再试一次。
            if ("executable doesn't exist" in low or "browser executable" in low
                    or ("msedge" in low and ("not found" in low or "not exist" in low))):
                alt = dict(kw)
                if alt.get("channel"):
                    alt.pop("channel", None)
                    path = find_edge_binary()
                    if path:
                        alt["executable_path"] = path
                    else:
                        raise
                elif alt.get("executable_path"):
                    alt.pop("executable_path", None)
                    alt["channel"] = "msedge"
                else:
                    raise
                return pw.chromium.launch_persistent_context(**alt)
            raise

    try:
        ctx = _try(**kwargs)
    except PWError as e:
        # root / 容器里开沙箱常会直接起不来，这时退一步关沙箱并抑制警告
        if use_sandbox and ("sandbox" in str(e).lower() or "namespace" in str(e).lower()
                            or "root" in str(e).lower() or "SUID" in str(e)):
            if verbose:
                print(f"[警告] 开沙箱启动失败（{str(e)[:120]}），自动改为关沙箱重试")
            kwargs["chromium_sandbox"] = False
            kwargs["args"] = build_args(window_size, proxy, suppress_flag_warning=True)
            ctx = _try(**kwargs)
        else:
            raise
    ctx.add_init_script(STEALTH_MIN_JS)
    # 只有无头模式才需要补齐插件列表（真实有头 Edge 自带，补了反而可能与真机不符）
    if headless:
        ctx.add_init_script(STEALTH_PLUGINS_JS)
    return ctx


_RETRYABLE_LAUNCH_ERRORS = (
    "has been closed", "processsingleton", "singletonlock",
    "failed to create a", "user data directory is already in use",
    "profile in use", "being used by another process", "permission denied",
)


def is_profile_conflict_error(exc) -> bool:
    """启动期因配置目录占用 / handoff 失败，值得换一个全新目录重试。"""
    low = str(exc).lower()
    return any(k in low for k in _RETRYABLE_LAUNCH_ERRORS)


_LAUNCH_ARG_ERRORS = (
    "unexpected", "no_viewport", "unknown option", "unrecognized",
    "not supported", "invalid argument", "permissions", "ignore_default_args",
)


def is_launch_arg_error(exc) -> bool:
    """Playwright / Edge 版本差异导致可选启动参数被拒绝，值得降级重试。"""
    low = str(exc).lower()
    return any(k in low for k in _LAUNCH_ARG_ERRORS)


def launch_automation_edge(pw, *, user_data_dir=None, source_profile=None,
                           verbose: bool = False, log=None, store=None,
                           profile_prefix: str = AUTOMATION_PROFILE_DIRNAME,
                           **kwargs):
    """在**全新空目录**启动 Edge，再注入指定凭证；返回 ``(ctx, snapshot)``。

    ``user_data_dir`` / ``source_profile`` 是旧版兼容参数，**一律忽略**：新版
    不复制、不读取任何已有 profile，因此不存在被独占的锁文件、``shutil.Error``
    或 handoff 到残留实例。``store`` 显式传入当前实例的 ``CookieStore``，多开时
    不会误注入另一个账号的会话；``profile_prefix`` 让领取窗口与自动化目录族
    分开。``log(action, detail)`` 把每一步写进运行日志，启动失败时不用再靠猜
    traceback 定位。调用方在 ``ctx.close()`` / ``pw.stop()`` 之后必须调用
    ``cleanup_automation_profile(snapshot, prefix=profile_prefix)``。
    """
    from playwright.sync_api import Error as PWError  # 延迟导入

    def note(action: str, detail: str = "") -> None:
        if log is not None:
            try:
                log(action, str(detail)[:300])
            except Exception:
                pass

    cookies = playwright_cookies(store)
    note("credentials", f"本机加密凭证 {len(cookies)} 条")
    if not cookies:
        # 不发无意义的浏览器启动：未登录就该明确提示重新登录，而不是让 Edge
        # 起来又退出、再被误报成「浏览器进程意外退出」。
        note("credentials", "凭证为空，跳过浏览器启动")
        raise RuntimeError(
            "尚未保存登录会话：请在「登录」里由本人完成一次 Microsoft Rewards "
            "登录（含 MFA），会话会加密保存在本机；同步与自动完成任务都用它。")

    last = None
    minimal_next = False
    for attempt in (1, 2):
        snap = new_automation_profile_dir(prefix=profile_prefix)
        note("profile", f"第 {attempt} 次：全新空目录 {snap.name}"
             + ("（最小参数）" if minimal_next else ""))
        ctx = None
        try:
            ctx = launch_edge(pw, user_data_dir=str(snap),
                              verbose=verbose, minimal=minimal_next, **kwargs)
            note("edge", "Edge 已启动" + ("（最小参数）" if minimal_next else ""))
            injected = inject_saved_cookies(ctx, store)
            note("cookies", f"已注入 {injected}/{len(cookies)} 条会话 Cookie")
            if verbose:
                print(f"[信息] 已注入 {injected} 条本机加密会话 Cookie")
            return ctx, snap
        except BaseException as exc:
            last = exc
            note("edge_error", f"{type(exc).__name__}: {str(exc)[:200]}")
            if ctx is not None:
                try:
                    ctx.close()
                except Exception:
                    pass
            cleanup_automation_profile(snap, prefix=profile_prefix)
            if not (isinstance(exc, PWError) and attempt == 1):
                raise
            if is_profile_conflict_error(exc):
                note("retry", "换一份全新空目录重试")
                continue                       # 占用类：换目录，参数不变
            if is_launch_arg_error(exc):
                note("retry", "启动参数不兼容，改用最小参数重试")
                minimal_next = True
                continue
            raise                             # 其余错误（没装 Edge 等）不空转
    raise last

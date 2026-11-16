# -*- coding: utf-8 -*-
"""启动真实 Microsoft Edge（headed）跑实验性自动化链路。

登录态来源默认是**应用自己的独立 profile**（``config.profile_dir()``，与只读
同步、登录窗口共用），并且自动化一律在它的快照副本里运行：

* 不碰用户日常打开的 Edge User Data，不会因为 Edge 正在运行就报
  ``ProcessSingleton`` / ``shutil.Error: [('...Permission denied...')]``；
* 不需要用户关闭浏览器，也不与后台只读同步抢同一份 Singleton 锁；
* 复制时跳过 Chromium 正在占用的锁文件 / WAL / 会话文件，单文件失败不中断。

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


IGNORE_DIRS = {
    "Cache", "Code Cache", "GPUCache", "ShaderCache", "Service Worker",
    "Crashpad", "logs", "blob_storage", "File System", "IndexedDB",
    "Session Storage", "Sessions", "VideoDecodeStats", "BudgetDatabase",
    "BrowserMetrics", "CrashpadMetrics",
}

# Chromium 运行时会独占这些文件；复制时失败是常态，跳过即可，不影响登录态。
SKIP_FILE_NAMES = {
    "Current Session", "Current Tabs", "Last Session", "Last Tabs",
    "DevToolsActivePort", "LOCK", "SingletonLock", "SingletonCookie",
    "SingletonSocket", ".DS_Store",
}
SKIP_SUFFIXES = ("-journal", "-wal", "-shm", ".tmp", ".log")

AUTOMATION_PROFILE_DIRNAME = "automation_edge_profile"


def default_profile_dir() -> Path:
    """Edge 用户数据根目录：Windows/macOS/Linux 均可识别。"""
    home = Path.home()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        return base / "Microsoft" / "Edge" / "User Data"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Microsoft Edge"
    cands = [
        home / ".config/microsoft-edge",
        home / ".config/microsoft-edge-stable",
        home / ".config/microsoft-edge-beta",
        home / ".config/microsoft-edge-dev",
        home / ".var/app/com.microsoft.Edge/config/microsoft-edge",
    ]
    for c in cands:
        if (c / "Default").exists() or c.exists():
            return c
    return cands[0]


def app_profile_dir() -> Path:
    """应用独立的登录 profile（登录窗口与只读同步共用，不碰日常 Edge）。"""
    return _app_profile_dir()


def automation_source_profile() -> Path:
    """自动化链路默认读取哪份登录态：应用独立 profile 优先。

    独立 profile 里已有 ``Default/`` 或 ``Local State`` 说明用户已完成应用内
    登录；否则退回系统 Edge 配置目录（仍会被复制成快照，不直接占用）。
    """
    p = app_profile_dir()
    if (p / "Default").is_dir() or (p / "Local State").is_file():
        return p
    return default_profile_dir()


def prune_automation_profiles(keep: int = 3) -> None:
    """清理历史快照目录；正在被进程占用的删不掉就留着，不报错。"""
    base = data_dir()
    try:
        cands = sorted(
            (d for d in base.iterdir()
             if d.is_dir() and d.name.startswith(AUTOMATION_PROFILE_DIRNAME + "_")),
            key=lambda d: d.stat().st_mtime, reverse=True)
    except OSError:
        return
    for d in cands[keep:]:
        shutil.rmtree(d, ignore_errors=True)


def new_automation_profile_dir() -> Path:
    """每次自动化运行都用**全新且唯一**的配置目录。

    固定复用同一个 ``automation_edge_profile`` 会让上一次残留的 Edge 进程
    （或它重建的 SingletonLock）把新启动 handoff 给自己后退出，Playwright
    于是报 ``Target page, context or browser has been closed`` —— 截图里那句
    「浏览器窗口被提前关闭」就是这么来的。唯一目录彻底断开这个 handoff 链。
    """
    prune_automation_profiles(keep=3)
    p = data_dir() / (f"{AUTOMATION_PROFILE_DIRNAME}_{os.getpid()}_"
                      f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:4]}")
    p.mkdir(parents=True, exist_ok=True)
    return p


def cleanup_automation_profile(path) -> None:
    """运行结束后删除本次快照；删不掉（进程未完全退出）就留给下次 prune。"""
    if path:
        shutil.rmtree(Path(path), ignore_errors=True)
    prune_automation_profiles(keep=3)


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


def copy_profile(src: Path, dst: Path) -> Path:
    """复制 profile 快照：带登录态，且单文件被占用时**只跳过、不整体失败**。

    Windows 上 Edge 正在运行时 ``shutil.copytree`` 会对 SQLite / 会话文件抛
    ``PermissionError`` 并汇总成 ``shutil.Error: [('C:\\Users\\...')]``，
    旧版因此把整条自动化链路判为「运行失败」。这里改为逐文件复制：缓存目录、
    锁文件、WAL 直接跳过，其余失败重试两次后跳过，保证快照始终可用。
    """
    src, dst = Path(src), Path(dst)
    if src.resolve() == dst.resolve():
        raise ValueError("快照目录不能作为源用户目录")
    if dst.exists():
        # 上次异常退出可能残留文件；删不掉的（被占用）留着，下面逐文件覆盖。
        shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True, exist_ok=True)
    # 关键：残留的 Singleton 锁会让 Chromium 直接报 Failed to create a
    # ProcessSingleton，即使快照本身是新的也一样。
    for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket",
                 "DevToolsActivePort", "LOCK"):
        try:
            (dst / lock).unlink()
        except OSError:
            pass
    dst_resolved = dst.resolve()

    for root, dirs, files in os.walk(src, topdown=True, followlinks=False):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS
                   and (Path(root) / d).resolve() != dst_resolved]
        rel = Path(root).relative_to(src)
        out = dst / rel
        out.mkdir(parents=True, exist_ok=True)
        for name in files:
            if name in SKIP_FILE_NAMES or name.endswith(SKIP_SUFFIXES):
                continue
            src_file, dst_file = Path(root) / name, out / name
            for attempt in range(3):
                try:
                    if src_file.is_symlink():
                        if not dst_file.exists():
                            dst_file.symlink_to(os.readlink(src_file))
                    else:
                        shutil.copy2(src_file, dst_file)
                    break
                except (OSError, shutil.Error):
                    if attempt < 2:
                        time.sleep(0.2)
                    # 仍失败：只是少一个非关键文件，绝不抛 shutil.Error
    return dst


def browser_error_hint(text: str, limit: int = 90) -> str:
    """把 Playwright / shutil 的原始异常压成一句可操作提示，不泄漏本地路径。"""
    raw = str(text or "").strip()
    first = raw.splitlines()[0] if raw else ""
    low = first.lower()
    if ("error: [(" in low or "shutil" in low or "errno 13" in low
            or "permission denied" in low or "winerror 32" in low
            or "being used by another process" in low):
        return "复制浏览器配置时部分文件被占用，已自动跳过；若仍未登录，请在应用内重新登录一次"
    if ("processsingleton" in low or "singletonlock" in low
            or "singletoncookie" in low or "failed to create a" in low
            or ("profile" in low and ("in use" in low or "already running" in low))):
        return "浏览器配置目录被占用：关闭应用自己打开的浏览器窗口后重试（日常 Edge 无需关闭）"
    if ("executable doesn't exist" in low or "browser executable" in low
            or "msedge" in low and ("not found" in low or "not exist" in low)):
        return "未找到 Microsoft Edge，请先安装 Edge 后重试"
    if ("target page, context or browser has been closed" in low
            or "browser has been closed" in low):
        return ("浏览器进程意外退出（配置目录被残留进程占用或窗口被关闭）；"
                "已自动换用全新配置重试，若仍失败请重启应用")
    if "timed out" in low or "timeout" in low:
        return "浏览器启动超时，请稍后重试"
    return first[:limit] or "浏览器启动失败，请重试"


def launch_edge(pw, *, user_data_dir: str, headless: bool = False,
                window_size: str | None = None, proxy: str | None = None,
                edge_path: str | None = None, slow_mo: int = 0,
                sandbox: str | None = None, suppress_flag_warning: bool | None = None,
                policy_dir: str | None = None, verbose: bool = True,
                flag_warning: str = "auto", geo=None):
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
            if "no_viewport" in msg or "unexpected" in msg.lower():
                kw.pop("no_viewport", None)
                return pw.chromium.launch_persistent_context(**kw)
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


def launch_automation_edge(pw, *, user_data_dir=None, source_profile=None,
                           verbose: bool = False, **kwargs):
    """复制一份唯一快照并启动 Edge；冲突时换全新目录自动重试一次。

    返回 ``(context, snapshot_path)``。调用方在 ``context.close()`` /
    ``pw.stop()`` 之后必须调用 ``cleanup_automation_profile(snapshot_path)``。
    """
    from playwright.sync_api import Error as PWError  # 延迟导入

    src = (Path(user_data_dir).expanduser() if user_data_dir
           else Path(source_profile or automation_source_profile()))
    if not src.exists():
        raise FileNotFoundError(
            f"浏览器配置目录不存在：{src}。请在应用内完成一次登录，"
            f"或用 --user-data-dir 指定 Edge 用户数据目录")

    last = None
    for attempt in (1, 2):
        snap = new_automation_profile_dir()
        if src.resolve() == snap.resolve():
            raise ValueError("自动化快照目录不能作为源用户目录")
        try:
            copy_profile(src, snap)
            ctx = launch_edge(pw, user_data_dir=str(snap),
                              verbose=verbose, **kwargs)
            return ctx, snap
        except PWError as exc:
            last = exc
            cleanup_automation_profile(snap)
            if attempt == 1 and is_profile_conflict_error(exc):
                if verbose:
                    print("[提示] 浏览器启动被占用，换用全新配置目录重试一次…")
                continue
            raise
        except BaseException:
            cleanup_automation_profile(snap)
            raise
    raise last

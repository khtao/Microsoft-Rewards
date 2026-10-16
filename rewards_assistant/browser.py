# -*- coding: utf-8 -*-
"""启动真实 Microsoft Edge（headed），复用用户数据目录里的 Cookie / 登录态。

要点：
1. launch_persistent_context + channel="msedge" —— 直接用系统安装的 Edge，
   指纹（字体、WebGL、UA、插件）都是本机真实值，不是 Playwright 自带的 Chromium。
2. 复用 user-data-dir，Cookie / localStorage / 历史记录全部沿用，省掉登录与养号。
3. 打掉最常被反作弊读取的 navigator.webdriver。
4. 沙箱：Playwright 默认 chromium_sandbox=False，它会自动注入 --no-sandbox，
   于是 Edge 顶上那条「不受支持的命令行标志」警告就是这么来的。本模块默认
   开回沙箱（chromium_sandbox=True）；root 下确实开不了沙箱时，用管理策略
   CommandLineFlagSecurityWarningsEnabled=false 关掉这条警告。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

# Edge on Linux 的管理策略目录（Chromium 是 /etc/chromium/policies/managed）
EDGE_POLICY_DIRS = (Path("/etc/opt/edge/policies/managed"),
                    Path("/etc/opt/microsoft/edge/policies/managed"))
# 这条策略专门用来关掉「不受支持的命令行标志」的安全警告横幅
FLAG_WARNING_POLICY = {"CommandLineFlagSecurityWarningsEnabled": False}

STEALTH_JS = """
(() => {
  const patch = (obj, name, value) => {
    try { Object.defineProperty(obj, name, { get: () => value, configurable: true }); } catch (e) {}
  };
  patch(navigator, 'webdriver', undefined);
  patch(navigator, 'languages', ['zh-CN', 'zh', 'en-US', 'en']);
  patch(navigator, 'hardwareConcurrency', 8);
  patch(navigator, 'deviceMemory', 8);
  patch(navigator, 'maxTouchPoints', 0);
  if (!window.chrome) window.chrome = {};
  window.chrome.runtime = window.chrome.runtime || {};
  window.chrome.loadTimes = window.chrome.loadTimes || function(){};
  window.chrome.csi = window.chrome.csi || function(){};
  const plugins = [
    {name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format'},
    {name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format'},
    {name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format'},
    {name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format'},
    {name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer', description: 'Portable Document Format'}
  ];
  plugins.forEach((p, i) => { p.length = 1; Object.defineProperty(p, i, {value: p}); });
  patch(navigator, 'plugins', plugins);
  patch(navigator, 'mimeTypes', [1, 2, 3]);
  try {
    const orig = navigator.permissions && navigator.permissions.query;
    if (orig) {
      navigator.permissions.query = (p) =>
        (p && p.name === 'notifications')
          ? Promise.resolve({ state: Notification.permission })
          : orig(p);
    }
  } catch (e) {}
  try {
    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    void origToDataURL;
  } catch (e) {}
})();
"""

IGNORE_DIRS = {
    "Cache", "Code Cache", "GPUCache", "ShaderCache", "Service Worker",
    "Crashpad", "logs", "blob_storage", "File System", "IndexedDB",
    "Session Storage", "VideoDecodeStats", "BudgetDatabase", "blob_storage",
}
IGNORE_FILES = {"SingletonLock", "SingletonCookie", "SingletonSocket", "LOCK", ".DS_Store"}


def default_profile_dir() -> Path:
    home = Path.home()
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


def is_root() -> bool:
    try:
        return os.geteuid() == 0
    except AttributeError:                     # Windows
        return False


# 会触发 Edge「不受支持的命令行标志」警告条的开关
UNSAFE_FLAGS = (
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-web-security",
    "--allow-running-insecure-content",
    "--disable-site-isolation-trials",
    "--single-process",
    "--allow-file-access-from-files",
)


def scan_edge_processes() -> list:
    """扫描当前正在运行的 Edge 进程，返回 (pid, 命中的不安全标志列表)。

    这是诊断横幅的关键：Edge 启动时若发现同 profile 已有进程，会把命令行
    **交给已存在的进程**（进程 handoff），新传的参数与刚写的策略都不会生效。
    所以真正决定有没有横幅的，往往是**早就开着那个 Edge 进程**的命令行。
    """
    hits = []
    try:
        pids = [d for d in os.listdir("/proc") if d.isdigit()]
    except Exception:
        return hits
    for pid in pids:
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "ignore")
        except Exception:
            continue
        if not cmd:
            continue
        exe = cmd.split("\x00")[0]
        if not ("/msedge" in exe or exe.endswith("msedge") or "microsoft-edge" in exe):
            continue
        flags = [f for f in cmd.split("\x00") if f in UNSAFE_FLAGS]
        hits.append((int(pid), flags))
    return hits


def diagnose_flag_warning(verbose: bool = True) -> dict:
    """诊断「不受支持的命令行标志」警告条会不会出现、根因是什么。"""
    root = is_root()
    procs = scan_edge_processes()
    running = [p for p in procs if p[1]]           # 真正带了不安全标志的进程
    info = {
        "root": root,
        "sandbox_on": resolve_sandbox(None),
        "edge_running": bool(procs),
        "unsafe_procs": running,
        "policy_installed": any((d / "human_search_policy.json").exists()
                                for d in EDGE_POLICY_DIRS),
    }
    if not verbose:
        return info

    print("── 「不受支持的命令行标志」诊断 " + "─" * 30)
    print(f"  运行身份      : {'root（沙箱不可用）' if root else '普通用户（可开沙箱）'}")
    print(f"  沙箱决策      : {'开 —— 不会出现 --no-sandbox' if info['sandbox_on'] else '关 —— 会带 --no-sandbox'}")
    print(f"  抑制策略      : {'已安装' if info['policy_installed'] else '未安装'}")
    if procs:
        print(f"  运行中的 Edge : {len(procs)} 个进程")
        for pid, flags in procs:
            print(f"      pid {pid}: {' '.join(flags) if flags else '（无不安全标志）'}")
    else:
        print("  运行中的 Edge : 无")

    print()
    if running:
        print("  >>> 根因：已有 Edge 进程带着不安全标志在跑。")
        print("      Edge 的进程 handoff 会让新启动的窗口复用这个进程，")
        print("      新传的参数和刚写入的策略都不会被重新读取 —— 横幅就是这么残留的。")
        print("      解决：完全退出 Edge 再运行脚本")
        print("        pkill -f msedge && sleep 2 && python3 main.py ...")
    elif root and not info["sandbox_on"]:
        print("  >>> 根因：root 下沙箱不可用，脚本被迫带 --no-sandbox。")
        print("      已自动写策略 + --test-type 兜底；若仍提示，先 pkill -f msedge 再跑。")
        print("      想彻底消除：改用普通用户运行（不要 sudo）。")
    elif not procs:
        print("  >>> 当前没有 Edge 进程，且参数干净：直接跑脚本不应出现横幅。")
    else:
        print("  >>> 现有 Edge 进程参数正常；若仍提示，pkill -f msedge 后重试一次。")
    return info


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
                 "microsoft-edge-dev", "msedge", "edge"):
        p = shutil.which(name)
        if p:
            return p
    for p in ("/usr/bin/microsoft-edge", "/usr/bin/microsoft-edge-stable",
              "/opt/microsoft/msedge/msedge", "/opt/microsoft/msedge-dev/msedge"):
        if os.path.exists(p):
            return p
    return None


def edge_is_running() -> bool:
    try:
        r = subprocess.run(["pgrep", "-u", str(os.getuid()), "-f", "msedge"],
                           capture_output=True, text=True, timeout=5)
        return bool(r.stdout.strip())
    except Exception:
        return False


def profile_locked(d: Path) -> bool:
    return any((d / f).exists() for f in ("SingletonLock", "SingletonSocket"))


def copy_profile(src: Path, dst: Path) -> Path:
    """复制一份 profile 快照：带上 Cookie，又不锁住正在运行的 Edge。"""
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    def _ignore(path, names):
        return {n for n in names if n in IGNORE_DIRS or n in IGNORE_FILES}

    shutil.copytree(src, dst, ignore=_ignore, symlinks=True, dirs_exist_ok=True)
    return dst


def launch_edge(pw, *, user_data_dir: str, headless: bool = False,
                window_size: str | None = None, proxy: str | None = None,
                edge_path: str | None = None, slow_mo: int = 0,
                sandbox: str | None = None, suppress_flag_warning: bool | None = None,
                policy_dir: str | None = None, verbose: bool = True,
                flag_warning: str = "auto"):
    """返回一个 persistent context（页面与 Cookie 全部落在真实 profile 里）。

    sandbox              "on" 开沙箱 / "off" 关沙箱 / None 自动（root 自动关）
    flag_warning         "off" 强制抑制警告条 / "on" 保留 / "auto" 仅在关沙箱时抑制
    """
    from playwright.sync_api import Error as PWError  # 延迟导入

    use_sandbox = resolve_sandbox(sandbox)
    if flag_warning == "on":
        suppress_flag_warning = False
    elif flag_warning == "off":
        suppress_flag_warning = True
    elif suppress_flag_warning is None:
        suppress_flag_warning = not use_sandbox

    if verbose and edge_is_running():
        print("[提示] 检测到已有 Edge 进程：新窗口可能被 handoff 给该进程，"
              "此时启动参数与策略不会重新生效。"
              "若警告条仍在，先 pkill -f msedge 再运行。")

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
        args=build_args(window_size, proxy, suppress_flag_warning=suppress_flag_warning),
        chromium_sandbox=use_sandbox,   # True 时 Playwright 不会加 --no-sandbox
        ignore_default_args=["--enable-automation"],
        permissions=["clipboard-read", "clipboard-write"],
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
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
    ctx.add_init_script(STEALTH_JS)
    return ctx

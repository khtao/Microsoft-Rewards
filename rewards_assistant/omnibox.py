# -*- coding: utf-8 -*-
"""浏览器地址栏（omnibox）的真实操作与前置条件检测。

地址栏不属于页面 DOM，Playwright 的键盘事件只发给 renderer，触达不到
Chromium omnibox。这里用 PyAutoGUI 把按键发给前台 Edge 窗口，真正触发一次
地址栏搜索：定位 → 清空 → 粘贴搜索词 → 回车。

因为粘贴的是**搜索词本身**（不是 URL），浏览器会用自己的默认搜索引擎解析，
所以只有默认引擎是 Bing 时这次搜索才等价于一次必应搜索。非必应时地址栏
搜索整体关闭，运行链路退回页面搜索框，设置页同步给出文本提示。

PyAutoGUI 是可选依赖：无图形环境 / 未安装时优雅降级，不允许 import 异常
冒到界面层。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

BING_MARKERS = ("bing.com",)
# Edge / Chromium 管理策略里强制指定默认搜索引擎的键
POLICY_URL_KEYS = ("DefaultSearchProviderSearchURL",)
POLICY_ENABLED_KEYS = ("DefaultSearchProviderEnabled",)
POLICY_DIRS = (
    "/etc/opt/edge/policies/managed",
    "/etc/chromium/policies/managed",
    "/Library/Application Support/Microsoft/Edge/policies/managed",
)

_LAST_STATUS: Optional[dict] = None
_GUI_CACHE: Any = ...          # ... = 尚未探测；None = 不可用


def _load_pyautogui():
    """延迟导入并缓存；无 DISPLAY / 未安装时返回 None，绝不抛异常。"""
    global _GUI_CACHE
    if _GUI_CACHE is not ...:
        return _GUI_CACHE
    try:
        import pyautogui
        _GUI_CACHE = pyautogui
    except Exception:
        _GUI_CACHE = None
    return _GUI_CACHE


def pyautogui_available() -> bool:
    return _load_pyautogui() is not None


def _is_bing(text: str) -> bool:
    low = str(text or "").lower()
    return any(m in low for m in BING_MARKERS) or "必应" in str(text or "")


def _policy_url() -> Optional[str]:
    """读 Edge 管理策略；策略会覆盖 profile 里的默认引擎设置。"""
    for d in POLICY_DIRS:
        try:
            for fp in sorted(Path(d).glob("*.json")):
                data = json.loads(fp.read_text(encoding="utf-8"))
                for key in POLICY_URL_KEYS:
                    if data.get(key):
                        return str(data[key])
        except Exception:
            continue
    if sys.platform == "win32":
        try:
            import winreg
            for root, sub in ((winreg.HKEY_LOCAL_MACHINE,
                               r"SOFTWARE\Policies\Microsoft\Edge"),
                              (winreg.HKEY_CURRENT_USER,
                               r"SOFTWARE\Policies\Microsoft\Edge")):
                try:
                    with winreg.OpenKey(root, sub) as k:
                        enabled, _ = winreg.QueryValueEx(
                            k, POLICY_ENABLED_KEYS[0])
                        if enabled:
                            url, _ = winreg.QueryValueEx(k, POLICY_URL_KEYS[0])
                            return str(url)
                except OSError:
                    continue
        except Exception:
            pass
    return None


def _profile_provider(profile: Path) -> tuple[Optional[str], Optional[str]]:
    """返回 (显示名, 搜索 URL)；字段缺失时返回 (None, None)。"""
    for name in ("Default/Secure Preferences", "Default/Preferences"):
        fp = profile / name
        if not fp.is_file():
            continue
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        node = data.get("default_search_provider_data") or {}
        tpl = node.get("template_url_data") or {}
        if not isinstance(tpl, dict):
            continue
        label = tpl.get("short_name") or tpl.get("keyword")
        url = tpl.get("url") or tpl.get("suggestions_url")
        if label or url:
            return (str(label or ""), str(url or ""))
    return (None, None)


def status(profile: Optional[Path] = None) -> dict:
    """检测能否进行地址栏搜索；纯本地读取，不启动浏览器、不联网。

    ``is_bing``   默认搜索引擎是否为 Bing（字段缺失按 Edge 出厂默认判定）；
    ``pyautogui`` PyAutoGUI 是否可用；
    ``enabled``   两者同时满足，地址栏搜索才会进行；
    ``reason``    ``ok`` / ``factory-default`` / ``not-bing`` / ``no-pyautogui``；
    ``provider``  给设置页展示的引擎名。
    """
    if profile is None:
        from .config import profile_dir
        profile = profile_dir()
    policy = _policy_url()
    label, url = _profile_provider(Path(profile))
    provider = "Bing"
    is_bing = True
    reason = "factory-default"
    if policy:
        is_bing = _is_bing(policy)
        provider = "Bing（策略）" if is_bing else policy.split("/")[2]
        reason = "ok" if is_bing else "not-bing"
    elif label or url:
        is_bing = _is_bing(url) or _is_bing(label)
        provider = label or url.split("/")[2]
        reason = "ok" if is_bing else "not-bing"

    gui = pyautogui_available()
    enabled = bool(is_bing and gui)
    if not gui:
        reason = "no-pyautogui"
    return {"is_bing": bool(is_bing), "pyautogui": gui,
            "enabled": enabled, "reason": reason, "provider": provider}


def remember(result: dict) -> dict:
    """运行链路把「本次浏览器窗口」的检测结果留给设置页显示。"""
    global _LAST_STATUS
    _LAST_STATUS = dict(result)
    return _LAST_STATUS


def current_status() -> dict:
    """设置页用：优先显示最近一次运行时的检测结果，否则做一次本地预检。"""
    return dict(_LAST_STATUS) if _LAST_STATUS is not None else status()


def _hotkey(gui, *keys):
    gui.hotkey(*keys)


def submit(page, keyword: str, human, log, budget=None) -> bool:
    """真实执行一次 omnibox 搜索：定位 → 清空 → 粘贴搜索词 → 回车。

    成功后浏览器自己跳到默认引擎的结果页，**不再调用 ``page.goto``**。
    返回 False 表示该轮未能触发（剪贴板写入失败等），由调用方记录并继续
    公共的结果阅读段；绝不伪造一次「成功的搜索」。
    """
    gui = _load_pyautogui()
    if gui is None:
        log("address_bar_skip", "PyAutoGUI 不可用，本轮不触发地址栏搜索")
        return False
    try:
        page.bring_to_front()
    except Exception:
        log("warn", "无法把浏览器窗口置前，地址栏按键可能落到其它窗口")
    human.pause(0.2, 0.5)

    cmd = "command" if sys.platform == "darwin" else "ctrl"
    log("focus", "定位地址栏（Ctrl+L / Cmd+L）")
    _hotkey(gui, cmd, "l")
    human.pause(0.15, 0.5)

    log("clear", "清空地址栏内容")
    _hotkey(gui, cmd, "a")
    gui.press("delete")
    human.pause(0.1, 0.3)

    try:
        page.evaluate("t => navigator.clipboard.writeText(t)", keyword)
    except Exception:
        log("warn", "剪贴板写入失败，无法向地址栏粘贴搜索词")
        return False
    human.pause(0.1, 0.4)
    log("paste", f"粘贴搜索词：{keyword}")
    _hotkey(gui, cmd, "v")
    human.pause(0.2, 0.7)

    log("submit", "回车搜索（omnibox）")
    gui.press("enter")
    timeout_ms = 20000
    if budget is not None:
        timeout_ms = int(max(4000, min(timeout_ms, budget.left * 1000 * 0.6)))
    try:
        page.wait_for_url("**/search?*", timeout=timeout_ms,
                          wait_until="domcontentloaded")
        log("address_bar", "omnibox 已跳转到必应结果页")
    except Exception:
        log("warn", "回车后未观察到结果页，本轮按当前页面继续阅读")
    human.pause(0.3, 0.9)
    return True

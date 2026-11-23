# -*- coding: utf-8 -*-
"""图形会话环境的单一判断来源（浏览器启动参数与地址栏诊断共用）。

Linux Wayland 会话若由 XWayland 提供了 ``DISPLAY``，可以给 Edge 加
``--ozone-platform=x11``，让浏览器窗口走 X11；pynput 的 XTEST 后端连的是
同一个 XWayland server，原生按键就能真正到达 omnibox。没有 X server 的纯
Wayland 会话**不能**加这个参数——浏览器会直接退出，此时地址栏分支按既有
降级逻辑关闭。

只做环境变量判断，不 import 任何图形库、不连 X server、不启动浏览器。
"""
from __future__ import annotations

import os
import sys

OZONE_X11_FLAG = "--ozone-platform=x11"
# 显式关掉 X11 ozone（例如用户用原生 Wayland + 其它输入后端）
NO_OZONE_X11_ENV = "MS_REWARDS_NO_OZONE_X11"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def is_wayland_session() -> bool:
    """当前桌面会话是否为 Wayland（含 XWayland 兼容层）。"""
    if not is_linux():
        return False
    return (bool(os.environ.get("WAYLAND_DISPLAY"))
            or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland")


def has_x11_display() -> bool:
    """是否存在可用的 X11 DISPLAY 变量（XWayland 会提供）。"""
    return bool(os.environ.get("DISPLAY"))


def session_kind() -> str:
    """给诊断与设置页文案用的会话类型。"""
    if not is_linux():
        return "native"
    if is_wayland_session():
        return "wayland-x11" if has_x11_display() else "wayland"
    if has_x11_display():
        return "x11"
    return "headless"


def x11_ozone_needed(headless: bool = False) -> bool:
    """是否给 Edge 注入 ``--ozone-platform=x11``。

    四个条件缺一不可：Linux、Wayland 会话、XWayland 提供了 DISPLAY、有头
    窗口。无头模式（后台只读同步）不需要 UI 输入，注入反而可能与新版
    headless ozone 冲突；用户可用环境变量显式关闭。
    """
    if headless or not is_linux() or not is_wayland_session():
        return False
    if os.environ.get(NO_OZONE_X11_ENV):
        return False
    return has_x11_display()


def ozone_x11_args(headless: bool = False) -> list:
    """返回需要追加的启动参数列表（0 或 1 项）。"""
    return [OZONE_X11_FLAG] if x11_ozone_needed(headless) else []

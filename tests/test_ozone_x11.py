# -*- coding: utf-8 -*-
"""Wayland 会话里给 Edge 注入 --ozone-platform=x11 的全部契约。

浏览器窗口走 XWayland 后，pynput 的 XTEST 按键连的是同一个 X server，
原生键盘事件才能真正到达 omnibox。重点是**没有 X server 时绝不加这个参数**
——那会让浏览器直接退出，比地址栏降级严重得多。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from rewards_assistant import browser, omnibox, platform_env
from rewards_assistant.ui.modern import viewmodel as vm

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def linux(monkeypatch):
    """把会话固定在 Linux，并清掉可能干扰判断的环境变量。"""
    monkeypatch.setattr(platform_env, "is_linux", lambda: True)
    for k in (platform_env.NO_OZONE_X11_ENV, "WAYLAND_DISPLAY",
              "XDG_SESSION_TYPE", "DISPLAY"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def _wayland(mp, display=True):
    mp.setenv("WAYLAND_DISPLAY", "wayland-0")
    mp.setenv("XDG_SESSION_TYPE", "wayland")
    if display:
        mp.setenv("DISPLAY", ":0")


# ---------------------------------------------------------------- 会话判断

def test_wayland_with_xwayland_needs_ozone_x11(linux):
    _wayland(linux)
    assert platform_env.is_wayland_session() is True
    assert platform_env.has_x11_display() is True
    assert platform_env.session_kind() == "wayland-x11"
    assert platform_env.x11_ozone_needed() is True
    assert platform_env.ozone_x11_args() == ["--ozone-platform=x11"]


def test_pure_wayland_without_display_must_not_add_flag(linux):
    """没有 X server 的纯 Wayland：加 ozone=x11 会让浏览器直接退出。"""
    _wayland(linux, display=False)
    assert platform_env.session_kind() == "wayland"
    assert platform_env.x11_ozone_needed() is False
    assert platform_env.ozone_x11_args() == []


def test_xorg_session_does_not_need_the_flag(linux):
    linux.setenv("DISPLAY", ":0")
    linux.setenv("XDG_SESSION_TYPE", "x11")
    assert platform_env.is_wayland_session() is False
    assert platform_env.session_kind() == "x11"
    assert platform_env.x11_ozone_needed() is False


def test_headless_never_gets_the_flag(linux):
    _wayland(linux)
    assert platform_env.x11_ozone_needed(headless=True) is False
    assert platform_env.ozone_x11_args(headless=True) == []


def test_env_switch_disables_the_flag(linux):
    _wayland(linux)
    linux.setenv(platform_env.NO_OZONE_X11_ENV, "1")
    assert platform_env.x11_ozone_needed() is False


def test_non_linux_never_gets_the_flag(monkeypatch):
    monkeypatch.setattr(platform_env, "is_linux", lambda: False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("DISPLAY", ":0")
    assert platform_env.session_kind() == "native"
    assert platform_env.x11_ozone_needed() is False


# ---------------------------------------------------------------- 启动参数

def test_build_args_includes_flag_only_for_headed_wayland_x11(linux):
    _wayland(linux)
    args = browser.build_args(headless=False)
    assert "--ozone-platform=x11" in args
    assert "--ozone-platform=x11" not in browser.build_args(headless=True)
    assert "--ozone-platform=x11" not in browser.build_args(ozone_x11=False)


def test_explicit_ozone_x11_override(linux):
    args = browser.build_args(headless=True, ozone_x11=True)
    assert "--ozone-platform=x11" in args


class _FakeCtx:
    def add_init_script(self, script):
        pass


def _pw(calls, fail_first=False, error="Target page, context or browser has been closed"):
    from playwright.sync_api import Error as PWError

    class PW:
        class chromium:
            @staticmethod
            def launch_persistent_context(**kw):
                calls.append(dict(kw))
                if fail_first and len(calls) == 1:
                    raise PWError(error)
                return _FakeCtx()

    return PW()


def test_launch_edge_passes_flag_for_headed_wayland(linux, tmp_path):
    _wayland(linux)
    calls = []
    ctx = browser.launch_edge(_pw(calls), user_data_dir=str(tmp_path / "p"),
                              headless=False, verbose=False, flag_warning="on")
    assert ctx is not None
    assert "--ozone-platform=x11" in calls[0]["args"]


def test_launch_edge_headless_omits_flag(linux, tmp_path):
    _wayland(linux)
    calls = []
    browser.launch_edge(_pw(calls), user_data_dir=str(tmp_path / "p"),
                        headless=True, verbose=False, flag_warning="on")
    assert "--ozone-platform=x11" not in calls[0]["args"]


def test_minimal_retry_has_no_args_at_all(linux, tmp_path):
    """最小参数降级路径不带 args，天然不会因 ozone 参数再失败一次。"""
    _wayland(linux)
    calls = []
    ctx = browser.launch_edge(_pw(calls), user_data_dir=str(tmp_path / "p"),
                              headless=False, verbose=False,
                              flag_warning="on", minimal=True)
    assert ctx is not None
    assert "args" not in calls[0]


def test_ozone_launch_failure_drops_flag_and_retries(linux, tmp_path):
    """XWayland 不可用 / Edge 拒绝该参数：去掉 flag 再试，保住浏览器能开。"""
    _wayland(linux)
    calls = []
    ctx = browser.launch_edge(
        _pw(calls, fail_first=True,
            error="Target page, context or browser has been closed (ozone x11)"),
        user_data_dir=str(tmp_path / "p"), headless=False,
        verbose=False, flag_warning="on")
    assert ctx is not None and len(calls) == 2
    assert "--ozone-platform=x11" in calls[0]["args"]
    assert "--ozone-platform=x11" not in calls[1]["args"]


# ---------------------------------------------------------------- 诊断联动

def test_omnibox_status_and_describe_report_ozone(linux):
    _wayland(linux)
    s = omnibox.status()
    assert s["session"] == "wayland-x11"
    assert s["ozone_x11"] is True
    d = omnibox.describe(s)
    assert "ozone_x11=True" in d and "session=wayland-x11" in d
    assert "\n" not in d


def test_omnibox_session_kind_matches_platform_env(monkeypatch):
    monkeypatch.setattr(platform_env, "is_linux", lambda: True)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    assert omnibox.session_kind() == platform_env.session_kind() == "wayland-x11"


def test_hint_for_wayland_x11_guides_xauthority():
    hint = vm.address_bar_hint({"is_bing": True, "keyboard": False,
                                "input_reason": "no-x11-display",
                                "session": "wayland-x11"})
    assert "XWayland" in hint and "XAUTHORITY" in hint
    en = vm.address_bar_hint({"is_bing": True, "keyboard": False,
                              "input_reason": "no-x11-display",
                              "session": "wayland-x11"}, "en")
    assert "XWayland" in en


# ---------------------------------------------------------------- 静态契约

def test_docs_and_dependency_comment_describe_the_flag():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    arch = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    for text in (readme, arch):
        assert "--ozone-platform=x11" in text
        assert "XWayland" in text
    assert "XWayland" in req or "Wayland" in req
    spec = (ROOT / "MSRewardsAssistant.spec").read_text(encoding="utf-8")
    assert "pynput.keyboard._xorg" in spec
    assert "py" + "autogui" not in (readme + arch + req + spec).lower()
    assert "platform_env.x11_ozone_needed" in (
        ROOT / "rewards_assistant" / "browser.py").read_text(encoding="utf-8")

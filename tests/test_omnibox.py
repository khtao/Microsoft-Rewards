# -*- coding: utf-8 -*-
"""omnibox：默认搜索引擎检测、pynput 四步提交与设置页提示文案。"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rewards_assistant import omnibox, platform_env
from rewards_assistant.ui.modern import viewmodel as vm


class _FakeKey:
    ctrl = "CTRL"
    cmd = "CMD"
    delete = "DELETE"
    enter = "ENTER"


class _Pressed:
    def __init__(self, ctl, mod):
        self.ctl, self.mod = ctl, mod

    def __enter__(self):
        self.ctl.calls.append(("down", self.mod))
        return self

    def __exit__(self, *a):
        self.ctl.calls.append(("up", self.mod))
        return False


class _FakeController:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def pressed(self, mod):
        return _Pressed(self, mod)

    def press(self, key):
        if self.fail_on == key:
            raise RuntimeError("keyboard device gone")
        self.calls.append(("press", key))

    def release(self, key):
        self.calls.append(("release", key))


class _FakePage:
    def __init__(self, clipboard_ok=True):
        self.clipboard = []
        self.gotos = []
        self.front = 0
        self.waited = []
        self.clipboard_ok = clipboard_ok

    def bring_to_front(self):
        self.front += 1

    def evaluate(self, script, arg):
        if not self.clipboard_ok:
            raise RuntimeError("clipboard denied")
        self.clipboard.append(arg)

    def wait_for_url(self, pattern, **kw):
        self.waited.append(pattern)

    def goto(self, url, **kw):
        self.gotos.append(url)


class _Human:
    def __init__(self):
        self.pauses = []

    def pause(self, *a, **k):
        self.pauses.append(a)


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setattr(omnibox, "_KB_CACHE", ...)
    monkeypatch.setattr(omnibox, "_KB_ERROR", "")
    monkeypatch.setattr(omnibox, "_CTRL_CACHE", None)
    monkeypatch.setattr(omnibox, "_CTRL_ERROR", "")
    monkeypatch.setattr(omnibox, "_LAST_STATUS", None)
    monkeypatch.setattr(omnibox, "_policy_url", lambda: None)


def _enable(monkeypatch, ctl=None):
    """让键盘后端在离线环境里表现为可用。"""
    ctl = ctl if ctl is not None else _FakeController()
    monkeypatch.setattr(omnibox, "_load_keyboard",
                        lambda: SimpleNamespace(Key=_FakeKey))
    monkeypatch.setattr(omnibox, "_controller", lambda: ctl)
    return ctl


def _disable(monkeypatch, error="ImportError: failed to acquire X connection: Bad display name"):
    monkeypatch.setattr(omnibox, "_load_keyboard", lambda: None)
    monkeypatch.setattr(omnibox, "_controller", lambda: None)
    monkeypatch.setattr(omnibox, "_KB_ERROR", error)
    monkeypatch.setattr(omnibox, "_CTRL_ERROR", "")


def _write_prefs(profile: Path, data: dict, name="Preferences"):
    d = profile / "Default"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(data), encoding="utf-8")


def _profile(tmp_path, url, label="Bing"):
    p = tmp_path / "profile"
    _write_prefs(p, {"default_search_provider_data": {
        "template_url_data": {"short_name": label, "keyword": label.lower(),
                              "url": url}}})
    return p


# ---------------------------------------------------------------- 默认引擎

def test_bing_provider_enables_address_bar(tmp_path, monkeypatch):
    _enable(monkeypatch)
    s = omnibox.status(_profile(tmp_path, "https://www.bing.com/search?q={searchTerms}"))
    assert s["is_bing"] is True and s["keyboard"] is True
    assert s["enabled"] is True and s["reason"] == "ok"


def test_google_provider_disables_address_bar(tmp_path, monkeypatch):
    _enable(monkeypatch)
    s = omnibox.status(_profile(tmp_path, "https://www.google.com/search?q={searchTerms}",
                                label="Google"))
    assert s["is_bing"] is False and s["enabled"] is False
    assert s["reason"] == "not-bing" and s["provider"] == "Google"


def test_secure_preferences_and_policy_are_read(tmp_path, monkeypatch):
    _enable(monkeypatch)
    p = tmp_path / "secure"
    _write_prefs(p, {"default_search_provider_data": {
        "template_url_data": {"short_name": "Baidu",
                              "url": "https://www.baidu.com/s?wd={searchTerms}"}}},
                 name="Secure Preferences")
    assert omnibox.status(p)["is_bing"] is False
    monkeypatch.setattr(omnibox, "_policy_url",
                        lambda: "https://www.bing.com/search?q={searchTerms}")
    assert omnibox.status(p)["is_bing"] is True, "策略优先于 profile"


def test_missing_field_falls_back_to_edge_factory_default(tmp_path, monkeypatch):
    _enable(monkeypatch)
    p = tmp_path / "empty"
    p.mkdir()
    s = omnibox.status(p)
    assert s["is_bing"] is True and s["reason"] == "factory-default"
    assert s["enabled"] is True


def test_keyboard_unavailable_disables_address_bar(tmp_path, monkeypatch):
    _disable(monkeypatch)
    s = omnibox.status(_profile(tmp_path, "https://www.bing.com/search?q={searchTerms}"))
    assert s["is_bing"] is True and s["keyboard"] is False
    assert s["enabled"] is False and s["reason"] == "no-keyboard"
    assert s["input_reason"] == "no-x11-display"
    assert "X connection" in s["input_error"]


def test_remember_caches_success_but_reprobes_failure(monkeypatch):
    """成功可缓存；失败每次重探，用户切到 Xorg 后无需重启应用。"""
    _enable(monkeypatch)
    omnibox.remember({"is_bing": True, "keyboard": True, "enabled": True,
                      "reason": "ok", "provider": "Bing（自动化窗口）"})
    assert omnibox.current_status()["provider"] == "Bing（自动化窗口）"

    monkeypatch.setattr(omnibox, "_LAST_STATUS", None)
    calls = []
    monkeypatch.setattr(omnibox, "keyboard_status",
                        lambda: calls.append(1) or {"available": False,
                                                    "reason": "no-x11-display",
                                                    "error": "", "session": "wayland",
                                                    "backend": "xorg"})
    omnibox.remember({"is_bing": True, "keyboard": False, "enabled": False,
                      "reason": "no-keyboard", "provider": "Bing"})
    assert omnibox.current_status()["keyboard"] is False
    assert omnibox.current_status()["keyboard"] is False
    assert len(calls) == 2, "失败结果不得被缓存"


# ---------------------------------------------------------------- 设置页文案

def test_hint_text_is_empty_when_conditions_hold():
    ok = {"is_bing": True, "keyboard": True, "enabled": True,
          "provider": "Bing"}
    assert vm.address_bar_hint(ok) == ""
    assert vm.address_bar_hint(ok, "en") == ""


def test_hint_text_for_non_bing_and_missing_backend():
    zh = vm.address_bar_hint({"is_bing": False, "provider": "Google"})
    assert zh == "默认搜索非必应，地址栏搜索不会进行（当前：Google）"
    en = vm.address_bar_hint({"is_bing": False, "provider": "Google"}, "en")
    assert en.startswith("Default search is not Bing; address-bar search will not run")

    assert "Wayland" in vm.address_bar_hint(
        {"is_bing": True, "keyboard": False,
         "input_reason": "no-x11-display", "session": "wayland"})
    assert "Xorg" in vm.address_bar_hint(
        {"is_bing": True, "keyboard": False,
         "input_reason": "no-x11-display", "session": "wayland"}, "en")
    assert "未安装 pynput" in vm.address_bar_hint(
        {"is_bing": True, "keyboard": False, "input_reason": "not-installed"})
    assert "依赖缺失" in vm.address_bar_hint(
        {"is_bing": True, "keyboard": False, "input_reason": "missing-dependency"})
    assert "键盘控制器" in vm.address_bar_hint(
        {"is_bing": True, "keyboard": False, "input_reason": "controller-failed"})
    assert "初始化失败" in vm.address_bar_hint(
        {"is_bing": True, "keyboard": False})
    en_fail = vm.address_bar_hint(
        {"is_bing": True, "keyboard": False}, "en")
    assert "failed to initialize" in en_fail and "（" not in en_fail


# ---------------------------------------------------------------- 诊断

def test_classify_real_linux_import_errors():
    assert omnibox._classify_input_error(
        "ImportError: failed to acquire X connection: Bad display name \"\"") == "no-x11-display"
    assert omnibox._classify_input_error(
        "Xlib.error.DisplayNameError: Bad display name") == "no-x11-display"
    assert omnibox._classify_input_error(
        "ModuleNotFoundError: No module named 'pynput'") == "not-installed"
    assert omnibox._classify_input_error(
        "ModuleNotFoundError: No module named 'Xlib'") == "missing-dependency"
    assert omnibox._classify_input_error(
        "PermissionError: /dev/uinput") == "controller-failed"
    assert omnibox._classify_input_error("RuntimeError: boom") == "import-failed"


def test_session_kind_follows_environment(monkeypatch):
    monkeypatch.setattr(platform_env, "is_linux", lambda: True)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert omnibox.session_kind() == "x11"
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert omnibox.session_kind() == "wayland-x11", "XWayland 提供了 DISPLAY"
    monkeypatch.delenv("DISPLAY")
    assert omnibox.session_kind() == "wayland"
    monkeypatch.delenv("WAYLAND_DISPLAY")
    assert omnibox.session_kind() == "headless"
    monkeypatch.setattr(platform_env, "is_linux", lambda: False)
    assert omnibox.session_kind() == "native"


def test_backend_name_respects_platform_and_override(monkeypatch):
    monkeypatch.delenv("PYNPUT_BACKEND", raising=False)
    monkeypatch.delenv("PYNPUT_BACKEND_KEYBOARD", raising=False)
    monkeypatch.setattr(omnibox.sys, "platform", "linux")
    assert omnibox.backend_name() == "xorg"
    monkeypatch.setattr(omnibox.sys, "platform", "win32")
    assert omnibox.backend_name() == "win32"
    monkeypatch.setattr(omnibox.sys, "platform", "darwin")
    assert omnibox.backend_name() == "darwin"
    monkeypatch.setenv("PYNPUT_BACKEND", "uinput")
    assert omnibox.backend_name() == "uinput", "尊重用户显式选择的后端"


def test_status_exposes_real_backend_error(monkeypatch, tmp_path):
    _disable(monkeypatch, "ImportError: failed to acquire X connection")
    monkeypatch.setattr(omnibox, "session_kind", lambda: "wayland")
    s = omnibox.status(tmp_path)
    assert s["enabled"] is False and s["reason"] == "no-keyboard"
    assert s["input_reason"] == "no-x11-display" and s["session"] == "wayland"
    assert s["backend"] == "xorg"
    d = omnibox.describe(s)
    assert "backend=xorg" in d and "input_reason=no-x11-display" in d
    assert "session=wayland" in d and "DISABLED" in d


def test_failed_import_is_retried(monkeypatch):
    """上次失败不写死缓存：后端恢复后同一进程内自动生效。"""
    import builtins
    real_import = builtins.__import__
    calls = []
    # `from pynput import keyboard` 要求返回对象带 keyboard 属性
    fake = SimpleNamespace(Key=_FakeKey)
    fake.keyboard = fake

    def flaky_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "pynput":
            calls.append(1)
            if len(calls) == 1:
                raise ImportError("failed to acquire X connection")
            return fake
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", flaky_import)
    assert omnibox._load_keyboard() is None
    assert omnibox._load_keyboard() is fake
    assert len(calls) == 2


def test_controller_cache_is_reset_on_key_failure(monkeypatch):
    """按键过程中设备掉线：清 Controller 缓存，下一轮重新构造。"""
    ctl = _FakeController(fail_on="l")
    _enable(monkeypatch, ctl)
    page = _FakePage()
    logs = []
    assert omnibox.submit(page, "广州 天气", _Human(),
                          lambda a, d="": logs.append((a, d))) is False
    assert omnibox._CTRL_CACHE is None
    assert any(a == "warn" and "键盘事件发送失败" in d for a, d in logs)
    # 修饰键一定释放，不会把 Ctrl 卡在按下状态
    assert ("up", "CTRL") in ctl.calls


# ---------------------------------------------------------------- 四步提交

def test_submit_follows_four_steps_and_never_gotos(monkeypatch):
    ctl = _enable(monkeypatch)
    page, human = _FakePage(), _Human()
    logs = []
    assert omnibox.submit(page, "广州 天气", human,
                          lambda a, d="": logs.append((a, d))) is True
    assert page.front == 1
    assert page.clipboard == ["广州 天气"]
    assert page.gotos == [], "跳转必须由 omnibox 回车触发"
    assert page.waited == ["**/search?*"]
    assert ctl.calls == [
        ("down", "CTRL"), ("press", "l"), ("release", "l"), ("up", "CTRL"),
        ("down", "CTRL"), ("press", "a"), ("release", "a"), ("up", "CTRL"),
        ("press", "DELETE"), ("release", "DELETE"),
        ("down", "CTRL"), ("press", "v"), ("release", "v"), ("up", "CTRL"),
        ("press", "ENTER"), ("release", "ENTER")]
    assert [a for a, _ in logs] == [
        "focus", "clear", "paste", "submit", "address_bar"]
    assert "定位地址栏" in logs[0][1] and "回车搜索（omnibox）" in logs[3][1]


def test_submit_uses_command_key_on_macos(monkeypatch):
    ctl = _enable(monkeypatch)
    monkeypatch.setattr(omnibox.sys, "platform", "darwin")
    omnibox.submit(_FakePage(), "weather", _Human(), lambda *a, **k: None)
    assert ctl.calls[0] == ("down", "CMD")
    assert ctl.calls[4] == ("down", "CMD")
    assert ctl.calls[10] == ("down", "CMD")


def test_clipboard_failure_returns_false_without_enter(monkeypatch):
    ctl = _enable(monkeypatch)
    page = _FakePage(clipboard_ok=False)
    logs = []
    assert omnibox.submit(page, "广州 天气", _Human(),
                          lambda a, d="": logs.append((a, d))) is False
    assert not any(c == ("press", "ENTER") for c in ctl.calls)
    assert any(a == "warn" for a, _ in logs)


def test_no_backend_returns_false_without_touching_the_page(monkeypatch):
    _disable(monkeypatch)
    page = _FakePage()
    logs = []
    assert omnibox.submit(page, "x", _Human(),
                          lambda a, d="": logs.append((a, d))) is False
    assert page.front == 0 and page.clipboard == []
    assert any(a == "address_bar_skip" for a, _ in logs)

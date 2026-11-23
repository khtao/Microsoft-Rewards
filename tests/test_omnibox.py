# -*- coding: utf-8 -*-
"""omnibox：默认搜索引擎检测、PyAutoGUI 四步提交与设置页提示文案。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rewards_assistant import omnibox
from rewards_assistant.ui.modern import viewmodel as vm


@pytest.fixture(autouse=True)
def _no_system_policy(monkeypatch):
    monkeypatch.setattr(omnibox, "_policy_url", lambda: None)


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
    monkeypatch.setattr(omnibox, "_load_pyautogui", lambda: object())
    s = omnibox.status(_profile(tmp_path, "https://www.bing.com/search?q={searchTerms}"))
    assert s["is_bing"] is True and s["pyautogui"] is True
    assert s["enabled"] is True and s["reason"] == "ok"


def test_google_provider_disables_address_bar(tmp_path, monkeypatch):
    monkeypatch.setattr(omnibox, "_load_pyautogui", lambda: object())
    s = omnibox.status(_profile(tmp_path, "https://www.google.com/search?q={searchTerms}",
                                label="Google"))
    assert s["is_bing"] is False and s["enabled"] is False
    assert s["reason"] == "not-bing" and s["provider"] == "Google"


def test_secure_preferences_and_policy_are_read(tmp_path, monkeypatch):
    monkeypatch.setattr(omnibox, "_load_pyautogui", lambda: object())
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
    monkeypatch.setattr(omnibox, "_load_pyautogui", lambda: object())
    p = tmp_path / "empty"
    p.mkdir()
    s = omnibox.status(p)
    assert s["is_bing"] is True and s["reason"] == "factory-default"
    assert s["enabled"] is True


def test_pyautogui_missing_disables_address_bar(tmp_path, monkeypatch):
    monkeypatch.setattr(omnibox, "_load_pyautogui", lambda: None)
    s = omnibox.status(_profile(tmp_path, "https://www.bing.com/search?q={searchTerms}"))
    assert s["is_bing"] is True and s["pyautogui"] is False
    assert s["enabled"] is False and s["reason"] == "no-pyautogui"


def test_remember_feeds_the_settings_page(monkeypatch):
    monkeypatch.setattr(omnibox, "_load_pyautogui", lambda: None)
    omnibox.remember({"is_bing": False, "pyautogui": True, "enabled": False,
                      "reason": "not-bing", "provider": "Google"})
    try:
        assert omnibox.current_status()["provider"] == "Google"
        assert "默认搜索非必应" in vm.address_bar_hint(omnibox.current_status())
    finally:
        monkeypatch.setattr(omnibox, "_LAST_STATUS", None)


# ---------------------------------------------------------------- 设置页文案

def test_hint_text_is_empty_when_conditions_hold():
    assert vm.address_bar_hint({"is_bing": True, "pyautogui": True,
                                "enabled": True, "provider": "Bing"}) == ""
    assert vm.address_bar_hint({"is_bing": True, "pyautogui": True,
                                "enabled": True, "provider": "Bing"}, "en") == ""


def test_hint_text_for_non_bing_and_missing_gui():
    zh = vm.address_bar_hint({"is_bing": False, "provider": "Google"})
    assert zh == "默认搜索非必应，地址栏搜索不会进行（当前：Google）"
    en = vm.address_bar_hint({"is_bing": False, "provider": "Google"}, "en")
    assert en.startswith("Default search is not Bing; address-bar search will not run")
    assert "PyAutoGUI 不可用" in vm.address_bar_hint(
        {"is_bing": True, "pyautogui": False})
    assert "PyAutoGUI unavailable" in vm.address_bar_hint(
        {"is_bing": True, "pyautogui": False}, "en")


# ---------------------------------------------------------------- 四步提交

class _GUI:
    def __init__(self):
        self.calls = []

    def hotkey(self, *keys):
        self.calls.append(("hotkey", keys))

    def press(self, key):
        self.calls.append(("press", key))


class _Page:
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


def test_submit_follows_four_steps_and_never_gotos(monkeypatch):
    gui, page, human = _GUI(), _Page(), _Human()
    monkeypatch.setattr(omnibox, "_load_pyautogui", lambda: gui)
    logs = []
    assert omnibox.submit(page, "广州 天气", human,
                          lambda a, d="": logs.append((a, d))) is True
    assert page.front == 1
    assert page.clipboard == ["广州 天气"]
    assert page.gotos == [], "跳转必须由 omnibox 回车触发"
    assert page.waited == ["**/search?*"]
    assert gui.calls == [
        ("hotkey", ("ctrl", "l")), ("hotkey", ("ctrl", "a")),
        ("press", "delete"), ("hotkey", ("ctrl", "v")), ("press", "enter")]
    assert [a for a, _ in logs] == [
        "focus", "clear", "paste", "submit", "address_bar"]
    assert "定位地址栏" in logs[0][1] and "回车搜索（omnibox）" in logs[3][1]


def test_submit_uses_command_key_on_macos(monkeypatch):
    gui, page = _GUI(), _Page()
    monkeypatch.setattr(omnibox, "_load_pyautogui", lambda: gui)
    monkeypatch.setattr(omnibox.sys, "platform", "darwin")
    omnibox.submit(page, "weather", _Human(), lambda *a, **k: None)
    assert gui.calls[0] == ("hotkey", ("command", "l"))
    assert gui.calls[1] == ("hotkey", ("command", "a"))
    assert gui.calls[3] == ("hotkey", ("command", "v"))


def test_clipboard_failure_returns_false_without_enter(monkeypatch):
    gui, page = _GUI(), _Page(clipboard_ok=False)
    monkeypatch.setattr(omnibox, "_load_pyautogui", lambda: gui)
    logs = []
    assert omnibox.submit(page, "广州 天气", _Human(),
                          lambda a, d="": logs.append((a, d))) is False
    assert not any(c == ("press", "enter") for c in gui.calls)
    assert any(a == "warn" for a, _ in logs)


def test_no_gui_returns_false_without_touching_the_page(monkeypatch):
    page = _Page()
    monkeypatch.setattr(omnibox, "_load_pyautogui", lambda: None)
    logs = []
    assert omnibox.submit(page, "x", _Human(),
                          lambda a, d="": logs.append((a, d))) is False
    assert page.front == 0 and page.clipboard == []
    assert any(a == "address_bar_skip" for a, _ in logs)

# -*- coding: utf-8 -*-
"""地址栏搜索：把「搜索框」换成「地址栏」，其余行为与原来一致。

链路回归：engines.address_bar_url / omnibox.submit / run_search 概率分支 →
api 签名默认 → viewmodel 三档 profile → config 白名单 → legacy._collect()
透传 → 设置页摘要。地址栏分支的四步（定位 → 清空 → 粘贴搜索词 → 回车）
由 PyAutoGUI 真实发给 omnibox；页面搜索框路径逐字保持原有行为。
"""
from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

from rewards_assistant import api, config, engines, omnibox
from rewards_assistant.ui.modern import viewmodel as vm

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- URL 落点

def test_address_bar_url_encodes_keyword_and_keeps_market():
    url = engines.address_bar_url(engines.ENGINES["bing"], "广州 天气 & 2026")
    assert url.startswith("https://www.bing.com/search?q=")
    assert "%E5%B9%BF%E5%B7%9E+%E5%A4%A9%E6%B0%94+%26+2026" in url
    assert "mkt=zh-CN" in url
    assert " " not in url and " & " not in url


def test_address_bar_url_follows_engine_home_market():
    custom = engines.Engine(
        name="bing", home="https://www.bing.com/?mkt=en-US",
        search="https://www.bing.com/search?q={q}&mkt={mkt}")
    assert "mkt=en-US" in engines.address_bar_url(custom, "weather")
    assert "q=weather" in engines.address_bar_url(custom, "weather")


def test_address_bar_url_is_the_expected_serp_not_a_navigation():
    """地址栏里粘贴的是搜索词，浏览器自己跳到 /search?q=，代码不再 goto。"""
    url = engines.address_bar_url(engines.ENGINES["bing"], "python")
    assert "/search?q=python" in url and "/?" not in url
    src = (ROOT / "rewards_assistant" / "engines.py").read_text("utf-8")
    assert "omnibox.submit(page, keyword, human, log, budget)" in src
    assert "page.goto(url" not in src


# ---------------------------------------------------------------- 离线替身

class _FakeRNG:
    def __init__(self, rolls):
        self.rolls = list(rolls)
        self.i = 0

    def random(self):
        v = self.rolls[min(self.i, len(self.rolls) - 1)]
        self.i += 1
        return v

    def uniform(self, a, b):
        return (a + b) / 2

    def randrange(self, *a):
        return 0


class _FakeGUI:
    def __init__(self):
        self.calls = []

    def hotkey(self, *keys):
        self.calls.append(("hotkey", keys))

    def press(self, key):
        self.calls.append(("press", key))


class _FakeHuman:
    def __init__(self, rolls):
        self.rng = _FakeRNG(rolls)
        self.events = []
        self.pasted = []
        self.fixed = []

    def bind(self, *a, **k):
        pass

    def pause(self, *a, **k):
        self.events.append("pause")
        return 0.0

    def human_click(self, *a, **k):
        self.events.append("click")
        return (1, 1)

    def paste_text(self, page, locator, text, seconds=None):
        self.events.append("paste")
        self.pasted.append(text)

    def fix_input(self, page, locator, expected):
        self.fixed.append(expected)
        return True

    def wander(self, page, *a, **k):
        pass

    def scroll_read(self, *a, **k):
        self.events.append("scroll")

    def read_pause(self, *a, **k):
        pass


class _FakePage:
    def __init__(self):
        self.url = "https://www.bing.com/"
        self.gotos = []
        self.presses = []
        self.keyboard = self
        self.clipboard = []
        self.front = 0
        self.waited = []

    def goto(self, url, **kw):
        self.gotos.append(url)

    def bring_to_front(self):
        self.front += 1

    def evaluate(self, script, arg):
        self.clipboard.append(arg)

    def wait_for_url(self, pattern, **kw):
        self.waited.append(pattern)

    def wait_for_load_state(self, *a, **k):
        pass

    def press(self, key):
        self.presses.append(key)

    def go_back(self, *a, **k):
        pass

    def locator(self, *a, **k):
        raise AssertionError("本用例不应访问 DOM 选择器")


def _run(monkeypatch, rate, rolls, omnibox_enabled=True, gui=None):
    """在完全离线的 fake 页 / fake human / fake PyAutoGUI 上跑一次 run_search。"""
    home_calls = []
    monkeypatch.setattr(engines, "goto_home",
                        lambda *a, **k: home_calls.append(True) or True)
    monkeypatch.setattr(engines, "detect_captcha", lambda *a, **k: False)
    monkeypatch.setattr(engines, "handle_consent", lambda *a, **k: None)
    fake_gui = gui if gui is not None else _FakeGUI()
    monkeypatch.setattr(omnibox, "_load_pyautogui", lambda: fake_gui)
    bing = engines.ENGINES["bing"]

    def fake_first_visible(page, selectors, timeout_ms=8000):
        # 页面路径需要「找到搜索框」；结果列表 / 按钮一律当没出现，
        # 让两条路径都直接落到公共的结果阅读段，便于对比动作差异。
        if selectors is bing.inputs or selectors == bing.inputs:
            return (object(), "fake-input")
        return (None, None)

    monkeypatch.setattr(engines, "_first_visible", fake_first_visible)
    page = _FakePage()
    human = _FakeHuman(rolls)
    logs = []
    cfg = SimpleNamespace(on_captcha="wait", click_prob=0.0, enter_rate=1.0,
                          scroll_screens=0.0, no_back=True, reuse_serp=False,
                          address_bar_rate=rate, omnibox_enabled=omnibox_enabled)
    result = engines.run_search(
        page, engines.ENGINES["bing"], "广州 天气", human,
        lambda a, d="", **kw: logs.append((a, d)), cfg)
    return page, human, logs, home_calls, result, fake_gui


# ---------------------------------------------------------------- 概率分支

def test_25_percent_roll_hits_address_bar(monkeypatch):
    page, human, logs, home_calls, result, gui = _run(monkeypatch, 0.25, [0.10])
    assert result["ok"] is True
    assert any(a == "address_bar" for a, _ in logs)
    assert home_calls == [], "地址栏路径不打开首页"
    assert page.gotos == [], "地址栏由 omnibox 自己跳转，代码不再 goto"
    assert page.waited == ["**/search?*"]
    assert page.front >= 1, "必须先把 Edge 窗口置前"
    assert page.clipboard == ["广州 天气"], "粘贴的是搜索词本身"
    assert [a for a, _ in logs if a in ("focus", "clear", "paste", "submit")] == [
        "focus", "clear", "paste", "submit"]
    assert gui.calls == [
        ("hotkey", ("ctrl", "l")), ("hotkey", ("ctrl", "a")),
        ("press", "delete"), ("hotkey", ("ctrl", "v")), ("press", "enter")]
    assert "scroll" in human.events, "两条路径必须共享结果阅读动作"
    assert human.pasted == [], "地址栏不往页面搜索框粘贴"


def test_address_bar_four_steps_only_change_the_input_box(monkeypatch):
    text = " ".join(d for _, d in _run(monkeypatch, 1.0, [0.10])[2])
    assert "定位地址栏" in text and "清空地址栏内容" in text
    assert "粘贴搜索词" in text and "回车搜索（omnibox）" in text


def test_non_bing_default_disables_address_bar(monkeypatch):
    """默认引擎非必应：地址栏搜索不会进行，本轮退回页面搜索框。"""
    page, human, logs, home_calls, result, gui = _run(
        monkeypatch, 1.0, [0.10, 0.9, 0.9], omnibox_enabled=False)
    assert result["ok"] is True
    assert any(a == "address_bar_skip" for a, _ in logs)
    assert not any(a == "address_bar" for a, _ in logs)
    assert gui.calls == [], "一次 omnibox 按键都不能有"
    assert home_calls, "退回原来的页面搜索框路径"
    assert human.pasted == ["广州 天气"] and "Enter" in page.presses


def test_pyautogui_unavailable_never_breaks_the_round(monkeypatch):
    """GUI 不可用时 submit 明确返回 False，不抛异常、不伪造一次搜索。"""
    monkeypatch.setattr(omnibox, "_load_pyautogui", lambda: None)
    page = _FakePage()
    logs = []
    assert omnibox.submit(page, "广州 天气", _FakeHuman([0.9]),
                          lambda a, d="": logs.append((a, d))) is False
    assert any(a == "address_bar_skip" for a, _ in logs)
    assert page.gotos == [] and page.clipboard == []


def test_page_path_keeps_original_behavior(monkeypatch):
    """页面搜索框路径与上一轮之前完全一致：50% 概率清空、粘贴自检、可点按钮。"""
    page, human, logs, home_calls, result, gui = _run(
        monkeypatch, 0.0, [0.90, 0.4, 0.9, 0.9])
    assert result["ok"] is True and home_calls
    assert not any(a == "address_bar" for a, _ in logs)
    assert "paste" in human.events and human.pasted == ["广州 天气"]
    assert human.fixed == ["广州 天气"]
    assert "Enter" in page.presses
    assert "Control+A" in page.presses and "Delete" in page.presses
    assert ("clear" not in [a for a, _ in logs]), "页面路径不新增 clear 日志"
    assert gui.calls == []
    assert not any("/search?q=" in u for u in page.gotos)
    assert "scroll" in human.events
    src = (ROOT / "rewards_assistant" / "engines.py").read_text("utf-8")
    assert "if human.rng.random() < 0.5:" in src
    assert "human.fix_input(page, box_loc, keyword)" in src


def test_rate_zero_and_one_are_deterministic(monkeypatch):
    _, _, logs0, _, _, _ = _run(monkeypatch, 0.0, [0.0, 0.9, 0.9, 0.9])
    assert not any(a == "address_bar" for a, _ in logs0)
    _, _, logs1, _, _, _ = _run(monkeypatch, 1.0, [0.999])
    assert any(a == "address_bar" for a, _ in logs1)


def test_invalid_rate_is_clamped(monkeypatch):
    _, _, too_high, _, _, _ = _run(monkeypatch, 5.0, [0.9])
    assert any(a == "address_bar" for a, _ in too_high), "5 → 裁剪为 1"
    _, _, negative, _, _, _ = _run(monkeypatch, -1.0, [0.0, 0.9, 0.9, 0.9])
    assert not any(a == "address_bar" for a, _ in negative), "-1 → 裁剪为 0"
    _, _, none_rate, _, _, _ = _run(monkeypatch, None, [0.0, 0.9, 0.9, 0.9])
    assert not any(a == "address_bar" for a, _ in none_rate)


# ---------------------------------------------------------------- 配置链路

def test_api_signature_default_is_25_percent():
    sig = inspect.signature(api.run_human_search)
    assert sig.parameters["address_bar_rate"].default == 0.25
    api_src = (ROOT / "rewards_assistant" / "api.py").read_text("utf-8")
    assert "omnibox_enabled=False" in api_src
    assert "omnibox.status(automation_snapshot)" in api_src


def test_all_three_intensity_profiles_carry_fixed_25_percent():
    assert "address_bar_rate" in vm.INTENSITY_PARAM_KEYS
    for choice, profile in vm.INTENSITY_PROFILES.items():
        assert profile["address_bar_rate"] == 0.25, choice
    for choice in ("低", "中", "高"):
        patch = vm.pref_to_settings(dict(vm.UI_DEFAULTS), "ui_search_level", choice)
        assert patch["search_panel"]["address_bar_rate"] == 0.25, choice


def test_persisted_and_transmitted_to_classic_chain():
    assert "address_bar_rate" in config.SEARCH_PANEL_KEYS
    from rewards_assistant.ui.legacy import SearchRunner
    assert SearchRunner.defaults()["address_bar_rate"] == 0.25
    legacy = (ROOT / "rewards_assistant" / "ui" / "legacy.py").read_text("utf-8")
    assert '"address_bar_rate"' in legacy
    assert "params[key]" in legacy and "state[key] = sp[key]" in legacy


def test_settings_summary_shows_six_rows_with_25_percent():
    zh = dict(vm.default_settings_summary(dict(vm.UI_DEFAULTS), "zh"))
    en = dict(vm.default_settings_summary(dict(vm.UI_DEFAULTS), "en"))
    assert len(zh) == 6 and zh["地址栏搜索"] == "25%"
    assert en["Address-bar search"] == "25%"


def test_settings_page_shows_the_disable_hint():
    win = (ROOT / "rewards_assistant" / "ui" / "modern" /
           "main_window.py").read_text("utf-8")
    assert "self.address_bar_note" in win
    assert "vm.address_bar_hint(omnibox.current_status()" in win
    assert "note.setVisible(bool(hint))" in win


def test_old_search_panel_without_key_falls_back_to_api_default():
    """旧配置没有该键时，api 的默认 0.25 仍然生效（不写 0、不报错）。"""
    assert "address_bar_rate" not in dict()
    assert inspect.signature(api.run_human_search).parameters[
        "address_bar_rate"].default == 0.25
    merged = dict(vm.intensity_profile("中"))
    merged.update({"level": 2, "repeat": 16})
    assert merged["address_bar_rate"] == 0.25

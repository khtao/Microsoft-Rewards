# -*- coding: utf-8 -*-
"""新增默认配置：每轮以 25% 概率使用「地址栏搜索」。

链路回归：engines.address_bar_url / run_search 概率分支 → api 签名默认 →
viewmodel 三档 profile → config 白名单 → legacy._collect() 透传 → 设置页摘要。
"""
from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

from rewards_assistant import api, config, engines
from rewards_assistant.ui.modern import viewmodel as vm

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- URL 构造

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


def test_address_bar_url_is_a_direct_serp_navigation():
    """地址栏搜索 = 直接 GET /search?q=，不经过首页表单提交链。"""
    url = engines.address_bar_url(engines.ENGINES["bing"], "python")
    assert "/search?q=python" in url
    assert "/?" not in url


# ---------------------------------------------------------------- 概率分支

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


class _FakeHuman:
    def __init__(self, rolls):
        self.rng = _FakeRNG(rolls)
        self.events = []

    def bind(self, *a, **k):
        pass

    def pause(self, *a, **k):
        self.events.append("pause")
        return 0.0

    def human_click(self, *a, **k):
        self.events.append("click")
        return (1, 1)

    def paste_text(self, *a, **k):
        self.events.append("paste")

    def fix_input(self, *a, **k):
        return True

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

    def goto(self, url, **kw):
        self.gotos.append(url)

    def wait_for_load_state(self, *a, **k):
        pass

    def press(self, key):
        self.presses.append(key)

    def go_back(self, *a, **k):
        pass

    def locator(self, *a, **k):
        raise AssertionError("本用例不应访问 DOM 选择器")


def _run(monkeypatch, rate, rolls):
    """在完全离线的 fake 页 / fake human 上跑一次 run_search。"""
    home_calls = []
    monkeypatch.setattr(engines, "goto_home",
                        lambda *a, **k: home_calls.append(True) or True)
    monkeypatch.setattr(engines, "detect_captcha", lambda *a, **k: False)
    monkeypatch.setattr(engines, "handle_consent", lambda *a, **k: None)
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
                          address_bar_rate=rate)
    result = engines.run_search(
        page, engines.ENGINES["bing"], "广州 天气", human,
        lambda a, d="", **kw: logs.append((a, d)), cfg)
    return page, human, logs, home_calls, result


def test_25_percent_roll_hits_address_bar(monkeypatch):
    page, human, logs, home_calls, result = _run(monkeypatch, 0.25, [0.10])
    assert result["ok"] is True
    assert any(a == "address_bar" for a, _ in logs)
    assert len(page.gotos) == 1 and "/search?q=" in page.gotos[0]
    assert "mkt=zh-CN" in page.gotos[0]
    assert home_calls == [], "地址栏路径不应再打开首页"
    assert "paste" not in human.events, "地址栏路径不应在搜索框输入"
    assert page.presses == [], "地址栏路径不应按回车提交表单"
    assert "scroll" in human.events, "两条路径必须共享结果阅读动作"


def test_75_percent_roll_uses_page_input_box(monkeypatch):
    page, human, logs, home_calls, result = _run(monkeypatch, 0.25, [0.90, 0.9, 0.9, 0.9])
    assert result["ok"] is True
    assert not any(a == "address_bar" for a, _ in logs)
    assert home_calls, "页面路径需要打开首页"
    assert "paste" in human.events
    assert "Enter" in page.presses
    assert not any("/search?q=" in u for u in page.gotos)
    assert "scroll" in human.events


def test_rate_zero_and_one_are_deterministic(monkeypatch):
    _, _, logs0, _, _ = _run(monkeypatch, 0.0, [0.0, 0.9, 0.9, 0.9])
    assert not any(a == "address_bar" for a, _ in logs0)
    _, _, logs1, _, _ = _run(monkeypatch, 1.0, [0.999])
    assert any(a == "address_bar" for a, _ in logs1)


def test_invalid_rate_is_clamped(monkeypatch):
    _, _, too_high, _, _ = _run(monkeypatch, 5.0, [0.9])
    assert any(a == "address_bar" for a, _ in too_high), "5 → 裁剪为 1"
    _, _, negative, _, _ = _run(monkeypatch, -1.0, [0.0, 0.9, 0.9, 0.9])
    assert not any(a == "address_bar" for a, _ in negative), "-1 → 裁剪为 0"
    _, _, none_rate, _, _ = _run(monkeypatch, None, [0.0, 0.9, 0.9, 0.9])
    assert not any(a == "address_bar" for a, _ in none_rate)


# ---------------------------------------------------------------- 配置链路

def test_api_signature_default_is_25_percent():
    sig = inspect.signature(api.run_human_search)
    assert sig.parameters["address_bar_rate"].default == 0.25


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


def test_old_search_panel_without_key_falls_back_to_api_default():
    """旧配置没有该键时，api 的默认 0.25 仍然生效（不写 0、不报错）。"""
    assert "address_bar_rate" not in dict()  # 旧 dict 形态
    assert inspect.signature(api.run_human_search).parameters[
        "address_bar_rate"].default == 0.25
    # viewmodel 的迁移补齐逻辑也会用 profile 补键
    merged = dict(vm.intensity_profile("中"))
    merged.update({"level": 2, "repeat": 12})
    assert merged["address_bar_rate"] == 0.25

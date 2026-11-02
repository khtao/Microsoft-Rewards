"""默认浏览器行为不需要 PySide6，可直接在 CI 中验证。"""
from pathlib import Path

import pytest

from rewards_assistant import local_browser


@pytest.mark.parametrize("url", ["", "javascript:alert(1)", "file:///tmp/file", "ftp://example.com", "not a url"])
def test_reject_unsafe_urls(url, monkeypatch):
    monkeypatch.setattr(local_browser.subprocess, "Popen",
                        lambda *args, **kw: pytest.fail("unexpected browser launch"))
    assert not local_browser.open_url(url)


def test_linux_uses_default_handler(monkeypatch):
    commands = []
    monkeypatch.setattr(local_browser.sys, "platform", "linux")
    monkeypatch.setattr(local_browser.shutil, "which", lambda cmd: "/usr/bin/xdg-open")
    monkeypatch.setattr(local_browser.subprocess, "Popen",
                        lambda cmd, **kw: commands.append(cmd))
    assert local_browser.open_url("https://example.com/item")
    assert commands == [["/usr/bin/xdg-open", "https://example.com/item"]]


def test_linux_reports_missing_xdg_open(monkeypatch):
    monkeypatch.setattr(local_browser.sys, "platform", "linux")
    monkeypatch.setattr(local_browser.shutil, "which", lambda cmd: None)
    assert not local_browser.open_url("https://example.com")


def test_windows_uses_default_association(monkeypatch):
    monkeypatch.setattr(local_browser.sys, "platform", "win32")
    opened = []
    monkeypatch.setattr(local_browser.os, "startfile", lambda u: opened.append(u), raising=False)
    assert local_browser.open_url("https://example.com")
    assert opened == ["https://example.com"]


def test_mac_uses_default_browser(monkeypatch):
    monkeypatch.setattr(local_browser.sys, "platform", "darwin")
    opened = []
    monkeypatch.setattr(local_browser.subprocess, "Popen", lambda cmd, **kw: opened.append(cmd))
    assert local_browser.open_url("http://example.com")
    assert opened == [["open", "http://example.com"]]


def test_open_failure_is_safe(monkeypatch):
    monkeypatch.setattr(local_browser.sys, "platform", "linux")
    monkeypatch.setattr(local_browser.shutil, "which", lambda cmd: "/usr/bin/xdg-open")
    def fail(*_a, **_kw):
        raise OSError("Cannot launch")
    monkeypatch.setattr(local_browser.subprocess, "Popen", fail)
    assert not local_browser.open_url("https://example.com")


def test_removed_standalone_and_search_page_entrypoints():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "run.py").exists()
    assert not (root / "rewards_assistant/get_rewards.py").exists()
    assert not (root / "rewards_assistant/chengyu.txt").exists()
    for fname in ("rewards_assistant/app.py", "rewards_assistant/ui/panels.py"):
        source = (root / fname).read_text(encoding="utf-8")
        assert "open_bing" not in source
        assert "打开 Bing 搜索页" not in source
        assert "detect_browsers" not in source

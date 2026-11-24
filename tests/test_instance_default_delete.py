# -*- coding: utf-8 -*-
"""启动默认环境指针、当前环境删除保护与浏览器启动链路的契约。

环境管理全部是本地文件操作，可在离线沙盒里完整验证；界面部分用源码级
静态契约锁定「删除键在名称旁边」「两道确认」「默认环境不可删」。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from rewards_assistant import autostart, browser, config, instances
from rewards_assistant.ui.modern import viewmodel as vm

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path / "data"))
    monkeypatch.setenv(config.ENV_INSTANCE, "default")
    config._preferred_default_for.cache_clear()
    yield
    config._preferred_default_for.cache_clear()


def _make(monkeypatch, label="工作账号", ident=None):
    entry = instances.create_instance(label, "zh")
    if ident is not None:
        assert entry["id"] == ident, entry["id"]
    return entry


# ---------------------------------------------------------------- 默认指针

def test_pointer_defaults_to_builtin():
    assert config.preferred_default_id() == config.DEFAULT_INSTANCE
    assert config.default_instance_pointer_path().name == "default_instance.json"
    assert config.default_instance_pointer_path().parent == config.base_data_dir()


def test_set_preferred_default_writes_pointer_and_reorders():
    _make(None, "工作账号", "acct-2")
    assert instances.set_default_instance("acct-2")["id"] == "acct-2"
    pointer = config.default_instance_pointer_path()
    assert json.loads(pointer.read_text(encoding="utf-8")) == {"id": "acct-2"}
    assert config.preferred_default_id() == "acct-2"
    assert config.is_preferred_default("acct-2") is True

    entries = instances.list_instances()
    assert entries[0]["id"] == "acct-2", "启动默认环境排第一"
    acct = next(e for e in entries if e["id"] == "acct-2")
    builtin = next(e for e in entries if e["id"] == "default")
    assert acct["is_preferred_default"] is True and acct["can_set_default"] is False
    assert builtin["is_builtin_default"] is True
    assert builtin["can_set_default"] is True
    assert builtin["can_delete"] is False


def test_invalid_or_stale_pointer_falls_back(tmp_path):
    pointer = config.default_instance_pointer_path()
    pointer.write_text('{"id": "ghost"}', encoding="utf-8")
    config._preferred_default_for.cache_clear()
    assert config.preferred_default_id() == "default"

    pointer.write_text("not json", encoding="utf-8")
    config._preferred_default_for.cache_clear()
    assert config.preferred_default_id() == "default"

    with pytest.raises(ValueError):
        config.set_preferred_default("ghost")


def test_pointer_only_applies_without_explicit_instance(monkeypatch):
    _make(monkeypatch, "工作账号", "acct-2")
    config.set_preferred_default("acct-2")
    monkeypatch.delenv(config.ENV_INSTANCE, raising=False)
    assert config.instance_id() == "acct-2"
    monkeypatch.setenv(config.ENV_INSTANCE, "default")
    assert config.instance_id() == "default", "显式 --instance 优先于指针"


def test_set_default_does_not_move_current_process(monkeypatch):
    """运行中改指针只影响下次启动，不改当前进程的数据目录 / IPC key。"""
    _make(monkeypatch, "工作账号", "acct-2")
    before = (config.data_dir(), config.single_instance_key())
    instances.set_default_instance("acct-2")
    assert (config.data_dir(), config.single_instance_key()) == before
    assert config.instance_id() == "default"


# ---------------------------------------------------------------- 删除保护

def test_delete_rejects_builtin_default():
    with pytest.raises(ValueError, match="内置默认环境"):
        instances.delete_instance("default")


def test_delete_rejects_non_current(monkeypatch):
    _make(monkeypatch, "工作账号", "acct-2")
    with pytest.raises(ValueError, match="只能删除当前"):
        instances.delete_instance("acct-2")


def test_delete_rejects_preferred_default(monkeypatch):
    _make(monkeypatch, "工作账号", "acct-2")
    instances.set_default_instance("acct-2")
    monkeypatch.setenv(config.ENV_INSTANCE, "acct-2")
    with pytest.raises(ValueError, match="默认环境不能删除"):
        instances.delete_instance("acct-2")


def test_delete_current_removes_only_its_own_directory(monkeypatch):
    _make(monkeypatch, "工作账号", "acct-2")
    _make(monkeypatch, "私人账号", "acct-3")
    root = config.base_data_dir()
    acct2 = root / "instances" / "acct-2"
    acct3 = root / "instances" / "acct-3"
    (acct2 / "settings.json").write_text("{}", encoding="utf-8")
    (acct2 / "session_cookies.bin").write_bytes(b"secret")
    (acct3 / "settings.json").write_text("{}", encoding="utf-8")
    pointer = config.default_instance_pointer_path()
    pointer.write_text('{"id": "acct-3"}', encoding="utf-8")
    config._preferred_default_for.cache_clear()

    monkeypatch.setenv(config.ENV_INSTANCE, "acct-2")
    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    out = instances.delete_instance("acct-2")
    assert out["deleted"] is True and out["id"] == "acct-2"
    assert not acct2.exists(), "当前环境目录必须真的删掉"
    assert acct3.exists() and (acct3 / "settings.json").exists()
    assert root.exists() and pointer.exists(), "根目录与其它环境不受影响"
    assert "acct-2" not in [e["id"] for e in instances.list_instances()]
    assert config.preferred_default_id() == "acct-3"


def test_delete_clears_autostart_when_enabled(monkeypatch):
    _make(monkeypatch, "工作账号", "acct-2")
    monkeypatch.setenv(config.ENV_INSTANCE, "acct-2")
    calls = []
    monkeypatch.setattr(autostart, "is_enabled", lambda: True)
    monkeypatch.setattr(autostart, "set_enabled",
                        lambda v: calls.append(v) or True)
    instances.delete_instance("acct-2")
    assert calls == [False]


# ---------------------------------------------------------------- 统一浏览器入口

def _launch_fixtures(monkeypatch, tmp_path, cookies):
    snap = tmp_path / "profile"
    ctx = SimpleNamespace(pages=[], close=lambda: None)
    injected = []
    monkeypatch.setattr(browser, "playwright_cookies", lambda store=None: cookies)
    monkeypatch.setattr(browser, "new_automation_profile_dir",
                        lambda *a, **k: snap)
    monkeypatch.setattr(browser, "launch_edge", lambda *a, **k: ctx)
    monkeypatch.setattr(browser, "inject_saved_cookies",
                        lambda *a, **k: injected.append(1) or len(cookies))
    monkeypatch.setattr(browser, "cleanup_automation_profile",
                        lambda *a, **k: None)
    return snap, ctx, injected


def test_launch_automation_edge_requires_credentials_by_default(monkeypatch, tmp_path):
    _launch_fixtures(monkeypatch, tmp_path, [])
    with pytest.raises(RuntimeError, match="尚未保存登录会话"):
        browser.launch_automation_edge(object(), headless=False)


# ---------------------------------------------------------------- 界面契约

def test_settings_page_puts_delete_next_to_the_name():
    win = (ROOT / "rewards_assistant" / "ui" / "modern" /
           "main_window.py").read_text("utf-8")
    assert win.count('Card(number="') == 6
    assert "QInputDialog" not in win
    assert "QFontMetrics" in win
    for needle in ("entry[\"can_delete\"]", "entry[\"can_set_default\"]",
                   "entry[\"is_preferred_default\"]",
                   "tr(lang, \"instance_delete\")",
                   "tr(lang, \"instance_set_default\")",
                   "app.delete_instance(i)", "app.set_default_instance(i)"):
        assert needle in win, needle
    # 删除按钮紧跟名称标签，位于同一行布局里
    start = win.index("    def refresh_instances(")
    end = win.index("    def current_search_rounds(", start)
    body = win[start:end]
    assert body.index("row.addWidget(label, 1)") < body.index(
        'tr(lang, "instance_delete")')
    assert "instance_open" in body and "instance_preferred" in body


def test_delete_instance_has_two_confirmations_and_exits():
    win = (ROOT / "rewards_assistant" / "ui" / "modern" /
           "main_window.py").read_text("utf-8")
    start = win.index("    def delete_instance(self, ident: str)")
    end = win.index("    # ------------------------------------------------------------ 日志 / 抓取",
                    start)
    method = win[start:end]
    assert method.count("QMessageBox.question(") == 2
    assert method.count("QMessageBox.StandardButton.No") >= 2  # 两道确认均默认「否」
    assert "instances.delete_instance(ident)" in method
    assert "self.store.close_thread_connection()" in method
    assert "self.controller.quit" in method
    assert "refresh_instances()" not in method, \
        "删除后刷新列表会把目录重新 mkdir 出来"
    assert "QMessageBox.StandardButton.Yes" in method


def test_entrypoint_pins_instance_id_before_data_layer():
    src = (ROOT / "rewards_assistant" / "__main__.py").read_text("utf-8")
    assert "preferred_default_id" in src
    assert "set_instance_id(instance if instance is not None else preferred_default_id())" in src
    assert "is_default_instance()" in src and "XDG_CACHE_HOME" in src


def test_i18n_covers_instance_actions():
    keys = ("account_delete_short", "instance_preferred", "instance_set_default", "instance_delete",
            "instance_set_default_done", "instance_delete_title",
            "instance_delete_q1", "instance_delete_q2", "instance_deleted",
            "instance_busy")
    for key in keys:
        assert vm.tr("zh", key) and vm.tr("zh", key) != key
        assert vm.tr("en", key) and vm.tr("en", key) != key


def test_automation_launcher_cleans_up_on_launch_failure(monkeypatch, tmp_path):
    """启动失败时由统一的启动函数自己清理 profile。"""
    snap = tmp_path / "profile"
    cleaned = []
    monkeypatch.setattr(browser, "playwright_cookies",
                        lambda store=None: [{"name": "a", "value": "b"}])
    monkeypatch.setattr(browser, "new_automation_profile_dir",
                        lambda *a, **k: snap)
    monkeypatch.setattr(browser, "cleanup_automation_profile",
                        lambda *a, **k: cleaned.append((a, k)))

    def boom(*a, **k):
        raise RuntimeError("edge missing")

    monkeypatch.setattr(browser, "launch_edge", boom)
    with pytest.raises(RuntimeError, match="edge missing"):
        browser.launch_automation_edge(object(), headless=False)
    assert len(cleaned) == 1 and cleaned[0][0][0] == snap

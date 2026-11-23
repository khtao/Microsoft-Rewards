# -*- coding: utf-8 -*-
"""多开环境：一个进程一个实例，环境完全独立，可登录不同账号。

覆盖：默认实例路径零迁移、六类数据路径隔离、加密凭证按实例加盐、
单实例 IPC 分流、开机自启命令、入口参数解析、UI 契约不被破坏。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from rewards_assistant import api, autostart, config, credentials, instances

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path / "data"))
    monkeypatch.setenv(config.ENV_INSTANCE, "default")


# ---------------------------------------------------------------- id 规范

def test_default_instance_keeps_legacy_paths_and_ipc_key():
    assert config.instance_id() == "default"
    assert config.is_default_instance()
    assert config.data_dir() == config.base_data_dir()
    assert config.db_path() == config.base_data_dir() / "rewards.db"
    assert config.profile_dir() == config.base_data_dir() / "browser_profile"
    assert config._settings_path() == config.base_data_dir() / "settings.json"
    assert config.single_instance_key() == "MSRewardsAssistant_SingleInstance_v1"
    assert config.window_title() == config.APP_TITLE
    assert config.win_app_id() == "Independent.RewardsActivityCompanion"
    assert config.autostart_name() == config.APP_NAME


@pytest.mark.parametrize("raw,want", [
    ("acct-2", "acct-2"), ("ACCT_2", "acct_2"), ("  work  ", "work"),
    ("账号A", "default"), ("", "default"), ("instances", "default"),
    ("../etc", "default"), ("a" * 60, "default"),
])
def test_normalize_instance_id(raw, want):
    assert config.normalize_instance_id(raw) == want


def test_all_data_paths_are_per_instance(monkeypatch):
    monkeypatch.setenv(config.ENV_INSTANCE, "acct-2")
    home = config.base_data_dir() / "instances" / "acct-2"
    assert config.data_dir() == home
    for fn, name in ((config.db_path, "rewards.db"),
                     (config.log_path, "run.log"),
                     (config.search_log_path, "search_actions.jsonl"),
                     (config.credentials_path, "session_cookies.bin"),
                     (config._settings_path, "settings.json")):
        assert fn() == home / name
    assert config.profile_dir() == home / "browser_profile"
    assert credentials.CookieStore().path == home / "session_cookies.bin"
    assert credentials.data_dir() == home
    assert str(api.default_log_path()) == str(home / "search_actions.jsonl")
    assert config.single_instance_key().endswith("_acct-2")
    assert config.autostart_name() == "MSRewardsAssistant_acct-2"
    assert "积分管理助手" in config.window_title()


def test_two_instances_never_share_a_path():
    a = config.instance_home("acct-2")
    b = config.instance_home("acct-3")
    assert a != b and a != config.instance_home("default")
    assert "instances/acct-2" in str(a).replace("\\", "/")
    assert "instances/acct-3" in str(b).replace("\\", "/")


# ---------------------------------------------------------------- 凭证隔离

def test_credentials_are_isolated_by_path_and_entropy(monkeypatch):
    """A 环境保存的账号，B 环境读不到；即便拷到 B 目录也解不开。"""
    monkeypatch.setenv(config.ENV_INSTANCE, "default")
    assert credentials._entropy() == credentials.ENTROPY  # 旧凭证零迁移
    a = credentials.CookieStore()
    a.save([{"name": "MSPAuth", "value": "ACCOUNT_A", "domain": ".bing.com"}])

    monkeypatch.setenv(config.ENV_INSTANCE, "acct-2")
    assert credentials._entropy() != credentials.ENTROPY
    b = credentials.CookieStore()
    assert b.path != a.path
    assert b.load() == [], "B 环境不该看到 A 账号的 Cookie"

    # 把 A 的密文原样拷到 B 的路径，密钥熵不同 → 解不开
    b.path.write_bytes(a.path.read_bytes())
    assert b.load() == [], "跨实例复制凭证必须失效"

    b.save([{"name": "MSPAuth", "value": "ACCOUNT_B", "domain": ".bing.com"}])
    assert b.load()[0]["value"] == "ACCOUNT_B"
    monkeypatch.setenv(config.ENV_INSTANCE, "default")
    assert a.load()[0]["value"] == "ACCOUNT_A", "A 环境未被 B 覆盖"


def test_each_instance_has_its_own_settings_and_db(monkeypatch):
    from rewards_assistant.storage import Store

    monkeypatch.setenv(config.ENV_INSTANCE, "default")
    s1 = config.Settings()
    s1.set("chart_days", 7)
    d1 = Store()
    monkeypatch.setenv(config.ENV_INSTANCE, "acct-2")
    s2 = config.Settings()
    assert s2.get("chart_days") != 7, "设置必须独立"
    s2.set("chart_days", 60)
    d2 = Store()
    assert d1.path != d2.path
    assert "instances/acct-2" in str(d2.path).replace("\\", "/")
    monkeypatch.setenv(config.ENV_INSTANCE, "default")
    assert config.Settings().get("chart_days") == 7


# ---------------------------------------------------------------- 环境管理

def test_create_and_list_instances():
    assert [e["id"] for e in instances.list_instances()] == ["default"]
    e1 = instances.create_instance("工作账号", "zh")
    e2 = instances.create_instance("Work account", "en")
    assert e1["id"] == "acct-2" and e1["label"] == "工作账号"
    assert e2["id"] == "acct-3" and e2["label"] == "Work account"
    assert json.loads((e1["home"] / config.INSTANCE_META).read_text("utf-8"))["id"] == "acct-2"
    entries = instances.list_instances()
    assert [e["id"] for e in entries] == ["default", "acct-2", "acct-3"]
    assert entries[0]["is_default"] and entries[0]["current"]
    assert entries[1]["ipc_key"] != entries[2]["ipc_key"]
    assert config.instance_label("acct-2") == "工作账号"


def test_launch_command_carries_instance_for_source_and_frozen(monkeypatch):
    assert instances.launch_command("acct-2")[-2:] == ["--instance", "acct-2"]
    assert "-m" in instances.launch_command("acct-2")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "C:/app/MSRewardsAssistant.exe")
    assert instances.launch_command("acct-2") == [
        "C:/app/MSRewardsAssistant.exe", "--instance", "acct-2"]


def test_launch_instance_uses_detached_process(monkeypatch):
    calls = {}

    def fake_popen(cmd, **kw):
        calls["cmd"], calls["kw"] = cmd, kw
        return object()

    monkeypatch.setattr(instances.subprocess, "Popen", fake_popen)
    assert instances.launch_instance("acct-2") is True
    assert calls["cmd"][-2:] == ["--instance", "acct-2"]
    assert calls["kw"]["stdin"] is instances.subprocess.DEVNULL
    assert (calls["kw"].get("start_new_session") or
            calls["kw"].get("creationflags")), "必须脱离当前进程"

    def boom(*_a, **_k):
        raise OSError("no exe")

    monkeypatch.setattr(instances.subprocess, "Popen", boom)
    assert instances.launch_instance("acct-2") is False


def test_create_and_launch_creates_meta_before_spawn(monkeypatch):
    seen = []
    monkeypatch.setattr(instances, "launch_instance",
                        lambda ident: seen.append(ident) or True)
    entry = instances.create_and_launch("zh")
    assert entry["launched"] is True and seen == [entry["id"]]
    assert (entry["home"] / config.INSTANCE_META).exists()


# ---------------------------------------------------------------- 自启 / IPC

def test_autostart_is_per_instance(monkeypatch):
    monkeypatch.setenv(config.ENV_INSTANCE, "default")
    assert "--instance" not in autostart._launch_command()
    assert autostart._entry_name() == "MSRewardsAssistant"
    monkeypatch.setenv(config.ENV_INSTANCE, "acct-2")
    cmd = autostart._launch_command()
    assert cmd[-3:-1] == ["--instance", "acct-2"]
    assert autostart._entry_name() == "MSRewardsAssistant_acct-2"
    assert autostart._linux_desktop().name == "msrewardsassistant_acct-2.desktop"
    assert autostart._mac_plist().name == "com.user.msrewardsassistant_acct-2.plist"


def test_ipc_key_is_instance_aware_in_app_source():
    src = (ROOT / "rewards_assistant" / "app.py").read_text("utf-8")
    assert "single_instance_key()" in src
    assert "connectToServer(SINGLE_INSTANCE_KEY)" not in src
    assert "server.listen(key)" in src
    assert "win_app_id()" in src
    assert "self.notifier.notify(window_title()," in src
    assert "def messages_enabled" in src and "def tray_enabled" in src


# ---------------------------------------------------------------- 入口参数

def test_entry_parses_instance_before_importing_data_layer(monkeypatch):
    from rewards_assistant import __main__ as entry

    captured = {}

    def fake_app_main(argv):
        captured["argv"] = argv
        captured["env"] = os.environ.get(config.ENV_INSTANCE)
        return 0

    import rewards_assistant.app as app_mod
    monkeypatch.setattr(app_mod, "main", fake_app_main)
    assert entry.main(["prog", "--widgets", "--instance", "acct-2",
                       "--minimized"]) == 0
    assert captured["env"] == "acct-2"
    assert "--instance" not in captured["argv"] and "acct-2" not in captured["argv"]
    assert "--widgets" not in captured["argv"]
    assert "--minimized" in captured["argv"]


def test_entry_supports_equals_form_and_default(monkeypatch):
    from rewards_assistant import __main__ as entry

    captured = {}
    import rewards_assistant.app as app_mod

    def fake_main(argv):
        captured["env"] = os.environ.get(config.ENV_INSTANCE)
        return 0

    monkeypatch.setattr(app_mod, "main", fake_main)
    entry.main(["prog", "--instance=work"])
    assert captured["env"] == "work"
    assert config.set_instance_id("bad/../id") == "default"


# ---------------------------------------------------------------- UI 契约

def test_ui_keeps_four_cards_and_tray_unchanged():
    win = (ROOT / "rewards_assistant" / "ui" / "modern" /
           "main_window.py").read_text("utf-8")
    assert win.count('Card(number="') == 6
    assert "QInputDialog" not in win
    assert 'self.setWindowTitle(window_title())' in win
    assert "def refresh_instances(self)" in win
    assert "def new_instance(" in win and "def open_instance(" in win
    tray = (ROOT / "rewards_assistant" / "app.py").read_text("utf-8")
    assert 'menu.addAction("显示主页面")' in tray
    assert 'menu.addAction("立即同步")' in tray
    assert 'menu.addAction("自动完成任务")' in tray
    assert 'menu.addAction("退出")' in tray


def test_linux_new_instance_gets_private_xdg_cache():
    """Linux 并发冷启动：两个实例不能抢同一个 fontconfig 缓存目录。"""
    src = (ROOT / "rewards_assistant" / "__main__.py").read_text("utf-8")
    assert "XDG_CACHE_HOME" in src and "instance_home() / \"cache\"" in src
    assert "is_default_instance()" in src


def test_readme_documents_multi_instance():
    text = (ROOT / "README.md").read_text("utf-8")
    assert "--instance" in text and "多开" in text
    assert "MS_REWARDS_INSTANCE" in text

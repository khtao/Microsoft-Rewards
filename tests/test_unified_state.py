# -*- coding: utf-8 -*-
"""统一存储结构的回归契约。

目标：
* 积分库 / 设置 / 凭证 / 最近同步 / 两个缓存**全部在一个** ``rewards.db``；
* 运行日志**只有** ``debug.log`` 一份；
* 设置只存 delta，不把默认值再抄一份；
* 库损坏 → 删除损坏文件 → 重建 → 回默认设置，不让应用起不来；
* 单个 kv 值损坏只影响该项；写不进盘时各有备选方案；
* 旧版散落文件一次性迁移且成功即删除，文件数真的变少。

不联网、不启动 Edge、不依赖 PySide6。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from rewards_assistant import config, credentials, debug_log, state, sync_state


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path / "data"))
    monkeypatch.setenv(config.ENV_INSTANCE, "default")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home" / ".cache").mkdir(parents=True, exist_ok=True)
    state._degraded_reason = None
    state._last_event = "ok"
    yield tmp_path
    state._degraded_reason = None
    state._last_event = "ok"


# ---------------------------------------------------------------- 1. 一个状态文件

def test_one_state_file_holds_all_four_functions(_isolated):
    assert state.ensure() == "ok"
    from rewards_assistant.storage import Store
    Store()                     # 积分库与 kv 表必须落在同一个文件里
    s = config.Settings()
    s.set("chart_days", 42)
    credentials.CookieStore().save([
        {"name": "MSPAuth", "value": "v", "domain": ".bing.com"}])
    sync_state.record(True, "同步成功")

    db = state.db_file()
    assert db.is_file()
    with sqlite3.connect(str(db)) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        keys = {r[0] for r in conn.execute("SELECT key FROM kv")}
    assert {"daily", "tasks", "used_terms", "kv"} <= tables
    assert {state.K_SETTINGS, state.K_CREDENTIALS,
            state.K_LAST_SYNC} <= keys

    # 四个功能都从这一个文件读得回来
    assert config.Settings().get("chart_days") == 42
    assert credentials.CookieStore().load()[0]["value"] == "v"
    assert sync_state.last()["ok"] is True

    # 常态下没有 WAL 附属文件，也没有旧的散落状态文件
    d = config.data_dir()
    for legacy in ("settings.json", "session_cookies.bin", "last_sync.json",
                   "run.log", "search_actions.jsonl",
                   "rewards.db-wal", "rewards.db-shm", "rewards.db-journal"):
        assert not (d / legacy).exists(), legacy


def test_storage_report_counts_only_two_own_files(_isolated):
    state.ensure()
    config.Settings().set("chart_days", 7)
    debug_log.info("ui", "hello")
    rep = state.storage_report()
    assert rep["state"] == "rewards.db" and rep["log"] == "debug.log"
    own = [n for n in rep["files"] if not n.startswith(".")]
    assert sorted(own) == ["debug.log", "rewards.db"], own


# ---------------------------------------------------------------- 2. 不存重复信息

def test_settings_store_only_delta_not_full_defaults(_isolated):
    state.ensure()
    s = config.Settings()
    s.set("chart_days", 90)
    delta = state.json_get(state.K_SETTINGS)
    assert delta is not None
    assert len(delta) < len(config.DEFAULT_SETTINGS) // 2, \
        "默认值不该整份再落盘一次"
    for k, v in config.DEFAULT_SETTINGS.items():
        assert k not in delta or delta[k] != v or k == "chart_days"
    assert config.Settings().get("sync_interval_minutes") == \
        config.DEFAULT_SETTINGS["sync_interval_minutes"]


def test_one_log_record_is_written_exactly_once(_isolated):
    debug_log.info("ui", "只写一次")
    text = state.debug_file().read_text("utf-8")
    assert text.count("只写一次") == 1
    assert config.log_path() == config.search_log_path() == state.debug_file()


# ---------------------------------------------------------------- 3. 损坏恢复

def test_corrupt_database_is_deleted_and_rebuilt_with_defaults(_isolated):
    db = state.db_file()
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"this is definitely not a sqlite database" * 8)
    assert state.ensure() == "rebuilt"
    assert db.is_file()
    with sqlite3.connect(str(db)) as conn:
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert config.Settings().get("chart_days") == \
        config.DEFAULT_SETTINGS["chart_days"]
    assert credentials.CookieStore().load() == []
    assert sync_state.last() is None
    assert "[WARN] [state]" in state.debug_file().read_text("utf-8")


def test_corrupt_database_can_be_quarantined_on_request(_isolated, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_KEEP_CORRUPT", "1")
    db = state.db_file()
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"broken" * 32)
    assert state.ensure() == "rebuilt"
    assert list(db.parent.glob("rewards.db.corrupt-*")), \
        "显式要求保留时应留一份损坏副本供排查"
    assert state.degraded_reason() == "corrupt-rebuilt"


def test_bad_kv_value_only_resets_that_key(_isolated):
    state.ensure()
    state.kv_set(state.K_CREDENTIALS, b"opaque-blob")
    state.kv_set(state.K_SETTINGS, b"{not json")
    assert state.json_get(state.K_SETTINGS) is None
    assert state.kv_get(state.K_SETTINGS) is None
    assert state.kv_get(state.K_CREDENTIALS) == b"opaque-blob"
    assert config.Settings().get("chart_days") == \
        config.DEFAULT_SETTINGS["chart_days"]


# ---------------------------------------------------------------- 4. 写失败备选

def test_settings_write_failure_falls_back_to_json(_isolated, monkeypatch):
    state.ensure()
    s = config.Settings()
    real = state.kv_set
    monkeypatch.setattr(state, "kv_set", lambda *a, **k: False)
    s.set("chart_days", 33)
    assert config._settings_path().is_file()
    assert json.loads(config._settings_path().read_text("utf-8"))["chart_days"] == 33
    monkeypatch.setattr(state, "kv_set", real)
    assert config.Settings().get("chart_days") == 33


def test_credential_write_failure_falls_back_to_file(_isolated, monkeypatch):
    state.ensure()
    real = state.kv_set
    monkeypatch.setattr(state, "kv_set", lambda *a, **k: False)
    store = credentials.CookieStore()
    n = store.save([{"name": "MSPAuth", "value": "v", "domain": ".bing.com"}])
    assert n == 1
    assert store.path.is_file(), "库写不进去必须退回独立加密文件"
    monkeypatch.setattr(state, "kv_set", real)
    assert store.load()[0]["value"] == "v"


def test_sync_state_write_failure_falls_back_to_json(_isolated, monkeypatch):
    state.ensure()
    real = state.json_set
    monkeypatch.setattr(state, "json_set", lambda *a, **k: False)
    entry = sync_state.record(False, "网络异常")
    assert sync_state.path().is_file()
    assert json.loads(sync_state.path().read_text("utf-8"))["ok"] is False
    monkeypatch.setattr(state, "json_set", real)
    assert sync_state.last()["detail"] == entry["detail"] == "网络异常"


def test_credential_write_failure_returns_zero_not_false_success(_isolated, monkeypatch):
    """两条路都不可用时明确返回 0，不假装登录成功。"""
    state.ensure()
    monkeypatch.setattr(state, "kv_set", lambda *a, **k: False)
    monkeypatch.setattr(credentials.CookieStore, "_save_to_file",
                        lambda self, blob: False)
    store = credentials.CookieStore()
    assert store.save([{"name": "MSPAuth", "value": "v"}]) == 0
    assert store.exists() is False


# ---------------------------------------------------------------- 5. 旧文件迁移

def test_legacy_files_are_migrated_and_deleted(_isolated, monkeypatch):
    d = config.data_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "settings.json").write_text(
        json.dumps({"chart_days": 77, "sync_interval_minutes": 30}), "utf-8")
    blob = credentials._seal(json.dumps(
        [{"name": "MSPAuth", "value": "legacy"}], ensure_ascii=False,
        separators=(",", ":")).encode("utf-8"))
    (d / "session_cookies.bin").write_bytes(blob)
    (d / "last_sync.json").write_text(
        json.dumps({"at": "2026-11-24T19:47:03", "ok": True, "detail": "ok"}), "utf-8")
    (d / "run.log").write_text("[19:47:03] 旧流水\n", "utf-8")
    (d / "search_actions.jsonl").write_text('{"action":"x"}\n', "utf-8")
    cache = Path.home() / ".cache"
    (cache / "human_search_trends.json").write_text(
        json.dumps({"ts": 1, "items": []}), "utf-8")
    (cache / "human_search_geo.json").write_text(
        json.dumps({"ip": "1.2.3.4"}), "utf-8")

    out = state.migrate_legacy()
    assert all(out.values()), out
    for name in ("settings.json", "session_cookies.bin", "last_sync.json",
                 "run.log", "search_actions.jsonl"):
        assert not (d / name).exists(), name
    assert not (cache / "human_search_trends.json").exists()
    assert not (cache / "human_search_geo.json").exists()

    assert config.Settings().get("chart_days") == 77
    # 密文是原样搬过去的，没有解密重加密
    assert state.kv_get(state.K_CREDENTIALS) == blob
    assert credentials.CookieStore().load()[0]["value"] == "legacy"
    assert sync_state.last()["ok"] is True
    log = state.debug_file().read_text("utf-8")
    assert "legacy run.log" in log and "旧流水" in log
    assert state.K_TRENDS_CACHE in state.kv_keys()
    assert state.K_GEO_CACHE in state.kv_keys()


def test_legacy_plaintext_cookies_are_not_deleted_before_migration(_isolated):
    """启动时不能提前删 cookies.txt：它要留给 CookieStore.migrate_legacy()。"""
    from rewards_assistant import session

    d = config.data_dir()
    d.mkdir(parents=True, exist_ok=True)
    legacy = d / "cookies.txt"
    legacy.write_text("Cookie: MSPAuth=OLD; MSCC=xyz", "utf-8")
    state.migrate_legacy()
    assert legacy.is_file(), "启动迁移不得抢先删掉明文凭证"
    session.default_fetcher()               # 这里才读明文 → 写 kv → 删除
    assert not legacy.exists()
    assert credentials.CookieStore().load()


def test_corrupt_legacy_file_is_deleted_instead_of_blocking(_isolated):
    d = config.data_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "settings.json").write_text('{"bad":', "utf-8")
    (d / "last_sync.json").write_text("not json", "utf-8")
    out = state.migrate_legacy()
    assert out["settings"] and out["last_sync"]
    assert not (d / "settings.json").exists()
    assert not (d / "last_sync.json").exists()
    assert config.Settings().get("chart_days") == \
        config.DEFAULT_SETTINGS["chart_days"]


# ---------------------------------------------------------------- 6. 缓存收拢

def test_caches_no_longer_live_in_home_dot_cache(_isolated):
    from rewards_assistant import geo, trends

    state.ensure()
    geo._save_cache({"ip": "1.2.3.4", "country": "CN"})
    trends._save_cache_kv([])
    assert state.kv_get(state.K_GEO_CACHE)
    assert state.kv_get(state.K_TRENDS_CACHE)
    cache = Path.home() / ".cache"
    assert not (cache / "human_search_geo.json").exists()
    assert not (cache / "human_search_trends.json").exists()
    assert geo._load_cache()["ip"] == "1.2.3.4"


# ---------------------------------------------------------------- 7. 静态契约

def test_no_second_state_file_is_created_by_production_code():
    """旧文件名只作为迁移/降级路径常量保留，常态写入必须走统一层。"""
    root = Path(__file__).resolve().parents[1] / "rewards_assistant"
    assert 'PRAGMA journal_mode=DELETE' in (root / "storage.py").read_text("utf-8")
    assert "state.ensure()" in (root / "__main__.py").read_text("utf-8")
    assert "state.migrate_legacy()" in (root / "__main__.py").read_text("utf-8")

    dbg = (root / "debug_log.py").read_text("utf-8")
    assert "_append(log_path()" not in dbg
    assert "def run_log_view(" in dbg and "def action_log_view(" in dbg

    win = (root / "ui/modern/main_window.py").read_text("utf-8")
    assert 'data_dir() / "run.log"' not in win
    assert '"run-session.log"' not in win
    assert "debug_log.run_log_view(raw)" in win

    api = (root / "api.py").read_text("utf-8")
    assert '"ACTION " + json.dumps(rec' in api

    for rel in ("config.py", "sync_state.py", "geo.py", "trends.py"):
        text = (root / rel).read_text("utf-8")
        assert "state." in text, f"{rel} 必须委托给统一状态层"

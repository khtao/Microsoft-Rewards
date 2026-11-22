# -*- coding: utf-8 -*-
"""本机加密凭证：不落明文、防篡改、过期过滤、明文迁移、退出登录清除。"""
from __future__ import annotations

import os
import stat
import time

import pytest

from rewards_assistant import credentials as cr


@pytest.fixture
def store(tmp_path):
    return cr.CookieStore(tmp_path / "session_cookies.bin")


def _cookies():
    return [
        {"name": "MSPAuth", "value": "SUPER_SECRET_VALUE_123",
         "domain": ".bing.com", "path": "/", "secure": True,
         "expires": time.time() + 3600},
        {"name": "expired", "value": "old", "expires": 1},
        {"name": "session", "value": "s", "expires": -1},
    ]


def test_roundtrip_never_writes_plaintext(store):
    store.save(_cookies())
    blob = store.path.read_bytes()
    assert blob.startswith(cr.MAGIC)
    assert b"SUPER_SECRET_VALUE_123" not in blob
    got = store.load()
    assert [c["name"] for c in got] == ["MSPAuth", "session"]
    assert got[0]["value"] == "SUPER_SECRET_VALUE_123"


def test_file_is_private_to_owner(store):
    store.save(_cookies())
    if os.name == "posix":
        mode = stat.S_IMODE(store.path.stat().st_mode)
        # 某些容器挂载忽略 chmod；只断言「不是世界可读」。
        assert not (mode & stat.S_IROTH)


def test_tampered_or_corrupt_file_is_treated_as_no_credentials(store):
    store.save(_cookies())
    blob = bytearray(store.path.read_bytes())
    blob[-3] ^= 0x01
    store.path.write_bytes(bytes(blob))
    assert store.load() == []
    store.path.write_bytes(b"not a credential file")
    assert store.load() == []
    store.path.write_bytes(cr.MAGIC + b"X" + b"short")
    assert store.load() == []


def test_clear_removes_credentials_and_temp(store):
    store.save(_cookies())
    assert store.exists()
    store.clear()
    assert not store.exists() and store.load() == []
    assert not (store.path.parent / (store.path.name + ".tmp")).exists()


def test_save_is_atomic_and_replaces_previous(store):
    store.save([{"name": "A", "value": "1"}])
    store.save([{"name": "B", "value": "2"}])
    got = store.load()
    assert len(got) == 1 and got[0]["name"] == "B"
    assert not (store.path.parent / (store.path.name + ".tmp")).exists()


def test_live_only_drops_expired_keeps_session_cookies():
    now = 1_000_000.0
    items = [{"name": "future", "expires": now + 10},
             {"name": "past", "expires": now - 10},
             {"name": "session", "expires": -1},
             {"name": "noexp"}]
    assert [c["name"] for c in cr.live_only(items, now=now)] == [
        "future", "session", "noexp"]


def test_normalize_keeps_only_what_requests_needs():
    out = cr.normalize([
        {"name": "A", "value": "1", "domain": ".bing.com", "path": "/",
         "secure": True, "httpOnly": True, "sameSite": "None", "expires": -1},
        {"name": "", "value": "x"},
        {"name": "B"},
        None,
    ])
    assert len(out) == 1
    assert set(out[0]) == {"name", "value", "domain", "path", "secure", "expires"}
    assert out[0]["expires"] == -1.0


def test_legacy_plaintext_is_migrated_then_deleted(store):
    legacy = store.path.parent / cr.LEGACY_FILENAME
    legacy.write_text("Cookie: A=1; B=two=three", encoding="utf-8")
    assert store.migrate_legacy() is True
    assert not legacy.exists(), "明文文件必须删除"
    assert {c["name"]: c["value"] for c in store.load()} == {
        "A": "1", "B": "two=three"}
    assert store.migrate_legacy() is False      # 幂等


def test_migration_does_not_delete_plaintext_on_failure(store, monkeypatch):
    legacy = store.path.parent / cr.LEGACY_FILENAME
    legacy.write_text("Cookie: A=1", encoding="utf-8")
    monkeypatch.setattr(store, "save", lambda cookies: 0)
    assert store.migrate_legacy() is False
    assert legacy.exists() and store.load() == []


def test_machine_seed_is_stable_and_nonempty():
    seed = cr._machine_seed()
    assert seed and seed == cr._machine_seed()


def test_seal_unseal_roundtrip_for_both_modes(monkeypatch):
    raw = b"payload with unicode \xe4\xb8\xad\xe6\x96\x87"
    monkeypatch.setattr(cr, "_dpapi", lambda data, protect: None)
    derived = cr._seal(raw)
    assert derived.startswith(cr.MAGIC + cr.MODE_DERIVED)
    assert cr._unseal(derived) == raw
    # 模拟 Windows DPAPI 可用的分支
    monkeypatch.setattr(cr, "_dpapi",
                        lambda data, protect: b"P:" + data if protect else
                        (data[2:] if data.startswith(b"P:") else None))
    sealed = cr._seal(raw)
    assert sealed.startswith(cr.MAGIC + cr.MODE_DPAPI)
    assert cr._unseal(sealed) == raw


def test_only_microsoft_related_domains_are_saved(store):
    """登录窗口导出的是整个 profile，无关站点 Cookie 不应落盘。"""
    cookies = [
        {"name": "MSPAuth", "value": "1", "domain": ".live.com", "expires": -1},
        {"name": "SRCHD", "value": "2", "domain": ".bing.com", "expires": -1},
        {"name": "rewards", "value": "3", "domain": "rewards.bing.com", "expires": -1},
        {"name": "_ga", "value": "4", "domain": ".google.com", "expires": -1},
        {"name": "ad_id", "value": "5", "domain": ".doubleclick.net", "expires": -1},
        {"name": "hostless", "value": "6", "domain": "", "expires": -1},
    ]
    assert store.save(cookies) == 4
    assert {c["name"] for c in store.load()} == {
        "MSPAuth", "SRCHD", "rewards", "hostless"}
    blob = store.path.read_bytes()
    assert b"_ga" not in blob and b"doubleclick" not in blob

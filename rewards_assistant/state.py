# -*- coding: utf-8 -*-
"""统一运行状态文件：一个 ``rewards.db`` 装下积分库、设置、凭证、同步状态与缓存。

设计目标
--------
* **一个状态文件**：SQLite 的 ``daily`` / ``tasks`` / ``used_terms`` 之外加一张
  ``kv`` 表，取代原先散落的 ``settings.json`` / ``session_cookies.bin`` /
  ``last_sync.json`` 与两个 ``~/.cache`` 文件。凭证仍是 :mod:`credentials`
  产出的**密文 blob** 原样存进去，不解密、不降级安全性。
* **不存重复信息**：设置只持久化与 ``DEFAULT_SETTINGS`` 不同的差异（delta），
  默认值由代码提供；运行日志全应用只写 ``debug.log`` 一份。
* **常态真的只有一个文件**：``journal_mode=DELETE``。WAL 会在库旁边常驻
  ``-wal`` / ``-shm``；DELETE 只在事务期间短暂出现 ``-journal``，空闲时清掉，
  对本项目「GUI 线程 + 一个同步线程、写事务都很短」的并发量没有实质损失。
* **稳健性**：启动做 ``PRAGMA quick_check``；库损坏 → 删除（或按要求隔离）
  → 重建 → 用默认设置继续跑。单个 kv 值坏 → 只让该项回默认，不重建整库。
  磁盘满 / 只读 / 权限不足 → 标记降级，调用方回退到独立文件或内存态，
  绝不因为写不进盘就让同步、登录、主流程崩掉。

本模块**不导入** :mod:`config`（路径函数内延迟导入），避免 ``config.Settings``
→ ``state`` → ``config`` 的循环。所有连接都是短连接 + ``busy_timeout``，
不与 :class:`storage.Store` 的线程局部长连接抢事务。
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

# kv 键：值统一是 bytes。credentials 是加密 blob，其余是 UTF-8 JSON。
K_SETTINGS = "settings"
K_CREDENTIALS = "credentials"
K_LAST_SYNC = "last_sync"
K_TRENDS_CACHE = "trends_cache"
K_GEO_CACHE = "geo_cache"

KV_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key         TEXT PRIMARY KEY,
    value       BLOB NOT NULL,
    updated_at  TEXT NOT NULL
);
"""

_BUSY_TIMEOUT_MS = 5000
_degraded_reason: Optional[str] = None
_last_event: str = "ok"          # ok / rebuilt / unavailable


# ---------------------------------------------------------------- 路径与连接

def db_file() -> Path:
    from .config import db_path
    return Path(db_path())


def debug_file() -> Path:
    """唯一运行日志；与 debug_log.debug_log_path() 同一个文件。"""
    from .config import data_dir
    return Path(data_dir()) / "debug.log"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_file()), timeout=15,
                           check_same_thread=False)
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode=DELETE")
    return conn


@contextmanager
def _session():
    """短连接上下文：``with conn`` 只管事务不会关闭连接，这里显式 close。

    kv 读写在同步 / 界面线程里很频繁，不 close 会慢慢累积文件描述符。
    """
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except BaseException:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def _note(message: str) -> None:
    """降级事件写进唯一日志；日志本身失败也不能抛。"""
    try:
        p = debug_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            f.write(f"[{ts}] [WARN] [state] {message}\n")
    except (OSError, ValueError):
        pass


def degraded() -> bool:
    return _degraded_reason is not None


def degraded_reason() -> Optional[str]:
    return _degraded_reason


def last_event() -> str:
    return _last_event


# ---------------------------------------------------------------- 完整性 / 恢复

def _create_schema(conn: sqlite3.Connection) -> None:
    """只在表缺失时执行 DDL：``kv_get`` 每次读都跑一遍 CREATE 会白拿写锁，
    在 ``Store.tx()`` 长事务期间可能白等 5 秒。"""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='kv'"
    ).fetchone()
    if not exists:
        conn.execute(KV_SCHEMA)


def _recover_corrupt(exc: BaseException) -> None:
    """库损坏：按用户要求删除损坏文件后重建；可用环境变量留一份便于排查。"""
    global _degraded_reason, _last_event
    p = db_file()
    _note(f"状态库完整性检查失败，删除并重建：{type(exc).__name__}: {exc}")
    try:
        if os.environ.get("MS_REWARDS_KEEP_CORRUPT", "").strip() in ("1", "true", "yes"):
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            p.replace(p.with_name(p.name + f".corrupt-{stamp}"))
        else:
            p.unlink(missing_ok=True)
    except OSError:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    # 顺手清掉可能跟着损坏库残留的 journal
    try:
        Path(str(p) + "-journal").unlink(missing_ok=True)
    except OSError:
        pass
    try:
        with _session() as conn:
            _create_schema(conn)
    except (sqlite3.Error, OSError) as err:
        # 连重建都失败（只读目录等）：彻底转内存降级，不再尝试写盘
        _degraded_reason = f"rebuild-failed: {type(err).__name__}"
        _last_event = "unavailable"
        _note(f"重建状态库失败，转为纯内存运行：{_degraded_reason}")
        return
    _degraded_reason = "corrupt-rebuilt"
    _last_event = "rebuilt"
    _note("状态库已重建；设置回落到默认值，积分历史为空，需要重新登录")


def ensure() -> str:
    """启动时调用一次：确保文件与 kv 表存在，返回 ok / rebuilt / unavailable。"""
    global _last_event
    try:
        db_file().parent.mkdir(parents=True, exist_ok=True)
        with _session() as conn:
            result = conn.execute("PRAGMA quick_check").fetchone()
            if not result or str(result[0]).lower() != "ok":
                raise sqlite3.DatabaseError(str(result[0] if result else "no result"))
            _create_schema(conn)
    except (sqlite3.Error, OSError) as exc:
        _recover_corrupt(exc)
        return _last_event
    _last_event = "ok"
    return "ok"


# ---------------------------------------------------------------- kv 读写

def kv_get(key: str) -> Optional[bytes]:
    """读一个值；库坏了自动重建并返回 None（调用方回默认）。"""
    try:
        with _session() as conn:
            _create_schema(conn)
            row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        return bytes(row[0]) if not isinstance(row[0], bytes) else row[0]
    except (sqlite3.Error, OSError) as exc:
        _recover_corrupt(exc)
        return None


def kv_set(key: str, value: bytes) -> bool:
    """写一个值。失败返回 False 并标记降级，调用方决定回退方案。"""
    global _degraded_reason
    try:
        with _session() as conn:
            _create_schema(conn)
            conn.execute(
                "INSERT INTO kv(key, value, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at",
                (key, sqlite3.Binary(bytes(value)),
                 datetime.now().isoformat(timespec="seconds")))
        return True
    except (sqlite3.Error, OSError) as exc:
        _degraded_reason = f"write-failed: {type(exc).__name__}"
        _note(f"状态库写入失败（{key}），调用方将启用备选方案：{exc}")
        return False


def kv_delete(key: str) -> bool:
    try:
        with _session() as conn:
            _create_schema(conn)
            conn.execute("DELETE FROM kv WHERE key=?", (key,))
        return True
    except (sqlite3.Error, OSError):
        return False


def kv_keys() -> list[str]:
    try:
        with _session() as conn:
            _create_schema(conn)
            return [str(r[0]) for r in conn.execute("SELECT key FROM kv")]
    except (sqlite3.Error, OSError):
        return []


# ---------------------------------------------------------------- JSON 小工具

def json_get(key: str) -> Optional[dict]:
    """读 JSON kv；坏 JSON 只让这一项回默认，并把坏值删掉避免反复报错。"""
    raw = kv_get(key)
    if raw is None:
        return None
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError):
        kv_delete(key)
        _note(f"kv[{key}] 内容损坏，已删除该项并回落到默认值")
        return None
    return obj if isinstance(obj, dict) else None


def json_set(key: str, obj: dict) -> bool:
    try:
        return kv_set(key, json.dumps(obj, ensure_ascii=False,
                                      separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- 旧文件迁移

def _legacy_paths() -> dict[str, Path]:
    from .config import data_dir
    d = Path(data_dir())
    return {
        "settings": d / "settings.json",
        "credentials": d / "session_cookies.bin",
        "last_sync": d / "last_sync.json",
        "run_log": d / "run.log",
        "search_log": d / "search_actions.jsonl",
    }


def migrate_legacy() -> dict[str, bool]:
    """把旧版散落文件一次性收进状态库，成功后删除旧文件。

    * 凭证**原样搬密文 blob**，不解密也不重新加密，零迁移风险；
    * 设置读的是「全量 JSON」，写入时由 :class:`config.Settings` 收敛成 delta；
    * 两份旧日志按行追加进 ``debug.log`` 后删除，历史不丢但不再各占一个文件；
    * 两个 ``~/.cache`` 文件同样搬进 kv（各带自己的 ts）。

    返回每个旧文件是否成功清理；任何一步失败都不删该文件，下次启动再试。
    """
    out: dict[str, bool] = {}
    # 不在这里调 ensure()：启动链已经做过完整性检查，重复调用会把
    # last_event 从 rebuilt 覆盖回 ok。kv_get/kv_set 自身会建表。
    p = _legacy_paths()
    existing = {k: kv_get(k) for k in (K_SETTINGS, K_CREDENTIALS, K_LAST_SYNC)}

    # --- 三个状态文件
    for key, path, kv_key in (
        ("settings", p["settings"], K_SETTINGS),
        ("credentials", p["credentials"], K_CREDENTIALS),
        ("last_sync", p["last_sync"], K_LAST_SYNC),
    ):
        if not path.is_file():
            out[key] = True
            continue
        if existing[kv_key] is not None:
            # kv 已有值：旧文件是残留，直接删（不覆盖较新的状态）
            try:
                path.unlink()
                out[key] = True
            except OSError:
                out[key] = False
            continue
        try:
            blob = path.read_bytes()
            if key != "credentials":
                json.loads(blob.decode("utf-8"))       # 坏文件不迁移，直接删
        except (OSError, ValueError, UnicodeDecodeError):
            try:
                path.unlink()                          # 损坏文件按约定删除
                _note(f"旧 {path.name} 已损坏，按默认设置处理并删除")
                out[key] = True
            except OSError:
                out[key] = False
            continue
        if kv_set(kv_key, blob):
            try:
                path.unlink()
                Path(str(path) + ".tmp").unlink(missing_ok=True)
                out[key] = True
            except OSError:
                out[key] = False
        else:
            out[key] = False                          # 写库失败：留着旧文件兜底

    # --- 两份旧日志并入 debug.log
    for key in ("run_log", "search_log"):
        path = p[key]
        if not path.is_file():
            out[key] = True
            continue
        try:
            target = debug_file()
            target.parent.mkdir(parents=True, exist_ok=True)
            # 逐行流式追加：旧日志是纯追加文件，可能已经很大，不整份读进内存
            with path.open("r", encoding="utf-8", errors="replace") as src, \
                    target.open("a", encoding="utf-8") as dst:
                dst.write(f"\n===== legacy {path.name} ====\n")
                for line in src:
                    dst.write(line)
                dst.write(f"===== end {path.name} =====\n")
            path.unlink()
            out[key] = True
        except OSError:
            out[key] = False

    # --- 两个外置缓存（原来硬编码在 ~/.cache，既不隔离也不受 HOME 覆盖）
    home_cache = Path.home() / ".cache"
    for name, kv_key in (("human_search_trends.json", K_TRENDS_CACHE),
                         ("human_search_geo.json", K_GEO_CACHE)):
        f = home_cache / name
        if not f.is_file():
            out[name] = True
            continue
        if kv_get(kv_key) is not None:
            try:
                f.unlink()
                out[name] = True
            except OSError:
                out[name] = False
            continue
        try:
            blob = f.read_bytes()
            json.loads(blob.decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            try:
                f.unlink()
                out[name] = True
            except OSError:
                out[name] = False
            continue
        if kv_set(kv_key, blob):
            try:
                f.unlink()
                out[name] = True
            except OSError:
                out[name] = False
        else:
            out[name] = False

    # 注意：遗留明文 cookies.txt **不能在这里删**。它由
    # credentials.CookieStore.migrate_legacy()（经 session.default_fetcher()）
    # 读进 kv 后才删除；启动时提前删会让旧版用户凭空丢一次登录态。
    return out


def storage_report() -> dict:
    """给诊断/测试用：当前实例常态下应该只有两个自有文件。"""
    from .config import data_dir
    d = Path(data_dir())
    own = sorted(x.name for x in d.iterdir() if x.is_file()
                 and not x.name.startswith("."))
    return {"dir": str(d), "files": own, "state": db_file().name,
            "log": debug_file().name, "event": _last_event,
            "degraded": _degraded_reason, "kv_keys": kv_keys()}

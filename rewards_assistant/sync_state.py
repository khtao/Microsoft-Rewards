# -*- coding: utf-8 -*-
"""最近一次同步结果：本地持久化，供登录对话框直接显示。

主界面右上角显示「已登录」来自本次同步，但登录对话框以前只显示
「尚未检测会话状态」——它从没问过主界面。这里把每次同步的时间与成功
与否写进当前实例的数据目录，登录对话框打开时就能如实显示「最近同步：
… · 同步成功 / 失败」，而不是让用户以为会话还没验证过。

与积分库分离：一个几字节的 JSON，原子替换；读写失败一律静默降级，
不影响同步主流程。多开时按实例目录隔离。退出登录会清掉这份记录。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from . import config

FILE_NAME = "last_sync.json"
_MAX_DETAIL = 160


def path():
    return config.data_dir() / FILE_NAME


def _valid(data) -> Optional[dict]:
    if not isinstance(data, dict) or not data.get("at") or "ok" not in data:
        return None
    return {"at": str(data["at"]), "ok": bool(data["ok"]),
            "detail": str(data.get("detail") or "")}


def record(ok: bool, detail: str = "") -> dict:
    """记一次**真正完成**的同步（距上次过近的跳过不算）。

    写入统一状态库；库不可写时回退到独立 JSON（原子替换），两条路都失败
    也只丢这一条状态，不影响同步本身。
    """
    entry = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "ok": bool(ok),
        "detail": str(detail or "").strip()[:_MAX_DETAIL],
    }
    from . import state
    if state.json_set(state.K_LAST_SYNC, entry):
        try:
            path().unlink(missing_ok=True)
            path().with_name(path().name + ".tmp").unlink(missing_ok=True)
        except OSError:
            pass
        return entry
    try:
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except (OSError, ValueError):
        pass
    return entry


def last() -> Optional[dict]:
    """读最近一次记录；缺失 / 损坏 / 字段不全都当作「从未同步」。"""
    from . import state
    data = state.json_get(state.K_LAST_SYNC)
    if data is None:
        try:
            data = json.loads(path().read_text("utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if data is not None:
            state.json_set(state.K_LAST_SYNC, data)
            try:
                path().unlink(missing_ok=True)
            except OSError:
                pass
    return _valid(data)


def clear() -> None:
    """退出登录 / 清除本地会话后调用，状态行回到「尚未检测」。"""
    from . import state
    state.kv_delete(state.K_LAST_SYNC)
    try:
        path().unlink(missing_ok=True)
        path().with_name(path().name + ".tmp").unlink(missing_ok=True)
    except OSError:
        pass


def stamp(entry: Optional[dict]) -> str:
    """把 ISO 时间显示成 11-24 19:47:03；异常时原样返回。"""
    if not entry:
        return ""
    raw = str(entry.get("at") or "")
    try:
        return datetime.fromisoformat(raw).strftime("%m-%d %H:%M:%S")
    except ValueError:
        return raw


def brief(detail: str, limit: int = 44) -> str:
    """同步失败原因压成一行，避免把整段网络日志铺在状态行里。"""
    text = " ".join(str(detail or "").split())
    return text[:limit] + "…" if len(text) > limit else text


def summary(entry: Optional[dict]) -> tuple[str, str]:
    """返回 (文案, 状态类别)；类别为 ok / err / normal，供界面配色加粗。"""
    if entry is None:
        return "尚未检测会话状态", "normal"
    ts = stamp(entry)
    if entry["ok"]:
        return f"最近同步：{ts} · 同步成功", "ok"
    reason = brief(entry["detail"])
    tail = f"（{reason}）" if reason else ""
    return f"最近同步：{ts} · 同步失败{tail}", "err"

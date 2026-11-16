# -*- coding: utf-8 -*-
"""后台同步：两页只读抓取 → 统一解析 → 落库（不推算、不写 0、不删历史）。

与旧版的区别
------------
旧链路：``/api/getuserinfo`` 单点 → 只有 available 余额是真值 → 「今日积分」
靠余额日差或任务合计回退 → HTML 只用来补标题（还要再抓一遍页面）。

新链路：

* 一次会话里读取 ``/dashboard`` + ``/earn``，两页 HTML 同时喂给
  ``readonly_report.build_report()``：三项积分、任务卡、搜索进度、解析日志
  一次拿全，不再额外抓页面补标题；
* 「今日积分」直接取页面显示值（``daily.today_points``），由
  ``points.calculate_daily_points`` 作为第一优先级，不再用余额日差或任务
  合计推算（余额日差是净变化，兑换扣分时会为负）；
* 三项积分 / 搜索进度读不到就是 ``None``：UI 显示「--」，保留上次快照，
  绝不写 0，也绝不伪造「搜索已满」；
* 网络错误、页面改版、部分页面不可用时保留上次快照并标记 stale，
  不覆盖、不清空任务、不弹「积分归零」提醒；
* 只有两页都完整可用且解析正常时才替换当日任务集合；
* ``可领取`` 只生成提醒，不自动领取；全程不搜索、不点击活动卡、不 POST。

对外契约保持：``StatusWorker`` / ``SyncWorker`` 的类名、构造签名与
``progress`` / ``succeeded`` / ``failed`` 信号不变，``app.py`` 无需改动。
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable, List, Optional

from PySide6.QtCore import QThread, Signal

from .auth import AuthError, AuthManager
from .config import SYNC_MIN_INTERVAL_SEC
from .readonly_report import CATEGORY_MAP, Report, build_report, to_legacy_dashboard
from .rewards_client import parse_dashboard
from .session import (
    ALLOWED_PATHS, DEGRADED, SIGNED_IN, SIGNED_OUT,
    ReadOnlyFetcher, default_fetcher, legacy_cookie_notice, probe, redact,
)
from .storage import Store, day_str


def _col(row, key, default=None):
    if row is None:
        return default
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return getattr(row, key, default)


def _stable_key(item) -> str:
    """页面卡片的稳定 key：同一张卡跨天 / 跨页可去重，不依赖易变的数组下标。"""
    raw = f"{item.name}|{item.description}|{item.points}|{item.section}"
    return "page:" + item.section + ":" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def rows_from_report(report: Report) -> List[dict]:
    """把解析结果转成 ``Store.upsert_task`` 的行。"""
    rows: List[dict] = []
    for group in report.groups:
        for item in group.items:
            if item.source == "counter":
                key, category = "pc_search", "搜索"
            else:
                key = _stable_key(item)
                category = CATEGORY_MAP.get(item.section, "更多活动")
            rows.append({
                "key": key,
                "name": item.name,
                "category": category,
                "points_max": int(item.points),
                "points_earned": int(item.earned),
                "complete": bool(item.completed),
                "link": item.url or "",
                "title_source": "page",
                "offer_id": "",
                "raw_name": item.name,
            })
    return rows


def _too_soon(store, day: str, min_interval_sec: int) -> bool:
    if min_interval_sec <= 0:
        return False
    synced_at = _col(store.daily(day), "synced_at")
    if not synced_at:
        return False
    try:
        last = datetime.fromisoformat(str(synced_at))
    except ValueError:
        return False
    return (datetime.now() - last).total_seconds() < min_interval_sec


def _last_snapshot(store, day: str) -> Optional[dict]:
    row = store.daily(day)
    return dict(row) if row is not None else None


# ---------------------------------------------------------------- 一次同步

def sync_once(
    store: Store,
    fetcher: Optional[ReadOnlyFetcher] = None,
    day: Optional[str] = None,
    level: int = 0,
    min_interval_sec: int = SYNC_MIN_INTERVAL_SEC,
    force: bool = False,
    progress: Optional[Callable[[str], None]] = None,
) -> dict:
    """执行一次只读同步。任何失败都返回 stale=True 并保留上次快照。

    纯函数式编排（除 store 外无副作用），可以在没有 Qt 的环境里直接单测。
    """
    day = day or day_str()
    emit = progress or (lambda _msg: None)
    fetcher = fetcher or default_fetcher()

    if not force and _too_soon(store, day, min_interval_sec):
        return {"ok": True, "skipped": True, "state": SIGNED_IN,
                "message": "距上次同步过近，已跳过（只读限频）",
                "summary": _last_snapshot(store, day), "stale": False}

    emit("正在只读检测会话…")
    status = probe(fetcher)
    if not status.can_sync:
        message = status.reason
        if status.state == SIGNED_OUT:
            message = "未登录或会话不可用：" + message
            note = legacy_cookie_notice()
            if note:
                message += " " + note
        return {"ok": False, "state": status.state, "message": message,
                "summary": _last_snapshot(store, day), "stale": True,
                "logs": list(status.warnings)}

    pages = status.pages
    dash = pages.get(ALLOWED_PATHS[0])
    earn = pages.get(ALLOWED_PATHS[1])
    dash_html = dash.html if dash and dash.usable else ""
    earn_html = earn.html if earn and earn.usable else ""
    emit("正在只读读取 /dashboard 与 /earn …")
    report = build_report(dash_html, earn_html)
    if report.signed_out:
        return {"ok": False, "state": SIGNED_OUT,
                "message": "未登录或会话不可用：识别为登录页 / 公开「关于」页，"
                           "不写假的 0 分，请先登录",
                "summary": _last_snapshot(store, day), "stale": True,
                "logs": report.logs}

    logs = list(report.logs) + list(status.warnings) + list(report.warnings)
    for line in logs:
        emit(redact(line))

    previous_rows = list(store.tasks_of_day(day))
    previous = {_col(r, "key"): r for r in previous_rows}
    rows = rows_from_report(report)
    # 页面临时缺字段时，沿用已保存的中文标题，不把名称覆盖成内部 ID
    for row in rows:
        old = previous.get(row["key"])
        if old is not None and not row["name"]:
            row["name"] = _col(old, "name", row["key"])
    emit(f"解析到 {len(rows)} 项任务，正在保存…")
    for r in rows:
        store.upsert_task(
            day=day, key=r["key"], name=r["name"], category=r["category"],
            points_max=r["points_max"], points_earned=r["points_earned"],
            complete=r["complete"], manual=False, link=r["link"],
            title_source=r["title_source"], offer_id=r["offer_id"],
            raw_name=r["raw_name"],
        )

    structure_changed = any("结构可能已变化" in w for w in report.warnings)
    complete_read = status.state == SIGNED_IN and not report.degraded
    if rows and complete_read and not structure_changed:
        # 两页都完整可用才替换；搜索进度未读到时保留上次搜索行
        keep = [r["key"] for r in rows]
        keep += [k for k in previous if k in ("pc_search", "mobile_search")]
        store.remove_missing_official_tasks(day, keep)
    else:
        msg = "本次未替换当日任务集合：页面读取不完整，保留上次记录"
        logs.append(msg)
        emit(msg)

    available = report.points.get("available")
    if available is not None:
        store.upsert_available(day, int(available), int(level))
    else:
        msg = "本次未读到可用积分，保留上次余额快照（不写 0）"
        logs.append(msg)
        emit(msg)

    # 必须先写真值列，再 refresh_day：汇总会把它作为「今日积分」第一优先级
    wrote_page = store.save_page_points(day, report.points)
    if not wrote_page:
        msg = "本次未读到页面「今日积分 / 可领取」，保留上次快照（不推算、不写 0）"
        logs.append(msg)
        emit(msg)

    summary = store.refresh_day(day)
    summary = dict(summary) if summary is not None else None

    claimable = report.points.get("claimable")
    messages: List[str] = []
    if claimable and claimable > 0:
        messages.append(
            f"有 {claimable} 分可领取，请到 rewards.bing.com 手动领取"
            "（通常 30 天内有效）；应用不代领。")
    if status.state == DEGRADED:
        messages.append("部分页面未读到，缺失项显示「--」并沿用上次快照。")
    if structure_changed:
        messages.append("页面结构可能变化，进度与积分以服务端最终记录为准。")

    # 兼容旧 UI：把页面真值转成原 dashboard JSON，再交给 parse_dashboard
    # 生成 Snapshot，app.py 的 _on_sync_ok / 自动完成链路无需改动。
    snap = parse_dashboard(to_legacy_dashboard(report, day, int(level)), day)

    return {
        "ok": True, "skipped": False, "stale": False,
        "state": status.state, "day": day,
        "points": dict(report.points),
        "groups": [{
            "key": g.key, "label": g.label, "earned": g.earned, "total": g.total,
            "items": [i.__dict__ for i in g.items],
        } for g in report.groups],
        "total": report.total, "earned": report.earned, "percent": report.percent,
        "summary": summary, "snapshot": snap, "official_streak": None,
        "message": " ".join(messages) or "同步完成（只读，进度以官网为准）",
        "logs": logs, "warnings": list(report.warnings),
    }


# ---------------------------------------------------------------- Qt 线程

class StatusWorker(QThread):
    """探测当前会话是否有效（五态压缩成 UI 需要的 bool + 文案）。"""

    done = Signal(bool, str)

    def run(self) -> None:
        try:
            st = AuthManager().status()
            if st.state == DEGRADED:
                self.done.emit(True, "会话可用（部分页面未读到，将降级同步）")
            else:
                self.done.emit(bool(st.can_sync), "" if st.can_sync else st.reason)
        except Exception as exc:
            self.done.emit(False, f"无法检测登录状态：{redact(str(exc))}")


class SyncWorker(QThread):
    """只读同步线程；信号与旧版完全一致。"""

    progress = Signal(str)
    succeeded = Signal(dict)
    failed = Signal(str)

    def __init__(self, store: Store, day: Optional[str] = None, parent=None,
                 enrich_pages: bool = True) -> None:
        super().__init__(parent)
        self.store = store
        self.day = day or day_str()
        # 兼容 app.py 的 45 分钟「页面补充」冷却参数：两页读取现在始终执行，
        # 该开关不再触发任何额外网络请求，仅保留字段避免改动调用点。
        self.enrich_pages = bool(enrich_pages)

    def run(self) -> None:
        try:
            # 自动同步的间隔由 app.py 的定时器控制；UI 触发时不重复限频
            outcome = sync_once(
                self.store, day=self.day, force=True,
                progress=self.progress.emit,
            )
            if outcome["ok"]:
                self.succeeded.emit(outcome)
            else:
                self.failed.emit(outcome["message"])
        except AuthError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit("同步失败：" + redact(str(exc)))
        finally:
            self.store.close_thread_connection()


def logout() -> None:
    """清除本地登录态（只清会话，不动历史数据）。"""
    AuthManager().logout()

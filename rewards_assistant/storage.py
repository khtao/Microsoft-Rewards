"""SQLite 数据层：每日汇总、任务明细、设置。

表设计
------
daily  : 每个自然日一行汇总（积分、任务完成数、可用积分、等级、连续天数）
tasks  : 任务明细，key 在同一天内唯一（用于增量 upsert）
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import List, Optional

from .config import db_path
from .task_labels import humanize_task
from .pending import all_reward_tasks_done
from .points import calculate_daily_points, TASK_FALLBACK, ALL_SOURCES

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily (
    day              TEXT PRIMARY KEY,
    points_earned    INTEGER NOT NULL DEFAULT 0,
    points_max       INTEGER NOT NULL DEFAULT 0,
    tasks_done       INTEGER NOT NULL DEFAULT 0,
    tasks_total      INTEGER NOT NULL DEFAULT 0,
    available_points INTEGER NOT NULL DEFAULT 0,
    level            INTEGER NOT NULL DEFAULT 0,
    streak           INTEGER NOT NULL DEFAULT 0,
    official_streak  INTEGER NOT NULL DEFAULT 0,
    official_points_earned INTEGER,  -- 兼容字段，不再参与计算
    available_synced_at TEXT,       -- 仅 API 真正返回余额时记录，0 也有效
    baseline_points INTEGER,       -- 差值所使用的昨日账户余额
    points_source TEXT NOT NULL DEFAULT 'task_fallback',
    -- 页面「今日积分 / 可领取」真值；NULL 表示本次没读到，绝不写 0
    today_points INTEGER,
    claimable_points INTEGER,
    page_points_synced_at TEXT,
    all_done         INTEGER NOT NULL DEFAULT 0,
    synced_at        TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    day           TEXT NOT NULL,
    key           TEXT NOT NULL,
    name          TEXT NOT NULL,
    category      TEXT NOT NULL,
    points_max    INTEGER NOT NULL DEFAULT 0,
    points_earned INTEGER NOT NULL DEFAULT 0,
    complete      INTEGER NOT NULL DEFAULT 0,
    link          TEXT NOT NULL DEFAULT '',
    manual        INTEGER NOT NULL DEFAULT 0,
    title_source  TEXT NOT NULL DEFAULT 'guess',
    offer_id      TEXT NOT NULL DEFAULT '',
    raw_name      TEXT NOT NULL DEFAULT '',
    updated_at    TEXT,
    UNIQUE(day, key)
);

CREATE INDEX IF NOT EXISTS idx_tasks_day ON tasks(day);

-- 当天已经用过的搜索词，避免重复搜索（同日去重）
CREATE TABLE IF NOT EXISTS used_terms (
    day     TEXT NOT NULL,
    term    TEXT NOT NULL,
    used_at TEXT,
    PRIMARY KEY (day, term)
);
"""


class Store:
    """线程安全的 SQLite 封装。GUI 线程与后台同步线程都会访问它。"""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = str(path or db_path())
        self._local = threading.local()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
        self._migrate()
        self._ensure_columns()
        self._migrate_legacy_points()

    def _migrate(self) -> None:
        """老库升级：缺列自动补，避免用户升级后打不开。"""
        with self._connect() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(daily)")}
            if "official_streak" not in cols:
                conn.execute(
                    "ALTER TABLE daily ADD COLUMN official_streak INTEGER NOT NULL DEFAULT 0"
                )
            if "official_points_earned" not in cols:
                conn.execute("ALTER TABLE daily ADD COLUMN official_points_earned INTEGER")
            if "points_source" not in cols:
                conn.execute("ALTER TABLE daily ADD COLUMN points_source TEXT NOT NULL DEFAULT 'task_fallback'")
            if "available_synced_at" not in cols:
                conn.execute("ALTER TABLE daily ADD COLUMN available_synced_at TEXT")
                # 仅正余额快照可确认；0 可能是无同步记录的默认值，不能猜。
                conn.execute("UPDATE daily SET available_synced_at=synced_at "
                             "WHERE available_points>0 AND synced_at IS NOT NULL")
            if "baseline_points" not in cols:
                conn.execute("ALTER TABLE daily ADD COLUMN baseline_points INTEGER")
            # 只读同步新增：三项积分里「今日 / 可领取」的真值列
            for col, decl in (("today_points", "INTEGER"),
                              ("claimable_points", "INTEGER"),
                              ("page_points_synced_at", "TEXT")):
                if col not in cols:
                    conn.execute(f"ALTER TABLE daily ADD COLUMN {col} {decl}")
            tcols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
            if "link" not in tcols:
                conn.execute("ALTER TABLE tasks ADD COLUMN link TEXT NOT NULL DEFAULT ''")
            conn.commit()

    def _ensure_columns(self) -> None:
        """老库补列：标题来源及用于核对同一活动的官方标识。"""
        with self._connect() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
            if "title_source" not in cols:
                conn.execute(
                    "ALTER TABLE tasks ADD COLUMN title_source TEXT NOT NULL DEFAULT 'guess'")
            if "manual" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN manual INTEGER NOT NULL DEFAULT 0")
            if "offer_id" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN offer_id TEXT NOT NULL DEFAULT ''")
            if "raw_name" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN raw_name TEXT NOT NULL DEFAULT ''")

    def _migrate_legacy_points(self) -> None:
        """一次性迁移既有 Earn/估算记录到统一的日差或任务回退规则。"""
        with self._connect() as conn:
            marks = ",".join("?" for _ in ALL_SOURCES)
            legacy = conn.execute(
                f"SELECT 1 FROM daily WHERE points_source NOT IN ({marks}) LIMIT 1",
                ALL_SOURCES,
            ).fetchone()
            if legacy is None:
                return
            rows = list(conn.execute(
                "SELECT day, available_points, available_synced_at FROM daily ORDER BY day"))
            totals = {r["day"]: int(r["points"] or 0) for r in conn.execute(
                "SELECT day, SUM(points_earned) AS points FROM tasks GROUP BY day")}
            by_day = {r["day"]: r for r in rows}
            updates = []
            for row in rows:
                yesterday = (datetime.strptime(row["day"], "%Y-%m-%d").date()
                             - timedelta(days=1)).isoformat()
                prev = by_day.get(yesterday)
                current_balance = (int(row["available_points"])
                                   if row["available_synced_at"] is not None else None)
                prev_balance = (int(prev["available_points"])
                                if prev is not None and prev["available_synced_at"] is not None
                                else None)
                earned, source, baseline = calculate_daily_points(
                    current_balance, prev_balance, totals.get(row["day"], 0))
                updates.append((earned, source, baseline, row["day"]))
            with self.tx() as tx:
                tx.executemany(
                    "UPDATE daily SET points_earned=?, points_source=?, baseline_points=?, "
                    "official_points_earned=NULL WHERE day=?", updates)
        if rows:
            self.recompute_streaks_from(rows[0]["day"])

    def _connect(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def close_thread_connection(self) -> None:
        """后台同步线程退出时主动关闭 SQLite 连接，避免长时间积累句柄。"""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            self._local.conn = None
            conn.close()

    @contextmanager
    def tx(self):
        """可重入事务：外层统一提交，内层调用只借用同一个连接。

        同步一次会写任务明细、余额、页面真值并刷新汇总；外层
        ``with store.tx():`` 包住整段写入后只提交一次，嵌套的 ``tx()``
        不再重复提交。
        """
        conn = self._connect()
        if getattr(self._local, "in_tx", False):
            yield conn
            return
        self._local.in_tx = True
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._local.in_tx = False

    # ------------------------------------------------------------------ 任务

    def upsert_task(
        self,
        day: str,
        key: str,
        name: str,
        category: str,
        points_max: int,
        points_earned: int,
        complete: bool,
        manual: bool = False,
        link: str = "",
        title_source: str = "guess",
        offer_id: str = "",
        raw_name: str = "",
    ) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.tx() as conn:
            conn.execute(
                """
                INSERT INTO tasks (day, key, name, category, points_max, points_earned,
                                   complete, link, manual, title_source, offer_id, raw_name, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(day, key) DO UPDATE SET
                    name=excluded.name,
                    category=excluded.category,
                    points_max=excluded.points_max,
                    points_earned=excluded.points_earned,
                    complete=excluded.complete,
                    link=COALESCE(NULLIF(excluded.link, ''), tasks.link),
                    manual=excluded.manual,
                    title_source=excluded.title_source,
                    offer_id=excluded.offer_id,
                    raw_name=excluded.raw_name,
                    updated_at=excluded.updated_at
                """,
                (
                    day,
                    key,
                    name,
                    category,
                    int(points_max),
                    int(points_earned),
                    1 if complete else 0,
                    link or "",
                    1 if manual else 0,
                    title_source,
                    offer_id or "",
                    raw_name or "",
                    now,
                ),
            )

    def upsert_tasks(self, day: str, rows) -> int:
        """一次事务批量写入当日任务；与 ``upsert_task`` 的 UPSERT 语义完全一致。"""
        now = datetime.now().isoformat(timespec="seconds")
        payload = [(
            day,
            str(r["key"]),
            str(r["name"]),
            str(r["category"]),
            int(r["points_max"]),
            int(r["points_earned"]),
            1 if r["complete"] else 0,
            r.get("link") or "",
            1 if r.get("manual", False) else 0,
            r.get("title_source", "guess"),
            r.get("offer_id") or "",
            r.get("raw_name") or "",
            now,
        ) for r in rows]
        if not payload:
            return 0
        with self.tx() as conn:
            conn.executemany(
                """
                INSERT INTO tasks (day, key, name, category, points_max, points_earned,
                                   complete, link, manual, title_source, offer_id, raw_name, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(day, key) DO UPDATE SET
                    name=excluded.name,
                    category=excluded.category,
                    points_max=excluded.points_max,
                    points_earned=excluded.points_earned,
                    complete=excluded.complete,
                    link=COALESCE(NULLIF(excluded.link, ''), tasks.link),
                    manual=excluded.manual,
                    title_source=excluded.title_source,
                    offer_id=excluded.offer_id,
                    raw_name=excluded.raw_name,
                    updated_at=excluded.updated_at
                """, payload)
        return len(payload)

    def remove_missing_official_tasks(self, day: str, current_keys) -> int:
        """同步后移除本日已不在官方接口内的旧任务，防止误判未完成。"""
        keys = set(current_keys)
        if not keys:
            # 空接口可能是网络异常/地区响应，不贸然清空缓存。
            return 0
        with self.tx() as conn:
            stale = [r["key"] for r in conn.execute(
                "SELECT key FROM tasks WHERE day=? AND manual=0", (day,)) if r["key"] not in keys]
            if stale:
                conn.executemany("DELETE FROM tasks WHERE day=? AND key=?",
                                 [(day, key) for key in stale])
        return len(stale)

    def set_task_complete(self, day: str, key: str, complete: bool) -> None:
        """手动勾选/取消勾选：同步更新完成状态与已得积分。"""
        now = datetime.now().isoformat(timespec="seconds")
        with self.tx() as conn:
            row = conn.execute(
                "SELECT points_max, points_earned, manual FROM tasks WHERE day=? AND key=?",
                (day, key),
            ).fetchone()
            if row is None:
                return
            earned = row["points_max"] if complete else 0
            # 官方数据同步过来的任务，其积分以官方进度为准，手动勾选只改状态
            if not row["manual"] and row["points_earned"] > 0 and complete:
                earned = row["points_earned"]
            conn.execute(
                "UPDATE tasks SET complete=?, points_earned=?, updated_at=? WHERE day=? AND key=?",
                (1 if complete else 0, int(earned), now, day, key),
            )

    def delete_task(self, day: str, key: str) -> None:
        with self.tx() as conn:
            conn.execute("DELETE FROM tasks WHERE day=? AND key=?", (day, key))

    def tasks_of_day(self, day: str) -> List[sqlite3.Row]:
        conn = self._connect()
        return list(
            conn.execute(
                "SELECT * FROM tasks WHERE day=? "
                "ORDER BY CASE category WHEN '搜索' THEN 0 WHEN '每日任务' THEN 1 "
                "WHEN '更多活动' THEN 2 ELSE 3 END, id",
                (day,),
            )
        )

    # ------------------------------------------------------------------ 汇总

    def summarize_day(self, day: str) -> dict:
        """唯一积分算法：账户今日余额减昨日余额；缺快照则用任务积分和。"""
        yesterday = (datetime.strptime(day, "%Y-%m-%d").date()
                     - timedelta(days=1)).isoformat()
        conn = self._connect()
        rows = list(conn.execute("SELECT * FROM tasks WHERE day=?", (day,)))
        current = conn.execute(
            "SELECT available_points, available_synced_at, level, "
            "today_points, claimable_points, page_points_synced_at "
            "FROM daily WHERE day=?",
            (day,),
        ).fetchone()
        previous = conn.execute(
            "SELECT available_points, available_synced_at FROM daily WHERE day=?",
            (yesterday,),
        ).fetchone()
        totals = conn.execute(
            "SELECT SUM(points_earned) e, SUM(points_max) m FROM tasks WHERE day=?",
            (day,),
        ).fetchone()

        task_earned = int(totals["e"] or 0)
        today_balance = (int(current["available_points"])
                         if current is not None and current["available_synced_at"] is not None
                         else None)
        yesterday_balance = (int(previous["available_points"])
                             if previous is not None and previous["available_synced_at"] is not None
                             else None)
        # 页面上真正显示的「今日积分」优先；读不到才退回余额日差 / 任务合计
        page_today = (int(current["today_points"])
                      if current is not None and current["today_points"] is not None
                      else None)
        earned, source, baseline = calculate_daily_points(
            today_balance, yesterday_balance, task_earned, page_today)
        done = sum(1 for r in rows if r["complete"])
        return {
            "day": day,
            "points_earned": earned,
            "points_source": source,
            "baseline_points": baseline,
            "available_synced_at": (current["available_synced_at"] if current else None),
            # 原样带回，避免 refresh_day / save_daily 把已读到的真值抹成 NULL
            "today_points": page_today,
            "claimable_points": (int(current["claimable_points"])
                                 if current is not None and current["claimable_points"] is not None
                                 else None),
            "page_points_synced_at": (current["page_points_synced_at"] if current else None),
            "points_max": int(totals["m"] or 0),
            "tasks_done": done,
            "tasks_total": len(rows),
            "all_done": all_reward_tasks_done(rows),
            "available_points": int(current["available_points"]) if current else 0,
            "level": int(current["level"]) if current else 0,
            "synced_at": datetime.now().isoformat(timespec="seconds"),
        }

    def save_daily(self, summary: dict) -> None:
        summary = dict(summary)
        summary["streak"] = 0  # 本轮保存后统一批量重算，避免逐日重复查询
        with self.tx() as conn:
            conn.execute(
                """
                INSERT INTO daily (day, points_earned, points_max, tasks_done, tasks_total,
                                   available_points, level, streak, all_done, synced_at,
                                   available_synced_at, baseline_points, points_source,
                                   today_points, claimable_points, page_points_synced_at)
                VALUES (:day,:points_earned,:points_max,:tasks_done,:tasks_total,
                        :available_points,:level,:streak,:all_done,:synced_at,
                        :available_synced_at,:baseline_points,:points_source,
                        :today_points,:claimable_points,:page_points_synced_at)
                ON CONFLICT(day) DO UPDATE SET
                    today_points=excluded.today_points,
                    claimable_points=excluded.claimable_points,
                    page_points_synced_at=excluded.page_points_synced_at,
                    points_earned=excluded.points_earned,
                    points_max=excluded.points_max,
                    baseline_points=excluded.baseline_points,
                    points_source=excluded.points_source,
                    tasks_done=excluded.tasks_done,
                    tasks_total=excluded.tasks_total,
                    available_points=excluded.available_points,
                    available_synced_at=excluded.available_synced_at,
                    level=excluded.level,
                    all_done=excluded.all_done,
                    synced_at=excluded.synced_at,
                    official_points_earned=NULL
                """,
                {**summary, "all_done": 1 if summary["all_done"] else 0},
            )

    def refresh_day(self, day: str) -> dict:
        summary = self.summarize_day(day)
        self.save_daily(summary)
        # 若补录昨日余额，次日差值也应同步更新。
        following = (datetime.strptime(day, "%Y-%m-%d").date()
                     + timedelta(days=1)).isoformat()
        if self.daily(following) is not None:
            self.save_daily(self.summarize_day(following))
        self.recompute_streaks_from(day)
        return self.daily(day) or summary

    def daily(self, day: str) -> Optional[sqlite3.Row]:
        conn = self._connect()
        return conn.execute("SELECT * FROM daily WHERE day=?", (day,)).fetchone()

    def recent_days(self, limit: int = 30) -> List[sqlite3.Row]:
        conn = self._connect()
        return list(
            conn.execute("SELECT * FROM daily ORDER BY day DESC LIMIT ?", (limit,))
        )

    def all_days(self) -> List[sqlite3.Row]:
        conn = self._connect()
        return list(conn.execute("SELECT * FROM daily ORDER BY day ASC"))

    # -------------------------------------------------------------- 连续天数

    def recompute_streaks_from(self, day: str) -> None:
        """固定规则：当日积分净变化或任务回退积分大于零，算一天。"""
        start = datetime.strptime(day, "%Y-%m-%d").date()
        rows = self.all_days()
        updates = []
        previous = None
        streak = 0
        for row in rows:
            current = datetime.strptime(row["day"], "%Y-%m-%d").date()
            if previous is None or (current - previous).days != 1:
                streak = 0
            passed = int(row["points_earned"] or 0) > 0
            streak = streak + 1 if passed else 0
            if current >= start:
                updates.append((streak, row["day"]))
            previous = current
        if updates:
            with self.tx() as conn:
                conn.executemany("UPDATE daily SET streak=? WHERE day=?", updates)

    def best_streak(self, rows=None) -> int:
        best = current = 0
        previous = None
        for row in rows if rows is not None else self.all_days():
            day = datetime.strptime(row["day"], "%Y-%m-%d").date()
            if previous is None or (day - previous).days != 1:
                current = 0
            passed = int(row["points_earned"] or 0) > 0
            current = current + 1 if passed else 0
            best = max(best, current)
            previous = day
        return best

    # ------------------------------------------------------------------ 统计

    def stats(self) -> dict:
        with self._connect() as conn:
            total = conn.execute("SELECT SUM(points_earned) e, COUNT(*) c FROM daily").fetchone()
            month = date.today().strftime("%Y-%m")
            m = conn.execute(
                "SELECT SUM(points_earned) e FROM daily WHERE substr(day,1,7)=?",
                (month,),
            ).fetchone()
            sources = conn.execute(
                "SELECT SUM(CASE WHEN points_source=? THEN 1 ELSE 0 END) n "
                "FROM daily WHERE substr(day,1,7)=?",
                (TASK_FALLBACK, month),
            ).fetchone()
        all_days = self.all_days()
        today = self.daily(date.today().isoformat())
        return {
            "total_points": int(total["e"] or 0),
            "tracked_days": int(total["c"] or 0),
            "month_points": int(m["e"] or 0),
            "month_fallback_days": int(sources["n"] or 0),
            "current_streak": int(today["streak"]) if today else 0,
            "best_streak": self.best_streak(all_days),
            "full_days": sum(1 for r in all_days if r["all_done"]),
        }

    def save_official_streak(self, day: str, streak: Optional[int]) -> None:
        """记录官方页面显示的连续天数（可能为 None）。"""
        if streak is None:
            return
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO daily (day, official_streak) VALUES (?,?) "
                "ON CONFLICT(day) DO UPDATE SET official_streak=excluded.official_streak",
                (day, int(streak)),
            )

    def prune_zero_point_tasks(self, keep_search: bool = True) -> int:
        """删掉不给积分的同步任务，返回删除条数。

        搜索类默认保留（部分地区接口分早晚两次下发，早上抓到 0 不代表今天没有）。
        """
        with self._connect() as conn:
            rows = list(conn.execute(
                "SELECT day, key, category, points_max, points_earned, manual FROM tasks"))
        doomed = []
        for r in rows:
            if keep_search and (r["category"] == "搜索"
                               or r["key"] in ("pc_search", "mobile_search")):
                continue
            if int(r["points_max"]) <= 0 and int(r["points_earned"]) <= 0:
                doomed.append((r["day"], r["key"]))
        if doomed:
            with self.tx() as conn:
                conn.executemany("DELETE FROM tasks WHERE day=? AND key=?", doomed)
        return len(doomed)

    def retranslate_names(self) -> int:
        """把历史库里的内部活动 ID 重译成中文名，返回改写条数。

        升级到带翻译的版本后，老记录不会自动变中文，这里补一次。
        已经是可读中文的会被 humanize 原样返回，不会二次破坏。
        """
        with self._connect() as conn:
            rows = list(conn.execute(
                "SELECT day, key, name, category, manual FROM tasks"))
        changed = 0
        with self.tx() as conn:
            for r in rows:
                idx = 0
                if ":" in r["key"]:
                    tail = r["key"].rsplit(":", 1)[-1]
                    idx = int(tail) if tail.isdigit() else 0
                new = humanize_task(r["key"], r["name"] or "", r["category"], idx)
                if new and new != r["name"]:
                    conn.execute(
                        "UPDATE tasks SET name=? WHERE day=? AND key=?",
                        (new, r["day"], r["key"]))
                    changed += 1
        return changed

    def clear_all(self) -> None:
        """清空全部历史（保留表结构）。"""
        with self.tx() as conn:
            conn.execute("DELETE FROM tasks")
            conn.execute("DELETE FROM daily")
            conn.execute("DELETE FROM used_terms")

    # ---------------------------------------------------------- 搜索词去重

    def used_terms(self, day: str) -> List[str]:
        with self._connect() as conn:
            return [r["term"] for r in conn.execute(
                "SELECT term FROM used_terms WHERE day=? ORDER BY used_at", (day,))]

    def mark_term_used(self, day: str, term: str) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.tx() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO used_terms (day, term, used_at) VALUES (?,?,?)",
                (day, term, now),
            )

    def clear_terms(self, day: str) -> None:
        with self.tx() as conn:
            conn.execute("DELETE FROM used_terms WHERE day=?", (day,))

    def latest_available(self) -> tuple:
        """最近一次同步到的账户可用积分与等级。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT available_points, level, official_streak FROM daily "
                "WHERE available_synced_at IS NOT NULL ORDER BY day DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return (0, 0, None)
        return (int(row["available_points"]), int(row["level"]),
                row["official_streak"] if row["official_streak"] else None)

    def upsert_available(self, day: str, available_points: int, level: int) -> None:
        """只有拿到 API 有效余额才调用；保留 0 元余额并标记快照有效。"""
        if isinstance(available_points, bool) or int(available_points) < 0:
            raise ValueError("账户余额无效")
        now = datetime.now().isoformat(timespec="seconds")
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO daily (day, available_points, level, available_synced_at) "
                "VALUES (?,?,?,?) ON CONFLICT(day) DO UPDATE SET "
                "available_points=excluded.available_points, level=excluded.level, "
                "available_synced_at=excluded.available_synced_at",
                (day, int(available_points), int(level), now),
            )

    # ------------------------------------------------------ 页面三项积分快照

    def save_page_points(self, day: str, points: dict) -> bool:
        """只写本次真正读到的「今日 / 可领取」；读不到的列保留上次值，不写 0。"""
        now = datetime.now().isoformat(timespec="seconds")
        updates = {}
        for col, key in (("today_points", "today"), ("claimable_points", "claimable")):
            value = (points or {}).get(key)
            if value is None or isinstance(value, bool) or int(value) < 0:
                continue
            updates[col] = int(value)
        if not updates:
            return False
        updates["page_points_synced_at"] = now
        cols = ", ".join(("day", *updates.keys()))
        marks = ", ".join("?" for _ in range(len(updates) + 1))
        assigns = ", ".join(f"{k}=excluded.{k}" for k in updates)
        with self.tx() as conn:
            conn.execute(
                f"INSERT INTO daily ({cols}) VALUES ({marks}) "
                f"ON CONFLICT(day) DO UPDATE SET {assigns}",
                (day, *updates.values()),
            )
        return True

    def page_points(self, day: str) -> dict:
        """供 UI 显示三项积分；None 一律显示「--」，绝不显示假的 0。"""
        row = self.daily(day)
        if row is None:
            return {"available": None, "today": None, "claimable": None}
        available = (int(row["available_points"])
                     if row["available_synced_at"] is not None else None)
        return {
            "available": available,
            "today": (int(row["today_points"])
                      if row["today_points"] is not None else None),
            "claimable": (int(row["claimable_points"])
                          if row["claimable_points"] is not None else None),
        }


def day_str(d: Optional[date] = None) -> str:
    return (d or date.today()).isoformat()

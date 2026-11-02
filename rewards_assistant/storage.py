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
from typing import Iterable, List, Optional

from .config import db_path
from .task_labels import humanize_task
from .pending import all_reward_tasks_done

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
    official_points_earned INTEGER,
    points_source TEXT NOT NULL DEFAULT 'estimated',
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

    def __init__(self, path: Optional[str] = None, streak_rule: str = "any_points") -> None:
        self.streak_rule = streak_rule if streak_rule in ("all_done", "any_points") else "any_points"
        self.path = str(path or db_path())
        self._local = threading.local()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
        self._migrate()
        self._ensure_columns()

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
                conn.execute("ALTER TABLE daily ADD COLUMN points_source TEXT NOT NULL DEFAULT 'estimated'")
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

    def _connect(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    @contextmanager
    def tx(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

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
        with self._connect() as conn:
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
        """依据任务明细重算当日汇总，并写回 daily 表。"""
        with self._connect() as conn:
            rows = list(conn.execute("SELECT * FROM tasks WHERE day=?", (day,)))
            prev = conn.execute(
                "SELECT available_points, level, official_points_earned FROM daily WHERE day=?", (day,)
            ).fetchone()
            totals = conn.execute(
                "SELECT SUM(points_earned) e, SUM(points_max) m FROM tasks WHERE day=?", (day,)
            ).fetchone()

        task_earned = int(totals["e"] or 0)
        # 本日已核验的数字不可被下一次 API 任务求和覆盖。
        official = prev["official_points_earned"] if prev else None
        earned = int(official) if official is not None else task_earned
        points_max = int(totals["m"] or 0)
        done = sum(1 for r in rows if r["complete"])
        total = len(rows)
        summary = {
            "day": day,
            "points_earned": earned,
            "official_points_earned": official,
            "points_source": "earn" if official is not None else "estimated",
            "points_max": points_max,
            "tasks_done": done,
            "tasks_total": total,
            "all_done": all_reward_tasks_done(rows),
            "available_points": int(prev["available_points"]) if prev else 0,
            "level": int(prev["level"]) if prev else 0,
            "synced_at": datetime.now().isoformat(timespec="seconds"),
        }
        return summary

    def save_daily(self, summary: dict) -> None:
        summary = dict(summary)
        summary["streak"] = self.compute_streak(summary["day"])
        with self.tx() as conn:
            conn.execute(
                """
                INSERT INTO daily (day, points_earned, points_max, tasks_done, tasks_total,
                                   available_points, level, streak, all_done, synced_at,
                                   official_points_earned, points_source)
                VALUES (:day,:points_earned,:points_max,:tasks_done,:tasks_total,
                        :available_points,:level,:streak,:all_done,:synced_at,
                        :official_points_earned,:points_source)
                ON CONFLICT(day) DO UPDATE SET
                    points_earned=excluded.points_earned,
                    points_max=excluded.points_max,
                    official_points_earned=excluded.official_points_earned,
                    points_source=excluded.points_source,
                    tasks_done=excluded.tasks_done,
                    tasks_total=excluded.tasks_total,
                    available_points=excluded.available_points,
                    level=excluded.level,
                    streak=excluded.streak,
                    all_done=excluded.all_done,
                    synced_at=excluded.synced_at
                """,
                {**summary, "all_done": 1 if summary["all_done"] else 0},
            )

    def refresh_day(self, day: str, official_points: Optional[int] = None) -> dict:
        s = self.summarize_day(day)
        if official_points is not None:
            if isinstance(official_points, bool) or int(official_points) < 0:
                raise ValueError("官方今日积分无效")
            s["official_points_earned"] = int(official_points)
            s["points_earned"] = int(official_points)
            s["points_source"] = "earn"
        self.save_daily(s)
        # 连续天数会随当天完成情况变化，次日记录需要重算
        self.recompute_streaks_from(day)
        return self.daily(day) or s

    def daily(self, day: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM daily WHERE day=?", (day,)).fetchone()

    def recent_days(self, limit: int = 30) -> List[sqlite3.Row]:
        with self._connect() as conn:
            return list(
                conn.execute("SELECT * FROM daily ORDER BY day DESC LIMIT ?", (limit,))
            )

    def all_days(self) -> List[sqlite3.Row]:
        with self._connect() as conn:
            return list(conn.execute("SELECT * FROM daily ORDER BY day ASC"))

    def set_streak_rule(self, rule: str) -> None:
        """设置项即时生效，重新计算已保存历史中的连续天数。"""
        if rule not in ("all_done", "any_points"):
            raise ValueError("不支持的连续天数规则")
        self.streak_rule = rule
        days = self.all_days()
        if days:
            self.recompute_streaks_from(days[0]["day"], rule)

    # -------------------------------------------------------------- 连续天数

    def compute_streak(self, day: str, rule: Optional[str] = None) -> int:
        """从 day 往前追溯，返回连续达标天数。"""
        rule = rule or self.streak_rule
        d = datetime.strptime(day, "%Y-%m-%d").date()
        streak = 0
        for _ in range(3650):
            row = self.daily(d.isoformat())
            if row is None:
                break
            ok = (
                bool(row["all_done"])
                if rule == "all_done"
                else int(row["points_earned"]) > 0
            )
            if not ok:
                break
            streak += 1
            d -= timedelta(days=1)
        return streak

    def recompute_streaks_from(self, day: str, rule: Optional[str] = None) -> None:
        """一次遍历重算连续天数，避免按天执行上百次 SQL 查询。"""
        rule = rule or self.streak_rule
        start = datetime.strptime(day, "%Y-%m-%d").date()
        rows = self.all_days()
        updates = []
        previous = None
        streak = 0
        for row in rows:
            current = datetime.strptime(row["day"], "%Y-%m-%d").date()
            if previous is None or (current - previous).days != 1:
                streak = 0
            passed = (bool(row["all_done"]) if rule == "all_done"
                      else int(row["points_earned"] or 0) > 0)
            streak = streak + 1 if passed else 0
            if current >= start:
                updates.append((streak, row["day"]))
            previous = current
        if updates:
            with self.tx() as conn:
                conn.executemany("UPDATE daily SET streak=? WHERE day=?", updates)

    def best_streak(self) -> int:
        best = current = 0
        previous = None
        for row in self.all_days():
            day = datetime.strptime(row["day"], "%Y-%m-%d").date()
            if previous is None or (day - previous).days != 1:
                current = 0
            passed = (bool(row["all_done"]) if self.streak_rule == "all_done"
                      else int(row["points_earned"] or 0) > 0)
            current = current + 1 if passed else 0
            best = max(best, current)
            previous = day
        return best

    # ------------------------------------------------------------------ 统计

    def stats(self) -> dict:
        with self._connect() as conn:
            total = conn.execute(
                "SELECT SUM(CASE WHEN points_source='earn' THEN points_earned ELSE 0 END) e, "
                "COUNT(*) c FROM daily"
            ).fetchone()
            month = date.today().strftime("%Y-%m")
            m = conn.execute(
                "SELECT SUM(points_earned) e FROM daily "
                "WHERE substr(day,1,7)=? AND points_source='earn'", (month,)
            ).fetchone()
        all_days = self.all_days()
        today = self.daily(date.today().isoformat())
        return {
            "total_points": int(total["e"] or 0),
            "tracked_days": int(total["c"] or 0),
            "month_points": int(m["e"] or 0),
            "current_streak": int(today["streak"]) if today else 0,
            "best_streak": self.best_streak(),
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
                "WHERE available_points > 0 ORDER BY day DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return (0, 0, None)
        return (int(row["available_points"]), int(row["level"]),
                row["official_streak"] if row["official_streak"] else None)

    def upsert_available(self, day: str, available_points: int, level: int) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO daily (day, available_points, level) VALUES (?,?,?) "
                "ON CONFLICT(day) DO UPDATE SET available_points=excluded.available_points, "
                "level=excluded.level",
                (day, int(available_points), int(level)),
            )


def day_str(d: Optional[date] = None) -> str:
    return (d or date.today()).isoformat()

"""Microsoft Rewards 数据抓取与解析。

数据全部来自微软自己的 dashboard 接口（rewards.bing.com 页面本身就在调用它），
本模块只做**只读**拉取：不模拟搜索、不点击任何任务、不代用户赚分。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from .task_labels import _CJK_RE, humanize_task


@dataclass
class TaskItem:
    key: str
    name: str
    category: str
    points_max: int = 0
    points_earned: int = 0
    complete: bool = False
    link: str = ""          # 任务落地页（用于一键在真实浏览器里打开）
    raw_name: str = ""      # 接口返回的原始 title/name（可能是内部 ID）
    offer_id: str = ""      # 活动 ID，用于与页面卡片精确匹配
    title_source: str = "guess"  # api=接口自带可读标题 / page=页面回填 / guess=兜底


@dataclass
class Snapshot:
    """一次同步拿到的完整状态。"""

    day: str
    level: int = 0
    available_points: int = 0
    pc_search: Tuple[int, int] = (0, 0)
    mobile_search: Tuple[int, int] = (0, 0)
    tasks: List[TaskItem] = field(default_factory=list)
    streak: Optional[int] = None  # 页面上的官方连续天数（可能抓不到）

    @property
    def tasks_done(self) -> int:
        return sum(1 for t in self.tasks if t.complete)

    @property
    def points_earned(self) -> int:
        return sum(t.points_earned for t in self.tasks)

    @property
    def points_max(self) -> int:
        return sum(t.points_max for t in self.tasks)

    @property
    def all_done(self) -> bool:
        return bool(self.tasks) and self.tasks_done == len(self.tasks)


def _level_to_int(raw: Any) -> int:
    """activeLevel 形如 'Level2' / 'newLevel2' / 2。"""
    if raw is None:
        return 0
    if isinstance(raw, int):
        return raw
    m = re.search(r"(\d+)", str(raw))
    return int(m.group(1)) if m else 0


def _counter(counters: Dict[str, Any], name: str) -> Tuple[int, int]:
    """counters.pcSearch 是数组，累加 progress / max。"""
    cur = mx = 0
    items = counters.get(name) or []
    if isinstance(items, dict):
        items = [items]
    for it in items:
        if not isinstance(it, dict):
            continue
        cur += int(it.get("pointProgress") or it.get("progress") or 0)
        mx += int(it.get("pointProgressMax") or it.get("pointMax") or 0)
    return cur, mx


def _promo_points(p: Dict[str, Any]) -> int:
    for k in ("pointProgressMax", "pointMax", "points"):
        v = p.get(k)
        if v:
            return int(v)
    # 部分活动把分值拆在 attributes 里
    offers = p.get("attributes") or {}
    if isinstance(offers, dict):
        v = offers.get("point_progress_max") or offers.get("pointmax")
        if v:
            return int(v)
    return 0


def _search_link(counters: Dict[str, Any], name: str) -> str:
    """搜索任务给一个普通的 Bing 首页，不代填查询词。"""
    items = counters.get(name) or []
    if isinstance(items, dict):
        items = [items]
    for it in items:
        if isinstance(it, dict):
            for k in ("destination", "linkUrl", "url"):
                v = it.get(k)
                if isinstance(v, str) and v.startswith("http"):
                    return v
    return "https://www.bing.com/"


# 本地化标题字段：微软在不同版本里用的 key 不一样，按"最像给人看的"排前面
TITLE_KEYS = (
    "title", "promotionTitle", "displayName", "cardTitle", "offerTitle",
    "name", "activityTitle", "description",
)


def _looks_internal_id(text: str) -> bool:
    """内部活动 ID：无中文、含下划线或驼峰、或纯英文长串。"""
    if not text:
        return True
    if _CJK_RE.search(text):
        return False
    if "_" in text:
        return True
    # 全英文且很长（如 GamificationDailySetZHCN）
    return len(text) > 18 and text.isalnum()


def _readable(text: str) -> bool:
    """这段文本是不是"页面上会给用户看的人话"。"""
    if not text:
        return False
    if _CJK_RE.search(text):
        return True
    # 纯英文短语（含空格、非全大写）也当可读，如 "Read to earn"
    return " " in text.strip() and not text.isupper()


def _promo_title(p: Dict[str, Any]) -> str:
    """取页面上真实显示的名字。

    关键：接口里常常**同时**有
        name  = "Gamification_DailySet_ZHCN_26101012"   ← 内部 ID
        title = "必应首页测验"                            ← 页面上显示的名字
    以前优先取 name，结果显示的永远是 ID 翻译出来的猜测名，所以一直不对。
    现在反过来：先找含中文/像人话的字段，找不到才退回 name。
    """
    candidates: list = []
    for k in TITLE_KEYS:
        v = p.get(k)
        if isinstance(v, str) and v.strip():
            candidates.append(v.strip())
    # attributes / promotion 里也可能藏着标题
    for holder_key in ("attributes", "promotion", "details"):
        holder = p.get(holder_key)
        if not isinstance(holder, dict):
            continue
        for k in TITLE_KEYS:
            v = holder.get(k)
            if isinstance(v, str) and v.strip():
                candidates.append(v.strip())

    for c in candidates:
        if not _looks_internal_id(c):
            return c
    return candidates[0] if candidates else ""


def _promo_offer_id(p: Dict[str, Any]) -> str:
    """活动 ID，用于把页面上的真实标题精确匹配回来。"""
    for k in ("offerId", "offerid", "offer_id", "id", "promotionId", "activityId"):
        v = p.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    attrs = p.get("attributes")
    if isinstance(attrs, dict):
        for k in ("offerId", "offerid", "offer_id", "id"):
            v = attrs.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _promo_link(p: Dict[str, Any]) -> str:
    """提取活动落地页。不同版本的字段位置不一样，逐个试。"""
    for k in ("destination", "destinationUrl", "linkUrl", "url", "link"):
        v = p.get(k)
        if isinstance(v, str) and v.startswith("http"):
            return v
    attrs = p.get("attributes")
    if isinstance(attrs, dict):
        for k in ("destination", "destinationUrl", "linkUrl", "url", "link"):
            v = attrs.get(k)
            if isinstance(v, str) and v.startswith("http"):
                return v
    return ""


def _promo_earned(p: Dict[str, Any]) -> int:
    if p.get("complete"):
        return _promo_points(p)
    for k in ("pointProgress", "progress"):
        v = p.get(k)
        if v:
            return int(v)
    return 0


def parse_dashboard(data: Dict[str, Any], today: Optional[str] = None) -> Snapshot:
    """把 getuserinfo 返回的 dashboard 解析成 Snapshot。

    接口结构在不同地区/版本略有差异，这里对每种字段都做了容错，
    拿不到的部分退化为 0，绝不让一次解析失败导致同步整体失败。
    """
    today = today or date.today().isoformat()
    dash = data.get("dashboard") if isinstance(data, dict) else None
    dash = dash if isinstance(dash, dict) else data if isinstance(data, dict) else {}

    user = dash.get("userStatus") or {}
    counters = user.get("counters") or {}

    snap = Snapshot(
        day=today,
        level=_level_to_int((user.get("levelInfo") or {}).get("activeLevel")),
        available_points=int(user.get("availablePoints") or 0),
    )
    snap.pc_search = _counter(counters, "pcSearch")
    snap.mobile_search = _counter(counters, "mobileSearch")

    # ---- 搜索类任务
    if snap.pc_search[1] or snap.pc_search[0]:
        cur, mx = snap.pc_search
        snap.tasks.append(
            TaskItem("pc_search", "电脑端搜索", "搜索", mx, cur, mx > 0 and cur >= mx,
                     link=_search_link(counters, "pcSearch"))
        )
    if snap.mobile_search[1] or snap.mobile_search[0]:
        cur, mx = snap.mobile_search
        snap.tasks.append(
            TaskItem("mobile_search", "移动端搜索", "搜索", mx, cur, mx > 0 and cur >= mx,
                     link=_search_link(counters, "mobileSearch"))
        )

    # ---- 每日任务集（dailySetPromotions 以日期为 key）
    dsp = dash.get("dailySetPromotions")
    if isinstance(dsp, dict):
        items = dsp.get(today) or dsp.get(date.today().strftime("%m/%d/%Y")) or []
        # 某些版本用 "2026-10-12T00:00:00+00:00" 之类的 key
        if not items:
            for k, v in dsp.items():
                if isinstance(k, str) and today in k:
                    items = v
                    break
        for i, p in enumerate(items or []):
            if not isinstance(p, dict):
                continue
            raw_name = _promo_title(p)
            snap.tasks.append(
                TaskItem(
                    key=f"dailyset:{i}",
                    title_source="api" if _readable(raw_name) else "guess",
                    name=humanize_task(f"dailyset:{i}", raw_name, "每日任务", i, p),
                    category="每日任务",
                    points_max=_promo_points(p),
                    points_earned=_promo_earned(p),
                    complete=bool(p.get("complete")),
                    link=_promo_link(p),
                    raw_name=raw_name,
                    offer_id=_promo_offer_id(p),
                )
            )

    # ---- 更多活动
    for i, p in enumerate(dash.get("morePromotions") or []):
        if not isinstance(p, dict):
            continue
        status = str(p.get("exclusiveLockedFeatureStatus") or "")
        if status.lower() == "locked":
            continue  # 未解锁的活动不纳入统计
        raw_name = _promo_title(p)
        snap.tasks.append(
            TaskItem(
                key=f"more:{i}",
                title_source="api" if _readable(raw_name) else "guess",
                name=humanize_task(f"more:{i}", raw_name, "更多活动", i, p),
                category="更多活动",
                points_max=_promo_points(p),
                points_earned=_promo_earned(p),
                complete=bool(p.get("complete")),
                link=_promo_link(p),
                raw_name=raw_name,
                offer_id=_promo_offer_id(p),
            )
        )

    # ---- 过滤掉不给积分的任务（points_max 与 points_earned 都是 0）
    #     微软会下发一些纯展示/引导型活动，它们不产生积分，混在列表里只会干扰判断
    snap.tasks = [t for t in snap.tasks if keeps_task(t)]

    return snap


def keeps_task(task: "TaskItem") -> bool:
    """任务是否值得留在列表里：只保留能拿到积分的（自定义任务已移除）。

    搜索类任务即使当前上限为 0 也先保留（部分地区接口分早晚两次下发），
    其余活动只要上限和已得分都为 0，就认为它不给分。
    """
    if task.key in ("pc_search", "mobile_search"):
        return True
    return int(task.points_max) > 0 or int(task.points_earned) > 0


STREAK_PATTERNS = (
    r'"streakCount"\s*:\s*(\d+)',
    r'"currentStreak"\s*:\s*(\d+)',
    r'"streak"\s*:\s*\{\s*"[^"]+"\s*:\s*(\d+)',
)


def extract_streak(html: str) -> Optional[int]:
    """尽力从 dashboard 页面里提取官方连续天数，抓不到返回 None。"""
    for pat in STREAK_PATTERNS:
        m = re.search(pat, html or "")
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                continue
    return None

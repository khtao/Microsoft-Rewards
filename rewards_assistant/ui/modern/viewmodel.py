# -*- coding: utf-8 -*-
"""复刻界面的纯数据逻辑：不依赖 PySide6，可离线单测。

职责：把 ``Store`` 里的 sqlite3.Row 转成界面要的分组 / 进度 / 日历格子，
以及轻量界面文案字典。所有「不写假的 0 分」的口径都在这里统一。
"""
from __future__ import annotations

import calendar as _calendar
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence

# 数据库分类 → 截图上的分组名
CATEGORY_LABELS: Dict[str, str] = {
    "搜索": "搜索",
    "每日任务": "每日活动",
    "更多活动": "日常任务",
}
GROUP_ORDER: Sequence[str] = ("搜索", "每日活动", "日常任务")


def _get(row: Any, key: str, default=None):
    if row is None:
        return default
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return getattr(row, key, default)


def points_text(value: Optional[int]) -> str:
    """没读到就是 ``--``，绝不把缺失显示成 0。"""
    if value is None:
        return "--"
    return f"{int(value):,}"


def progress_of(rows: Iterable[Any]) -> dict:
    """今日任务进度：按任务卡的 points_earned / points_max 计算。"""
    rows = list(rows)
    earned = max(0, sum(int(_get(r, "points_earned", 0) or 0) for r in rows))
    total = max(0, sum(int(_get(r, "points_max", 0) or 0) for r in rows))
    done = sum(1 for r in rows if bool(_get(r, "complete", 0)))
    complete = bool(rows) and done == len(rows)
    return {
        "percent": round(earned / total * 100) if total else 0,
        "earned": earned, "total": total,
        "done": done, "count": len(rows),
        "complete": complete,
    }


def group_tasks(rows: Iterable[Any]) -> List[dict]:
    """按截图里的三组顺序整理任务卡；未知分类并入「日常任务」。"""
    buckets: Dict[str, List[dict]] = {g: [] for g in GROUP_ORDER}
    for r in rows:
        raw = str(_get(r, "category", "") or "")
        label = CATEGORY_LABELS.get(raw, "日常任务")
        buckets[label].append({
            "key": str(_get(r, "key", "") or ""),
            "name": str(_get(r, "name", "") or ""),
            "description": str(_get(r, "description", "") or ""),
            "points": int(_get(r, "points_max", 0) or 0),
            "earned": int(_get(r, "points_earned", 0) or 0),
            "complete": bool(_get(r, "complete", 0)),
            "link": str(_get(r, "link", "") or ""),
        })
    out: List[dict] = []
    for label in GROUP_ORDER:
        items = buckets[label]
        if not items:
            continue
        earned = sum(i["earned"] for i in items)
        total = sum(i["points"] for i in items)
        out.append({
            "label": label, "items": items,
            "earned": earned, "total": total,
            "percent": round(earned / total * 100) if total else 0,
        })
    return out


def month_total(rows: Iterable[Any], year: int, month: int) -> int:
    prefix = f"{year:04d}-{month:02d}"
    return sum(int(_get(r, "points_earned", 0) or 0)
               for r in rows if str(_get(r, "day", "") or "").startswith(prefix))


def _points_by_day(rows: Iterable[Any]) -> Dict[str, int]:
    """只保留**有积分**的日期；未来 / 过去没有积分的日子一律不进入日历。"""
    by: Dict[str, int] = {}
    for r in rows:
        day = str(_get(r, "day", "") or "")[:10]
        pts = int(_get(r, "points_earned", 0) or 0)
        if day and pts > 0:
            by[day] = by.get(day, 0) + pts
    return by


def _week_monday(d: date) -> date:
    from datetime import timedelta
    return d - timedelta(days=d.weekday())


def calendar_days(rows: Iterable[Any], year: int, month: int) -> List[dict]:
    """「日」视图：本月有积分的日子，一排七个，无记录的日子直接删除。"""
    by = _points_by_day(rows)
    prefix = f"{year:04d}-{month:02d}"
    cells = []
    for d in range(1, _calendar.monthrange(year, month)[1] + 1):
        key = f"{prefix}-{d:02d}"
        points = by.get(key, 0)
        if points <= 0:
            continue
        cells.append({
            "key": key, "label": f"{month}/{d}", "points": points,
            "has": True, "dots": [True], "unit": "day",
        })
    return cells


def calendar_week(rows: Iterable[Any], today: date) -> List[dict]:
    """「周」视图：本月涉及到的周，一排一周；只显示有积分的周。

    每个格子是一个圆角矩形三排卡片：``W3  11/9-11/15`` / 周合计 / 7 个圆点，
    圆点依次对应该周周一至周日是否有积分（与截图样式一致）。
    """
    from datetime import timedelta

    by = _points_by_day(rows)
    first = date(today.year, today.month, 1)
    last_day = _calendar.monthrange(today.year, today.month)[1]
    last = date(today.year, today.month, last_day)
    cursor = _week_monday(first)
    week_end_of_month = _week_monday(last) + timedelta(days=6)

    out: List[dict] = []
    number = 0
    while cursor <= week_end_of_month:
        number += 1
        end = cursor + timedelta(days=6)
        days = [cursor + timedelta(days=i) for i in range(7)]
        dots = [by.get(d.isoformat(), 0) > 0 for d in days]
        total = sum(by.get(d.isoformat(), 0) for d in days)
        if total > 0:
            out.append({
                "key": cursor.isoformat(),
                "label": f"W{number}  {cursor.month}/{cursor.day}-{end.month}/{end.day}",
                "points": total, "has": True, "dots": dots, "unit": "week",
            })
        cursor = end + timedelta(days=1)
    return out


def calendar_months(rows: Iterable[Any], today: date) -> List[dict]:
    """「月」视图：只显示有积分的月份，不再铺满最近 6 个月。"""
    from datetime import timedelta

    by = _points_by_day(rows)
    totals: Dict[str, int] = {}
    for day, pts in by.items():
        totals[day[:7]] = totals.get(day[:7], 0) + pts

    out: List[dict] = []
    for key in sorted(totals):
        y, m = int(key[:4]), int(key[5:7])
        first = date(y, m, 1)
        last = date(y, m, _calendar.monthrange(y, m)[1])
        cursor, dots = _week_monday(first), []
        while cursor <= last:
            end = cursor + timedelta(days=6)
            dots.append(any(by.get((cursor + timedelta(days=i)).isoformat(), 0) > 0
                            for i in range(7)))
            cursor = end + timedelta(days=1)
        out.append({
            "key": key, "label": f"{y}/{m:02d}", "points": totals[key],
            "has": True, "dots": dots, "unit": "month",
        })
    return out


def calendar_summary(cells: Sequence[dict], lang: str = "zh") -> dict:
    """汇总随视图口径变化：日=日均 / 周=周均 / 月=月均，最高与计数同步。"""
    earned = [int(c["points"]) for c in cells if c.get("has") and c.get("points")]
    active = len(earned)
    unit = cells[0].get("unit", "day") if cells else "day"
    zh = lang != "en"
    labels = {
        "day": ("日均", "最高", "天有积分", "Daily avg", "Max", "days with points"),
        "week": ("周均", "最高", "周有积分", "Weekly avg", "Max", "weeks with points"),
        "month": ("月均", "最高", "个月有积分", "Monthly avg", "Max", "months with points"),
    }
    a, b, c_, d, e, f = labels.get(unit, labels["day"])
    return {
        "avg": round(sum(earned) / active) if active else 0,
        "max": max(earned) if earned else 0,
        "active": active,
        "avg_label": a if zh else d,
        "max_label": b if zh else e,
        "unit_label": c_ if zh else f,
    }


# ---------------------------------------------------------------- 界面文案

I18N: Dict[str, Dict[str, str]] = {
    "zh": {
        "app_title": "拟人搜索助手", "settings": "设置",
        "settings_sub": "常用设置",
        "today_tasks": "今日任务", "all_done": "已完成", "pending": "待完成",
        "sync_desktop": "同步与桌面", "sync_interval": "自动刷新",
        "remind_group": "任务提醒", "background_group": "后台与通知",
        "tools": "工具", "sync_off": "关",
        "available": "可用积分", "today": "今日积分", "claimable": "可领取",
        "sync": "自动完成任务", "refresh": "刷新",
        "empty_tasks": "暂无任务记录，点击圆环同步一次。",
        "ring_refresh": "点击刷新",
        "stop_run": "停止自动运行", "stopping": "正在停止…",
        "sync_blocked": "自动运行期间禁止同步，运行结束后会自动同步一次",
        "appearance": "外观", "language": "语言 / Language",
        "theme": "主题",
        "runtime": "运行设置", "intensity": "拟人强度",
        "auto_sync": "自动同步", "off": "关", "on": "开",
        "schedule": "定时运行", "schedule_hint": "点 + 从 08:00 起每 30 分钟追加",
        "schedule_on": "定时运行", "search_cap": "搜索次数上限",
        "default_summary": "拟人搜索默认设置",
        "default_summary_hint": "强度可切换，其余按推荐值固定",
        "start_times": "启动时间", "calendar": "积分赚取日历",
        "logs": "记录运行日志", "desktop": "桌面版", "autostart": "开机自动启动",
        "reset": "恢复默认设置", "signed_in": "已登录", "signed_out": "未登录",
        "unknown": "未检测", "export_logs": "导出运行日志",
        "capture_html": "抓取关键网页（HTML zip）",
        "day": "日", "week": "周", "month": "月",
        "cal_month_title": "有积分的月份", "cal_empty": "暂无积分记录",
        "cal_month_total": "本月累计",
        "readonly_note": "进度与积分以官网为准",
        "status_syncing": "正在同步 Rewards 数据…", "status_ok": "同步完成",
        "status.ready": "就绪", "status.reading": "读取积分",
        "status.checking": "检查今日进度", "status.syncing": "正在同步状态",
        "status.searching": "搜索中", "status.cooldown": "Bing 冷却",
        "status.finalizing": "收尾中", "status.completed": "已完成",
        "status.limit": "已达上限", "status.error": "出错",
        "status.all_done": "已完成，无需动作", "status.synced": "已同步积分",
        "status.stopping": "正在停止", "status.completing": "正在完成任务",
    },
    "en": {
        "app_title": "Assistant", "settings": "Settings",
        "settings_sub": "Essentials",
        "today_tasks": "Today's tasks", "all_done": "All done", "pending": "Pending",
        "sync_desktop": "Sync & desktop", "sync_interval": "Auto refresh",
        "remind_group": "Task reminders", "background_group": "Background",
        "tools": "Tools", "sync_off": "Off",
        "available": "Available", "today": "Earned today", "claimable": "To claim",
        "sync": "Auto-complete tasks", "refresh": "Refresh",
        "empty_tasks": "No tasks yet. Click the ring to sync.",
        "ring_refresh": "Click to refresh",
        "stop_run": "Stop auto-run", "stopping": "Stopping…",
        "sync_blocked": "Sync is paused while auto-run is active; it will run afterwards",
        "appearance": "Appearance", "language": "Language",
        "theme": "Theme",
        "runtime": "Behavior", "intensity": "Intensity",
        "auto_sync": "Auto sync", "off": "Off", "on": "On",
        "schedule": "Timed runs", "schedule_hint": "+ adds 08:00, 08:30, 09:00…",
        "default_summary": "Human-like search defaults",
        "default_summary_hint": "Adjust intensity; others use recommended values",
        "schedule_on": "Timed auto-run", "search_cap": "Search cap",
        "start_times": "Start times", "calendar": "Points calendar",
        "logs": "Run log", "desktop": "Desktop", "autostart": "Launch at login",
        "reset": "Restore defaults", "signed_in": "Signed in", "signed_out": "Signed out",
        "unknown": "Not checked", "export_logs": "Export run log",
        "capture_html": "Capture pages (HTML zip)",
        "day": "Day", "week": "Week", "month": "Month",
        "cal_month_title": "Months with points", "cal_empty": "No points yet",
        "cal_month_total": "This month",
        "readonly_note": "Points follow the official site",
        "status_syncing": "Syncing Rewards data…", "status_ok": "Sync complete",
        "status.ready": "Ready", "status.reading": "Reading points",
        "status.checking": "Checking today's progress", "status.syncing": "Syncing status",
        "status.searching": "Searching", "status.cooldown": "Bing cooldown",
        "status.finalizing": "Wrapping up", "status.completed": "Completed",
        "status.limit": "Daily limit reached", "status.error": "Error",
        "status.all_done": "All tasks done — nothing to do", "status.synced": "Points synced",
        "status.stopping": "Stopping", "status.completing": "Completing tasks",
    },
}


def resolve_language(choice: str) -> str:
    """「跟随系统」按 locale 猜；测试环境可稳定回退到中文。"""
    if choice in ("中文", "zh", "Chinese"):
        return "zh"
    if choice in ("English", "en"):
        return "en"
    import locale

    lang, _ = locale.getdefaultlocale()
    code = str(lang or "").lower()
    if not code or code.startswith("zh"):
        return "zh"
    return "en" if code.startswith("en") else "zh"


def tr(lang: str, key: str) -> str:
    return I18N.get(lang, I18N["zh"]).get(key, I18N["zh"].get(key, key))


# ---------------------------------------------------------------- 设置绑定
#
# 界面控件键 → 经典控制器真正读取的 Settings 键（1.3.0 语义）。
# 只有带 ui_ 前缀且不在 CLASSIC_BINDING 里的才是纯界面偏好。

UI_DEFAULTS: Dict[str, Any] = {
    # 01 外观（纯界面偏好，真实生效）
    "ui_language": "跟随系统",
    "ui_theme": "跟随系统",
    "ui_calendar_view": "周",
    "ui_log_enabled": True,
    # 02 运行设置（全部映射到经典 Settings 键）
    "ui_search_level": "中",          # → search_panel.level 1/2/3
    "ui_auto_sync": True,             # → sync_interval_minutes 0 / ui_sync_interval
    "ui_repeat": 12,                  # → search_panel.repeat（自动完成任务默认 12 轮）
    "ui_sync_interval": 60,           # → sync_interval_value_minutes
    "ui_remind_enabled": True,        # → remind_time "" / ui_remind_time
    "ui_remind_time": "09:00",        # → remind_time_value
    "ui_evening_enabled": True,       # → evening_remind_time "" / ui_evening_time
    "ui_evening_time": "20:30",       # → evening_remind_time_value
    "ui_notify_after_sync": True,     # → notify_after_sync
    "ui_silent_all_done": True,       # → silent_when_all_done
    "ui_close_to_tray": True,         # → close_to_tray
    "ui_sleep_all_done": False,       # → sleep_when_all_done
    "ui_launch_at_login": False,      # → launch_at_login
    "ui_keywords": "",                # → search_panel.keywords
    "ui_sources": "baidu,weibo,toutiao,zhihu,bing,sina",  # → search_panel.keyword_sources
    "ui_chart_days": 30,              # → chart_days
    # 03 定时计划
    "ui_schedule_enabled": False,     # → auto_run_enabled
    "ui_schedule_times": ["09:00"],  # → auto_run_times
    "ui_search_cap": 12,             # → search_panel.repeat（与 ui_repeat 同源）
    # 统一管理组：一个控件同时写多个经典键（见 pref_to_settings）
    "ui_sync_mode": "60",            # 关 / 15 / 30 / 60 分钟
    "ui_remind_mode": True,          # 早晚提醒一起开关
    "ui_background_mode": True,      # 通知 / 静音 / 托盘一起开关
}

# 控件键 → Settings 键；值为 None 表示该键本身就是 Settings 键（纯界面偏好）。
CLASSIC_BINDING: Dict[str, Optional[str]] = {
    "ui_language": None, "ui_theme": None,
    "ui_calendar_view": None, "ui_log_enabled": None,
    "ui_search_level": "search_panel.level",
    "ui_auto_sync": "sync_interval_minutes",
    "ui_repeat": "search_panel.repeat",
    "ui_sync_interval": "sync_interval_value_minutes",
    "ui_remind_enabled": "remind_time",
    "ui_remind_time": "remind_time_value",
    "ui_evening_enabled": "evening_remind_time",
    "ui_evening_time": "evening_remind_time_value",
    "ui_notify_after_sync": "notify_after_sync",
    "ui_silent_all_done": "silent_when_all_done",
    "ui_close_to_tray": "close_to_tray",
    "ui_sleep_all_done": "sleep_when_all_done",
    "ui_launch_at_login": "launch_at_login",
    "ui_keywords": "search_panel.keywords",
    "ui_sources": "search_panel.keyword_sources",
    "ui_chart_days": "chart_days",
    "ui_schedule_enabled": "auto_run_enabled",
    "ui_schedule_times": "auto_run_times",
    "ui_search_cap": "search_panel.repeat",
    # 组键：值只作文档标注，实际写入多个键由 pref_to_settings 负责
    "ui_sync_mode": "sync_interval_minutes + sync_interval_value_minutes",
    "ui_remind_mode": "remind_time + evening_remind_time (+_value)",
    "ui_background_mode": "notify_after_sync + silent_when_all_done + close_to_tray",
}

LEVEL_TO_CHOICE = {1: "低", 2: "中", 3: "高"}
DEFAULT_SOURCES = ["baidu", "weibo", "toutiao", "zhihu", "bing", "sina"]


def _search_panel(settings) -> dict:
    sp = settings.get("search_panel", {}) or {}
    return dict(sp) if isinstance(sp, dict) else {}


def _safe_int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return fallback


def _time_value(value, fallback: str) -> str:
    text = str(value or "").strip()
    return text if len(text) == 5 and text[2] == ":" else fallback


def settings_values(settings) -> Dict[str, Any]:
    """把经典 Settings 的真实键映射成界面控件值（单一真源在 Settings）。"""
    sp = _search_panel(settings)
    try:
        level = int(sp.get("level", 2) or 2)
    except (TypeError, ValueError):
        level = 2
    sources = sp.get("keyword_sources", None)
    if isinstance(sources, (list, tuple)):
        sources_text = ",".join(str(x) for x in sources)
    else:
        sources_text = ",".join(DEFAULT_SOURCES)
    try:
        interval = int(settings.get("sync_interval_value_minutes", 60) or 60)
    except (TypeError, ValueError):
        interval = 60
    try:
        repeat = int(sp.get("repeat", 12) or 12)
    except (TypeError, ValueError):
        repeat = 12
    times = settings.get("auto_run_times", ["09:00"])
    out = dict(UI_DEFAULTS)
    out.update({
        "ui_language": settings.get("ui_language", UI_DEFAULTS["ui_language"]),
        "ui_theme": settings.get("ui_theme", UI_DEFAULTS["ui_theme"]),
        "ui_calendar_view": settings.get("ui_calendar_view", "周"),
        "ui_log_enabled": bool(settings.get("ui_log_enabled", True)),
        "ui_search_level": LEVEL_TO_CHOICE.get(level, "中"),
        "ui_intensity_params": _intensity_params_from_sp(sp, level),
        "ui_auto_sync": int(settings.get("sync_interval_minutes", 60) or 0) > 0,
        "ui_repeat": repeat,
        "ui_sync_interval": interval,
        "ui_remind_enabled": bool(settings.get("remind_time", "09:00")),
        "ui_remind_time": _time_value(settings.get("remind_time_value", "09:00"), "09:00"),
        "ui_evening_enabled": bool(settings.get("evening_remind_time", "20:30")),
        "ui_evening_time": _time_value(settings.get("evening_remind_time_value", "20:30"), "20:30"),
        "ui_notify_after_sync": bool(settings.get("notify_after_sync", True)),
        "ui_silent_all_done": bool(settings.get("silent_when_all_done", True)),
        "ui_close_to_tray": bool(settings.get("close_to_tray", True)),
        "ui_sleep_all_done": bool(settings.get("sleep_when_all_done", False)),
        "ui_launch_at_login": bool(settings.get("launch_at_login", False)),
        "ui_keywords": str(sp.get("keywords", "") or ""),
        "ui_sources": sources_text,
        "ui_chart_days": _safe_int(settings.get("chart_days", 30), 30),
        "ui_schedule_enabled": bool(settings.get("auto_run_enabled", False)),
        "ui_schedule_times": list(times) if isinstance(times, (list, tuple)) else ["09:00"],
        "ui_search_cap": repeat,
    })
    # ---- 统一管理组：从经典键反推一个控件值 ----
    minutes = _safe_int(settings.get("sync_interval_minutes", 60), 60)
    # 用语言无关的 "0" 表示关；Segmented 会自动回退到第一个「关 / Off」按钮
    out["ui_sync_mode"] = ("0" if minutes <= 0 else
                           str(minutes) if minutes in (15, 30, 60) else "60")
    out["ui_remind_mode"] = bool(settings.get("remind_time", "09:00")) or bool(
        settings.get("evening_remind_time", "20:30"))
    out["ui_background_mode"] = any(bool(settings.get(k, True)) for k in (
        "notify_after_sync", "silent_when_all_done", "close_to_tray"))
    return out


def pref_to_settings(prefs: dict, key: str, value: Any) -> dict:
    """界面控件的一次改动 → 经典 Settings 的写入补丁（纯函数，可离线单测）。

    所有会改变运行行为的项都落到 1.3.0 控制器真正读取的键上；
    ``Application.apply_settings(patch)`` 随后负责持久化与重排定时器。
    """
    p = dict(prefs)
    p[key] = value
    sp_keys = (*INTENSITY_PARAM_KEYS, "keywords", "keyword_sources")

    def search_patch(**kw) -> dict:
        sp = _search_panel_from_prefs(p)
        sp.update(kw)
        return {"search_panel": {k: sp.get(k) for k in sp_keys if sp.get(k) is not None}}

    if key in ("ui_language", "ui_theme",
               "ui_calendar_view", "ui_log_enabled"):
        return {key: value}
    if key == "ui_search_level":
        # 「统一管理」：一个控件一次写齐 5 项拟人参数（其余参数也随档位联动）。
        return search_patch(**intensity_profile(str(value)))
    if key in ("ui_repeat", "ui_search_cap"):
        n = int(value)
        return search_patch(repeat=max(1, min(200, n)))
    if key == "ui_keywords":
        return search_patch(keywords=str(value))
    if key == "ui_sources":
        names = [x.strip() for x in str(value).split(",") if x.strip()]
        return search_patch(keyword_sources=tuple(names) or tuple(DEFAULT_SOURCES))
    if key == "ui_auto_sync":
        minutes = int(p.get("ui_sync_interval", 60) or 60)
        return {"sync_interval_minutes": minutes if value else 0,
                "sync_interval_value_minutes": minutes}
    if key == "ui_sync_interval":
        minutes = max(0, int(value))
        return {"sync_interval_value_minutes": minutes,
                "sync_interval_minutes": minutes if p.get("ui_auto_sync", True) else 0}
    if key == "ui_remind_enabled":
        return {"remind_time": _time_value(p.get("ui_remind_time"), "09:00") if value else ""}
    if key == "ui_remind_time":
        t = _time_value(value, "09:00")
        return {"remind_time_value": t,
                "remind_time": t if p.get("ui_remind_enabled", True) else ""}
    if key == "ui_evening_enabled":
        return {"evening_remind_time": _time_value(p.get("ui_evening_time"), "20:30") if value else ""}
    if key == "ui_evening_time":
        t = _time_value(value, "20:30")
        return {"evening_remind_time_value": t,
                "evening_remind_time": t if p.get("ui_evening_enabled", True) else ""}
    if key == "ui_notify_after_sync":
        return {"notify_after_sync": bool(value)}
    if key == "ui_silent_all_done":
        return {"silent_when_all_done": bool(value)}
    if key == "ui_close_to_tray":
        return {"close_to_tray": bool(value)}
    if key == "ui_sleep_all_done":
        return {"sleep_when_all_done": bool(value)}
    if key == "ui_launch_at_login":
        return {"launch_at_login": bool(value)}
    if key == "ui_chart_days":
        return {"chart_days": max(1, min(365, int(value)))}
    if key == "ui_schedule_enabled":
        return {"auto_run_enabled": bool(value)}
    if key == "ui_schedule_times":
        return {"auto_run_times": list(value)}
    # ---- 统一管理：一次改动写齐一组经典键 ----
    if key == "ui_sync_mode":
        minutes = 0 if str(value) in ("关", "Off", "0", "") else _safe_int(value, 60)
        return {"sync_interval_minutes": minutes,
                "sync_interval_value_minutes": minutes or 60}
    if key == "ui_remind_mode":
        on = bool(value)
        return {
            "remind_time": "09:00" if on else "",
            "remind_time_value": "09:00",
            "evening_remind_time": "20:30" if on else "",
            "evening_remind_time_value": "20:30",
        }
    if key == "ui_background_mode":
        on = bool(value)
        # 休眠属于不推荐改动项，两档都保持关闭；其余三项统一管理。
        return {"notify_after_sync": on, "silent_when_all_done": on,
                "close_to_tray": on, "sleep_when_all_done": False}
    return {key: value}


def _search_panel_from_prefs(p: dict) -> dict:
    # 以当前档位为底，保证任何一次设置写入都不会把 5 项拟人参数冲掉。
    out = intensity_profile(str(p.get("ui_search_level", "中")))
    out["repeat"] = int(p.get("ui_repeat", REPEAT_DEFAULT) or REPEAT_DEFAULT)
    out["keywords"] = str(p.get("ui_keywords", "") or "")
    names = [x.strip() for x in str(p.get("ui_sources", "") or "").split(",") if x.strip()]
    out["keyword_sources"] = tuple(names) if names else tuple(DEFAULT_SOURCES)
    return out


REPEAT_DEFAULT = 12      # 「自动完成任务」默认搜索轮数
REPEAT12_MARKER = "ui_repeat_default_12"


def repeat_upgrade_patch(settings) -> dict:
    """旧配置的 5 轮一次性升级为 12 轮；用户自定义过的值不覆盖。

    ``SIMPLIFY_MARKER`` 已经写过的配置不会再走 ``defaults_patch()``，
    所以这里用独立标记补一次，且只在 repeat 恰为旧默认 5 时才改。
    """
    if settings.get(REPEAT12_MARKER):
        return {}
    sp = _search_panel(settings)
    repeat = _safe_int(sp.get("repeat", REPEAT_DEFAULT), REPEAT_DEFAULT)
    if repeat != 5:
        return {REPEAT12_MARKER: True}
    upgraded = dict(sp)
    upgraded["repeat"] = REPEAT_DEFAULT
    return {"search_panel": upgraded, REPEAT12_MARKER: True}


def defaults_patch(current_level: int = 2) -> dict:
    """被隐藏 / 被合并的设置项统一写成推荐默认，只写它们自己那批键。

    ``search_panel`` 必须一次性给全字段：``Settings.update`` 是整体替换
    这个 dict，逐个 patch 叠加会只剩最后一个字段。拟人强度档位的 5 项参数
    也一并写入，保证默认档位（中）就是真实执行值。
    """
    p = dict(UI_DEFAULTS)
    level = max(1, min(3, _safe_int(current_level, 2)))
    sp = intensity_profile(LEVEL_TO_CHOICE.get(level, "中"))
    sp.update(level=level, keywords="",
              keyword_sources=tuple(DEFAULT_SOURCES))
    patch: dict = {
        "search_panel": sp,
        "chart_days": 30,
        "ui_log_enabled": True,
    }
    for key in ("ui_sync_mode", "ui_remind_mode", "ui_background_mode"):
        patch.update(pref_to_settings(p, key, p[key]))
    return patch


# 只在首次启动 / 旧配置升级时写一次默认值，之后不再覆盖用户通过精简控件
# 做出的选择（否则每次启动都会把「自动同步 15 分钟」重置回 60）。
SIMPLIFY_MARKER = "ui_prefs_simplified_v2"


def reset_patch() -> dict:
    """恢复默认：隐藏项 + 可见项全部回到推荐值，不动历史、会话与迁移标记。"""
    p = dict(UI_DEFAULTS)
    p["ui_search_cap"] = p["ui_repeat"]
    p["ui_schedule_enabled"] = False
    p["ui_schedule_times"] = ["09:00"]
    patch = defaults_patch()
    for key in VISIBLE_SETTING_KEYS:
        patch.update(pref_to_settings(p, key, p[key]))
    return patch


# ---------------------------------------------------------------- 交互常量
#
# 与插件版同一套 ease-out 节奏，桌面端略微放大以便看得清。
# 动画恒定开启：不再有「减少动画」开关，也不再有鼠标悬浮提示浮层。
TRANSITION_MS = 260
STAGGER_STEP_MS = 85
RING_ANIM_MS = 760
BAR_ANIM_MS = 680
SWITCH_ANIM_MS = 240


def progress_fill(percent: int, width: int) -> int:
    """进度条填充像素：严格按容器宽度的百分比。

    旧实现用 ``percent * 2.6`` 的固定像素，30/30 分（100%）也只画 260px，
    容器变宽就填不满。这里 100% 一定等于容器宽度，不再有溢出细线。
    """
    w = max(0, int(width))
    p = max(0, min(100, int(percent or 0)))
    if p <= 0 or w <= 0:
        return 0
    if p >= 100:
        return w
    return max(min(4, w), int(round(w * p / 100)))


def animation_ms(base_ms: int = TRANSITION_MS) -> int:
    """动画时长恒定开启，不再有「减少动画」开关。"""
    return int(base_ms)


def stagger_delay(index: int) -> int:
    return int(index) * STAGGER_STEP_MS


def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, float(t)))
    return 1.0 - (1.0 - t) ** 3


# ---------------------------------------------------------------- 设置项精简
#
# 旧版有 24 个控件，其中大多数「一般不会改 / 不推荐改」：拟人强度、搜索轮数、
# 关键词、热搜来源、趋势图天数、日志开关、同步间隔数值、早晚提醒各自的时间、
# 4 个后台开关……现在一律按 defaults_patch() 写成推荐默认并移除控件。
#
# 能统一管理的合并成一个控件（一次写齐一组经典键）：
#   自动同步   = 开关 + 间隔（关 / 15 / 30 / 60 分钟）
#   任务提醒   = 早间 + 晚间（含 HH:MM）
#   后台与通知 = 通知 + 静音 + 托盘（休眠保持关闭，属不推荐改动项）
#
# 最终可见控件 10 个键，且每个都真实生效；「减少动画」已删除，动画恒定开启。

# 兼容常量：旧版控件键，已不再显示；保留供既有配置读取与回归测试。
SWITCH_KEYS: Sequence[str] = (
    "ui_notify_after_sync",
    "ui_silent_all_done",
    "ui_close_to_tray",
    "ui_sleep_all_done",
    "ui_remind_enabled",
    "ui_evening_enabled",
)



VISIBLE_SETTING_KEYS: Sequence[str] = (
    # 01 外观
    "ui_language", "ui_theme",
    # 02 同步与桌面（拟人强度控件放在底部的「拟人搜索默认设置」区）
    "ui_search_level", "ui_sync_mode", "ui_remind_mode",
    "ui_background_mode", "ui_launch_at_login",
    # 03 定时运行
    "ui_schedule_enabled", "ui_schedule_times",
    # 04 日历（查看器，不是配置项）
    "ui_calendar_view",
)

HIDDEN_DEFAULT_KEYS: Sequence[str] = tuple(
    k for k in UI_DEFAULTS if k not in VISIBLE_SETTING_KEYS)


def managed_group_keys(key: str) -> Sequence[str]:
    """供测试/文档：组键实际会写哪些经典键。"""
    p = dict(UI_DEFAULTS)
    return tuple(pref_to_settings(p, key, p[key]).keys())


# ---------------------------------------------------------------- 拟人搜索默认设置
#
# 设置页底部只展示 5 个拟人搜索参数（搜索轮数 / 单轮耗时 / 回车提交 / 结果点击 /
# 滚动屏数），且**不再单独可调**：全部由「拟人强度 低 / 中 / 高」这一个控件统一
# 控制。切换强度时 pref_to_settings() 一次性把整组参数写进经典 ``search_panel``，
# 经典 SearchPanel._collect() 再把它们透传给 api.run_human_search()，所以显示值
# 就是真实执行值，不是摆设。
#
# 中档取值与 api.run_human_search() 签名默认逐项一致（单测会核对）；低档更轻快，
# 高档更慢、抖动更大。level 同时驱动 human.PROFILES 的 1 / 2 / 3 级拟人行为。

INTENSITY_PARAM_KEYS: Sequence[str] = (
    "repeat", "level", "round_seconds", "max_round_seconds",
    "gap_seconds", "enter_rate", "click_prob", "scroll_screens",
)

INTENSITY_PROFILES: Dict[str, Dict[str, Any]] = {
    "低": dict(level=1, repeat=8,
               round_seconds=(10.0, 18.0), max_round_seconds=20.0,
               gap_seconds=(2.0, 6.0), enter_rate=0.35,
               click_prob=0.20, scroll_screens=0.6),
    "中": dict(level=2, repeat=12,
               round_seconds=(16.0, 29.0), max_round_seconds=30.0,
               gap_seconds=(3.0, 12.0), enter_rate=0.50,
               click_prob=0.35, scroll_screens=1.2),
    "高": dict(level=3, repeat=16,
               round_seconds=(24.0, 40.0), max_round_seconds=45.0,
               gap_seconds=(6.0, 18.0), enter_rate=0.70,
               click_prob=0.55, scroll_screens=2.0),
}

DEFAULT_SUMMARY_ROWS: Sequence[tuple[str, str, str]] = (
    # (profile 键, 中文标签, 英文标签)
    ("repeat", "搜索轮数", "Search rounds"),
    ("round_seconds", "单轮耗时", "Round length"),
    ("enter_rate", "回车提交", "Enter submits"),
    ("click_prob", "结果点击", "Result clicks"),
    ("scroll_screens", "滚动屏数", "Result scrolling"),
)

# 旧配置一次性迁移标记：把只有 level 的 search_panel 补齐为整组档位参数。
INTENSITY_MARKER = "ui_intensity_profile_v1"


def intensity_profile(choice: str) -> Dict[str, Any]:
    """返回某个强度档位的完整参数副本；未知值一律回落到默认「中」。"""
    return dict(INTENSITY_PROFILES.get(str(choice), INTENSITY_PROFILES["中"]))


def intensity_upgrade_patch(settings) -> dict:
    """旧配置只有 level/repeat/keywords/sources：按当前 level 补齐整组参数。

    只执行一次（INTENSITY_MARKER），不覆盖用户后续切换强度写下的值。
    """
    if settings.get(INTENSITY_MARKER):
        return {}
    sp = _search_panel(settings)
    try:
        level = int(sp.get("level", 2) or 2)
    except (TypeError, ValueError):
        level = 2
    choice = LEVEL_TO_CHOICE.get(max(1, min(3, level)), "中")
    merged = dict(intensity_profile(choice))
    merged.update(sp)   # 用户已有值一律优先，档位只补齐缺失键
    return {"search_panel": merged, INTENSITY_MARKER: True}


def default_settings_summary(prefs: dict, lang: str = "zh") -> list[tuple[str, str]]:
    """5 行「标签 → 当前值」，值取自当前强度档位真实写入的参数。"""
    en = lang == "en"
    params = intensity_profile(prefs.get("ui_search_level", "中"))
    params.update(prefs.get("ui_intensity_params") or {})
    rows: list[tuple[str, str]] = []
    for key, zh, english in DEFAULT_SUMMARY_ROWS:
        label = english if en else zh
        if key == "repeat":
            value = f"{_safe_int(params.get('repeat', 12), 12)} " + ("rounds" if en else "轮")
        elif key == "round_seconds":
            lo, hi = _range_pair(params.get("round_seconds"), (16.0, 29.0))
            value = f"{_num(lo)}–{_num(hi)} " + ("sec" if en else "秒")
        elif key in ("enter_rate", "click_prob"):
            value = f"{int(round(float(params.get(key, 0)) * 100))}%"
        else:  # scroll_screens
            value = f"{_num(params.get('scroll_screens', 1.2))} " + (
                "screens" if en else "屏")
        rows.append((label, value))
    return rows


def _intensity_params_from_sp(sp: dict, level: int) -> dict:
    """读取真实写入的档位参数；旧配置缺字段时以对应档位补齐，不显示 0%。"""
    out = intensity_profile(LEVEL_TO_CHOICE.get(max(1, min(3, level)), "中"))
    for key in INTENSITY_PARAM_KEYS:
        if key in sp and sp[key] is not None:
            out[key] = sp[key]
    return out


def _range_pair(value, fallback) -> tuple:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return (float(value[0]), float(value[1]))
        except (TypeError, ValueError):
            pass
    return fallback


def _num(value) -> str:
    n = float(value)
    return str(int(n)) if n.is_integer() else str(round(n, 1))


# ---------------------------------------------------------------- 启动时间档位
#
# 「+」不再弹 HH:MM 输入框：从 08:00 起每 30 分钟一档，自动补第一个空位。
# 与 config.normalize_run_times() 的「最多 10 个、互不重复、HH:MM」约束一致。

SCHEDULE_FIRST_MIN = 8 * 60
SCHEDULE_STEP_MIN = 30
SCHEDULE_SLOTS = 32          # 08:00 → 23:30
SCHEDULE_MAX = 10


def next_schedule_time(times) -> Optional[str]:
    """返回 08:00 起第一个未被占用的半小时档；满 10 个或无空位返回 None。"""
    existing = {str(t).strip() for t in (times or [])}
    if len(existing) >= SCHEDULE_MAX:
        return None
    for step in range(SCHEDULE_SLOTS):
        minutes = SCHEDULE_FIRST_MIN + step * SCHEDULE_STEP_MIN
        text = f"{minutes // 60:02d}:{minutes % 60:02d}"
        if text not in existing:
            return text
    return None


# ---------------------------------------------------------------- 圆环下状态条
#
# 插件版 popup.html 的 <div id="status" class="status stopped"> 里有
# .status-dot + #status-text；class 随 phase 变（stopped 灰 / running 蓝 /
# completed 绿 / error 红），全部完成显示「已完成，无需动作」。
# 这里用同一套分类，文字颜色与圆点颜色一起变，运行中圆点做呼吸。

STATUS_KINDS = ("ready", "busy", "done", "error")

_BUSY_WORDS = ("正在", "读取", "检查", "同步", "搜索", "收尾", "启动", "停止中",
               "冷却", "完成中", "Syncing", "Reading", "Checking", "Searching",
               "Wrapping", "Starting", "Cooldown", "Stopping", "Loading")
_ERROR_WORDS = ("失败", "错误", "出错", "未登录", "异常", "无法", "超时",
                "Failed", "Error", "Sign in", "Unavailable")
_DONE_WORDS = ("已完成", "完成：", "已同步", "已达上限", "无需动作", "同步完成",
               "Completed", "All tasks done", "Synced", "Limit reached")


def status_kind(text: str) -> str:
    """从状态文案推断插件版 .status 的 class（纯函数，可离线单测）。"""
    t = str(text or "")
    if any(w in t for w in _ERROR_WORDS):
        return "error"
    if any(w in t for w in _DONE_WORDS):
        return "done"
    if any(w in t for w in _BUSY_WORDS):
        return "busy"
    return "ready"


def status_color_key(kind: str) -> str:
    return {"busy": "primary", "done": "green_text",
            "error": "warn"}.get(kind, "text_sub")

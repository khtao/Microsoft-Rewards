"""把微软返回的内部活动 ID 翻译成人能看懂的中文。

微软的活动 name 常常长这样（就是你在任务页看到的原始内容）：

    Gamification_DailySet_ZHCN_26101012 / Child1
    WW_Rewards_locked_level2_Oct26w2_offer1
    ZHstar_Rewards_DailyGlobalOffer_Evergreen_Monday
    WW_banner_wallpaper_free_trt1_202609
    WW_Rewards_Campaign_RAF_Engagement_20250926_2

直接显示没法看。本模块做规则化翻译：切词 → 定主词 → 拼修饰 → 丢噪音。
目标效果：

    每日集卡 1 / 等级 2 解锁活动 1 / 每日活动（常驻·周一）/ 免费壁纸 / 邀请好友专题活动

纯函数，不碰网络与数据库。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

# --------------------------------------------------------------------------- #
# 主词表：按"具体 → 泛化"排序，命中第一个就当主词
# --------------------------------------------------------------------------- #
MAIN_WORDS: List[tuple] = [
    (("dailyset", "gamification"), "每日集卡"),
    (("dailyglobaloffer", "dailygloblet", "dailyglobal"), "每日活动"),
    (("quiz",), "趣味测验"),
    (("poll",), "投票"),
    (("survey",), "问卷"),
    (("punchcard",), "打卡挑战"),
    (("streak",), "连续挑战"),
    (("welcometour", "welcome"), "新手引导"),
    (("raf", "referral"), "邀请好友"),
    (("wallpaper",), "壁纸"),
    (("pcsearch",), "电脑端搜索"),
    (("mobilesearch",), "移动端搜索"),
    (("search",), "搜索"),
    (("pcsearch",), "电脑端搜索"),
    (("mobilesearch",), "移动端搜索"),
    (("punchcard",), "打卡挑战"),
]

# --------------------------------------------------------------------------- #
# 只有下面这些才算"高置信度"——即 ID 里出现该词，基本能确定任务是什么类型。
# 其余一律不猜：宁可显示原始 ID，也不要编一个看起来像、实际对不上的中文名
# （踩过：把 promotionType=urlreward 一律翻成"网页浏览"，结果和页面上显示的名字
#  完全不符，用户看到的是一堆错的"每日网页浏览/等级2解锁网页浏览"）。
# --------------------------------------------------------------------------- #
CONFIDENT_KEYWORDS = {
    "dailyset": "每日集卡", "gamification": "每日集卡",
    "wallpaper": "壁纸", "quiz": "趣味测验", "trivia": "趣味测验",
    "poll": "投票", "survey": "问卷", "punchcard": "打卡挑战",
    "streak": "连续挑战", "raf": "邀请好友", "referral": "邀请好友",
    "welcometour": "新手引导", "welcome": "新手引导",
    "copilot": "Copilot", "xbox": "Xbox", "gamepass": "Xbox",
}

# promotionType 只用于极少数能确定的类型；其余不猜
TYPE_MAP = {
    "quiz": "趣味测验",
    "poll": "投票",
    "survey": "问卷",
    "welcometour": "新手引导",
    "punchcard": "打卡挑战",
}

# 修饰词：不抢主词位置，拼在合适的地方
PREFIX_WORDS = {
    "free": "免费",
    "evergreen": "常驻",
    "daily": "每日",
    "weekly": "每周",
    "new": "新",
}
SUFFIX_WORDS = {
    "trt": "目标任务",
    "engagement": "互动",
    "unlock": "解锁",
    "bonus": "奖励",
}

WEEKDAYS = {
    "monday": "周一", "tuesday": "周二", "wednesday": "周三", "thursday": "周四",
    "friday": "周五", "saturday": "周六", "sunday": "周日",
}

# 噪音：地区/语言码、日期戳、版本号
NOISE = {
    "ww", "zh", "cn", "zhcn", "zhtw", "zhhk", "zht", "en", "us", "uk", "gb", "au", "ca",
    "in", "jp", "kr", "de", "fr", "es", "it", "br", "mx", "global", "intl", "row",
    "the", "a", "an", "of", "and", "for", "to", "with", "v1", "v2", "v3", "star",
    "hstar", "zhstar", "microsoft", "rewards", "reward", "msn", "bing", "set",
}

_DATE_RE = re.compile(r"^\d{5,8}$")                       # 202609 / 20250926 / 26101012
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")                  # 2026
_MONTH_RE = re.compile(
    r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\d*(w\d+)?$")  # Oct26w2
_LEVEL_RE = re.compile(r"^level(\d+)$")
_CHILD_RE = re.compile(r"^(child|item|part|step|stage|childoffer|offer)(\d+)$", re.I)
_INDEX_RE = re.compile(r"^\d{1,2}$")

_CJK_RE = re.compile(r"[一-鿿]")

CATEGORY_FALLBACK = {
    "搜索": "搜索任务",
    "每日任务": "每日任务",
    "更多活动": "更多活动",
}


def _split(raw: str) -> List[str]:
    """把 'WW_Rewards_Campaign_RAF_20250926_2' 切成 token 列表。"""
    raw = (raw or "").strip()
    if not raw:
        return []
    norm = re.sub(r"[\s/\\.\-|]+", "_", raw)          # 空格/斜杠/点/连字符都当分隔符
    norm = re.sub(r"(?<=[a-z])(?=[A-Z])", "_", norm)  # 驼峰：dailySet -> daily_Set
    norm = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", norm)
    return [t for t in norm.split("_") if t]


def _is_noise(token: str) -> bool:
    low = token.lower()
    return (low in NOISE or bool(_DATE_RE.match(low)) or bool(_YEAR_RE.match(low))
            or bool(_MONTH_RE.match(low)))


def clean_id(raw: str, max_tokens: int = 4, drop_level: bool = False) -> str:
    """把内部 ID 清理成还能看出语义的短串，用于"没拿到官方名称"时兜底。

        WW_Rewards_locked_level2_Oct26w2_offer1  ->  Locked Level2 Offer1

    只丢地区码 / 日期戳 / 品牌词 / 单字母碎片，剩下的英文原样保留 —— 不编中文。
    """
    if not raw:
        return ""
    tokens = [t for t in _split(raw) if not _is_noise(t.lower())]
    out: List[str] = []
    for t in tokens:
        low = t.lower()
        if low in ("rewards", "reward", "microsoft", "bing", "msn", "offer", "promotion",
                   "promotions", "task", "activity", "campaign", "banner", "urlreward",
                   "no", "not", "none", "default", "generic", "item", "child", "part"):
            continue
        if low in WEEKDAYS:
            out.append(t)
            continue
        if low.startswith("level"):
            if not drop_level:      # 前缀已经写了"等级 N"，这里就不再重复
                out.append(t)
            continue
        if len(t) <= 1:            # 驼峰切分产生的单字母碎片（Z / H）
            continue
        out.append(t[:1].upper() + t[1:])
        if len(out) >= max_tokens:
            break
    return " ".join(out).strip()


def _main_word(tokens: List[str]) -> Optional[str]:
    """按优先级找主词。

    既比对单个 token，也比对把所有 token 拼起来后的子串 ——
    因为驼峰切分会把 'DailySet' 拆成 'daily'+'set'、'URLReward' 拆成 'url'+'reward'，
    只按 token 比对必然漏掉。
    """
    joined = "".join(tokens)
    for group, word in MAIN_WORDS:
        for kw in group:
            if kw in tokens or kw in joined:
                return word
    return None


def humanize(raw: str, category: str = "", index: int = 0,
             promo: Optional[Dict] = None) -> str:
    """把原始活动名翻译成简短中文。

    raw      接口里的 name / title
    category 任务分类（搜索 / 每日任务 / 更多活动）
    index    同一分类内的序号，用于最终兜底
    promo    原始活动 dict，可读 promotionType 等辅助字段
    """
    raw = (raw or "").strip()
    promo = promo or {}

    # ---- 1. 本来就是人话（含中文、不是编码串）→ 直接放行
    if raw and _CJK_RE.search(raw) and "_" not in raw and len(raw) <= 40:
        return raw

    tokens = [t.lower() for t in _split(raw)]

    level: Optional[str] = None
    weekday: Optional[str] = None
    numbers: List[str] = []
    prefixes: List[str] = []
    suffixes: List[str] = []
    unknown: List[str] = []

    for low in tokens:
        m = _LEVEL_RE.match(low)
        if m:
            level = m.group(1)
            continue
        m = _CHILD_RE.match(low)
        if m:
            numbers.append(m.group(2))
            continue
        if low in WEEKDAYS:
            weekday = WEEKDAYS[low]
            continue
        if _INDEX_RE.match(low):
            numbers.append(low)
            continue
        if _is_noise(low):
            continue
        if low in PREFIX_WORDS:
            if PREFIX_WORDS[low] not in prefixes:
                prefixes.append(PREFIX_WORDS[low])
            continue
        if low in SUFFIX_WORDS:
            if SUFFIX_WORDS[low] not in suffixes:
                suffixes.append(SUFFIX_WORDS[low])
            continue
        # 主词表里出现过的，留给下一步挑主词
        if any(low in g for g, _ in MAIN_WORDS):
            continue
        if 2 <= len(low) <= 14:
            unknown.append(low)

    # ---- 2. 主词：只认"高置信度"关键词，绝不泛化猜测
    main = _confident_word(tokens)

    prefixes = [p for p in prefixes if p not in (main or "")]
    if level:
        prefixes.append(f"等级 {level} 解锁")

    if main is None:
        # 拿不到可信主词 —— 显示清理后的原始 ID，不编中文。
        # 调用方可通过 name_is_guess 标记在界面上区分。
        fallback = clean_id(raw, drop_level=bool(level))
        tail = fallback or CATEGORY_FALLBACK.get(category, "活动")
        name = ("".join(prefixes) + " " + tail).strip()
        # 兜底名也要能互相区分：末尾已是编号就不追加，否则补上组内序号
        if not re.search(r"\d$", tail) and category in ("每日任务", "更多活动"):
            name += f" {index + 1}"
    else:
        name = "".join(prefixes) + main
        tags = suffixes + ([weekday] if weekday else [])
        if tags:
            name += "（" + "·".join(tags) + "）"
        number = numbers[0] if len(numbers) == 1 else None
        if number:
            name += f" {number}"
        elif category in ("每日任务", "更多活动"):
            name += f" {index + 1}"

    return re.sub(r"\s+", " ", name).strip()


def _confident_word(tokens: List[str]) -> Optional[str]:
    """只在 ID 里出现"能确定类型"的关键词时才返回中文主词，否则 None。

    同时比对单个 token 与拼接串（驼峰会把 DailySet 拆成 daily+set）。
    """
    joined = "".join(tokens).lower()
    for kw, word in CONFIDENT_KEYWORDS.items():
        if kw in tokens or kw in joined:
            return word
    for group, word in MAIN_WORDS:
        for kw in group:
            if kw in tokens or kw in joined:
                return word
    return None


def is_guess(name: str, raw: str = "") -> bool:
    """这个名字是不是"没拿到官方名"的兜底结果。

    判定：名字里没有中文，或者就是从原始 ID 清理来的。
    """
    if not name:
        return True
    if not _CJK_RE.search(name):
        return True
    return False


def index_from_key(key: str, default: int = 0) -> int:
    """'more:3' -> 3，'dailyset:0' -> 0；解析不出来就用 default。"""
    if ":" in key:
        tail = key.rsplit(":", 1)[-1]
        if tail.isdigit():
            return int(tail)
    return default


def humanize_task(key: str, name: str, category: str, index: int = 0,
                  promo: Optional[Dict] = None) -> str:
    """storage / UI 用的入口。

    序号优先从 key 里解析（more:3 -> 3），这样即使调用方没传 index，
    多个同类活动也不会全都叫"1"。
    """
    if key in ("pc_search", "mobile_search"):
        return {"pc_search": "电脑端搜索", "mobile_search": "移动端搜索"}[key]
    return humanize(name, category, index_from_key(key, index), promo)

# -*- coding: utf-8 -*-
"""Microsoft Rewards 页面只读解析层（零第三方依赖 · 纯函数 · 可单测）。

设计要点：

1. 只吃 ``/dashboard`` 与 ``/earn`` 两个页面的 HTML，不做任何网络请求；
2. 三项积分「卡片标签 → 相邻数值」优先、RSC 字段兜底，**绝不用任务合计推算**；
   读不到就是 ``None``，绝不写成 0（UI 显示「--」）；
3. 识别登录页 / 公开「关于」页，直接返回 ``signed_out``，不写假的 0 分；
4. 任务卡同时支持 RSC ``dailySetItems``（每日活动）与 ``activityCards``
   （/earn「日常任务」，含折叠区里未完成的卡）、可见 DOM 绿色徽章卡；
   RSC 完成态优先、两页按 offerId 去重，并输出逐条解析日志；
5. ``pointsCounters`` 读不到时返回 ``None`` 而不是伪造「已满」；官方每日
   搜索上限按等级为 15 / 30 / 60 分，硬编码 60/60 会误报完成进度。

本模块不含登录、不含写库、不含任何点击/搜索/领取动作。
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------- 常量

POINT_LABELS: Dict[str, Tuple[str, ...]] = {
    "available": ("可用积分", "可用点数", "Available points", "Available"),
    "today": ("今日积分", "今日已赚取", "今日赚取", "Points earned today", "Today's points"),
    "claimable": ("可领取", "可领取积分", "待领取积分", "Points to claim", "Claimable"),
}

SECTION_LABEL: Dict[str, str] = {
    "search": "搜索", "daily": "每日活动", "promo": "日常任务",
}

# 与现有 SQLite 分类的对应关系（供同步层使用）
CATEGORY_MAP: Dict[str, str] = {
    "search": "搜索", "daily": "每日任务", "promo": "更多活动",
}



@dataclass
class Card:
    name: str
    description: str = ""
    section: str = "daily"       # search / daily / promo
    points: int = 0
    earned: int = 0
    completed: bool = False
    url: str = ""
    source: str = "rsc"          # rsc / dom / counter
    offer_id: str = ""           # RSC offerId：同名卡去重/落库不互相覆盖


@dataclass
class SearchCounter:
    max: Optional[int] = None
    progress: Optional[int] = None

    @property
    def known(self) -> bool:
        return self.max is not None and self.progress is not None

    @property
    def completed(self) -> bool:
        return self.known and self.progress >= self.max  # type: ignore[operator]


@dataclass
class Group:
    key: str
    label: str
    items: List[Card] = field(default_factory=list)
    earned: int = 0
    total: int = 0


@dataclass
class Report:
    points: Dict[str, Optional[int]] = field(default_factory=dict)
    groups: List[Group] = field(default_factory=list)
    total: int = 0
    earned: int = 0
    percent: int = 0
    counter: Optional[SearchCounter] = None
    signed_out: bool = False
    degraded: bool = False
    logs: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def cards(self) -> List[Card]:
        return [i for g in self.groups for i in g.items]


# ---------------------------------------------------------------- 基础工具

def decode_payload(html: str) -> str:
    """RSC payload 里的 ``\\"`` / ``\\\\"`` 还原成普通引号。"""
    return str(html or "").replace('\\\\"', '"').replace('\\"', '"')


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def num_from(text) -> Optional[int]:
    """从 '1,234 分' / '-5' 之类的文本里取整数；取不到返回 None。"""
    m = re.search(r"-?[\d,.\s]+\d|-?\d+", str(text or ""))
    if not m:
        return None
    try:
        return int(re.sub(r"[^\d-]", "", m.group(0)))
    except ValueError:
        return None


def pick_number(clean: str, patterns: Iterable[re.Pattern]) -> Optional[int]:
    for re_ in patterns:
        m = re_.search(clean)
        if not m:
            continue
        n = num_from(m.group(1))
        if n is not None:
            return n
    return None


def clean_text(s) -> str:
    t = re.sub(r"<[^>]+>", " ", str(s or ""))
    t = re.sub(r"\\u0026|&amp;", "&", t, flags=re.I)
    t = re.sub(r"&nbsp;|&#160;", " ", t, flags=re.I)
    t = re.sub(r"\\n|\\r", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:160]


def strip_tags(s) -> str:
    return clean_text(str(s or ""))


# ---------------------------------------------------------------- 登录页识别

_SIGNIN_PATTERNS: Tuple[Tuple[str, str], ...] = (
    # 双条件组合：出现第一个且出现第二个才算登录页
    (r"登录到 Microsoft 帐户需要启用 JavaScript", r"Microsoft"),
    (r"使用你的 Microsoft 帐户", r"创建帐户|忘记用户名"),
    (r"Sign in to your Microsoft account", r"Create (?:an? )?account|No account\?"),
    (r"(?:About|关于)\s*[–\-—]\s*Microsoft Rewards", r"(?:\bSign in\b|登录)"),
    (r"Gift cards for the win|礼品卡为你添彩", r"(?:\bSign in\b|登录)"),
    # 中文「关于」官方页（原 JS 只认 About 标题，这里补上）
    (r"了解微软积分商城的优势", r"登录|创建帐户"),
    (r"立即开始赚取积分", r"登录创建帐户|加入 Microsoft Rewards"),
    (r"什么是 Microsoft Rewards\?", r"加入 Microsoft Rewards 需要付费吗"),
)


# 已登录的 Rewards 页面一定带这些服务端数据（RSC payload）。公开「关于」页
# 与登录页都没有。新版 /dashboard 页脚可能仍挂「登录 / 创建帐户」推广链接，
# 若只按文案组合判断会把已登录页误判成登录页：/dashboard 整页被丢弃，
# 导出 zip 里只剩 /earn 一页，同步也被压成 DEGRADED。
_SIGNED_IN_MARKERS = re.compile(
    r'"(?:pointsCounters|dailySetItems|activityCards|pointsHistory|'
    r'availablePoints|userStatus|dashboardBalance)"', re.I)


def is_sign_in_page(html: str) -> bool:
    """未登录页 / 公开 About 页识别。宁可判成未登录，也不要拿公开页去解析积分。"""
    t = str(html or "")
    if _SIGNED_IN_MARKERS.search(t):
        return False
    for first, second in _SIGNIN_PATTERNS:
        if re.search(first, t, re.I) and re.search(second, t, re.I):
            return True
    return False


# ---------------------------------------------------------------- 三项积分

_DOM_CARD_RE = re.compile(
    r"<p[^>]*text-labelControl[^>]*>\s*([^<]{1,30}?)\s*</p>"
    r"[\s\S]{0,1500}?"
    r"<p[^>]*text-pageHeader[^>]*>\s*([^<]{1,30}?)\s*</p>"
)
_RSC_CARD_RE = re.compile(
    r'"className":"text-labelControl","children":"([^"]{1,30})"'
    r'[\s\S]{0,2000}?'
    r'"className":"text-pageHeader","children":"([^"]{1,30})"'
)


def cards_points(html: str, clean: Optional[str] = None) -> Dict[str, int]:
    """积分卡：标签 → 相邻数值（DOM 与 RSC 两种形态）。

    ``clean`` 可由 ``build_report`` 传入，避免同一页重复做 ``decode_payload``。
    """
    clean = decode_payload(html) if clean is None else clean
    out: Dict[str, int] = {}

    def put(label, value) -> None:
        n = num_from(value)
        if n is None:
            return
        text = str(label or "").strip()
        for key, names in POINT_LABELS.items():
            if any(text == x or x in text for x in names):
                if key not in out:
                    out[key] = n
                return

    for m in _DOM_CARD_RE.finditer(clean):
        put(m.group(1), m.group(2))
    for m in _RSC_CARD_RE.finditer(clean):
        put(m.group(1), m.group(2))
    return out


_FIELD_PATTERNS: Dict[str, Tuple[re.Pattern, ...]] = {
    "available": (re.compile(r'"(?:availablePoints|balance|pointsBalance)"\s*:\s*"?(\d[\d,]*)"?', re.I),),
    "today": (re.compile(r'"(?:pointsEarnedToday|todayPoints|pointsToday|earnedToday)"\s*:\s*"?(\d[\d,]*)"?', re.I),),
    "claimable": (re.compile(r'"(?:claimablePoints|pointsToClaim|claimable)"\s*:\s*"?(\d[\d,]*)"?', re.I),),
}


def parse_points_trio(html: str, clean: Optional[str] = None) -> Dict[str, Optional[int]]:
    """卡片值优先，字段兜底；绝不用任务合计推算。"""
    clean = decode_payload(html) if clean is None else clean
    cards = cards_points(html, clean)
    out: Dict[str, Optional[int]] = {}
    for key in ("available", "today", "claimable"):
        out[key] = cards[key] if key in cards else pick_number(clean, _FIELD_PATTERNS[key])
    return out


def merge_trio(a: Optional[dict], b: Optional[dict]) -> Dict[str, Optional[int]]:
    """两页合并：先到先得，缺失留 None。"""
    out: Dict[str, Optional[int]] = {}
    for key in ("available", "today", "claimable"):
        av = (a or {}).get(key)
        bv = (b or {}).get(key)
        out[key] = av if _finite(av) else (bv if _finite(bv) else None)
    return out


# ---------------------------------------------------------------- 任务卡

_H2_RE = re.compile(r"<h2[^>]*>([\s\S]{1,80}?)</h2>", re.I)
_PROMO_H2_RE = re.compile(r"日常任务|每日任务|Daily tasks|More activities", re.I)
_DAILY_H2_RE = re.compile(r"每日活动|Daily activities|Daily set", re.I)
_EXCLUDE_H2_RE = re.compile(r"升级活动|连续打卡|任务集|punch card|streak|monthly", re.I)


def _heading_sections(html: str) -> Tuple[List[int], List[str]]:
    """一次扫描出所有 <h2> 的结束位置与分组；供卡片二分查找。

    旧实现对每张卡都执行 ``_H2_RE.finditer(html[:index])``，卡片多时是
    O(卡片数 × HTML 长度)。现在整页只扫一次，单卡查找 O(log 标题数)。
    """
    ends: List[int] = []
    sections: List[str] = []
    for m in _H2_RE.finditer(html):
        near = strip_tags(m.group(1))
        if _EXCLUDE_H2_RE.search(near):
            section = "other"
        elif _PROMO_H2_RE.search(near):
            section = "promo"
        elif _DAILY_H2_RE.search(near):
            section = "daily"
        else:
            section = "other"
        ends.append(m.end())
        sections.append(section)
    return ends, sections


def section_before_heading(html: str, index: int,
                           headings: Optional[Tuple[List[int], List[str]]] = None) -> str:
    """卡片前面最近的 <h2> 决定分组；升级活动 / 连续打卡等区块显式排除。"""
    if headings is None:
        headings = _heading_sections(html[:index])
    ends, sections = headings
    i = bisect.bisect_right(ends, index) - 1
    return sections[i] if i >= 0 else "other"


def json_array_objects(clean: str, key: str) -> List[str]:
    """按方括号深度切出 ``"key":[...]`` 里的一级对象。

    相比原 JS 版增加引号/转义跳过，避免标题里出现 ``]`` 时提前截断。
    """
    at = clean.find('"' + key + '"')
    if at < 0:
        return []
    open_at = clean.find("[", at)
    if open_at < 0:
        return []
    depth, quoted, escaped = 0, False, False
    end = -1
    for i in range(open_at, min(len(clean), open_at + 200000)):
        ch = clean[i]
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            quoted = not quoted
            continue
        if quoted:
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        return []
    objs: List[str] = []
    level, quoted, escaped, start = 0, False, False, -1
    for i in range(open_at + 1, end):
        ch = clean[i]
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            quoted = not quoted
            continue
        if quoted:
            continue
        if ch == "{":
            if level == 0:
                start = i
            level += 1
        elif ch == "}" and level > 0:
            level -= 1
            if level == 0 and start >= 0:
                objs.append(clean[start:i + 1])
    return objs


# RSC 数组 → 分组。``activityCards`` 是 /earn「日常任务 / MoreActivities」区
# 的服务端真值：页面上默认折叠（"显示更多"）的卡只出现在这里，DOM 里看不到。
# 例如「你是否知道答案？ +5」今天未完成的那张，就只在 activityCards 里。
RSC_ARRAY_SECTIONS: Tuple[Tuple[str, str], ...] = (
    ("dailySetItems", "daily"),   # 每日活动 3 张 ×10
    ("activityCards", "promo"),   # 日常任务：只要有积分就纳入
)


def structured_items_from(html: str,
                          keys: Optional[Sequence] = None,
                          section: str = "daily",
                          clean: Optional[str] = None) -> List[Card]:
    """RSC：每日活动 + 日常任务卡（标题 / 分值 / 完成态 / 落地页 / offerId）。

    ``keys`` 默认是 ``RSC_ARRAY_SECTIONS``；也可传 ``["dailySetItems"]`` 之类
    的字符串序列（沿用 ``section``），或 ``(key, section)`` 对。
    """
    clean = decode_payload(html) if clean is None else clean
    specs: List[Tuple[str, str]] = []
    for spec in (RSC_ARRAY_SECTIONS if keys is None else keys):
        if isinstance(spec, (tuple, list)) and len(spec) == 2:
            specs.append((str(spec[0]), str(spec[1])))
        else:
            specs.append((str(spec), section))
    out: List[Card] = []
    seen = set()
    for key, sec in specs:
        for obj in json_array_objects(clean, key):
            def grab(pattern: str) -> Optional[str]:
                m = re.search(pattern, obj, re.I)
                return m.group(1) if m else None

            title = clean_text(grab(r'"title"\s*:\s*"([^"]*)"'))
            points = num_from(grab(r'"points"\s*:\s*"?(\d+)"?')) or 0
            if not title or points <= 0:
                continue
            desc = clean_text(grab(r'"description"\s*:\s*"([^"]*)"'))
            completed = bool(re.search(r'"isCompleted"\s*:\s*true', obj, re.I))
            offer_id = grab(r'"offerId"\s*:\s*"([^"]*)"') or ""
            # 同名卡（如两天的「你是否知道答案？」）靠 offerId + 描述区分，
            # 不能只按标题去重，否则未完成的那张会被已完成的同名卡吃掉。
            dedup = "|".join((title, desc, str(points), offer_id))
            if dedup in seen:
                continue
            seen.add(dedup)
            out.append(Card(
                name=title, description=desc, section=sec, points=points,
                earned=points if completed else 0, completed=completed,
                url=grab(r'"(?:destination|url|href)"\s*:\s*"([^"]*)"') or "",
                source="rsc", offer_id=offer_id,
            ))
    return out


_ANCHOR_RE = re.compile(r"<a\b[^>]*>[\s\S]*?</a>", re.I)
_BADGE_RE = re.compile(
    r"bg-statusSuccessRewardsBg[\s\S]{0,500}?"
    r'<p class="text-metadata text-fgCtrlOnImage">\s*(\d+)\s*</p>', re.I)
_TITLE_RE = re.compile(r'<p class="line-clamp-3 text-globalBody2Strong">([^<]*)</p>', re.I)
_DESC_RE = re.compile(r'<p class="line-clamp-3 text-fgCtrlNeutralSecondaryRest">([^<]*)</p>', re.I)
_DONE_RE = re.compile(r"已完成|已领取|Completed|Claimed", re.I)


def visible_cards_from(html: str) -> Tuple[List[Card], Dict[str, int]]:
    """可见 DOM：只认带绿色数字徽章的 <a> 卡片。"""
    out: List[Card] = []
    no_badge = other_seen = 0
    headings = _heading_sections(html)
    for m in _ANCHOR_RE.finditer(html):
        chunk = m.group(0)
        badge = _BADGE_RE.search(chunk)
        if not badge:
            no_badge += 1
            continue
        points = int(badge.group(1))
        if points <= 0:
            no_badge += 1
            continue
        section = section_before_heading(html, m.start(), headings)
        if section == "other":
            other_seen += 1
            continue
        tm = _TITLE_RE.search(chunk)
        name = clean_text(tm.group(1)) if tm else ""
        if not name:
            continue
        dm = _DESC_RE.search(chunk)
        completed = bool(_DONE_RE.search(chunk))
        out.append(Card(
            name=name, description=clean_text(dm.group(1)) if dm else "",
            section=section, points=points, earned=points if completed else 0,
            completed=completed, source="dom",
        ))
    return out, {"noBadge": no_badge, "otherSeen": other_seen, "domCards": len(out)}


def card_dedup_key(item: Card) -> str:
    """基础键：标题 / 描述 / 分值 / 分组（不含 offerId），跨页口径一致。"""
    return "|".join((item.name, item.description, str(item.points), item.section))


def dedup_cards(cards: Sequence[Card]) -> List[Card]:
    """统一去重，规则只有三条，页内与跨页共用：

    1. **带 offerId 的 RSC 卡是服务端真值**，每个不同 offerId 各留一张——
       所以两天的同名同分「你是否知道答案？」（一张未完成、一张已完成）
       会同时保留，不会再互相覆盖成假 100%；
    2. 同基础键下**没有 offerId 的卡是镜像**（DOM 卡、或 /dashboard 的简化
       payload），只要该组已有带 offerId 的真卡就丢弃，避免跨页重复显示；
    3. 整组都没有 offerId 时只留一张，RSC 优先。

    顺序按原始出现位置恢复，不改变界面上的分组排列。
    """
    bases = [card_dedup_key(c) for c in cards]
    offered: Dict[str, set] = {}
    for card, base in zip(cards, bases):
        if card.offer_id:
            offered.setdefault(base, set()).add(card.offer_id)
    # 带 offerId 的排前面（信息更全），其次 RSC 优先
    order = sorted(range(len(cards)), key=lambda i: (
        0 if cards[i].offer_id else 1,
        0 if cards[i].source == "rsc" else 1,
        i,
    ))
    keep: Dict[str, int] = {}
    for i in order:
        card, base = cards[i], bases[i]
        if not card.offer_id and offered.get(base):
            continue
        key = base + ("|" + card.offer_id if card.offer_id else "")
        if key not in keep:
            keep[key] = i
    return [cards[i] for i in sorted(keep.values())]


def cards_from_rewards_html(html: str, clean: Optional[str] = None
                            ) -> Tuple[List[Card], Dict[str, int]]:
    """合并 RSC 与可见 DOM 卡，RSC 优先。

    DOM 只渲染「显示更多」展开前的卡，且完成态来自可见文案；RSC 是服务端
    真值（``isCompleted``）。同名同分同分组时以 RSC 为准，否则 DOM 里已完成的
    「你是否知道答案？」会把 activityCards 里未完成的那张挤掉，主界面误报
    「今日全部任务已完成」。
    """
    rsc = structured_items_from(html, clean=clean)
    dom, stats = visible_cards_from(html)
    rsc_ident = {(i.name, i.section, i.points) for i in rsc}
    candidates: List[Card] = []
    for item in (*rsc, *dom):
        if item.section not in SECTION_LABEL:
            continue
        if item.source == "dom" and (item.name, item.section, item.points) in rsc_ident:
            continue
        candidates.append(item)
    return [x for x in dedup_cards(candidates)
            if x.points > 0 and x.name], stats


# ---------------------------------------------------------------- 搜索进度

_COUNTER_RE = re.compile(
    r'"pointsCounters"\s*:\s*\{[\s\S]{0,600}?'
    r'"(?:pc|pcSearch|combined|combinedSearch|search)"\s*:\s*\{([^{}]*)\}')


def parse_search_counter(html: str, clean: Optional[str] = None
                         ) -> Optional[SearchCounter]:
    """页面 pointsCounters；读不到返回 None，绝不伪造「60/60 已满」。"""
    clean = decode_payload(html) if clean is None else clean
    block = _COUNTER_RE.search(clean)
    if not block:
        return None
    mx = re.search(r'"max"\s*:\s*(\d+)', block.group(1))
    pg = re.search(r'"progress"\s*:\s*(\d+)', block.group(1))
    if not (mx and pg):
        return None
    return SearchCounter(max=int(mx.group(1)), progress=int(pg.group(1)))




# ---------------------------------------------------------------- 组装报告

def build_report(dash_html: Optional[str], earn_html: Optional[str]) -> Report:
    """把两个页面 HTML 组装成一次完整读取结果（纯函数，无副作用）。"""
    logs: List[str] = []
    warnings: List[str] = []
    dash_ok, earn_ok = bool(dash_html), bool(earn_html)
    dash, earn = str(dash_html or ""), str(earn_html or "")

    dash_signin = dash_ok and is_sign_in_page(dash)
    earn_signin = earn_ok and is_sign_in_page(earn)
    if dash_ok and earn_ok and dash_signin and earn_signin:
        return Report(
            points={"available": None, "today": None, "claimable": None},
            signed_out=True,
            logs=["识别为登录页 / 公开「关于」页：不写假的 0 分，请先在浏览器中登录"],
        )
    degraded = (dash_signin or earn_signin) or not (dash_ok and earn_ok)
    if degraded:
        warnings.append("仅部分页面可用：只采用能读到的字段，其余保留上次快照")

    # 登录页不能参与积分解析；整页只解码一次，后续解析全部复用。
    d_html = "" if dash_signin else dash
    e_html = "" if earn_signin else earn
    d_clean = decode_payload(d_html) if d_html else ""
    e_clean = decode_payload(e_html) if e_html else ""

    trio = merge_trio(
        parse_points_trio(d_html, d_clean) if d_html else None,
        parse_points_trio(e_html, e_clean) if e_html else None,
    )
    logs.append("三项积分：卡片「标签→数值」解析 /dashboard + /earn → 合并取值")

    page_cards: List[Card] = []
    rsc_hits = dom_hits = 0
    for page_name, page_html, page_clean in (("/dashboard", d_html, d_clean),
                                             ("/earn", e_html, e_clean)):
        if not page_html:
            logs.append(page_name + "：本次不可用，跳过")
            continue
        page_items, stats = cards_from_rewards_html(page_html, page_clean)
        rsc_hits += sum(1 for x in page_items if x.source == "rsc")
        dom_hits += sum(1 for x in page_items if x.source == "dom")
        promo_rsc = sum(1 for x in page_items
                        if x.source == "rsc" and x.section == "promo")
        logs.append(
            f"{page_name}：徽章卡 {stats['domCards']} 张，无徽章推广卡 {stats['noBadge']} 张，"
            f"其它版块卡 {stats['otherSeen']} 张，日常任务 RSC 卡 {promo_rsc} 张")
        page_cards.extend(page_items)
    # 两页统一去重：/dashboard 与 /earn 的同一张卡合并，服务端 offerId
    # 不同的同名卡分别保留（未完成的那张不会被已完成的吞掉）。
    items = dedup_cards(page_cards)
    logs.append(f"RSC 命中 {rsc_hits} 张（两页相加、去重前，含 activityCards 日常任务）；"
                f"可见 DOM 徽章卡命中 {dom_hits} 张")
    logs.append(f"两页去重后保留 {len(items)} 张有积分任务卡")

    counter = (parse_search_counter(e_html, e_clean) if e_html else None) or (
        parse_search_counter(d_html, d_clean) if d_html else None)
    if counter and counter.known:
        logs.append(f"搜索进度 pointsCounters {counter.progress}/{counter.max}"
                    + ("（已满）" if counter.completed else ""))
    else:
        logs.append("搜索进度 pointsCounters 未读到：显示「未知」，不伪造 60/60 已完成")

    if (d_html or e_html) and not items and not any(_finite(v) for v in trio.values()):
        warnings.append("页面非登录页但未解析到任何积分卡：页面结构可能已变化，请以官网显示为准")

    all_items: List[Card] = []
    if counter and counter.known:
        all_items.append(Card(
            name="今日 Bing 搜索", description="", section="search",
            points=counter.max, earned=counter.progress,  # type: ignore[arg-type]
            completed=counter.completed, source="counter",
        ))
    all_items.extend(i for i in items if i.section != "search")

    groups: List[Group] = []
    for key in ("search", "daily", "promo"):
        lst = [i for i in all_items if i.section == key]
        if not lst:
            continue
        groups.append(Group(
            key=key, label=SECTION_LABEL[key], items=lst,
            earned=sum(i.earned for i in lst), total=sum(i.points for i in lst),
        ))

    total = sum(i.points for i in all_items)
    earned = sum(i.earned for i in all_items)
    logs.append(f"任务合计 {earned} / {total} 分（与「今日积分」不必相等：周任务 / 额外奖励分）")

    return Report(
        points=trio, groups=groups, total=total, earned=earned,
        percent=round(earned / total * 100) if total else 0,
        counter=counter, signed_out=False, degraded=degraded,
        logs=logs, warnings=warnings,
    )


# ---------------------------------------------------------------- 兼容桥接

def to_legacy_dashboard(report: Report, day: str, level: int = 0) -> dict:
    """把「两页 HTML 解析结果」转成旧 ``/api/getuserinfo`` 的 dashboard 结构。

    目的：``rewards_client.parse_dashboard``、``activity_worker``、``app.py`` 里
    对 ``Snapshot`` 的所有既有读取点无需改动，就能吃到页面真值；同时不再请求
    那个单点接口。缺失字段一律不下发（而不是塞 0），让旧解析层自己判为无效。
    """
    user: Dict[str, object] = {"levelInfo": {"activeLevel": level or ""}}
    if report.points.get("available") is not None:
        user["availablePoints"] = int(report.points["available"])

    pc: List[dict] = []
    if report.counter and report.counter.known:
        pc.append({
            "pointProgress": int(report.counter.progress),  # type: ignore[arg-type]
            "pointProgressMax": int(report.counter.max),     # type: ignore[arg-type]
            "destination": "https://www.bing.com/",
        })
    user["counters"] = {"pcSearch": pc, "mobileSearch": []}

    def promo(card: Card) -> dict:
        return {
            "title": card.name, "name": card.name,
            "pointProgressMax": int(card.points),
            "pointProgress": int(card.earned),
            "complete": bool(card.completed),
            "destination": card.url or "",
            "offerId": card.offer_id or "",
        }

    daily = [promo(i) for g in report.groups if g.key == "daily" for i in g.items]
    more = [promo(i) for g in report.groups if g.key == "promo" for i in g.items]
    return {"dashboard": {
        "userStatus": user,
        "dailySetPromotions": {day: daily},
        "morePromotions": more,
    }}

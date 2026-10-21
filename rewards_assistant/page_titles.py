"""从 Rewards 页面抓取**页面真实显示的活动名称**。

为什么要这个模块
----------------
API（/api/getuserinfo）返回的 name 常常是内部 ID，例如
`Gamification_DailySet_ZHCN_26101012`、`WW_Rewards_locked_level2_Oct26w2_offer1`。

但用户在这两个页面上看到的并不是这些 ID：
* https://rewards.bing.com/dashboard  → 每日活动（Daily Set / activityCards）
* https://rewards.bing.com/earn       → 日常任务（More activities / promotionCards）

页面上渲染的是本地化后的真实标题（"必应首页测验"、"今日投票"之类）。
本模块用 Cookie 抓这两个页面的 HTML，把真实标题取出来，
再按 offerId 或序号回填到 API 解析出的任务上 —— **显示名与页面完全一致**。

只做只读 GET，不点任何东西、不调任何写接口。
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

import requests

from .config import REWARDS_DASHBOARD_URL, REWARDS_EARN_URL

# 页面里承载活动卡片的 JSON 数组名（新版 / 旧版 / A-B 版本都列上）
CARD_ARRAY_NAMES = (
    "dailySetItems",
    "activityCards",
    "promotionCards",
    "promotions",
    "dailySet",
    "cards",
)

# 标题字段优先级：谁更像"页面上显示的那个名字"谁靠前
TITLE_FIELDS = ("title", "displayName", "name", "cardTitle", "promotionTitle", "text")
ID_FIELDS = ("offerId", "offerid", "offer_id", "id", "promotionId", "activityId")
POINTS_FIELDS = ("points", "pointProgressMax", "rewardPoints", "maxPoints")
DONE_FIELDS = ("isCompleted", "completed", "isComplete", "complete")
LINK_FIELDS = ("destination", "destinationUrl", "linkUrl", "url", "link", "href")


class PageCard:
    """从页面 HTML 里解析出的一张活动卡片。"""

    __slots__ = ("offer_id", "title", "points", "complete", "link")

    def __init__(self, offer_id: str = "", title: str = "", points: int = 0,
                 complete: bool = False, link: str = "") -> None:
        self.offer_id = offer_id
        self.title = title
        self.points = points
        self.complete = complete
        self.link = link

    def __repr__(self) -> str:  # 便于日志排查
        return f"PageCard({self.offer_id!r}, {self.title!r}, {self.points})"


def _unescape(text: str) -> str:
    """处理 \\u4e2d\\u6587 与常见 HTML 实体。"""
    if "\\u" in text or "\\/" in text:
        try:
            text = text.encode("utf-8", "ignore").decode("unicode_escape", "ignore")
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
        text = text.replace(a, b)
    return text.strip()


def _find_json_array(text: str, name: str) -> Optional[str]:
    """找到 "name": [ ... ] 的完整数组片段（按括号配对，能处理嵌套）。"""
    m = re.search(r'"%s"\s*:\s*\[' % re.escape(name), text)
    if not m:
        return None
    start = m.end() - 1  # 指向 '['
    depth, quoted, escaped = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
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
                return text[start:i + 1]
    return None


def _split_objects(array_src: str) -> List[str]:
    """把 JSON 数组按顶层对象切开（比正则 /{[^{}]*}/ 更稳，能容纳一层嵌套）。"""
    out: List[str] = []
    depth, quoted, escaped, start = 0, False, False, -1
    for i, ch in enumerate(array_src):
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
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                out.append(array_src[start:i + 1])
                start = -1
    return out


def _field(obj: str, names: Tuple[str, ...]) -> str:
    for n in names:
        m = re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"' % re.escape(n), obj)
        if m:
            val = _unescape(m.group(1))
            if val:
                return val
    return ""


def _int_field(obj: str, names: Tuple[str, ...]) -> int:
    for n in names:
        m = re.search(r'"%s"\s*:\s*(\d+)' % re.escape(n), obj)
        if m:
            return int(m.group(1))
    return 0


def _bool_field(obj: str, names: Tuple[str, ...]) -> bool:
    for n in names:
        m = re.search(r'"%s"\s*:\s*(true|false)' % re.escape(n), obj, re.I)
        if m:
            return m.group(1).lower() == "true"
    return False


def _looks_like_id(value: str) -> bool:
    """内部 ID 长这样：全英文+下划线+数字，且不含中文。"""
    if not value:
        return False
    if re.search(r"[\u4e00-\u9fff]", value):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_\-./ ]{3,}", value))


def _pick_title(obj: str) -> str:
    """挑一个"像人话"的标题。

    很多卡片同时有 name（内部 ID）和 title（真实标题）—— 必须选后者，
    这正是用户反馈"不能用一个固定名字简单替代"的关键。
    """
    candidates = []
    for f in TITLE_FIELDS:
        m = re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"' % re.escape(f), obj)
        if m:
            val = _unescape(m.group(1))
            if val:
                candidates.append((f, val))
    # 含中文 / 含空格的优先（ID 一般既没中文也没空格）
    def score(item):
        field, val = item
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", val))
        has_space = " " in val.strip()
        # title/displayName 本身就是给界面用的，加权
        bonus = 2 if field in ("title", "displayName", "cardTitle", "promotionTitle") else 0
        return (has_cjk, has_space, bonus, -len(val))

    candidates.sort(key=score, reverse=True)
    return candidates[0][1] if candidates else ""


def parse_cards(html: str) -> List[PageCard]:
    """从一段 HTML 里解析所有活动卡片。"""
    if not html:
        return []
    cards: List[PageCard] = []
    seen_titles: set = set()

    for name in CARD_ARRAY_NAMES:
        src = _find_json_array(html, name)
        if not src:
            continue
        for obj in _split_objects(src):
            title = _pick_title(obj)
            offer_id = _field(obj, ID_FIELDS)
            if not title and not offer_id:
                continue
            # 只有内部 ID、没有可读标题的，先记下 ID，标题留空由调用方兜底
            if title and _looks_like_id(title) and not re.search(r"[\u4e00-\u9fff]", title):
                # 这仍是 ID，不是显示名 —— 只有当它是唯一候选时才用
                title = ""
            card = PageCard(
                offer_id=offer_id,
                title=title,
                points=_int_field(obj, POINTS_FIELDS),
                complete=_bool_field(obj, DONE_FIELDS),
                link=_field(obj, LINK_FIELDS),
            )
            key = (card.offer_id or "", card.title or "")
            if key in seen_titles:
                continue
            seen_titles.add(key)
            cards.append(card)
        if cards:
            break  # 命中一个数组就够了，避免把同一批卡片重复收一遍

    if not cards:
        cards = _parse_data_attributes(html)
    return cards


def _parse_data_attributes(html: str) -> List[PageCard]:
    """兜底：新版页面用 data-offer-id / data-hash 属性渲染卡片。"""
    cards: List[PageCard] = []
    for m in re.finditer(r'data-offer-id="([^"]+)"', html):
        offer_id = m.group(1)
        # 在该标签附近找可见文本
        tail = html[m.end():m.end() + 600]
        texts = re.findall(r">([^<>]{2,60})<", tail)
        title = ""
        for t in texts:
            t = _unescape(t).strip()
            if re.search(r"[\u4e00-\u9fff]", t):
                title = t
                break
        cards.append(PageCard(offer_id=offer_id, title=title))
    return cards


def fetch_html(url: str, cookies: Dict[str, str], timeout: int = 25) -> str:
    """用 Cookie 只读 GET 一个页面。"""
    resp = requests.get(
        url,
        cookies=cookies,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": REWARDS_DASHBOARD_URL,
        },
        timeout=timeout,
        allow_redirects=True,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"{url} 返回 {resp.status_code}")
    resp.encoding = resp.encoding or "utf-8"
    return resp.text


def fetch_real_titles(cookies: Dict[str, str], timeout=(3, 5), *,
                      include=("daily", "routine"),
                      with_dashboard_html: bool = False) -> Dict:
    """并行读取需要的官方页面；某个页面失败不影响其他页面。

    使用 requests 的 (连接, 读取) 双超时；只拉取指定页面。可选返回
    dashboard_html 给连续天数提取复用，避免原实现第三次请求同一页面。
    任务完成与积分不从这些 HTML 判定，仅通过 getuserinfo 实时验证。
    """
    result = {"daily": [], "routine": []}
    if with_dashboard_html:
        result["dashboard_html"] = ""
    urls = {
        "daily": REWARDS_DASHBOARD_URL,
        "routine": REWARDS_EARN_URL,
    }
    targets = [key for key in urls if key in include]
    if not targets:
        return result

    # 独立 requests.get 可安全并发（不共享 requests.Session）。
    with ThreadPoolExecutor(max_workers=len(targets), thread_name_prefix="rewards-page") as pool:
        futures = {pool.submit(fetch_html, urls[key], cookies, timeout): key
                   for key in targets}
        for future in as_completed(futures):
            key = futures[future]
            try:
                html = future.result()
                result[key] = parse_cards(html)
                if key == "daily" and with_dashboard_html:
                    result["dashboard_html"] = html
            except Exception:
                # 附助信息不可用时用官方 API 标题兜底，不能妨碍任务同步。
                result[key] = []
    return result


def merge_titles(tasks: List, daily: List[PageCard], routine: List[PageCard],
                 logger=None) -> int:
    """把页面真实标题回填到任务列表上，返回成功替换的条数。

    回填优先级（从可靠到兜底）：
    1. offerId 精确匹配        —— 最可靠，与顺序无关
    2. 同组内按序号对齐         —— daily → 每日任务，routine → 更多活动
    3. 同组内按积分值匹配       —— 序号对不上但分值唯一时可用（只匹配一次，不重复用）
    """
    log = logger or (lambda m: None)
    by_offer: Dict[str, str] = {}
    for card in list(daily) + list(routine):
        if card.offer_id and card.title:
            by_offer.setdefault(card.offer_id.lower(), card.title)

    used_by_points: set = set()
    changed = 0

    for t in tasks:
        if getattr(t, "title_source", "guess") == "api":
            continue  # 接口自带可读标题，最可信，不覆盖
        if getattr(t, "raw_name", "") and _looks_readable(t.raw_name):
            continue

        # 1) offerId 精确匹配
        offer_id = (getattr(t, "offer_id", "") or "").lower()
        if offer_id and offer_id in by_offer:
            t.name = by_offer[offer_id]
            t.title_source = "page"
            changed += 1
            continue

        pool = daily if t.category == "每日任务" else routine
        if not pool:
            continue
        idx = _index_of(t.key)

        # 2) 组内序号对齐
        if 0 <= idx < len(pool) and pool[idx].title:
            t.name = pool[idx].title
            t.title_source = "page"
            changed += 1
            continue

        # 3) 按积分值匹配（组内该分值唯一时才敢用）
        pts = int(getattr(t, "points_max", 0) or 0)
        if pts > 0:
            hits = [c for c in pool if c.title and c.points == pts and id(c) not in used_by_points]
            if len(hits) == 1:
                t.name = hits[0].title
                t.title_source = "page"
                used_by_points.add(id(hits[0]))
                changed += 1

    if changed:
        log(f"已从页面回填 {changed} 个任务的真实名称")
    return changed


def _index_of(key: str) -> int:
    if ":" in key:
        tail = key.rsplit(":", 1)[-1]
        if tail.isdigit():
            return int(tail)
    return -1


def _looks_readable(text: str) -> bool:
    """含中文、或含空格的英文短语，都认为是页面上能看到的人话。"""
    if not text:
        return False
    if re.search(r"[\u4e00-\u9fff]", text):
        return True
    return " " in text.strip() and not text.isupper()

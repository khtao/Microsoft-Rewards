"""只读解析 Rewards /earn 中明确标注的“今日获得积分”。

重要：页面是动态应用，未经登录或侧栏未渲染时可能根本不包含数据。
这种情形返回 None，绝不把任务积分之和代替官方数字。
"""
from __future__ import annotations

import html as html_module
import re
from typing import Optional

EARN_URL = "https://rewards.bing.com/earn"

# 只接受语义明确的当日已得分字段；避免将剩余、上限、可用余额误认成今日积分。
_EXPLICIT_KEYS = (
    "todayPointsEarned", "pointsEarnedToday", "earnedPointsToday",
    "todayEarnedPoints", "dailyEarnedPoints", "todaysEarnedPoints",
)
_LABEL = r"(?:today[’']?s\s+points|points\s+earned\s+today|今日(?:获得|已获|赚取)?积分|今日积分)"


def parse_earn_today_points(source: str) -> Optional[int]:
    """解析 Earn HTML 中已明确标注的今日积分；缺失/歧义一律返回 None。"""
    if not source or not isinstance(source, str):
        return None
    text = html_module.unescape(source)
    matches: list[int] = []
    for key in _EXPLICIT_KEYS:
        key_pat = re.escape(key)
        for pattern in (
            rf'["\']{key_pat}["\']\s*:\s*["\']?([0-9]{{1,7}})["\']?',
            rf'{key_pat}\\?"?\s*:\s*([0-9]{{1,7}})',
        ):
            matches += [int(x) for x in re.findall(pattern, text, flags=re.I)]
    # 明确的可见文字、ARIA 标签（包括“今日积分：42”）。
    for pattern in (
        rf'{_LABEL}\s*[:：\-]?\s*([0-9]{{1,7}})\s*(?:积分|points)?',
        rf'([0-9]{{1,7}})\s*(?:points|积分)\s*(?:earned\s+today|今日获得)',
    ):
        matches += [int(x) for x in re.findall(pattern, text, flags=re.I)]
    # 某些客户端渲染版本会在指定卡片内部嵌套数值元素。
    # 只有显式 today-points 标记且内部恰有一个整数时才接受，避免拾取其他卡片数据。
    for hit in re.finditer(
        r'<(?:section|div|button|article)\b[^>]*data-testid\s*=\s*["\']'
        r'(?:today[-_]?points|todays[-_]?points|daily[-_]?earned[-_]?points)'
        r'["\'][^>]*>', text, re.I,
    ):
        near = text[hit.end():hit.end() + 450]
        # 解析到同一视觉卡片范围时最多一处数值；否则标记来源不明确。
        flat = re.sub(r'<[^>]*>', ' ', near)
        flat = re.sub(r'\s+', ' ', flat)
        nums = [int(x) for x in re.findall(r'(?<!\d)\d{1,7}(?!\d)', flat[:110])]
        if len(nums) == 1:
            matches.append(nums[0])
    uniq = set(matches)
    # 矛盾来源时不猜；积分 0 是合法结果。
    return next(iter(uniq)) if len(uniq) == 1 else None


def parse_earn_breakdown_text(text: str) -> Optional[int]:
    """只解析用户点击 Today's Points 后弹出的积分明细面板。"""
    if not text:
        return None
    match = re.search(rf'{_LABEL}\s*[:：\-]?\s*([0-9]{{1,7}})', text, re.I)
    if match:
        return int(match.group(1))
    # 新版积分明细按 Today / This month / This year 列出。
    # 要求同时出现月份行，以防页面其他位置的 Today 被误认成积分。
    if re.search(r'\b(?:this month|本月)\b', text, re.I):
        match = re.search(r'(?:^|\n)\s*(?:today|今天)\s*[:：\-]?\s*([0-9]{1,7})\b', text, re.I)
        if match:
            return int(match.group(1))
    return None


def fetch_today_points_in_edge(cookie_jar: dict[str, str], timeout_ms: int = 9000) -> Optional[int]:
    """仅在普通 GET 未含官方今日积分时，临时使用 Edge 渲染 Earn 页。

    不保存 profile，不复用浏览器进程；所有路径都会关闭 Chromium/Playwright。
    这不是静默后台常驻浏览器。微软登录态或页面结构变化时返回 None。
    """
    if not cookie_jar:
        return None
    from playwright.sync_api import sync_playwright

    # 避免把大额 JSON、页面源代码与 Cookie 记录进运行日志。
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True,
                                             args=["--disable-extensions"])
        try:
            context = browser.new_context()
            try:
                context.add_cookies([
                    {"name": str(k), "value": str(v), "url": EARN_URL}
                    for k, v in cookie_jar.items() if k and v
                ])
                page = context.new_page()
                page.set_default_timeout(min(timeout_ms, 6000))
                page.goto(EARN_URL, wait_until="domcontentloaded", timeout=timeout_ms)
                # 先尝试卡片（可能已经渲染积分）；若只显示入口，点击展开明细。
                text = page.locator("body").inner_text(timeout=timeout_ms)
                points = parse_earn_today_points(text)
                if points is not None:
                    return points
                locator = page.get_by_text(
                    re.compile(r"Today[’']?s\s+points|今日(?:获得)?积分", re.I)
                ).first
                locator.click(timeout=min(timeout_ms, 3500))
                page.wait_for_timeout(350)
                # 优先解析积分明细区域，避免混淆主页活动卡片中的数字。
                for selector in (
                    '[role="dialog"]',
                    '[data-testid*="points-breakdown"]',
                    '[class*="drawer"]',
                    'aside',
                ):
                    for node in page.locator(selector).all()[:8]:
                        if node.is_visible():
                            result = parse_earn_breakdown_text(node.inner_text())
                            if result is not None:
                                return result
                return parse_earn_today_points(page.locator("body").inner_text())
            finally:
                context.close()
        finally:
            browser.close()

"""在 Bing Rewards 侧栏中定位任务并产生真实鼠标事件（纯 Python，可独立测试）。

不以 page.goto(活动落地链接) 代替点击；不做积分或任务的本地伪完成。
页面结构随地区、账户和版本变化，找不到唯一可信目标时返回未点击。
"""
from __future__ import annotations

import random
import time
from urllib.parse import urlparse

BING_ENTRY = "https://cn.bing.com/?scope=web&FORM=ANNTH1&pc=CNNDDB"
FLYOUT_URL = "https://www.bing.com/rewards/panelflyout?channel=bingflyout&partnerId=BingRewards"
REWARDS_DASHBOARD = "https://rewards.bing.com/dashboard"
REWARDS_EARN = "https://rewards.bing.com/earn"

# Bing 顶部积分入口在不同布局中的常见标记；限定可见元素，拒绝任意链接。
REWARDS_TRIGGERS = (
    ".points-container", "#id_rc", "#id_rh", "#bingRewards",
    "[aria-label='Microsoft Rewards']", "[title='Microsoft Rewards']",
    "[data-testid='rewards-button']",
)


def task_titles(task) -> list[str]:
    """原始名称 + 经过官方页面回填后的名称，不使用猜测的任务编号。"""
    titles = []
    for value in (getattr(task, "name", ""), getattr(task, "raw_name", "")):
        value = str(value or "").strip()
        if len(value) >= 3 and value not in titles and not value.startswith(("dailyset:", "more:")):
            titles.append(value)
    return titles


def _visible(locator) -> bool:
    try:
        return locator.is_visible(timeout=800)
    except Exception:
        return False


def _first_visible(locator, maximum: int = 8):
    try:
        for i in range(min(locator.count(), maximum)):
            result = locator.nth(i)
            if _visible(result):
                return result
    except Exception:
        pass
    return None


def _frames(page):
    try:
        return list(page.frames)
    except Exception:
        return [page.main_frame]


def locate_activity(page, task):
    """在页面及跨域 iframe 查找活动卡片，优先用 offerId 和准确标题。"""
    offer_id = str(getattr(task, "offer_id", "") or "").strip()
    link = str(getattr(task, "link", "") or "").strip()
    for frame in _frames(page):
        # 每日任务集在 Bing 侧栏中常只显示三个圆形图标，没有文字标题。
        # 仅对明确的 dailyset 索引使用官方面板的三个 Offer 顺序定位。
        # aria-label 明确已完成的项绝不再点。
        key = str(getattr(task, "key", "") or "")
        if key.startswith("dailyset:"):
            try:
                index = int(key.partition(":")[2])
            except ValueError:
                index = -1
            if 0 <= index < 3:
                for selector in (
                    "#bingRewards .flyout_control_threeOffers div[aria-label*='Offer']",
                    ".flyout_control_threeOffers div[aria-label*='Offer']",
                ):
                    offers = frame.locator(selector)
                    try:
                        if offers.count() != 3:
                            continue
                        candidate = offers.nth(index)
                        label = (candidate.get_attribute("aria-label") or "").lower()
                        if ("offer not completed" in label and _visible(candidate)):
                            return candidate
                    except Exception:
                        continue
        if offer_id:
            # 限制 CSS 属性值中的特殊字符，防止拼接出任意选择器。
            from json import dumps
            val = dumps(offer_id)
            for selector in (f'[data-offer-id={val}]', f'[data-offerid={val}]',
                             f'[data-promotion-id={val}]', f'[data-testid={val}]'):
                loc = _first_visible(frame.locator(selector))
                if loc is not None:
                    return loc
        if link and urlparse(link).scheme == "https":
            from json import dumps
            loc = _first_visible(frame.locator(f'a[href={dumps(link)}]'))
            if loc is not None:
                return loc
        for name in task_titles(task):
            # 准确的标题通常是卡片内 span/h3；点击会冒泡到父级卡片。
            loc = _first_visible(frame.get_by_text(name, exact=True))
            if loc is not None:
                return loc
    return None


def _has_task(page, tasks) -> bool:
    return any(locate_activity(page, task) is not None for task in tasks)


def open_rewards_panel(page, tasks, *, stop=lambda: False, log=lambda _: None) -> bool:
    """优先打开所给 Bing 页的侧栏；必要时在 Rewards 官方 UI 内回退。"""
    if stop():
        return False
    page.goto(BING_ENTRY, wait_until="domcontentloaded", timeout=25000)
    page.wait_for_timeout(1500)
    if _has_task(page, tasks):
        return True
    for selector in REWARDS_TRIGGERS:
        if stop():
            return False
        target = _first_visible(page.locator(selector))
        if target is None:
            continue
        try:
            target.click(timeout=2500)  # Playwright 生成真实鼠标事件，而非 element.click() 脚本
            page.wait_for_timeout(1600)
            if _has_task(page, tasks):
                log("已打开 Bing Rewards 侧栏")
                return True
        except Exception:
            continue

    # 某些地区顶部入口是扩展专有元素；尝试官方面板本身，再退回官网页面。
    for url in (FLYOUT_URL, REWARDS_DASHBOARD, REWARDS_EARN):
        if stop():
            return False
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1300)
            if _has_task(page, tasks):
                log(f"已在官方页面找到活动卡片：{urlparse(url).path}")
                return True
        except Exception as exc:
            log(f"打开官方任务界面失败：{type(exc).__name__}")
    return False


def click_activity(page, task, *, rng=None) -> bool:
    """鼠标移动、按下、抬起。找不到元素时不访问原始任务链接。"""
    target = locate_activity(page, task)
    if target is None:
        return False
    rng = rng or random.Random()
    target.scroll_into_view_if_needed(timeout=3500)
    box = target.bounding_box()
    if box is None or box["width"] < 1 or box["height"] < 1:
        return False
    # locator.bounding_box 为主视口坐标（包括 iframe 的位移）。
    x = box["x"] + box["width"] * rng.uniform(.3, .7)
    y = box["y"] + box["height"] * rng.uniform(.3, .7)
    page.mouse.move(x, y, steps=rng.randint(6, 16))
    time.sleep(rng.uniform(.08, .2))
    page.mouse.down()
    time.sleep(rng.uniform(.04, .1))
    page.mouse.up()
    return True


def confirmed_completed(original, current) -> bool:
    """严格按官方 offerId / 当日任务 key 对照，不把“点击成功”等同完成。"""
    old_id = str(getattr(original, "offer_id", "") or "")
    for task in getattr(current, "tasks", ()):
        if old_id and old_id == str(getattr(task, "offer_id", "") or ""):
            return bool(task.complete or (task.points_max > 0 and task.points_earned >= task.points_max))
        # API 老版本没有 offerId 时，索引与原始活动名称必须一起匹配。
        if not old_id and task.key == original.key and task.raw_name == original.raw_name:
            return bool(task.complete or (task.points_max > 0 and task.points_earned >= task.points_max))
    return False

# -*- coding: utf-8 -*-
"""拟人行为库 + 时间预算。

两件事：
1. 拟人：所有操作都带噪声——时间抖动、贝塞尔鼠标轨迹、偶发多余动作。
   关键词一律**复制粘贴**进输入框（Ctrl+V），不做逐字敲击。
2. 预算：一轮搜索必须在 N 秒内结束，且 N 随机。所有停顿都先问 Budget 还剩多少，
   时间不够就压缩甚至跳过可选动作（滚动、点结果），保证不超时。

level 1~3 控制拟人强度：
  1 = 机械但可用（对照组）
  2 = 默认，接近普通用户
  3 = 高拟人，抖动大、动作慢、会犹豫
"""
from __future__ import annotations

import random
import time

PROFILES = {
    1: dict(paste=(0.10, 0.30), overshoot=0.00,
            steps=(4, 10), mouse=(0.004, 0.02), scroll=(0.2, 0.8), read=(1.0, 4.0), wander=0.0),
    2: dict(paste=(0.25, 0.80), overshoot=0.22,
            steps=(12, 30), mouse=(0.004, 0.02), scroll=(0.4, 1.6), read=(3.0, 10.0), wander=0.35),
    3: dict(paste=(0.40, 1.40), overshoot=0.40,
            steps=(24, 52), mouse=(0.006, 0.03), scroll=(0.6, 2.8), read=(6.0, 25.0), wander=0.75),
}


class Budget:
    """一轮搜索的时间预算：deadline 驱动，所有停顿都要先过它。

    total      本轮总时长（秒），由外部随机给定
    reserve    必须留给后续阶段的时间，低于它就别再做可选动作
    """

    def __init__(self, total: float, rng: random.Random, reserve: float = 0.0):
        self.total = float(total)
        self.rng = rng
        self.reserve = reserve
        self.t0 = time.time()

    @property
    def elapsed(self) -> float:
        return time.time() - self.t0

    @property
    def left(self) -> float:
        return self.total - self.elapsed

    def usable(self, reserve: float | None = None) -> float:
        """当前还能自由支配的时间。"""
        return self.left - (self.reserve if reserve is None else reserve)

    def fits(self, need: float, reserve: float | None = None) -> bool:
        return self.usable(reserve) > need

    def sleep(self, a: float, b: float, reserve: float | None = None,
              share: float = 0.7) -> float:
        """在 [a,b] 里随机等一会儿，但最多只吃掉剩余可用时间的 share。

        返回实际睡眠时长（用于日志）。预算耗尽时直接返回 0，不阻塞。
        """
        want = self.rng.uniform(a, b)
        room = self.usable(reserve)
        if room <= 0:
            return 0.0
        t = min(want, room * share, room)
        if t > 0:
            time.sleep(t)
        return t

    def sleep_exact(self, seconds: float) -> float:
        """需要精确耗时的场景，只做上限裁剪，不做比例压缩。"""
        t = max(0.0, min(seconds, self.left))
        if t > 0:
            time.sleep(t)
        return t


class Human:
    def __init__(self, level: int = 2, seed: int | None = None, budget: Budget | None = None):
        self.level = max(1, min(3, level))
        self.p = PROFILES[self.level]
        self.rng = random.Random(seed)
        self.budget = budget

    def bind(self, budget: Budget | None):
        self.budget = budget
        return self

    # ---------- 基础：随机等待 ----------
    def sleep(self, a: float, b: float, reserve=None, share: float = 0.7) -> float:
        if self.budget is None:
            t = self.rng.uniform(a, b)
            time.sleep(t)
            return t
        return self.budget.sleep(a, b, reserve=reserve, share=share)

    def pause(self, a: float, b: float, reserve=None) -> float:
        """带一点走神倾向的停顿，模拟人的反应时间。"""
        base = self.rng.uniform(a, b)
        if self.rng.random() < 0.15:
            base += self.rng.uniform(0.3, 1.8)
        if self.budget is None:
            time.sleep(base)
            return base
        room = self.budget.usable(reserve)
        t = min(base, room * 0.7, room) if room > 0 else 0.0
        if t > 0:
            time.sleep(t)
        return t

    def read_pause(self, reserve=None) -> float:
        return self.pause(*self.p["read"], reserve=reserve)

    # ---------- 鼠标 ----------
    def _bezier(self, x0, y0, x1, y1, steps: int):
        """二次贝塞尔 + 轻微手抖，生成一条不像直线的轨迹。"""
        cx = x0 + (x1 - x0) * self.rng.uniform(0.2, 0.5) + self.rng.uniform(-60, 60)
        cy = y0 + (y1 - y0) * self.rng.uniform(0.1, 0.6) + self.rng.uniform(-40, 40)
        pts = []
        n = max(2, steps)
        for i in range(n + 1):
            t = i / n
            t = t * t * (3 - 2 * t)                      # ease-in-out，符合肌肉运动
            x = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t ** 2 * x1
            y = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t ** 2 * y1
            if i not in (0, n):
                x += self.rng.uniform(-1.5, 1.5)
                y += self.rng.uniform(-1.5, 1.5)
            pts.append((x, y))
        return pts

    def _min_jerk(self, t: float) -> float:
        """最小急动度模型（Flash & Hogan）：人手目标导向运动的经典速度剖面。

        速度是单峰钟形——起步慢、中间最快、接近目标减速，而不是对称的 ease-in-out。
        """
        return 10 * t ** 3 - 15 * t ** 4 + 6 * t ** 5

    def move_to(self, page, x: float, y: float, from_xy=None, quick: bool = False) -> tuple:
        steps = self.rng.randint(*self.p["steps"])
        if quick or (self.budget is not None and self.budget.left < 6.0):
            steps = max(3, steps // 4)
        if from_xy is None:
            from_xy = (self.rng.uniform(0, 200), self.rng.uniform(0, 200))

        pts = self._bezier(from_xy[0], from_xy[1], x, y, steps)
        lo, hi = self.p["mouse"]
        n = len(pts) - 1
        for i, (px, py) in enumerate(pts):
            page.mouse.move(px, py)
            # 按最小急动度的「速度倒数」分配每步间隔：峰速度处间隔小，两端大
            t = (i + 0.5) / n
            v = 30 * t ** 2 - 60 * t ** 3 + 30 * t ** 4      # d(min_jerk)/dt
            dt = self.rng.uniform(lo, hi) * (1.0 / max(0.25, v * 1.6))
            time.sleep(min(dt, hi * 3))

        # 过冲修正：真人瞄不准会冲过去一点再拉回来，偶尔还会二次微调
        if not quick and self.rng.random() < self.p.get("overshoot", 0.0):
            dist = ((x - from_xy[0]) ** 2 + (y - from_xy[1]) ** 2) ** 0.5
            if dist > 80:
                ux, uy = (x - from_xy[0]) / dist, (y - from_xy[1]) / dist
                over = self.rng.uniform(4, 18)
                page.mouse.move(x + ux * over, y + uy * over)
                time.sleep(self.rng.uniform(0.02, 0.06))
                page.mouse.move(x + self.rng.uniform(-2, 2), y + self.rng.uniform(-2, 2))
                time.sleep(self.rng.uniform(0.03, 0.10))
        return (x, y)

    def move_to_box(self, page, box: dict, last_xy=None, quick: bool = False) -> tuple:
        """移动到元素内的随机一点（真人不会每次都点正中心）。"""
        x = box["x"] + box["width"] * self.rng.uniform(0.25, 0.75)
        y = box["y"] + box["height"] * self.rng.uniform(0.3, 0.7)
        return self.move_to(page, x, y, last_xy, quick=quick)

    def human_click(self, page, locator, last_xy=None, before_click_pause=True,
                    quick: bool = False):
        box = locator.bounding_box()
        if box is None:
            locator.click()
            return last_xy
        xy = self.move_to_box(page, box, last_xy, quick=quick)
        if before_click_pause:
            time.sleep(self.rng.uniform(0.05, 0.30))     # 移动到位后的确认停顿
        page.mouse.down()
        time.sleep(self.rng.uniform(0.02, 0.10))         # 按下时长
        page.mouse.up()
        return xy

    def wander(self, page, w=1440, h=900, n=None, max_seconds: float | None = None):
        """无目的鼠标游走，模拟阅读/发呆时的手部动作。

        带预算时最多只占用剩余可用时间的一小段（默认 0~2.5s），到点立刻收手。
        """
        if self.rng.random() > self.p["wander"]:
            return None
        if max_seconds is None and self.budget is not None:
            max_seconds = max(0.0, min(2.5, self.budget.usable() * 0.2))
        deadline = time.time() + max_seconds if max_seconds is not None else None
        n = n or self.rng.randint(2, 6)
        last = (self.rng.uniform(0, w), self.rng.uniform(0, h))
        for _ in range(n):
            if deadline is not None and time.time() > deadline:
                break
            tx = self.rng.uniform(0.1 * w, 0.9 * w)
            ty = self.rng.uniform(0.1 * h, 0.9 * h)
            last = self.move_to(page, tx, ty, last)
            if deadline is not None:
                time.sleep(min(self.rng.uniform(0.05, 0.35), max(0.0, deadline - time.time())))
            else:
                time.sleep(self.rng.uniform(0.05, 0.35))
        return last

    # ---------- 滚动阅读（受预算约束） ----------
    def scroll_read(self, page, screens: float = 1.5, max_seconds: float | None = None,
                    reserve: float = 1.5):
        """分段滚动 + 停顿，偶尔回头看一眼；时间不够会提前收手。"""
        t_end = None
        if max_seconds is not None:
            t_end = time.time() + max_seconds
        step = self.rng.randint(220, 620)
        total = int(screens * 900)
        moved = 0
        while moved < total:
            if t_end is not None and time.time() > t_end:
                break
            if self.budget is not None and not self.budget.fits(1.0, reserve=reserve):
                break
            d = step if self.rng.random() < 0.85 else -self.rng.randint(80, 260)
            page.mouse.wheel(0, d)
            moved += abs(d)
            if t_end is not None:
                time.sleep(min(self.rng.uniform(*self.p["scroll"]), max(0.0, t_end - time.time())))
            else:
                self.sleep(*self.p["scroll"], reserve=reserve)
            if self.rng.random() < 0.12:
                self.wander(page)
        if self.rng.random() < 0.4 and (self.budget is None or self.budget.fits(2.0, reserve=reserve)):
            page.mouse.wheel(0, -self.rng.randint(120, 400))
            self.sleep(0.5, 1.5, reserve=reserve)

    # ---------- 输入：复制粘贴 ----------
    def paste_text(self, page, locator, text: str, seconds: float | None = None) -> bool:
        """把整段关键词一次性粘贴进输入框（Ctrl+V）。

        真人在搜索引擎里换词时复制粘贴很常见；这里保留
        「写剪贴板 → 停顿 → Ctrl+V → 停顿」的节奏，而不是无延迟 insertText
        （后者在事件层面等同于脚本注入，多数风控一眼识破）。
        seconds 非空时整体压缩到这个时长内。返回是否走通了剪贴板路径。
        """
        lo, hi = self.p["paste"]
        pre = self.rng.uniform(lo, hi)
        gap = self.rng.uniform(lo, hi)
        if seconds:
            pre = min(pre, max(0.05, seconds * 0.35))
            gap = min(gap, max(0.05, seconds * 0.35))
        time.sleep(pre)

        try:
            page.evaluate("t => navigator.clipboard.writeText(t)", text)
        except Exception:
            page.keyboard.insert_text(text)          # 剪贴板不可用时的替代
            time.sleep(self.rng.uniform(0.05, 0.2))
            return False
        time.sleep(gap)
        try:
            page.keyboard.press("Control+V")
        except Exception:
            page.keyboard.insert_text(text)
            return False
        time.sleep(self.rng.uniform(lo, hi) * 0.5)
        return True

    # ---------- 输入自检 ----------
    def fix_input(self, page, locator, expected: str) -> bool:
        """粘贴后自检：剪贴板被占用 / 页面拦截都可能让内容不对，错了就重来一次。"""
        try:
            value = locator.input_value()
        except Exception:
            return False
        if value == expected:
            return True
        try:
            locator.click()
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            time.sleep(self.rng.uniform(0.1, 0.3))
            page.keyboard.insert_text(expected)
            time.sleep(self.rng.uniform(0.05, 0.2))
            return locator.input_value() == expected
        except Exception:
            return False

    def idle(self, seconds: float, page=None, stop=None):
        """两轮之间的空档：人不会立刻搜下一个词。

        stop 是一个无参 callable，返回 True 就立刻收尾（GUI 停止按钮用）。
        """
        end = time.time() + seconds
        while time.time() < end:
            chunk = min(self.rng.uniform(2.0, 8.0), end - time.time())
            if chunk <= 0:
                break
            time.sleep(chunk)
            if stop is not None and stop():
                return
            if page is not None and self.rng.random() < 0.5:
                try:
                    self.wander(page, n=self.rng.randint(1, 3), max_seconds=1.5)
                except Exception:
                    return

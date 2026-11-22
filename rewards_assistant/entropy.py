# -*- coding: utf-8 -*-
"""行为节奏与熵值：用真实人类时间分布替换均匀随机。

为什么不能用 uniform：
  真人操作间隔是**乘性过程**（大脑按倍数估计时间，不是按加性噪声），
  实测分布接近对数正态，且带帕累托长尾（偶尔发呆很久）；
  连续动作之间还存在**自相似的 1/f 相关性**——成串操作后长时间静默，
  而不是独立同分布。uniform 随机熵值偏低、无长尾、无相关性，
  在统计检验（KS 检验、自相关函数、Hurst 指数）上很容易被区分。

本模块提供：
  · lognormal / pareto / weibull / gamma  —— 单段时间分布
  · PinkNoise                             —— 1/f 粉红噪声，制造自相似节奏
  · Cadence                               —— 带状态的时间采样器（一次会话内相关）
  · ReadingModel                          —— 阅读时长 ∝ 内容长度（中文 CPM）
  · Circadian                             —— 昼夜节律（按当地时间调整快慢）
  · Poisson                               —— 零星微动作的到达过程
"""
from __future__ import annotations

import math
import random
import time
from datetime import datetime, timezone, timedelta

# ------------------------------------------------------------------ 单段分布

def lognormal(rng: random.Random, median: float, sigma: float = 0.45) -> float:
    """对数正态：median 是中位时长，sigma 控制离散度（越大长尾越重）。"""
    if median <= 0:
        return 0.0
    return rng.lognormvariate(math.log(median), max(0.01, sigma))


def pareto(rng: random.Random, xm: float, alpha: float = 1.6) -> float:
    """帕累托：xm 是最小值，alpha 越小长尾越重。用来偶发一次超长停留。"""
    return xm / (rng.random() ** (1.0 / max(0.1, alpha)))


def weibull(rng: random.Random, scale: float, shape: float = 1.5) -> float:
    """韦布尔：反应时间 / 完成时长常用模型。shape<1 递减风险，>1 递增。"""
    return rng.weibullvariate(max(1e-6, scale), max(0.1, shape))


def bounded(rng: random.Random, sampler, low: float, high: float, tries: int = 6):
    """把任意重尾分布截到 [low, high]，重采样而非硬截断（硬截断会在边界堆积）。"""
    for _ in range(tries):
        v = sampler()
        if low <= v <= high:
            return v
    return min(high, max(low, sampler()))


# ------------------------------------------------------------------ 1/f 噪声

class PinkNoise:
    """Voss-McCartney 粉红噪声：输出近似 N(0,1)，但功率谱 ~ 1/f。

    用它调制时长指数，就得到"自相似"的节奏——
    一段时间密集操作，一段时间安静，而不是均匀铺开。
    """

    def __init__(self, rng: random.Random, octaves: int = 6):
        self.rng = rng
        self.octaves = max(2, octaves)
        self.state = [rng.gauss(0.0, 1.0) for _ in range(self.octaves)]
        self.counter = 0
        self._norm = math.sqrt(self.octaves)

    def next(self) -> float:
        # 找到最低位的 0，替换对应八度 —— 这就是 Voss 的更新规则
        i = 0
        while i < self.octaves and (self.counter >> i) & 1 == 0:
            self.state[i] = self.rng.gauss(0.0, 1.0)
            i += 1
        self.counter = (self.counter + 1) & 0xFFFFFFFF
        return sum(self.state) / self._norm

    def reset(self):
        self.counter = 0
        self.state = [self.rng.gauss(0.0, 1.0) for _ in range(self.octaves)]


class Cadence:
    """会话级节奏采样器：整段会话共享一个粉红噪声状态，动作间隔彼此相关。

    sigma   相关性强度（0 = 退化为独立对数正态，0.6 ≈ 明显成串）
    drift   随会话推进的缓慢疲劳（越往后越慢），模拟注意力衰减
    """

    def __init__(self, rng: random.Random, sigma: float = 0.45, drift: float = 0.04,
                 octaves: int = 6, session_len: int = 12, drift_cap: float = 1.22):
        self.rng = rng
        self.sigma = sigma
        self.drift = drift
        self.session_len = max(1, session_len)
        self.drift_cap = drift_cap
        self.noise = PinkNoise(rng, octaves)
        self.n = 0
        # 除掉 E[exp(sigma*N)] = exp(sigma²/2)，保证中位数不被噪声整体抬高
        self._noise_bias = math.exp(-sigma * sigma / 2.0)

    def interval(self, median: float, low: float | None = None,
                 high: float | None = None, tail_p: float = 0.05) -> float:
        """按中位时长取一个间隔；low/high 为空时按中位数的合理倍数自动放宽。"""
        if median <= 0:
            return 0.0
        lo = low if low is not None else median * 0.35
        hi = high if high is not None else median * 4.5
        # 粉红噪声调制指数 → 对数正态 + 1/f 相关（已做偏置校正，中位数保持 median）
        v = lognormal(self.rng, median, self.sigma * 0.5) * math.exp(
            self.sigma * self.noise.next()) * self._noise_bias
        # 疲劳漂移：按"会话进度"缓慢变慢，封顶避免长会话整体失控
        prog = min(1.0, self.n / self.session_len)
        v *= 1.0 + (self.drift_cap - 1.0) * prog
        self.n += 1
        # 偶发超长停留（走神 / 被别的事打断）；用重尾但量级收敛的分布
        if self.rng.random() < tail_p:
            v += min(pareto(self.rng, median * 1.2, alpha=2.6), median * 6.0)
        return float(bounded(self.rng, lambda: v, lo, hi))

    def sleep(self, median: float, low: float | None = None, high: float | None = None,
              tail_p: float = 0.05, stop=None, cap: float | None = None) -> float:
        """取间隔并真的睡过去；cap 硬性封顶（时间预算用），stop 可中断。"""
        t = self.interval(median, low, high, tail_p)
        if cap is not None:
            t = min(t, max(0.0, cap))
        if t <= 0:
            return 0.0
        end = time.time() + t
        while True:
            now = time.time()
            if now >= end:
                break
            time.sleep(min(0.25, end - now))
            if stop is not None and stop():
                return time.time() - (end - t)
        return t


# ------------------------------------------------------------------ 阅读模型

class ReadingModel:
    """阅读时长 ∝ 内容长度。

    中文扫读约 400~700 字/分钟，细读 200~350 字/分钟；
    真人经常只看标题和前两行就走，所以这里用"扫读 + 概率细读"。
    """

    def __init__(self, rng: random.Random, cpm_scan=(420, 700), cpm_deep=(200, 340),
                 deep_p: float = 0.25, floor: float = 1.5, ceil: float = 90.0,
                 attention=(0.25, 0.60)):
        self.rng = rng
        self.cpm_scan = cpm_scan
        self.cpm_deep = cpm_deep
        self.deep_p = deep_p
        self.floor = floor
        self.ceil = ceil
        self.attention = attention

    def dwell(self, chars: int, floor: float | None = None, ceil: float | None = None) -> float:
        floor = self.floor if floor is None else floor
        ceil = self.ceil if ceil is None else ceil
        chars = max(0, int(chars))
        if chars == 0:
            return self.rng.uniform(floor, floor * 2)
        deep = self.rng.random() < self.deep_p
        lo, hi = self.cpm_deep if deep else self.cpm_scan
        cpm = self.rng.uniform(lo, hi)
        # 真人不会读完整个页面：只有一部分内容真正进入视野
        t = chars * self.rng.uniform(*self.attention) / cpm * 60.0
        # 长文递减：人不会线性读完，后面的内容扫得更快
        if chars > 1200:
            t *= 0.75
        t = lognormal(self.rng, t, sigma=0.35)
        return float(min(ceil, max(floor, t)))


# ------------------------------------------------------------------ 昼夜节律

class Circadian:
    """按**当地**时间给出节奏系数：深夜慢、上午快、午后平缓。

    同时给出"这个时段用户倾向于搜几轮"的权重，供调度使用。
    """

    # 小时 -> (速度系数, 活跃度)
    CURVE = {
        0: (1.35, 0.35), 1: (1.40, 0.25), 2: (1.45, 0.18), 3: (1.45, 0.14),
        4: (1.40, 0.15), 5: (1.30, 0.22), 6: (1.20, 0.40), 7: (1.10, 0.65),
        8: (1.00, 0.85), 9: (0.92, 1.00), 10: (0.90, 1.00), 11: (0.92, 0.95),
        12: (0.98, 0.85), 13: (1.02, 0.80), 14: (1.00, 0.90), 15: (0.98, 0.95),
        16: (0.96, 1.00), 17: (0.95, 0.95), 18: (0.98, 0.90), 19: (1.00, 0.95),
        20: (1.05, 1.00), 21: (1.10, 1.00), 22: (1.18, 0.90), 23: (1.28, 0.65),
    }

    def __init__(self, tz_offset_hours: float | None = None):
        self.offset = tz_offset_hours

    def local_hour(self) -> int:
        if self.offset is None:
            return datetime.now().hour
        return datetime.now(timezone.utc).astimezone(
            timezone(timedelta(hours=self.offset))).hour

    def speed(self) -> float:
        """>1 表示更慢（疲惫），<1 表示更快（专注）。"""
        h = self.local_hour()
        base = self.CURVE.get(h, (1.0, 1.0))[0]
        return base

    def activity(self) -> float:
        """0~1，这个时段的活跃程度，可用于决定本轮搜几轮。"""
        return self.CURVE.get(self.local_hour(), (1.0, 1.0))[1]

    def describe(self) -> str:
        h = self.local_hour()
        return f"当地 {h:02d} 时（速度 x{self.speed():.2f}，活跃 {self.activity():.2f}）"


# ------------------------------------------------------------------ 泊松过程

class Poisson:
    """零星微动作（鼠标抖动、偶尔回看）的到达过程：单位时间 λ 个。"""

    def __init__(self, rng: random.Random, rate: float = 0.6):
        self.rng = rng
        self.rate = rate

    def wait_next(self, cap: float = 30.0) -> float:
        """到下一个事件的时间间隔（指数分布）。"""
        if self.rate <= 0:
            return cap
        return min(cap, self.rng.expovariate(self.rate))

    def count(self, seconds: float) -> int:
        """seconds 秒内大约发生几个事件。"""
        n = 0
        acc = 0.0
        while acc < seconds:
            acc += self.wait_next()
            if acc < seconds:
                n += 1
        return n

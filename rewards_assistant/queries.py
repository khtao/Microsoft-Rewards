# -*- coding: utf-8 -*-
"""查询内容质量：把热搜词改写成"人话"，并组织成会话链。

问题：热搜榜的词大多是不完整的片段（人名、片名、两个字的梗），
真人几乎不会直接拿它当查询 —— 直接搜会显得非常机械，且长度分布明显偏短。

真实查询的形态：
  · 自然语言提问：xxx 是什么意思 / xxx 怎么关闭 / 怎么 xxx
  · 意图修饰：官网 / 价格 / 攻略 / 教程 / 推荐 / 下载 / 时间 / 地址
  · 时效修饰：最新 / 2026 / 今天
  · 本地化：{城市} + 词
  · 长尾：主词 + 限定语（越往后越长）
  · 会话链：同一个话题下连续几次搜索，逐步收窄或换角度

本模块：
  · QueryBuilder —— 按意图分布把种子词改写成自然查询
  · SessionChain —— 生成有语义关联的连续查询（宽 → 窄 → 换角度）
  · quality()    —— 给出查询质量指标（长度分布、重复率、长尾率）
"""
from __future__ import annotations

import random
import re

# ------------------------------------------------------------------ 中文模板
# (模板, 权重)。{s}=种子词，{c}=城市
CN_TEMPLATES = [
    ("{s}", 0.14),              # 原词：热搜词本身确实常被直接搜
    ("{s} 最新消息", 0.07),
    ("{s} 是什么意思", 0.06),
    ("{s} 怎么样", 0.06),
    ("{s} 为什么", 0.05),
    ("{s} 推荐", 0.05),
    ("{s} 攻略", 0.04),
    ("{s} 教程", 0.04),
    ("{s} 多少钱", 0.04),
    ("{s} 怎么弄", 0.04),
    ("{s} 官网", 0.04),
    ("{s} 价格", 0.03),
    ("{s} 是真的吗", 0.03),
    ("{s} 有哪些", 0.03),
    ("{s} 附近", 0.03),
    ("{s} 时间", 0.02),
    ("{s} 地址", 0.02),
    ("{s} 详情", 0.03),
    ("{s} 2026", 0.03),
    ("怎么 {s}", 0.03),
    ("{c}{s}", 0.04),           # 本地化：广州xxx
    ("{s} {c}", 0.02),
]

# 英文模板（非中文地区）
EN_TEMPLATES = [
    ("{s}", 0.20),
    ("{s} news", 0.08),
    ("what is {s}", 0.07),
    ("{s} review", 0.07),
    ("{s} price", 0.06),
    ("how to {s}", 0.07),
    ("{s} near me", 0.06),
    ("best {s}", 0.07),
    ("{s} official site", 0.05),
    ("{s} tutorial", 0.05),
    ("{s} 2026", 0.04),
    ("{s} meaning", 0.04),
    ("{s} guide", 0.05),
    ("{s} vs", 0.04),
    ("{c} {s}", 0.05),
]

# 会话链：后续查询相对上一轮的形态
FOLLOWUP_CN = [
    ("refine", 0.34),    # 收窄：在上一轮基础上加限定
    ("aspect", 0.30),    # 换角度：同一话题换一个修饰
    ("requery", 0.22),   # 换个说法重搜
    ("new", 0.14),       # 彻底换话题
]
REFINE_CN = ["官方", "时间", "价格", "地址", "教程", "最新", "详情", "怎么弄", "是真的吗"]
ASPECT_CN = ["是什么意思", "怎么样", "为什么", "推荐", "攻略", "有哪些", "多少钱", "附近", "官网"]

FOLLOWUP_EN = [
    ("refine", 0.34), ("aspect", 0.30), ("requery", 0.22), ("new", 0.14),
]
REFINE_EN = ["official", "price", "time", "location", "how to", "review", "2026"]
ASPECT_EN = ["what is", "best", "vs", "near me", "tutorial", "guide", "meaning"]

# 无意义的超短词（不适合当查询，会被过滤或补全）
JUNK = re.compile(r"^[\s\W_]+$")


class QueryBuilder:
    """把种子词改写成自然查询。"""

    def __init__(self, geo=None, rng: random.Random | None = None,
                 chinese: bool = True, long_tail_p: float = 0.28,
                 local_p: float = 0.18):
        self.geo = geo
        self.rng = rng or random.Random()
        self.chinese = chinese if geo is None else getattr(geo, "is_chinese",
                                                           lambda: chinese)()
        self.long_tail_p = long_tail_p
        self.local_p = local_p
        self.templates = CN_TEMPLATES if self.chinese else EN_TEMPLATES
        self._weights = [w for _, w in self.templates]

    def city(self) -> str:
        if self.geo is None:
            return ""
        try:
            return self.geo.local_city() or ""
        except Exception:
            return ""

    def _pick(self):
        return self.rng.choices(self.templates, weights=self._weights, k=1)[0][0]

    def build(self, seed: str) -> str:
        """把一个种子词改写成自然查询。"""
        seed = (seed or "").strip()
        if not seed or JUNK.match(seed):
            return seed
        tpl = self._pick()
        city = self.city() if self.rng.random() < self.local_p else ""
        q = tpl.replace("{s}", seed).replace("{c}", city)
        q = re.sub(r"\s{2,}", " ", q).strip()
        # 长尾：追加一个限定语（真人搜几次后会越搜越长）
        if self.rng.random() < self.long_tail_p:
            q = self._extend(q)
        return q.strip()

    def _extend(self, q: str) -> str:
        if self.chinese:
            tail = self.rng.choice(["最新", "2026", "怎么办", "有哪些", "推荐",
                                    "多少钱", "在哪", "官网", "攻略", "时间"])
            if tail in q:
                return q
            return f"{q} {tail}"
        tail = self.rng.choice(["2026", "review", "price", "guide", "near me", "official"])
        if tail in q:
            return q
        return f"{q} {tail}"

    def build_many(self, seeds: list) -> list:
        return [self.build(s) for s in seeds]


class SessionChain:
    """生成有语义关联的连续查询：宽泛 → 收窄 → 换角度 → 偶尔换话题。

    真人一次"搜索会话"通常是围绕一个话题连搜几次，
    而不是每次都从完全无关的新词开始。
    """

    def __init__(self, builder: QueryBuilder, rng: random.Random | None = None,
                 chain_p: float = 0.62, max_chain: int = 4):
        self.b = builder
        self.rng = rng or random.Random()
        self.chain_p = chain_p
        self.max_chain = max_chain
        self.topic = ""            # 当前话题（种子词）
        self.chain_len = 0
        self.prev = ""

    def _followups(self):
        return FOLLOWUP_CN if self.b.chinese else FOLLOWUP_EN

    def _refiners(self):
        return REFINE_CN if self.b.chinese else REFINE_EN

    def _aspects(self):
        return ASPECT_CN if self.b.chinese else ASPECT_EN

    def next(self, seed: str) -> tuple:
        """返回 (查询, 是否属于上一个话题的延续)。"""
        # 上一轮话题还能延续，且概率命中 → 基于话题生成后续
        if self.topic and self.chain_len < self.max_chain and self.rng.random() < self.chain_p:
            kind = self.rng.choices(self._followups(),
                                    weights=[w for _, w in self._followups()], k=1)[0][0]
            base = self.prev if (kind in ("refine", "requery") and self.prev) else self.topic
            if kind == "refine":
                tail = self.rng.choice(self._refiners())
                q = f"{base} {tail}" if self.b.chinese else f"{tail} {base}"
            elif kind == "aspect":
                tail = self.rng.choice(self._aspects())
                if self.b.chinese:
                    # 去掉已有修饰，换成另一个角度
                    core = self._core(base)
                    q = f"{core}{tail}" if tail.startswith("是什么") else f"{core} {tail}"
                else:
                    q = f"{tail} {self._core(base)}"
            elif kind == "requery":
                q = self.b.build(self.topic)
            else:
                q = self.b.build(seed)
                self.topic, self.chain_len = seed, 0
                self.prev = q
                return q, False
            self.chain_len += 1
            self.prev = q
            return self._clean(q), True

        # 开启新话题
        q = self.b.build(seed)
        self.topic, self.chain_len = seed, 0
        self.prev = q
        return self._clean(q), False

    def _core(self, q: str) -> str:
        """去掉尾巴上的修饰语，回到核心词。"""
        tails = sorted(set(self._refiners() + self._aspects()), key=len, reverse=True)
        core = q
        for t in tails:
            core = core.replace(t, "")
        return re.sub(r"\s{2,}", " ", core).strip() or q

    @staticmethod
    def _clean(q: str) -> str:
        return re.sub(r"\s{2,}", " ", (q or "").strip())


# ------------------------------------------------------------------ 质量指标

def quality(queries: list) -> dict:
    """给出一批查询的质量指标，用于自查和调优。"""
    qs = [q for q in queries if isinstance(q, str)]
    if not qs:
        return {"n": 0}
    lens = [len(q.replace(" ", "")) for q in qs]          # 中文按字，英文去空格
    uniq = len(set(qs))
    long_tail = sum(1 for q in qs if len(q.split()) >= 3 or len(q.replace(" ", "")) >= 8)
    question = sum(1 for q in qs if any(k in q for k in
                   ("怎么", "为什么", "是什么", "多少钱", "有哪些", "在哪", "how", "what", "?")))
    n = len(qs)
    return {
        "n": n,
        "avg_len": round(sum(lens) / n, 2),
        "min_len": min(lens),
        "max_len": max(lens),
        "unique_rate": round(uniq / n, 3),
        "long_tail_rate": round(long_tail / n, 3),
        "question_rate": round(question / n, 3),
        "samples": qs[:5],
    }


def describe_quality(q: dict) -> str:
    if not q.get("n"):
        return "无查询"
    return (f"{q['n']} 条 · 均长 {q['avg_len']} 字 · 去重率 {q['unique_rate']} · "
            f"长尾 {q['long_tail_rate']} · 提问式 {q['question_rate']}")

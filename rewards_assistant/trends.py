# -*- coding: utf-8 -*-
"""实时热搜 / 头条新闻关键词采集。

三级降级，保证任何网络环境都能拿到关键词：
  L1  HTTP 直连多源            快，但榜单站常有反爬
  L2  用已启动的真实 Edge 抓    带 Cookie + 真实指纹，成功率最高
  L3  本地缓存 / 内置词表       断网兜底

对外只暴露：
    fetch_trending_keywords(...)             -> 纯 HTTP 采集
    fetch_trending_keywords_via_browser(...) -> 浏览器采集
    get_keyword_pool(...)                    -> 自动串起三级降级（推荐）
"""
from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from pathlib import Path

try:
    import requests
except ImportError:                      # requests 缺失时仍可用浏览器模式
    requests = None

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0")

DEFAULT_CACHE = Path.home() / ".cache/human_search_trends.json"

# 断网兜底词表：语法上是"正常搜索词"，避免污染测试
FALLBACK = [
    "今日新闻头条", "广州天气", "国内油价调整", "高铁时刻表查询",
    "python 异步编程", "edge 浏览器快捷键", "nginx 反向代理配置",
    "考研报名时间", "医保报销比例", "人民币汇率",
    "2026 放假安排", "新能源汽车销量排行", "世界杯赛程", "人工智能大会",
    "房贷利率最新", "台风最新路径", "黄金价格今日", "五险一金缴纳比例",
]


@dataclass
class Keyword:
    text: str
    source: str
    rank: int = 0
    heat: str = ""
    url: str = ""

    def to_dict(self):
        return asdict(self)


def _clean(s: str) -> str:
    s = re.sub(r"[#\[\]【】「」\"'“”]", "", s or "")
    s = re.sub(r"\s+", " ", s).strip(" -—·|")
    return s


def _valid(s: str) -> bool:
    if not (2 <= len(s) <= 30):
        return False
    if re.fullmatch(r"[\W\d_]+", s):
        return False
    return True


def _dedup(items: list[Keyword], limit: int) -> list[Keyword]:
    seen, out = set(), []
    for k in items:
        key = re.sub(r"[\s\W_]+", "", k.text.lower())
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(k)
        if limit and len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- L1: HTTP
def _get(url: str, timeout: float, referer: str | None = None, json_hint: bool = False):
    if requests is None:
        raise RuntimeError("未安装 requests，请用浏览器模式: pip install requests")
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if referer:
        headers["Referer"] = referer
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json() if json_hint else r.text


def _src_baidu(timeout: float) -> list[Keyword]:
    """百度热搜榜（实时）。"""
    url = "https://top.baidu.com/board?tab=realtime"
    html = _get(url, timeout, referer=url)
    out = []
    # 新版页面把榜单数据内联在 HTML 的 JSON 里，字段名叫 query
    for m in re.finditer(r'"query"\s*:\s*"([^"\\]{2,60})"', html):
        out.append(Keyword(_clean(m.group(1)), "baidu"))
    if not out:                                   # 老版 DOM
        for m in re.finditer(r'class="c-single-text-ellipsis"[^>]*>([^<]{2,60})</div>', html):
            out.append(Keyword(_clean(m.group(1)), "baidu"))
    for i, k in enumerate(out):
        k.rank = i + 1
    return out


def _src_weibo(timeout: float) -> list[Keyword]:
    """微博热搜（公开 JSON 接口）。"""
    url = "https://weibo.com/ajax/side/hotSearch"
    data = _get(url, timeout, referer="https://weibo.com/", json_hint=True)
    out = []
    for i, item in enumerate((data.get("data") or {}).get("realtime") or []):
        w = _clean(item.get("word") or item.get("note") or "")
        if w:
            out.append(Keyword(w, "weibo", i + 1, str(item.get("num", ""))))
    return out


def _src_toutiao(timeout: float) -> list[Keyword]:
    """今日头条热榜。"""
    url = "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc"
    data = _get(url, timeout, referer="https://www.toutiao.com/", json_hint=True)
    out = []
    for i, item in enumerate(data.get("data") or []):
        w = _clean(item.get("Title") or item.get("title") or "")
        if w:
            out.append(Keyword(w, "toutiao", i + 1, str(item.get("HotValue", ""))))
    return out


def _src_zhihu(timeout: float) -> list[Keyword]:
    """知乎热榜。"""
    url = "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=50&desktop=true"
    data = _get(url, timeout, referer="https://www.zhihu.com/hot", json_hint=True)
    out = []
    for i, item in enumerate(data.get("data") or []):
        t = item.get("target") or {}
        w = _clean(t.get("title") or item.get("title") or "")
        if w:
            out.append(Keyword(w, "zhihu", i + 1, item.get("detail_text", "")))
    return out


def _src_bing(timeout: float) -> list[Keyword]:
    """必应热搜 / trending topics 页。"""
    url = "https://www.bing.com/news/trendingtopics?form=HPNTOP"
    html = _get(url, timeout, referer="https://www.bing.com/")
    out = []
    for m in re.finditer(r'<a[^>]+class="[^"]*title[^"]*"[^>]*>([^<]{2,60})</a>', html):
        out.append(Keyword(_clean(m.group(1)), "bing"))
    if not out:
        for m in re.finditer(r'"text"\s*:\s*"([^"\\]{2,60})"', html):
            out.append(Keyword(_clean(m.group(1)), "bing"))
    for i, k in enumerate(out):
        k.rank = i + 1
    return out


def _src_sina_rss(timeout: float) -> list[Keyword]:
    """新浪新闻 RSS —— 企业网络里通常不会被拦，适合当兜底新闻源。"""
    url = "https://rss.sina.com.cn/news/marquee/ddt.xml"
    text = _get(url, timeout)
    out = []
    try:
        root = ET.fromstring(text.encode("utf-8", "ignore"))
    except Exception:
        return out
    for i, node in enumerate(root.iter("title")):
        w = _clean(node.text or "")
        if w:
            out.append(Keyword(w, "sina", i + 1))
    return out


HTTP_SOURCES = {
    "baidu": _src_baidu,
    "weibo": _src_weibo,
    "toutiao": _src_toutiao,
    "zhihu": _src_zhihu,
    "bing": _src_bing,
    "sina": _src_sina_rss,
}


def fetch_trending_keywords(sources=("baidu", "weibo", "toutiao", "zhihu", "bing", "sina"),
                            limit: int = 40, timeout: float = 8.0,
                            log=None) -> list[Keyword]:
    """L1：HTTP 直连多源采集，任一源失败不影响其它源。"""
    out: list[Keyword] = []
    for name in sources:
        fn = HTTP_SOURCES.get(name)
        if fn is None:
            continue
        try:
            got = [k for k in fn(timeout) if _valid(k.text)]
            if log:
                log("trend", f"[{name}] 抓到 {len(got)} 条")
            out += got
        except Exception as e:
            if log:
                log("trend_fail", f"[{name}] {type(e).__name__}: {str(e)[:80]}")
    return _dedup(out, limit)


# ------------------------------------------------------------ L2: 浏览器
# 榜单页 DOM 选择器（多套备选，抗改版）
BROWSER_SOURCES = {
    "baidu": ("https://top.baidu.com/board?tab=realtime",
              [".c-single-text-ellipsis", ".title_dHsXQ", "[class*='item-wrap'] [class*='title']"]),
    "weibo": ("https://s.weibo.com/top/summary?cate=realtimehot",
              ["td.td-02 a", "#pl_top_realtimehot tbody a", ".hot_rank_list a"]),
    "toutiao": ("https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc",
                ["pre"]),
    "zhihu": ("https://www.zhihu.com/hot",
              [".HotList-item .HotList-itemTitle", ".HotItem-title"]),
    "bing": ("https://cn.bing.com/search?q=%E7%83%AD%E6%90%9C%E6%A6%9C&ensearch=0",
             ["#b_results h2 a", ".b_algo h2 a"]),
    "sina": ("https://news.sina.com.cn/hotnews/",
             [".cons_news a", "#con-head a", ".blk_02 a"]),
}


def _browser_one(page, name: str, url: str, selectors: list, wait_ms: int) -> list[Keyword]:
    page.goto(url, timeout=30000, wait_until="domcontentloaded")
    page.wait_for_timeout(wait_ms)
    texts: list[str] = []
    # 头条 hot-board 直接返回 JSON，页面文本优先尝试解析
    if name == "toutiao":
        try:
            raw = page.inner_text("body")[:200000]
            data = json.loads(raw)
            for i, item in enumerate(data.get("data") or []):
                w = _clean(item.get("Title") or "")
                if w:
                    texts.append(w)
        except Exception:
            pass
    if not texts:
        for sel in selectors:
            try:
                page.wait_for_selector(sel, timeout=3000)
                vals = page.eval_on_selector_all(
                    sel, "els => els.map(e => (e.innerText || e.textContent || '').trim())")
                vals = [v for v in vals if v]
                if vals:
                    texts = vals
                    break
            except Exception:
                continue
    out = []
    for i, t in enumerate(texts):
        w = _clean(t)
        if _valid(w):
            out.append(Keyword(w, name, i + 1))
    return out


def fetch_trending_keywords_via_browser(page, sources=("baidu", "toutiao", "weibo", "bing"),
                                        limit: int = 40, wait_ms: int = 1800,
                                        log=None) -> list[Keyword]:
    """L2：用真实 Edge（带 Cookie / 真实指纹）抓榜单，反爬站也能拿到。

    注意会占用传入的 page；调用方通常另开一个临时标签页执行。
    """
    out: list[Keyword] = []
    for name in sources:
        cfg = BROWSER_SOURCES.get(name)
        if cfg is None:
            continue
        url, selectors = cfg
        try:
            got = _browser_one(page, name, url, selectors, wait_ms)
            got = [k for k in got if _valid(k.text)]
            if log:
                log("trend", f"[browser:{name}] 抓到 {len(got)} 条")
            out += got
        except Exception as e:
            if log:
                log("trend_fail", f"[browser:{name}] {type(e).__name__}: {str(e)[:80]}")
    return _dedup(out, limit)


# ------------------------------------------------------------ L3: 缓存
def save_cache(items: list[Keyword], path: Path = DEFAULT_CACHE):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "ts": time.time(),
            "items": [k.to_dict() for k in items],
        }, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def load_cache(path: Path = DEFAULT_CACHE, max_age: float = 86400.0) -> list[Keyword]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - float(raw.get("ts", 0)) > max_age:
            return []
        return [Keyword(**i) for i in raw.get("items", [])]
    except Exception:
        return []


def get_keyword_pool(*, sources=("baidu", "weibo", "toutiao", "zhihu", "bing", "sina"),
                     limit: int = 40, timeout: float = 8.0,
                     page=None, cache_path: Path = DEFAULT_CACHE,
                     max_cache_age: float = 86400.0, use_cache_fallback: bool = True,
                     log=None) -> tuple[list[Keyword], str]:
    """三级降级拿到关键词池，返回 (关键词列表, 来源说明)。

    1) HTTP 直连；2) 不够就用浏览器抓；3) 还不行就读缓存，最后用内置词表。
    """
    items = fetch_trending_keywords(sources=sources, limit=limit, timeout=timeout, log=log)
    how = "http"
    if len(items) < max(5, limit // 4) and page is not None:
        more = fetch_trending_keywords_via_browser(page, limit=limit, log=log)
        if more:
            items = _dedup(items + more, limit)
            how = "http+browser" if items is not more else "browser"
    if items:
        save_cache(items, cache_path)
        return items, how
    if use_cache_fallback:
        cached = load_cache(cache_path, max_cache_age)
        if cached:
            if log:
                log("trend", f"实时采集失败，使用本地缓存 {len(cached)} 条")
            return cached, "cache"
    if log:
        log("trend", "实时采集与缓存均不可用，使用内置兜底词表")
    return [Keyword(w, "fallback", i + 1) for i, w in enumerate(FALLBACK)], "fallback"

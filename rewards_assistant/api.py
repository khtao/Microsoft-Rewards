# -*- coding: utf-8 -*-
"""函数式接口：一句话启动一轮"真人搜索"实验。

给其它脚本用：

    from human_search import run_human_search
    report = run_human_search(repeat=10, level=3)
    print(report.ok_count, report.rounds[0].keyword, report.rounds[0].duration)

也可以只取关键词：

    from human_search import fetch_keywords
    kws = fetch_keywords(limit=30)          # [Keyword(text, source, rank, heat, url), ...]
"""
from __future__ import annotations

import json
import random
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

try:                                   # 作为包导入
    from .engines import ENGINES, resolve_engine, run_search, home_url
    from .human import Budget, Human
    from .trends import Keyword, get_keyword_pool
except ImportError:                     # 直接把目录加入 sys.path 后导入
    from engines import ENGINES, resolve_engine, run_search, home_url
    from human import Budget, Human
    from trends import Keyword, get_keyword_pool

DEFAULT_LOG = "search_actions.jsonl"


# ------------------------------------------------------------------ 数据结构
@dataclass
class RoundReport:
    index: int
    engine: str
    keyword: str
    source: str = ""
    ok: bool = True
    reason: str | None = None
    clicked: str | None = None
    plan_seconds: float = 0.0
    duration: float = 0.0
    started_at: str = ""
    events: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class SearchReport:
    sid: str
    started_at: str
    ended_at: str = ""
    total_seconds: float = 0.0
    keyword_source: str = ""
    keyword_pool_size: int = 0
    ok_count: int = 0
    fail_count: int = 0
    clicked_count: int = 0
    rounds: list = field(default_factory=list)
    log_path: str = ""
    geo: dict = field(default_factory=dict)
    fingerprint: dict = field(default_factory=dict)
    checks: list = field(default_factory=list)
    query_quality: dict = field(default_factory=dict)
    request_samples: list = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        d["rounds"] = [r.to_dict() if isinstance(r, RoundReport) else r for r in self.rounds]
        return d

    def to_json(self, indent=2):
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def __str__(self):
        return (f"SearchReport(sid={self.sid}, 成功={self.ok_count}, 失败={self.fail_count}, "
                f"点击结果={self.clicked_count}, 关键词池={self.keyword_pool_size}"
                f"[{self.keyword_source}], 用时={self.total_seconds:.1f}s)")


class ActionLogger:
    """行为日志（JSONL），方便和自家反作弊日志按时间戳对齐比对。"""

    def __init__(self, path: Path | None, sid: str, verbose: bool = True,
                 callback=None):
        self.path = Path(path) if path else None
        self.sid = sid
        self.verbose = verbose
        self.callback = callback
        self.t0 = time.time()
        self.fp = None
        self.sink = None                       # 当前轮的事件收集器
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.fp = open(self.path, "a", encoding="utf-8")
        self.log("session_start", f"sid={sid}")

    def __call__(self, action: str, detail: str = "", **kw):
        rec = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "t": round(time.time() - self.t0, 3),
            "sid": self.sid,
            "action": action,
            "detail": detail,
        }
        rec.update({k: v for k, v in kw.items() if v is not None})
        if self.fp:
            self.fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self.fp.flush()
        if self.sink is not None:
            self.sink.append(rec)
        if self.callback:
            try:
                self.callback(rec)
            except Exception:
                pass
        if self.verbose:
            print(f"[{rec['t']:8.2f}s] {action:<14} {detail}", flush=True)

    log = __call__

    def close(self):
        self("session_end", "done")
        if self.fp:
            self.fp.close()
            self.fp = None


# ------------------------------------------------------------------ 关键词
def fetch_keywords(sources=("baidu", "weibo", "toutiao", "zhihu", "bing", "sina"),
                   limit: int = 40, timeout: float = 8.0,
                   verbose: bool = False) -> list[Keyword]:
    """实时抓取热搜/头条关键词（纯 HTTP，不开浏览器）。失败自动降级。"""
    log = (lambda a, d="": print(f"  · {a}: {d}")) if verbose else None
    items, how = get_keyword_pool(sources=sources, limit=limit, timeout=timeout,
                                  page=None, log=log)
    if verbose:
        print(f"  来源：{how}，共 {len(items)} 条")
    return items


# ------------------------------------------------------------------ 主接口
def run_human_search(
        *,
        repeat: int = 5,
        keywords: list | None = None,
        keyword_sources=("baidu", "weibo", "toutiao", "zhihu", "bing", "sina"),
        keyword_limit: int = 40,
        refresh_every: int = 0,
        unique_keywords: bool = True,
        engines=("bing",),
        alternate: bool = False,
        level: int = 2,
        seed: int | None = None,
        round_seconds=(16.0, 29.0),
        max_round_seconds: float = 30.0,
        gap_seconds=(3.0, 12.0),
        click_prob: float = 0.35,
        enter_rate: float = 0.5,
        scroll_screens: float = 1.2,
        on_captcha: str = "wait",
        user_data_dir: str | None = None,
        copy_profile: bool = False,
        edge_path: str | None = None,
        headless: bool = False,
        window_size: str | None = None,
        proxy: str | None = None,
        slow_mo: int = 0,
        keep_open: float = 0.0,
        sandbox: str | None = None,
        flag_warning: str = "auto",
        policy_dir: str | None = None,
        geo=None,
        detect_geo: bool = True,
        force_geo: bool = False,
        rewrite_queries: bool = True,
        session_chain: bool = True,
        reuse_serp: bool = True,
        audit_requests: bool = False,
        log_file: str | None = DEFAULT_LOG,
        log_callback=None,
        stop_check=None,
        verbose: bool = True,
        page=None,
        context=None,
        close_browser: bool = True,
) -> SearchReport:
    """跑一批拟人搜索。

    每轮时长在 round_seconds 区间内随机取值，且被 max_round_seconds 硬性封顶；
    轮内所有等待都由 Budget 裁剪，保证不超时。

    参数
    ----
    repeat            : 搜索轮数（重复次数）
    keywords          : 额外自定义关键词，与实时热搜混合
    keyword_sources   : 热搜来源，见 trends.HTTP_SOURCES
    refresh_every     : 每 N 轮重新抓一次热搜；0 表示只在开头抓一次
    engines           : 搜索引擎序列，随机或交替（alternate=True）选取
    level             : 拟人强度 1~3
    round_seconds     : 每轮时长随机区间（秒），默认 16~29，配合 max_round_seconds=30
    gap_seconds       : 两轮之间的间隔区间（秒）
    sandbox           : "on" 开沙箱 / "off" 关沙箱 / None 自动（root 自动关，并抑制警告横幅）
    page / context    : 注入已有的 Playwright 页面或上下文；不传则自行启动 Edge
    close_browser     : 结束时是否关闭浏览器（注入的对象不受影响）

    返回
    ----
    SearchReport，含每轮耗时、是否命中、点击了哪条结果，可 .to_dict() / .to_json()
    """
    if repeat < 1:
        raise ValueError("repeat 必须 >= 1")
    # 只保留 Bing：历史配置里的 baidu / both 会被收敛掉，不报错
    engines = tuple(dict.fromkeys(resolve_engine(e) for e in (engines or ("bing",))))
    if len(engines) > 1:
        print(f"[信息] 已收敛为单一引擎 {engines}（本项目只测 Bing）")

    rng = random.Random(seed)
    sid = uuid.uuid4().hex[:8]
    t_start = time.time()
    report = SearchReport(sid=sid, started_at=datetime.now().isoformat(timespec="seconds"))
    logger = ActionLogger(log_file, sid, verbose=verbose, callback=log_callback)
    human = Human(level=level, seed=seed)
    human.rng = rng                                  # 与计划共用随机源，便于复现

    cfg = SimpleNamespace(
        on_captcha=on_captcha, click_prob=click_prob,
        enter_rate=enter_rate, scroll_screens=scroll_screens, no_back=False,
        reuse_serp=reuse_serp,
    )

    # ---- 地理位置：统一时区 / 语言 / 市场，保证与 IP 归属自洽
    if geo is None and detect_geo:
        try:
            from .geo import detect as _detect_geo
            geo = _detect_geo(use_cache=True, timeout=4.0, verbose=verbose, force=force_geo)
        except Exception:
            geo = None
    if geo is not None:
        from .geo import describe as _describe_geo
        report.geo = getattr(geo, "to_dict", lambda: {})()
        logger("geo", _describe_geo(geo))
        if verbose:
            from entropy import Circadian
            circ = Circadian(geo.tz_offset_hours)
            print(f"[信息] 当地作息：{circ.describe()}")

    # ---- 浏览器：用外部的，或自己启动
    own_context = False
    ctx = context
    if ctx is None and page is not None:
        ctx = page.context
    if ctx is None:
        from .browser import (copy_profile as _copy, default_profile_dir, edge_is_running,
                             launch_edge, find_edge_binary, profile_locked)
        from playwright.sync_api import sync_playwright

        profile = Path(user_data_dir).expanduser() if user_data_dir else default_profile_dir()
        if not profile.exists():
            raise FileNotFoundError(
                f"用户数据目录不存在：{profile}。用 --user-data-dir 指定，"
                f"或在 Edge 里打开 chrome://version 查看『配置文件路径』的上一层")
        if not copy_profile and (edge_is_running() or profile_locked(profile)):
            if verbose:
                print("[提示] Edge 正在运行 / profile 被锁，自动改用 profile 快照")
            copy_profile = True
        if copy_profile:
            snapshot = Path.home() / ".cache/human_search_edge_profile"
            if verbose:
                print(f"[提示] 复制 profile -> {snapshot}")
            profile = _copy(profile, snapshot)

        pw = sync_playwright().start()
        ctx = launch_edge(pw, user_data_dir=profile, headless=headless,
                          window_size=window_size, proxy=proxy,
                          edge_path=edge_path or find_edge_binary(), slow_mo=slow_mo,
                          sandbox=sandbox, flag_warning=flag_warning,
                          policy_dir=policy_dir, verbose=verbose, geo=geo)
        own_context = True
        logger("browser", f"Edge 启动 user_data_dir={profile} headless={headless}")
    else:
        logger("browser", "复用外部传入的 Playwright context/page")

    try:
        main_page = page or (ctx.pages[0] if ctx.pages else ctx.new_page())

        # ---- 请求上下文：真实 Client Hints + 与地区一致的 Accept-Language
        try:
            from .  import context as _ctxmod
            from .  import fingerprint as _fpmod
            fp = _fpmod.collect(main_page)
            report.fingerprint = fp or {}
            rctx = _ctxmod.build(geo, (fp or {}).get("uaData"))
            _ctxmod.apply(ctx, rctx, verbose=verbose)
            if audit_requests:
                _req_samples = []
                _ctxmod.install_recorder(main_page, _req_samples)
                report.request_samples = _req_samples
            logger("context", f"请求头就绪：AL={rctx.accept_language[:28]} | "
                              f"ClientHints {len(rctx.client_hints)} 项")
        except Exception as _e:
            logger("warn", f"请求上下文设置失败（不影响运行）：{_e}")

        # ---- 查询改写：把热搜词改写成自然查询，并组织成会话链
        rewriter = None
        if rewrite_queries:
            try:
                from .queries import QueryBuilder, SessionChain, quality as _q
                qb = QueryBuilder(geo, rng)
                chain = SessionChain(qb, rng) if session_chain else None
                if chain is not None:
                    def rewriter(text, _c=chain):
                        return _c.next(text)[0]
                else:
                    def rewriter(text, _b=qb):
                        return _b.build(text)
                logger("queries", f"查询改写开启（会话链={'开' if session_chain else '关'}）")
            except Exception as _e:
                logger("warn", f"查询改写不可用：{_e}")
                rewriter = None

        # ---- 关键词池
        def _collect(tmp_page):
            pool, how = get_keyword_pool(sources=keyword_sources, limit=keyword_limit,
                                         page=tmp_page, log=logger)
            extra = [Keyword(str(k), "custom", i + 1) for i, k in enumerate(keywords or [])]
            return extra + pool, how

        pool, how = _collect(main_page)
        report.keyword_source = how
        report.keyword_pool_size = len(pool)
        logger("keywords", f"关键词池 {len(pool)} 条（来源 {how}），示例："
                           f"{' | '.join(k.text for k in pool[:5])}")
        if not pool:
            raise RuntimeError("关键词池为空，检查网络或自定义 keywords 参数")

        plan = _sample_plan(pool, repeat, rng, unique_keywords, engines, alternate,
                            rewriter=rewriter)
        try:
            from .queries import quality as _qq
            report.query_quality = _qq([kw.text for _, kw in plan])
            logger("queries", f"查询质量：均长 {report.query_quality.get('avg_len')} 字，"
                              f"长尾率 {report.query_quality.get('long_tail_rate')}，"
                              f"提问式 {report.query_quality.get('question_rate')}")
        except Exception:
            pass

        # ---- 逐轮执行
        for i, (ename, kw) in enumerate(plan, 1):
            if stop_check is not None and stop_check():
                logger("abort", "收到停止请求，在第 {i} 轮前退出".format(i=i))
                break
            engine = ENGINES[ename]
            total = min(max_round_seconds,
                        rng.uniform(min(round_seconds), max(round_seconds)))
            budget = Budget(total, rng)
            rnd = RoundReport(index=i, engine=ename, keyword=kw.text, source=kw.source,
                              plan_seconds=round(total, 2),
                              started_at=datetime.now().isoformat(timespec="seconds"))
            logger.sink = rnd.events
            logger("search_start", f"第 {i}/{repeat} 次：{kw.text}",
                   engine=ename, keyword=kw.text, source=kw.source,
                   plan_seconds=round(total, 2))

            try:
                pg = main_page
                res = run_search(pg, engine, kw.text, human,
                                 lambda a, d="", **x: logger(a, d, engine=ename,
                                                             keyword=kw.text, **x),
                                 cfg, budget)
                rnd.ok = bool(res.get("ok"))
                rnd.reason = res.get("reason")
                rnd.clicked = res.get("clicked")
            except KeyboardInterrupt:
                logger("abort", "用户中断")
                rnd.ok, rnd.reason = False, "abort"
                report.rounds.append(rnd)
                break
            except Exception as e:
                rnd.ok, rnd.reason = False, f"{type(e).__name__}: {e}"
                logger("error", rnd.reason, engine=ename, keyword=kw.text)

            rnd.duration = round(budget.elapsed, 2)
            report.rounds.append(rnd)
            report.ok_count += int(rnd.ok)
            report.fail_count += int(not rnd.ok)
            report.clicked_count += int(bool(rnd.clicked))
            logger("round_end", f"第 {i} 轮结束：成功={rnd.ok} 用时={rnd.duration}s"
                                f"（预算 {rnd.plan_seconds}s）", engine=ename, keyword=kw.text)
            logger.sink = None

            if i >= repeat:
                break

            # ---- 轮间隔；顺带按需刷新热搜
            gap = rng.uniform(min(gap_seconds), max(gap_seconds))
            logger("idle", f"间隔 {gap:.1f}s")
            if refresh_every and i % refresh_every == 0:
                tmp = ctx.new_page()
                try:
                    pool, how2 = _collect(tmp)
                    report.keyword_source = how2
                    logger("keywords", f"刷新关键词池 {len(pool)} 条（来源 {how2}）")
                    rest = _sample_plan(pool, repeat - i, rng, unique_keywords,
                                        engines, alternate,
                                        used=[p[1].text for p in plan[:i]],
                                        rewriter=rewriter)
                    plan = plan[:i] + rest
                finally:
                    tmp.close()
            human.idle(gap, pg, stop=stop_check)

        if keep_open:
            logger("keep_open", f"保持窗口 {keep_open}s")
            try:
                time.sleep(keep_open)
            except KeyboardInterrupt:
                pass
    finally:
        report.ended_at = datetime.now().isoformat(timespec="seconds")
        report.total_seconds = round(time.time() - t_start, 2)
        if own_context and close_browser:
            try:
                ctx.close()
            except Exception:
                pass
        logger.close()
        report.log_path = str(Path(log_file).resolve()) if log_file else ""
        try:
            from . import fingerprint as _fpmod
            from . import geo as _geomod
            fp = report.fingerprint or _fpmod.collect(main_page)
            ck = list(_fpmod.checks(fp))
            if geo is not None:
                ck += _geomod.consistency_checks(
                    geo, fp.get("timezone"), fp.get("language"), fp.get("languages"))
                if getattr(report, "request_samples", None):
                    from . import context as _ctxmod
                    ck = _ctxmod.check_request_coherence(report.request_samples, geo, ck)
            report.checks = [{"name": a, "value": b, "ok": bool(c)} for a, b, c in ck]
            bad = [c for c in report.checks if not c["ok"]]
            if bad and verbose:
                print(f"[提示] 一致性检查有 {len(bad)} 项不通过：")
                for c in bad[:8]:
                    print(f"       · {c['name']}：{c['value']}")
        except Exception:
            pass

    if verbose:
        print(f"\n完成：成功 {report.ok_count} / 失败 {report.fail_count}，"
              f"点击结果 {report.clicked_count} 次，用时 {report.total_seconds:.1f}s")
        if report.log_path:
            print(f"行为日志：{report.log_path}")
    return report


def _sample_plan(pool: list[Keyword], n: int, rng: random.Random,
                 unique: bool, engines, alternate: bool,
                 used: list | None = None, rewriter=None) -> list:
    """从关键词池采样 n 条，并分配引擎。"""
    texts = [k.text for k in pool]
    chosen: list[Keyword] = []
    if unique:
        avoid = set(used or [])
        candidates = [k for k in pool if k.text not in avoid]
        rng.shuffle(candidates)
        chosen = candidates[:n]
        if len(chosen) < n:                    # 池不够，允许回绕复用
            rng.shuffle(pool)
            for k in pool:
                if len(chosen) >= n:
                    break
                chosen.append(k)
    else:
        chosen = [rng.choice(pool) for _ in range(n)]

    plan = []
    for i, kw in enumerate(chosen[:n]):
        if alternate:
            ename = engines[i % len(engines)]
        else:
            ename = rng.choice(engines)
        if rewriter is not None:
            try:
                new_text = rewriter(kw.text)
                if new_text:
                    kw = Keyword(new_text, kw.source, kw.rank)
            except Exception:
                pass
        plan.append((ename, kw))
    del texts
    return plan

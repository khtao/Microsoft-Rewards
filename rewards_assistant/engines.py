# -*- coding: utf-8 -*-
"""必应的页面流程与选择器（多套备选选择器，抗页面改版）。

只保留 Bing —— 测试目标是单一搜索引擎的行为特征，混用两个引擎会让
「同一批请求来自同一客户端」的判断变复杂。

注意：这里的「引擎」只指**去哪儿搜**。关键词仍然可以取自百度热搜榜
（见 trends.py / hotwords.py），那只是取词来源，不是搜索引擎。

所有等待时长都由 Budget 动态裁剪：一轮搜索必须在给定时长内结束，
时间不够就少滚几屏、跳过点击，绝不因为等元素而超时。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

# 提交之后至少要留给"看结果"的时间
RESULTS_RESERVE = 3.0
# 想点进结果页至少需要这么多剩余时间（点开 + 看一眼 + 返回）
CLICK_RESERVE = 6.0


@dataclass
class Engine:
    name: str
    home: str
    inputs: list = field(default_factory=list)
    buttons: list = field(default_factory=list)
    results: list = field(default_factory=list)
    consent: list = field(default_factory=list)
    captcha: list = field(default_factory=list)
    ready: list = field(default_factory=list)


ENGINES = {
    "bing": Engine(
        name="bing",
        home="https://www.bing.com/?mkt=zh-CN",
        inputs=["#sb_form_q", "textarea[name='q']", "input[name='q']", "#q"],
        buttons=["#sb_form_go", "input[type='submit']", "#sb_form_submit", "label#search_icon"],
        results=["li.b_algo h2 a", "#b_results > li h2 a", ".b_algo a", "#b_results li a"],
        consent=[".bnp_button_accept", "#bnp_container button", "button:has-text('接受')"],
        captcha=["#verify", "#captcha", "iframe[src*='captcha']", ".vs_captcha",
                 "text=请证明你不是机器人", "text=Verify you are human"],
        ready=["#sb_form_q", "#b_results"],
    ),
}

# 允许使用的搜索引擎（单一引擎，保留元组形式便于后续扩展）
ENGINE_CHOICES = ("bing",)
DEFAULT_ENGINE = "bing"


def resolve_engine(name: str | None) -> str:
    """把任意历史取值（baidu / both）都收敛到 bing，避免旧配置报错。"""
    if not name or name in ("both", "baidu", "all"):
        return DEFAULT_ENGINE
    return name if name in ENGINES else DEFAULT_ENGINE


def home_url(engine: Engine, geo=None) -> str:
    """首页 URL 带市场参数，与 IP 归属保持一致。"""
    mkt = getattr(geo, "market", None) or "zh-CN"
    return f"https://www.bing.com/?mkt={mkt}"


def on_engine_domain(page, engine: Engine) -> bool:
    """当前页面是否已经在 Bing 上（决定能否复用 SERP）。"""
    try:
        url = page.url or ""
    except Exception:
        return False
    return "bing.com" in url


def _first_visible(page, selectors: list, timeout_ms: int = 8000):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=max(300, timeout_ms))
            return loc, sel
        except Exception:
            continue
    return None, None


def goto_home(page, engine: Engine, human, log, budget=None, timeout: int = 30000):
    if budget is not None:
        timeout = int(max(4000, min(timeout, budget.left * 1000 * 0.6)))
    log("navigate", f"打开首页 {engine.home}")
    page.goto(engine.home, timeout=timeout, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=min(8000, timeout))
    except Exception:
        pass
    human.pause(0.6, 2.0)                      # 打开首页后扫一眼
    human.wander(page)
    wait_ms = 15000 if budget is None else int(max(3000, min(15000, budget.left * 1000 * 0.5)))
    loc, _ = _first_visible(page, engine.ready, timeout_ms=wait_ms)
    if loc is None:
        log("warn", "首页关键元素未出现，可能被重定向或网络异常")
    return loc is not None


def handle_consent(page, engine: Engine, human, log, last_xy):
    loc, sel = _first_visible(page, engine.consent, timeout_ms=2500)
    if loc is None:
        return last_xy
    log("consent", f"点击隐私提示 {sel}")
    return human.human_click(page, loc, last_xy)


def detect_captcha(page, engine: Engine, log, on_captcha: str = "wait") -> bool:
    loc, sel = _first_visible(page, engine.captcha, timeout_ms=1200)
    if loc is None:
        return False
    log("captcha", f"命中风控/验证元素：{sel}")
    if on_captcha == "wait":
        # 非交互环境（被其它脚本调用、CI、后台任务）没法等人，自动退化为 skip
        interactive = bool(getattr(sys.stdin, "isatty", lambda: False)())
        if not interactive:
            log("captcha", "非交互环境，无法等待人工验证，按 skip 处理")
            return True
        try:
            input("  >>> 请在浏览器窗口里人工完成验证，完成后按回车继续（Ctrl+C 终止）：")
        except (EOFError, OSError):
            log("captcha", "stdin 不可用，按 skip 处理")
            return True
    elif on_captcha == "abort":
        raise RuntimeError(f"触发验证页面：{sel}")
    return True


def run_search(page, engine: Engine, keyword: str, human, log, cfg, budget=None) -> dict:
    """完整一次搜索：进首页 → 输入 → 提交 → 阅读 → 可选点击结果。

    budget 不为 None 时，全程按剩余时间裁剪动作，保证在预算内收尾。
    返回 {"ok": bool, "clicked": str|None, "reason": str|None}

    导航链：首轮进首页；后续轮次若已在 Bing 上，就**留在 SERP 直接改词**——
    真人不会每搜一个词都重新打开一次首页，那会产生一串可疑的重复首页请求。
    """
    if budget is not None:
        human.bind(budget)

    reuse = bool(getattr(cfg, "reuse_serp", True))
    if reuse and on_engine_domain(page, engine):
        log("reuse", "停留在搜索结果页，直接改词（不重新打开首页）")
        human.pause(0.3, 1.2)
    else:
        goto_home(page, engine, human, log, budget)
        if budget is not None and budget.left <= RESULTS_RESERVE + 1:
            log("warn", f"打开首页已耗时 {budget.elapsed:.1f}s，本轮压缩后续动作")

    if detect_captcha(page, engine, log, cfg.on_captcha):
        log("captcha", "验证后继续本轮搜索")
    last_xy = handle_consent(page, engine, human, log, None)

    wait_ms = 15000 if budget is None else int(max(3000, min(15000, budget.left * 1000 * 0.4)))
    box_loc, sel = _first_visible(page, engine.inputs, timeout_ms=wait_ms)
    if box_loc is None:
        log("error", "找不到搜索框")
        return {"ok": False, "reason": "no-input", "clicked": None}
    log("focus", f"定位搜索框 {sel}")

    last_xy = human.human_click(page, box_loc, last_xy)
    human.pause(0.15, 0.6)
    if human.rng.random() < 0.5:               # 真人换词时也会全选删掉
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        human.pause(0.1, 0.3)

    # 粘贴预算：给提交和看结果留位置即可，粘贴本身很快
    paste_seconds = None
    if budget is not None:
        room = budget.left - RESULTS_RESERVE - 1.0
        paste_seconds = max(0.4, min(3.0, room * 0.2))
    log("paste", f"粘贴关键词：{keyword}"
                 + (f"（预算 {paste_seconds:.1f}s）" if paste_seconds else ""))
    human.paste_text(page, box_loc, keyword, seconds=paste_seconds)
    ok_input = human.fix_input(page, box_loc, keyword)
    if not ok_input:
        log("warn", f"输入框内容与预期不一致，已尝试修正：{keyword}")
    human.pause(0.3, 1.2)                       # 输完会犹豫一下

    if human.rng.random() < cfg.enter_rate:
        log("submit", "回车提交")
        page.keyboard.press("Enter")
    else:
        btn, bsel = _first_visible(page, engine.buttons, timeout_ms=3000)
        if btn is not None:
            log("submit", f"点击搜索按钮 {bsel}")
            human.human_click(page, btn, last_xy)
        else:
            log("submit", "未找到按钮，退回回车")
            page.keyboard.press("Enter")

    wait_ms = 20000 if budget is None else int(max(2500, min(20000, budget.left * 1000 * 0.7)))
    res, rsel = _first_visible(page, engine.results, timeout_ms=wait_ms)
    if res is None:
        log("warn", "结果列表未出现")
    else:
        try:
            log("results", f"结果已渲染（{rsel}），共 {page.locator(rsel).count()} 条")
        except Exception:
            log("results", f"结果已渲染（{rsel}）")

    human.pause(0.8, 2.5)
    read_seconds = None
    if budget is not None:
        read_seconds = max(0.0, budget.left - 1.5)
    human.scroll_read(page, screens=cfg.scroll_screens, max_seconds=read_seconds)

    clicked = None
    if human.rng.random() < cfg.click_prob:
        if budget is not None and not budget.fits(CLICK_RESERVE):
            log("skip", "剩余时间不足，跳过点击结果")
        else:
            try:
                items = page.locator(engine.results[0]) if engine.results else None
                n = items.count() if items else 0
                if n:
                    idx = human.rng.randrange(min(n, 8))
                    target = items.nth(idx)
                    title = (target.inner_text() or "")[:40]
                    log("click_result", f"点击第 {idx+1} 条结果：{title}")
                    target.scroll_into_view_if_needed()
                    human.pause(0.3, 0.8)
                    human.human_click(page, target, last_xy)
                    clicked = title
                    if budget is not None:
                        human.pause(1.0, min(4.0, max(0.0, budget.left - 1.0)))
                    else:
                        human.read_pause()
                    if not cfg.no_back:
                        page.go_back(wait_until="domcontentloaded")
                        log("back", "返回结果页")
                        human.pause(0.8, 2.5)
            except Exception as e:
                log("warn", f"点击结果失败：{e}")

    if budget is not None:
        log("budget", f"本轮耗时 {budget.elapsed:.1f}s / 预算 {budget.total:.1f}s")
    return {"ok": True, "clicked": clicked, "reason": None}

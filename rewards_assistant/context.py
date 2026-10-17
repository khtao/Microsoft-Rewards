# -*- coding: utf-8 -*-
"""请求上下文完整性：让每次请求都带着自洽的头部与合理的来路。

风控看的不只是 UA，而是**整条请求链**：
  · sec-ch-ua / sec-ch-ua-platform / sec-ch-ua-mobile / sec-ch-ua-arch / sec-ch-ua-bitness
    （Client Hints，现代 Chromium 必带；缺失或被篡改成与 UA 不符就是强特征）
  · Accept-Language 与 IP 地区、时区是否一致
  · Referer 链是否自然（直接 goto 结果页 = 凭空出现，很可疑）
  · Sec-Fetch-Dest / Mode / Site / User 四件套（浏览器自动生成，脚本伪造必错）

关键做法：
  1) Client Hints 的值**从真实浏览器里读出来**（navigator.userAgentData），
     不自己编 —— 编出来的版本一定和 UA 里的 Edg/xxx 对不上。
  2) Accept-Language 由 geo 推导，与地区/市场一致。
  3) 导航链：首轮进首页 → 提交得 SERP → 后续轮次**留在 SERP 直接改词**，
     不再 goto 首页（真人不会每搜一个词都重新打开一次首页）。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RequestContext:
    """一次会话的请求上下文（头部 + 导航策略）。"""
    extra_headers: dict = field(default_factory=dict)
    client_hints: dict = field(default_factory=dict)
    accept_language: str = ""
    timezone: str = ""
    locale: str = ""
    reuse_serp: bool = True          # 后续轮次复用 SERP 改词，而不是重新 goto 首页


def build_client_hints(ua_data: dict | None) -> dict:
    """把 navigator.userAgentData 映射成请求头里的 sec-ch-ua 系列。"""
    if not ua_data:
        return {}
    brands = ua_data.get("brands") or []
    # 完整品牌列表（含小版本），形如 "Chromium";v="126", "Microsoft Edge";v="126"
    full = "; ".join(f'"{b.get("brand","")}";v="{b.get("version","")}"' for b in brands)
    # 简版：只保留各品牌主版本（真实浏览器发的是简版）
    simple = "; ".join(f'"{b.get("brand","")}";v="{str(b.get("version","")).split(".")[0]}"'
                       for b in brands)
    hints = {}
    if simple:
        hints["sec-ch-ua"] = simple
        hints["sec-ch-ua-full-version-list"] = full
    if ua_data.get("mobile") is not None:
        hints["sec-ch-ua-mobile"] = "?1" if ua_data["mobile"] else "?0"
    if ua_data.get("platform"):
        hints["sec-ch-ua-platform"] = f'"{ua_data["platform"]}"'
    if ua_data.get("architecture"):
        hints["sec-ch-ua-arch"] = f'"{ua_data["architecture"]}"'
    if ua_data.get("bitness"):
        hints["sec-ch-ua-bitness"] = f'"{ua_data["bitness"]}"'
    return hints


def build(geo, ua_data: dict | None = None, locale: str | None = None) -> RequestContext:
    """由地理位置 + 真实 UA 数据生成请求上下文。"""
    locale = locale or geo.locale
    accept_language = getattr(geo, "accept_language", "") or f"{locale},{locale[:2]};q=0.9"
    hints = build_client_hints(ua_data)
    headers = {
        "Accept-Language": accept_language,
        "Upgrade-Insecure-Requests": "1",
    }
    headers.update(hints)
    return RequestContext(
        extra_headers=headers,
        client_hints=hints,
        accept_language=accept_language,
        timezone=getattr(geo, "timezone", "") or "",
        locale=locale,
    )


def apply(ctx, rctx: RequestContext, verbose: bool = False) -> bool:
    """把额外头部挂到 Playwright context 上。

    注意：Chromium 对 sec-ch-ua 系列是**自动接管**的，同名头部通常会被它自己的
    值覆盖 —— 这没问题，因为我们写的值本来就取自真实浏览器，二者一致。
    真正需要显式设的是 Accept-Language（Playwright 的 locale 只改 navigator.language）。
    """
    if not rctx.extra_headers:
        return False
    try:
        ctx.set_extra_http_headers(rctx.extra_headers)
        if verbose:
            keys = ", ".join(sorted(rctx.extra_headers))
            print(f"[信息] 已设置请求头：{keys}")
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ 请求审计

AUDIT_JS_INSTALL = """
() => {
  window.__hsReq = [];
  const po = window.performance && window.performance.getEntriesByType;
  return !!po;
}
"""


def audit_navigation(page) -> list:
    """取回本次导航相关的性能条目，用于检查来路链是否自然。"""
    try:
        return page.evaluate("""() => {
          const es = (performance.getEntriesByType('navigation') || []);
          return es.map(e => ({
            name: e.name, type: e.type,
            redirectCount: e.redirectCount,
            startTime: Math.round(e.startTime),
            duration: Math.round(e.duration),
            transferSize: e.transferSize || 0
          }));
        }""") or []
    except Exception:
        return []


def check_request_coherence(samples: list, geo, checks: list | None = None) -> list:
    """对抓到的请求头部做一致性检查。samples 为 [{'headers': {...}, 'url': str}]。"""
    out = checks or []
    if not samples:
        out.append(("请求采样", "未捕获", False))
        return out
    hs = samples[0].get("headers") or {}
    # 统一小写
    low = {k.lower(): v for k, v in hs.items()}
    # 1) Client Hints 是否齐全
    need = ["sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform"]
    have = [h for h in need if h in low]
    out.append(("Client Hints", f"{len(have)}/{len(need)}", len(have) >= 2))
    # 2) Accept-Language 与地区一致
    al = low.get("accept-language", "")
    ok = (al.split(",")[0].split("-")[0] == geo.locale.split("-")[0]) if al else False
    out.append(("Accept-Language", al[:38] or "-", ok))
    # 3) UA 里有 Edg/
    ua = low.get("user-agent", "")
    out.append(("UA 含 Edg", "是" if "Edg/" in ua else "否", "Edg/" in ua))
    # 4) 是否存在明显的自动化头
    bad = [k for k in low if "headless" in str(low[k]).lower() or k.startswith("x-playwright")]
    out.append(("无自动化头", ",".join(bad) or "干净", not bad))
    return out


def install_recorder(page, sink: list, max_n: int = 40):
    """用 CDP/route 记录请求头，供事后审计。返回 route 对象（可 unroute）。"""
    def _on_route(route, request):
        try:
            if len(sink) < max_n:
                sink.append({"url": request.url, "method": request.method,
                             "headers": request.all_headers(),
                             "resource_type": request.resource_type})
        except Exception:
            pass
        try:
            route.continue_()
        except Exception:
            pass
    try:
        page.route("**/*", _on_route)
        return True
    except Exception:
        return False

# -*- coding: utf-8 -*-
"""客户端环境指纹：读取真实值 + 交叉一致性校验。

原则 —— **一致性 > 伪装**：
  风控现在很少只看单个字段，而是查**字段之间是否自洽**：
    · screen.width/height 与 window.innerWidth/innerHeight 是否匹配
    · devicePixelRatio 与屏幕物理尺寸是否可能
    · navigator.hardwareConcurrency 与 WebGL 报告的 GPU 是否同一档次
    · 时区 / 语言 / 地区是否一致
    · UA 里的 Edge 版本与 navigator.userAgentData 的 brands 是否一致
  所以这里**不硬编码任何硬件值**（硬编码 8 核在 16 核机器上就是自曝），
  只做两件事：① 去掉自动化标记；② 补齐 headless 下缺失的对象。

提供：
  · collect(page)    —— 一次性取回全部指纹字段
  · checks(fp)       —— 交叉校验，返回冲突项
  · stealth_js()     —— 最小化注入脚本（默认只打 webdriver）
"""
from __future__ import annotations

# 采集脚本：只读，不修改
COLLECT_JS = """
() => {
  const g = (o, k) => { try { return o && o[k]; } catch (e) { return undefined; } };
  const nav = navigator;
  let uaData = null;
  try {
    const d = nav.userAgentData;
    if (d) {
      uaData = {
        brands: (d.brands || []).map(b => ({brand: b.brand, version: b.version})),
        mobile: d.mobile, platform: d.platform,
        architecture: g(d, 'architecture'), bitness: g(d, 'bitness'),
        uaFullVersion: g(d, 'uaFullVersion'),
        fullVersionList: (d.getHighEntropyValues ? null : null)
      };
    }
  } catch (e) {}
  let webgl = {};
  try {
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
    if (gl) {
      const dbg = gl.getExtension('WEBGL_debug_renderer_info');
      webgl = {
        vendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR),
        renderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
        version: gl.getParameter(gl.VERSION),
        shadingLanguage: gl.getParameter(gl.SHADING_LANGUAGE_VERSION),
        maxTextureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE),
      };
    }
  } catch (e) {}
  const plugins = [];
  try { for (let i = 0; i < nav.plugins.length; i++) { plugins.push(nav.plugins[i].name); } } catch (e) {}
  const conn = nav.connection || {};
  return {
    userAgent: nav.userAgent,
    platform: nav.platform,
    vendor: nav.vendor,
    languages: nav.languages ? Array.from(nav.languages) : [],
    language: nav.language,
    hardwareConcurrency: nav.hardwareConcurrency,
    deviceMemory: g(nav, 'deviceMemory'),
    maxTouchPoints: nav.maxTouchPoints,
    pdfViewerEnabled: g(nav, 'pdfViewerEnabled'),
    webdriver: g(nav, 'webdriver'),
    cookieEnabled: nav.cookieEnabled,
    doNotTrack: g(nav, 'doNotTrack'),
    timezone: (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) { return ''; } })(),
    timezoneOffset: new Date().getTimezoneOffset(),
    screen: {w: screen.width, h: screen.height, availW: screen.availWidth,
             availH: screen.availHeight, colorDepth: screen.colorDepth, pixelDepth: screen.pixelDepth},
    window: {innerW: window.innerWidth, innerH: window.innerHeight, outerW: window.outerWidth,
             outerH: window.outerHeight, dpr: window.devicePixelRatio, screenX: window.screenX,
             screenY: window.screenY},
    uaData: uaData,
    webgl: webgl,
    plugins: plugins,
    connection: {effectiveType: conn.effectiveType, downlink: conn.downlink, rtt: conn.rtt,
                 saveData: conn.saveData},
    hasChrome: typeof window.chrome !== 'undefined',
    touchSupport: ('ontouchstart' in window) || (nav.maxTouchPoints || 0) > 0,
  };
}
"""

# 只做"去自动化痕迹"，不伪造任何硬件值
STEALTH_MIN_JS = """
(() => {
  const patch = (obj, name, value) => {
    try { Object.defineProperty(obj, name, { get: () => value, configurable: true }); } catch (e) {}
  };
  // 1) 唯一真正的自动化标记
  patch(navigator, 'webdriver', undefined);
  // 2) headless 缺失的 window.chrome（真实 Edge 一定有）
  if (typeof window.chrome === 'undefined') {
    window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){}, app: {} };
  }
  // 3) 权限查询：headless 下 notifications 会立刻 denied，真人通常是 prompt/default
  try {
    const orig = navigator.permissions && navigator.permissions.query;
    if (orig) {
      navigator.permissions.query = (p) =>
        (p && p.name === 'notifications')
          ? Promise.resolve({ state: Notification.permission || 'prompt' })
          : orig(p);
    }
  } catch (e) {}
})();
"""

# 仅在无头模式/插件列表为空时补齐（有头真实 Edge 不需要）
STEALTH_PLUGINS_JS = """
(() => {
  try {
    if (navigator.plugins && navigator.plugins.length > 0) return;
  } catch (e) { return; }
  const plugins = [
    {name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format'},
    {name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format'},
    {name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format'},
    {name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format'},
    {name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer', description: 'Portable Document Format'}
  ];
  const arr = plugins.slice();
  arr.forEach((p, i) => { p.length = 1; Object.defineProperty(p, i, {value: p}); });
  arr.item = (i) => arr[i]; arr.namedItem = (n) => arr.find(p => p.name === n) || null;
  arr.refresh = () => {};
  try { Object.defineProperty(navigator, 'plugins', {get: () => arr, configurable: true}); } catch (e) {}
  try { Object.defineProperty(navigator, 'mimeTypes', {get: () => [1,2,3], configurable: true}); } catch (e) {}
})();
"""


def collect(page) -> dict:
    """在页面上执行采集脚本，返回指纹字典。失败返回空字典。"""
    try:
        return page.evaluate(COLLECT_JS) or {}
    except Exception:
        return {}


def checks(fp: dict) -> list:
    """交叉一致性校验，返回 [(项, 现状, 是否通过)]。"""
    out = []
    if not fp:
        return [("指纹采集", "失败", False)]

    # 1) webdriver 必须不存在
    wd = fp.get("webdriver")
    out.append(("webdriver 标记", "已移除" if wd in (None, False, "undefined") else f"仍为 {wd}",
                wd in (None, False)))

    # 2) 视口不得大于屏幕
    s, w = fp.get("screen", {}), fp.get("window", {})
    if s and w:
        ok = w.get("innerW", 0) <= s.get("w", 10**9) and w.get("innerH", 0) <= s.get("h", 10**9)
        out.append(("视口 ≤ 屏幕",
                    f"{w.get('innerW')}x{w.get('innerH')} / 屏 {s.get('w')}x{s.get('h')}", ok))
        # 有头浏览器：outer 应大于 inner（有工具栏/边框）
        ok2 = (w.get("outerH", 0) - w.get("innerH", 0)) >= 0
        out.append(("outer ≥ inner", f"{w.get('outerH')} / {w.get('innerH')}", ok2))
        # 窗口不能超出可用区域太多
        ok3 = w.get("innerH", 0) <= s.get("availH", 10**9) + 2
        out.append(("高度 ≤ 可用高度", f"{w.get('innerH')} / {s.get('availH')}", ok3))

    # 3) devicePixelRatio 合理
    dpr = (w or {}).get("dpr", 1)
    out.append(("devicePixelRatio", str(dpr), 0.5 <= (dpr or 1) <= 4.0))

    # 4) 色深
    cd = (s or {}).get("colorDepth", 24)
    out.append(("颜色深度", str(cd), cd in (24, 30, 32)))

    # 5) UA 与 userAgentData 的 Edge 版本一致
    ua = fp.get("userAgent") or ""
    uad = fp.get("uaData") or {}
    brands = uad.get("brands") or []
    edge_ua = ""
    for tok in ua.split():
        if tok.startswith("Edg/"):
            edge_ua = tok.split("/", 1)[1]
    edge_brand = ""
    for b in brands:
        if "Edge" in (b.get("brand") or ""):
            edge_brand = (b.get("version") or "").split(".")[0]
    if edge_ua or edge_brand:
        out.append(("Edge 版本一致", f"UA {edge_ua or '-'} / UAData {edge_brand or '-'}",
                    bool(edge_ua) and bool(edge_brand) and edge_ua.split(".")[0] == edge_brand))
    else:
        out.append(("Edge 版本一致", "未取到", False))

    # 6) 平台字段与 UA 里的平台一致
    plat = (uad.get("platform") or "").lower()
    ua_platform = "windows" if "Windows" in ua else ("linux" if "Linux" in ua else
                                                     ("mac" if "Macintosh" in ua else ""))
    out.append(("平台一致", f"{plat or '-'} / UA {ua_platform or '-'}",
                bool(plat) and plat.startswith(ua_platform[:3]) if ua_platform else False))

    # 7) window.chrome 存在（真实 Edge 必备）
    out.append(("window.chrome", "存在" if fp.get("hasChrome") else "缺失", bool(fp.get("hasChrome"))))

    # 8) 语言与时区
    langs = fp.get("languages") or []
    out.append(("languages 非空", ",".join(langs[:3]) or "-", len(langs) > 0))
    tz = fp.get("timezone") or ""
    out.append(("时区", tz or "-", bool(tz)))

    # 9) 硬件值必须真实存在（不能被硬编码成可疑的 8/8）
    hc = fp.get("hardwareConcurrency")
    out.append(("CPU 核心数", str(hc), isinstance(hc, int) and hc > 0))

    # 10) 无头特征：插件为空 + 无 chrome + 视口异常
    plugins = fp.get("plugins") or []
    out.append(("插件列表", f"{len(plugins)} 个", len(plugins) > 0))

    # 11) WebGL 是否可用（无 GPU 的无头环境常为空）
    wgl = fp.get("webgl") or {}
    out.append(("WebGL", (wgl.get("renderer") or "不可用")[:40], bool(wgl.get("renderer"))))
    return out


def summarize(fp: dict) -> str:
    s = fp.get("screen", {})
    w = fp.get("window", {})
    wgl = fp.get("webgl", {})
    return (f"{s.get('w')}x{s.get('h')}@{w.get('dpr')} · 视口 {w.get('innerW')}x{w.get('innerH')} · "
            f"{fp.get('hardwareConcurrency')}核 · {fp.get('timezone')} · "
            f"{(wgl.get('renderer') or 'no-webgl')[:28]}")


def risk_score(checks_result: list) -> tuple:
    """根据校验结果给一个 0~100 的风险分（越高越可疑）。"""
    if not checks_result:
        return 100, ["无校验结果"]
    failed = [c for c in checks_result if not c[2]]
    score = int(round(100.0 * len(failed) / len(checks_result)))
    return score, [f"{c[0]}：{c[1]}" for c in failed]

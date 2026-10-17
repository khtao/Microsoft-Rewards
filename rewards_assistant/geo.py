# -*- coding: utf-8 -*-
"""地理位置一致性：让 IP、时区、语言、搜索市场、当地作息互相自洽。

风控最容易抓的一类"不一致"：
  · IP 在广州，浏览器时区却是 America/New_York
  · 时区 Asia/Shanghai，Accept-Language 却是 en-US
  · Bing 的 mkt=zh-CN，但请求来自境外 IP
  · 当地凌晨 3 点还在高频搜索（作息不合理）

本模块探测出口 IP 的真实地理位置，推导出一整套自洽参数，
并在 selftest 里逐项校验是否冲突。探测失败时优雅降级到系统时区/语言，
不会让脚本跑不起来。
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo, available_timezones
    _HAS_ZONEINFO = True
except Exception:                                   # pragma: no cover
    _HAS_ZONEINFO = False
    ZoneInfo = None                                  # type: ignore

CACHE_PATH = Path.home() / ".cache/human_search_geo.json"
CACHE_TTL = 6 * 3600                                 # 6 小时

# 免费、无需 key 的 IP 归属查询（按顺序尝试）
ENDPOINTS = (
    ("http://ip-api.com/json/?fields=status,country,countryCode,regionName,city,timezone,lat,lon,isp,as,query",
     "ip-api"),
    ("https://ipapi.co/json/", "ipapi"),
    ("https://ipinfo.io/json", "ipinfo"),
)

# 国家/地区 -> (语言标签, Bing 市场, 常用时区)
COUNTRY_PROFILE = {
    "CN": ("zh-CN", "zh-CN", "Asia/Shanghai"),
    "HK": ("zh-HK", "zh-HK", "Asia/Hong_Kong"),
    "TW": ("zh-TW", "zh-TW", "Asia/Taipei"),
    "MO": ("zh-MO", "zh-MO", "Asia/Macau"),
    "JP": ("ja-JP", "ja-JP", "Asia/Tokyo"),
    "KR": ("ko-KR", "ko-KR", "Asia/Seoul"),
    "SG": ("en-SG", "en-SG", "Asia/Singapore"),
    "US": ("en-US", "en-US", "America/New_York"),
    "GB": ("en-GB", "en-GB", "Europe/London"),
    "DE": ("de-DE", "de-DE", "Europe/Berlin"),
    "FR": ("fr-FR", "fr-FR", "Europe/Paris"),
    "AU": ("en-AU", "en-AU", "Australia/Sydney"),
    "CA": ("en-CA", "en-CA", "America/Toronto"),
    "IN": ("en-IN", "en-IN", "Asia/Kolkata"),
    "RU": ("ru-RU", "ru-RU", "Europe/Moscow"),
    "BR": ("pt-BR", "pt-BR", "America/Sao_Paulo"),
}

# 语言标签 -> Accept-Language 头（带 q 值，和真实浏览器一致）
ACCEPT_LANGUAGE = {
    "zh-CN": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    "zh-TW": "zh-TW,zh;q=0.9,en;q=0.8",
    "zh-HK": "zh-HK,zh;q=0.9,en;q=0.8",
    "ja-JP": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
    "ko-KR": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "en-US": "en-US,en;q=0.9",
    "en-GB": "en-GB,en;q=0.9,en-US;q=0.8",
    "de-DE": "de-DE,de;q=0.9,en;q=0.8",
    "fr-FR": "fr-FR,fr;q=0.9,en;q=0.8",
    "ru-RU": "ru-RU,ru;q=0.9,en;q=0.8",
    "pt-BR": "pt-BR,pt;q=0.9,en;q=0.8",
    "en-IN": "en-IN,en;q=0.9",
    "en-SG": "en-SG,en;q=0.9",
    "en-AU": "en-AU,en;q=0.9",
    "en-CA": "en-CA,en;q=0.9",
}

# 中国主要城市（用于本地化查询与"当地感"）
CN_CITIES = ("北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "西安",
             "南京", "重庆", "苏州", "天津", "长沙", "郑州", "青岛", "东莞")


@dataclass
class GeoProfile:
    """一整套自洽的地理/区域参数。"""
    ip: str = ""
    country: str = ""
    country_code: str = ""
    region: str = ""
    city: str = ""
    timezone: str = "Asia/Shanghai"
    latitude: float | None = None
    longitude: float | None = None
    isp: str = ""
    asn: str = ""
    locale: str = "zh-CN"
    market: str = "zh-CN"           # Bing 的 mkt=
    accept_language: str = ""
    detected: bool = False          # IP 探测是否成功
    source: str = "fallback"        # 数据来源
    ts: float = field(default_factory=time.time)

    # ---------------- 派生
    @property
    def tz_offset_hours(self) -> float:
        """当前时刻的 UTC 偏移（含夏令时）。失败返回本地系统偏移。"""
        if _HAS_ZONEINFO:
            try:
                tz = ZoneInfo(self.timezone)
                off = datetime.now(tz).utcoffset()
                if off is not None:
                    return round(off.total_seconds() / 3600.0, 2)
            except Exception:
                pass
        return round(-time.timezone / 3600.0, 2)

    @property
    def languages(self) -> list:
        """navigator.languages 应返回的值（主语言 + 简写 + 英文兜底）。"""
        base = self.locale
        out = [base]
        short = base.split("-")[0]
        if short != base:
            out.append(short)
        if not base.startswith("en"):
            out += ["en-US", "en"]
        return out

    @property
    def local_hour(self) -> int:
        return datetime.now(timezone(timedelta(hours=self.tz_offset_hours))).hour

    @property
    def home_url(self) -> str:
        """Bing 首页：市场参数与地理位置一致。"""
        return f"https://www.bing.com/?mkt={self.market}"

    def local_city(self) -> str:
        """用于生成"当地感"查询的城市名。"""
        if self.city and self.country_code in ("CN", "HK", "TW", "MO"):
            return self.city
        if self.country_code == "CN" and self.region:
            return self.region
        return self.city or (self.region if self.region else "")

    def is_chinese(self) -> bool:
        return self.locale.startswith("zh")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tz_offset_hours"] = self.tz_offset_hours
        d["local_hour"] = self.local_hour
        return d


# ------------------------------------------------------------------ 探测

def _http_json(url: str, timeout: float = 5.0):
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"),
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def _normalize(data: dict, source: str) -> dict:
    """把三家 API 的字段统一成一套。"""
    out = {"source": source}
    if source == "ip-api":
        out.update(ip=data.get("query", ""), country=data.get("country", ""),
                   country_code=data.get("countryCode", ""), region=data.get("regionName", ""),
                   city=data.get("city", ""), timezone=data.get("timezone", ""),
                   latitude=data.get("lat"), longitude=data.get("lon"),
                   isp=data.get("isp", ""), asn=data.get("as", ""))
    elif source == "ipapi":
        out.update(ip=data.get("ip", ""), country=data.get("country_name", ""),
                   country_code=data.get("country_code", ""),
                   region=data.get("region", ""), city=data.get("city", ""),
                   timezone=data.get("timezone", ""),
                   latitude=data.get("latitude"), longitude=data.get("longitude"),
                   isp=data.get("org", ""), asn=data.get("asn", ""))
    else:                                            # ipinfo
        loc = (data.get("loc") or "").split(",")
        out.update(ip=data.get("ip", ""), country=data.get("country", ""),
                   country_code=data.get("country", ""),
                   region=data.get("region", ""), city=data.get("city", ""),
                   timezone=data.get("timezone", ""),
                   latitude=float(loc[0]) if len(loc) == 2 and loc[0] else None,
                   longitude=float(loc[1]) if len(loc) == 2 and loc[1] else None,
                   isp=data.get("org", ""), asn="")
    return out


def _load_cache() -> dict | None:
    try:
        if CACHE_PATH.exists() and time.time() - CACHE_PATH.stat().st_mtime < CACHE_TTL:
            d = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            if d.get("ip"):
                return d
    except Exception:
        pass
    return None


def _save_cache(d: dict):
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _system_fallback() -> GeoProfile:
    """探测失败：用系统时区/语言兜底，保证脚本仍可运行。"""
    tz = "Asia/Shanghai"
    try:
        if os.path.exists("/etc/timezone"):
            tz = Path("/etc/timezone").read_text(encoding="utf-8").strip() or tz
        elif "TZ" in os.environ:
            tz = os.environ["TZ"]
    except Exception:
        pass
    loc = os.environ.get("LANG", "zh-CN")
    locale = "zh-CN"
    for k in COUNTRY_PROFILE.values():
        if loc.lower().startswith(k[0][:2].lower()):
            locale = k[0]
            break
    prof = COUNTRY_PROFILE.get("CN", ("zh-CN", "zh-CN", tz))
    market = next((m for c, m, _ in COUNTRY_PROFILE.values() if c == locale), locale)
    return GeoProfile(timezone=tz, locale=locale, market=market,
                      accept_language=ACCEPT_LANGUAGE.get(locale, "zh-CN,zh;q=0.9"),
                      detected=False, source="fallback")


def detect(use_cache: bool = True, timeout: float = 5.0,
           verbose: bool = False, force: bool = False) -> GeoProfile:
    """探测出口 IP 归属，返回自洽的 GeoProfile。失败自动降级。"""
    raw = None
    if use_cache and not force:
        raw = _load_cache()
        if raw and verbose:
            print(f"[信息] 使用缓存的 IP 归属（{raw.get('source')}），--force-geo 可强制刷新")

    if raw is None:
        for url, name in ENDPOINTS:
            try:
                data = _http_json(url, timeout=timeout)
                if not data or data.get("status") == "fail":
                    continue
                raw = _normalize(data, name)
                _save_cache(raw)
                break
            except Exception:
                continue

    if raw is None:
        if verbose:
            print("[警告] IP 归属探测失败（离线或被拦截），使用系统时区/语言兜底")
        p = _system_fallback()
        return p

    cc = (raw.get("country_code") or "").upper()
    locale, market, tz_default = COUNTRY_PROFILE.get(cc, (None, None, None))
    tz = raw.get("timezone") or tz_default or "UTC"
    if not locale:
        # 未知国家：按语言习惯给英文，市场用国家代码
        locale = f"en-{cc}" if cc else "en-US"
        market = f"en-{cc}" if cc else "en-US"
        tz = raw.get("timezone") or "UTC"

    p = GeoProfile(
        ip=raw.get("ip", ""), country=raw.get("country", ""), country_code=cc,
        region=raw.get("region", ""), city=raw.get("city", ""), timezone=tz,
        latitude=raw.get("latitude"), longitude=raw.get("longitude"),
        isp=raw.get("isp", ""), asn=raw.get("asn", ""),
        locale=locale, market=market,
        accept_language=ACCEPT_LANGUAGE.get(locale, f"{locale},{locale[:2]};q=0.9"),
        detected=True, source=raw.get("source", "?"),
    )
    if verbose:
        print(f"[信息] IP 归属：{p.city or p.region} / {p.country}（{p.ip}）"
              f" · 时区 {p.timezone} · 市场 {p.market} · 来源 {p.source}")
    return p


# ------------------------------------------------------------------ 一致性校验

def consistency_checks(geo: GeoProfile, browser_tz: str | None = None,
                       browser_locale: str | None = None,
                       browser_languages: list | None = None) -> list:
    """逐项校验地理一致性，返回 [(项, 现状, 是否通过)]。"""
    out = []
    # 1) 浏览器时区 vs IP 时区
    if browser_tz:
        same = browser_tz == geo.timezone or browser_tz.replace(" ", "_") == geo.timezone
        out.append(("时区一致性", f"浏览器 {browser_tz} / IP {geo.timezone}", same))
    # 2) 浏览器语言 vs 地区语言
    if browser_locale:
        ok = browser_locale.split("-")[0] == geo.locale.split("-")[0]
        out.append(("语言一致性", f"浏览器 {browser_locale} / 地区 {geo.locale}", ok))
    # 3) navigator.languages 首项
    if browser_languages:
        first = (browser_languages[0] if browser_languages else "")
        ok = first.split("-")[0] == geo.locale.split("-")[0]
        out.append(("languages 首项", f"{first} / 期望 {geo.locale}", ok))
    # 4) 市场与地区匹配
    ok = geo.market.split("-")[-1] == geo.country_code or geo.market == geo.locale
    out.append(("搜索市场", f"mkt={geo.market} / {geo.country_code or '?'}", ok))
    # 5) 时区偏移合理
    off = geo.tz_offset_hours
    ok = -12 <= off <= 14
    out.append(("时区偏移", f"UTC{off:+.2f}", ok))
    return out


def describe(geo: GeoProfile) -> str:
    bits = [f"{geo.city or geo.region or '未知'}/{geo.country or geo.country_code or '?'}",
            f"tz={geo.timezone}", f"locale={geo.locale}", f"mkt={geo.market}"]
    if geo.detected:
        bits.append(f"ip={geo.ip}")
    return " · ".join(bits)

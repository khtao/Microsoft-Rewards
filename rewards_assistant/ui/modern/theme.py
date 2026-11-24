# -*- coding: utf-8 -*-
"""截图配色：浅灰蓝底 + 白色圆角卡 + 亮蓝主按钮；附深色主题。

纯字符串模块，不 import Qt，方便离线单测。自绘组件通过 ``current()`` 取色。
"""
from __future__ import annotations

from typing import Dict

LIGHT: Dict[str, str] = {
    "window": "#f2f5fa",
    "topbar": "#eaf0f8",
    "card": "#ffffff",
    "card_alt": "#f7f9fc",
    "text": "#1b2430",
    "text_sub": "#7b8798",
    "text_muted": "#9aa6b6",
    "border": "#e5eaf2",
    "primary": "#2f6bff",
    "primary_soft": "#e8f0ff",
    "blue_chip": "#e7efff",
    "blue_chip_text": "#2f6bff",
    "green": "#2f9e5f",
    "green_soft": "#e6f5ec",
    "green_text": "#248a4f",
    "track": "#e9edf4",
    "progress": "#3aa76d",
    "segment_bg": "#e9edf5",
    "segment_on": "#ffffff",
    "warn": "#c92a2a",
    "danger": "#e0454b",
    "danger_hover": "#c9363c",
    "orange": "#f59e0b",
    "purple": "#7c5cff",
    "scroll": "#c9d2e0",
    # 对齐 hsa-shared.css 的 .hover-tip：--surface-raised 底 / --fg 字 /
    # 1px --border / 圆角 / 投影，绝不用系统黑色 tooltip 框
    "tip_bg": "#FFFFFF",
    "tip_fg": "#11181C",
    "tip_muted": "#8A939C",
    "tip_border": "rgba(0,0,0,0.07)",
    "primary_hover": "#1f5cf0",
    "card_hover": "#fbfcff",
    "row_hover": "#f0f4fb",
    "border_hover": "#b9cdf7",
}

DARK: Dict[str, str] = {
    "window": "#141922",
    "topbar": "#1a2130",
    "card": "#202836",
    "card_alt": "#1b2230",
    "text": "#e9eef7",
    "text_sub": "#98a5b8",
    "text_muted": "#75829a",
    "border": "#2b3546",
    "primary": "#5b8cff",
    "primary_soft": "#243454",
    "blue_chip": "#27395f",
    "blue_chip_text": "#8fb0ff",
    "green": "#4cc38a",
    "green_soft": "#1d3a2c",
    "green_text": "#5fd39a",
    "track": "#2a3342",
    "progress": "#4cc38a",
    "segment_bg": "#2a3342",
    "segment_on": "#3b4658",
    "warn": "#ff7a7a",
    "danger": "#ef5b62",
    "danger_hover": "#ff7178",
    "orange": "#ffc14d",
    "purple": "#a08bff",
    "scroll": "#3a4658",
    "tip_bg": "#202026",
    "tip_fg": "#ECEDEE",
    "tip_muted": "#9BA1A6",
    "tip_border": "rgba(255,255,255,0.12)",
    "primary_hover": "#6f9bff",
    "card_hover": "#242c3b",
    "row_hover": "#222b39",
    "border_hover": "#3d5578",
}

_CURRENT = dict(LIGHT)
_SCHEME = "light"


def set_scheme(scheme: str) -> str:
    global _CURRENT, _SCHEME
    _SCHEME = "dark" if scheme == "dark" else "light"
    _CURRENT = dict(DARK if _SCHEME == "dark" else LIGHT)
    return _SCHEME


def current() -> Dict[str, str]:
    return dict(_CURRENT)


def scheme() -> str:
    return _SCHEME


def resolve_theme(choice: str) -> str:
    """「跟随系统」读取 Qt 色彩方案；不可用时回退浅色。"""
    if choice in ("浅色", "Light", "light"):
        return "light"
    if choice in ("深色", "Dark", "dark"):
        return "dark"
    try:
        from PySide6.QtGui import QGuiApplication

        hints = QGuiApplication.styleHints()
        cs = getattr(hints, "colorScheme", None)
        if cs is not None and str(cs).endswith("Dark"):
            return "dark"
    except Exception:
        pass
    return "light"


def stylesheet() -> str:
    c = _CURRENT
    return f"""
QWidget#modernRoot {{
    background: {c['window']};
    color: {c['text']};
    font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "WenQuanYi Micro Hei", sans-serif;
    font-size: 13px;
}}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 6px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['scroll']}; border-radius: 3px; min-height: 28px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QFrame#card {{
    background: {c['card']};
    border: 1px solid {c['border']};
    border-radius: 16px;
}}
QFrame#flat {{
    background: {c['card_alt']};
    border: 1px solid {c['border']};
    border-radius: 12px;
}}
QFrame#heroBar {{
    background: {c['card_alt']};
    border: 1px solid {c['border']};
    border-radius: 10px;
}}
QLabel {{ background: transparent; color: {c['text']}; }}
QLabel#muted {{ color: {c['text_sub']}; }}
QLabel#tiny {{ color: {c['text_muted']}; font-size: 11px; }}
/* 设置页正文：字体家族沿用全局，字号与「外观」卡 Row 标签一致（13px）。
   说明文字、环境行名称与状态标记全部用它，不再混用
   #tiny 的 11px / 内联 12px。徽章与胶囊保留各自小字号以免溢出。 */
QLabel#body {{ color: {c['text_sub']}; font-size: 13px; }}
/* 设置页输入框（时间、环境名称）与正文同字号；占位色由控件 palette
   随浅色/深色主题设置，QSS 不写死。无秒段、无上下箭头。 */
QLineEdit#body {{ font-size: 13px; }}
QLineEdit#body:focus {{ border-color: {c['primary']}; }}
QLineEdit#body:disabled {{ color: {c['text_muted']}; }}
QLabel#group {{ color: {c['text_sub']}; font-size: 12px; }}
QLabel#cardTitle {{ font-weight: 600; font-size: 15px; }}
QLabel#status {{ color: {c['text_sub']}; font-size: 12px; }}
QPushButton {{
    background: {c['card']}; color: {c['text']};
    border: 1px solid {c['border']}; border-radius: 10px;
    padding: 7px 12px; font-size: 13px;
}}
QPushButton:hover {{ border-color: {c['primary']}; }}
QPushButton:flat {{ border: none; background: transparent; color: {c['blue_chip_text']}; }}
QPushButton#primary {{
    background: {c['primary']}; color: #ffffff; border: none;
    border-radius: 12px; font-size: 15px; font-weight: 600; padding: 13px;
}}
QPushButton#primary:hover {{ background: {c['primary_hover']}; }}
QPushButton#primary[danger="true"] {{
    background: {c['danger']}; color: #ffffff; border: none;
}}
QPushButton#primary[danger="true"]:hover {{ background: {c['danger_hover']}; }}
QPushButton#primary[danger="true"]:disabled {{ background: {c['danger']}; }}
QPushButton#icon {{ border: none; background: transparent; font-size: 17px; padding: 4px; }}
/* 顶部栏：品牌图标 / 标题 / 登录状态 / 齿轮固定同一行高（24px），
   文字字号统一 16px（与「积分管理助手」标题一致）。品牌图标是纯展示
   QLabel，不是刷新按钮；刷新只由中央进度圆环承担。 */
QLabel#brandIcon {{ min-height: 24px; max-height: 24px; }}
QLabel#topTitle {{ font-size: 16px; font-weight: 700; min-height: 24px; max-height: 24px; }}
QPushButton#topAction {{
    border: none; background: transparent; font-size: 16px;
    padding: 0px 4px; min-height: 24px; max-height: 24px;
}}
QPushButton#topAction:hover {{ background: {c['primary_soft']}; border-radius: 8px; }}
QPushButton#seg {{
    border: none; background: transparent; color: {c['text_sub']};
    border-radius: 8px; padding: 6px 10px; font-size: 13px;
}}
/* 环境行按钮：紧凑内边距，保证「删除账号 / 设为默认」四字文案完整显示。 */
QPushButton#envBtn {{ padding: 5px 8px; font-size: 13px; }}
QPushButton#seg:checked {{
    background: {c['segment_on']}; color: {c['text']}; font-weight: 600;
}}
QFrame#segBox {{ background: {c['segment_bg']}; border: none; border-radius: 10px; }}
QFrame#chip {{
    background: {c['blue_chip']}; border: none; border-radius: 14px;
}}
QLabel#chipText {{ color: {c['blue_chip_text']}; font-size: 12px; font-weight: 600; }}
QPushButton#chipX {{
    border: none; background: transparent; color: {c['blue_chip_text']};
    font-size: 13px; padding: 0px; min-width: 16px;
}}
QFrame#badge {{
    background: {c['green_soft']}; border: none; border-radius: 9px;
}}
QLabel#badgeText {{ color: {c['green_text']}; font-size: 11px; font-weight: 600; }}
QFrame#numBadge {{
    background: {c['green']}; border: none; border-radius: 11px;
}}
QLabel#numBadgeText {{ color: #ffffff; font-size: 11px; font-weight: 700; }}
QFrame#taskRow {{
    background: {c['card_alt']}; border: 1px solid {c['border']}; border-radius: 12px;
}}
QFrame#numberTag {{
    background: {c['primary_soft']}; border: none; border-radius: 6px;
}}
QLabel#numberTagText {{ color: {c['primary']}; font-size: 11px; font-weight: 700; }}
/* 数值输入统一用 QLineEdit + QIntValidator/QDoubleValidator：
   QSpinBox 在样式表下会把内部 lineEdit 挤成 1px，数值不可见。 */
QLineEdit {{
    background: {c['card']}; color: {c['text']};
    border: 1px solid {c['border']}; border-radius: 10px; padding: 7px 9px;
}}
QProgressBar {{
    background: {c['track']}; border: none; border-radius: 4px; height: 7px;
}}
QProgressBar::chunk {{ background: {c['progress']}; border-radius: 4px; }}

/* ---- 插件版 hover 反馈（网页 CSS 用 180ms transition；Qt QSS 无 transition，
        所有透明度 / 圆环 / 进度条 / 开关 / 入场动画由 ui.modern.effects 负责） ---- */
QFrame#card:hover {{ background: {c['card_hover']}; border-color: {c['border_hover']}; }}
QFrame#flat:hover {{ border-color: {c['border_hover']}; }}
QFrame#taskRow:hover {{ background: {c['row_hover']}; border-color: {c['border_hover']}; }}
QPushButton#primary:hover {{ background: {c['primary_hover']}; }}
QPushButton#seg:hover {{ color: {c['text']}; }}
QPushButton#icon:hover {{ background: {c['primary_soft']}; border-radius: 8px; }}
QLineEdit:focus {{ border-color: {c['primary']}; }}
QPushButton:disabled {{ color: {c['text_muted']}; }}
"""

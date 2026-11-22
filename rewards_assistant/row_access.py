# -*- coding: utf-8 -*-
"""sqlite3.Row / dict / 轻量对象的统一字段取值。

Store 返回 sqlite3.Row，离线测试与界面预览常用普通 dict 或数据类实例；
「缺字段返回默认值」的规则集中在这里，同步层与界面层共用同一份实现。
"""
from __future__ import annotations

from typing import Any


def get_field(row: Any, key: str, default=None):
    """命中字段时直接下标取值最快；缺字段才回退到属性或默认值。

    Store 的行与界面字典绝大多数调用都是命中，故不预先做类型判断——
    额外的 isinstance / .get 会让这条热路径变慢。
    """
    if row is None:
        return default
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return getattr(row, key, default)

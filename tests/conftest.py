# -*- coding: utf-8 -*-
"""测试环境适配：无图形显示时跳过需要真实 QApplication 的交互用例。

这些用例在桌面环境（有 DISPLAY / WAYLAND_DISPLAY）或用 ``xvfb-run`` 时
会正常执行；纯逻辑与静态契约测试始终运行。
"""
from __future__ import annotations

import os
import sys

import pytest

GUI_TEST_FILES = ("tests/test_tray_menu.py", "tests/test_ui_settings.py")


def pytest_collection_modifyitems(config, items):
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if not sys.platform.startswith("linux") or has_display:
        return
    marker = pytest.mark.skip(
        reason="无图形显示环境：跳过 QApplication 交互用例（桌面或 xvfb-run 下会执行）")
    for item in items:
        if any(item.nodeid.startswith(prefix) for prefix in GUI_TEST_FILES):
            item.add_marker(marker)

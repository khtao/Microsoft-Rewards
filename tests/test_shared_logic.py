# -*- coding: utf-8 -*-
"""共享取字段、设置默认值复制与去重 helper 的行为契约（离线）。"""
from __future__ import annotations

import copy
import sqlite3
from pathlib import Path

from rewards_assistant import browser, config, sync
from rewards_assistant.row_access import get_field
from rewards_assistant.ui.modern import effects, viewmodel as vm

ROOT = Path(__file__).resolve().parents[1]


def _module_text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_get_field_handles_row_dict_object_and_missing():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    row = con.execute("select 1 as a, 2 as b").fetchone()

    class Box:
        a = 7

        def __getitem__(self, key):
            raise TypeError("not subscriptable")

    assert get_field(row, "a") == 1
    assert get_field(row, "missing", 99) == 99
    assert get_field({"a": 3}, "a") == 3
    assert get_field({"a": 3}, "missing", 99) == 99
    assert get_field(Box(), "a") == 7
    assert get_field(Box(), "missing", 99) == 99
    assert get_field(None, "a", 99) == 99


def test_sync_and_viewmodel_share_one_accessor():
    assert sync._col is get_field
    assert vm._get is get_field
    assert "def get_field" in _module_text("rewards_assistant/row_access.py")


def test_copy_defaults_matches_deepcopy_and_isolates_nested():
    a = config._copy_defaults(config.DEFAULT_SETTINGS)
    b = copy.deepcopy(config.DEFAULT_SETTINGS)
    assert a == b and a is not config.DEFAULT_SETTINGS
    a["auto_run_times"].append("99:99")
    a["search_panel"]["probe"] = 1
    assert b == config.DEFAULT_SETTINGS
    assert "99:99" not in config.DEFAULT_SETTINGS["auto_run_times"]
    assert "probe" not in config.DEFAULT_SETTINGS["search_panel"]


def test_settings_starts_from_independent_defaults():
    s = config.Settings()
    s._data["auto_run_times"].append("23:59")
    assert "23:59" not in config.DEFAULT_SETTINGS["auto_run_times"]


def test_error_classification_uses_one_matcher():
    text = _module_text("rewards_assistant/browser.py")
    assert text.count("def _error_matches") == 1
    assert text.count("low = str(exc).lower()") == 1
    assert browser._error_matches(ValueError("HAS BEEN CLOSED"),
                                  browser._RETRYABLE_LAUNCH_ERRORS)
    assert browser.is_profile_conflict_error(ValueError("browser has been closed"))
    assert browser.is_launch_arg_error(ValueError("Unexpected option: permissions"))
    assert not browser.is_launch_arg_error(ValueError("Permission denied"))


def test_fade_cleanup_is_shared_by_both_animations():
    text = _module_text("rewards_assistant/ui/modern/effects.py")
    assert text.count("def _clear_fade") == 1
    assert text.count("def _fade_restore") == 1
    assert text.count("_fade_restore(") >= 2
    assert effects._clear_fade is not None


def test_viewmodel_imports_timedelta_once():
    text = _module_text("rewards_assistant/ui/modern/viewmodel.py")
    assert "from datetime import date, timedelta" in text
    assert "    from datetime import" not in text

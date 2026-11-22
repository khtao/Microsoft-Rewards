"""可在没有 PySide6 的环境下执行的设置回归测试。"""
import json

import pytest

from rewards_assistant.config import Settings, DEFAULT_SETTINGS


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    return tmp_path


def test_defaults_and_batch_roundtrip(home):
    settings = Settings()
    assert settings.get("auto_open_time") is None
    assert settings.update({"auto_open_time": "14:35", "auto_run_pending": True,
                            "search_panel": {"repeat": 17, "keyword_sources": []},
                            "chart_days": 90, "chart_metric": "streak"})
    saved = home / "settings.json"
    assert saved.is_file()
    assert Settings().get("search_panel") == {"repeat": 17, "keyword_sources": []}
    assert Settings().get("auto_open_time") is None
    assert Settings().get("chart_days") == 90
    assert not settings.update({"auto_run_pending": True})
    assert json.loads(saved.read_text(encoding="utf-8"))["chart_metric"] == "streak"


def test_disabled_settings_keep_editable_values(home):
    settings = Settings()
    settings.update({"sync_interval_minutes": 0, "sync_interval_value_minutes": 37,
                     "remind_time": "", "remind_time_value": "11:25",
                     "evening_remind_time": "", "evening_remind_time_value": "18:15"})
    again = Settings()
    assert again.get("sync_interval_minutes") == 0
    assert again.get("sync_interval_value_minutes") == 37
    assert again.get("remind_time_value") == "11:25"
    assert again.get("evening_remind_time_value") == "18:15"


def test_atomic_replace_does_not_corrupt_saved_file(home, monkeypatch):
    import rewards_assistant.config as config
    s = Settings()
    s.set("chart_days", 7)
    path = home / "settings.json"
    before = path.read_bytes()
    def broken_replace(*args):
        raise OSError("disk failed")
    monkeypatch.setattr(config.os, "replace", broken_replace)
    with pytest.raises(OSError):
        s.set("chart_days", 90)
    assert s.get("chart_days") == 7
    assert path.read_bytes() == before
    assert not list(home.glob(".settings-*.tmp"))


def test_broken_json_recovers_with_defaults(home):
    (home / "settings.json").write_text('{"bad":', encoding="utf-8")
    s = Settings()
    assert s.get("chart_days") == DEFAULT_SETTINGS["chart_days"]
    s.set("chart_days", 90)
    assert Settings().get("chart_days") == 90


def test_preserve_future_keys(home):
    (home / "settings.json").write_text(json.dumps({"new_future_key": [1, 2]}))
    s = Settings()
    s.set("auto_run_pending", True)
    assert Settings().get("new_future_key") == [1, 2]


def test_old_browser_selection_is_dropped(home):
    (home / "settings.json").write_text(
        json.dumps({"browser_name": "Microsoft Edge", "chart_days": 90}), encoding="utf-8")
    settings = Settings()
    assert "browser_name" not in settings.as_dict()
    settings.set("chart_days", 30)
    assert "browser_name" not in json.loads((home / "settings.json").read_text(encoding="utf-8"))

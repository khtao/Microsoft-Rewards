"""统一状态库下的设置回归：delta 存储、损坏回默认、写失败备选方案。"""
import json

import pytest

from rewards_assistant import state
from rewards_assistant.config import Settings, DEFAULT_SETTINGS


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    state._degraded_reason = None
    state._last_event = "ok"
    return tmp_path


def test_defaults_and_batch_roundtrip(home):
    settings = Settings()
    assert settings.get("auto_open_time") is None
    assert settings.update({"auto_open_time": "14:35", "auto_run_pending": True,
                            "search_panel": {"repeat": 17, "keyword_sources": []},
                            "chart_days": 90, "chart_metric": "streak"})
    # 只有一个状态文件；旧 settings.json 不再落盘
    assert state.db_file().is_file()
    assert not (home / "settings.json").exists()
    delta = state.json_get(state.K_SETTINGS)
    assert delta is not None
    # 不存重复信息：默认值由代码提供，磁盘只留差异
    assert "sync_interval_minutes" not in delta
    assert delta["chart_metric"] == "streak"
    assert Settings().get("search_panel") == {"repeat": 17, "keyword_sources": []}
    assert Settings().get("auto_open_time") is None
    assert Settings().get("chart_days") == 90
    assert not settings.update({"auto_run_pending": True})


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


def test_write_failure_falls_back_to_standalone_file(home, monkeypatch):
    """状态库写不进去 → 自动回退独立 JSON，设置仍可恢复且不崩。"""
    import rewards_assistant.config as config

    s = Settings()
    s.set("chart_days", 7)
    assert not (home / "settings.json").exists()

    original_kv_set = state.kv_set
    monkeypatch.setattr(state, "kv_set", lambda *a, **k: False)
    s.set("chart_days", 90)
    fallback = home / "settings.json"
    assert fallback.is_file(), "库写失败必须回退到独立文件"
    assert json.loads(fallback.read_text("utf-8"))["chart_days"] == 90
    assert s.get("chart_days") == 90, "写失败也不能破坏内存态"
    assert not list(home.glob(".settings-*.tmp"))
    # 库恢复后再次保存，会清掉回退文件，重新收敛成单文件；
    # 只恢复 kv_set，保留 MS_REWARDS_HOME 指向本用例的临时目录。
    monkeypatch.setattr(state, "kv_set", original_kv_set)
    s.set("chart_days", 120)
    assert not fallback.exists()
    assert Settings().get("chart_days") == 120


def test_broken_json_recovers_with_defaults(home):
    (home / "settings.json").write_text('{"bad":', encoding="utf-8")
    s = Settings()
    assert s.get("chart_days") == DEFAULT_SETTINGS["chart_days"]
    assert not (home / "settings.json").exists(), "损坏文件按约定删除"
    s.set("chart_days", 90)
    assert Settings().get("chart_days") == 90


def test_broken_kv_value_only_resets_that_item(home):
    """单个 kv 值损坏：只回默认并删该项，不动积分库/凭证。"""
    state.kv_set(state.K_CREDENTIALS, b"opaque-credential-blob")
    state.kv_set(state.K_SETTINGS, b"not json at all")
    s = Settings()
    assert s.get("chart_days") == DEFAULT_SETTINGS["chart_days"]
    assert state.kv_get(state.K_SETTINGS) is None
    assert state.kv_get(state.K_CREDENTIALS) == b"opaque-credential-blob"


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
    delta = state.json_get(state.K_SETTINGS)
    assert "browser_name" not in delta
    assert not (home / "settings.json").exists()

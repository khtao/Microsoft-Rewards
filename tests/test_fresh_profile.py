# -*- coding: utf-8 -*-
"""回归：启动期「浏览器进程意外退出」→ 每次唯一快照 + 自动换目录重试。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from rewards_assistant import browser

ROOT = Path(__file__).resolve().parents[1]


def _source(tmp_path: Path) -> Path:
    src = tmp_path / "login_profile"
    (src / "Default").mkdir(parents=True)
    (src / "Local State").write_text("{}", encoding="utf-8")
    (src / "Default" / "Preferences").write_text("{}", encoding="utf-8")
    return src


def test_new_automation_profile_dir_is_unique(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    a = browser.new_automation_profile_dir()
    b = browser.new_automation_profile_dir()
    assert a != b
    assert a.name.startswith("automation_edge_profile_")
    assert a.is_dir() and b.is_dir()
    # 不再复用固定的 automation_edge_profile 目录
    assert a.name != "automation_edge_profile"


def test_prune_keeps_only_recent_snapshots(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    made = [browser.new_automation_profile_dir() for _ in range(6)]
    # prune 发生在下一次 new 的开头；显式收口后应恰好保留最近 3 个
    browser.prune_automation_profiles(keep=3)
    left = [d for d in tmp_path.iterdir()
            if d.is_dir() and d.name.startswith("automation_edge_profile_")]
    assert len(left) == 3
    assert all(d in made for d in left)


def test_launch_retries_with_a_brand_new_dir_on_closed_browser(tmp_path, monkeypatch):
    """截图日志：launch_persistent_context 报 browser has been closed。"""
    from playwright.sync_api import Error as PWError

    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    src = _source(tmp_path)
    used = []

    def fake_launch(pw, *, user_data_dir, **kw):
        used.append(user_data_dir)
        if len(used) == 1:
            raise PWError("BrowserType.launch_persistent_context: "
                          "Target page, context or browser has been closed")
        return object()

    monkeypatch.setattr(browser, "launch_edge", fake_launch)
    ctx, snap = browser.launch_automation_edge(object(), source_profile=src)

    assert len(used) == 2
    assert used[0] != used[1]                 # 换的是全新目录，不是同一个
    assert not Path(used[0]).exists()          # 失败快照已清理
    assert ctx is not None and snap == Path(used[1])
    assert browser.is_profile_conflict_error(PWError("browser has been closed"))
    browser.cleanup_automation_profile(snap)


def test_launch_does_not_retry_unrelated_errors(tmp_path, monkeypatch):
    from playwright.sync_api import Error as PWError

    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    src = _source(tmp_path)
    used = []

    def fake_launch(pw, *, user_data_dir, **kw):
        used.append(user_data_dir)
        raise PWError("Executable doesn't exist at msedge.exe")

    monkeypatch.setattr(browser, "launch_edge", fake_launch)
    try:
        browser.launch_automation_edge(object(), source_profile=src)
    except PWError:
        pass
    else:
        raise AssertionError("应当直接抛出，不应重试")
    assert len(used) == 1
    assert not Path(used[0]).exists()


def test_closed_browser_hint_is_actionable():
    hint = browser.browser_error_hint(
        "BrowserType.launch_persistent_context: Target page, context or "
        "browser has been closed")
    assert "意外退出" in hint
    assert "全新配置" in hint and "重试" in hint
    assert "请重试" != hint


def test_geo_describe_masks_public_ip():
    from rewards_assistant.geo import describe, mask_ip

    geo = SimpleNamespace(city="Guangzhou", region="", country="China",
                          country_code="CN", timezone="Asia/Shanghai",
                          locale="zh-CN", market="zh-CN", detected=True,
                          ip="183.62.48.142")
    out = describe(geo)
    assert "183.62.*.*" in out
    assert "183.62.48.142" not in out
    assert mask_ip("2001:db8:85a3::8a2e:370:7334") == "2001:db8:*:*"
    assert mask_ip("") == ""
    geo_src = (ROOT / "rewards_assistant" / "geo.py").read_text(encoding="utf-8")
    assert "mask_ip(p.ip)" in geo_src          # verbose 输出也不写完整 IP
    assert "（{p.ip}）" not in geo_src


def test_callers_use_unique_snapshot_and_cleanup():
    for rel in ("rewards_assistant/api.py", "rewards_assistant/activity_worker.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "launch_automation_edge" in src, rel
        assert "cleanup_automation_profile" in src, rel
        assert "copy_profile(" not in src, rel
    app = (ROOT / "rewards_assistant/api.py").read_text(encoding="utf-8")
    assert "use_snapshot" not in app
    assert "automation_profile_dir()" not in app

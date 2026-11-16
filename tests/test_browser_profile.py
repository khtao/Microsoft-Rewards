# -*- coding: utf-8 -*-
"""浏览器 profile 隔离、EXE 图标与窗口宽度的离线回归。"""
from __future__ import annotations

from pathlib import Path

from rewards_assistant import browser

ROOT = Path(__file__).resolve().parents[1]


def test_automation_profile_is_isolated_from_login_and_live_edge(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    assert browser.app_profile_dir() == tmp_path / "browser_profile"
    snap = browser.new_automation_profile_dir()
    assert snap.parent == tmp_path and snap.name.startswith("automation_edge_profile_")
    assert snap != browser.app_profile_dir()
    # 应用内已登录时，自动化读取同一份登录态（而不是日常 Edge User Data）
    (tmp_path / "browser_profile" / "Default").mkdir(parents=True)
    assert browser.automation_source_profile() == tmp_path / "browser_profile"


def test_copy_profile_skips_locked_files_without_shutil_error(tmp_path, monkeypatch):
    src = tmp_path / "src"
    (src / "Default").mkdir(parents=True)
    (src / "Local State").write_text("{}", encoding="utf-8")
    (src / "Default" / "Cookies").write_text("cookie", encoding="utf-8")
    (src / "Default" / "Preferences").write_text("{}", encoding="utf-8")
    (src / "Default" / "LOCK").write_text("x", encoding="utf-8")
    (src / "Default" / "Cache").mkdir()
    (src / "Default" / "Cache" / "big").write_text("x", encoding="utf-8")
    (src / "SingletonLock").write_text("stale", encoding="utf-8")
    dst = tmp_path / "snapshot"
    (dst).mkdir()
    (dst / "SingletonLock").write_text("stale", encoding="utf-8")

    real_copy2 = browser.shutil.copy2

    def flaky(src_file, dst_file):
        if Path(src_file).name == "Cookies":
            raise PermissionError("locked by Edge")
        return real_copy2(src_file, dst_file)

    monkeypatch.setattr(browser.shutil, "copy2", flaky)
    out = browser.copy_profile(src, dst)     # 旧版会抛 shutil.Error 导致「运行失败」

    assert out == dst
    assert (dst / "Local State").read_text(encoding="utf-8") == "{}"
    assert (dst / "Default" / "Preferences").read_text(encoding="utf-8") == "{}"
    assert not (dst / "Default" / "Cookies").exists()      # 被占用 → 跳过，不中断
    assert not (dst / "Default" / "LOCK").exists()         # 锁文件不复制
    assert not (dst / "Default" / "Cache").exists()        # 缓存目录不复制
    assert not (dst / "SingletonLock").exists()            # 陈旧锁必须清掉


def test_browser_error_hint_never_leaks_local_paths():
    raw = ("Error: [('C:\\\\Users\\\\86155\\\\AppData\\\\Local\\\\Microsoft\\\\Edge\\\\"
           "User Data\\\\Profile 1\\\\LOCK', 'snapshot', '[Errno 13] Permission denied')]")
    hint = browser.browser_error_hint(raw)
    assert "C:" not in hint and "Users" not in hint and "AppData" not in hint
    assert "占用" in hint
    assert "ProcessSingleton" not in hint
    singleton = ("BrowserType.launch_persistent_context: Failed to create a "
                 "ProcessSingleton for your profile directory.")
    assert "关闭应用自己" in browser.browser_error_hint(singleton)
    assert "Edge" in browser.browser_error_hint("Executable doesn't exist at msedge.exe")


def test_automation_callers_use_app_owned_profile_snapshot():
    aw = (ROOT / "rewards_assistant" / "activity_worker.py").read_text(encoding="utf-8")
    api = (ROOT / "rewards_assistant" / "api.py").read_text(encoding="utf-8")
    for src in (aw, api):
        assert "launch_automation_edge" in src
        assert "cleanup_automation_profile" in src
        assert "default_profile_dir()" not in src
        assert "copy_profile(" not in src
    browser = (ROOT / "rewards_assistant" / "browser.py").read_text(encoding="utf-8")
    assert "def new_automation_profile_dir" in browser
    assert "def launch_automation_edge" in browser
    assert "is_profile_conflict_error" in browser
    assert "activity_edge_profile_snapshot" not in aw
    assert ".cache/human_search_edge_profile" not in api
    assert "browser_error_hint" in (ROOT / "rewards_assistant" / "ui" / "legacy.py").read_text(encoding="utf-8")


def test_exe_icon_is_embedded_in_spec():
    spec = (ROOT / "MSRewardsAssistant.spec").read_text(encoding="utf-8")
    assert "icon=EXE_ICON" in spec
    assert "app.ico" in spec
    ico = ROOT / "assets" / "icons" / "app.ico"
    assert ico.is_file() and ico.read_bytes()[:4] == b"\x00\x00\x01\x00"
    win = (ROOT / "build_windows.bat").read_text(encoding="utf-8")
    assert "app.ico" in win


def test_main_window_width_is_fixed_and_status_wraps():
    mw = (ROOT / "rewards_assistant" / "ui" / "modern" / "main_window.py").read_text(encoding="utf-8")
    widgets = (ROOT / "rewards_assistant" / "ui" / "modern" / "widgets.py").read_text(encoding="utf-8")
    assert "setFixedWidth(392)" in mw
    assert "QEvent.Type.WindowStateChange" in mw
    assert "browser_error_hint" in mw
    assert "self._label.setWordWrap(False)" in widgets
    assert "self._label.setMaximumWidth(340)" in widgets
    assert "elidedText" in widgets


def test_activity_worker_turns_old_launch_error_into_short_hint(tmp_path, monkeypatch):
    """回归截图里的「运行失败: Error: [('C:\\Users...Permission denied')]」。"""
    from rewards_assistant import activity_worker as aw
    raw = ("Error: [('C:\\\\Users\\\\86155\\\\AppData\\\\Local\\\\Microsoft\\\\Edge\\\\"
           "User Data\\\\Profile 1\\\\LOCK', 'snap', '[Errno 13] Permission denied')]")
    monkeypatch.setattr(aw, "launch_automation_edge",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError(raw)))

    def fake_start():
        class Starter:
            def start(self):
                class PW:
                    def stop(self):
                        pass
                return PW()
        return Starter()

    import playwright.sync_api as pw_api
    monkeypatch.setattr(pw_api, "sync_playwright", fake_start)

    worker = aw.ActivityWorker([], browser_options={})
    seen = []
    worker.completed.connect(lambda stats: seen.append(stats))
    worker.run()          # 直接跑，不起线程、不弹浏览器

    assert len(seen) == 1
    msg = seen[0]["error"]
    assert "C:" not in msg and "Users" not in msg and "Error: [(" not in msg
    assert "占用" in msg

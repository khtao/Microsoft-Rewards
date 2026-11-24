# -*- coding: utf-8 -*-
"""本批界面与可观测性改动的离线回归：

1. 顶栏放大镜换成**纯展示**的积分奖章图标，文字/图标/齿轮等高同字号；
2. 领取窗口关闭事件检测失败时，从成功打开起 30 秒兜底刷新积分；
3. 自动完成任务全程复用**同一个浏览器**（点击活动卡 + 拟人搜索）；
4. 新增 DEBUG 级日志（完整堆栈 / 上下文）并与日常流水分层；
5. 导出日志与关键页面 zip 带日期时间后缀。

不访问网络、不启动真实 Edge；Playwright 与 Qt 文件对话框全部用桩。
"""
from __future__ import annotations

import os
import re
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from rewards_assistant import app as app_mod, claim, debug_log  # noqa: E402
from rewards_assistant import config  # noqa: E402
from rewards_assistant.ui import icons  # noqa: E402
from rewards_assistant.ui.modern import main_window as mw  # noqa: E402
from rewards_assistant.ui.modern import theme  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def src(rel: str) -> str:
    return (ROOT / rel).read_text("utf-8")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- 1. 顶栏图标

def test_brand_and_gear_svgs_exist_and_render(qapp):
    assets = ROOT / "assets" / "icons"
    assert (assets / "brand.svg").is_file()
    assert (assets / "gear.svg").is_file()
    assert not icons.brand_pixmap(24).isNull()
    assert not icons.gear_pixmap(20).isNull()
    # 兜底绘制不依赖 SVG 文件
    assert not icons._draw_brand_fallback(24).isNull()
    assert not icons._draw_gear_fallback(20, "#8895A8").isNull()


def test_magnifier_removed_and_brand_icon_is_display_only():
    text = src("rewards_assistant/ui/modern/main_window.py")
    for forbidden in ("🔍", "🔎", 'self.logo.clicked.connect',
                      'self.logo.setEnabled', "QPushButton(\"🔍\")"):
        assert forbidden not in text, forbidden
    assert "self.logo = QLabel()" in text
    assert 'self.logo.setObjectName("brandIcon")' in text
    assert "brand_pixmap(TOPBAR_ROW_H)" in text
    assert "gear_pixmap(28" in text
    assert "TOPBAR_ROW_H = 32" in text
    # 主题里顶栏三者等高、字号统一 16px
    css = src("rewards_assistant/ui/modern/theme.py")
    assert "QLabel#brandIcon" in css
    assert "QLabel#topTitle" in css and "font-size: 16px" in css
    assert "QPushButton#topAction" in css
    assert css.count("min-height: 32px; max-height: 32px;") >= 2


def test_topbar_widgets_share_one_row_height(qapp):
    app = SimpleNamespace(
        lang="zh", sync_now=lambda *a, **k: None,
        open_login=lambda: None, show_settings=lambda: None,
        primary_clicked=lambda: None, stop_running=lambda: None)
    home = mw.HomePage(app)
    assert isinstance(home.logo, QLabel)
    assert not home.logo.pixmap().isNull()
    for w in (home.logo, home.title, home.login_badge):
        assert w.height() == mw.TOPBAR_ROW_H, (w.objectName(), w.height())
    assert home.title.objectName() == "topTitle"
    assert home.login_badge.objectName() == "topAction"
    # 纯展示图标不随运行 / 同步状态禁用
    home.set_running(True)
    home.set_busy(True)
    assert home.logo.isEnabled() and not home.ring.isEnabled()
    home.set_running(False)
    home.set_busy(False)
    home.deleteLater()


# ---------------------------------------------------------------- 2. 30 秒兜底

class _StubPage:
    def __init__(self, close_after: int = 999) -> None:
        self.calls = 0
        self.close_after = close_after
        self.handlers = {}
        self.goto_url = ""

    def on(self, event, cb):
        self.handlers[event] = cb

    def goto(self, url, **kw):
        self.goto_url = url

    def is_closed(self):
        self.calls += 1
        return self.calls >= self.close_after

    def wait_for_timeout(self, ms):
        pass


class _StubCtx:
    def __init__(self, page) -> None:
        self.pages = [page]
        self.handlers = {}

    def on(self, event, cb):
        self.handlers[event] = cb

    def close(self):
        pass


class _StubPW:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _StubStore:
    def load(self, now=None):
        return [{"name": "MSPAuth", "value": "t"}]


class _StubFetcher:
    def __init__(self, store=None):
        pass

    def fetch_many(self, paths):
        from rewards_assistant.session import PageResult
        return {p: PageResult(path=p, ok=True, status=200,
                              html='<div>"claimablePoints":60</div>',
                              kind="ok", signin_page=False) for p in paths}


def _install_claim_stubs(monkeypatch, tmp_path, page):
    snap = tmp_path / "claim_profile"
    snap.mkdir()
    monkeypatch.setattr(claim, "HttpFetcher", _StubFetcher)
    monkeypatch.setattr(claim, "_playwright", lambda: _StubPW())
    monkeypatch.setattr(claim, "launch_automation_edge",
                        lambda pw, **kw: (_StubCtx(page), snap))
    monkeypatch.setattr(claim, "cleanup_automation_profile",
                        lambda *a, **k: None)
    monkeypatch.setattr(claim, "sleep_jitter", lambda *a, **k: 0.0)
    return snap


def test_claim_fallback_is_30_seconds_after_successful_open(monkeypatch, tmp_path):
    assert claim.CLAIM_SYNC_FALLBACK_SECONDS == 30.0
    page = _StubPage(close_after=3)
    _install_claim_stubs(monkeypatch, tmp_path, page)
    # 打开时 t=0；第一次轮询后 t=31（越过 30 秒）；第三次轮询时窗口"关掉"。
    ticks = iter([0.0, 31.0, 31.0, 31.0])
    opened, fallback = [], []
    assert claim.open_claim_window(
        store=_StubStore(), wait_for_close=True,
        on_opened=lambda: opened.append(True),
        on_close_undetected=lambda: fallback.append(True),
        clock=lambda: next(ticks, 31.0)) is True
    assert page.goto_url == claim.CLAIM_URL
    assert opened == [True], "成功打开后必须通知控制器开始计时"
    assert fallback == [True], "关闭事件未捕获时必须在 30 秒后兜底一次"
    # 事件回调注册齐全：page/context close + page crash
    assert "close" in page.handlers and "crash" in page.handlers


def test_claim_fallback_not_fired_when_close_detected(monkeypatch, tmp_path):
    page = _StubPage(close_after=1)      # 第一次轮询就发现已关闭
    _install_claim_stubs(monkeypatch, tmp_path, page)
    fallback = []
    claim.open_claim_window(
        store=_StubStore(), wait_for_close=True,
        on_close_undetected=lambda: fallback.append(True),
        clock=lambda: 999.0)
    assert fallback == [], "正常检测到关窗不应再触发 30 秒兜底"


def test_claim_stop_event_skips_fallback(monkeypatch, tmp_path):
    page = _StubPage(close_after=999)
    _install_claim_stubs(monkeypatch, tmp_path, page)
    fallback = []
    claim.open_claim_window(
        store=_StubStore(), wait_for_close=True,
        stop_event=SimpleNamespace(is_set=lambda: True),
        on_close_undetected=lambda: fallback.append(True))
    assert fallback == []


def test_application_dedupes_fallback_and_close_sync(monkeypatch, tmp_path, qapp):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    ap = app_mod.Application([])
    ap.window = None
    synced = []
    ap.sync_now = lambda auto=False: synced.append(auto)
    ap._log = lambda msg: None
    ap._claim_sync_done = False
    ap._on_claim_sync_fallback()        # 兜底先到
    ap._on_claim_closed()               # 随后又捕获到关窗：必须去重
    assert synced == [True]
    assert ap._claim_sync_done is True
    ap._on_claim_sync_fallback()
    assert synced == [True]


# ---------------------------------------------------------------- 3. 同一浏览器

def test_activity_worker_runs_search_in_the_same_browser():
    text = src("rewards_assistant/activity_worker.py")
    assert "search_params" in text
    assert "run_human_search(**params)" in text
    assert "page=page, context=context, close_browser=False" in text
    assert "stop_check=self._stop.is_set" in text
    # 整轮只启动一次 Edge、关闭一次 context / pw
    assert text.count("launch_automation_edge(") == 1
    assert text.count("context.close()") == 1
    assert text.count("pw.stop()") == 1
    assert "同一个真实 Edge" in text

    app_text = src("rewards_assistant/app.py")
    assert "search_params=search_params" in app_text
    assert "self._activity_then_search = False" in app_text
    body = app_text.split("def _on_activity_completed", 1)[1].split("\n    def ", 1)[0]
    assert "_start_pc_search()" not in body
    assert 'stats.get("search")' in body
    assert "search_params" in app_text.split("def _start_activity_clicks", 1)[1].split("\n    def ", 1)[0]


def test_activity_worker_search_params_are_optional():
    from rewards_assistant.activity_worker import ActivityWorker
    assert ActivityWorker([], browser_options={}).search_params is None
    w = ActivityWorker([], search_params={"repeat": 3})
    assert w.search_params == {"repeat": 3}


# ---------------------------------------------------------------- 4. DEBUG 日志

def test_debug_log_levels_stack_and_redaction(monkeypatch, tmp_path):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    debug_log._ENABLED = None
    try:
        raise RuntimeError("Edge exploded at C:\\Users\\secret\\profile")
    except RuntimeError as exc:
        rec = debug_log.format_record(
            "ERROR", "claim", "启动失败", exc=exc, sid="abc", repeat=3)
    assert "[ERROR]" in rec and "[claim]" in rec
    assert "exception=RuntimeError" in rec
    assert "traceback" in rec.lower() or "File" in rec
    assert "C:\\Users\\secret" not in rec
    assert "sid=abc" in rec and "repeat=3" in rec


def test_debug_log_writes_debug_file_and_run_summary(monkeypatch, tmp_path):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    monkeypatch.setenv("MS_REWARDS_DEBUG", "1")
    debug_log._ENABLED = None
    debug_log.debug("activity", "卡片已点击", task="daily", index=2)
    debug_log.info("ui", "同步完成")
    debug_log.warn("claim", "关闭事件未捕获", fallback_seconds=30)
    debug_log.error("sync", "同步失败", exc=ValueError("bad html"))
    dbg = debug_log.debug_log_path().read_text("utf-8")
    run = config.log_path().read_text("utf-8")
    assert "[DEBUG]" in dbg and "task=daily" in dbg and "index=2" in dbg
    assert "[INFO]" in dbg and "[WARN]" in dbg
    assert "[ERROR]" in dbg and "ValueError" in dbg
    assert "[ERROR] 同步失败 (ValueError)" in run
    assert "[DEBUG]" not in run and "卡片已点击" not in run
    debug_log._ENABLED = None


def test_debug_log_can_be_disabled_and_is_instance_scoped(monkeypatch, tmp_path):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    monkeypatch.setenv("MS_REWARDS_DEBUG", "0")
    debug_log._ENABLED = None
    before = (debug_log.debug_log_path().read_text("utf-8")
              if debug_log.debug_log_path().exists() else "")
    debug_log.debug("activity", "should not be written")
    after = (debug_log.debug_log_path().read_text("utf-8")
             if debug_log.debug_log_path().exists() else "")
    assert before == after
    debug_log.error("sync", "still written")
    assert "[ERROR]" in debug_log.debug_log_path().read_text("utf-8")
    assert debug_log.debug_log_path().name == "debug.log"
    assert debug_log.debug_log_path().parent == config.data_dir()
    assert config.DEFAULT_SETTINGS["debug_log_enabled"] is True
    debug_log._ENABLED = None


def test_production_code_emits_debug_records():
    for rel in ("rewards_assistant/claim.py", "rewards_assistant/activity_worker.py",
                "rewards_assistant/api.py", "rewards_assistant/app.py",
                "rewards_assistant/ui/modern/main_window.py",
                "rewards_assistant/ui/legacy.py"):
        text = src(rel)
        assert "debug_log." in text, rel
    assert "except Exception as e:" in src("rewards_assistant/api.py")


# ---------------------------------------------------------------- 5. 时间戳 zip

def test_zip_names_carry_datetime_suffix():
    now = datetime(2026, 11, 24, 15, 30, 12)
    assert mw.timestamped_zip_name("run-log", now) == "run-log_20261124_153012.zip"
    assert mw.timestamped_zip_name("rewards-pages", now) == \
        "rewards-pages_20261124_153012.zip"
    p = Path("/tmp/exports/mypages.zip")
    assert mw.ensure_timestamped_zip(str(p), now).endswith(
        "mypages_20261124_153012.zip")
    already = Path("/tmp/exports/mypages_20261124_153012.zip")
    assert mw.ensure_timestamped_zip(str(already), now) == str(already)
    no_ext = Path("/tmp/exports/logs")
    assert mw.ensure_timestamped_zip(str(no_ext), now) == \
        str(no_ext.with_name("logs_20261124_153012.zip"))


def test_export_dialogs_use_timestamped_defaults_and_debug_log():
    text = src("rewards_assistant/ui/modern/main_window.py")
    assert 'timestamped_zip_name("run-log")' in text
    assert 'timestamped_zip_name("rewards-pages")' in text
    assert text.count("ensure_timestamped_zip(path)") == 2
    assert 'z.write(dbg, arcname="debug.log")' in text
    assert '"run-session.log"' in text
    assert "导出时间" in text


def test_capture_zip_readme_has_export_time(monkeypatch, tmp_path):
    from rewards_assistant.session import PageResult

    class Fetcher:
        def fetch_many(self, paths):
            return {p: PageResult(path=p, ok=True, status=200,
                                  html="<html>ok</html>", kind="ok",
                                  signin_page=False) for p in paths}

    monkeypatch.setattr(mw, "default_fetcher", lambda: Fetcher())
    target = tmp_path / "rewards-pages_20261124_153012.zip"
    runner = mw.CaptureRunner(target)
    done, failed = [], []
    runner.done.connect(done.append)
    runner.fail.connect(failed.append)
    runner.run()
    assert not failed and done == [str(target)]
    with zipfile.ZipFile(target) as z:
        readme = z.read("README.txt").decode("utf-8")
        assert "导出时间：" in readme
        assert any(name.endswith(".html") for name in z.namelist())

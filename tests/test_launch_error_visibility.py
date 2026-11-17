# -*- coding: utf-8 -*-
"""回归：自动运行启动失败时，日志必须给出「完整异常原因」而不是被截断的栈帧。

用户日志曾经是：

    session_start sid=...
    geo ...
    session_end done                      <- 失败却写 done
    error Traceback (most recent call last):
      File "rewards_assistant/ui/legacy.py", line 67, in run
      File "rewards_assistant/api.py", line 270, in run_human_search   <- 到此为止

根因有两层：``runner.log`` 把 detail 截到 200 字符，而 ``format_exc`` 的
异常原因在最后一行；``ActionLogger.close()`` 又无条件写 ``session_end done``。
结果是既看不到原因，还误以为正常收尾，只能靠猜。
"""
from __future__ import annotations

from pathlib import Path

from rewards_assistant import api, browser
from rewards_assistant.credentials import CookieStore

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------- ActionLogger 收尾语义

def test_close_records_error_reason_instead_of_done(tmp_path):
    recs = []
    log = api.ActionLogger(tmp_path / "a.jsonl", "sid1", verbose=False,
                           callback=recs.append)
    log("geo", "ok")
    log.close("error", "RuntimeError: Edge 启动失败")
    actions = [(r["action"], r["detail"]) for r in recs]
    assert ("session_start", "sid=sid1") in actions
    end = [d for a, d in actions if a == "session_end"]
    assert end == ["error: RuntimeError: Edge 启动失败"]
    assert "done" not in end[0]
    assert (tmp_path / "a.jsonl").is_file()


def test_close_defaults_to_ok(tmp_path):
    recs = []
    log = api.ActionLogger(None, "sid2", verbose=False, callback=recs.append)
    log.close()
    assert recs[-1]["action"] == "session_end" and recs[-1]["detail"] == "ok"


# ------------------------------------------------------- run_human_search 失败

def test_launch_failure_logs_full_reason_and_error_session_end(tmp_path, monkeypatch):
    """模拟 launch_automation_edge 抛异常，验证日志顺序与内容。"""
    recs = []

    class PW:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    pw = PW()

    def fake_start():
        class Starter:
            def start(self):
                return pw
        return Starter()

    import playwright.sync_api as pw_api
    monkeypatch.setattr(pw_api, "sync_playwright", fake_start)
    monkeypatch.setattr(browser, "launch_automation_edge",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("Edge 可执行文件被占用，无法启动")))
    monkeypatch.setattr(browser, "find_edge_binary", lambda: None)

    try:
        api.run_human_search(
            repeat=1, geo=None, detect_geo=False, verbose=False,
            log_file=tmp_path / "run.jsonl", log_callback=recs.append)
    except RuntimeError:
        pass
    else:
        raise AssertionError("异常应向外传播给 SearchWorker")

    actions = [(r["action"], r["detail"]) for r in recs]
    names = [a for a, _ in actions]
    assert "session_start" in names and "browser_error" in names
    assert names[-1] == "session_end"
    reason = dict(actions)["browser_error"]
    assert "RuntimeError" in reason and "Edge 可执行文件被占用" in reason
    end = actions[-1][1]
    assert end.startswith("error:") and "done" not in end
    assert "Edge 可执行文件被占用" in end
    assert pw.stopped, "失败时必须停止 Playwright 驱动"
    # 原始异常没有被 pw.stop() 覆盖
    assert "Traceback" not in " ".join(d for _, d in actions)


# ------------------------------------------------------- 阶段日志

def test_launch_stage_logs_credentials_profile_edge_cookies(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    CookieStore().save([{"name": "MSPAuth", "value": "v", "domain": ".live.com",
                         "path": "/", "secure": True, "expires": -1}])
    stages = []

    class Ctx:
        def add_cookies(self, cookies):
            pass

        def close(self):
            pass

    monkeypatch.setattr(browser, "launch_edge",
                        lambda pw, *, user_data_dir, **kw: Ctx())
    ctx, snap = browser.launch_automation_edge(
        object(), log=lambda a, d: stages.append((a, d)))
    assert ctx is not None
    assert [a for a, _ in stages] == ["credentials", "profile", "edge", "cookies"]
    assert "1 条" in stages[0][1]
    assert "全新空目录" in stages[1][1]
    browser.cleanup_automation_profile(snap)


def test_no_credentials_logs_skip_and_never_launches(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    stages = []
    called = []

    def forbidden(*a, **k):
        called.append(True)
        raise AssertionError("凭证为空时不应启动浏览器")

    monkeypatch.setattr(browser, "launch_edge", forbidden)
    try:
        browser.launch_automation_edge(object(),
                                       log=lambda a, d: stages.append((a, d)))
    except RuntimeError as exc:
        assert "尚未保存登录会话" in str(exc)
    else:
        raise AssertionError("应当提示重新登录")
    assert not called
    assert any("凭证为空" in d for _, d in stages)
    assert not list(tmp_path.glob("automation_edge_profile_*"))


def test_launch_failure_stage_is_logged_before_raise(tmp_path, monkeypatch):
    from playwright.sync_api import Error as PWError

    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    CookieStore().save([{"name": "A", "value": "1", "domain": ".bing.com",
                         "path": "/", "secure": True, "expires": -1}])
    stages = []

    def boom(pw, *, user_data_dir, **kw):
        raise PWError("BrowserType.launch_persistent_context: "
                      "Executable doesn't exist at msedge.exe")

    monkeypatch.setattr(browser, "launch_edge", boom)
    try:
        browser.launch_automation_edge(object(),
                                       log=lambda a, d: stages.append((a, d)))
    except PWError:
        pass
    else:
        raise AssertionError("非冲突错误应直接抛出")
    errors = [d for a, d in stages if a == "edge_error"]
    assert len(errors) == 1
    assert "Executable doesn't exist" in errors[0]


# ------------------------------------------------------- 界面不再截断原因

def test_worker_error_log_is_single_line_with_reason(tmp_path, monkeypatch):
    """SearchWorker 的 error 必须是单行完整原因，界面 200/500 截断都留得下。"""
    from rewards_assistant.ui import legacy

    seen = []

    def fake_run(*a, **k):
        raise RuntimeError("尚未保存登录会话：请在登录里重新登录一次")

    monkeypatch.setattr(legacy, "run_human_search", fake_run)
    worker = legacy.SearchWorker({"repeat": 1}, None)
    worker.sig.log.connect(lambda a, d: seen.append((a, d)))
    failures = []
    worker.sig.failed.connect(failures.append)
    worker.run()

    err = [d for a, d in seen if a == "error"]
    assert len(err) == 1
    assert "\n" not in err[0]
    assert "RuntimeError" in err[0] and "尚未保存登录会话" in err[0]
    assert len(err[0]) <= 300
    traces = [d for a, d in seen if a == "trace"]
    assert len(traces) == 1 and "File " in traces[0] and "\n" not in traces[0]
    assert failures and "尚未保存登录会话" in failures[0]
    assert "Microsoft Rewards 登录" in failures[0]


def test_runner_log_limit_keeps_full_reason():
    mw = (ROOT / "rewards_assistant" / "ui" / "modern" / "main_window.py").read_text("utf-8")
    assert "self.runner.log.connect(lambda a, d: self.log(f\"{a} {d}\".strip()[:500]))" in mw
    assert "[:200]" not in mw
    lg = (ROOT / "rewards_assistant" / "ui" / "legacy.py").read_text("utf-8")
    assert 'self.sig.log.emit("error", reason)' in lg
    assert 'format_exc(limit=4)' not in lg


def test_api_success_path_closes_with_ok():
    src = (ROOT / "rewards_assistant" / "api.py").read_text("utf-8")
    assert 'logger.close("ok")' in src
    assert 'logger("browser_error", reason[:400])' in src
    assert 'logger.close("error", reason[:200])' in src
    assert 'log=lambda a, d: logger("browser", f"{a} {d}")' in src


def test_mid_run_finally_distinguishes_error_from_ok():
    """主循环中途抛异常时，finally 不能再无条件写 session_end ok。"""
    src = (ROOT / "rewards_assistant" / "api.py").read_text("utf-8")
    assert "pending_exc = sys.exc_info()[1]" in src
    assert 'logger("run_error", why)' in src
    assert 'logger.close("error", why)' in src
    assert 'logger.close("ok")' in src
    assert 'if pending_exc is not None:' in src


def test_injection_failure_has_actionable_hint():
    hint = browser.browser_error_hint(
        "RuntimeError: 本机会话 Cookie 无法注入浏览器，请在「登录」里重新登录一次")
    assert "重新登录" in hint
    assert "无法注入浏览器" in hint

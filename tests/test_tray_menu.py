"""托盘动作与主界面入口一一对应。无 Qt 时做 AST 静态检查。"""
from __future__ import annotations

import ast
import os
from pathlib import Path


def test_tray_menu_has_no_private_business_actions():
    source = (Path(__file__).resolve().parents[1] / "rewards_assistant" / "app.py").read_text("utf-8")
    root = ast.parse(source)
    cls = next(node for node in root.body if isinstance(node, ast.ClassDef) and node.name == "Application")
    func = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_build_tray")
    labels = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "addAction":
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                labels.append(node.args[0].value)
    assert set(labels) == {
        "显示主窗口", "立即同步", "刷新界面",
        "自动完成未完成任务", "仅打开未完成任务页（手动完成）",
        "停止", "今日完成后休眠至明天", "真正退出程序",
    }
    assert "复制一个搜索词" not in source
    assert "停止继续打开" not in source
    assert "act_stop_open" not in source


def test_tray_bindings_point_to_main_window_ui():
    source = (Path(__file__).resolve().parents[1] / "rewards_assistant" / "app.py").read_text("utf-8")
    for binding in (
        "self.window.act_sync.trigger",
        "self.window.act_refresh.trigger", "self.window.quick.tasks_btn.click",
        "self.window.quick.stop_btn.click",
    ):
        assert binding in source


def test_tray_menu_interactions_with_qt(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import pytest
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QApplication, QPushButton, QWidget
    from rewards_assistant.app import Application
    from rewards_assistant import app as app_module
    from PySide6.QtWidgets import QSystemTrayIcon

    if not QSystemTrayIcon.isSystemTrayAvailable():
        # 无系统托盘的 Ubuntu/CI 会自动回退为可见主窗口，不能测试真实菜单交互。
        import pytest
        pytest.skip("当前桌面没有系统托盘")

    qapp = QApplication.instance() or QApplication([])
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    app = Application([])
    calls = []

    class StubWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.act_sync = QAction("立即同步", self)
            self.act_login = QAction("登录微软账号", self)
            self.act_refresh = QAction("刷新界面", self)
            self.quick = QWidget(self)
            self.quick.tasks_btn = QPushButton("自动完成未完成任务", self.quick)
            self.quick.stop_btn = QPushButton("停止", self.quick)
            self.quick.stop_btn.setEnabled(False)
            for action, label in ((self.act_sync, "sync"), (self.act_login, "login"),
                                  (self.act_refresh, "refresh")):
                action.triggered.connect(lambda _checked=False, label=label: calls.append(label))
            self.quick.tasks_btn.clicked.connect(lambda: calls.append("run"))
            self.quick.stop_btn.clicked.connect(lambda: calls.append("stop"))

    class StubNotifier:
        def attach(self, _tray):
            pass

    app.window = StubWindow()
    app.notifier = StubNotifier()
    app._build_tray()
    actions = {a.text(): a for a in app._tray_menu.actions() if not a.isSeparator()}
    for label, result in (("立即同步", "sync"),
                          ("刷新界面", "refresh"), ("自动完成未完成任务", "run")):
        actions[label].trigger()
        assert calls.pop() == result
    assert not actions["停止"].isEnabled()
    app.window.quick.tasks_btn.setEnabled(False)
    app.window.quick.stop_btn.setEnabled(True)
    app.window.act_sync.setEnabled(False)
    app.update_tray_actions()
    assert not actions["自动完成未完成任务"].isEnabled()
    assert not actions["立即同步"].isEnabled()
    assert actions["停止"].isEnabled()
    actions["停止"].trigger()
    assert calls == ["stop"]
    app.tray.hide()
    app.window.close()

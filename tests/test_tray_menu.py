"""托盘动作与主界面入口一一对应。无 Qt 时做 AST 静态检查。

按需求托盘只保留 4 项：显示主页面 / 立即同步 / 自动完成任务 / 退出。
"""
from __future__ import annotations

import ast
import os
from pathlib import Path


def test_tray_menu_has_exactly_four_items():
    source = (Path(__file__).resolve().parents[1] / "rewards_assistant" / "app.py").read_text("utf-8")
    root = ast.parse(source)
    cls = next(node for node in root.body if isinstance(node, ast.ClassDef) and node.name == "Application")
    func = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_build_tray")
    found = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "addAction":
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                found.append((node.lineno, node.args[0].value))
    # ast.walk 是 BFS，不保源码顺序；按行号还原菜单真实顺序
    labels = [text for _line, text in sorted(found)]
    assert labels == ["显示主页面", "立即同步", "自动完成任务", "退出"]
    assert "复制一个搜索词" not in source
    assert "停止继续打开" not in source
    assert "act_stop_open" not in source
    # 托盘图标不再显示鼠标悬浮提示
    assert "self.tray.setToolTip(" not in source
    assert "def update_tray_tooltip" not in source


def test_tray_bindings_point_to_main_window_ui():
    source = (Path(__file__).resolve().parents[1] / "rewards_assistant" / "app.py").read_text("utf-8")
    for binding in (
        "self.window.act_sync.trigger",
        "self.window.quick.tasks_btn.click()",
        "self._on_tray_run",
        "self.show_window",
        "self.quit",
    ):
        assert binding in source
    assert "self.window.act_refresh.trigger" not in source
    assert "self.window.quick.stop_btn.click" not in source


def test_tray_menu_interactions_with_qt(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import pytest
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QApplication, QPushButton, QWidget
    from rewards_assistant.app import Application
    from PySide6.QtWidgets import QSystemTrayIcon

    if not QSystemTrayIcon.isSystemTrayAvailable():
        import pytest
        pytest.skip("当前桌面没有系统托盘")

    QApplication.instance() or QApplication([])   # 持有 QApplication 直到用例结束
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    app = Application([])
    calls = []

    class StubWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.act_sync = QAction("立即同步", self)
            self.act_refresh = QAction("刷新界面", self)
            self.quick = QWidget(self)
            self.quick.tasks_btn = QPushButton("自动完成任务", self.quick)
            self.quick.stop_btn = QPushButton("停止", self.quick)
            self.quick.stop_btn.setEnabled(False)
            self.act_sync.triggered.connect(lambda _checked=False: calls.append("sync"))
            self.quick.tasks_btn.clicked.connect(lambda: calls.append("run"))
        def stop_running(self):
            calls.append("stop")

    class StubNotifier:
        def attach(self, _tray):
            pass

    app.window = StubWindow()
    app.notifier = StubNotifier()
    app._build_tray()
    actions = [a for a in app._tray_menu.actions() if not a.isSeparator()]
    assert [a.text() for a in actions] == ["显示主页面", "立即同步", "自动完成任务", "退出"]
    by_text = {a.text(): a for a in actions}
    by_text["立即同步"].trigger()
    assert calls.pop() == "sync"
    by_text["自动完成任务"].trigger()
    assert calls.pop() == "run"

    # 同步进行中：立即同步禁用；自动完成任务保持可点（同步期点它不会误启动）
    app.window.act_sync.setEnabled(False)
    app.update_tray_actions()
    assert not by_text["立即同步"].isEnabled()

    # 自动运行期间：菜单项变「停止自动运行」，点击只停止、绝不再次启动
    monkeypatch.setattr(app, "auto_running", lambda: True)
    app.update_tray_actions()
    stopped = app._tray_menu.actions()[2]
    assert stopped.text() == "停止自动运行"
    stopped.trigger()
    assert calls.pop() == "stop"
    assert not by_text["立即同步"].isEnabled()
    app.window.act_sync.setEnabled(True)
    monkeypatch.setattr(app, "auto_running", lambda: False)
    app.update_tray_actions()
    assert app._tray_menu.actions()[2].text() == "自动完成任务"
    app.tray.hide()
    app.window.close()

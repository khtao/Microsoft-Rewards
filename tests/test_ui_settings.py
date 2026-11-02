"""需要 PySide6；在桌面电脑可用 QT_QPA_PLATFORM=offscreen pytest -q 测试。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, QPointF, Qt, QCoreApplication
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from rewards_assistant.config import Settings
from rewards_assistant.ui.controls import (
    NoWheelComboBox, NoWheelDoubleSpinBox, NoWheelSpinBox, NoWheelTimeEdit,
)
from rewards_assistant.ui.main_window import SearchPanel
from rewards_assistant.ui.panels import AccountPanel
from rewards_assistant.ui.chart_view import ChartView


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    return Settings()


def test_wheel_does_not_change_setting(qapp):
    controls = [NoWheelSpinBox(), NoWheelDoubleSpinBox(), NoWheelTimeEdit(), NoWheelComboBox()]
    controls[-1].addItems(["A", "B", "C"])
    for ctrl in controls:
        if isinstance(ctrl, (NoWheelSpinBox, NoWheelDoubleSpinBox)):
            ctrl.setRange(0, 100)
            ctrl.setValue(25)
        before = ctrl.currentIndex() if isinstance(ctrl, NoWheelComboBox) else (
            ctrl.time() if isinstance(ctrl, NoWheelTimeEdit) else ctrl.value())
        evt = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(), QPoint(0, 120),
                          Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                          Qt.ScrollPhase.ScrollUpdate, False)
        QCoreApplication.sendEvent(ctrl, evt)
        after = ctrl.currentIndex() if isinstance(ctrl, NoWheelComboBox) else (
            ctrl.time() if isinstance(ctrl, NoWheelTimeEdit) else ctrl.value())
        assert before == after


def test_account_autosaves_and_restores_values(qapp, settings, monkeypatch):
    from rewards_assistant.ui import panels
    monkeypatch.setattr(panels.autostart, "is_enabled", lambda: False)
    class App:
        store = object()
        def __init__(self): self.settings = settings
        def login(self): pass
        def logout(self): pass
        def sync_now(self): pass
        def apply_settings(self): pass
    app = App()
    panel = AccountPanel(app)
    panel.settings_changed.connect(settings.update)
    panel.interval.setValue(37)
    panel.auto_sync.setChecked(False)
    panel.remind_time.setTime(panel._parse_time("13:47"))
    panel.remind.setChecked(False)
    assert Settings().get("sync_interval_value_minutes") == 37
    assert Settings().get("remind_time_value") == "13:47"
    restored = AccountPanel(app)
    assert restored.interval.value() == 37
    assert restored.remind_time.time().toString("HH:mm") == "13:47"


def test_search_panel_autosaves(qapp, settings, monkeypatch):
    from rewards_assistant.ui import main_window
    monkeypatch.setattr(main_window, "find_edge_binary", lambda: None)
    panel = SearchPanel(settings=settings, save_settings=settings.update)
    panel.spin_repeat.setValue(23)
    panel.edt_keywords.setText("旅行, 美食")
    data = Settings().get("search_panel")
    assert data["repeat"] == 23
    assert "copy_profile" not in data
    assert data["keywords"] == "旅行, 美食"
    other = SearchPanel(settings=settings, save_settings=settings.update)
    assert other.spin_repeat.value() == 23
    assert other.edt_keywords.text() == "旅行, 美食"


def test_chart_restores_view(qapp, settings):
    class EmptyStore:
        def all_days(self): return []
    view = ChartView(EmptyStore(), settings=settings, save_settings=settings.update)
    view.range_box.setCurrentIndex(view.range_box.findData(90))
    view.metric.setCurrentIndex(view.metric.findData("streak"))
    other = ChartView(EmptyStore(), settings=Settings(), save_settings=settings.update)
    assert other.range_box.currentData() == 90
    assert other.metric.currentData() == "streak"


def test_quick_panel_only_uses_default_browser(qapp):
    from rewards_assistant.ui.panels import QuickPanel
    from PySide6.QtWidgets import QComboBox, QPushButton
    panel = QuickPanel()
    assert not panel.findChildren(QComboBox)
    assert all("打开 Bing 搜索页" not in b.text()
               for b in panel.findChildren(QPushButton))

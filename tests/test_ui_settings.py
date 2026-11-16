"""需要 PySide6；在桌面电脑可用 QT_QPA_PLATFORM=offscreen pytest -q 测试。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, QPointF, Qt, QCoreApplication
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox, QPushButton

from rewards_assistant.config import Settings
from rewards_assistant.ui.controls import (
    NoWheelComboBox, NoWheelDoubleSpinBox, NoWheelSpinBox, NoWheelTimeEdit,
)
from rewards_assistant.ui import legacy


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


def test_search_panel_autosaves_real_settings(qapp, settings, monkeypatch):
    """经典 SearchPanel 写 search_panel，控制器 _collect() 直接读它。"""
    monkeypatch.setattr(legacy, "find_edge_binary", lambda: None)
    panel = legacy.SearchPanel(settings=settings, save_settings=settings.update)
    panel.spin_repeat.setValue(23)
    panel.edt_keywords.setText("旅行, 美食")
    data = Settings().get("search_panel")
    assert data["repeat"] == 23
    assert "copy_profile" not in data
    assert data["keywords"] == "旅行, 美食"
    other = legacy.SearchPanel(settings=settings, save_settings=settings.update)
    assert other.spin_repeat.value() == 23
    assert other.edt_keywords.text() == "旅行, 美食"
    params = other._collect()
    assert params["repeat"] == 23
    assert params["keywords"] == ["旅行", "美食"]


def test_search_runner_for_pending_uses_classic_logic(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    from datetime import date
    from rewards_assistant.storage import Store

    st = Store()
    day = date.today().isoformat()
    st.upsert_task(day, "pc_search", "今日 Bing 搜索", "搜索", 30, 12, False,
                   False, "", "official", "", "")
    # 经典 for_pending() 要求 daily 行存在且当日未完成；真实同步链路也是
    # 「写任务 → refresh_day() 落汇总」这个顺序，测试构造必须一致。
    st.refresh_day(day)
    p = legacy.SearchRunner.for_pending(st, legacy.SearchRunner.defaults())
    assert p is not None and p["repeat"] > 0
    assert p["headless"] is False and p["keyword_sources"]


def test_quick_panel_is_the_classic_action_proxy(qapp):
    panel = legacy.QuickPanel()
    assert isinstance(panel.tasks_btn, QPushButton)
    assert panel.tasks_btn.text() == "自动完成未完成任务"
    assert not panel.findChildren(QComboBox)
    assert panel.stop_btn.isEnabled() is False
    panel.set_busy(True)
    assert panel.tasks_btn.isEnabled() is False and panel.stop_btn.isEnabled() is True

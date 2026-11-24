# -*- coding: utf-8 -*-
"""设置页六个统一 Card、字号与按钮字号一致的契约。

「本机账号」不单独成卡，删除账号与删除环境两个入口都放在当前环境那一行；
正文、分组标题与所有按键统一 13px，不再叠长说明小字。
"""
from __future__ import annotations

from pathlib import Path

from rewards_assistant.config import Settings
from rewards_assistant.ui.modern import theme, viewmodel as vm
from rewards_assistant.ui.modern.main_window import ModernMainWindow

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- 设置页样式

def test_settings_page_has_six_uniform_cards_and_no_account_card():
    src = (ROOT / "rewards_assistant" / "ui" / "modern" /
           "main_window.py").read_text("utf-8")
    seg = src[src.index("class SettingsPage(QWidget):"):
              src.index("class _AccountFacade:")]
    assert seg.count('Card(number="') == 6
    assert 'Card(number="05"' in seg and 'Card(number="06"' in seg
    assert 'QFrame()' not in seg, "所有设置模块都必须是 Card"
    assert 'setObjectName("flat")' not in seg
    assert 'acct = QFrame()' not in seg and 'account_state' not in seg
    assert 'acct_title' not in seg and 'acct_hint' not in seg


def test_hint_small_text_removed_and_buttons_unified():
    src = (ROOT / "rewards_assistant" / "ui" / "modern" /
           "main_window.py").read_text("utf-8")
    seg = src[src.index("class SettingsPage(QWidget):"):
              src.index("class _AccountFacade:")]
    for gone in ("ds_hint", "test_hint", "env_hint", "acct_hint",
                 "instance_hint", "font-size:11px", "font-size:12px"):
        assert gone not in seg, gone
    css = theme.stylesheet()
    assert "padding: 7px 12px; font-size: 13px;" in css
    assert "border-radius: 8px; padding: 6px 10px; font-size: 13px;" in css


def test_delete_account_button_only_on_current_environment_row(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication, QPushButton, QLabel

    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("MS_REWARDS_INSTANCE", "default")
    from rewards_assistant import instances
    from rewards_assistant.config import _preferred_default_for
    _preferred_default_for.cache_clear()
    instances.create_instance("khtao", "zh")

    app = QApplication.instance() or QApplication([])
    w = ModernMainWindow(settings=Settings())
    p = w.settings_page
    layouts = [p.env_rows.itemAt(i) for i in range(p.env_rows.count())]
    assert len(layouts) == 2
    for lay in layouts:
        items = [lay.itemAt(j).widget() for j in range(lay.count())]
        texts = [x.text() for x in items if isinstance(x, QPushButton)]
        labels = [x.text() for x in items if isinstance(x, QLabel)]
        if any("当前" in x for x in labels):
            assert "删除账号" in texts, texts
            assert "删除环境" not in texts, "内置默认环境不可删"
        else:
            assert "删除账号" not in texts and "删除环境" not in texts, texts
        # 名称只显示可读名称，不带括号里的实例 id。
        for lab in labels:
            assert "(default)" not in lab and "  (" not in lab, labels
        if any("默认环境" in x for x in labels):
            assert any(x == "默认环境" for x in labels), labels
    _preferred_default_for.cache_clear()


def test_settings_input_controls_share_the_body_font_size(monkeypatch, tmp_path):
    """时间框与环境名称都是 13px QLineEdit，时间框空态显示 --:--。"""
    from PySide6.QtWidgets import QApplication, QLineEdit
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path / "data"))
    app = QApplication.instance() or QApplication([])
    w = ModernMainWindow(settings=Settings())
    w.show()
    app.processEvents()
    page = w.settings_page
    edits = page.findChildren(QLineEdit)
    assert len(edits) == 2
    assert all(e.objectName() == "body" and e.font().pixelSize() == 13
               for e in edits), [(e.objectName(), e.font().pixelSize()) for e in edits]
    box = page.time_input
    assert box.placeholderText() == "--:--" and box.maxLength() == 5
    assert box.text() == ""
    assert "QLineEdit#body" in theme.stylesheet()
    assert "QTimeEdit" not in theme.stylesheet()
    app.processEvents()


def test_time_input_theme_adapts_light_and_dark(monkeypatch, tmp_path):
    """两套 QSS 都给时间框配色；占位色由 palette 随主题设置。"""
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path / "data"))
    app = QApplication.instance() or QApplication([])
    original = theme.scheme()
    try:
        light = theme.set_scheme("light")
        css_light = theme.stylesheet()
        dark = theme.set_scheme("dark")
        css_dark = theme.stylesheet()
        assert light == "light" and dark == "dark"
        for css in (css_light, css_dark):
            assert "QLineEdit#body" in css
            assert "QLineEdit#body:focus" in css
            assert "QLineEdit#body:disabled" in css
            assert "font-size: 13px" in css
        assert css_light != css_dark
        assert theme.DARK["text_sub"] in css_dark
        assert theme.LIGHT["text_sub"] in css_light
        src = (ROOT / "rewards_assistant" / "ui" / "modern" /
               "main_window.py").read_text("utf-8")
        assert "QPalette.ColorRole.PlaceholderText" in src
        assert 'theme.current()["text_muted"]' in src
        assert "sync_placeholder_color()" in src, \
            "套用 QSS 后必须重设占位色，否则会被样式表覆盖"
        # 切主题后重建 UI，占位色确实取自新主题。
        theme.set_scheme("light")
        w = ModernMainWindow(settings=Settings())
        assert (w.settings_page.time_input.palette().color(
            QPalette.ColorRole.PlaceholderText).name().lower()
            == theme.LIGHT["text_muted"].lower())
        app.processEvents()
    finally:
        theme.set_scheme(original)


def test_all_settings_buttons_share_one_font_size(monkeypatch, tmp_path):
    """除符号型的返回键，设置页所有按钮（含分段器）都是 13px。"""
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path / "data"))
    app = QApplication.instance() or QApplication([])
    w = ModernMainWindow(settings=Settings())
    w.show()
    app.processEvents()
    p = w.settings_page
    buttons = [b for b in p.findChildren(QPushButton)
               if b.objectName() != "icon"]
    sizes = {(b.objectName() or "-", b.font().pixelSize()) for b in buttons}
    assert buttons
    assert all(size == 13 for _, size in sizes), sizes
    body_sizes = {x.font().pixelSize() for x in p.findChildren(QLabel)
                  if x.objectName() == "body"}
    assert body_sizes == {13}, body_sizes
    app.processEvents()


def test_preview_script_asserts_button_font_size():
    src = (ROOT / "tools" / "render_previews.py").read_text("utf-8")
    assert 'b.font().pixelSize() == 13' in src
    assert 'x.font().pixelSize() == 13' in src


def test_i18n_full_button_labels():
    assert vm.tr("zh", "account_delete_short") == "删除账号"
    assert vm.tr("en", "account_delete_short") == "Delete account"
    assert vm.tr("zh", "instance_delete") == "删除环境"
    assert vm.tr("zh", "instance_open") == "打开"
    assert vm.tr("zh", "instance_set_default") == "设为默认"
    assert vm.tr("zh", "instance_current") == "当前"
    assert vm.tr("en", "instance_set_default") == "Set default"


def test_settings_body_font_is_the_appearance_card_size():
    """「外观」卡 Row 标签是 13px，设置页正文 QLabel#body 也是 13px。"""
    widgets = (ROOT / "rewards_assistant" / "ui" / "modern" /
               "widgets.py").read_text("utf-8")
    assert 'lab.setStyleSheet("font-weight:600;font-size:13px;")' in widgets
    css = theme.stylesheet()
    assert "QLabel#body" in css and "font-size: 13px" in css
    assert "QLabel#tiny" in css and "font-size: 11px" in css, \
        "主页的 tiny 徽章保持原样，只统一设置页正文"
    win = (ROOT / "rewards_assistant" / "ui" / "modern" /
           "main_window.py").read_text("utf-8")
    seg = win[win.index("class SettingsPage(QWidget):"):
              win.index("class _AccountFacade:")]
    assert 'setObjectName("tiny")' not in seg
    assert "font-size:11px" not in seg and "font-size:12px" not in seg
    assert seg.count('setObjectName("body")') >= 5


def test_theme_body_rule_exists_in_both_schemes():
    original = theme.scheme()
    try:
        for scheme in ("light", "dark"):
            theme.set_scheme(scheme)
            css = theme.stylesheet()
            assert "QLabel#body" in css and "font-size: 13px" in css
    finally:
        theme.set_scheme(original)

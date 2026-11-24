# -*- coding: utf-8 -*-
"""登录对话框改版：用图片 + 循环动图演示流程，文字大幅压缩。

离线验证：不启动浏览器、不访问网络；Qt 用 offscreen 平台。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rewards_assistant.ui import login_guide as lg  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def src(rel: str) -> str:
    return (ROOT / rel).read_text("utf-8")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- 文字压缩

def test_dialog_no_long_html_instruction_block():
    text = src("rewards_assistant/ui/login_dialog.py")
    for gone in ("<h3>", "<li>", "</ol>", "<p style=",
                 "同步只读取 /dashboard", "会话 Cookie 只",
                 "浏览器会话登录</h3>"):
        assert gone not in text, f"旧的大段说明仍残留：{gone}"
    assert "LoginGuideWidget()" in text
    assert "self.guide" in text
    assert 'self.login_btn.setObjectName("loginPrimary")' in text
    # 独立弹窗自己按主题配色，不再写死灰字
    assert "_dialog_qss" in text
    assert "#2f6fed" not in text and "#495057" not in text
    assert "_set_status" in text


def test_guide_labels_are_one_short_line_each():
    assert lg.FRAME_COUNT == 5
    assert len(lg.FRAME_LABELS) == lg.FRAME_COUNT
    assert all(4 <= len(s) <= 22 for s in lg.FRAME_LABELS)
    assert all(s[0] in "①②③④⑤" for s in lg.FRAME_LABELS)


# ---------------------------------------------------------------- 绘制与动图

def test_no_gif_decoder_dependency():
    """界面内是 Qt 实时绘制的动画，不依赖打包环境未必有的 qgif 插件。"""
    text = src("rewards_assistant/ui/login_guide.py")
    for gone in ("QMovie", "QGraphicsOpacityEffect", "setMovie("):
        assert gone not in text, gone
    # 只校验真实调用，不误伤文档里解释设计取舍的词
    assert "QTimer" in text and "QPainter" in text
    assert "def paint_guide(" in text
    assert "def render_frame_image(" in text


def test_render_frame_is_nonblank(qapp):
    from rewards_assistant.ui.modern import theme

    for scheme in ("light", "dark"):
        theme.set_scheme(scheme)
        img = lg.render_frame_image(2, 0.4, theme.current())
        assert isinstance(img, QImage)
        assert img.width() == 560 and img.height() == 232
        assert not img.isNull()
        # 不是整张纯色：抽样若干像素，颜色种类 > 1
        colors = {img.pixelColor(x, y).rgb() for x in (20, 150, 300, 420, 540)
                  for y in (20, 100, 180, 210)}
        assert len(colors) > 3, scheme
    theme.set_scheme("light")


def test_paint_guide_accepts_crossfade(qapp):
    """帧切换时上一帧按比例叠在下面，不抛异常。"""
    from PySide6.QtCore import QRectF

    pal = lg.guide_palette()
    img = QImage(560, 232, QImage.Format.Format_ARGB32)
    img.fill(pal["window"])
    p = QPainter(img)
    lg.paint_guide(p, QRectF(0, 0, 560, 232), pal,
                   3, 0.06, prev_frame=2, blend=0.4)
    p.end()
    assert not img.isNull()


def test_widget_animates_and_can_freeze(qapp):
    w = lg.LoginGuideWidget()
    assert w.minimumWidth() == 560 and w.height() == 232
    assert w._timer.isActive()
    assert w._timer.interval() == 40
    assert 0 <= w.current_frame() < lg.FRAME_COUNT
    w.freeze(4, 0.5)
    assert w.frame_state() == (4, 0.5)
    assert w.current_frame() == 4
    assert not w._timer.isActive()
    w.refresh_theme()
    assert w._pal
    w.resume()
    assert w._timer.isActive()
    assert w._frozen is None
    w.deleteLater()


def test_dialog_hosts_the_guide(qapp, tmp_path, monkeypatch):
    from rewards_assistant.ui.login_dialog import LoginDialog
    from rewards_assistant.ui.modern import theme

    # 隔离实例目录：确保「从未同步过」这条回落真的被走到，不被本机记录影响
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    theme.set_scheme("light")
    dlg = LoginDialog()
    assert isinstance(dlg.guide, lg.LoginGuideWidget)
    assert dlg.minimumWidth() >= 600
    assert dlg.login_btn.objectName() == "loginPrimary"
    assert dlg.status.objectName() == "loginStatus"
    assert "尚未检测会话状态" in dlg.status.text()
    # 定帧后不因窗口显示而自动续播
    dlg.guide.freeze(1, 0.4)
    dlg.show()
    qapp.processEvents()
    assert dlg.guide.current_frame() == 1
    dlg.close()
    qapp.processEvents()
    dlg.deleteLater()


def test_status_uses_theme_colors(qapp, tmp_path, monkeypatch):
    from rewards_assistant.ui.login_dialog import LoginDialog
    from rewards_assistant.ui.modern import theme

    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    theme.set_scheme("dark")
    dlg = LoginDialog()
    dlg._set_status("测试", "ok")
    assert dlg.status.text() == "测试"
    assert theme.current()["green"] in dlg.status.styleSheet()
    dlg._set_status("测试", "busy")
    assert theme.current()["primary"] in dlg.status.styleSheet()
    dlg.deleteLater()
    theme.set_scheme("light")


# ---------------------------------------------------------------- 交付物

def test_export_script_exists_and_emits_gif():
    text = src("tools/render_login_guide.py")
    assert 'login_flow.gif' in text
    assert "save_all=True" in text
    assert "render_frame_image" in text
    assert "freeze(" in text
    assert "login_dialog_dark.png" in text


def test_exported_previews_are_present():
    prev = ROOT / "docs" / "previews"
    gif = prev / "login_flow.gif"
    assert gif.is_file() and gif.stat().st_size > 10_000
    assert gif.read_bytes()[:6] in (b"GIF87a", b"GIF89a")
    for name in ("login_flow.png", "login_dialog.png",
                 "login_dialog_dark.png"):
        f = prev / name
        assert f.is_file() and f.stat().st_size > 5_000, name
    try:
        from PIL import Image

        with Image.open(gif) as im:
            assert im.n_frames >= 25
            assert im.size == (560, 232)
    except ImportError:
        pass

# -*- coding: utf-8 -*-
"""主界面登录状态：文字与「积分管理助手」同字号同字重，只有文字变色。

- 已登录文字金色，图标为细橙环 + 斜纹绶带 + 橙金渐变圆章（高清参考图复刻）；
- 未登录文字红色，图标为描边环 + 白色同款奖章；浅色主题下圆底改灰色；
- 奖章与左侧品牌图标同为 32×32，是 QPainter 矢量图，不是 emoji 或截图素材。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QSize  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rewards_assistant.ui.icons import login_badge_pixmap  # noqa: E402
from rewards_assistant.ui.modern import main_window as mw  # noqa: E402
from rewards_assistant.ui.modern import theme  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def home(qapp):
    theme.set_scheme("light")
    app = SimpleNamespace(
        lang="zh", sync_now=lambda *a, **k: None,
        open_login=lambda: None, show_settings=lambda: None,
        primary_clicked=lambda: None, stop_running=lambda: None)
    page = mw.HomePage(app)
    yield page
    page.deleteLater()
    theme.set_scheme("light")


def _css(widget) -> str:
    return widget.styleSheet()


def _pix_hash(pm) -> str:
    img = pm.toImage()
    # PySide6 6.x 的 bits() 返回 memoryview，老版本才提供 asstring()
    return hashlib.md5(bytes(img.bits())).hexdigest()


# ---------------------------------------------------------------- 字体

def test_badge_font_matches_title(home):
    assert home.login_badge.font().family() == home.title.font().family()
    css = _css(home.login_badge)
    assert "font-size:16px" in css
    assert "font-weight:700" in css
    sheet = theme.stylesheet()
    assert "QLabel#topTitle" in sheet
    assert "font-size: 16px; font-weight: 700;" in sheet


def test_emoji_is_gone_and_no_screenshot_asset(home):
    src = (ROOT / "rewards_assistant/ui/modern/main_window.py").read_text("utf-8")
    icons = (ROOT / "rewards_assistant/ui/icons.py").read_text("utf-8")
    assert "🏅" not in src
    assert "login_badge_pixmap" in src
    assert "_draw_login_badge" in icons
    # 图标是现画的矢量造型，没有引用任何截图 / 位图素材
    for gone in ("image.png", "ref_badge", "screenshot"):
        assert gone not in icons, gone


# ---------------------------------------------------------------- 32px 与造型

def test_badge_is_32px_same_as_brand_icon(home):
    """奖章与「积分管理助手」左侧品牌图标同为 32×32。"""
    assert mw.TOPBAR_ROW_H == 32
    assert home.logo.width() == 32 and home.logo.height() == 32
    assert home.login_badge.iconSize() == QSize(32, 32)
    for ok in (True, False, None):
        home.set_login_badge(ok)
        pm = home.login_badge.icon().pixmap(32, 32)
        assert pm.width() == 32 and pm.height() == 32


def test_refined_shape_matches_hd_reference(home):
    """细等宽环 + 斜纹绶带 + 渐变圆章，不再是粗渐变环 + 向下飘带。"""
    icons = (ROOT / "rewards_assistant/ui/icons.py").read_text("utf-8")
    assert "def _ribbon(" in icons
    assert "setClipPath" in icons          # 绶带斜条纹靠裁切绘制
    assert "QLinearGradient" in icons      # 橙金渐变圆章
    assert "2.4 * s" in icons              # 细环线宽
    assert "QPen(QBrush(grad), 7.0 * s)" not in icons   # 旧的粗渐变环已移除
    assert "向下张开" not in icons


# ---------------------------------------------------------------- 文字配色

def test_signed_in_text_is_gold(home):
    home.set_login_badge(True)
    css = _css(home.login_badge)
    assert "已登录" in home.login_badge.text()
    assert theme.current()["gold"] in css
    assert theme.current()["warn"] not in css
    assert "font-weight:700" in css


def test_signed_out_text_is_red(home):
    home.set_login_badge(False)
    css = _css(home.login_badge)
    assert "未登录" in home.login_badge.text()
    assert theme.current()["warn"] in css
    assert theme.current()["gold"] not in css


def test_unknown_text_is_neutral(home):
    home.set_login_badge(None)
    css = _css(home.login_badge)
    assert "未检测" in home.login_badge.text()
    assert theme.current()["text_sub"] in css
    assert theme.current()["warn"] not in css
    assert theme.current()["gold"] not in css


def test_text_colors_follow_dark_theme(home):
    theme.set_scheme("dark")
    home.set_login_badge(True)
    assert theme.current()["gold"] in _css(home.login_badge)
    home.set_login_badge(False)
    assert theme.current()["warn"] in _css(home.login_badge)


# ---------------------------------------------------------------- 奖章图标

def test_badge_has_real_icon_in_every_state(home):
    for ok in (True, False, None):
        home.set_login_badge(ok)
        assert not home.login_badge.icon().isNull(), ok


def test_icon_differs_by_state_and_theme(qapp):
    signed_in = login_badge_pixmap(32, "signed_in", "light")
    signed_out = login_badge_pixmap(32, "signed_out", "light")
    unknown = login_badge_pixmap(32, "unknown", "light")
    dark_out = login_badge_pixmap(32, "signed_out", "dark")
    assert not signed_in.isNull() and not signed_out.isNull()
    hashes = {_pix_hash(pm) for pm in (signed_in, signed_out, unknown, dark_out)}
    assert len(hashes) == 4, "三态 + 深浅主题必须给出四张不同的图"


def test_signed_out_disk_is_gray_on_light_theme(qapp):
    """浅色背景下未登录圆底是灰色，不是第二张参考图那种深色块。"""
    def sample(scheme: str):
        img = login_badge_pixmap(64, "signed_out", scheme).toImage()
        # 圆半径 28、白环半径 21.5：取环外、圆内的顶部一点
        return img.pixelColor(32, 5)

    light = sample("light")
    dark = sample("dark")
    assert light.alpha() > 200 and dark.alpha() > 200
    # 灰：三通道接近；深棕：明显偏暗且红 > 蓝
    assert max(light.red(), light.green(), light.blue()) - min(
        light.red(), light.green(), light.blue()) < 35
    assert (light.red() + light.green() + light.blue()) / 3 > 100
    assert (dark.red() + dark.green() + dark.blue()) / 3 < 110

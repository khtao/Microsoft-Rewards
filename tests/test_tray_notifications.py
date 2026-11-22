# -*- coding: utf-8 -*-
"""「后台与通知」拆分为托盘运行 / 消息通知两个独立开关。

* 两者默认打开；旧配置（三键全关）平滑回退为两者都关。
* 托盘运行关闭后不创建托盘图标、不启动重试定时器，关闭窗口彻底退出。
* 消息通知关闭后普通提示只写日志，同步/登录失败等严重错误仍提醒。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace


from rewards_assistant import app as app_mod
from rewards_assistant.config import Settings
from rewards_assistant.ui.modern import main_window as win_mod
from rewards_assistant.ui.modern import viewmodel as vm

ROOT = Path(__file__).resolve().parents[1]
APP_SRC = (ROOT / "rewards_assistant" / "app.py").read_text("utf-8")
WIN_SRC = (ROOT / "rewards_assistant" / "ui" / "modern" /
           "main_window.py").read_text("utf-8")


class FakeSettings(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


# ---------------------------------------------------------------- 设置模型

def test_two_switches_are_separate_and_default_on():
    assert vm.UI_DEFAULTS["ui_tray_mode"] is True
    assert vm.UI_DEFAULTS["ui_messages_mode"] is True
    assert "ui_background_mode" not in vm.UI_DEFAULTS
    for key in ("ui_tray_mode", "ui_messages_mode"):
        assert key in vm.VISIBLE_SETTING_KEYS
        assert key in vm.CLASSIC_BINDING
    p = dict(vm.UI_DEFAULTS)
    tray = vm.pref_to_settings(p, "ui_tray_mode", False)
    msg = vm.pref_to_settings(p, "ui_messages_mode", False)
    assert tray == {"tray_enabled": False, "close_to_tray": False}
    assert msg == {"messages_enabled": False, "notify_after_sync": False}
    assert set(tray).isdisjoint(msg), "两个开关不得互相牵连"
    patch = vm.defaults_patch()
    for k in ("tray_enabled", "close_to_tray",
              "messages_enabled", "notify_after_sync"):
        assert patch[k] is True


def test_reset_patch_restores_both_switches_to_on():
    patch = vm.reset_patch()
    assert patch["tray_enabled"] is True and patch["close_to_tray"] is True
    assert patch["messages_enabled"] is True and patch["notify_after_sync"] is True


def test_legacy_background_group_off_stays_off_after_split(tmp_path, monkeypatch):
    """既有配置里关过「后台与通知」的用户，拆分后两个开关仍保持关闭。"""
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    # 直接写一份不含两个新键的既有配置：关组开关会写三键 False。
    (tmp_path / "settings.json").write_text(
        json.dumps({"notify_after_sync": False,
                    "silent_when_all_done": False,
                    "close_to_tray": False}), encoding="utf-8")
    v = vm.settings_values(Settings())
    assert v["ui_tray_mode"] is False and v["ui_messages_mode"] is False


def test_roundtrip_one_switch_does_not_touch_the_other(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    s = Settings()
    s.update(vm.pref_to_settings(vm.settings_values(s), "ui_tray_mode", False))
    v = vm.settings_values(Settings())
    assert v["ui_tray_mode"] is False
    assert v["ui_messages_mode"] is True
    assert Settings().get("messages_enabled") is True


# ---------------------------------------------------------------- 闸门

def test_tray_enabled_falls_back_to_close_to_tray():
    on = SimpleNamespace(settings=FakeSettings(tray_enabled=True))
    off = SimpleNamespace(settings=FakeSettings(tray_enabled=False))
    legacy = SimpleNamespace(settings=FakeSettings(close_to_tray=False))
    fresh = SimpleNamespace(settings=FakeSettings())
    assert app_mod.Application.tray_enabled(on) is True
    assert app_mod.Application.tray_enabled(off) is False
    assert app_mod.Application.tray_enabled(legacy) is False
    assert app_mod.Application.tray_enabled(fresh) is True


def test_messages_enabled_falls_back_to_notify_after_sync():
    off = SimpleNamespace(settings=FakeSettings(messages_enabled=False))
    legacy = SimpleNamespace(settings=FakeSettings(notify_after_sync=False))
    fresh = SimpleNamespace(settings=FakeSettings())
    assert app_mod.Application.messages_enabled(off) is False
    assert app_mod.Application.messages_enabled(legacy) is False
    assert app_mod.Application.messages_enabled(fresh) is True


class FakeTray:
    def __init__(self, *_a, **_kw):
        self.hidden = False
        self.menu = object()
        self.deleted = False

    def hide(self): self.hidden = True
    def setContextMenu(self, menu): self.menu = menu
    def deleteLater(self): self.deleted = True


class FakeRetry:
    def __init__(self): self.stopped = False
    def stop(self): self.stopped = True


class FakeNotifier:
    def __init__(self): self.attached = []
    def attach(self, tray): self.attached.append(tray)
    def notify(self, *a, **kw): self.attached.append(("notify", a, kw))


def _controller(tray_on=True, msg_on=True):
    ctl = SimpleNamespace(
        settings=FakeSettings(tray_enabled=tray_on, messages_enabled=msg_on),
        tray=None, _tray_menu=None, act_tray_sync=None, act_tray_run=None,
        _tray_retry=FakeRetry(), notifier=FakeNotifier(),
        _log=lambda msg: None,
    )
    ctl.tray_enabled = lambda: app_mod.Application.tray_enabled(ctl)
    ctl.messages_enabled = lambda: app_mod.Application.messages_enabled(ctl)
    ctl._destroy_tray = lambda: app_mod.Application._destroy_tray(ctl)
    ctl._build_tray = lambda: app_mod.Application._build_tray(ctl)
    ctl._apply_tray_mode = lambda: app_mod.Application._apply_tray_mode(ctl)
    return ctl


def test_build_tray_creates_nothing_when_disabled(monkeypatch):
    """托盘运行关闭：不构造 QSystemTrayIcon、不启动重试定时器。"""
    called = []
    monkeypatch.setattr(app_mod, "QSystemTrayIcon",
                        lambda *a, **k: called.append("icon"))
    monkeypatch.setattr(app_mod, "QTimer",
                        lambda *a, **k: called.append("timer"))
    ctl = _controller(tray_on=False)
    app_mod.Application._build_tray(ctl)
    assert called == [], "关闭时不应创建任何托盘/定时器对象"
    assert ctl.tray is None
    assert ctl._tray_retry.stopped is True


def test_destroy_tray_detaches_icon_and_menu():
    ctl = _controller(tray_on=False)
    ctl.tray = FakeTray()
    menu = SimpleNamespace(deleteLater=lambda: setattr(menu, "deleted", True))
    ctl._tray_menu = menu
    app_mod.Application._destroy_tray(ctl)
    assert ctl.tray is None and ctl._tray_menu is None
    assert ctl._tray_retry.stopped is True
    assert ctl.notifier.attached[-1] is None
    assert menu.deleted is True


def test_destroy_tray_is_safe_when_tray_was_never_built():
    """首次启动就关闭托盘运行：_tray_menu 尚未存在，不能 AttributeError。"""
    ctl = _controller(tray_on=False)
    del ctl._tray_menu
    app_mod.Application._destroy_tray(ctl)
    app_mod.Application._build_tray(ctl)
    assert ctl.tray is None


def test_apply_tray_mode_reacts_immediately(monkeypatch):
    """设置切换即时生效，无需重启：关→销毁，开→重建。"""
    built, destroyed = [], []
    monkeypatch.setattr(app_mod.Application, "_build_tray",
                        lambda self: built.append(True))
    monkeypatch.setattr(app_mod.Application, "_destroy_tray",
                        lambda self: destroyed.append(True))
    off = _controller(tray_on=False)
    app_mod.Application._apply_tray_mode(off)
    assert destroyed and not built
    built.clear(); destroyed.clear()
    on = _controller(tray_on=True)
    app_mod.Application._apply_tray_mode(on)
    assert built and not destroyed


def test_apply_settings_schedules_tray_mode_sync():
    assert "QTimer.singleShot(0, self._apply_tray_mode)" in APP_SRC
    assert "if not self.tray_enabled():" in APP_SRC
    assert "def _destroy_tray" in APP_SRC and "def _apply_tray_mode" in APP_SRC


# ---------------------------------------------------------------- 关闭窗口

def test_close_event_hides_only_when_tray_really_exists():
    assert win_mod.should_hide_in_tray(None) is False
    with_tray = SimpleNamespace(tray_enabled=lambda: True, tray=object())
    no_tray = SimpleNamespace(tray_enabled=lambda: True, tray=None)
    disabled = SimpleNamespace(tray_enabled=lambda: False, tray=object())
    assert win_mod.should_hide_in_tray(with_tray) is True
    assert win_mod.should_hide_in_tray(no_tray) is False
    assert win_mod.should_hide_in_tray(disabled) is False


def test_close_event_quits_when_tray_disabled():
    src = WIN_SRC.split("def closeEvent")[1]
    assert "should_hide_in_tray(ctl)" in src
    assert "event.accept()" in src
    assert "QTimer.singleShot(0, ctl.quit)" in src
    assert "QSystemTrayIcon.isSystemTrayAvailable()" not in src


# ---------------------------------------------------------------- 消息通知

class RecordingNotifier:
    def __init__(self): self.calls = []
    def notify(self, *a, **kw): self.calls.append((a, kw))


def _toast_host(msg_on=True):
    log = []
    obj = SimpleNamespace(
        settings=FakeSettings(messages_enabled=msg_on),
        notifier=RecordingNotifier(),
        _log=log.append,
    )
    obj.messages_enabled = lambda: app_mod.Application.messages_enabled(obj)
    return obj, log


def test_messages_off_suppresses_routine_notices():
    obj, log = _toast_host(False)
    app_mod.Application._toast(obj, "设置已保存")
    app_mod.Application._toast(obj, "今日积分变化：+10")
    assert obj.notifier.calls == [], "普通提示不能发系统消息"
    assert log == ["设置已保存", "今日积分变化：+10"], "仍要写运行日志"


def test_messages_off_keeps_severe_errors():
    obj, log = _toast_host(False)
    app_mod.Application._toast(obj, "同步失败：未登录", severe=True)
    assert len(obj.notifier.calls) == 1
    assert obj.notifier.calls[0][1]["timeout_ms"] == 10000
    assert log == [], "严重错误不必重复写日志"


def test_messages_on_behaves_as_before():
    obj, log = _toast_host(True)
    app_mod.Application._toast(obj, "设置已保存")
    assert len(obj.notifier.calls) == 1 and log == []


def test_sync_failure_is_the_only_severe_call_site():
    assert 'self._toast(message, severe=True)' in APP_SRC
    assert APP_SRC.count('self.notifier.notify(') == 1
    assert "_on_sync_fail" in APP_SRC
    # 其余提示统一走 _toast，不再有第二套直接通知
    for routine in ('新的一天', '早间打卡提醒', '今日积分变化', '设置已保存'):
        assert routine in APP_SRC


def test_remind_text_no_longer_mentions_tray_icon():
    assert "点圆环同步一下吧" in APP_SRC
    assert "点托盘图标同步一下吧" not in APP_SRC


# ---------------------------------------------------------------- UI 契约

def test_settings_page_has_two_switches_and_no_background_group():
    page = WIN_SRC.split("class SettingsPage")[1].split("class _AccountFacade")[0]
    assert 'self.tray_sw = Switch(' in page
    assert 'self.messages_sw = Switch(' in page
    assert "background_sw" not in page
    assert 'tr(lang, "tray_group")' in page
    assert 'tr(lang, "messages_group")' in page
    assert page.count('Card(number="') == 4
    assert "QInputDialog" not in page


def test_i18n_has_both_labels():
    assert vm.tr("zh", "tray_group") == "托盘运行"
    assert vm.tr("zh", "messages_group") == "消息通知"
    assert vm.tr("en", "tray_group") == "Tray icon"
    assert vm.tr("en", "messages_group") == "Notifications"


def test_config_defaults_declare_both_keys():
    src = (ROOT / "rewards_assistant" / "config.py").read_text("utf-8")
    assert '"messages_enabled": True' in src
    assert '"tray_enabled": True' in src

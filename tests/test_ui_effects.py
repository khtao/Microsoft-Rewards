# -*- coding: utf-8 -*-
"""界面交互回归：进度条比例、动画恒定开启、无悬浮提示、强度统一控制。

纯函数全部离线可测；Qt 类只做静态源码契约断言，不创建 QApplication。
本版契约：
* 全界面删除鼠标悬浮提示框（无自定义浮层、无原生 tooltip、无托盘 tooltip）；
* 「减少动画」开关删除，动画默认恒定开启；
* 设置页底部「拟人搜索默认设置」展示 5 项参数，由「拟人强度 低/中/高」
  统一控制，一次切换把整组参数写进经典 search_panel 并透传给执行层。
"""
from __future__ import annotations

from pathlib import Path

from rewards_assistant.ui.modern import theme, viewmodel as vm

ROOT = Path(__file__).parents[1]
WIDGETS = ROOT / "rewards_assistant" / "ui" / "modern" / "widgets.py"
EFFECTS = ROOT / "rewards_assistant" / "ui" / "modern" / "effects.py"
WINDOW = ROOT / "rewards_assistant" / "ui" / "modern" / "main_window.py"
LEGACY = ROOT / "rewards_assistant" / "ui" / "legacy.py"
CONFIG = ROOT / "rewards_assistant" / "config.py"


def row(**kw):
    base = {"key": "", "name": "", "category": "", "description": "",
            "points_max": 0, "points_earned": 0, "complete": 0, "link": ""}
    base.update(kw)
    return base


# ---------------------------------------------------------------- 进度条

def test_progress_fill_is_proportional_and_full_at_100():
    """截图 bug：每日活动 30/30 分（100%）的绿色条必须填满整条。"""
    assert vm.progress_fill(100, 316) == 316
    assert vm.progress_fill(100, 380) == 380
    assert vm.progress_fill(0, 316) == 0
    assert vm.progress_fill(30, 320) == 96
    assert vm.progress_fill(92, 300) == 276
    assert vm.progress_fill(1, 300) == 4
    assert vm.progress_fill(150, 300) == 300
    assert vm.progress_fill(-5, 300) == 0
    assert all(vm.progress_fill(p, 320) <= 320 for p in range(0, 101))


def test_full_group_reports_100_percent():
    tasks = [
        row(key="a", name="任务一", category="每日任务",
            points_max=10, points_earned=10, complete=1),
        row(key="b", name="任务二", category="每日任务",
            points_max=10, points_earned=10, complete=1),
        row(key="c", name="任务三", category="每日任务",
            points_max=10, points_earned=10, complete=1),
    ]
    daily = vm.group_tasks(tasks)[0]
    assert daily["label"] == "每日活动"
    assert (daily["earned"], daily["total"], daily["percent"]) == (30, 30, 100)
    assert vm.progress_fill(daily["percent"], 316) == 316


def test_progress_bar_is_drawn_proportionally_not_fixed_pixels():
    src = WIDGETS.read_text(encoding="utf8")
    assert "class ProgressBar" in src
    assert "vm.progress_fill" in src
    assert 'group["percent"] * 2.6' not in src
    assert "inner.setFixedWidth" not in src
    assert "ProgressBar(group[\"percent\"])" in src
    assert "GroupBlock(group)" in WINDOW.read_text(encoding="utf8")


# ---------------------------------------------------------------- 无悬浮提示

def test_no_hover_tip_ui_anywhere():
    """删除全部鼠标悬浮提示框：浮层类、安装器、原生 tooltip 都不存在。"""
    effects = EFFECTS.read_text(encoding="utf8")
    for gone in ("class HoverTip", "class HoverTipManager", "install_hover_tips",
                 "hide_all_tips", "set_tip(", "show_for(", "QFrame#tipCard",
                 "QGraphicsDropShadowEffect", "QEvent.Type.ToolTip",
                 "QEvent.Type.Enter", "QEvent.Type.Leave", "WA_ShowWithoutActivating",
                 "tip_bg", "reduce_motion"):
        assert gone not in effects, f"浮层残留：{gone}"
    for kept in ("def stop_animations", "def stagger_enter", "def fade_in",
                 "_safe_start", "hsaCancelled"):
        assert kept in effects, kept

    win = WINDOW.read_text(encoding="utf8")
    for gone in ("setToolTip(", "install_hover_tips", "hide_all_tips",
                 "_hsa_hover_manager", "effects.set_tip", "vm.setting_hint",
                 "set_reduce_motion", "theme.reduce_motion",
                 "ui_reduce_motion", "motion_sw"):
        assert gone not in win, f"界面残留悬浮提示 / 减少动画：{gone}"
    assert "effects.stop_animations(self)" in win
    assert "effects.stagger_enter(" in win and "effects.fade_in(self)" in win

    tray = (ROOT / "rewards_assistant" / "app.py").read_text(encoding="utf8")
    assert "self.tray.setToolTip(" not in tray
    assert "def update_tray_tooltip" not in tray


def test_theme_has_no_reduce_motion_state():
    src = (ROOT / "rewards_assistant" / "ui" / "modern" / "theme.py").read_text("utf8")
    assert "_REDUCE_MOTION" not in src
    assert "def reduce_motion" not in src
    assert "def set_reduce_motion" not in src
    assert not hasattr(theme, "reduce_motion")
    assert not hasattr(theme, "set_reduce_motion")
    assert ":hover" in theme.stylesheet()   # hover 配色反馈保留，只是不弹提示框


def test_tip_only_animation_helpers_removed_from_viewmodel():
    src = (ROOT / "rewards_assistant" / "ui" / "modern" / "viewmodel.py").read_text("utf8")
    for gone in ("def tip_is_short", "def tip_max_width", "def tip_max_height",
                 "def ring_hint", "def group_hint", "TIP_SHORT_CHARS",
                 "TIP_FADE_MS", "TIP_MAX_WIDTH", "TIP_MAX_HEIGHT_RATIO",
                 "SETTING_HINTS", "def setting_hint", "ui_reduce_motion"):
        assert gone not in src, f"提示词 / 减少动画死代码残留：{gone}"


# ---------------------------------------------------------------- 动画恒定开启

def test_animation_is_always_on_and_timings_match_extension():
    assert vm.TRANSITION_MS == 260
    assert vm.STAGGER_STEP_MS == 85
    assert vm.RING_ANIM_MS == 760 and vm.BAR_ANIM_MS == 680
    assert vm.animation_ms() == vm.TRANSITION_MS
    assert vm.animation_ms(900) == 900
    assert vm.stagger_delay(0) == 0
    assert vm.stagger_delay(3) == 255
    assert vm.ease_out_cubic(0) == 0.0
    assert vm.ease_out_cubic(1) == 1.0
    assert vm.ease_out_cubic(0.5) > 0.5
    widgets = WIDGETS.read_text(encoding="utf8")
    assert "theme.reduce_motion()" not in widgets
    assert "class StatusChip" in widgets and "_pulse" in widgets


# ---------------------------------------------------------------- 设置项

def test_visible_setting_keys_unique_and_all_bound():
    keys = list(vm.VISIBLE_SETTING_KEYS)
    assert len(keys) == len(set(keys))
    assert 8 <= len(keys) <= 12, f"可见设置项当前 {len(keys)}"
    assert "ui_search_level" in keys
    assert "ui_reduce_motion" not in keys and "ui_reduce_motion" not in vm.UI_DEFAULTS
    assert len(vm.SWITCH_KEYS) == len(set(vm.SWITCH_KEYS)) == 6
    assert "ui_schedule_enabled" not in vm.SWITCH_KEYS
    assert "ui_launch_at_login" not in vm.SWITCH_KEYS
    for key in keys:
        assert key in vm.UI_DEFAULTS, key
        assert key in vm.CLASSIC_BINDING, key


def test_status_chip_matches_extension_status_semantics():
    assert vm.status_kind("就绪") == "ready"
    assert vm.status_kind("正在同步 Rewards 数据…") == "busy"
    assert vm.status_kind("搜索中 3/10") == "busy"
    assert vm.status_kind("已完成，无需动作") == "done"
    assert vm.status_kind("同步失败：网络异常") == "error"
    assert vm.status_kind("Syncing status") == "busy"
    assert vm.status_kind("All tasks done — nothing to do") == "done"
    assert vm.status_color_key("busy") == "primary"
    assert vm.status_color_key("done") == "green_text"
    assert vm.status_color_key("error") == "warn"
    assert vm.status_color_key("ready") == "text_sub"
    assert vm.tr("zh", "status.all_done") == "已完成，无需动作"
    src = WIDGETS.read_text(encoding="utf8")
    assert "class StatusChip" in src and "class _StatusDot" in src
    assert "vm.status_kind" in src
    win = WINDOW.read_text(encoding="utf8")
    assert "self.status_chip = StatusChip(self._lang)" in win
    assert "done_chip" not in win


def test_wayland_desktop_file_registration_is_skipped():
    app = (ROOT / "rewards_assistant" / "app.py").read_text(encoding="utf8")
    assert 'str(app.platformName()).lower() != "wayland"' in app


def test_settings_page_has_four_cards_and_intensity_in_defaults_card():
    src = WINDOW.read_text(encoding="utf8")
    page = src.split("class SettingsPage")[1].split("class _AccountFacade")[0]
    assert "search_panel.level" not in page
    assert page.count('Card(number="') == 4
    assert page.count("add_row(") <= 10
    for gone in ("SWITCH_ROWS", "switch_map", "number_map", "cap_seg",
                 "cap_note", "auto_sync_seg", "log_sw", "motion_sw",
                 "NumberField", "TextField", "manual_note", "log_note",
                 "install_hover_tips", "setToolTip(", "manager.refresh"):
        assert gone not in page, f"旧控件 / 悬浮提示残留：{gone}"
    for kept in ('self.level_seg = Segmented(', 'self.sync_seg = Segmented(',
                 'self.remind_sw = Switch(', 'self.background_sw = Switch(',
                 'self.autostart_sw = Switch(', 'self.schedule_sw = Switch(',
                 'self.cal_seg = Segmented(', 'self.defaults_grid = QGridLayout(',
                 'def refresh_defaults_summary(', 'vm.default_settings_summary(',
                 'dsl.addWidget(Row('):
        assert kept in page, f"控件缺失：{kept}"
    assert 'Segmented(["低", "中", "高"]' in page
    # 强度控件必须放在「拟人搜索默认设置」卡（ds）里，而不是 02 同步卡
    ds = page.split("# 拟人搜索默认设置")[1]
    assert "self.level_seg = Segmented(" in ds
    assert "self.defaults_grid = QGridLayout(" in ds
    widgets = WIDGETS.read_text(encoding="utf8")
    assert "class NumberField" not in widgets and "class TextField" not in widgets


# ---------------------------------------------------------------- 精简设置模型

def test_managed_groups_write_several_real_classic_keys_at_once():
    p = dict(vm.UI_DEFAULTS)
    sync = vm.pref_to_settings(p, "ui_sync_mode", "15")
    assert sync == {"sync_interval_minutes": 15, "sync_interval_value_minutes": 15}
    off = vm.pref_to_settings(p, "ui_sync_mode", vm.tr("zh", "sync_off"))
    assert off["sync_interval_minutes"] == 0 and off["sync_interval_value_minutes"] == 60
    assert vm.pref_to_settings(p, "ui_sync_mode", "0")["sync_interval_minutes"] == 0

    remind = vm.pref_to_settings(p, "ui_remind_mode", False)
    assert remind["remind_time"] == "" and remind["evening_remind_time"] == ""
    assert set(remind) >= {"remind_time", "remind_time_value",
                           "evening_remind_time", "evening_remind_time_value"}
    on = vm.pref_to_settings(p, "ui_remind_mode", True)
    assert on["remind_time"] == "09:00" and on["evening_remind_time"] == "20:30"

    bg = vm.pref_to_settings(p, "ui_background_mode", False)
    assert bg["notify_after_sync"] is False and bg["close_to_tray"] is False
    assert bg["sleep_when_all_done"] is False
    assert set(vm.managed_group_keys("ui_background_mode")) >= {
        "notify_after_sync", "silent_when_all_done", "close_to_tray"}


def test_hidden_keys_are_fixed_to_defaults_and_groups_stay_editable():
    hidden = set(vm.HIDDEN_DEFAULT_KEYS)
    visible = set(vm.VISIBLE_SETTING_KEYS)
    assert hidden.isdisjoint(visible) and hidden | visible == set(vm.UI_DEFAULTS)
    for key in ("ui_search_cap", "ui_repeat", "ui_keywords",
                "ui_sources", "ui_chart_days", "ui_log_enabled",
                "ui_sync_interval", "ui_remind_time", "ui_evening_time",
                "ui_notify_after_sync", "ui_close_to_tray", "ui_sleep_all_done"):
        assert key in hidden, key
    for key in ("ui_language", "ui_theme", "ui_search_level", "ui_sync_mode",
                "ui_remind_mode", "ui_background_mode", "ui_schedule_times",
                "ui_launch_at_login"):
        assert key in visible, key

    patch = vm.defaults_patch()
    sp = patch["search_panel"]
    assert sp["level"] == 2 and sp["repeat"] == vm.REPEAT_DEFAULT == 12
    assert sp["keywords"] == "" and sp["keyword_sources"] == tuple(vm.DEFAULT_SOURCES)
    # 默认档位必须带齐 6 项拟人参数
    for key in vm.INTENSITY_PARAM_KEYS:
        assert key in sp, key
    assert vm.defaults_patch(3)["search_panel"]["level"] == 3
    assert "vm.defaults_patch(current_level)" in WINDOW.read_text(encoding="utf8")
    assert patch["chart_days"] == 30 and patch["ui_log_enabled"] is True
    assert patch["sync_interval_minutes"] == 60
    assert patch["notify_after_sync"] is True and patch["sleep_when_all_done"] is False
    for key in ("ui_language", "ui_theme", "ui_schedule_enabled",
                "ui_schedule_times", "ui_launch_at_login", "ui_calendar_view"):
        assert key not in patch, key
    assert vm.SIMPLIFY_MARKER == "ui_prefs_simplified_v2"
    assert "if not self.settings.get(vm.SIMPLIFY_MARKER):" in WINDOW.read_text(encoding="utf8")


def test_reset_patch_restores_both_hidden_and_visible():
    patch = vm.reset_patch()
    assert patch["auto_run_enabled"] is False
    assert patch["auto_run_times"] == ["09:00"]
    assert patch["launch_at_login"] is False
    assert patch["sync_interval_minutes"] == 60
    assert patch["search_panel"]["repeat"] == 12
    assert patch["search_panel"]["level"] == 2
    assert "ui_reduce_motion" not in patch
    assert "sleep_until_day" not in patch and "prune_version" not in patch


# ---------------------------------------------------------------- 启动时间

def test_next_schedule_time_starts_at_8_and_steps_by_30_minutes():
    seq: list[str] = []
    for _ in range(5):
        seq.append(vm.next_schedule_time(seq))
    assert seq == ["08:00", "08:30", "09:00", "09:30", "10:00"]
    assert vm.next_schedule_time(["08:00", "09:00", "10:00"]) == "08:30"
    assert vm.next_schedule_time(["08:00", "08:30", "09:00"]) == "09:30"
    ten = [f"{8 + i // 2:02d}:{30 * (i % 2):02d}" for i in range(10)]
    assert vm.next_schedule_time(ten) is None
    full = [f"{8 + (i * 30) // 60:02d}:{(i * 30) % 60:02d}"
            for i in range(vm.SCHEDULE_SLOTS)]
    assert vm.next_schedule_time(full) is None


def test_plus_button_adds_time_without_input_dialog():
    src = WINDOW.read_text(encoding="utf8")
    assert "QInputDialog" not in src
    assert "vm.next_schedule_time(times)" in src
    assert 'add = QPushButton("+")' in src
    assert "normalize_run_times(times + [candidate])" in src
    assert "点 + 从 08:00 起每 30 分钟追加" in vm.tr("zh", "schedule_hint")


# ---------------------------------------------------------------- 拟人强度统一控制

def test_intensity_profiles_cover_three_levels_and_six_displayed_params():
    assert list(vm.INTENSITY_PROFILES) == ["低", "中", "高"]
    assert [vm.INTENSITY_PROFILES[k]["level"] for k in ("低", "中", "高")] == [1, 2, 3]
    labels = [row[1] for row in vm.DEFAULT_SUMMARY_ROWS]
    assert labels == ["搜索轮数", "单轮耗时", "回车提交", "结果点击",
                      "滚动屏数", "地址栏搜索"]
    keys = {row[0] for row in vm.DEFAULT_SUMMARY_ROWS}
    assert keys == {"repeat", "round_seconds", "enter_rate",
                    "click_prob", "scroll_screens", "address_bar_rate"}
    for choice, profile in vm.INTENSITY_PROFILES.items():
        for key in keys | set(vm.INTENSITY_PARAM_KEYS):
            assert key in profile, (choice, key)
        assert profile["repeat"] >= 1
        lo, hi = profile["round_seconds"]
        assert 0 < lo < hi <= profile["max_round_seconds"]
        assert 0 <= profile["enter_rate"] <= 1
        assert 0 <= profile["click_prob"] <= 1
        assert profile["scroll_screens"] > 0
    # 除「地址栏搜索」这项固定默认配置外，三档取值必须彼此不同
    for key in keys - {"address_bar_rate"}:
        assert len({str(vm.INTENSITY_PROFILES[c][key]) for c in vm.INTENSITY_PROFILES}) == 3, key
    # 25% 是固定默认配置，三档一致，但仍由强度切换统一写回
    for choice, profile in vm.INTENSITY_PROFILES.items():
        assert profile["address_bar_rate"] == 0.25, choice


def test_middle_profile_matches_api_run_human_search_defaults():
    """「中」档是默认：5 项显示值与 api 真实执行默认逐项一致。"""
    import inspect
    from rewards_assistant import api
    sig = inspect.signature(api.run_human_search)
    mid = vm.intensity_profile("中")
    assert sig.parameters["repeat"].default == mid["repeat"] == 12
    assert sig.parameters["level"].default == mid["level"] == 2
    assert sig.parameters["round_seconds"].default == mid["round_seconds"]
    assert sig.parameters["enter_rate"].default == mid["enter_rate"]
    assert sig.parameters["click_prob"].default == mid["click_prob"]
    assert sig.parameters["scroll_screens"].default == mid["scroll_screens"]
    assert sig.parameters["address_bar_rate"].default == mid["address_bar_rate"] == 0.25


def test_intensity_switch_writes_all_six_params_at_once():
    """一个控件统一控制：切档一次写齐整组参数，且关键词/来源不丢失。"""
    p = dict(vm.UI_DEFAULTS)
    p.update({"ui_keywords": "旅行", "ui_sources": "baidu,bing"})
    for choice in ("低", "中", "高"):
        patch = vm.pref_to_settings(p, "ui_search_level", choice)
        assert set(patch) == {"search_panel"}, patch
        sp = patch["search_panel"]
        profile = vm.intensity_profile(choice)
        for key in vm.INTENSITY_PARAM_KEYS:
            assert sp[key] == profile[key], (choice, key)
        assert sp["keywords"] == "旅行"
        assert sp["keyword_sources"] == ("baidu", "bing")
        # 其它设置写入不能把 6 项参数冲掉
        again = vm.pref_to_settings({**p, "ui_search_level": choice},
                                    "ui_keywords", "摄影")
        for key in vm.INTENSITY_PARAM_KEYS:
            if key not in ("repeat", "level"):
                assert again["search_panel"][key] == profile[key], (choice, key)


def test_default_settings_summary_is_six_rows_and_follows_intensity():
    zh = dict(vm.default_settings_summary(dict(vm.UI_DEFAULTS), "zh"))
    assert len(zh) == len(vm.DEFAULT_SUMMARY_ROWS) == 6
    assert zh["搜索轮数"] == "12 轮"
    assert zh["单轮耗时"] == "16–29 秒"
    assert zh["回车提交"] == "50%"
    assert zh["结果点击"] == "35%"
    assert zh["滚动屏数"] == "1.2 屏"
    assert zh["地址栏搜索"] == "25%"

    low = dict(vm.default_settings_summary(
        {"ui_search_level": "低", "ui_intensity_params": vm.intensity_profile("低")}, "zh"))
    assert low == {"搜索轮数": "8 轮", "单轮耗时": "10–18 秒",
                   "回车提交": "35%", "结果点击": "20%", "滚动屏数": "0.6 屏",
                   "地址栏搜索": "25%"}
    high = dict(vm.default_settings_summary(
        {"ui_search_level": "高", "ui_intensity_params": vm.intensity_profile("高")}, "zh"))
    assert high == {"搜索轮数": "16 轮", "单轮耗时": "24–40 秒",
                    "回车提交": "70%", "结果点击": "55%", "滚动屏数": "2 屏",
                    "地址栏搜索": "25%"}

    en = dict(vm.default_settings_summary(
        {"ui_search_level": "中", "ui_intensity_params": vm.intensity_profile("中")}, "en"))
    assert en["Search rounds"] == "12 rounds"
    assert en["Round length"] == "16–29 sec"
    assert en["Enter submits"] == "50%"
    assert en["Result clicks"] == "35%"
    assert en["Result scrolling"] == "1.2 screens"
    assert en["Address-bar search"] == "25%"


def test_intensity_params_are_persisted_and_transmitted_to_classic_chain():
    """真实生效：设置白名单 + 经典 _collect 透传 + 旧配置一次性迁移。"""
    cfg = CONFIG.read_text(encoding="utf8")
    assert "SEARCH_PANEL_KEYS" in cfg
    for key in vm.INTENSITY_PARAM_KEYS:
        assert f'"{key}"' in cfg
    legacy = LEGACY.read_text(encoding="utf8")
    for key in ("round_seconds", "max_round_seconds", "gap_seconds",
                "enter_rate", "click_prob", "scroll_screens",
                "address_bar_rate"):
        assert key in legacy, key
    assert "params[key]" in legacy
    assert "state[key] = sp[key]" in legacy, "_view_state 必须保留强度参数"
    win = WINDOW.read_text(encoding="utf8")
    assert "vm.intensity_upgrade_patch(self.settings)" in win
    assert "vm.repeat_upgrade_patch(self.settings)" in win


def test_intensity_upgrade_patch_completes_old_search_panel_without_overwriting():
    class S(dict):
        def get(self, k, d=None):
            return dict.get(self, k, d)

    old = S({"search_panel": {"level": 3, "repeat": 20, "keywords": "旅行"}})
    patch = vm.intensity_upgrade_patch(old)
    assert patch[vm.INTENSITY_MARKER] is True
    sp = patch["search_panel"]
    assert sp["level"] == 3 and sp["repeat"] == 20 and sp["keywords"] == "旅行"
    high = vm.intensity_profile("高")
    for key in ("round_seconds", "enter_rate", "click_prob", "scroll_screens"):
        assert sp[key] == high[key], key
    # 已迁移过的配置不再重复写
    assert vm.intensity_upgrade_patch(S({vm.INTENSITY_MARKER: True})) == {}


def test_repeat_default_is_12_everywhere():
    """自动完成任务的默认搜索次数为 12 次。"""
    assert vm.REPEAT_DEFAULT == 12
    assert vm.UI_DEFAULTS["ui_repeat"] == 12
    assert vm.UI_DEFAULTS["ui_search_cap"] == 12
    assert vm.intensity_profile("中")["repeat"] == 12
    assert vm.defaults_patch()["search_panel"]["repeat"] == 12
    assert vm.reset_patch()["search_panel"]["repeat"] == 12
    assert "REPEAT_DEFAULT = 12" in (ROOT / "rewards_assistant" / "ui" / "modern" / "viewmodel.py").read_text("utf8")
    assert "repeat=12" in LEGACY.read_text(encoding="utf8")
    assert "repeat: int = 12" in (ROOT / "rewards_assistant" / "api.py").read_text("utf8")
    # 死类 TaskConfig 已删除：默认值只保留在真正生效的三处
    assert "class TaskConfig" not in CONFIG.read_text(encoding="utf8")


def test_settings_values_roundtrip_for_managed_groups(tmp_path, monkeypatch):
    from rewards_assistant.config import Settings

    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    s = Settings()
    s.update(vm.pref_to_settings(vm.settings_values(s), "ui_sync_mode", "15"))
    s.update(vm.pref_to_settings(vm.settings_values(s), "ui_remind_mode", False))
    s.update(vm.pref_to_settings(vm.settings_values(s), "ui_background_mode", False))
    v = vm.settings_values(Settings())
    assert v["ui_sync_mode"] == "15"
    assert v["ui_remind_mode"] is False
    assert v["ui_background_mode"] is False
    assert Settings().get("sync_interval_minutes") == 15
    assert Settings().get("remind_time") == ""
    assert Settings().get("close_to_tray") is False

"""不需要 PySide6/Edge 的回归测试：配置迁移、上限、桌面搜索、日汇总。"""
from __future__ import annotations

import ast
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from rewards_assistant.config import Settings, normalize_run_times
from rewards_assistant.pending import (
    all_reward_tasks_done, outstanding, pending_activity_links,
    pending_desktop_rounds, planned_rounds,
)
from rewards_assistant.storage import Store

ROOT = Path(__file__).resolve().parents[1]


def task(key, max_points=20, earned=0, *, complete=0, link="https://example.org"):
    return dict(key=key, points_max=max_points, points_earned=earned,
                complete=complete, link=link, name=key)


def test_times_validation():
    assert normalize_run_times(["21:45", "07:00", "13:30"]) == ["07:00", "13:30", "21:45"]
    assert normalize_run_times([]) == []
    assert len(normalize_run_times([f"{i:02d}:30" for i in range(10)])) == 10
    for invalid in (["09:00", "09:00"], ["24:00"], ["09:60"],
                    [f"{i:02d}:00" for i in range(11)], "09:00", ["9:00"]):
        with pytest.raises(ValueError):
            normalize_run_times(invalid)


def test_migrate_old_switch_and_save_atomically(monkeypatch, tmp_path):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    path = tmp_path / "settings.json"
    path.write_text('{"auto_open_time": "07:35", "auto_run_pending": true, '
                    '"search_panel": {"level": 3}, "sync_interval_minutes": 30}', encoding="utf-8")
    settings = Settings()
    assert settings.get("auto_run_enabled") is True
    assert settings.get("auto_run_times") == ["07:35"]
    assert settings.get("search_panel")["level"] == 3
    assert settings.update({"auto_run_times": ["18:15", "08:20"]})
    assert Settings().get("auto_run_times") == ["08:20", "18:15"]
    from rewards_assistant import state
    saved = state.json_get(state.K_SETTINGS) or {}
    assert "auto_run_pending" not in saved
    assert not path.exists(), "旧 settings.json 迁移后应被清理"
    with pytest.raises(ValueError):
        settings.update({"auto_run_times": ["12:00"] * 11})
    assert Settings().get("auto_run_times") == ["08:20", "18:15"]


def test_new_install_schedule_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("MS_REWARDS_HOME", str(tmp_path))
    s = Settings()
    assert s.get("auto_run_enabled") is False
    assert s.get("auto_run_times") == ["09:00"]


def test_pending_filters_mobile_and_zero_placeholders():
    tasks = [task("pc_search", 55, 30), task("mobile_search", 35, 0),
             task("daily_1", 5, 0), task("daily_2", 10, 10, complete=1),
             task("other", 0, 0)]
    assert pending_desktop_rounds(tasks) == 9      # 25 分剩余 / 3 分一次，向上取整
    assert [t["key"] for t in pending_activity_links(tasks)] == ["daily_1"]
    assert not all_reward_tasks_done(tasks)
    assert outstanding(tasks[-1]) is False
    for t in tasks:
        if t["points_max"]:
            t["points_earned"] = t["points_max"]
    assert all_reward_tasks_done(tasks)
    assert pending_desktop_rounds(tasks) == 0


def test_prune_obsolete_official_tasks(tmp_path):
    db = Store(str(tmp_path / "clean.db"))
    day = date.today().isoformat()
    db.upsert_task(day, "old_event", "过期活动", "每日任务", 5, 0, False)
    db.upsert_task(day, "live_event", "有效活动", "每日任务", 5, 5, True)
    assert db.remove_missing_official_tasks(day, ["live_event"]) == 1
    assert [r["key"] for r in db.tasks_of_day(day)] == ["live_event"]
    assert db.remove_missing_official_tasks(day, []) == 0


def test_daily_all_done_ignores_zero_point_search_placeholders(tmp_path):
    db = Store(str(tmp_path / "test.db"))
    day = date.today().isoformat()
    db.upsert_task(day, "daily_a", "任务 A", "每日任务", 10, 10, True)
    db.upsert_task(day, "pc_search", "电脑搜索", "搜索", 0, 0, False)
    summary = db.summarize_day(day)
    assert summary["all_done"]
    db.upsert_task(day, "pc_search", "电脑搜索", "搜索", 30, 10, False)
    assert not db.summarize_day(day)["all_done"]


def _read_method(path, name, class_name):
    """单独编译 Qt 控制器业务方法，避免测试环境缺少 Qt 的限制。"""
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    method.decorator_list = []
    return compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(path), "exec")


def test_search_runner_uses_only_pc_remaining():
    class FakeRunner:
        @staticmethod
        def defaults():
            return {"repeat": 5, "level": 2}

    scope = dict(date=date, SearchRunner=FakeRunner,
                 planned_rounds=planned_rounds)
    exec(_read_method("rewards_assistant/ui/legacy.py", "for_pending", "SearchRunner"), scope)
    run_config = scope["for_pending"]

    class StoreStub:
        def daily(self, _day):
            return {"all_done": 0}
        def tasks_of_day(self, _day):
            return [task("pc_search", 45, 30), task("mobile_search", 100, 0)]

    # 45 - 30 = 15 分剩余 → 5 次；cap=100 不再被任何全局硬顶截断
    assert run_config(StoreStub(), {"level": 3, "repeat": 100}) == {"repeat": 5, "level": 3}
    class AllDone(StoreStub):
        def daily(self, _day):
            return {"all_done": 1}
    assert run_config(AllDone(), {}) is None
    class OnlyMobile(StoreStub):
        def tasks_of_day(self, _day):
            return [task("mobile_search", 100, 0)]
    assert run_config(OnlyMobile(), {}) is None


def test_scheduler_once_per_slot_and_skip_if_disabled():
    clock = SimpleNamespace(value=datetime(2026, 10, 19, 9, 0))
    class Clock:
        @staticmethod
        def now():
            return clock.value

    scope = dict(datetime=Clock, timedelta=__import__("datetime").timedelta, APP_TITLE="test")
    exec(_read_method("rewards_assistant/app.py", "_tick", "Application"), scope)
    tick = scope["_tick"]

    class ScheduleStub:
        def __init__(self):
            self._today = clock.value.date()
            self._fired = set()
            self._next_sync = None
            self.settings = SimpleNamespace(get=lambda key, default=None: {
                "sync_interval_minutes": 0,
                "auto_run_enabled": True, "auto_run_times": ["09:00", "12:00"],
                "remind_time": "08:30", "evening_remind_time": "20:00",
            }.get(key, default))
            self.calls = []
        def _remember_run_day(self, _now):
            pass
        def _request_run_pending(self, **kwargs):
            self.calls.append(("run", kwargs["scheduled"]))
        def _remind(self, key):
            self.calls.append(("remind", key))

    obj = ScheduleStub()
    tick(obj)
    tick(obj)
    assert obj.calls == [("run", True)]
    clock.value = datetime(2026, 10, 19, 12, 0)
    tick(obj)
    tick(obj)
    assert obj.calls == [("run", True), ("run", True)]


def test_scheduled_run_does_nothing_if_all_completed_or_search_running():
    scope = {}
    exec(_read_method("rewards_assistant/app.py", "_request_run_pending", "Application"), scope)
    start = scope["_request_run_pending"]

    class FakeApp:
        def __init__(self, done=False, running=False):
            self.done = done
            self.running = running
            self.events = []
            self._after_sync = set()
            self._syncing = False
            self.window = SimpleNamespace(runner=SimpleNamespace(is_running=lambda: running),
                                              update_running_state=lambda: None)
        def auto_running(self):
            return self.running
        def _all_done(self):
            return self.done
        def activity_running(self):
            return False
        def _log(self, msg):
            self.events.append(msg)
        def _toast(self, msg):
            self.events.append(msg)
        def sync_now(self, auto):
            self.events.append(("sync", auto))
    done = FakeApp(done=True)
    start(done, scheduled=True)
    assert not done._after_sync and ("sync", True) not in done.events
    running = FakeApp(running=True)
    start(running, scheduled=True)
    assert not running._after_sync and ("sync", True) not in running.events
    incomplete = FakeApp()
    start(incomplete, scheduled=True)
    assert incomplete._after_sync == {"run"}
    assert ("sync", True) in incomplete.events
    incomplete._syncing = True
    start(incomplete, scheduled=True)
    assert incomplete.events.count(("sync", True)) == 1


def test_activity_selection_includes_tasks_without_links():
    from rewards_assistant.pending import pending_activity_items
    item = lambda key, **kw: SimpleNamespace(
        key=key, name=kw.get('name', '活动'), offer_id=kw.get('offer_id', ''),
        link=kw.get('link', ''), complete=kw.get('complete', False),
        points_max=kw.get('max', 10), points_earned=kw.get('earned', 0))
    tasks = [item('more:0'), item('dailyset:0', offer_id='abc'), item('pc_search'),
             item('mobile_search'), item('more:1', complete=True),
             item('more:2', max=0)]
    assert [t.key for t in pending_activity_items(tasks)] == ['more:0', 'dailyset:0']


def test_activity_run_never_does_official_verification():
    """运行期间不做逐项核验；整轮结束后由控制器统一同步一次。"""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    worker = (root / "rewards_assistant" / "activity_worker.py").read_text("utf-8")
    click = (root / "rewards_assistant" / "activity_click.py").read_text("utf-8")
    app = (root / "rewards_assistant" / "app.py").read_text("utf-8")
    assert "confirmed_completed" not in worker and "confirmed_completed" not in click
    assert "fetch_dashboard" not in worker
    assert "核验失败" not in worker
    assert "官方已确认完成" not in worker
    assert "官方确认" not in app
    assert "运行结束后统一同步" in app


def test_card_click_uses_mouse_not_direct_url(monkeypatch):
    from rewards_assistant import activity_click as act
    events = []
    class Locator:
        def scroll_into_view_if_needed(self, **kw):
            events.append('scroll')
        def bounding_box(self):
            return {'x': 10, 'y': 20, 'width': 100, 'height': 50}
    class Mouse:
        def move(self, x, y, **kw):
            events.append('move')
        def down(self):
            events.append('down')
        def up(self):
            events.append('up')
    monkeypatch.setattr(act, 'locate_activity', lambda page, task: Locator())
    monkeypatch.setattr(act.time, 'sleep', lambda seconds: None)
    page = SimpleNamespace(mouse=Mouse())
    assert act.click_activity(page, SimpleNamespace(name='目标卡片'))
    assert events == ['scroll', 'move', 'down', 'up']


def test_daily_set_offer_locator_skips_completed():
    from rewards_assistant.activity_click import locate_activity
    class Element:
        def __init__(self, label):
            self.label = label
        def get_attribute(self, name):
            return self.label
        def is_visible(self, **kwargs):
            return True
    class Locator:
        def __init__(self):
            self.items = [Element('Offer is completed'), Element('Offer not completed'),
                          Element('Offer not completed')]
        def count(self):
            return len(self.items)
        def nth(self, i):
            return self.items[i]
    class Frame:
        def locator(self, css):
            return Locator() if 'threeOffers' in css else SimpleNamespace(count=lambda: 0)
        def get_by_text(self, *args, **kwargs):
            return SimpleNamespace(count=lambda: 0)
    page = SimpleNamespace(frames=[Frame()])
    target = lambda i: SimpleNamespace(key=f'dailyset:{i}', name='', offer_id='', link='', raw_name='')
    assert locate_activity(page, target(0)) is None
    assert locate_activity(page, target(1)) is not None


def test_controller_runs_activity_before_pc_search():
    scope = {}
    exec(_read_method('rewards_assistant/app.py', '_run_pending_from_snapshot', 'Application'),
         {'pending_activity_items': lambda ts: [t for t in ts if t.key != 'pc_search']}, scope)
    dispatch = scope['_run_pending_from_snapshot']

    class App:
        def __init__(self):
            self.calls = []
        def _all_done(self):
            return False
        def _start_activity_clicks(self, tasks, then_search=False):
            self.calls.append(('activity', len(tasks), then_search))
        def _start_pc_search(self):
            self.calls.append('search')
        def _log(self, msg):
            self.calls.append(msg)

    app = App()
    dispatch(app, SimpleNamespace(tasks=[SimpleNamespace(key='more:0'),
                                           SimpleNamespace(key='pc_search')]))
    assert app.calls == [('activity', 1, True)]
    app.calls.clear()
    dispatch(app, SimpleNamespace(tasks=[SimpleNamespace(key='pc_search')]))
    assert app.calls == ['search']


def test_activity_completion_cancels_followup_when_stopped():
    source = _read_method('rewards_assistant/app.py', '_on_activity_completed', 'Application')
    scope = {}
    exec(source, scope)
    finish = scope['_on_activity_completed']
    class Fake:
        def __init__(self):
            self._after_sync = set()
            self._activity_then_search = True
            self.calls = []
            self.window = SimpleNamespace(update_running_state=lambda: None)
        def _log(self, msg): self.calls.append(msg)
        def _set_open_progress(self, msg): self.calls.append(msg)
        def _start_pc_search(self): self.calls.append('search')
        def sync_now(self, auto): self.calls.append(('sync', auto))
    stats = {'total': 1, 'clicked': 1, 'verified': 0,
             'unconfirmed': ['task'], 'missing': [], 'stopped': True, 'error': ''}
    obj = Fake()
    finish(obj, stats)
    assert obj._after_sync == set()
    assert obj._activity_then_search is False
    assert ('sync', True) not in obj.calls
    stats['stopped'] = False
    obj._activity_then_search = True
    finish(obj, stats)
    # 运行期间禁止同步；搜索已在同一个 ActivityWorker 里跑完，
    # 结束时只汇总统计，不再关掉浏览器另起 _start_pc_search()。
    assert obj._after_sync == set()
    assert ('sync', True) not in obj.calls
    assert 'search' not in obj.calls
    stats['search'] = {'ok': 3, 'fail': 0, 'clicked': 1, 'seconds': 12.5}
    obj.calls.clear()
    finish(obj, stats)
    assert any('拟人搜索完成' in str(c) for c in obj.calls)
    assert 'search' not in obj.calls
    assert obj._after_sync == set()
    assert ('sync', True) not in obj.calls






def _page_result(html, path='/dashboard', *, kind='ok', status=200, ok=True,
                 signin=False):
    from rewards_assistant.session import PageResult
    return PageResult(path=path, ok=ok, status=status, html=html,
                      kind=kind, signin_page=signin)


def _two_page_fetcher(dash_html, earn_html):
    """新链路只依赖 ReadOnlyFetcher.fetch_many，测试无需浏览器与网络。"""
    class Fetcher:
        def fetch_many(self, paths):
            return {
                '/dashboard': _page_result(dash_html, '/dashboard'),
                '/earn': _page_result(earn_html, '/earn'),
            }
        def fetch(self, path):
            return self.fetch_many([path])[path]
    return Fetcher()


def _rsc_points(available='1,234', today='45', claimable='60'):
    q = '\\"'
    def card(label, value):
        return (f'{q}className{q}:{q}text-labelControl{q},{q}children{q}:{q}{label}{q}'
                f'<i></i>{q}className{q}:{q}text-pageHeader{q},'
                f'{q}children{q}:{q}{value}{q}')
    return (card('可用积分', available) + card('今日积分', today)
            + card('可领取', claimable))


def _dom_card(title, points=10, done=False):
    return (f'<h2>每日活动</h2><a href="#">'
            f'<div class="bg-statusSuccessRewardsBg">'
            f'<p class="text-metadata text-fgCtrlOnImage">{points}</p></div>'
            f'<p class="line-clamp-3 text-globalBody2Strong">{title}</p>'
            f'<p class="line-clamp-3 text-fgCtrlNeutralSecondaryRest">说明</p>'
            f'{"<span>已完成</span>" if done else ""}</a>')


def _sync_readonly(store, dash_html, earn_html, **kw):
    from rewards_assistant import sync as sync_mod
    return sync_mod.sync_once(store, _two_page_fetcher(dash_html, earn_html),
                              day='2026-10-21', force=True, **kw)


def test_sync_uses_page_trio_and_reuses_saved_title(tmp_path):
    """三项积分取页面真值；重复同步更新进度但不覆盖中文标题。"""
    store = Store(str(tmp_path / 'page_sync.db'))
    html = _rsc_points() + _dom_card('必应首页测验')
    first = _sync_readonly(store, html, html)
    assert first['ok'] and first['points'] == {
        'available': 1234, 'today': 45, 'claimable': 60}
    row = store.daily('2026-10-21')
    assert row['today_points'] == 45 and row['claimable_points'] == 60
    assert row['points_earned'] == 45 and row['points_source'] == 'page_today'
    keys_before = {r['key'] for r in store.tasks_of_day('2026-10-21')}

    second = _sync_readonly(store, _rsc_points(today='55') + _dom_card(
        '必应首页测验', done=True), html)
    assert second['ok'] and second['points']['today'] == 55
    assert {r['key'] for r in store.tasks_of_day('2026-10-21')} == keys_before
    assert store.daily('2026-10-21')['points_earned'] == 55


def test_sync_does_not_reuse_other_cards_title(tmp_path):
    """不同卡片生成不同稳定 key，不会把上一张的标题安到新卡上。"""
    store = Store(str(tmp_path / 'new_card.db'))
    _sync_readonly(store, _rsc_points() + _dom_card('旧活动'), '')
    _sync_readonly(store, _rsc_points() + _dom_card('新活动'), '')
    names = {r['name'] for r in store.tasks_of_day('2026-10-21')}
    assert '新活动' in names


def test_sync_missing_points_never_writes_zero(tmp_path):
    """页面没给「今日 / 可领取」就留 NULL，UI 显示 --，绝不写 0。"""
    store = Store(str(tmp_path / 'missing.db'))
    html = '<script>{"availablePoints":"777"}</script>' + _dom_card('一个小测验')
    out = _sync_readonly(store, html, html)
    assert out['points']['today'] is None and out['points']['claimable'] is None
    row = store.daily('2026-10-21')
    assert row['today_points'] is None and row['claimable_points'] is None
    assert row['available_points'] == 777


def test_sync_transient_keeps_snapshot_and_never_blames_login(tmp_path):
    """429 / 超时是服务问题，不是登录失效；不写库、不诱导重新登录。"""
    from rewards_assistant import sync as sync_mod
    from rewards_assistant.session import TRANSIENT_ERROR
    store = Store(str(tmp_path / 'transient.db'))
    store.upsert_available('2026-10-21', 999, 2)

    class Fetcher:
        def fetch_many(self, paths):
            return {p: _page_result('', p, kind='transient', ok=False, status=503)
                    for p in paths}
        def fetch(self, path):
            return self.fetch_many([path])[path]

    out = sync_mod.sync_once(store, Fetcher(), day='2026-10-21', force=True)
    assert out['ok'] is False and out['state'] == TRANSIENT_ERROR
    assert out['stale'] is True and '无需重新登录' in out['message']
    assert store.daily('2026-10-21')['available_points'] == 999


def test_store_migrates_legacy_title_match_fields(tmp_path):
    import sqlite3
    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE tasks (id INTEGER PRIMARY KEY, day TEXT, key TEXT, '
                   'name TEXT, category TEXT, points_max INTEGER, points_earned INTEGER, '
                   'complete INTEGER, link TEXT, manual INTEGER, title_source TEXT, '
                   'updated_at TEXT, UNIQUE(day,key))')
    store = Store(str(path))
    store.upsert_task('2026-10-21', 't', '活动', '更多活动', 10, 0, False,
                      offer_id='official-offer', raw_name='offer-internal-name')
    row = store.tasks_of_day('2026-10-21')[0]
    assert row['offer_id'] == 'official-offer'
    assert row['raw_name'] == 'offer-internal-name'


def test_sync_page_enrichment_cooldown_and_failure_retry():
    from datetime import timedelta
    clock = SimpleNamespace(value=datetime(2026, 10, 21, 10))
    class Clock:
        @staticmethod
        def now(): return clock.value

    created = []
    class Signal:
        def connect(self, fn): pass
    class FakeWorker:
        def __init__(self, store, day, app, enrich_pages, **kwargs):
            created.append(enrich_pages)
            self.progress = self.succeeded = self.failed = self.finished = Signal()
        def start(self): pass
        def deleteLater(self): pass

    scope = dict(datetime=Clock, timedelta=timedelta, SyncWorker=FakeWorker,
                 day_str=lambda: '2026-10-21')
    exec(_read_method('rewards_assistant/app.py', 'sync_now', 'Application'), scope)
    start = scope['sync_now']
    obj = SimpleNamespace(store=object(), _syncing=False, _workers=[],
                          auto_running=lambda: False,
                          _last_page_enrichment=None, _sync_enrich_current=False,
                          window=SimpleNamespace(set_busy=lambda *a: None),
                          _on_sync_ok=lambda *_: None, _on_sync_fail=lambda *_: None,
                          _forget_worker=lambda *_: None)
    # 初次允许页面补充。成功后下一次 45 分钟内只有 API。
    start(obj)
    assert created == [True]
    obj._syncing = False
    obj._last_page_enrichment = clock.value
    clock.value += timedelta(minutes=2)
    start(obj)
    assert created == [True, False]
    obj._syncing = False
    clock.value += timedelta(minutes=46)
    start(obj)
    assert created == [True, False, True]
    # 模拟网络失败：_on_sync_ok 没有被调用，冷却时间仍为上一次成功时刻。
    assert obj._last_page_enrichment < clock.value
    obj._syncing = False
    start(obj)
    assert created == [True, False, True, True]


# 1.1.3: 桌面图标、Linux panel/桌面入口、移除主页刷新控件的打包回归测试

def test_application_icon_assets():
    import struct
    import xml.etree.ElementTree as ET
    icons = ROOT / "assets/icons"
    svg = icons / "app.svg"
    assert svg.is_file()
    assert ET.parse(svg).getroot().tag.endswith("svg")
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256, 512):
        f = icons / f"app_{size}.png"
        assert f.exists(), f"Missing {f}"
        with f.open("rb") as fd:
            assert fd.read(8) == b"\x89PNG\r\n\x1a\n"
            fd.read(8)  # PNG IHDR chunk length and type
            width, height = struct.unpack(">II", fd.read(8))
            assert (width, height) == (size, size)
    assert (icons / "app.ico").is_file()


def test_app_icon_is_bundled_in_build_and_desktop_file():
    # 图标资源由统一的 MSRewardsAssistant.spec 收集（构建脚本不再重复 --add-data）
    build = (ROOT / "build_ubuntu.sh").read_text("utf-8")
    spec = (ROOT / "MSRewardsAssistant.spec").read_text("utf-8")
    deb = (ROOT / "build_deb.sh").read_text("utf-8")
    main = (ROOT / "rewards_assistant/app.py").read_text("utf-8")
    assert "MSRewardsAssistant.spec" in build
    assert "assets" in spec and "icons" in spec
    assert "assets/icons" in spec
    assert "Icon=ms-rewards-assistant" in deb
    assert "ms-rewards-assistant.desktop" in deb
    assert "icons/hicolor/scalable/apps/ms-rewards-assistant.svg" in deb
    assert 'setDesktopFileName("ms-rewards-assistant")' in main
    assert "QSystemTrayIcon(app_icon()" in main


def test_main_toolbar_hides_manual_refresh_but_preserves_tray_actions():
    """唯一界面用隐藏 QAction 保留 F5 / 托盘入口，不在顶部栏加按钮。"""
    s = (ROOT / "rewards_assistant/ui/modern/main_window.py").read_text("utf-8")
    assert 'self.act_sync = QAction("立即同步", self)' in s
    assert 'self.act_refresh = QAction("刷新界面", self)' in s
    assert "self.addAction(self.act_sync)" in s
    assert "self.addAction(self.act_refresh)" in s
    assert "bar.addAction(act_sync)" not in s
    tray = (ROOT / "rewards_assistant/app.py").read_text("utf-8")
    # 托盘按需求只保留 4 项；隐藏 QAction 仍供 F5 / 快捷键使用，不进托盘菜单
    assert "self.window.act_sync.trigger" in tray
    assert "self.window.act_refresh.trigger" not in tray
    for label in ("显示主页面", "立即同步", "自动完成任务", "退出"):
        assert f'"{label}"' in tray
    for gone in ("刷新界面", "仅打开未完成任务页", "今日完成后休眠至明天",
                 "真正退出程序", '"停止"'):
        assert f'"{gone}"' not in tray, gone

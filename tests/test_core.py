"""不需要 PySide6/Edge 的回归测试：配置迁移、上限、桌面搜索、日汇总。"""
from __future__ import annotations

import ast
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from rewards_assistant.config import Settings, normalize_run_times
from rewards_assistant.pending import (
    all_reward_tasks_done, outstanding, pending_activity_links, pending_desktop_rounds,
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
    assert "auto_run_pending" not in path.read_text(encoding="utf-8")
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
    assert pending_desktop_rounds(tasks) == 5
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

    scope = dict(date=date, SearchRunner=FakeRunner, pending_desktop_rounds=pending_desktop_rounds)
    exec(_read_method("rewards_assistant/ui/main_window.py", "for_pending", "SearchRunner"), scope)
    run_config = scope["for_pending"]

    class StoreStub:
        def daily(self, _day):
            return {"all_done": 0}
        def tasks_of_day(self, _day):
            return [task("pc_search", 45, 30), task("mobile_search", 100, 0)]

    assert run_config(StoreStub(), {"level": 3, "repeat": 100}) == {"repeat": 3, "level": 3}
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
                "sync_interval_minutes": 0, "auto_open_time": "09:00",
                "auto_run_enabled": True, "auto_run_times": ["09:00", "12:00"],
                "remind_time": "08:30", "evening_remind_time": "20:00",
            }.get(key, default))
            self.calls = []
        def _remember_run_day(self, _now):
            pass
        def _auto_open_morning(self):
            self.calls.append("open")
        def _request_run_pending(self, **kwargs):
            self.calls.append(("run", kwargs["scheduled"]))
        def _remind(self, key):
            self.calls.append(("remind", key))

    obj = ScheduleStub()
    tick(obj)
    tick(obj)
    assert obj.calls == ["open", ("run", True)]
    clock.value = datetime(2026, 10, 19, 12, 0)
    tick(obj)
    tick(obj)
    assert obj.calls == ["open", ("run", True), ("run", True)]


def test_scheduled_run_does_nothing_if_all_completed_or_search_running():
    scope = {}
    exec(_read_method("rewards_assistant/app.py", "_request_run_pending", "Application"), scope)
    start = scope["_request_run_pending"]

    class FakeApp:
        def __init__(self, done=False, running=False):
            self.done = done
            self.events = []
            self._after_sync = set()
            self._syncing = False
            self.window = SimpleNamespace(runner=SimpleNamespace(is_running=lambda: running),
                                              update_running_state=lambda: None)
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


def test_official_confirmation_never_counts_click_as_success():
    from rewards_assistant.activity_click import confirmed_completed
    original = SimpleNamespace(key='more:0', raw_name='秋日', offer_id='offer-123')
    snapshot = lambda tasks: SimpleNamespace(tasks=tasks)
    item = lambda key, raw, offer, points, complete=False: SimpleNamespace(
        key=key, raw_name=raw, offer_id=offer, points_max=10,
        points_earned=points, complete=complete)
    assert not confirmed_completed(original, snapshot([]))
    assert not confirmed_completed(original, snapshot([item('more:0', '秋日', 'offer-123', 0)]))
    assert confirmed_completed(original, snapshot([item('more:3', '秋日', 'offer-123', 10)]))
    assert not confirmed_completed(original, snapshot([item('more:0', '秋日', 'different', 10)]))
    old_no_id = SimpleNamespace(key='dailyset:0', raw_name='卡片', offer_id='')
    assert confirmed_completed(old_no_id, snapshot([item('dailyset:0', '卡片', '', 10)]))
    assert not confirmed_completed(old_no_id, snapshot([item('dailyset:0', '别的卡片', '', 10)]))


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
        def _log(self, msg): pass
        def _set_open_progress(self, msg): self.calls.append(msg)
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
    assert obj._after_sync == {'search'}
    assert ('sync', True) in obj.calls


def test_parallel_optional_html_fetch_and_dashboard_reuse(monkeypatch):
    """两个页面确实并发；读取 dashboard HTML 后无需另发签到天数请求。"""
    import threading
    from rewards_assistant import page_titles
    barrier = threading.Barrier(2)
    seen = []

    def fake_fetch(url, _cookies, _timeout):
        seen.append(url)
        barrier.wait(timeout=2)
        return '{"dailySetItems":[{"offerId":"foo","title":"每日活动"}]}'

    monkeypatch.setattr(page_titles, 'fetch_html', fake_fetch)
    result = page_titles.fetch_real_titles({'token': 'fake'}, timeout=(2, 3),
                                            with_dashboard_html=True)
    assert len(seen) == 2
    assert len(result['daily']) == 1
    assert len(result['routine']) == 1
    assert 'dailySetItems' in result['dashboard_html']


def test_optional_html_failure_never_aborts_other_page(monkeypatch):
    from rewards_assistant import page_titles
    seen = []

    def fake_fetch(url, _cookies, _timeout):
        seen.append(url)
        if url.endswith('/earn'):
            raise TimeoutError('页面不可用')
        return '{"activityCards":[{"offerId":"foo","title":"任务标题"}]}'

    monkeypatch.setattr(page_titles, 'fetch_html', fake_fetch)
    result = page_titles.fetch_real_titles({}, include=('routine', 'daily'))
    assert len(result['daily']) == 1
    assert result['routine'] == []
    result = page_titles.fetch_real_titles({}, include=('daily',), with_dashboard_html=True)
    assert len(seen) == 3  # 只按需拉取页面
    assert result['dashboard_html']


def _sync_runner(scope, store, *, enrich_pages):
    """只运行后台线程的业务逻辑，测试机不需要安装 PySide6。"""
    scope = dict(scope)
    scope.setdefault('AuthError', RuntimeError)
    scope.setdefault('Snapshot', object)
    exec(_read_method('rewards_assistant/sync.py', 'run', 'SyncWorker'), scope)
    sent = SimpleNamespace(progress=[], succeeded=[], failed=[])
    obj = SimpleNamespace(store=store, day='2026-10-21', enrich_pages=enrich_pages,
                          progress=SimpleNamespace(emit=sent.progress.append),
                          succeeded=SimpleNamespace(emit=sent.succeeded.append),
                          failed=SimpleNamespace(emit=sent.failed.append))
    scope['run'](obj)
    return sent


def test_fast_sync_refreshes_authoritative_progress_and_keeps_same_offer_title(tmp_path):
    """快速同步不拉 HTML；同一 offerId 的旧中文标题可以继续显示，但积分必更新。"""
    from rewards_assistant.rewards_client import TaskItem, Snapshot
    store = Store(str(tmp_path / 'fast_sync.db'))
    store.upsert_task('2026-10-21', 'more:0', '活动真实名称', '更多活动',
                      10, 0, False, title_source='page', offer_id='offer-abc')
    store.upsert_task('2026-10-21', 'pc_search', '电脑搜索', '搜索',
                      90, 0, False)
    snapshot = Snapshot(day='2026-10-21', available_points=998, level=2,
                        tasks=[TaskItem('more:0', '活动ID', '更多活动', 10, 10, True,
                                        offer_id='offer-abc', title_source='guess'),
                               TaskItem('pc_search', '电脑搜索', '搜索', 90, 30, False,
                                        title_source='api')])
    calls = []

    class Auth:
        def __init__(self, logger):
            pass
        def fetch_dashboard(self, timeout):
            calls.append(('api', timeout))
            return {'test': 1}

    scope = dict(AuthManager=Auth, parse_dashboard=lambda data, day: snapshot,
                 fetch_real_titles=lambda *args, **kw: pytest.fail('不应抓取页面'),
                 extract_streak=lambda html: pytest.fail('不应额外抓取签到'),)
    result = _sync_runner(scope, store, enrich_pages=False)
    assert not result.failed and len(result.succeeded) == 1
    assert calls == [('api', (4, 12))]
    rows = {r['key']: r for r in store.tasks_of_day('2026-10-21')}
    assert rows['more:0']['name'] == '活动真实名称'
    assert rows['more:0']['complete'] == 1 and rows['more:0']['points_earned'] == 10
    assert rows['pc_search']['points_earned'] == 30
    assert store.daily('2026-10-21')['available_points'] == 998


def test_fast_sync_does_not_reuse_other_offers_title(tmp_path):
    from rewards_assistant.rewards_client import TaskItem, Snapshot
    store = Store(str(tmp_path / 'new_offer.db'))
    store.upsert_task('2026-10-21', 'more:0', '旧任务标题', '更多活动',
                      10, 0, False, title_source='page', offer_id='old-offer')
    snap = Snapshot(day='2026-10-21', tasks=[
        TaskItem('more:0', '新活动', '更多活动', 10, 0, False,
                 offer_id='new-offer', title_source='guess')])
    class Auth:
        def __init__(self, logger): pass
        def fetch_dashboard(self, timeout): return {}
    result = _sync_runner(dict(AuthManager=Auth, parse_dashboard=lambda *_: snap),
                          store, enrich_pages=False)
    assert not result.failed
    row = store.tasks_of_day('2026-10-21')[0]
    assert row['name'] == '新活动'
    assert row['offer_id'] == 'new-offer'


def test_optional_enrichment_uses_one_dashboard_html_for_streak(tmp_path):
    from rewards_assistant.rewards_client import TaskItem, Snapshot
    store = Store(str(tmp_path / 'enrich.db'))
    snap = Snapshot(day='2026-10-21', tasks=[
        TaskItem('more:0', '活动ID', '更多活动', 10, 0, False, offer_id='x')])
    pages = []
    class Auth:
        def __init__(self, logger): pass
        def fetch_dashboard(self, timeout): return {}
        def jar(self): return {'x': 'y'}
        def fetch_page_html(self, *a, **kw): pytest.fail('不得再次读取 dashboard 页面')

    def fetch_titles(cookies, timeout, *, include, with_dashboard_html):
        pages.append((include, with_dashboard_html))
        return {'daily': [], 'routine': [], 'dashboard_html': '连续签到 5 天'}

    result = _sync_runner(dict(AuthManager=Auth, parse_dashboard=lambda *_: snap,
                               fetch_real_titles=fetch_titles,
                               merge_titles=lambda *args, **kw: 0,
                               extract_streak=lambda html: 5),
                          store, enrich_pages=True)
    assert not result.failed
    assert pages == [({'routine', 'daily'}, True)]
    assert store.daily('2026-10-21')['official_streak'] == 5


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
        def __init__(self, store, day, app, enrich_pages):
            created.append(enrich_pages)
            self.progress = self.succeeded = self.failed = self.finished = Signal()
        def start(self): pass
        def deleteLater(self): pass

    scope = dict(datetime=Clock, timedelta=timedelta, SyncWorker=FakeWorker,
                 day_str=lambda: '2026-10-21')
    exec(_read_method('rewards_assistant/app.py', 'sync_now', 'Application'), scope)
    start = scope['sync_now']
    obj = SimpleNamespace(store=object(), _syncing=False, _workers=[],
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
    pyinstaller = (ROOT / "build_ubuntu.sh").read_text("utf-8")
    deb = (ROOT / "build_deb.sh").read_text("utf-8")
    main = (ROOT / "rewards_assistant/app.py").read_text("utf-8")
    assert "assets/icons:assets/icons" in pyinstaller
    assert "Icon=ms-rewards-assistant" in deb
    assert "ms-rewards-assistant.desktop" in deb
    assert "icons/hicolor/scalable/apps/ms-rewards-assistant.svg" in deb
    assert 'setDesktopFileName("ms-rewards-assistant")' in main
    assert "QSystemTrayIcon(app_icon()" in main


def test_main_toolbar_hides_manual_refresh_but_preserves_tray_actions():
    s = (ROOT / "rewards_assistant/ui/main_window.py").read_text("utf-8")
    start = s.index("    def _build_toolbar(self)")
    end = s.index("    def _build_statusbar(self)", start)
    toolbar = s[start:end]
    assert "bar.addAction(act_sync)" not in toolbar
    assert "bar.addAction(act_refresh)" not in toolbar
    assert "self.addAction(act_sync)" in toolbar
    assert "self.addAction(act_refresh)" in toolbar
    assert "self.act_sync = act_sync" in toolbar
    assert "self.act_refresh = act_refresh" in toolbar

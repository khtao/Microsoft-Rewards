"""全局配置与路径管理。

所有用户数据（SQLite 数据库、浏览器登录态、设置）都放在同一个数据目录下，
避免污染项目目录，也方便用户备份与彻底卸载。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Literal, Optional, Union

APP_NAME = "MSRewardsAssistant"
APP_TITLE = "Microsoft Rewards 每日助手"
APP_VERSION = "1.0.0"

REWARDS_HOME = "https://rewards.bing.com/"
REWARDS_DASHBOARD = "https://rewards.bing.com/"
# 两个任务页面：dashboard = 每日活动，earn = 日常任务（真实显示名从这里抓）
REWARDS_DASHBOARD_URL = "https://rewards.bing.com/dashboard"
REWARDS_EARN_URL = "https://rewards.bing.com/earn"
# 微软官方 dashboard 数据接口（页面本身也在调用它）
USER_INFO_API = "https://rewards.bing.com/api/getuserinfo?type=1&X-Requested-With=XMLHttpRequest"


@dataclass
class BrowserConfig:
    user_data_dir: str | None = None
    copy_profile: bool = False
    edge_path: str | None = None
    headless: bool = False
    window_size: str | None = None
    proxy: str | None = None
    slow_mo: int = 0
    keep_browser: bool = True  # 任务之间复用同一个 Edge 实例
    idle_close: float = 300.0  # 空闲多少秒后关闭浏览器
    sandbox: Optional[Union[Literal["on"], Literal["off"]]] = None  # None=自动（非 root 开沙箱）
    policy_dir: str | None = None  # 写 Edge 管理策略用，抑制 --no-sandbox 警告横幅


def data_dir() -> Path:
    """返回数据目录，可通过环境变量 MS_REWARDS_HOME 覆盖（方便测试与便携版）。"""
    override = os.environ.get("MS_REWARDS_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = Path.home() / ".local" / "share" / APP_NAME.lower()
    base.mkdir(parents=True, exist_ok=True)
    return base


def db_path() -> Path:
    return data_dir() / "rewards.db"


def profile_dir() -> Path:
    """Playwright 持久化用户目录，登录态保存在这里。"""
    p = data_dir() / "browser_profile"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _settings_path() -> Path:
    return data_dir() / "settings.json"


DEFAULT_SETTINGS = {
    # 自动同步间隔（分钟），0 表示不自动同步
    "sync_interval_minutes": 60,
    "sync_interval_value_minutes": 60,
    # 每日提醒时间（本地时间 HH:MM），空字符串表示关闭
    "remind_time": "09:00",
    "remind_time_value": "09:00",
    # 晚间二次提醒时间，空字符串表示关闭
    "evening_remind_time": "20:30",
    "evening_remind_time_value": "20:30",
    # 当天全部任务完成时是否静默（不再提醒）
    "silent_when_all_done": True,
    # 连续天数的判定方式：all_done=当日任务全部完成；any_points=当日有积分进账
    "streak_rule": "any_points",
    # 每天定时自动打开未完成任务页（HH:MM），空字符串=关闭
    "auto_open_time": "09:00",
    "auto_open_time_value": "09:00",
    "auto_run_pending": False,
    # 逐个打开时的随机间隔区间（秒），模拟人的操作节奏
    "auto_open_gap_min": 5,
    "auto_open_gap_max": 10,
    # 是否随系统启动（Windows 写注册表 / macOS 写 LaunchAgent / Linux 写 autostart）
    "launch_at_login": False,
    # 同步后是否弹出通知
    "notify_after_sync": True,
    # 主窗口关闭时最小化到托盘而不是退出
    "close_to_tray": True,
    # 趋势图显示天数
    "chart_days": 30,
    "chart_metric": "earned",
    # 拟人搜索页的全部表单选项。
    "search_panel": {},
}


@dataclass
class SearchConfig:
    """单次搜索的行为参数。"""
    engine: Literal["bing"] = "bing"  # 只测 Bing
    level: int = 3  # 拟人强度 1~3
    enter_rate: float = 0.5  # 回车提交占比
    click_prob: float = 0.35  # 点击结果概率
    scroll_screens: float = 1.2  # 结果页滚动屏数
    no_back: bool = False  # 点击结果后不返回
    on_captcha: Literal["wait", "skip", "abort"] = "wait"


@dataclass
class TaskConfig:
    """一次任务（可含多轮）。"""
    repeat: int = 30  # 重复轮数
    round_seconds_min: float = 15.0  # 单轮耗时下限（秒）
    round_seconds_max: float = 30.0  # 单轮耗时上限（秒）
    gap_min: float = 2.0  # 轮间最小间隔
    gap_max: float = 8.0  # 轮间最大间隔
    source: Literal["hot", "news", "mixed", "custom"] = "mixed"
    keywords: list = field(default_factory=list)  # source=custom 时必填
    hot_ttl: float = 900.0  # 热词缓存秒数
    refresh_hotwords: bool = False  # 强制实时重抓
    seed: int | None = None
    search: SearchConfig = field(default_factory=SearchConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)

    def validate(self):
        if self.repeat < 1:
            raise ValueError("repeat 必须 >= 1")
        if not (3 <= self.round_seconds_min <= self.round_seconds_max <= 600):
            raise ValueError("单轮耗时区间非法：需满足 3 <= min <= max <= 600")
        if self.source == "custom" and not self.keywords:
            raise ValueError("source=custom 时必须提供 keywords")
        if self.gap_min > self.gap_max:
            self.gap_min, self.gap_max = self.gap_max, self.gap_min
        return self

    def to_dict(self):
        return asdict(self)


class Settings:
    """JSON 设置存储：合并更新、原子替换、旧版本设置兼容。"""

    def __init__(self) -> None:
        self._data = dict(DEFAULT_SETTINGS)
        self.load()

    def load(self) -> None:
        path = _settings_path()
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    # 已取消浏览器选择，下次保存时自动丢弃旧版字段。
                    loaded.pop("browser_name", None)
                    self._data.update(loaded)
            except (OSError, ValueError, TypeError):
                # 损坏文件不阻断启动；下一次保存使用正常数据原子替换。
                pass

    def _write(self, data: dict) -> None:
        path = _settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=".settings-", suffix=".tmp", delete=False,
            ) as file:
                temp_name = file.name
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.write("\n")
            os.replace(temp_name, path)  # 避免程序意外退出导致设置文件只写了一半
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

    def save(self) -> None:
        self._write(self._data)

    def get(self, key: str, default=None):
        return self._data.get(key, DEFAULT_SETTINGS.get(key, default))

    def update(self, changes: dict) -> bool:
        """一次保存多项；无变化不写盘。失败时保留原内存设置。"""
        changed = {k: v for k, v in changes.items()
                   if k not in self._data or self._data[k] != v}
        if not changed:
            return False
        new_data = {**self._data, **changed}
        self._write(new_data)
        self._data = new_data
        return True

    def set(self, key: str, value) -> None:
        self.update({key: value})

    def as_dict(self) -> dict:
        return dict(self._data)

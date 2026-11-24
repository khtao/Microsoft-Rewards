"""全局配置与路径管理。

所有用户数据（SQLite 数据库、浏览器登录态、设置）都放在同一个数据目录下，
避免污染项目目录，也方便用户备份与彻底卸载。

**多开环境**：一个进程对应一个实例（``--instance <id>`` / 环境变量
``MS_REWARDS_INSTANCE``）。内置实例 ``default`` 沿用原来的数据目录，旧数据
零迁移；其余实例各自落在 ``<base>/instances/<id>/`` 下，设置、积分库、
加密凭证、浏览器 profile、运行日志、自动化快照、开机自启项全部独立，
因此可以登录不同的 Microsoft 账号且互不干扰。

「启动默认环境」是写在根目录 ``default_instance.json`` 的指针：不传
``--instance`` 也没有环境变量时，进程进入指针指向的环境。它与内置
``default`` 目录是两个概念——后者是所有实例的根目录，永远存在、永远
不可删除，因此删环境只删 ``instances/<id>/``，不会动到其它环境。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from functools import lru_cache
import re
from typing import Optional

APP_NAME = "MSRewardsAssistant"
APP_TITLE = "积分管理助手"
APP_VERSION = "2.2"

# search_panel 允许持久化的键：4 个经典表单项 + 拟人强度档位的 5 项参数。
# round_seconds / gap_seconds 是 [min, max] 区间，随 JSON 存为 list。
SEARCH_PANEL_KEYS = (
    "repeat", "level", "keywords", "keyword_sources",
    "round_seconds", "max_round_seconds", "gap_seconds",
    "enter_rate", "click_prob", "scroll_screens",
)

SYNC_MIN_INTERVAL_SEC = 900        # 自动同步的最短间隔，避免频繁打扰服务端
LOGIN_WINDOW_SECONDS = 300         # 「打开浏览器登录」窗口保持时长


# ---------------------------------------------------------------- 实例标识

ENV_INSTANCE = "MS_REWARDS_INSTANCE"
DEFAULT_INSTANCE = "default"
DEFAULT_INSTANCE_POINTER = "default_instance.json"
_SINGLE_KEY = "MSRewardsAssistant_SingleInstance_v1"
_WIN_APP_ID = "Independent.RewardsActivityCompanion"
INSTANCE_META = "instance.json"


def normalize_instance_id(raw) -> str:
    """实例 id 只接受 1~32 位 ASCII slug；其余一律回落默认实例。

    不做「清洗后接受」：中文名、``../`` 路径片段、超长串都应明确拒绝，
    否则用户以为开了新环境，实际却写进了某个意想不到的目录。显示名由
    ``instance.json`` 的 ``label`` 单独保存，可以任意中文。
    """
    s = str(raw or "").strip()
    if not s or len(s) > 32 or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}", s):
        return DEFAULT_INSTANCE
    name = s.lower()
    return DEFAULT_INSTANCE if name == "instances" else name


def default_instance_pointer_path() -> Path:
    """启动默认环境指针文件；放在根目录，不属于任何一个实例的数据目录。"""
    return base_data_dir() / DEFAULT_INSTANCE_POINTER


@lru_cache(maxsize=8)
def _preferred_default_for(base: str) -> str:
    """按数据根目录缓存；不同 ``MS_REWARDS_HOME`` 互不串值。"""
    try:
        pointer = Path(base) / DEFAULT_INSTANCE_POINTER
        obj = json.loads(pointer.read_text(encoding="utf-8"))
        ident = normalize_instance_id(obj.get("id") if isinstance(obj, dict) else None)
    except (OSError, ValueError, TypeError, AttributeError):
        return DEFAULT_INSTANCE
    if ident == DEFAULT_INSTANCE:
        return DEFAULT_INSTANCE
    if not (Path(base) / "instances" / ident / INSTANCE_META).is_file():
        return DEFAULT_INSTANCE
    return ident


def preferred_default_id() -> str:
    """不带 ``--instance`` 启动时进入的环境；文件无效一律回落内置 default。

    指针指向的环境必须真实存在，避免删环境后留下一个空壳默认值。写入后会
    清缓存；另一个进程改了指针，本进程重启后生效。
    """
    return _preferred_default_for(str(base_data_dir()))


def set_preferred_default(ident) -> str:
    """把某个环境设为启动默认；内置 default 表示恢复出厂设置。"""
    name = normalize_instance_id(ident)
    if name != DEFAULT_INSTANCE and not (
            instances_root() / name / INSTANCE_META).is_file():
        raise ValueError("该环境不存在，无法设为默认")
    pointer = default_instance_pointer_path()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    tmp = pointer.with_name(pointer.name + ".tmp")
    tmp.write_text(json.dumps({"id": name}, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(str(tmp), str(pointer))
    _preferred_default_for.cache_clear()
    return name


def is_preferred_default(ident: str) -> bool:
    return normalize_instance_id(ident) == preferred_default_id()


def instance_id() -> str:
    """当前进程的实例 id（进程启动后不再变化）。

    显式 ``--instance`` / ``MS_REWARDS_INSTANCE`` 优先级最高；否则读启动
    默认环境指针。多开进程一律带显式参数，不会被指针改写。
    """
    explicit = os.environ.get(ENV_INSTANCE)
    if explicit:
        return normalize_instance_id(explicit)
    return preferred_default_id()


def set_instance_id(raw) -> str:
    """入口处设定实例并写入环境变量；必须在导入数据层之前调用。"""
    ident = normalize_instance_id(raw)
    os.environ[ENV_INSTANCE] = ident
    return ident


def is_default_instance(ident: Optional[str] = None) -> bool:
    return (ident if ident is not None else instance_id()) == DEFAULT_INSTANCE


def base_data_dir() -> Path:
    """所有实例共享的根目录（默认实例本身就用它）。"""
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


def instances_root() -> Path:
    return base_data_dir() / "instances"


def instance_home(ident: Optional[str] = None) -> Path:
    """某个实例独占的数据目录；默认实例沿用既有数据目录。

    传 ident 可查询「别的实例」的目录（不切换当前进程环境），供环境管理
    界面列目录用；不传则用当前进程实例。
    """
    name = normalize_instance_id(ident) if ident is not None else instance_id()
    home = base_data_dir() if name == DEFAULT_INSTANCE else instances_root() / name
    home.mkdir(parents=True, exist_ok=True)
    return home


def instance_meta_path(ident: Optional[str] = None) -> Path:
    return instance_home(ident) / INSTANCE_META


def instance_label(ident: Optional[str] = None) -> str:
    """实例显示名；默认实例固定显示中文「默认环境」（界面可按语言替换）。"""
    name = normalize_instance_id(ident) if ident is not None else instance_id()
    if name == DEFAULT_INSTANCE:
        return "默认环境"
    try:
        meta = json.loads(instance_meta_path(name).read_text(encoding="utf-8"))
        label = str(meta.get("label") or "").strip()
        if label:
            return label[:24]
    except (OSError, ValueError, TypeError):
        pass
    return name


def window_title() -> str:
    return APP_TITLE if is_default_instance() else f"{APP_TITLE} · {instance_label()}"


def single_instance_key(ident: Optional[str] = None) -> str:
    """QLocalServer 名按实例区分：同环境只开一份，不同环境可并存。"""
    name = normalize_instance_id(ident) if ident is not None else instance_id()
    return _SINGLE_KEY if name == DEFAULT_INSTANCE else f"{_SINGLE_KEY}_{name}"


def win_app_id() -> str:
    name = instance_id()
    return _WIN_APP_ID if name == DEFAULT_INSTANCE else f"{_WIN_APP_ID}.{name}"


def autostart_name() -> str:
    name = instance_id()
    return APP_NAME if name == DEFAULT_INSTANCE else f"{APP_NAME}_{name}"


# ---------------------------------------------------------------- 数据路径
# 下面全部经 ``data_dir()`` 派生，因此旧代码无需改动就自动按实例隔离。

def data_dir() -> Path:
    """当前实例的数据目录；可通过 MS_REWARDS_HOME 覆盖根目录。"""
    return instance_home()


def db_path() -> Path:
    return data_dir() / "rewards.db"


def profile_dir() -> Path:
    """Playwright 持久化用户目录，登录态保存在这里（每个实例独立）。"""
    p = data_dir() / "browser_profile"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _settings_path() -> Path:
    return data_dir() / "settings.json"


def log_path() -> Path:
    return data_dir() / "run.log"


def search_log_path() -> Path:
    return data_dir() / "search_actions.jsonl"


def credentials_path() -> Path:
    return data_dir() / "session_cookies.bin"


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
    # 独立的“自动完成未完成任务”时间表，0~10 个且互不重复。
    # 既有 auto_run_pending/auto_open_time 会在加载时迁移。
    "auto_run_enabled": False,
    "auto_run_times": ["09:00"],
    "auto_run_fired_day": "",
    "auto_run_fired_times": [],
    # 是否随系统启动（Windows 写注册表 / macOS 写 LaunchAgent / Linux 写 autostart）
    "launch_at_login": False,
    # 消息通知总开关（默认开）。关闭后除同步/登录失败等严重错误外，
    # 跨天、早晚提醒、积分变化、设置已保存等一律只写日志、不弹系统消息。
    "messages_enabled": True,
    # 同步后是否弹出通知；与 messages_enabled 同值写入，兼容经典语义。
    "notify_after_sync": True,
    # 托盘运行总开关（默认开）。关闭后不创建托盘图标，关闭窗口即彻底退出。
    "tray_enabled": True,
    # 主窗口关闭时最小化到托盘而不是退出；与 tray_enabled 同值，兼容经典语义。
    "close_to_tray": True,
    # 在官方任务全部完成后低内存休眠：进程仍运行，次日自动恢复托盘。
    "sleep_when_all_done": False,
    "sleep_until_day": "",
    # 趋势图显示天数
    "chart_days": 30,
    "chart_metric": "earned",
    # DEBUG 级别细目日志（debug.log）：含异常堆栈与上下文；run.log 仍只记
    # 日常操作级流水。可用环境变量 MS_REWARDS_DEBUG=0 临时关闭细目。
    "debug_log_enabled": True,
    # 账户与设置中拟人搜索区域的全部表单选项（兼容原独立选项卡）。
    "search_panel": {},
}


_TIME_RE = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


def normalize_run_times(times) -> list[str]:
    """验证启动时刻：每天本地 HH:MM；最多 10 个，不允许重复。"""
    if not isinstance(times, (list, tuple)):
        raise ValueError("启动时间必须为列表")
    if len(times) > 10:
        raise ValueError("最多只能设置 10 个启动时间点")
    if any(not isinstance(t, str) or not _TIME_RE.fullmatch(t) for t in times):
        raise ValueError("启动时间必须为 HH:MM（24 小时制）")
    if len(set(times)) != len(times):
        raise ValueError("自动完成任务的启动时间不能重复")
    return sorted(times)


def _copy_defaults(value):
    """按内建容器递归复制设置默认值，标量直接返回。

    DEFAULT_SETTINGS 由标量、字符串列表与一两层嵌套字典组成；只处理
    dict / list / tuple 即可让返回的副本与模块常量完全隔离。
    """
    if isinstance(value, dict):
        return {k: _copy_defaults(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_copy_defaults(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_copy_defaults(v) for v in value)
    return value


class Settings:
    """JSON 设置存储：合并更新、原子替换、既有版本设置兼容。"""

    def __init__(self) -> None:
        self._data = _copy_defaults(DEFAULT_SETTINGS)
        self.load()

    def load(self) -> None:
        path = _settings_path()
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    # 已取消浏览器选择，下次保存时自动丢弃废弃字段。
                    for obsolete in ("browser_name", "earn_browser_fallback", "streak_rule",
                                     "auto_open_gap_min", "auto_open_gap_max"):
                        loaded.pop(obsolete, None)
                    if isinstance(loaded.get("search_panel"), dict):
                        # 老版本高级选项已取消，不再默默影响浏览器行为。
                        # 拟人强度档位的 6 项参数随 search_panel 一起持久化。
                        loaded["search_panel"] = {
                            k: v for k, v in loaded["search_panel"].items()
                            if k in SEARCH_PANEL_KEYS}
                    # 「后台与通知」拆成「托盘运行 / 消息通知」时的一次性迁移：
                    # 旧组关闭会把下面三个键同时写成 False。若新键尚未落盘且
                    # 命中该特征，两个新开关继承「关闭」，不擅自重新打开用户
                    # 明确关掉的托盘与系统消息；全新安装仍默认打开。
                    if ("tray_enabled" not in loaded
                            and "messages_enabled" not in loaded
                            and loaded.get("notify_after_sync") is False
                            and loaded.get("silent_when_all_done") is False
                            and loaded.get("close_to_tray") is False):
                        loaded["tray_enabled"] = False
                        loaded["messages_enabled"] = False
                    # 从既有的「到点自动运行」开关迁移，保留原有时间。
                    if "auto_run_times" not in loaded:
                        old_time = loaded.get("auto_open_time") or loaded.get("auto_open_time_value", "09:00")
                        loaded["auto_run_times"] = [old_time] if _TIME_RE.fullmatch(str(old_time)) else ["09:00"]
                        loaded["auto_run_enabled"] = bool(loaded.get("auto_run_pending", False))
                    # 单时刻的自动打开与多时刻任务计划重复，取消旧自动打开计划。
                    # 不擅自迁移为自动搜索（可能造成意外自动操作）。
                    loaded.pop("auto_open_time", None)
                    loaded.pop("auto_open_time_value", None)
                    loaded.pop("auto_run_pending", None)
                    try:
                        loaded["auto_run_times"] = normalize_run_times(loaded["auto_run_times"])
                    except ValueError:
                        loaded["auto_run_times"] = ["09:00"]
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
        changes = dict(changes)
        if "auto_run_times" in changes:
            changes["auto_run_times"] = normalize_run_times(changes["auto_run_times"])
        # 不再往设置文件回写已废弃的开关。
        for obsolete in ("auto_run_pending", "earn_browser_fallback", "streak_rule",
                         "auto_open_gap_min", "auto_open_gap_max", "browser_name",
                         "auto_open_time", "auto_open_time_value"):
            changes.pop(obsolete, None)
        if isinstance(changes.get("search_panel"), dict):
            changes["search_panel"] = {
                k: v for k, v in changes["search_panel"].items()
                if k in SEARCH_PANEL_KEYS}
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

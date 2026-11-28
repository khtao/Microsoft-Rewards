# -*- coding: utf-8 -*-
"""多开环境管理：列出、创建、启动彼此独立的实例进程。

一个进程只绑定一个实例（见 ``config.instance_id()``）。每个实例有独立的：

* ``rewards.db``（积分历史、任务、定时计划、设置、同步状态、缓存）
* 加密凭证作为密文 blob 存在同一状态库，可登录不同 Microsoft 账号
* ``browser_profile/``（登录窗口的 Playwright profile）
* ``debug.log``（唯一运行日志）/ 自动化快照目录
* 单实例 IPC key、Windows AppUserModelID、开机自启项

「新建并打开」不会在当前进程里切换环境（那样会污染已经打开的数据库、
凭证和托盘状态），而是**启动一个带 ``--instance <id>`` 的全新进程**。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime
from typing import Dict, List

from . import autostart
from .config import (
    DEFAULT_INSTANCE, INSTANCE_META, autostart_name, instance_home,
    instance_id, instance_meta_path, instances_root,
    normalize_instance_id, preferred_default_id, set_preferred_default,
    single_instance_key, window_title,
)

MAX_INSTANCES = 12


def _read_meta(ident: str) -> dict:
    try:
        obj = json.loads(instance_meta_path(ident).read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _entry(ident: str) -> Dict[str, object]:
    meta = _read_meta(ident) if ident != DEFAULT_INSTANCE else {}
    label = ("默认环境" if ident == DEFAULT_INSTANCE
             else str(meta.get("label") or ident)[:24])
    builtin = ident == DEFAULT_INSTANCE
    preferred = preferred_default_id() == ident
    current = ident == instance_id()
    return {
        "id": ident,
        "label": label,
        "home": instance_home(ident),
        "created": str(meta.get("created") or ""),
        # is_default 是内置目录的事实，界面上的「默认」徽章用 is_preferred_default
        "is_default": builtin,
        "is_builtin_default": builtin,
        "is_preferred_default": preferred,
        "current": current,
        # 只能删当前环境；内置根目录与启动默认环境都不可删
        "can_delete": bool(current and not builtin and not preferred),
        "can_set_default": bool(not preferred),
        "title": window_title() if current else label,
        "ipc_key": single_instance_key(ident),
        "autostart_name": autostart_name() if current else
                          ("MSRewardsAssistant" if builtin
                           else f"MSRewardsAssistant_{ident}"),
    }


def list_instances() -> List[Dict[str, object]]:
    """启动默认环境排第一，其余按创建顺序（id 数字）排列。

    内置 ``default`` 仍列在表里：它是所有实例的根目录，永远存在、永远不可删。
    """
    out = [_entry(DEFAULT_INSTANCE)]
    root = instances_root()
    try:
        dirs = sorted((d for d in root.iterdir() if d.is_dir()),
                      key=lambda d: (len(d.name), d.name))
    except OSError:
        dirs = []
    for d in dirs:
        ident = normalize_instance_id(d.name)
        if ident == DEFAULT_INSTANCE or ident != d.name:
            continue                      # 跳过非法目录名 / instances 自身
        if not (d / INSTANCE_META).is_file():
            continue                      # 不是由本应用创建的环境
        out.append(_entry(ident))
    # 稳定排序：只把启动默认环境提到最前，不打乱其余创建顺序。
    out.sort(key=lambda e: 0 if e["is_preferred_default"] else 1)
    return out


def next_instance_label(lang: str = "zh") -> str:
    existing = {str(e["id"]) for e in list_instances()}
    n = 2
    while f"acct-{n}" in existing:
        n += 1
    return (f"环境 {n}" if lang != "en" else f"Account {n}"), f"acct-{n}"


def create_instance(label: str = "", lang: str = "zh") -> Dict[str, object]:
    """创建一个全新独立环境（只建目录与元数据，不启动进程）。"""
    if len(list_instances()) >= MAX_INSTANCES:
        raise RuntimeError(f"最多只能创建 {MAX_INSTANCES} 个独立环境")
    auto_label, ident = next_instance_label(lang)
    display = str(label or "").strip()[:24] or auto_label
    home = instance_home(ident)
    meta = {
        "id": ident,
        "label": display,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    (home / INSTANCE_META).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return _entry(ident)


def launch_command(ident: str) -> List[str]:
    """启动另一个实例进程的命令行（frozen EXE / 源码两种形态都支持）。"""
    name = normalize_instance_id(ident)
    if getattr(sys, "frozen", False):
        return [sys.executable, "--instance", name]
    return [sys.executable, "-m", "rewards_assistant", "--instance", name]


def launch_instance(ident: str, minimized: bool = False) -> bool:
    """启动独立进程；失败返回 False，由界面提示，绝不抛异常打断当前实例。"""
    cmd = launch_command(ident)
    if minimized:
        cmd.append("--minimized")
    # 多开进程必须脱离当前进程：新实例有自己的 QApplication、托盘、数据库。
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL, close_fds=True)
    if sys.platform == "win32":
        kwargs["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP |
                                   subprocess.DETACHED_PROCESS)
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(cmd, **kwargs)
        return True
    except OSError:
        return False


def create_and_launch(lang: str = "zh") -> Dict[str, object]:
    """界面「新建并打开」一键动作：建环境 → 起独立进程。"""
    entry = create_instance(lang=lang)
    entry["launched"] = launch_instance(str(entry["id"]))
    return entry


def set_default_instance(ident: str) -> Dict[str, object]:
    """把某个环境设为启动默认（不带 --instance 时进入）；指针不改动任何数据。"""
    name = set_preferred_default(ident)
    return _entry(name)


def delete_instance(ident: str) -> Dict[str, object]:
    """删除**当前**环境的数据目录；内置根目录与默认环境一律拒绝。

    只删 ``<base>/instances/<id>/``，绝不碰根目录（那里还放着其它环境和
    默认指针）。删除前顺手关掉该环境的开机自启项，避免留下指向空目录的
    启动项；当前进程随后会退出，防止 SQLite / 日志把目录又建回来。
    """
    name = normalize_instance_id(ident)
    if name == DEFAULT_INSTANCE:
        raise ValueError("内置默认环境不能删除（它也是其它环境的根目录）")
    if name != instance_id():
        raise ValueError("只能删除当前窗口正在使用的环境")
    if name == preferred_default_id():
        raise ValueError("默认环境不能删除，请先把其它环境设为默认")
    home = instance_home(name)
    root = instances_root()
    if home.parent != root or not (home / INSTANCE_META).is_file():
        raise ValueError("环境目录无效，未删除任何文件")
    try:
        if autostart.is_enabled():
            autostart.set_enabled(False)
    except Exception:
        pass                              # 自启动项清理失败不阻断删除
    shutil.rmtree(home)
    if preferred_default_id() == name:
        set_preferred_default(DEFAULT_INSTANCE)
    return {"id": name, "home": home, "deleted": True}

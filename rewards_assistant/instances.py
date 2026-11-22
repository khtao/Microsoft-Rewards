# -*- coding: utf-8 -*-
"""多开环境管理：列出、创建、启动彼此独立的实例进程。

一个进程只绑定一个实例（见 ``config.instance_id()``）。每个实例有独立的：

* ``settings.json`` / ``rewards.db``（积分历史、任务、定时计划）
* ``session_cookies.bin``（本机加密凭证，可登录不同 Microsoft 账号）
* ``browser_profile/``（登录窗口的 Playwright profile）
* ``run.log`` / ``search_actions.jsonl`` / 自动化快照目录
* 单实例 IPC key、Windows AppUserModelID、开机自启项

「新建并打开」不会在当前进程里切换环境（那样会污染已经打开的数据库、
凭证和托盘状态），而是**启动一个带 ``--instance <id>`` 的全新进程**。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from typing import Dict, List

from .config import (
    DEFAULT_INSTANCE, INSTANCE_META, autostart_name, instance_home,
    instance_id, instance_meta_path, instances_root,
    normalize_instance_id, single_instance_key, window_title,
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
    return {
        "id": ident,
        "label": label,
        "home": instance_home(ident),
        "created": str(meta.get("created") or ""),
        "is_default": ident == DEFAULT_INSTANCE,
        "current": ident == instance_id(),
        "title": window_title() if ident == instance_id() else label,
        "ipc_key": single_instance_key(ident),
        "autostart_name": autostart_name() if ident == instance_id() else
                          ("MSRewardsAssistant" if ident == DEFAULT_INSTANCE
                           else f"MSRewardsAssistant_{ident}"),
    }


def list_instances() -> List[Dict[str, object]]:
    """默认环境永远排第一，其余按创建顺序（id 数字）排列。"""
    out = [_entry(DEFAULT_INSTANCE)]
    root = instances_root()
    try:
        dirs = sorted((d for d in root.iterdir() if d.is_dir()),
                      key=lambda d: (len(d.name), d.name))
    except OSError:
        return out
    for d in dirs:
        ident = normalize_instance_id(d.name)
        if ident == DEFAULT_INSTANCE or ident != d.name:
            continue                      # 跳过非法目录名 / instances 自身
        if not (d / INSTANCE_META).exists():
            continue                      # 不是由本应用创建的环境
        out.append(_entry(ident))
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

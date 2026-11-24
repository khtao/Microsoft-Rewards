# -*- coding: utf-8 -*-
"""分级运行日志：``run.log`` 保持日常操作级（INFO），另写 ``debug.log``。

为什么要分两层
--------------
`ModernMainWindow.log()` 写进 ``run.log`` 的是给用户看的「日常操作」流水：
同步完成、任务开始、设置变更……一行一句，导出时分享也放心。但排查浏览器
启动失败、同步异常、领取窗口关不掉这类问题时，这些行不够：没有异常类型、
没有堆栈、没有当时的上下文。

本模块补上 DEBUG / WARN / ERROR 三个级别：

* ``debug(source, msg, **fields)``：细粒度过程记录（浏览器启动、Cookie 注入、
  活动卡片命中、搜索轮次、领取窗口事件……），只进 ``debug.log``；
* ``warn`` / ``error(source, msg, exc=..., **fields)``：带异常类型、完整堆栈
  （含 traceback 与 cause 链）和键值上下文；ERROR 额外在 ``run.log`` 留一行
  摘要，保证用户侧也能看到「出过错」。

所有内容都经 ``session.redact()`` 脱敏后再落盘：本机路径、Cookie、令牌不会
因为「更细致的日志」被写进去。``debug.log`` 与 ``run.log`` 一样按实例隔离，
``export_logs`` 会把两者一起打进带时间戳的 zip。

开关：设置项 ``debug_log_enabled``（默认 True），或环境变量
``MS_REWARDS_DEBUG=0`` 临时关闭 DEBUG 细目；ERROR 详细记录始终写入。
"""
from __future__ import annotations

import os
import re
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .config import data_dir, log_path

DEBUG_LOG_NAME = "debug.log"
_ENABLED: Optional[bool] = None

LEVELS = ("DEBUG", "INFO", "WARN", "ERROR")


def debug_log_path() -> Path:
    """DEBUG 细目文件；与 run.log 同目录，多开时各写各的。"""
    return data_dir() / DEBUG_LOG_NAME


def debug_enabled() -> bool:
    """DEBUG 细目开关（设置项 + 环境变量覆盖，进程内缓存一次）。"""
    global _ENABLED
    env = os.environ.get("MS_REWARDS_DEBUG", "").strip().lower()
    if env in ("0", "false", "off", "no"):
        return False
    if env in ("1", "true", "on", "yes"):
        return True
    if _ENABLED is None:
        try:
            from .config import Settings
            _ENABLED = bool(Settings().get("debug_log_enabled", True))
        except Exception:
            _ENABLED = True
    return bool(_ENABLED)


def _redact(text: Any) -> str:
    """凭证 / 邮箱（session.redact）+ 本机绝对路径，绝不写进日志。

    ``session.redact`` 只处理 Cookie / Bearer / token / 邮箱；Playwright 的
    异常堆栈里经常带 ``C:\\Users\\...`` 或 ``/home/...`` 形式的 user-data-dir，
    这里按 ``browser_error_hint`` 的同一套规则再抹一层。
    """
    try:
        from .session import redact
        t = redact(str(text))
    except Exception:
        t = str(text)
    t = re.sub(r"[A-Za-z]:[\\/][^\s'\"]+", "[本机路径已省略]", t)
    t = re.sub(r"/(?:home|Users|tmp|var)/[^\s'\"]+", "[本机路径已省略]", t)
    return t


def _exception_lines(exc: BaseException) -> list[str]:
    """异常类型 + 完整堆栈 + cause 链，**逐行**脱敏后写进 debug.log。"""
    lines = [f"exception={type(exc).__name__}: {_redact(exc)}"]
    try:
        tb = "".join(traceback.format_exception(
            type(exc), exc, exc.__traceback__))
        lines += [_redact(ln) for ln in tb.strip().splitlines()]
    except Exception as err:
        lines.append(f"traceback unavailable: {type(err).__name__}")
    seen = {id(exc)}
    cur = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        lines.append(f"caused by {type(cur).__name__}: {_redact(cur)}")
        try:
            lines += [_redact(ln) for ln in "".join(traceback.format_exception(
                type(cur), cur, cur.__traceback__)).strip().splitlines()]
        except Exception:
            pass
        cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
    return lines


def format_record(level: str, source: str, message: str, *,
                  exc: Optional[BaseException] = None,
                  fields: Optional[dict] = None, **extra: Any) -> str:
    """格式化一条记录；``fields=`` 字典与 ``**extra`` 都会写成 key=value。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    line = f"[{ts}] [{level}] [{_redact(source)}] {_redact(message)}"
    merged = dict(fields or {})
    merged.update(extra)
    for key, value in sorted(merged.items()):
        line += f" | {_redact(key)}={_redact(value)}"
    if exc is not None:
        for extra in _exception_lines(exc):
            line += "\n    " + extra
    return line


def _append(path: Path, lines: list[str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass                      # 日志失败绝不影响业务


def write(level: str, source: str, message: str, *,
          exc: Optional[BaseException] = None,
          run_log_summary: bool = False, **fields: Any) -> None:
    """写一条记录：DEBUG/INFO 只进 debug.log；ERROR 始终进 debug.log。

    ``run_log_summary=True`` 时额外在 run.log 追加一行不带堆栈的摘要。
    """
    level = level.upper()
    if level not in LEVELS:
        level = "DEBUG"
    if level == "DEBUG" and not debug_enabled():
        return
    record = format_record(level, source, message, exc=exc, fields=fields)
    _append(debug_log_path(), [record])
    if run_log_summary or level == "ERROR":
        ts = datetime.now().strftime("%H:%M:%S")
        summary = f"[{ts}] [{level}] {_redact(message)}"
        if exc is not None:
            summary += f" ({type(exc).__name__})"
        _append(log_path(), [summary])


def debug(source: str, message: str, **fields: Any) -> None:
    write("DEBUG", source, message, **fields)


def info(source: str, message: str, **fields: Any) -> None:
    write("INFO", source, message, **fields)


def warn(source: str, message: str, *,
         exc: Optional[BaseException] = None, **fields: Any) -> None:
    write("WARN", source, message, exc=exc, **fields)


def error(source: str, message: str, *,
          exc: Optional[BaseException] = None, **fields: Any) -> None:
    """错误：debug.log 记完整堆栈，run.log 只留一行摘要。"""
    write("ERROR", source, message, exc=exc, **fields)

# -*- coding: utf-8 -*-
"""唯一运行日志 ``debug.log``：一份文件、四个级别，按实例隔离。

同一条记录**只落一次**：界面流水是 INFO，浏览器启动 / Cookie 注入 / 活动卡
命中 / 搜索轮次 / 领取窗口事件是 DEBUG，``warn`` / ``error`` 额外带异常类型、
完整堆栈（含 traceback 与 cause 链）和键值上下文。所有内容都经
``session.redact()`` 脱敏后再落盘：本机路径、Cookie、令牌不会因为「更细致的
日志」被写进去。

原先的 ``run.log``（日常流水）、``search_actions.jsonl``（结构化行为日志）不再
各占一个磁盘文件：后者以 ``ACTION `` 前缀行并进同一文件，前者由
:func:`run_log_view` 在导出 zip 时按级别过滤生成，:func:`action_log_view` 还原
行为日志视图。磁盘上始终只有一份日志，不存储重复信息。

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

from .config import data_dir

DEBUG_LOG_NAME = "debug.log"
_ENABLED: Optional[bool] = None

LEVELS = ("DEBUG", "INFO", "WARN", "ERROR")


def debug_log_path() -> Path:
    """唯一运行日志；多开时每个实例一份。"""
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


# 记录形如：[2026-11-24 15:30:12.123] [INFO] [ui] 消息 | k=v
_LINE_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d{3}\] "
    r"\[(DEBUG|INFO|WARN|ERROR)\] \[[^\]]*\] (.*)$")


def run_log_view(text: str) -> str:
    """从 debug.log 生成「日常操作级」视图，供导出 zip 的 ``run.log`` 条目。

    磁盘上只有一份日志；这里只在导出时按级别过滤、去掉 DEBUG 与堆栈续行，
    不产生第二份落盘文件，也不重复存储。
    """
    out: list[str] = []
    for line in text.splitlines():
        m = _LINE_RE.match(line)
        if not m or m.group(2) == "DEBUG":
            continue                            # DEBUG 细目与堆栈续行都不进摘要
        ts, level, body = m.group(1)[11:], m.group(2), m.group(3)
        msg = body.split(" | ", 1)[0]
        out.append(f"[{ts}] [{level}] {msg}")
    return "\n".join(out) + ("\n" if out else "")


def action_log_view(text: str) -> str:
    """从 debug.log 的 ``ACTION `` 前缀行还原结构化 JSONL 视图。"""
    out = [line[len("ACTION "):] for line in text.splitlines()
           if line.startswith("ACTION ")]
    return "\n".join(out) + ("\n" if out else "")


def write(level: str, source: str, message: str, *,
          exc: Optional[BaseException] = None,
          run_log_summary: bool = False, **fields: Any) -> None:
    """写一条记录进**唯一**的 debug.log；一条只落一次，不再双写 run.log。

    ``run_log_summary`` 保留参数兼容旧调用点：导出的 ``run.log`` 视图由
    :func:`run_log_view()` 从本文件按级别过滤生成，磁盘上不存在第二份。
    """
    level = level.upper()
    if level not in LEVELS:
        level = "DEBUG"
    if level == "DEBUG" and not debug_enabled():
        return
    record = format_record(level, source, message, exc=exc, fields=fields)
    _append(debug_log_path(), [record])


def debug(source: str, message: str, **fields: Any) -> None:
    write("DEBUG", source, message, **fields)


def info(source: str, message: str, **fields: Any) -> None:
    write("INFO", source, message, **fields)


def warn(source: str, message: str, *,
         exc: Optional[BaseException] = None, **fields: Any) -> None:
    write("WARN", source, message, exc=exc, **fields)


def error(source: str, message: str, *,
          exc: Optional[BaseException] = None, **fields: Any) -> None:
    """错误：debug.log 记完整堆栈；导出的摘要视图由级别过滤生成。"""
    write("ERROR", source, message, exc=exc, **fields)

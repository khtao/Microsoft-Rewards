"""唯一桌面应用入口：python -m rewards_assistant。

本版本只有一种界面 —— ``--widgets`` 所指向的单列复刻界面。
``python -m rewards_assistant`` 与 ``python -m rewards_assistant --widgets``
完全等价：都走 ``app.Application`` 控制器 + ``ui.modern.ModernMainWindow``。
``--classic`` 不再启动独立窗口，只作为兼容别名映射到同一模式；
托盘「自动完成未完成任务」与主页主按钮调用同一份 ``run_pending_tasks()``。

**多开**：``--instance <id>``（或环境变量 ``MS_REWARDS_INSTANCE``）为当前
进程选择一个独立环境；不传就是默认环境，沿用原来的数据目录。每个环境有
自己的设置、积分库、加密凭证、浏览器 profile 与日志，可登录不同账号。
亦作为打包入口，从任何工作目录都能找到包。
"""
from __future__ import annotations

import os
import sys

if not __package__:
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv)

    # --instance 必须在导入数据层之前落到环境变量：config / credentials /
    # api 里都有模块级路径求值，进程启动后实例就不再变化。
    rest, instance = [], None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--instance" and i + 1 < len(argv):
            instance = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--instance="):
            instance = arg.split("=", 1)[1]
            i += 1
            continue
        rest.append(arg)
        i += 1
    # 无论是否显式传 --instance，都在导入数据层之前把实例 id 固定进环境
    # 变量：不传时读取「启动默认环境」指针。这样进程运行期间在设置页把别的
    # 环境设为默认，只会影响下一次启动，不会让当前进程的数据目录 / 凭证 /
    # IPC key 突然漂移到另一个环境。
    from rewards_assistant.config import (instance_home,
                                         is_default_instance,
                                         preferred_default_id,
                                         set_instance_id)

    set_instance_id(instance if instance is not None else preferred_default_id())
    # Linux：多个实例若共用 HOME/.cache/fontconfig，第一次同时冷启动会
    # 争抢同一个缓存目录，表现为两个窗口都迟迟不出现。给新实例单独一份
    # XDG 缓存，环境隔离也更彻底；默认实例保持原路径，零迁移。
    if sys.platform.startswith("linux") and not is_default_instance():
        cache = instance_home() / "cache"
        try:
            cache.mkdir(parents=True, exist_ok=True)
            os.environ["XDG_CACHE_HOME"] = str(cache)
        except OSError:
            pass

    # Qt 不需要看到这两个参数，避免把环境 id 当成 Qt 命令行选项。
    argv = [a for a in rest if a not in ("--widgets", "--classic")]

    # 统一状态库：启动先做完整性检查（损坏则删除重建、回默认设置），再把
    # 旧版散落的 settings/credentials/last_sync/两份日志/两个 ~/.cache 收进来。
    # 必须在导入 app 之前完成，因为 Application 构造时就会 Settings() / Store()。
    from rewards_assistant import state
    state.ensure()
    state.migrate_legacy()

    from rewards_assistant.app import main as app_main

    return app_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())

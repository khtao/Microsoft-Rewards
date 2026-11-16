"""唯一桌面应用入口：python -m rewards_assistant。

本版本只有一种界面 —— ``--widgets`` 所指向的单列复刻界面。
``python -m rewards_assistant`` 与 ``python -m rewards_assistant --widgets``
完全等价：都走 ``app.Application`` 控制器 + ``ui.modern.ModernMainWindow``。
``--classic`` 不再启动独立窗口，只作为兼容别名映射到同一模式；
托盘「自动完成未完成任务」与主页主按钮调用同一份 ``run_pending_tasks()``。
亦作为打包入口，从任何工作目录都能找到包。
"""
from __future__ import annotations

import sys

if not __package__:
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv=None) -> int:
    argv = [a for a in list(argv if argv is not None else sys.argv)
            if a not in ("--widgets", "--classic")]
    from rewards_assistant.app import main as app_main

    return app_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())

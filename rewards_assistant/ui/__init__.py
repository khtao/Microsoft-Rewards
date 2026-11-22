# -*- coding: utf-8 -*-
"""界面层：唯一窗口在 ``ui/modern``；``ui/legacy`` 只保留经典执行内核。

这里刻意不导入任何子模块，避免 ``ui.modern.main_window`` 反向引用
``ui.legacy`` 时形成循环导入。
"""

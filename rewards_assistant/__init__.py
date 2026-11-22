"""Microsoft Rewards 桌面助手（社区独立、非官方项目）。

分层：

* ``session`` / ``auth`` / ``readonly_report`` / ``rewards_client`` / ``storage``
  —— 只读解析与本地数据，不依赖 Qt；
* ``sync`` / ``notifier`` / ``autostart`` —— 桌面集成；
* ``ui`` / ``app`` —— 唯一 Qt Widgets 界面、托盘与控制器。
"""
from .config import APP_TITLE, APP_VERSION

__all__ = ["APP_TITLE", "APP_VERSION"]
__version__ = APP_VERSION

"""Microsoft Rewards 每日助手 —— 只读统计 + 手动打卡的桌面小工具。

分层说明
--------
config / auth / rewards_client / storage : 数据与网络层（不依赖 Qt）
notifier / scheduler / autostart / sync  : 桌面集成层（不依赖窗口）
ui / app                                 : 界面与托盘入口
"""
from .config import APP_TITLE, APP_VERSION

__all__ = ["APP_TITLE", "APP_VERSION"]
__version__ = APP_VERSION

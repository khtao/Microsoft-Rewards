# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（路径全部相对 spec 所在目录，便于在任何机器上构建）。

界面只有一种：Qt Widgets 的 ui/modern 单列窗口。
不需要 QWebEngineView / HTML 资源 / desktop-bridge.js。
经典自动完成链路（ActivityWorker / SearchRunner / api.run_human_search）
保持在包内，由 app.Application 驱动，主按钮与托盘共用 run_pending_tasks()。
"""
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(globals().get('SPECPATH', os.getcwd())).resolve()

datas = []
binaries = []
hiddenimports = []

# Windows 资源管理器 / 任务栏显示的是 EXE 内嵌图标，不是运行时的
# QApplication.setWindowIcon()。不传 icon= 时 PyInstaller 会用默认空白图标，
# 这正是「只有运行时窗口有图标、exe 本身没图标」的原因。Linux 忽略此参数。
APP_ICO = ROOT / 'assets' / 'icons' / 'app.ico'
EXE_ICON = str(APP_ICO) if (os.name == 'nt' and APP_ICO.is_file()) else None

icons = ROOT / 'assets' / 'icons'
if icons.is_dir():
    datas.append((str(icons), 'assets/icons'))

# 可选字体目录：存在则随包携带，不存在就直接用系统中文字体。
# 不打包 search_actions.jsonl —— 那是 ActionLogger 写到用户数据目录的运行时
# 日志（api.DEFAULT_LOG = data_dir()/'search_actions.jsonl'），包内无人读取；
# 仓库里那份历史样本还含真实 IP / 会话 ID，打包进 .deb 会泄露个人信息。
fonts = ROOT / 'fonts'
if fonts.is_dir():
    datas.append((str(fonts), 'fonts'))

# Playwright 的驱动（node + driver）必须整体携带。
# PySide6 交给 PyInstaller 自带的 hook-PySide6.QtXxx：它按实际 import 图
# 收集 Qt 共享库与 plugins/platforms（xcb 等），只带 QtWidgets / QtGui /
# QtCore / QtSvg / QtNetwork。不要用 collect_all('PySide6') —— 元包会连带
# 安装 QtWebEngine / Qt3D / QtMultimedia 等扩展（约 400MB），全量收集会让
# .deb 无谓膨胀，且与本版本「不需要 QtWebEngine」的设计冲突。
d, b, h = collect_all('playwright')
datas += d
binaries += b
hiddenimports += h

# --- shiboken6 动态库：显式平铺，绝不依赖 PyInstaller hook 的隐式摆放 ---
# collect_dynamic_libs('shiboken6') 在 PyInstaller 6.x 返回空；某些构建环境
# 里 hook-PySide6 只把 libshiboken6.abi3.so.6.9 放进 _internal/shiboken6/
# 子目录，甚至只在 _internal 顶层留一个 symlink。而 Shiboken.abi3.so 的
# DT_NEEDED 由动态加载器按 LD_LIBRARY_PATH（PyInstaller onedir 设成
# _internal）搜索 —— 顶层没有真实文件时就会报
#   libshiboken6.abi3.so.6.9: cannot open shared object file
# 这个行为随 PyInstaller / shiboken6 版本、venv 布局、文件系统是否保留
# symlink 而变化，是构建可移植性缺陷。这里把**真实文件**（非 symlink）
# 显式放到 _internal 顶层，Windows 的 shiboken6*.dll 同样处理。
SHIBOKEN_EXPLICIT = []
try:
    import shiboken6 as _sb
    _sb_dir = Path(_sb.__file__).resolve().parent
    _patterns = ('libshiboken6*.so*', 'libshiboken6*.dylib',
                 'shiboken6*.dll', 'libshiboken6*.dll')
    _seen = set()
    for _pat in _patterns:
        for _lib in sorted(_sb_dir.glob(_pat)):
            _real = _lib.resolve() if _lib.is_symlink() else _lib
            _key = (_real.name, _real)
            if _real.is_file() and _key not in _seen:
                _seen.add(_key)
                SHIBOKEN_EXPLICIT.append((str(_real), '.'))
except Exception:
    pass
binaries += SHIBOKEN_EXPLICIT
hiddenimports += ['shiboken6']
hiddenimports += [
    # 同步层用 async_api 并发抓两页，sync_api 作为无异步环境兜底；
    # 显式列出，避免打包后因动态导入缺失而退化成串行。
    'playwright.async_api', 'playwright.sync_api',
    'PySide6.QtWidgets', 'PySide6.QtGui', 'PySide6.QtCore',
    'PySide6.QtSvg', 'PySide6.QtNetwork',
    'rewards_assistant.ui.modern.main_window',
    'rewards_assistant.ui.modern.effects',
    'rewards_assistant.ui.legacy',
]

a = Analysis(
    ['rewards_assistant/__main__.py'],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MSRewardsAssistant',
    icon=EXE_ICON,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MSRewardsAssistant',
)

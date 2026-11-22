# -*- coding: utf-8 -*-
"""构建后归一化：保证 ``_internal`` 顶层的 shiboken6 动态库是**真实文件**。

PyInstaller 6.x 会把顶层 ``libshiboken6.abi3.so.6.9`` 做成指向
``shiboken6/`` 子目录的 symlink（"symlink suppression"）。本机能解析，但
换机器 / 解包 / 某些文件系统不保留 symlink / Windows 上 DLL 名不同，就会
报::

    libshiboken6.abi3.so.6.9: cannot open shared object file
    ImportError: ... shiboken6/__init__.py line 27

因此在 PyInstaller 之后、打包之前，把顶层库**复制**成真实文件；找不到时
从子目录或构建环境的 shiboken6 安装包补齐。Linux / macOS / Windows 通用。

    python tools/normalize_bundle.py dist/MSRewardsAssistant
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

PATTERNS = (
    "libshiboken6*.so*", "libshiboken6*.dylib",
    "shiboken6*.dll", "libshiboken6*.dll",
)


def _candidates(directory: Path) -> list[Path]:
    out: list[Path] = []
    for pattern in PATTERNS:
        out.extend(sorted(directory.glob(pattern)))
    return out


def _source_for(internal: Path, name: str) -> Path | None:
    """按优先级找一份真实的库文件。"""
    # 1) 子目录里 PyInstaller 收集的那份（最常见）
    for sub in ("shiboken6", "PySide6", "."):
        for cand in _candidates(internal / sub):
            if cand.is_file() and cand.name == name and not cand.is_symlink():
                return cand
    # 2) 构建环境里安装的 shiboken6
    try:
        import shiboken6
        d = Path(shiboken6.__file__).resolve().parent
        for cand in _candidates(d):
            if cand.is_file():
                return cand
    except Exception:
        pass
    return None


def normalize(bundle_dir: Path) -> list[str]:
    internal = bundle_dir / "_internal"
    if not internal.is_dir():
        raise SystemExit(f"[错误] 找不到 {internal}，请确认 PyInstaller 已构建完成")

    # 期望出现在顶层的库名：优先用现有条目（含 symlink），否则从子目录推断
    top = _candidates(internal)
    if not top:
        inner = _candidates(internal / "shiboken6")
        if not inner:
            raise SystemExit("[错误] 包内完全没有 shiboken6 动态库，拒绝交付坏包")
        names = [c.name for c in inner]
    else:
        names = [c.name for c in top]

    done: list[str] = []
    for name in dict.fromkeys(names):
        dst = internal / name
        real = None
        if dst.is_symlink():
            try:
                target = dst.resolve()
                if target.is_file():
                    real = target
            except OSError:
                real = None
        elif dst.is_file():
            real = dst
        if real is None:
            real = _source_for(internal, name)
        if real is None or not real.is_file():
            raise SystemExit(f"[错误] 无法为 {name} 找到真实的 shiboken6 库文件")
        if dst.is_symlink() or not dst.exists():
            if dst.is_symlink() or dst.exists():
                dst.unlink()
            shutil.copy2(real, dst)
            done.append(f"{name} ← {real.name}（复制为真实文件）")
        elif dst.resolve() == dst:
            done.append(f"{name}（已是真实文件）")
        else:
            dst.unlink()
            shutil.copy2(real, dst)
            done.append(f"{name} ← {real.name}（复制为真实文件）")

    # 终检：顶层必须存在真实普通文件
    real_top = [c for c in _candidates(internal) if c.is_file() and not c.is_symlink()]
    if not real_top:
        raise SystemExit("[错误] 归一化后顶层仍没有真实 shiboken6 库")
    return done


def main() -> int:
    if len(sys.argv) != 2:
        print("用法: python tools/normalize_bundle.py <dist/MSRewardsAssistant>")
        return 2
    bundle = Path(sys.argv[1]).resolve()
    for line in normalize(bundle):
        print("[normalize]", line)
    print(f"[normalize] OK: {bundle / '_internal'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

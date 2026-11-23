# -*- coding: utf-8 -*-
"""回归：打包二进制不得依赖 PyInstaller hook 隐式摆放 shiboken6 动态库。

真机报错：

    libshiboken6.abi3.so.6.9: cannot open shared object file
    shiboken6/__init__.py line 27: from shiboken6.Shiboken import *

根因是 ``collect_dynamic_libs('shiboken6')`` 在 PyInstaller 6.x 返回空，
顶层那份库由 hook-PySide6 顺手创建（且常是 symlink），行为随版本 / venv
布局 / 文件系统是否保留 symlink 变化。修法：spec 显式把**真实文件**平铺到
``_internal`` 顶层，deb 启动器补 LD_LIBRARY_PATH，构建脚本加防呆校验。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_spec_explicitly_flat_collects_shiboken6_real_files():
    src = _read("MSRewardsAssistant.spec")
    assert "SHIBOKEN_EXPLICIT" in src
    assert "import shiboken6 as _sb" in src
    assert "binaries += SHIBOKEN_EXPLICIT" in src
    # 必须是真实文件（resolve 掉 symlink），且平铺到 _internal 顶层
    assert "_lib.resolve() if _lib.is_symlink()" in src
    assert "SHIBOKEN_EXPLICIT.append((str(_real), '.'))" in src
    # Linux / macOS / Windows 三种库名都要覆盖
    for pattern in ("libshiboken6*.so*", "libshiboken6*.dylib",
                    "shiboken6*.dll", "libshiboken6*.dll"):
        assert pattern in src, pattern
    assert "hiddenimports += ['shiboken6']" in src
    # 不能退化成 collect_all('PySide6')（会把 QtWebEngine 全量带进来）。
    # spec 注释里会提到这个反例，故只检查真正的赋值语句。
    assert not re.search(r"^\s*\w+\s*=\s*collect_all\(['\"]PySide6['\"]\)",
                         src, re.M)


def test_spec_comment_documents_why_not_rely_on_hook():
    src = _read("MSRewardsAssistant.spec")
    assert "collect_dynamic_libs('shiboken6')" in src
    assert "cannot open shared object file" in src
    assert "DT_NEEDED" in src or "LD_LIBRARY_PATH" in src


def test_ubuntu_build_guards_against_missing_real_library():
    src = _read("build_ubuntu.sh")
    assert "SHIBOKEN_LIB" in src
    assert '[[ -f "$f" && ! -L "$f" ]]' in src, "必须拒绝悬空 symlink"
    assert "cannot open shared object file" in src
    assert "exit 1" in src
    assert "ldd" in src and "not found" in src


def test_deb_launcher_adds_internal_to_library_path():
    src = _read("build_deb.sh")
    mark = "cat > \"$PKG/usr/bin/ms-rewards-assistant\" <<'SHLAUNCH'"
    launcher = src.split(mark, 1)[1].split("\nSHLAUNCH", 1)[0]
    assert "LD_LIBRARY_PATH" in launcher
    assert '"$INTERNAL:$INTERNAL/shiboken6' in launcher
    assert "exec /opt/ms-rewards-assistant/MSRewardsAssistant" in launcher
    # 打包前同样防呆
    assert "SB_OK" in src
    assert "拒绝打出坏包" in src


def test_windows_build_guards_shiboken_dll():
    src = _read("build_windows.bat")
    assert "SB_OK" in src
    assert "shiboken6*.dll" in src
    assert "SHIBOKEN_EXPLICIT" in src
    assert "goto :fail" in src


def test_architecture_documents_the_failure_and_fix():
    src = _read("docs/ARCHITECTURE.md")
    assert "libshiboken6" in src or "shiboken6" in src


def test_runtime_bundle_has_real_flat_lib_when_built():
    """构建产物存在时做真校验；源码包（无 dist）时跳过。"""
    internal = ROOT / "dist" / "MSRewardsAssistant" / "_internal"
    if not internal.is_dir():
        return
    libs = [f for f in list(internal.glob("libshiboken6*.so*")) +
            list(internal.glob("shiboken6*.dll"))
            if f.is_file() and not f.is_symlink()]
    assert libs, f"_internal 顶层缺少真实 shiboken6 库: {internal}"
    shib = internal / "shiboken6" / "Shiboken.abi3.so"
    if shib.is_file() and shutil_which("ldd"):
        import subprocess
        out = subprocess.run(["ldd", str(shib)], capture_output=True,
                             text=True).stdout
        assert "not found" not in out, out


def shutil_which(name: str) -> bool:
    import shutil
    return shutil.which(name) is not None


# ---------------------------------------------------------------- 归一化工具

def test_normalize_bundle_replaces_symlink_with_real_file(tmp_path):
    from tools.normalize_bundle import normalize
    internal = tmp_path / "_internal"
    (internal / "shiboken6").mkdir(parents=True)
    real = internal / "shiboken6" / "libshiboken6.abi3.so.6.9"
    real.write_bytes(b"ELF-SHIBOKEN")
    link = internal / "libshiboken6.abi3.so.6.9"
    link.symlink_to("shiboken6/libshiboken6.abi3.so.6.9")
    assert link.is_symlink()

    lines = normalize(tmp_path)
    assert not link.is_symlink() and link.is_file()
    assert link.read_bytes() == b"ELF-SHIBOKEN"
    assert any("copied as real file" in x for x in lines)


def test_normalize_bundle_recovers_from_dangling_symlink(tmp_path, monkeypatch):
    from tools.normalize_bundle import normalize
    internal = tmp_path / "_internal"
    (internal / "shiboken6").mkdir(parents=True)
    real = internal / "shiboken6" / "libshiboken6.abi3.so.6.9"
    real.write_bytes(b"REAL")
    link = internal / "libshiboken6.abi3.so.6.9"
    link.symlink_to("shiboken6/不存在.so")
    normalize(tmp_path)
    assert link.is_file() and not link.is_symlink()
    assert link.read_bytes() == b"REAL"


def test_normalize_bundle_creates_top_level_when_missing(tmp_path):
    from tools.normalize_bundle import normalize
    internal = tmp_path / "_internal"
    (internal / "shiboken6").mkdir(parents=True)
    (internal / "shiboken6" / "libshiboken6.abi3.so.6.9").write_bytes(b"REAL")
    normalize(tmp_path)
    top = internal / "libshiboken6.abi3.so.6.9"
    assert top.is_file() and not top.is_symlink()


def test_normalize_bundle_rejects_bundle_without_any_lib(tmp_path):
    import pytest
    from tools.normalize_bundle import normalize
    (tmp_path / "_internal").mkdir()
    with pytest.raises(SystemExit):
        normalize(tmp_path)


def test_build_scripts_call_normalizer():
    assert "normalize_bundle.py" in _read("build_ubuntu.sh")
    assert "normalize_bundle.py" in _read("build_deb.sh")
    assert "normalize_bundle.py" in _read("build_windows.bat")


def test_normalizer_is_packaged_and_syntax_ok():
    src = _read("tools/normalize_bundle.py")
    compile(src, "normalize_bundle.py", "exec")
    assert "_internal" in src and "is_symlink" in src

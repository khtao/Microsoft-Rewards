# -*- coding: utf-8 -*-
"""回归：构建只用系统 Python，不创建 / 不复用虚拟环境。

用户明确要求「编译不使用虚拟环境进行配置，使用系统环境」。旧脚本会在
.venv-build-* 里另装一套 requests / anyio，PyInstaller 分析时与系统
site-packages 混用，是 ``'lazy_import' object is not callable`` 这类导入层
异常的温床。同时保留已修好的 spec 收集、shiboken 归一化与防呆校验。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_ubuntu_build_never_creates_or_reuses_a_venv():
    src = _read("build_ubuntu.sh")
    assert "-m venv" not in src
    assert "--upgrade pip" not in src
    assert 'PY="${PYTHON:-python3}"' in src
    assert "使用系统 Python（不创建虚拟环境）" in src
    assert ".venv-build-ubuntu" in src and "不再使用" in src
    assert "$PY" in src and "normalize_bundle.py" in src
    assert "SHIBOKEN_LIB" in src          # 防呆校验仍在
    assert '[[ -f "$f" && ! -L "$f" ]]' in src


def test_windows_build_never_creates_or_reuses_a_venv():
    src = _read("build_windows.bat")
    assert "-m venv" not in src
    assert "--upgrade pip" not in src
    assert ".venv-build-win" in src and "NOT used" in src
    assert "SYSTEM Python" in src
    assert 'set "PYCMD=%PYTHON%"' in src
    assert "%PYCMD% -m PyInstaller" in src
    assert "normalize_bundle.py" in src and "SB_OK" in src
    assert "app.ico" in src


def test_deb_build_inherits_system_python_and_normalizer():
    src = _read("build_deb.sh")
    assert "build_ubuntu.sh" in src
    assert "normalize_bundle.py" in src
    assert "LD_LIBRARY_PATH" in src
    assert "SB_OK" in src


def test_requirements_build_still_layers_on_runtime_requirements():
    src = _read("requirements-build.txt")
    assert "-r requirements.txt" in src
    assert "pyinstaller" in src.lower()


def test_readme_documents_system_only_build_and_lazy_import_fix():
    src = _read("README.md")
    assert "虚拟环境" in src
    assert "系统" in src
    assert "lazy_import" in src


def test_no_stale_venv_references_in_other_scripts():
    for rel in ("build_ubuntu.sh", "build_windows.bat"):
        src = _read(rel)
        # 只允许出现在「旧目录提示」里，不允许出现在实际执行语句中
        for line in src.splitlines():
            stripped = line.strip()
            if "-m venv" in stripped or "Scripts\\python.exe" in stripped:
                raise AssertionError(f"{rel} 仍在创建/使用 venv: {stripped}")

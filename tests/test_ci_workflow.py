# -*- coding: utf-8 -*-
"""依赖分层与 GitHub Actions 打包流水线的静态契约（离线、不需要构建环境）。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"


def _lines(name: str) -> list[str]:
    text = (ROOT / name).read_text(encoding="utf-8")
    return [l.strip() for l in text.splitlines()
            if l.strip() and not l.lstrip().startswith("#")]


def test_runtime_uses_only_pyside6_essentials():
    deps = _lines("requirements.txt")
    qt = [l for l in deps if l.startswith("PySide6")]
    assert len(qt) == 2
    assert all(l.startswith("PySide6-Essentials>=") for l in qt)
    assert not any(l.startswith("PySide6>=") for l in deps)
    assert not any("Addons" in l or "WebEngine" in l for l in deps)


def test_dependency_layers_are_separate():
    build = _lines("requirements-build.txt")
    dev = _lines("requirements-dev.txt")
    assert "-r requirements.txt" in build and "-r requirements.txt" in dev
    assert any(l.startswith("pyinstaller>=6.0") for l in build)
    assert any(l.startswith("pytest>=") for l in dev)
    assert not any("pytest" in l for l in build)
    assert not any("pyinstaller" in l for l in dev)


def test_no_undeclared_optional_dependency_in_shipped_code():
    assert not (ROOT / "tools" / "memory_monitor.py").exists()
    for sub in ("rewards_assistant", "tools"):
        for path in (ROOT / sub).rglob("*.py"):
            assert "psutil" not in path.read_text(encoding="utf-8", errors="ignore"), path


def test_spec_collects_qt_flag_support_without_metapackage():
    # 只装 PySide6-Essentials 时没有 PySide6 元包的 dist-info，PyInstaller 的
    # hook-PySide6 探测不到版本，不会自动带上这个模块。
    spec = (ROOT / "MSRewardsAssistant.spec").read_text(encoding="utf-8")
    assert "PySide6.support.deprecated" in spec


def test_ci_workflow_covers_three_platforms():
    yml = WORKFLOW.read_text(encoding="utf-8")
    assert yml.startswith("name: Build packages")
    for job in ("test:", "ubuntu:", "windows:", "macos:", "release:"):
        assert job in yml, job
    for token in ("requirements-dev.txt", "requirements-build.txt",
                  "python -m pytest -q", "bash build_deb.sh",
                  "build_windows.bat", "build_inno.bat", "build_macos.sh",
                  "choco install innosetup", "dpkg-deb --info",
                  "actions/upload-artifact@v4",
                  "startsWith(github.ref, 'refs/tags/v')"):
        assert token in yml, token
    assert "runs-on: ubuntu-" in yml and "runs-on: windows-" in yml
    assert "runs-on: macos-" in yml
    # Ubuntu 22.04 的源没有 libxcb-cursor0，必须尽力安装而不是让 apt 整条失败
    assert "::warning::libxcb-cursor0 unavailable" in yml
    # runner 自带系统 python3（22.04 上是 3.10），必须显式锁定 setup-python
    # 的解释器，否则 pip 与 PyInstaller 可能落在不同版本上
    assert "PYTHON: python" in yml
    assert 'set "PYTHON=python"' in yml
    # 与本地构建一致：CI 只用系统解释器，不建虚拟环境、不升级系统 pip
    assert "-m venv" not in yml and "--upgrade pip" not in yml


def test_ci_test_job_installs_qt_base_runtime_before_pytest():
    """回归：ubuntu runner 默认缺 libEGL.so.1，QtGui 在 pytest 收集阶段就导入失败。

    QT_QPA_PLATFORM=offscreen 不能替代系统库——libQt6Gui.so.6 的 DT_NEEDED
    在 import 时就要解析，与是否真的开窗口无关。
    """
    yml = WORKFLOW.read_text(encoding="utf-8")
    head = yml.split("  # ------------------------------------------------------- Ubuntu", 1)[0]
    assert "Install Qt base runtime libraries" in head
    for pkg in ("libegl1", "libgl1", "libdbus-1-3", "libfontconfig1",
                "libxkbcommon0"):
        assert pkg in head, pkg
    # 装系统库必须排在 pip 装依赖与跑测试之前
    assert head.index("Install Qt base runtime libraries") < head.index("python -m pytest -q")
    assert "Verify Qt imports" in head
    assert "PySide6.QtTest import QTest" in head
    assert "bash -n build_macos.sh" in head


def test_ci_workflow_fails_loudly_and_verifies_artifacts():
    yml = WORKFLOW.read_text(encoding="utf-8")
    # cmd 步骤必须显式传递退出码，且 ISCC 不能用 /Q 吞掉编译错误
    assert yml.count("if errorlevel 1 exit /b 1") == 2
    assert "build_inno.bat --no-build" in yml
    assert "--quiet" not in yml
    # 三个平台的产物都要做实质校验，而不只是「文件存在」
    for token in ("dpkg-deb --field", "config import APP_VERSION",
                  "libqxcb.so", "LinkType", "ProductVersion",
                  "libqcocoa.dylib", "codesign --verify",
                  "no .deb artifact", "no Windows .exe artifact",
                  "no macOS .dmg artifact"):
        assert token in yml, token


def test_ci_workflow_uploads_all_artifacts():
    yml = WORKFLOW.read_text(encoding="utf-8")
    assert "deb-dist/*.deb" in yml
    assert "MSRewardsAssistantSetup.exe" in yml
    assert "macos-dist/*.dmg" in yml
    assert yml.count("if-no-files-found: error") == 3

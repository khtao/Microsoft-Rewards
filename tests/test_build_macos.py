# -*- coding: utf-8 -*-
"""macOS 构建脚本 / spec BUNDLE 分支 / CI job 的静态契约（离线）。

沙盒是 Linux，PyInstaller 不能交叉编译 macOS 产物；这里检查脚本、spec
与流水线的结构，真实构建由 GitHub Actions 的 macos runner 完成。
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "build_macos.sh"
SPEC = ROOT / "MSRewardsAssistant.spec"
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_macos_script_parses_and_is_executable():
    assert SCRIPT.exists()
    assert SCRIPT.stat().st_mode & 0o111, "build_macos.sh 必须可执行"
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_macos_script_uses_system_python_and_single_spec():
    s = _text(SCRIPT)
    assert 'PY="${PYTHON:-python3}"' in s
    assert "使用系统 Python（不创建虚拟环境）" in s
    assert "-m venv" not in s and "--upgrade pip" not in s
    assert "requirements-build.txt" in s
    assert "MSRewardsAssistant.spec" in s
    assert "--noconfirm --clean" in s
    assert "tools/normalize_bundle.py" in s
    assert 'uname -s)' in s and "Darwin" in s


def test_macos_script_is_self_contained_and_guards_bundle():
    s = _text(SCRIPT)
    # 只用 macOS 系统自带工具，不引入任何第三方打包依赖
    for tool in ("sips", "iconutil", "plutil", "codesign", "ditto", "hdiutil"):
        assert tool in s, tool
    for option in ("--no-dmg", "--package-only", "--help"):
        assert option in s, option
    # 产物完整性守卫
    for token in ("base_library.zip", "libqcocoa.dylib",
                  "libshiboken6*.dylib", "Contents/Info.plist",
                  "codesign --verify --deep --strict", "PlistBuddy"):
        assert token in s, token
    assert "ln -s /Applications" in s
    assert "UDZO" in s
    assert "macos-dist/" in s


def test_spec_bundle_branch_is_darwin_only():
    src = _text(SPEC)
    tree = ast.parse(src)
    # darwin 分支用 sys.platform；精简 hiddenimports 时不能把 import sys 删掉，
    # 否则 macOS 构建会在 spec 执行阶段直接 NameError（Linux 构建不会触发）。
    imports = {alias.name for node in tree.body
               if isinstance(node, ast.Import) for alias in node.names}
    assert "sys" in imports
    branches = [n for n in tree.body
                if isinstance(n, ast.If) and "darwin" in ast.unparse(n.test)]
    assert len(branches) == 1
    branch = ast.unparse(branches[0])
    assert "BUNDLE(" in branch
    assert "name='MSRewardsAssistant.app'" in branch
    assert "bundle_identifier='community.msrewardsassistant'" in branch
    assert "version=APP_VERSION" in branch
    assert "CFBundleDisplayName" in branch
    assert ".macos-build" in branch
    # 非 macOS 平台仍只走 EXE + COLLECT
    assert "COLLECT(" in src and "Analysis(" in src


def test_gitignore_ignores_macos_artifacts():
    ignore = _text(ROOT / ".gitignore")
    for token in ("macos-dist/", ".macos-build/", "*.dmg", "*.icns", "*.app/"):
        assert token in ignore, token


def test_ci_runs_macos_job_and_publishes_dmg():
    yml = _text(WORKFLOW)
    assert "macos:" in yml
    assert "runs-on: macos-" in yml
    assert "bash build_macos.sh" in yml
    assert yml.count("PYTHON: python") == 2  # Ubuntu 与 macOS 都锁定解释器
    assert "needs: [ubuntu, windows, macos]" in yml
    assert "macos-dist/*.dmg" in yml
    assert "no macOS .dmg artifact" in yml
    assert yml.count("if-no-files-found: error") == 3

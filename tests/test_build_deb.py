"""依赖清单 / .deb 构建脚本的静态回归（不需要 root、不需要打包环境）。

校验三类约束：运行时只装 PySide6-Essentials 核心模块；构建脚本使用统一
spec 与系统 Python；deb 的 Depends 覆盖 Qt xcb 平台插件所需的系统库。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _dep_lines() -> list[str]:
    req = (ROOT / "requirements.txt").read_text(encoding="utf8")
    return [l.strip() for l in req.splitlines()
            if l.strip() and not l.lstrip().startswith("#")]


def _marker_matches(line: str, py: tuple[int, int]) -> bool:
    """只解析 requirements.txt 里实际使用的两种 python_version marker。"""
    marker = line.split(";", 1)[1].strip() if ";" in line else ""
    if not marker:
        return True
    if 'python_version < "3.14"' in marker:
        return py < (3, 14)
    if 'python_version >= "3.14"' in marker:
        return py >= (3, 14)
    raise AssertionError(f"未识别的 marker: {marker}")


def test_runtime_dependencies_are_three_packages():
    lines = _dep_lines()
    assert sum(1 for l in lines if l.startswith("PySide6")) == 2
    assert sum(1 for l in lines if l.startswith("requests>=")) == 1
    assert sum(1 for l in lines if l.startswith("playwright>=")) == 1
    assert not any("Addons" in l or "WebEngine" in l for l in lines)


def test_pyside6_upper_bound_does_not_lock_out_new_python():
    """回归：写死 <6.10 会让 Python 3.15 报 No matching distribution found。"""
    pyside = [l for l in _dep_lines() if l.startswith("PySide6")]
    assert len(pyside) == 2
    assert all("python_version" in l for l in pyside)
    assert "<6.10" in pyside[0] and 'python_version < "3.14"' in pyside[0]
    # 新 Python 这一行不能再带上界，否则将来 6.13+ 发布会重蹈覆辙
    assert "<6.10" not in pyside[1] and '>= "3.14"' in pyside[1]
    for py in ((3, 10), (3, 11), (3, 12), (3, 13), (3, 14), (3, 15), (3, 16)):
        hits = [l for l in pyside if _marker_matches(l, py)]
        assert len(hits) == 1, (py, hits)


def test_build_dependencies_layer_on_runtime():
    txt = (ROOT / "requirements-build.txt").read_text(encoding="utf8")
    lines = [l.strip() for l in txt.splitlines()
             if l.strip() and not l.lstrip().startswith("#")]
    assert "-r requirements.txt" in lines
    assert any(l.startswith("pyinstaller>=6.0") for l in lines)


def test_pyinstaller_spec_collects_only_what_is_needed():
    spec = (ROOT / "MSRewardsAssistant.spec").read_text(encoding="utf8")
    code = "\n".join(l for l in spec.splitlines()
                     if l.strip() and not l.lstrip().startswith("#"))
    assert "collect_all('playwright')" in code
    assert "collect_all('PySide6')" not in code       # 交给官方 Qt hook
    assert "search_actions.jsonl" not in code        # 运行时日志不进包
    assert "assets/icons" not in code or "icons" in code
    assert "upx=False" in code                        # CI 无 upx 也稳定
    assert "Analysis(" in code and "COLLECT(" in code


def test_build_scripts_use_the_single_spec_and_system_python_only():
    ubuntu = (ROOT / "build_ubuntu.sh").read_text(encoding="utf8")
    win = (ROOT / "build_windows.bat").read_text(encoding="utf8")
    assert "MSRewardsAssistant.spec" in ubuntu and "MSRewardsAssistant.spec" in win
    # 用户明确要求：编译只用系统环境，不创建 / 不复用虚拟环境
    assert "-m venv" not in ubuntu and "-m venv" not in win
    assert "--upgrade pip" not in ubuntu and "--upgrade pip" not in win
    assert 'PY="${PYTHON:-python3}"' in ubuntu
    assert "使用系统 Python（不创建虚拟环境）" in ubuntu
    assert "SYSTEM Python" in win and 'set "PYCMD=%PYTHON%"' in win
    assert "requirements-build.txt" in ubuntu and "requirements-build.txt" in win
    assert "--noconfirm --clean" in ubuntu
    # 不再自己拼 --add-data / --collect-all（由 spec 统一管）
    assert "assets/icons:assets/icons" not in ubuntu
    assert "--collect-all playwright" not in ubuntu


def test_deb_control_covers_all_qt_xcb_libraries():
    deb = (ROOT / "build_deb.sh").read_text(encoding="utf8")
    dep = deb.split("Depends: ", 1)[1].split("\n", 1)[0]
    aliases = {x.strip() for alt in dep.split(",") for x in alt.split("|")}
    # ldd libqxcb.so 在精简环境里报缺的 8 个库，逐一映射到 Debian 包
    for pkg in ("libxkbcommon-x11-0", "libxcb-cursor0", "libxcb-icccm4",
                "libxcb-util1", "libxcb-image0", "libxcb-keysyms1",
                "libxcb-render-util0", "libxcb-xkb1"):
        assert pkg in aliases, pkg
    # Ubuntu 24.04 的 t64 改名兼容
    assert "libglib2.0-0t64" in aliases and "libdbus-1-3t64" in aliases
    # 基本图形栈
    for pkg in ("libfontconfig1", "libegl1", "libgl1", "libx11-xcb1"):
        assert pkg in aliases, pkg


def test_deb_packaging_guards_against_incomplete_dist():
    """CI 超时/进程被杀时 COLLECT 可能只拷一半，仍会留下可执行 EXE。"""
    deb = (ROOT / "build_deb.sh").read_text(encoding="utf8")
    assert "_internal/base_library.zip" in deb
    assert "assets/icons/app.svg" in deb
    assert "构建不完整" in deb
    assert "dpkg-deb --root-owner-group --build" in deb
    assert "postinst" in deb and "ms-rewards-assistant.desktop" in deb


def test_gitignore_and_no_personal_log_sample_in_repo():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf8")
    for token in ("cookies.txt", "session_cookies.bin", "browser_profile/", "*.db", "*.log",
                  "search_actions.jsonl", "dist/", "deb-dist/", "*.deb"):
        assert token in ignore, token
    # 运行时日志可能含真实公网 IP 与会话 ID，且包内无人读取，不应入库
    assert not (ROOT / "rewards_assistant" / "search_actions.jsonl").exists()


def test_shell_scripts_parse():
    for name in ("build_deb.sh", "build_ubuntu.sh", "build_macos.sh"):
        r = subprocess.run(["bash", "-n", str(ROOT / name)],
                           capture_output=True, text=True)
        assert r.returncode == 0, (name, r.stderr)

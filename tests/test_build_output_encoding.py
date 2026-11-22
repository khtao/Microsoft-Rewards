# -*- coding: utf-8 -*-
"""Build-time output encoding contracts.

Windows CI captures stdout through a pipe whose encoding is cp1252. ``chcp``
only changes the console code page, not a redirected pipe, so one non-ASCII
character printed by a Python build tool aborts the whole job with
``UnicodeEncodeError``. Shell ``echo`` is fine (raw UTF-8 bytes); Python's
strict codec is not.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NORMALIZER = ROOT / "tools" / "normalize_bundle.py"
WIN_BAT = ROOT / "build_windows.bat"


def test_normalizer_source_is_pure_ascii():
    src = NORMALIZER.read_bytes().decode("ascii")
    assert src.isascii()
    assert "[ERROR]" in src
    assert "[错误]" not in src


def test_normalizer_guards_stdout_and_stderr():
    src = NORMALIZER.read_text(encoding="ascii")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_make_console_safe"]
    assert calls, "main() must install the console guard"
    assert "backslashreplace" in src
    assert "sys.stdout" in src and "sys.stderr" in src
    main = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "_make_console_safe" for n in ast.walk(main))


def test_windows_bat_is_ascii_crlf_and_never_changes_code_page():
    raw = WIN_BAT.read_bytes()
    assert all(b < 128 for b in raw)
    assert raw.count(b"\r\n") == raw.count(b"\n")
    # Only executable lines matter: a REM comment may legitimately explain
    # why the code page must NOT be switched.
    executable = [l.strip() for l in raw.decode("ascii").split("\r\n")
                  if l.strip() and not l.lstrip().upper().startswith("REM ")]
    assert not any(l.lower().startswith("chcp ") for l in executable)
    assert not any("PYTHONIOENCODING" in l for l in executable)
    assert any("normalize_bundle.py" in l for l in executable)


def test_normalizer_survives_cp1252_stdout_with_non_ascii_path(tmp_path):
    """End-to-end regression for the exact CI failure.

    A Chinese directory name reaches ``print("[normalize] OK: ...")``; under
    PYTHONIOENCODING=cp1252 the old build raised UnicodeEncodeError. With ASCII
    messages + backslashreplace it must finish and keep stdout ASCII-only.
    """
    bundle = tmp_path / "下载目录" / "MSRewardsAssistant"
    internal = bundle / "_internal"
    (internal / "shiboken6").mkdir(parents=True)
    real = internal / "shiboken6" / "libshiboken6.abi3.so.6.9"
    real.write_bytes(b"REAL")
    link = internal / "libshiboken6.abi3.so.6.9"
    link.symlink_to("shiboken6/libshiboken6.abi3.so.6.9")

    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    proc = subprocess.run(
        [sys.executable, str(NORMALIZER), str(bundle)],
        capture_output=True, env=env, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr.decode("ascii", "backslashreplace")
    assert all(b < 128 for b in proc.stdout), proc.stdout
    assert b"[normalize] OK:" in proc.stdout
    assert link.is_file() and not link.is_symlink()
    assert link.read_bytes() == b"REAL"

# -*- coding: utf-8 -*-
"""Build-time normalizer: keep a REAL shiboken6 library at ``_internal`` root.

PyInstaller 6.x may place a symlink at ``_internal/libshiboken6.abi3.so.6.9``
("symlink suppression"). It resolves on the build machine, but a different
machine, an unpacked archive, a filesystem without symlinks, or a Windows DLL
name can break it::

    libshiboken6.abi3.so.6.9: cannot open shared object file
    ImportError: ... shiboken6/__init__.py line 27

Run this after PyInstaller and before packaging. Linux / macOS / Windows.

    python tools/normalize_bundle.py dist/MSRewardsAssistant

All user-visible output is ASCII. On Windows CI the runner captures stdout
through a pipe whose encoding is cp1252 (``chcp`` does not affect pipes), so a
single non-ASCII character in a message or a path used to abort the whole build
with ``UnicodeEncodeError``. ``_make_console_safe`` additionally lets any
remaining non-ASCII bytes degrade to escapes instead of raising.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

PATTERNS = (
    "libshiboken6*.so*", "libshiboken6*.dylib",
    "shiboken6*.dll", "libshiboken6*.dll",
)


def _make_console_safe() -> None:
    """Never let an unencodable character abort the build."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="backslashreplace")
        except (OSError, ValueError, AttributeError):
            pass


def _candidates(directory: Path) -> list[Path]:
    out: list[Path] = []
    for pattern in PATTERNS:
        out.extend(sorted(directory.glob(pattern)))
    return out


def _source_for(internal: Path, name: str) -> Path | None:
    """Find a real library file, in order of preference."""
    # 1) The copy PyInstaller collected into a subdirectory (most common).
    for sub in ("shiboken6", "PySide6", "."):
        for cand in _candidates(internal / sub):
            if cand.is_file() and cand.name == name and not cand.is_symlink():
                return cand
    # 2) The shiboken6 package installed in the build environment.
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
        raise SystemExit(f"[ERROR] Missing {internal}; run PyInstaller first")

    # Library names expected at top level: use existing entries (including
    # symlinks) when present, otherwise infer them from the subdirectory.
    top = _candidates(internal)
    if not top:
        inner = _candidates(internal / "shiboken6")
        if not inner:
            raise SystemExit("[ERROR] No shiboken6 library in bundle; refusing to ship a broken build")
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
            raise SystemExit(f"[ERROR] Cannot find a real shiboken6 library for {name}")
        if dst.is_symlink() or not dst.exists():
            if dst.is_symlink() or dst.exists():
                dst.unlink()
            shutil.copy2(real, dst)
            done.append(f"{name} <- {real.name} (copied as real file)")
        elif dst.resolve() == dst:
            done.append(f"{name} (already a real file)")
        else:
            dst.unlink()
            shutil.copy2(real, dst)
            done.append(f"{name} <- {real.name} (copied as real file)")

    # Final check: a real regular file must exist at top level.
    real_top = [c for c in _candidates(internal) if c.is_file() and not c.is_symlink()]
    if not real_top:
        raise SystemExit("[ERROR] No real shiboken6 library at top level after normalization")
    return done


def main() -> int:
    _make_console_safe()
    if len(sys.argv) != 2:
        print("Usage: python tools/normalize_bundle.py <dist/MSRewardsAssistant>")
        return 2
    bundle = Path(sys.argv[1]).resolve()
    for line in normalize(bundle):
        print("[normalize]", line)
    print(f"[normalize] OK: {bundle / '_internal'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

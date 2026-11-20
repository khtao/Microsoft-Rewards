@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

if not "%OS%"=="Windows_NT" (
    echo [ERROR] Please run this script on Windows.
    exit /b 1
)

if not exist "MSRewardsAssistant.spec" (
    echo [ERROR] Missing MSRewardsAssistant.spec
    goto :fail
)

if not exist "assets\icons\app.ico" (
    echo [WARN] assets\icons\app.ico missing; the EXE will use the default icon.
)


if exist ".venv-build-win" (
    echo [NOTE] Found stale .venv-build-win from older builds; it is NOT used.
    echo        You can delete it with: rmdir /s /q .venv-build-win
)

echo [1/3] Using system Python (no venv)...
python -c "import sys;print('[INFO] Interpreter:',sys.executable)" || goto :fail
python -c "import sys;print('[INFO] Build Python: {}.{}'.format(*sys.version_info[:2]))" || goto :fail

python -c "import sys;sys.exit(0 if sys.version_info >= (3,15) else 1)"
if not errorlevel 1 (
    echo [NOTE] Python 3.15+ needs PySide6 6.12+; requirements.txt selects it automatically.
    echo        This combination is not fully regression-tested. Python 3.12/3.13 is recommended.
)

REM Do NOT upgrade system pip: building against the system environment must not
REM modify the machine-wide package manager. Only install what is missing.
echo [2/3] Installing runtime and packaging dependencies into SYSTEM Python...
python -m pip install --disable-pip-version-check -r requirements-build.txt || goto :depfail
python -c "import importlib.metadata as m;print('[INFO] requests',m.version('requests'))" || goto :depfail

REM Icons / fonts / Playwright driver / PySide6 / hiddenimports are all
REM collected by MSRewardsAssistant.spec (SPECPATH is this directory).
echo [3/3] Building Windows desktop application from spec...
python -m PyInstaller --noconfirm --clean MSRewardsAssistant.spec || goto :fail
REM PyInstaller 6.x may emit a symlink / relocate the shiboken6 DLL; force a
REM real file in _internal root before the guard below.
python tools\normalize_bundle.py dist\MSRewardsAssistant || goto :fail

if not exist "dist\MSRewardsAssistant\MSRewardsAssistant.exe" (
    echo [ERROR] Build output missing.
    goto :fail
)
REM Same guard as build_ubuntu.sh: shiboken6 DLL must be a real file in the
REM _internal root. Some PyInstaller/shiboken6 combinations only place it in
REM _internal\shiboken6\ and the exe then fails with
REM "shiboken6.abi3.dll: The specified module could not be found".
set "SB_OK="
for %%F in ("dist\MSRewardsAssistant\_internal\shiboken6*.dll" "dist\MSRewardsAssistant\_internal\libshiboken6*.dll") do (
    if exist %%F set "SB_OK=1"
)
if not defined SB_OK (
    echo [ERROR] shiboken6 DLL is missing from _internal root.
    echo         Rebuild after confirming MSRewardsAssistant.spec SHIBOKEN_EXPLICIT.
    goto :fail
)
echo.
echo Build complete: %CD%\dist\MSRewardsAssistant\MSRewardsAssistant.exe
echo Keep all files inside dist\MSRewardsAssistant together when distributing.
exit /b 0

:depfail
echo.
echo [ERROR] Dependency install failed.
echo   - "No matching distribution found for PySide6": check requirements.txt is
echo     the python_version-split version, then retry with the official index:
echo       python -m pip install -i https://pypi.org/simple -r requirements-build.txt
echo   - Or build with a different SYSTEM interpreter (no venv needed):
echo       set PYTHON=py -3.13
echo     See README "Build installers" for details.
goto :fail

:fail
echo [ERROR] Build failed. Please check the messages above.
exit /b 1

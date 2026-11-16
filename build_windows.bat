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

REM Allow an explicit interpreter, e.g. set PYTHON=py -3.13 before running.
if defined PYTHON goto :have_python
where py >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON=py -3"
) else (
    set "PYTHON=python"
)
:have_python

if not exist ".venv-build-win\Scripts\python.exe" (
    echo [1/3] Creating Windows virtual environment...
    %PYTHON% -m venv .venv-build-win || goto :fail
) else (
    echo [1/3] Reusing existing .venv-build-win
)
set "PY=%CD%\.venv-build-win\Scripts\python.exe"

"%PY%" -c "import sys;print('[INFO] Build Python: {}.{}'.format(*sys.version_info[:2]))"

"%PY%" -c "import sys;sys.exit(0 if sys.version_info >= (3,15) else 1)"
if %errorlevel%==0 (
    echo [NOTE] Python 3.15+ needs PySide6 6.12+; requirements.txt selects it automatically.
    echo        This combination is not fully regression-tested. Python 3.12/3.13 is recommended.
)

echo [2/3] Installing runtime and packaging dependencies...
"%PY%" -m pip install --disable-pip-version-check --upgrade pip || goto :depfail
"%PY%" -m pip install --disable-pip-version-check -r requirements-build.txt || goto :depfail

REM Icons / fonts / Playwright driver / PySide6 / hiddenimports are all
REM collected by MSRewardsAssistant.spec (SPECPATH is this directory).
echo [3/3] Building Windows desktop application from spec...
"%PY%" -m PyInstaller --noconfirm --clean MSRewardsAssistant.spec || goto :fail

if not exist "dist\MSRewardsAssistant\MSRewardsAssistant.exe" (
    echo [ERROR] Build output missing.
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
echo       "%PY%" -m pip install -i https://pypi.org/simple -r requirements-build.txt
echo   - Or delete .venv-build-win and rebuild with Python 3.12/3.13:
echo       set PYTHON=py -3.13
echo     See README "Build installers" for details.
goto :fail

:fail
echo [ERROR] Build failed. Please check the messages above.
exit /b 1

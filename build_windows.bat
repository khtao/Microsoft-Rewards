@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

if not "%OS%"=="Windows_NT" (
    echo [ERROR] Please run this script on Windows.
    exit /b 1
)

where py >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON=py -3"
) else (
    set "PYTHON=python"
)

if not exist ".venv-build-win\Scripts\python.exe" (
    echo [1/3] Creating Windows virtual environment...
    %PYTHON% -m venv .venv-build-win || goto :fail
)
set "PY=.venv-build-win\Scripts\python.exe"

echo [2/3] Installing runtime and packaging dependencies...
"%PY%" -m pip install --upgrade pip || goto :fail
"%PY%" -m pip install -r requirements-build.txt || goto :fail

echo [3/3] Building Windows desktop application...
set "FONT_ARGS="
if exist "fonts\" set "FONT_ARGS=--add-data fonts;fonts"
"%PY%" -m PyInstaller --noconfirm --clean --onedir --windowed ^
  --name MSRewardsAssistant --paths "." ^
  --collect-all playwright ^
  %FONT_ARGS% ^
  rewards_assistant\__main__.py || goto :fail

echo.
echo Build complete: %CD%\dist\MSRewardsAssistant\MSRewardsAssistant.exe
echo Keep all files inside dist\MSRewardsAssistant together when distributing.
exit /b 0

:fail
echo [ERROR] Build failed. Please check the messages above.
exit /b 1

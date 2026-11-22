@echo off
setlocal EnableExtensions EnableDelayedExpansion
REM ===========================================================================
REM  build_inno.bat - build MSRewardsAssistantSetup.exe with Inno Setup.
REM
REM  Inno Setup is the ONLY build-time tool needed. The generated Setup.exe is
REM  completely self-contained and needs nothing at install time (no Inno
REM  Setup, no .NET, no VC++ redist, no companion files).
REM
REM  Pure ASCII + CRLF, no code page switching. No CMD/VBS install logic at all:
REM  shortcuts, Start Menu group, uninstaller and registry entry are all
REM  handled natively by Inno Setup from setup.iss.
REM
REM  Usage:  installer\build_inno.bat [options] ["C:\path\ISCC.exe"]
REM            --no-build   do not run build_windows.bat if dist is missing
REM            --english    force English-only wizard (skip ChineseSimplified.isl)
REM            --quiet      ISCC /Q (errors only)
REM            --open       open setup.iss in the graphical Inno Setup Compiler
REM ===========================================================================
for %%A in ("%~dp0..") do set "ROOT=%%~fA"
set "ISS=%ROOT%\installer\setup.iss"
set "ICO=%ROOT%\assets\icons\app.ico"
set "DIST=%ROOT%\dist\MSRewardsAssistant"
set "OUT=%ROOT%\MSRewardsAssistantSetup.exe"
set "NOBUILD=0"
set "ENGLISH=0"
set "QUIET=0"
set "OPEN=0"
set "ISCC="
if not "%*"=="" (
    for %%A in (%*) do (
        if /i "%%~A"=="--no-build" set "NOBUILD=1"
        if /i "%%~A"=="--english" set "ENGLISH=1"
        if /i "%%~A"=="--quiet" set "QUIET=1"
        if /i "%%~A"=="--open" set "OPEN=1"
        if /i "%%~xA"==".exe" set "ISCC=%%~fA"
    )
)

echo ============================================================
echo  MSRewardsAssistant - Inno Setup builder
echo ============================================================
echo.
echo  Project  : %ROOT%
echo  Script   : %ISS%
echo.
if "%ROOT:~0,2%"=="\\" (
    echo  NOTE: project is on a UNC share. Inno Setup 6+ is Unicode and can
    echo        handle it, but a local folder such as C:\MRA is recommended.
    echo.
)

REM ---- [1/5] Locate ISCC.exe -------------------------------------------------
echo [1/5] Locating Inno Setup Command-Line Compiler (ISCC.exe) ...
if defined ISCC goto :have_iscc
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 7\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 7\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 5\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 5\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 5\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 5\ISCC.exe"
if not defined ISCC (
    for /f "delims=" %%I in ('where iscc 2^>nul') do if not defined ISCC set "ISCC=%%I"
)
if not defined ISCC goto :noiscc
:have_iscc
if not exist "%ISCC%" goto :noiscc
for %%I in ("%ISCC%") do set "ISCDIR=%%~dpI"
echo        ISCC : %ISCC%

REM ---- [2/5] Inputs ---------------------------------------------------------
echo [2/5] Checking inputs ...
if not exist "%ISS%" goto :noiss
if not exist "%ICO%" goto :noico
if exist "%DIST%\MSRewardsAssistant.exe" goto :have_dist
if "%NOBUILD%"=="1" goto :nodist
echo        dist not found - calling build_windows.bat (may take a few minutes)...
pushd "%ROOT%"
call "%ROOT%\build_windows.bat"
set "BEC=%ERRORLEVEL%"
popd
if not "%BEC%"=="0" goto :buildfail
if not exist "%DIST%\MSRewardsAssistant.exe" goto :nodist
:have_dist
echo        dist : OK
echo        icon : OK

REM ---- [3/5] Language + optional GUI IDE ------------------------------------
echo [3/5] Compiler options ...
set "LANGSW="
if "%ENGLISH%"=="1" goto :eng
if exist "%ISCDIR%Languages\ChineseSimplified.isl" goto :zh
echo        ChineseSimplified.isl not found - using English wizard.
set "LANGSW=/DEnglishOnly=1"
goto :langdone
:zh
echo        ChineseSimplified.isl found - Chinese + English wizard.
goto :langdone
:eng
echo        --english specified - English-only wizard.
set "LANGSW=/DEnglishOnly=1"
:langdone
set "QS="
if "%QUIET%"=="1" set "QS=/Q"
if "%OPEN%"=="0" goto :noopen
echo        Opening graphical Inno Setup Compiler...
if exist "%ISCDIR%Compil32.exe" (
    start "" "%ISCDIR%Compil32.exe" "%ISS%"
) else (
    start "" "%ISS%"
)
echo        Press Ctrl+F9 in the IDE to compile. Done.
exit /b 0
:noopen

REM ---- [4/5] Compile --------------------------------------------------------
echo [4/5] Compiling setup.iss ...
if exist "%OUT%" del /f /q "%OUT%" 2>nul
pushd "%ROOT%"
"%ISCC%" %LANGSW% %QS% "%ISS%"
set "IEC=%ERRORLEVEL%"
popd
if not "%IEC%"=="0" goto :compilefail
if not exist "%OUT%" goto :noout
for %%A in ("%OUT%") do if %%~zA LSS 100000 goto :noout

REM ---- [5/5] Done -----------------------------------------------------------
echo [5/5] Done.
echo.
for %%A in ("%OUT%") do echo    Setup package : %OUT%  size=%%~zA bytes
echo    Distribute this ONE exe. Double-click for a graphical wizard.
echo    It creates the desktop shortcut, Start Menu group and uninstall entry,
echo    installs to %%LOCALAPPDATA%%\Programs\MSRewardsAssistant, no UAC.
echo.
echo    Silent install  : MSRewardsAssistantSetup.exe /VERYSILENT
echo    Silent uninstall: "%%LOCALAPPDATA%%\Programs\MSRewardsAssistant\unins000.exe" /VERYSILENT
echo.
exit /b 0

REM ===========================================================================
:noiscc
echo.
echo [ERROR] Inno Setup compiler (ISCC.exe) not found.
echo.
echo   Install Inno Setup 6.5+ (free) from:  https://jrsoftware.org/isdl.php
echo   Default location: %%ProgramFiles(x86)%%\Inno Setup 6\ISCC.exe
echo.
echo   Or pass the full path explicitly:
echo        installer\build_inno.bat "C:\Some Folder\Inno Setup 6\ISCC.exe"
echo.
echo   Inno Setup is needed ONLY on this build machine. The generated
echo   MSRewardsAssistantSetup.exe has no such requirement for end users.
goto :fail
:noiss
echo.
echo [ERROR] Missing %ISS%
echo         Re-copy the whole installer\ folder from the project zip.
goto :fail
:noico
echo.
echo [ERROR] Missing %ICO% (used by SetupIconFile in setup.iss).
echo         Re-copy the assets\icons folder, or remove SetupIconFile.
goto :fail
:nodist
echo.
echo [ERROR] Cannot find %DIST%\MSRewardsAssistant.exe
echo         Run build_windows.bat first (or remove --no-build).
goto :fail
:buildfail
echo.
echo [ERROR] build_windows.bat failed (exit code %BEC%).
goto :fail
:compilefail
echo.
echo [ERROR] ISCC failed (exit code %IEC%). 0=ok, 1=bad args, 2=compile error.
echo         Read the compiler output above - it names the exact line.
echo         Script: %ISS%
echo         Common causes:
echo           1. dist\MSRewardsAssistant is empty or missing.
echo           2. ChineseSimplified.isl incompatible -> retry with --english
echo           3. Inno Setup 5 is too old -> install 6.5+ (PrivilegesRequired=lowest,
echo              recursesubdirs, CloseApplications, WizardStyle all need 6.x).
goto :fail
:noout
echo.
echo [ERROR] Compilation returned 0 but %OUT% is missing or too small.
echo         Check OutputDir=.. inside setup.iss and the disk free space.
goto :fail
:fail
echo.
echo [ERROR] Build failed.
popd 2>nul
exit /b 1

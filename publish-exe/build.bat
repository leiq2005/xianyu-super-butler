@echo off
setlocal EnableExtensions DisableDelayedExpansion
rem ============================================================
rem  publish-exe / build.bat
rem  One-click Windows EXE release builder (Nuitka standalone).
rem
rem  Usage:
rem    build.bat
rem
rem  Output:
rem    publish-exe\release\<APP_NAME>-<APP_VERSION>\
rem      Start.bat        double click to run
rem      Stop.bat         stop the running instance
rem      <APP_NAME>.exe   the compiled program
rem
rem  This file stays pure ASCII on purpose: cmd decodes .bat with the
rem  local code page, UTF-8 bytes would corrupt the parser.
rem ============================================================

cd /d "%~dp0"

rem --- locate a usable CPython 3.11 -----------------------------
set "PY="
if exist "%~dp0.buildenv\Scripts\python.exe" set "PY=%~dp0.buildenv\Scripts\python.exe"
if not defined PY (
    python -c "import sys;raise SystemExit(0 if sys.version_info[:2]==(3,11) else 1)" >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    py -3.11 -c "import sys;raise SystemExit(0)" >nul 2>&1
    if not errorlevel 1 set "PY=py -3.11"
)
if not defined PY (
    echo [ERROR] Python 3.11 not found.
    echo Install Python 3.11 and add it to PATH, or set PYTHON_HOME.
    pause
    exit /b 1
)

call "%~dp0config.bat"

echo.
echo Build toolchain : %PY%
echo Release         : %APP_NAME% %APP_VERSION%
echo.

%PY% "%~dp0build.py"
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo [ERROR] Build failed with code %RC%. Review the messages above.
    pause
    exit /b %RC%
)
pause
exit /b 0

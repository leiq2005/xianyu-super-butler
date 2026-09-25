@echo off
rem ============================================
rem  Backend server - runs the app directly, no Docker, no image build.
rem  This is the development entry point.
rem  For releases use publish\build.bat + publish\run.bat instead.
rem
rem  Usage:
rem    start-server.bat
rem
rem  Typical dev setup: run this in one window, start-client.bat in another.
rem  Backend (Python) changes need a restart: Ctrl+C, then run this again.
rem  Frontend changes do NOT need a restart - just refresh the browser.
rem
rem  Port conflict: if %API_PORT% is already taken, you get to choose
rem  between killing the old process, moving to the next free port, or
rem  quitting - instead of a confusing bind error.
rem ============================================
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

rem Prefer the project virtualenv when present, else PATH python.
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

rem These are only consumed on the very first start, when data\xianyu_data.db
rem does not exist yet and the admin account gets created.
if not defined ADMIN_USERNAME set "ADMIN_USERNAME=admin"
if not defined ADMIN_PASSWORD set "ADMIN_PASSWORD=admin123"
if not defined DB_PATH set "DB_PATH=data\xianyu_data.db"
if not defined API_PORT set "API_PORT=8080"

rem ============================================
rem  Resolve port conflicts before starting.
rem ============================================
:check_port
set "PORT_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /C:":%API_PORT% " ^| findstr /I "LISTENING"') do (
    if not defined PORT_PID set "PORT_PID=%%P"
)
if not defined PORT_PID goto start_server

set "PORT_PROC=unknown"
for /f "delims=," %%A in ('tasklist /fi "PID eq %PORT_PID%" /fo csv /nh 2^>nul') do set "PORT_PROC=%%~A"
echo.
echo [WARN] Port %API_PORT% is already in use.
echo        PID    : %PORT_PID%
echo        Process: %PORT_PROC%
echo.
echo   [K] Kill that process and start on %API_PORT%
echo   [U] Use the next free port instead
echo   [Q] Quit
choice /C KUQ /N /M "Choose K/U/Q: "
if errorlevel 3 goto quit
if errorlevel 2 goto find_free_port

echo Stopping PID %PORT_PID% ...
taskkill /PID %PORT_PID% /F >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Could not stop PID %PORT_PID%. Close it manually, then retry.
    pause
    exit /b 1
)
rem Give the OS a moment to release the socket.
timeout /t 2 /nobreak >nul
set "PORT_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /C:":%API_PORT% " ^| findstr /I "LISTENING"') do (
    if not defined PORT_PID set "PORT_PID=%%P"
)
if defined PORT_PID (
    echo [ERROR] Port %API_PORT% is still held by PID %PORT_PID% after killing.
    pause
    exit /b 1
)
goto start_server

:find_free_port
set /a API_PORT+=1
set "PORT_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /C:":%API_PORT% " ^| findstr /I "LISTENING"') do (
    if not defined PORT_PID set "PORT_PID=%%P"
)
if defined PORT_PID goto find_free_port
echo Switching to free port %API_PORT%.

:start_server
echo.
echo Starting backend http://localhost:%API_PORT%/
echo   interpreter: %PY%
echo   database   : %DB_PATH%
echo   login      : %ADMIN_USERNAME% / %ADMIN_PASSWORD%
echo.
echo Frontend is served from static\ - run start-client.bat to build it.
echo Press Ctrl+C to stop.
echo.
"%PY%" Start.py

echo.
echo Server stopped.
pause
exit /b 0

:quit
echo.
echo Nothing started. Port %API_PORT% left untouched.
pause
exit /b 0

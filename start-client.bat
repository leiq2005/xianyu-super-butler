@echo off
rem ============================================
rem  Frontend build - compiles frontend\ into static\, no Docker.
rem  The backend serves static\ straight from disk, so after a rebuild you
rem  only need to refresh the browser - the server does NOT need a restart.
rem
rem  Usage:
rem    start-client.bat         watch mode: auto-rebuild on every save
rem    start-client.bat once    single build, then exit
rem ============================================
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

if not exist "frontend\node_modules" (
    echo [ERROR] frontend\node_modules is missing. Run this once:
    echo           cd frontend ^&^& npm install
    pause
    exit /b 1
)

pushd frontend
if /i "%1"=="once" (
    echo Building frontend into static\ ...
    call npm run build
) else (
    echo Building frontend into static\ in WATCH mode. Ctrl+C to stop.
    call npx vite build --watch
)
set "RC=%errorlevel%"
popd

if not "%RC%"=="0" (
    echo.
    echo [ERROR] Frontend build failed with code %RC%.
    pause
    exit /b 1
)

@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "SOURCE=%~dp0"
call "%SOURCE%config.bat"
for %%I in ("%SOURCE%.") do set "RELEASE=%%~fI"
set "ARCHIVE=%RELEASE%\%ARCHIVE_NAME%"

where docker >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker was not found. Install Docker Desktop first.
    goto failed
)
docker info >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Start Docker Desktop, then retry.
    goto failed
)
docker image inspect "%IMAGE%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Image %IMAGE% is missing. Run build.bat first.
    goto failed
)

echo Exporting %IMAGE% to "%ARCHIVE%" ...
docker save -o "%ARCHIVE%.partial" "%IMAGE%"
if errorlevel 1 (
    echo [ERROR] Export failed. The previous .tar was not replaced.
    goto failed
)
move /y "%ARCHIVE%.partial" "%ARCHIVE%" >nul
if errorlevel 1 goto failed

echo.
echo [OK] Release files: "%RELEASE%"
echo Send the .tar, run.bat, stop.bat and LICENSE to the recipient.
echo Also provide the corresponding source code under AGPL-3.0.
echo Do NOT send your data, logs, backups or uploads directories.
echo Double-click run.bat next to the .tar file to start.
timeout /t 3 /nobreak >nul
exit /b 0

:failed
echo.
echo [ERROR] Export did not complete. Check the error above and free disk space.
pause
exit /b 1

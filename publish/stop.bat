@echo off
setlocal EnableExtensions DisableDelayedExpansion
call "%~dp0config.bat"
for %%I in ("%~dp0.") do set "DIR=%%~fI"

where docker >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker was not found.
    goto failed
)
docker info >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Start Docker Desktop, then retry.
    goto failed
)
docker container inspect "%CONTAINER%" >nul 2>&1
if errorlevel 1 (
    echo [OK] Container %CONTAINER% does not exist. Nothing to stop.
    goto done
)
set "OWNER="
for /f "delims=" %%I in ('docker inspect --format "{{.Config.Labels.xianyu_release_dir}}" "%CONTAINER%"') do set "OWNER=%%I"
if /i not "%OWNER%"=="%DIR%" (
    echo [ERROR] Container %CONTAINER% belongs to another folder or deployment.
    echo Run stop.bat from its original folder.
    goto failed
)
docker stop "%CONTAINER%" >nul
if errorlevel 1 goto failed
docker rm "%CONTAINER%" >nul
if errorlevel 1 goto failed
echo [OK] Container stopped and removed. The image and all data are kept.

:done
echo Data directory: "%DIR%"
timeout /t 3 /nobreak >nul
exit /b 0

:failed
echo.
echo [ERROR] Stop did not complete. Check the error above.
pause
exit /b 1

@echo off
setlocal EnableExtensions DisableDelayedExpansion
call "%~dp0config.bat"
for %%I in ("%~dp0.") do set "DIR=%%~fI"
set "ARCHIVE=%DIR%\%ARCHIVE_NAME%"

rem 判定是否首次建库：data\xianyu_data.db 不存在时为全新环境，会按本配置的
rem ADMIN_USERNAME / ADMIN_PASSWORD 创建管理员账号；已存在则沿用旧密码。
if exist "%DIR%\data\xianyu_data.db" (set "FRESH_DB=0") else (set "FRESH_DB=1")

if not "%ADMIN_USERNAME%"=="admin" (
    echo [ERROR] This application currently requires the administrator username admin.
    goto failed
)
if exist "%DIR%\data\xianyu_data.db" goto check_docker
if not defined ADMIN_PASSWORD goto configure_password
if "%ADMIN_PASSWORD%"=="CHANGE_ME" goto configure_password

:check_docker
where docker >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Install Docker Desktop first and enable Linux containers.
    goto failed
)
set "DOCKER_OS="
for /f "delims=" %%O in ('docker info --format "{{.OSType}}"') do set "DOCKER_OS=%%O"
if not defined DOCKER_OS (
    echo [ERROR] Cannot query the Docker engine. Check the error above.
    echo Start Docker Desktop and wait until the engine is ready, then retry.
    goto failed
)
if /i not "%DOCKER_OS%"=="linux" (
    echo [ERROR] Docker is not using Linux containers. Switch Docker Desktop to Linux containers.
    goto failed
)

if exist "%ARCHIVE%" (
    echo Loading the bundled image. This may take a few minutes ...
    docker load -i "%ARCHIVE%"
    if errorlevel 1 goto failed
)
docker image inspect "%IMAGE%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Image %IMAGE% is missing.
    echo Place %ARCHIVE_NAME% next to this script.
    goto failed
)

docker container inspect "%CONTAINER%" >nul 2>&1
if errorlevel 1 goto prepare
set "OWNER="
for /f "delims=" %%I in ('docker inspect --format "{{.Config.Labels.xianyu_release_dir}}" "%CONTAINER%"') do set "OWNER=%%I"
if /i not "%OWNER%"=="%DIR%" (
    echo [ERROR] Container %CONTAINER% belongs to another folder or deployment.
    echo Stop it from its original folder before starting this deployment.
    goto failed
)

:prepare
for %%D in (data logs backups uploads) do (
    if not exist "%DIR%\%%D" (
        mkdir "%DIR%\%%D"
        if errorlevel 1 goto failed
    )
)
if exist "%DIR%\data\xianyu_data.db" (
    echo Existing database found. Keep using your current login password.
    echo Editing ADMIN_PASSWORD does not reset an existing account.
)

:start
docker container inspect "%CONTAINER%" >nul 2>&1
if errorlevel 1 goto create
echo Stopping the previous container. Persistent data will be kept ...
docker stop "%CONTAINER%" >nul
if errorlevel 1 goto failed
docker rm "%CONTAINER%" >nul
if errorlevel 1 goto failed

:create
docker run -d --name "%CONTAINER%" --pull never ^
    --label "xianyu_release_dir=%DIR%" ^
    --restart unless-stopped ^
    -p "127.0.0.1:%WEB_PORT%:8080" ^
    -v "%DIR%\data:/app/data" ^
    -v "%DIR%\logs:/app/logs" ^
    -v "%DIR%\backups:/app/backups" ^
    -v "%DIR%\uploads:/app/static/uploads" ^
    -e TZ=Asia/Shanghai ^
    -e DB_PATH=/app/data/xianyu_data.db ^
    -e ADMIN_USERNAME ^
    -e ADMIN_PASSWORD ^
    "%IMAGE%"
if errorlevel 1 goto failed

echo.
echo Container created. Waiting for the application health check ...
set "WAIT_COUNT=0"

:wait_ready
set "STATE="
set "HEALTH="
for /f "tokens=1,2" %%S in ('docker inspect --format "{{.State.Status}} {{.State.Health.Status}}" "%CONTAINER%"') do (
    set "STATE=%%S"
    set "HEALTH=%%T"
)
if not "%STATE%"=="running" goto startup_failed
if "%HEALTH%"=="healthy" goto ready
if "%HEALTH%"=="unhealthy" goto startup_failed
set /a WAIT_COUNT+=1 >nul
if %WAIT_COUNT% geq 60 goto startup_failed
timeout /t 3 /nobreak >nul
goto wait_ready

:ready
echo.
echo [OK] Application health check passed.
echo Open http://localhost:%WEB_PORT%/
if "%FRESH_DB%"=="1" (
    echo First-time setup: a new admin account was created from config.bat
    echo   username: %ADMIN_USERNAME%
    echo   password: %ADMIN_PASSWORD%
    echo Please change it after login (Settings - Account - Change password).
) else (
    echo username: %ADMIN_USERNAME%  (existing database - password unchanged)
)
echo Persistent data: "%DIR%"
echo Health check: http://localhost:%WEB_PORT%/health
echo Live logs follow. This window will stay open.
echo Closing this window does NOT stop the container. Use stop.bat to stop it.
docker logs --tail 100 --follow "%CONTAINER%"
echo.
echo Log streaming ended. Review the messages above before closing this window.
pause
exit /b 0

:configure_password
echo [ERROR] Before the first start, edit ADMIN_PASSWORD at the top of config.bat.
echo Replace CHANGE_ME with your own non-empty password. Username is admin.
goto failed

:startup_failed
echo.
echo [ERROR] The application did not become ready. Container status: %STATE%, health: %HEALTH%.
docker inspect --format "{{json .State}}" "%CONTAINER%"
echo.
echo Recent application logs:
docker logs --tail 100 "%CONTAINER%"
goto failed

:failed
echo.
echo [ERROR] Startup did not complete. Check the error above.
echo If port %WEB_PORT% is occupied, change WEB_PORT in this script.
pause
exit /b 1

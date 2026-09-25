@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "ROOT=%~dp0..\"
call "%~dp0config.bat"
set "DOCKERFILE=Dockerfile-cn"
if /i "%~1"=="official" set "DOCKERFILE=Dockerfile"

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

echo Building %IMAGE% from "%ROOT%" using %DOCKERFILE% ...
docker build --platform linux/amd64 -t "%IMAGE%" -f "%ROOT%%DOCKERFILE%" "%ROOT%."
if errorlevel 1 (
    echo [ERROR] Build failed. No image was exported.
    echo For base image TLS errors, check Docker Desktop proxy and DNS settings.
    echo Do not disable certificate verification.
    echo For dependency mirror errors only, try: build.bat official
    goto failed
)

call "%~dp0export.bat"
exit /b %errorlevel%

:failed
echo.
pause
exit /b 1

@echo off
rem ============================================================
rem  publish-exe / config.bat
rem  Central configuration for the Windows EXE release.
rem  build.bat reads this file; build.py consumes the variables.
rem
rem  IMPORTANT: keep this file pure ASCII. cmd decodes .bat files
rem  with the local code page (GBK on Chinese Windows); UTF-8 bytes
rem  would corrupt the parser and break quoting.
rem ============================================================

rem --- Identity ------------------------------------------------
rem APP_NAME    : exe file name, also used as the release folder name.
rem APP_VERSION : release version, appears in folder and zip name.
set "APP_NAME=XianyuButler"
set "APP_VERSION=1.0.0"

rem --- Runtime defaults ----------------------------------------
rem WEB_PORT : port the built-in web server listens on.
rem            Start.bat falls back to the next free port if busy.
rem ADMIN_USERNAME / ADMIN_PASSWORD : used ONLY on the first start,
rem            when data\xianyu_data.db does not exist yet. An existing
rem            database keeps its password - change it in the web UI.
set "WEB_PORT=8080"
set "ADMIN_USERNAME=admin"
set "ADMIN_PASSWORD=admin123"

rem --- Build options -------------------------------------------
rem BUILD_FRONTEND : auto = rebuild only when static\ is missing or older
rem                  than the frontend sources; 1 = always; 0 = never.
rem DOWNLOAD_BROWSER : 1 = download Playwright Chromium and ship it inside
rem                  the release. Required for slider captcha and QR login.
rem                  Adds ~400 MB to the release folder.
rem BUNDLE_NODE  : 1 = copy the local node.exe into the release so PyExecJS
rem                  works on machines without Node.js installed.
rem MAKE_ZIP     : 1 = create a .zip next to the release folder.
rem COMPILER     : auto | mingw64 | msvc   (C compiler used by Nuitka)
rem JOBS         : parallel compile jobs. Empty = up to 8 (safe default).
rem                 Compiling pandas/numpy peaks at 1-2 GB per gcc process;
rem                 set it too high on a many-core box and you get an OOM.
set "BUILD_FRONTEND=auto"
set "DOWNLOAD_BROWSER=1"
set "BUNDLE_NODE=1"
set "MAKE_ZIP=1"
set "COMPILER=auto"
set "JOBS=30"

rem --- Network -------------------------------------------------
rem PyPI mirror used while creating the build virtualenv.
rem Leave empty to use your own pip configuration.
set "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
set "PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright"

rem --- MinGW64 (optional) --------------------------------------
rem Only needed when MinGW64 lives outside C:\mingw64 or C:\MinGW64.
rem set "MINGW64_HOME=C:\mingw64"

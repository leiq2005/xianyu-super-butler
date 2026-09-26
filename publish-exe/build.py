# -*- coding: utf-8 -*-
"""publish-exe / build.py

把项目用 Nuitka 编译成 Windows 独立 EXE，并组装成可直接分发的目录。

由 build.bat 调用（build.bat 负责定位 Python 3.11 并加载 config.bat）。
所有用户输入都通过 config.bat 里的变量传入，本文件不解析命令行参数。

产出：
    publish-exe/release/<APP_NAME>-<APP_VERSION>/
        Start.bat          双击启动
        Stop.bat           停止
        <APP_NAME>.exe     Nuitka standalone 可执行程序
        static/            前端产物
        global_config.yml  配置
        data/ logs/ backups/   运行时数据（升级时保留）
        playwright/        内置 Chromium（滑块验证 / 扫码登录）
        node.exe           PyExecJS 需要的 JS 运行时
        使用说明.txt
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

# Windows 控制台默认是 GBK，直接 print 中文会 UnicodeEncodeError。
# 先切到 UTF-8 代码页再重新包装 stdout/stderr（与 Start.py 的做法一致）。
if sys.platform == "win32":
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# 子进程（pip / nuitka / playwright）默认按 cp936 解码文本，中文 Windows 上
# 一碰到 UTF-8 内容就崩。强制 UTF-8 模式让它们统一按 UTF-8 处理。
os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

ROOT = Path(__file__).resolve().parents[1]      # 项目根目录
HERE = Path(__file__).resolve().parent          # publish-exe
BUILD_ENV = HERE / ".buildenv"                  # 独立的构建虚拟环境
BUILD_DIR = HERE / ".build"                     # Nuitka 中间产物
RELEASE_DIR = HERE / "release"                  # 最终产出


def env(name, default=""):
    v = os.environ.get(name, "")
    v = v.strip()
    return v if v else default


APP_NAME = env("APP_NAME", "XianyuButler")
APP_VERSION = env("APP_VERSION", "1.0.0")
WEB_PORT = env("WEB_PORT", "8080")
ADMIN_USERNAME = env("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = env("ADMIN_PASSWORD", "admin123")

BUILD_FRONTEND = env("BUILD_FRONTEND", "auto").lower()
DOWNLOAD_BROWSER = env("DOWNLOAD_BROWSER", "1") == "1"
BUNDLE_NODE = env("BUNDLE_NODE", "1") == "1"
MAKE_ZIP = env("MAKE_ZIP", "1") == "1"
COMPILER = env("COMPILER", "auto").lower()
# 编译 pandas / numpy 时单个 gcc 进程峰值能到 1-2 GB，核数越多越容易 OOM。
# 默认最多 8 个并行，想更快就在 config.bat 里把 JOBS 调大。
JOBS = env("JOBS", "") or str(min(os.cpu_count() or 4, 8))
PIP_INDEX_URL = env("PIP_INDEX_URL", "")
PLAYWRIGHT_HOST = env("PLAYWRIGHT_DOWNLOAD_HOST", "https://cdn.npmmirror.com/binaries/playwright")
MINGW64_HOME = env("MINGW64_HOME", "")

OUT = RELEASE_DIR / f"{APP_NAME}-{APP_VERSION}"


def log(msg=""):
    print(msg, flush=True)


def step(msg):
    log("")
    log("=" * 66)
    log(f"  {msg}")
    log("=" * 66)


def fail(msg):
    log("")
    log(f"[ERROR] {msg}")
    sys.exit(1)


def run(cmd, cwd=None, environ=None, check=True):
    printable = " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd)
    log(f"  $ {printable}")
    result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=environ)
    if check and result.returncode != 0:
        fail(f"命令失败，退出码 {result.returncode}：{printable}")
    return result.returncode


def sanitized_requirements(source, dest_name):
    """生成一份纯 ASCII 的 requirements 副本给 pip 用。

    pip 读 requirements 文件时先 BOM 嗅探，失败就按 locale 解码。中文 Windows
    的 locale 是 cp936，项目 requirements 里的中文注释是 UTF-8，于是直接
    UnicodeDecodeError。剥掉注释只留依赖行即可绕开，不必改项目文件。
    """
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    kept, dropped = [], []
    for raw in source.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if all(ord(ch) < 128 for ch in line):
            kept.append(line)
        else:
            dropped.append(line)

    if dropped:
        log(f"  [WARN] {source.name} 中有 {len(dropped)} 行含非 ASCII 字符，已忽略：")
        for line in dropped:
            log(f"         {line}")

    dest = BUILD_DIR / dest_name
    dest.write_text("\n".join(kept) + "\n", encoding="ascii")
    log(f"  生成 pip 用的 ASCII 清单：{dest}")
    return dest


# ------------------------------------------------------------------
# 1. 构建虚拟环境
# ------------------------------------------------------------------
def ensure_build_env():
    """创建 publish-exe/.buildenv 并安装运行依赖 + Nuitka。

    用独立虚拟环境是为了不污染开发机上的 Python：Nuitka 需要装进同一个
    解释器环境，才能正确分析 site-packages 里的包。
    """
    step("1/7 准备构建环境")

    venv_py = BUILD_ENV / "Scripts" / "python.exe"
    if not venv_py.exists():
        log("  创建虚拟环境 .buildenv ...")
        run([sys.executable, "-m", "venv", str(BUILD_ENV)])
        if not venv_py.exists():
            fail("虚拟环境创建失败，请确认 Python 安装完整（含 venv 模块）")
    else:
        log("  复用已有虚拟环境 .buildenv")

    index_args = ["-i", PIP_INDEX_URL] if PIP_INDEX_URL else []
    base_pip = [str(venv_py), "-m", "pip", "install", "--disable-pip-version-check"]

    # 老版本 pip 解析部分新包元数据会出问题，先升一次；失败不影响后续。
    log("  升级 pip ...")
    run(base_pip + index_args + ["--upgrade", "pip"], check=False)

    reqs = sanitized_requirements(ROOT / "requirements.txt", "requirements.txt")
    build_reqs = sanitized_requirements(ROOT / "requirements-build.txt", "requirements-build.txt")

    log("  安装依赖（首次会下载几百 MB，请耐心）...")
    run(base_pip + index_args + ["-r", str(reqs)])
    log("  安装打包工具链（Nuitka）...")
    run(base_pip + index_args + ["-r", str(build_reqs)])

    return venv_py


# ------------------------------------------------------------------
# 2. 前端产物
# ------------------------------------------------------------------
def frontend_needs_build():
    static_index = ROOT / "static" / "index.html"
    frontend = ROOT / "frontend"

    if not frontend.exists():
        return False
    if BUILD_FRONTEND == "0":
        return False
    if BUILD_FRONTEND == "1":
        return True
    if not static_index.exists():
        return True

    # 只比较源码，必须剪掉 node_modules，否则几万个文件会把这一趟拖到分钟级
    ignored = {"node_modules", "dist", ".vite"}
    latest = 0.0
    for cur, dirs, files in os.walk(frontend):
        dirs[:] = [d for d in dirs if d not in ignored]
        for name in files:
            try:
                mtime = os.stat(os.path.join(cur, name)).st_mtime
            except OSError:
                continue
            latest = max(latest, mtime)
    return latest > static_index.stat().st_mtime


def build_frontend():
    step("2/7 前端产物")
    frontend = ROOT / "frontend"

    if not frontend_needs_build():
        log("  static/ 已是最新，跳过构建")
        return

    if shutil.which("npm") is None:
        if (ROOT / "static" / "index.html").exists():
            log("  [WARN] 未找到 npm，沿用已有的 static/（前端可能不是最新）")
            return
        fail("未找到 npm，且 static/ 里没有构建产物。请先安装 Node.js 后重试。")

    # vite.config.ts 的 outDir 默认是 ../static，直接产出到项目 static/
    if (frontend / "package-lock.json").exists():
        run(["npm", "ci"], cwd=frontend)
    else:
        run(["npm", "install"], cwd=frontend)
    run(["npm", "run", "build"], cwd=frontend)

    if not (ROOT / "static" / "index.html").exists():
        fail("前端构建后仍未找到 static/index.html")


# ------------------------------------------------------------------
# 3. Nuitka 编译
# ------------------------------------------------------------------
def compiler_flags():
    if COMPILER == "msvc":
        return ["--msvc=latest"]
    if COMPILER == "mingw64":
        return ["--mingw64"]

    # auto
    if shutil.which("cl"):
        log("  检测到 MSVC (cl.exe)，使用 --msvc=latest")
        return ["--msvc=latest"]
    for default_mingw in (MINGW64_HOME, r"C:\mingw64", r"C:\MinGW64"):
        if default_mingw and (Path(default_mingw) / "bin" / "gcc.exe").exists():
            log(f"  检测到 MinGW64: {default_mingw}")
            return ["--mingw64"]
    log("  未检测到本地编译器，交给 Nuitka 自动下载 MinGW64")
    return ["--mingw64"]


def utils_modules():
    """utils/ 没有 __init__.py（隐式命名空间包），显式列出模块更稳。"""
    utils_dir = ROOT / "utils"
    if not utils_dir.exists():
        return []
    mods = []
    for py_file in sorted(utils_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        mods.append(f"utils.{py_file.stem}")
    return mods


def existing_modules(venv_py, names):
    """只保留构建环境里真实存在的模块。

    --include-module 指向不存在的模块时 Nuitka 会直接报错退出，而
    uvicorn[standard] 这类可选依赖在不同平台上的安装结果并不一致。
    """
    code = (
        "import importlib.util, json, sys\n"
        "ok = []\n"
        "for name in json.load(sys.stdin):\n"
        "    try:\n"
        "        if importlib.util.find_spec(name) is not None:\n"
        "            ok.append(name)\n"
        "    except Exception:\n"
        "        pass\n"
        "print(json.dumps(ok))\n"
    )
    result = subprocess.run(
        [str(venv_py), "-c", code],
        input=json.dumps(names),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return list(names)
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except Exception:
        return list(names)


def nuitka_major(venv_py):
    result = subprocess.run(
        [str(venv_py), "-m", "nuitka", "--version"],
        capture_output=True, text=True,
    )
    head = (result.stdout or "").strip().splitlines()
    if not head:
        return 0
    for chunk in head[0].replace("Nuitka", "").strip().split("."):
        if chunk.isdigit():
            return int(chunk)
    return 0


def nuitka_supported_options(venv_py):
    """从 nuitka --help 里抓出所有受支持的选项名。

    Nuitka 大版本之间会增删开关（比如 3.x 起 --standalone 换成 --mode=，
    4.x 起带值参数必须用 = 形式）。先核对一遍，把不支持的剔除掉，总好过
    编译到一半因为一个过时开关直接退出。
    """
    result = subprocess.run(
        [str(venv_py), "-m", "nuitka", "--help"],
        capture_output=True, text=True,
    )
    text = (result.stdout or "") + (result.stderr or "")
    return set(re.findall(r"--[a-z0-9][a-z0-9-]*", text))


def filter_supported(cmd, supported):
    if not supported:
        return cmd, []
    kept, dropped = [], []
    for item in cmd:
        name = item.split("=", 1)[0]
        if item.startswith("--") and name not in supported:
            dropped.append(item)
            continue
        kept.append(item)
    return kept, dropped


def mode_flags(venv_py):
    """standalone 模式的写法在 Nuitka 3 起换成 --mode=standalone。"""
    major = nuitka_major(venv_py)
    if major == 0:
        log("  [WARN] 无法识别 Nuitka 版本，按 2.x 的参数写法处理")
        return ["--standalone"]
    log(f"  Nuitka 主版本：{major}")
    return ["--mode=standalone"] if major >= 3 else ["--standalone"]


def build_nuitka_cmd(venv_py):
    cmd = [
        str(venv_py), "-m", "nuitka",
        "--remove-output",
        "--assume-yes-for-downloads",
        # 带值的选项一律用 = 形式：新版 Nuitka 会拒绝 "--opt value"
        f"--output-dir={BUILD_DIR}",
        f"--output-filename={APP_NAME}.exe",
        # 让模块里的 __file__ 指向运行时的真实路径，app/config.py 的
        # PROJECT_ROOT 才能落在发布目录上
        "--file-reference-choice=runtime",
        "--include-package=app",
        "--include-package-data=playwright",
        "--include-package-data=patchright",
        "--include-package-data=PIL",
        "--nofollow-import-to=tests",
        "--nofollow-import-to=pytest",
        "--nofollow-import-to=tkinter",
        "--nofollow-import-to=IPython",
        f"--jobs={JOBS}",
        "--show-progress",
        "--show-memory",
    ]

    cmd += mode_flags(venv_py)
    cmd += compiler_flags()

    for mod in utils_modules():
        cmd.append(f"--include-module={mod}")

    # 这些是框架在运行时用字符串动态导入的，静态分析看不到，必须显式包含
    dynamic = (
        "multipart",                        # python-multipart，FastAPI 表单上传
        "email_validator",                  # pydantic EmailStr
        "jwt",                              # PyJWT
        "passlib.handlers.bcrypt",          # passlib 的 bcrypt 后端
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.httptools_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.lifespan.on",
        "uvicorn.logging",
    )
    present = set(existing_modules(venv_py, list(dynamic)))
    for mod in dynamic:
        if mod in present:
            cmd.append(f"--include-module={mod}")
        else:
            log(f"  跳过未安装的模块：{mod}")

    cmd.append(str(ROOT / "Start.py"))
    return cmd


def compile_with_nuitka(venv_py):
    step("3/7 Nuitka 编译（这一步最慢，通常 20-60 分钟）")
    log(f"  并行编译进程数：{JOBS}")

    cmd = build_nuitka_cmd(venv_py)

    supported = nuitka_supported_options(venv_py)
    cmd, dropped = filter_supported(cmd, supported)
    for item in dropped:
        log(f"  [WARN] 当前 Nuitka 不支持该选项，已忽略：{item}")

    if MINGW64_HOME:
        os.environ["PATH"] = str(Path(MINGW64_HOME) / "bin") + os.pathsep + os.environ.get("PATH", "")

    started = time.time()
    run(cmd, cwd=ROOT)
    log(f"  编译耗时 {int(time.time() - started) // 60} 分钟")


def nuitka_dist():
    dist = BUILD_DIR / "Start.dist"
    if not dist.exists():
        found = sorted(BUILD_DIR.glob("*.dist"))
        if not found:
            fail(f"未找到 Nuitka 产物目录：{BUILD_DIR}")
        dist = found[0]
    return dist


# ------------------------------------------------------------------
# 4. 组装发布目录
# ------------------------------------------------------------------
def assemble(dist):
    step("4/7 组装发布目录")

    if OUT.exists():
        log(f"  清理旧产物 {OUT}")
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)

    log(f"  复制 Nuitka 产物 -> {OUT}")
    shutil.copytree(dist, OUT, dirs_exist_ok=True)

    exe = OUT / f"{APP_NAME}.exe"
    if not exe.exists():
        fail(f"发布目录中缺少 {exe.name}")

    # 前端静态资源：app/config.py 的 PROJECT_ROOT 指向 exe 所在目录
    static_src = ROOT / "static"
    if static_src.exists():
        log("  复制 static/")
        shutil.copytree(static_src, OUT / "static", dirs_exist_ok=True)
    else:
        fail("缺少 static/ 目录，请先构建前端")

    cfg = ROOT / "global_config.yml"
    if cfg.exists():
        log("  复制 global_config.yml")
        shutil.copy2(cfg, OUT / "global_config.yml")
    else:
        fail("缺少 global_config.yml")

    for folder in ("data", "logs", "backups"):
        (OUT / folder).mkdir(exist_ok=True)
    (OUT / "static" / "uploads" / "images").mkdir(parents=True, exist_ok=True)

    return exe


# ------------------------------------------------------------------
# 5. 内置浏览器
# ------------------------------------------------------------------
def playwright_cache_dirs():
    candidates = []
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        candidates.append(Path(localappdata) / "ms-playwright")
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "ms-playwright")
    candidates.append(Path.home() / ".cache" / "ms-playwright")
    return [c for c in candidates if c.exists()]


def ship_browsers(venv_py):
    step("5/7 内置 Chromium 浏览器")
    if not DOWNLOAD_BROWSER:
        log("  已跳过（DOWNLOAD_BROWSER=0）。滑块验证与扫码登录将依赖系统浏览器。")
        return

    environ = os.environ.copy()
    environ["PLAYWRIGHT_DOWNLOAD_HOST"] = PLAYWRIGHT_HOST
    log(f"  下载 Chromium（镜像 {PLAYWRIGHT_HOST}）...")
    run([str(venv_py), "-m", "playwright", "install", "chromium"], environ=environ, check=False)

    target = OUT / "playwright"
    copied = 0
    for cache in playwright_cache_dirs():
        for browser_dir in sorted(cache.glob("chromium*")):
            if not browser_dir.is_dir():
                continue
            dest = target / browser_dir.name
            if dest.exists():
                continue
            log(f"  复制 {browser_dir.name}")
            target.mkdir(parents=True, exist_ok=True)
            shutil.copytree(browser_dir, dest, dirs_exist_ok=True)
            copied += 1

    if copied == 0:
        log("  [WARN] 未找到 Chromium，发布包将不含浏览器。")
        log("         滑块验证、扫码登录会退化为使用系统 Chrome/Edge。")
    else:
        log(f"  已内置 {copied} 个浏览器目录 -> {target}")


# ------------------------------------------------------------------
# 6. Node（PyExecJS）
# ------------------------------------------------------------------
def ship_node():
    step("6/7 Node.js 运行时（PyExecJS）")
    if not BUNDLE_NODE:
        log("  已跳过（BUNDLE_NODE=0）")
        return

    node = shutil.which("node")
    if not node:
        log("  [WARN] 本机未找到 node.exe，未内置。目标机器需自行安装 Node.js，")
        log("         否则依赖 JS 计算的接口会失败。")
        return

    node_path = Path(node).resolve()
    # npm 的 shim 在 Windows 上是 node.exe 的软链/脚本，确认拿到的是真身
    if node_path.suffix.lower() != ".exe":
        log(f"  [WARN] {node_path} 不是可执行文件，跳过")
        return

    shutil.copy2(node_path, OUT / "node.exe")
    log(f"  已内置 node.exe（{node_path.stat().st_size // 1024 // 1024} MB）")


# ------------------------------------------------------------------
# 7. 启动脚本 + 说明 + 压缩包
# ------------------------------------------------------------------
START_BAT = r'''@echo off
setlocal EnableExtensions DisableDelayedExpansion
rem ============================================================
rem  Start.bat - double click to run Xianyu Butler.
rem  Keep this window open; closing it stops the program.
rem ============================================================

cd /d "%~dp0"

rem Put the bundled node.exe and the runtime folder on PATH (PyExecJS).
set "PATH=%~dp0;%PATH%"

if not defined ADMIN_USERNAME set "ADMIN_USERNAME=__ADMIN_USERNAME__"
if not defined ADMIN_PASSWORD set "ADMIN_PASSWORD=__ADMIN_PASSWORD__"
if not defined API_PORT set "API_PORT=__WEB_PORT__"

rem --- find a free port -----------------------------------------
:check_port
set "PORT_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /C:":%API_PORT% " ^| findstr /I "LISTENING"') do (
    if not defined PORT_PID set "PORT_PID=%%P"
)
if not defined PORT_PID goto port_ok
set /a API_PORT+=1
if %API_PORT% gtr 8100 goto port_failed
goto check_port

:port_failed
echo [ERROR] No free port found (8080-8100). Close other programs and retry.
pause
exit /b 1

:port_ok
echo.
echo   Xianyu Butler is starting ...
echo   Address : http://localhost:%API_PORT%/
echo   Admin   : %ADMIN_USERNAME% / %ADMIN_PASSWORD%   (first start only)
echo   Data    : %~dp0data
echo.
echo   Keep this window open. Run Stop.bat or close it to shut down.
echo.

rem Open the browser after the server had time to bind the port.
start "" /min cmd /c "timeout /t 8 /nobreak >nul & start http://localhost:%API_PORT%/"

__EXE_NAME__.exe

echo.
echo Server stopped.
pause
exit /b 0
'''

STOP_BAT = r'''@echo off
setlocal EnableExtensions DisableDelayedExpansion
taskkill /IM __EXE_NAME__.exe /F >nul 2>&1
if errorlevel 1 (
    echo No running instance found.
) else (
    echo Stopped.
)
pause
exit /b 0
'''

README_TXT = """闲鱼智控 - Windows 独立运行版
================================================

【怎么启动】
  双击 Start.bat
  等待窗口出现服务日志后，浏览器会自动打开管理后台。
  若没有自动打开，请手动访问：http://localhost:{port}/

  默认管理员账号：{user} / {passwd}
  说明：这个密码只在“第一次启动、data 目录还没有数据库”时生效。
        之后请在后台「设置」里修改密码，改这里的文件不会生效。

  关闭窗口或双击 Stop.bat 即可停止程序。

【目录里都是什么】
  {exe}.exe        主程序，双击也可以启动（但看不到启动提示）
  Start.bat        推荐的启动方式（自动挑空闲端口、自动开浏览器）
  Stop.bat         停止程序
  static/          前端页面资源
  global_config.yml  配置文件（一般不需要改）
  data/            数据库，账号、商品、订单都在这里 —— 备份请复制整个目录
  logs/            日志
  backups/         数据库自动备份
  playwright/      内置 Chromium 浏览器（滑块验证、扫码登录用）
  node.exe         内置的 JavaScript 运行时（程序内部计算需要）

【升级怎么保留数据】
  把旧目录里的 data/ 整个复制到新版本的 data/ 即可。
  不要只复制单个 .db 文件，也不要覆盖新版本的 global_config.yml。

【端口被占用】
  Start.bat 会自动往后找空闲端口（8080 开始）。
  实际端口请看窗口里打印的 Address。

【常见问题】
  1. 杀毒软件报毒 / 拦截
     Nuitka 打包会产生大量 dll，容易被误报。把整个目录加入白名单。

  2. 滑块验证失败、扫码登录打不开
     说明内置 Chromium 没能启动。确认 playwright/ 目录完整，
     或者在机器上安装 Chrome / Edge 作为备用。

  3. 首次启动很慢
     Nuitka 编译的独立程序首次运行要解压并校验依赖，属正常现象。

  4. 想换端口 / 改管理员密码
     用记事本打开 Start.bat，修改开头的 ADMIN_PASSWORD / API_PORT。

版本：{version}
构建时间：{built}
"""


def write_launchers():
    step("7/7 启动脚本与说明")

    start_bat = (START_BAT
                 .replace("__ADMIN_USERNAME__", ADMIN_USERNAME)
                 .replace("__ADMIN_PASSWORD__", ADMIN_PASSWORD)
                 .replace("__WEB_PORT__", WEB_PORT)
                 .replace("__EXE_NAME__", APP_NAME))
    # bat 必须是 ASCII，中文一律不放进 bat，避免 GBK 代码页下的解析问题
    (OUT / "Start.bat").write_text(start_bat, encoding="ascii")
    (OUT / "Stop.bat").write_text(STOP_BAT.replace("__EXE_NAME__", APP_NAME), encoding="ascii")

    readme = README_TXT.format(
        port=WEB_PORT,
        user=ADMIN_USERNAME,
        passwd=ADMIN_PASSWORD,
        exe=APP_NAME,
        version=APP_VERSION,
        built=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    # BOM 保证双击用记事本打开时中文不乱码
    (OUT / "使用说明.txt").write_text("\ufeff" + readme, encoding="utf-8")

    log(f"  已生成 Start.bat / Stop.bat / 使用说明.txt")


def zip_release():
    if not MAKE_ZIP:
        return
    zip_path = RELEASE_DIR / f"{APP_NAME}-{APP_VERSION}.zip"
    if zip_path.exists():
        zip_path.unlink()

    log(f"  打包 {zip_path.name} ...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        for file_path in OUT.rglob("*"):
            if file_path.is_dir():
                continue
            zf.write(file_path, file_path.relative_to(RELEASE_DIR))
    size_mb = zip_path.stat().st_size // 1024 // 1024
    log(f"  完成：{zip_path}（{size_mb} MB）")


def main():
    log("")
    log("##############################################################")
    log(f"  闲鱼智控 EXE 发布构建  {APP_NAME} {APP_VERSION}")
    log(f"  输出目录：{OUT}")
    log("##############################################################")

    if sys.platform != "win32":
        fail("该脚本只能在 Windows 上运行（Nuitka 需要交叉编译配置）")

    venv_py = ensure_build_env()
    build_frontend()
    compile_with_nuitka(venv_py)
    assemble(nuitka_dist())
    ship_browsers(venv_py)
    ship_node()
    write_launchers()
    zip_release()

    log("")
    log("=" * 66)
    log("  构建完成")
    log(f"  发布目录：{OUT}")
    log(f"  启动方式：双击 {OUT / 'Start.bat'}")
    log("=" * 66)


if __name__ == "__main__":
    main()

# publish-exe：Windows 独立 EXE 发布

把项目用 **Nuitka** 编译成不依赖 Python 的独立程序，产出目录可直接拷给不懂技术的 Windows 用户，双击 `Start.bat` 即用。

与 `publish/`（Docker 镜像路线）完全独立，互不干扰。

## 目录结构

```
publish-exe/
  config.bat    发布参数（版本、端口、管理员密码、是否内置浏览器等）
  build.bat     一键打包入口，双击即可
  build.py      实际构建逻辑（由 build.bat 调用）
  README.md     本文件
  .buildenv/    自动创建的构建虚拟环境（首次运行时生成）
  .build/       Nuitka 中间产物
  release/      最终产出
```

## 使用方式

1. 编辑 `config.bat`，至少把 `ADMIN_PASSWORD` 改成你自己的密码。
2. 双击 `build.bat`。
3. 等待（首次约 20–60 分钟，主要耗时在 Nuitka 编译 pandas/numpy/playwright）。
4. 产出：

```
publish-exe/release/XianyuButler-1.0.0/
  Start.bat          双击启动
  Stop.bat           停止
  XianyuButler.exe   主程序
  static/            前端产物
  global_config.yml  配置
  data/ logs/ backups/   运行时数据
  playwright/        内置 Chromium（滑块验证 / 扫码登录）
  node.exe           内置 JS 运行时（PyExecJS 需要）
  使用说明.txt
```

把整个文件夹压缩后发给用户即可（`MAKE_ZIP=1` 时会自动生成 zip）。

## 构建机前置条件

| 依赖 | 说明 |
|---|---|
| Python 3.11 | `build.bat` 会自动找 `python` / `py -3.11` |
| Node.js | 构建前端用；只在 `static/` 过期时才需要 |
| C 编译器 | 优先用 `C:\mingw64` 的 MinGW64；没有则 Nuitka 自动下载。也可用 MSVC（`COMPILER=msvc`） |
| 网络 | 下载 PyPI 依赖、Nuitka、Chromium |

目标机器（用户机器）**不需要**任何依赖，也不需要 Docker / WSL / Python / Node。

## config.bat 主要参数

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_NAME` | `XianyuButler` | exe 名与发布目录名前缀 |
| `APP_VERSION` | `1.0.0` | 版本号 |
| `WEB_PORT` | `8080` | 服务端口，`Start.bat` 占用时自动往后找 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / `admin123` | **仅在首次建库时生效** |
| `BUILD_FRONTEND` | `auto` | `auto` 按 mtime 判断；`1` 强制；`0` 永不 |
| `DOWNLOAD_BROWSER` | `1` | 是否内置 Chromium，关掉可省约 400 MB |
| `BUNDLE_NODE` | `1` | 是否内置 `node.exe` |
| `MAKE_ZIP` | `1` | 是否额外生成 zip |
| `COMPILER` | `auto` | `auto` / `mingw64` / `msvc` |
| `JOBS` | 空 | 并行编译数，空 = CPU 核心数 |
| `PIP_INDEX_URL` | 清华源 | 留空则用本机 pip 配置 |
| `PLAYWRIGHT_DOWNLOAD_HOST` | npmmirror | Chromium 下载镜像 |

## 源码侧为打包做的适配

三处改动都做了条件判断，不影响源码运行和 Docker 路线：

1. `app/config.py`、`app/reply_server.py`：`PROJECT_ROOT` 在 `sys.frozen` 时取 exe 所在目录，而不是 `__file__` 的上两级（打包后源码树不存在）。
2. `Start.py`：打包后跳过前端构建（机器上没有 npm 和 frontend 源码）。
3. `Start.py`：打包后 uvicorn 直接接收 ASGI 应用对象，而不是字符串 `"app.reply_server:app"`（字符串形式依赖 importlib 按文件名查找模块）。

## 已知限制

- **体积大**：内置 Chromium 后发布目录通常 600 MB–1 GB。不需要滑块验证/扫码登录时可设 `DOWNLOAD_BROWSER=0`。
- **杀软误报**：Nuitka standalone 会产出大量 dll，容易被误判，需加入白名单。
- **升级保留数据**：让用户复制旧版 `data/` 到新版即可，不要覆盖 `global_config.yml`。
- **Nuitka 版本**：`requirements-build.txt` 里是 `nuitka>=2.7`，不同小版本的编译参数可能有差异，报错时先看 Nuitka 自身提示。

# AgentHub Tauri Desktop

本目录提供 AgentHub 的本地桌面发行版。Tauri 2 承载编译后的 React 前端，并在启动时自动运行打包的 FastAPI sidecar；用户不需要分别启动前端、后端、PostgreSQL 或 Redis。

## 本地数据与进程

- sidecar 每次选择空闲的本机端口，仅监听 `127.0.0.1`。
- 桌面端默认使用单一本机身份，启动后直接进入工作台，不提供注册、登录、改密和用户权限管理。
- 每次启动会生成临时桌面会话凭证，只有当前 Tauri 窗口能访问本地 API；凭证不会写入数据库或前端存储。
- 数据库使用 SQLite，文件、日志和稳定加密密钥保存在系统的 AgentHub AppLocalData 目录。
- 首次启动自动执行 Alembic migration，退出桌面应用时终止 sidecar。
- 应用采用单实例运行；重复启动只聚焦已有窗口，避免两个进程同时写 SQLite。
- Provider API Key 仍由后端加密保存，不写入 Tauri 配置或命令行。

升级已有桌面数据时，如果数据库里只有一个有效用户，应用会将其接管为本机身份，保留其会话、模型凭据和工作区；新安装则自动创建本机身份。Web 与 Docker 部署仍使用原有多用户认证，不受此模式影响。

Electron 客户端已经移除。Tauri V1 聚焦“安装后双击即用”；旧桌面壳的托盘、全局快捷键和截图浮窗暂未迁移。
Codex、Claude Code、Docker、Office 转换器等外部程序仍需用户单独安装；桌面包不会私自携带或安装这些工具。

## 开发

需要 Python 3.11、`uv`、Node.js 20+、`pnpm`、Rust MSVC toolchain 和 WebView2。首次准备：

```powershell
cd backend
uv sync --extra dev
cd ../frontend
pnpm install
cd ../desktop-client
pnpm install
```

启动 Tauri 开发窗口：

```powershell
cd desktop-client
pnpm dev
```

首次执行会通过 PyInstaller 构建 Python sidecar，后续只在后端输入更新时重建。前端由 Tauri 的 `beforeDevCommand` 自动启动。

## Windows 安装包

```powershell
cd desktop-client
pnpm build:win
```

NSIS 安装包输出到 `desktop-client/src-tauri/target/release/bundle/nsis/`。安装器按当前用户安装，并嵌入 WebView2 bootstrapper。sidecar 中包含完整 Python 运行时和后端依赖，因此安装包体积主要由 Python、ONNX Runtime 和文档处理依赖决定。

## 关键目录

```text
src-tauri/src/lib.rs         sidecar 生命周期与 Tauri 窗口
src-tauri/tauri.conf.json    构建、CSP、安装包和外部二进制配置
scripts/build-sidecar.ps1    PyInstaller sidecar 构建
scripts/prepare-tauri.ps1    前端与 sidecar 打包准备
../backend/desktop_entry.py  桌面环境、密钥、迁移与 Uvicorn 入口
```

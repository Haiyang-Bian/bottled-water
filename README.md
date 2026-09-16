# AgentHub

AgentHub 是一个围绕多智能体协作运行时构建的实验性应用。项目保留了可以实际使用的
Web 界面和 Windows 桌面端，用来验证对话、调度、工具调用、工作流和运行记录能否在
同一套系统中稳定协作。

这个仓库仍在持续开发。它适合本地试用、研究和继续实现 Runtime，不应被视为已经完成
生产验证的通用 Agent 平台。

## 目前可以做什么

- 注册用户并创建单智能体或多智能体会话。
- 配置 DeepSeek、Ark 或其他 OpenAI 兼容模型服务。
- 为 Agent 选择模型、工具、技能和 MCP 服务。
- 使用流式对话、思考展示、工具调用和取消操作。
- 创建并运行简单工作流，查看执行状态与产物。
- 通过持久运行记录补齐断线期间的可见事件。
- 使用本地 CLI 跨目录恢复任务，保存和采纳基础记忆。
- 查找已知资源与旧实验；0.2.3 支持直接调用本机 Python/uv/Git、项目虚拟环境和正常网络，软件登记成为可选入口，详见[资源与软件](./docs/resources.md)。

具体能力和已知限制以[当前实现状态](./docs/implementation-status.md)为准。归档交接、源码与 wheel 对应关系、尚未合入的 PR 及本地证据位置见 [0.2.3 归档记录](./docs/operations/archive-handoff-0.2.3.md)。

## 运行方式

### 本地 CLI

```powershell
uv tool install --python 3.11 ".[cli]"
agenthub init
cd D:\Work\my-project
agenthub
# 退出后续聊
agenthub --continue
```

0.2.3 以普通用户执行，可直接跨目录操作、调用项目 Python/本机软件并联网，无需逐目录信任或管理员初始化。配置与记录默认保存在 `%USERPROFILE%\.agenthub`。从任意目录用 `-r` 找回任务，`/cd` 修改保存位置；任务历史保持独立。LPAC 强隔离暂缓，旧受限任务须显式转换。详见 [CLI 使用说明](./docs/cli.md)和[本版验收](./docs/acceptance/native-user-experience-0.2.3.md)。

### Windows 桌面端

桌面端会自动启动本地后端、初始化 SQLite 数据库并执行迁移，并以本机单用户身份直接进入
工作台；不需要注册账号，也不需要分别打开前后端：

```powershell
cd desktop-client
pnpm install
pnpm build:win
```

安装包生成在 `desktop-client/src-tauri/target/release/bundle/nsis/`。开发模式使用
`pnpm dev`。完整说明见 [desktop-client/README.md](./desktop-client/README.md)。

### 源码开发

需要 Python 3.11、`uv`、Node.js 20+ 和 `pnpm`。

```powershell
cd backend
uv sync --package agenthub-backend --extra dev
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

另开终端启动前端：

```powershell
cd frontend
pnpm install
pnpm dev
```

浏览器访问 `http://localhost:5173`。首次使用时可在设置中添加模型 Provider；API Key
只写入加密凭据，不会回填到界面。

### Docker

```powershell
Copy-Item docker/env.example docker/.env
docker compose --env-file docker/.env -f docker/docker-compose.yml up --build
```

启动前请替换 `docker/.env` 中的占位密钥和密码；示例 `WEB_PORT=80` 对应 `http://localhost`，如修改端口则使用配置值。本版未重新执行 Docker 验收。

## 仓库结构

- `src/`：共享契约、Runtime、模型、子系统、本机驱动和 CLI；由根级 `pyproject.toml` 发行。
- `tests/`：共享系统和 CLI 验证。
- `backend/`：FastAPI 服务、Runtime 集成、数据库模型和迁移。
- `frontend/`：React Web 应用。
- `desktop-client/`：Tauri Windows 客户端和本地后端打包脚本。
- `mobile-client/`：移动端 PWA/Capacitor 客户端。
- `docs/architecture/`：按内核、子系统、驱动与宿主划分的系统目标架构、模块职责和迁移验收。
- `docs/runtime/`：Runtime 的目标架构、不变量、现状和演化记录。
- `docker/`：容器化运行配置。
- `scripts/`：分组测试和仓库工具。

## 测试

测试必须选择模块和类型；全量运行需要显式使用 `-All`。

```powershell
.\scripts\run-tests.ps1 -List
.\scripts\run-tests.ps1 -Stack system -Module cli -Type integration
.\scripts\run-tests.ps1 -Stack backend -Module runtime -Type unit
.\scripts\run-tests.ps1 -Stack frontend -Module chat -Type unit
```

真实模型测试属于 `live` 分组，只在显式提供对应 API Key 时运行。

## 文档

- [文档索引](./docs/README.md)
- [CLI 安装与使用手册](./docs/cli.md)
- [0.2.3 升级与回退](./docs/releases/0.2.3.md)
- [0.2.3 归档与后续交接](./docs/operations/archive-handoff-0.2.3.md)
- [系统架构与拆分设计](./docs/architecture/README.md)
- [子系统与模块职责](./docs/architecture/subsystems.md)
- [新旧架构差异与迁移验收](./docs/architecture/migration.md)
- [Runtime 文档](./docs/runtime/README.md)
- [开发指南](./docs/development-guide.md)
- [安全与模型供应商](./docs/security-and-model-providers.md)
- [贡献指南](./AGENTS.md)

## 项目边界

当前已交付独立 CLI wheel，并维护共享 Runtime、事件日志及 Web/桌面执行链。0.2.3 的重点是原生本机使用体验；LPAC 强隔离暂缓。桌面 sidecar 构建和启动已回归，不代表完整安装器及所有 UI 已验收。部分文档转换、外部编码 Agent 和浏览器能力仍依赖单独安装的本机工具。

后续拆分以 Runtime 为内核，将执行、模型、上下文、工具、MCP、Skill、工作空间等能力
整理为共享子系统，由 AgentHub、本地 CLI 和未来评测宿主组装。CLI MVP 已完成首条公共执行链迁移，
MCP/Skill/Workflow 等领域仍待按实际用途抽取；分阶段验收以复用能力与依赖边界为准，不以文件数量为准。

本项目使用 [Apache License 2.0](./LICENSE)。

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

具体能力和已知限制以[当前实现状态](./docs/implementation-status.md)为准。

## 运行方式

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
uv sync --extra dev
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

启动前请替换 `docker/.env` 中的占位密钥和密码，随后访问 `http://localhost:8080`。

## 仓库结构

- `backend/`：FastAPI 服务、Runtime 集成、数据库模型和迁移。
- `frontend/`：React Web 应用。
- `desktop-client/`：Tauri Windows 客户端和本地后端打包脚本。
- `docs/runtime/`：Runtime 的目标架构、不变量、现状和演化记录。
- `docker/`：容器化运行配置。
- `scripts/`：分组测试和仓库工具。

## 测试

测试必须选择模块和类型；全量运行需要显式使用 `-All`。

```powershell
.\scripts\run-tests.ps1 -List
.\scripts\run-tests.ps1 -Stack backend -Module runtime -Type unit
.\scripts\run-tests.ps1 -Stack frontend -Module chat -Type unit
```

真实模型测试属于 `live` 分组，只在显式提供对应 API Key 时运行。

## 文档

- [文档索引](./docs/README.md)
- [Runtime 文档](./docs/runtime/README.md)
- [开发指南](./docs/development-guide.md)
- [安全与模型供应商](./docs/security-and-model-providers.md)
- [贡献指南](./AGENTS.md)

## 项目边界

当前工作的重点是 Runtime 生命周期、事件日志、调度策略和桌面端可用性，而不是继续扩张
社区或平台功能。Windows 桌面端是首个本地发行目标；部分文档转换、外部编码 Agent 和
浏览器能力仍依赖单独安装的本机工具。

本项目使用 [Apache License 2.0](./LICENSE)。

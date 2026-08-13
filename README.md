# AgentHub - AI Agent 开发者交流社区

![状态](https://img.shields.io/badge/状态-积极开发-yellow)
![TypeScript](https://img.shields.io/badge/TypeScript-5.3-3178C6?logo=typescript&logoColor=white)
![Node.js](https://img.shields.io/badge/Node.js-20%2B-339933?logo=node.js&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)

> 面向 AI Agent 开发者、研究者和爱好者的综合性协同平台，把聊天、群协作、智能体配置、模型接入、工具链、工作流编排、文件与成果预览、审计和部署整合在同一产品界面中。
## Demo 演示视频

[点击下载演示视频](https://github.com/jiajiajiaxr/bottled-water/releases/latest)
## 功能亮点

- 🤖 **Agent 展示** - 发布、评分、评论、版本管理、分类检索
- 💬 **社区交流** - 讨论区、问答、投票、嵌套评论
- 📨 **实时通讯** - 私信、WebSocket 在线状态、通知推送
- 🏆 **积分系统** - 等级体系、排行榜、每日签到
- 📝 **内容管理** - 博客文章、资源分享、活动日历
- 🔎 **全文搜索** - MeiliSearch 集成，支持回退检索
- 🛡️ **安全合规** - XSS 防护、速率限制、数据导出与删除
- 📊 **后台管理** - 仪表盘、用户管理、内容审核、数据统计

## 项目结构

- `backend/src`：FastAPI、SQLAlchemy、Alembic、运行时服务、工具、技能、MCP、文件、成果和工作流执行。
- `frontend/src`：React 18、TypeScript、Vite、Ant Design、Zustand、聊天工作台、平台面板、预览面板、工作流画布和文档页。
- `docker/`：一键本地部署，包含 nginx、后端、PostgreSQL 和 Redis。
- `desktop-client/`：轻量 Electron 桌面端，封装 Web 应用并补充托盘、全局快捷键、快速输入、通知和独立窗口。
- `mobile-client/`：PWA / Capacitor 移动端，用于轻量会话、成果核验、进度跟踪和安装流程。

## 当前能力

- 用户注册与登录、数据库 RBAC、管理员引导、工作区、项目、会话列表、归档、置顶和分类流转。
- 单人聊天与多智能体群聊，支持 SSE / WebSocket 流式输出。
- 模型供应商配置，支持 Ark、OpenAI 兼容接口和 DeepSeek V4 Flash/Pro；DeepSeek 支持流式思考和工具调用续轮。
- 智能体目录，支持模型、工具、技能、MCP 和循环策略权限配置。
- 内置工具覆盖文件、成果、沙箱执行、浏览器预览、部署预览、数据库检查、安全审计、测试和外部编码智能体。
- Codex 与 Claude Code 外部编码智能体接入，包含 probe、run、status、cancel、运行记录和工具调用日志。
- 技能与 MCP 服务管理，支持探测、调用和持久化记录。
- 工作区文件、上传附件、文本提取、预览、Office 转 PDF 回退路径和知识入口。
- HTML / Web 应用与 Office / 文档格式成果的生成、预览、编辑、Diff、导出和部署预览。
- 工作流画布的生成、编辑、保存、启用、运行、节点状态持久化，以及真实工具 / 智能体节点执行。
- 安全运营面板，支持审计日志、角色、权限和用户角色更新。
- Docker 本地或演示环境部署能力。

## 快速开始

后端：

```powershell
cd backend
uv sync --extra dev
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

前端：

```powershell
cd frontend
pnpm install
pnpm dev
```

浏览器打开 `http://localhost:5173`。

## Docker 部署

复制模板、替换其中所有 `replace-with-*` 值，再在仓库根目录运行：

```powershell
Copy-Item docker/env.example docker/.env
docker compose --env-file docker/.env -f docker/docker-compose.yml up --build
```

打开 `http://localhost:8080`。

公开地址和端口可继续在 `docker/.env` 中调整。

更多说明见 [docker/README.md](./docker/README.md)。

## 客户端

- `desktop-client/`：桌面端，与 Web 能力同步，并提供托盘、全局快捷键、快速输入、通知和独立窗口。
- `mobile-client/`：移动端 / PWA，用于轻量会话、成果核验、安装流程和进度跟踪。

## 配置

后端按顺序读取仓库根目录 `.env`、`backend/.env` 和系统环境变量。

常见本地配置：

```env
DATABASE_URL=sqlite:///./agenthub_dev.db
SECRET_KEY=agenthub-dev-secret-change-me
LLM_PROVIDER=auto
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
ENABLE_FUNCTION_CALLING=true
```

模型密钥通过“全局设置 → 模型 API”保存到加密的 Provider 凭据字段。DeepSeek 使用 `https://api.deepseek.com`，默认模型为 `deepseek-v4-flash`；思考模式默认关闭，可选 `high` 或 `max`。API Key 永不回填。

创建首位管理员：

```powershell
cd backend
uv run python -m app.cli create-admin
```

生产环境必须设置非占位 `SECRET_KEY`、`DEBUG=false` 和真实数据库密码；也可仅在首次 Docker 启动时设置 `AGENTHUB_BOOTSTRAP_ADMIN_EMAIL`、`...USERNAME`、`...PASSWORD`。

Docker 环境建议基于 [docker/env.example](./docker/env.example) 创建 `docker/.env`。Compose 会根据 `POSTGRES_*` 自动拼装 PostgreSQL 的 `DATABASE_URL`，避免根目录 `.env` 误导容器使用 SQLite。

## 测试

```powershell
.\scripts\run-tests.ps1 -List
.\scripts\run-tests.ps1 -Stack backend -Module auth -Type integration
.\scripts\run-tests.ps1 -Stack backend -Module providers -Type unit
.\scripts\run-tests.ps1 -Stack frontend -Module models -Type component
```

不带选择器会拒绝运行；全量测试必须显式使用 `-All`。`live` Provider 测试仅在显式提供真实 API Key 时调用外部服务。

## 文档索引

- [Docs index](./docs/README.md)
- [开发指南](./docs/development-guide.md)
- [功能指南](./docs/functional-guide.md)
- [后端架构](./docs/backend-architecture.md)
- [工作流运行时](./docs/agent-workflow-runtime.md)
- [事件协议](./docs/event-protocol.md)
- [文件地图](./docs/file-map.md)
- [能力与数据边界](./docs/capability-data-boundaries.md)
- [安全与模型供应商](./docs/security-and-model-providers.md)
- [当前实现状态](./docs/implementation-status.md)

## 说明

- 本仓库仍在积极开发；本地/Docker 可运行不等同于已完成生产级部署验证。

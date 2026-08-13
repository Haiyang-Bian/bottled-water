import type { ReactNode } from "react";
import {
  ApiOutlined,
  AppstoreOutlined,
  AuditOutlined,
  BookOutlined,
  BranchesOutlined,
  CodeOutlined,
  DatabaseOutlined,
  DeploymentUnitOutlined,
  FileProtectOutlined,
  FileTextOutlined,
  MessageOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
  ToolOutlined,
} from "@ant-design/icons";

export type NavGroup = {
  title: string;
  links: Array<{
    title: string;
    href: string;
  }>;
};

export type IconCard = {
  id?: string;
  href?: string;
  title: string;
  description: string;
  scenario?: string;
  steps?: string[];
  signals?: string[];
  icon: ReactNode;
};

export type ReadingPath = {
  audience: string;
  title: string;
  description: string;
  steps: string[];
  href: string;
};

export type ApiEntry = {
  id: string;
  title: string;
  meta: string;
  icon: ReactNode;
};

export type EndpointEntry = {
  module: string;
  endpoint: string;
  purpose: string;
};

export type RuntimeEntry = {
  name: string;
  usage: string;
  boundary: string;
};

export const navGroups: NavGroup[] = [
  {
    title: "开始使用",
    links: [
      { title: "产品总览", href: "#platform-overview" },
      { title: "推荐阅读路径", href: "#reading-path" },
      { title: "首次会话", href: "#first-run" },
      { title: "本地启动", href: "#local" },
    ],
  },
  {
    title: "核心概念",
    links: [
      { title: "工作区与会话", href: "#workspace" },
      { title: "Agent 与能力授权", href: "#agents" },
      { title: "工作流画布", href: "#workflow-nodes" },
      { title: "文件与产物", href: "#asset-lifecycle" },
      { title: "权限与审计", href: "#security" },
    ],
  },
  {
    title: "开发接入",
    links: [
      { title: "API 总览", href: "#api-overview" },
      { title: "认证与用户", href: "#api-auth" },
      { title: "工作区 / 会话 / 消息", href: "#api-chat" },
      { title: "Agent / Tool / MCP", href: "#api-capability" },
      { title: "文件 / 知识库 / 产物", href: "#api-assets" },
      { title: "工作流运行", href: "#api-workflow" },
      { title: "前端 SDK 示例", href: "#frontend-sdk" },
    ],
  },
  {
    title: "扩展运行",
    links: [
      { title: "模型供应商", href: "#models" },
      { title: "Reasoning 与流式输出", href: "#reasoning" },
      { title: "自定义工具", href: "#custom-tools" },
      { title: "Skill 运行", href: "#skills" },
      { title: "MCP 服务", href: "#mcp" },
      { title: "沙箱执行", href: "#sandbox" },
    ],
  },
  {
    title: "交付维护",
    links: [
      { title: "平台能力地图", href: "#capability-map" },
      { title: "产物预览与导出", href: "#artifacts" },
      { title: "部署预览", href: "#deploy" },
      { title: "后台任务与用量", href: "#usage" },
      { title: "常见问题与排查", href: "#faq" },
      { title: "版本记录", href: "#updates" },
    ],
  },
];

export const quickEntries: IconCard[] = [
  {
    href: "#first-run",
    title: "5 分钟跑通",
    description: "从演示登录开始，完成一次可复盘的 Agent 会话，并确认消息流、工具摘要和产物预览都能正常工作。",
    icon: <ThunderboltOutlined />,
  },
  {
    href: "#capability-map",
    title: "能力地图",
    description: "按控制台入口理解账号、工作区、会话、Agent、文件、工具、审计之间的边界，避免排查时在错误层级绕圈。",
    icon: <AppstoreOutlined />,
  },
  {
    href: "#workflow-nodes",
    title: "工作流编排",
    description: "理解复杂群聊如何在 Team Leader 自动调度和 workflow 固定编排之间切换，以及节点输入、输出、失败状态如何形成可观察链路。",
    icon: <BranchesOutlined />,
  },
  {
    href: "#api-overview",
    title: "API 接入",
    description: "从认证、会话、消息、Agent、文件和产物接口切入，了解前端 SDK 如何封装后端能力。",
    icon: <ApiOutlined />,
  },
  {
    href: "#integration",
    title: "扩展工具链",
    description: "把自定义工具、Skill、MCP 和沙箱能力接入 Agent，并明确谁能调用、在哪里看结果、失败后如何回退。",
    icon: <CodeOutlined />,
  },
  {
    href: "#faq",
    title: "排查手册",
    description: "按现象定位登录、模型、流式消息、文件、产物、MCP 和沙箱问题，并给出优先检查顺序。",
    icon: <BookOutlined />,
  },
];

export const readingPaths: ReadingPath[] = [
  {
    audience: "第一次体验",
    title: "从会话到产物",
    description: "适合演示人员和产品同学。目标不是读完整个系统，而是快速证明 AgentHub 能把输入、推理、工具调用和交付结果串成一条可展示的链路。",
    steps: ["打开控制台并确认登录态", "选择或创建一个独立工作区", "新建默认 Daily 单聊或手动选择群聊成员", "上传材料并发送明确任务", "查看流式回复、工具摘要、协作进度和产物预览"],
    href: "#first-run",
  },
  {
    audience: "配置平台能力",
    title: "从模型到 Agent",
    description: "适合管理员和 Agent 配置者。先把模型、工具和外部能力变成受控资源，再把这些资源授权给具体 Agent，最后进入会话或工作流验证。",
    steps: ["配置模型供应商与 mock/auto 策略", "创建 Agent 并写清角色边界", "绑定工具 / Skill / MCP 能力", "用最小输入测试调用链路", "加入工作流并观察节点输出"],
    href: "#models",
  },
  {
    audience: "开发与集成",
    title: "从 API 到扩展",
    description: "适合开发维护者。新增能力时先确定 API 边界和数据归属，再复用前端 SDK、后端 service、运行记录和审计链路，减少临时入口。",
    steps: ["查看 API 模块和数据库模型", "复用前端 SDK 封装请求", "补充后端服务与权限校验", "运行单测和端到端验证", "通过任务、事件和审计日志排查"],
    href: "#api-overview",
  },
];

export const capabilitySections: IconCard[] = [
  {
    id: "chat",
    title: "会话工作台",
    description:
      "会话是 AgentHub 的主入口，也是用户最先理解平台能力的地方。左侧负责会话、分类、归档和工作区上下文，中间承载用户输入、Agent 流式回复、工具摘要和运行提示，右侧在需要时打开文件或产物预览。新建会话默认选择一个 Daily Chat Agent，适合日常问答；群聊适合把多角色协作、Team Leader 自动组织和 workflow 运行放在同一条时间线里。",
    scenario:
      "当用户拿到一份需求、报告、代码片段或业务材料时，不需要先理解后端工具目录，只要创建会话、选择 Agent、上传材料并提出任务，就能看到系统如何组织上下文、调用能力和输出结果。",
    steps: ["选择工作区并新建会话", "默认使用 Daily 或手动增加协作 Agent", "上传附件或粘贴需求", "观察流式消息、工具摘要、协作计划和后台任务", "点击产物卡片进入右侧预览"],
    signals: ["消息是否持续流式回填", "复杂任务是否出现短任务进度", "Team Leader 是否只在需要总结时发最终交付", "产物卡片是否只在需要交付时出现", "停止生成、重试、后台任务状态是否一致"],
    icon: <MessageOutlined />,
  },
  {
    id: "agents",
    title: "Agent 广场",
    description:
      "Agent 广场把角色设定、模型选择、工具权限、Skill、MCP 和 agentic loop 能力统一配置起来。官方 Agent 用于提供稳定的基础角色，自定义 Agent 用于沉淀团队自己的业务职责。每个 Agent 都应说明能做什么、不能做什么、可调用哪些能力，以及这些能力在审计和工作区边界内如何落地。",
    scenario:
      "当团队需要把 Reviewer、Frontend Worker、Writing Agent 这类角色稳定复用时，应优先创建 Agent，而不是每次在聊天里重复粘贴长提示词。",
    steps: ["创建或复制一个 Agent", "配置系统提示词和模型模式", "绑定允许调用的工具、Skill、MCP", "用单聊做最小验证", "把验证通过的 Agent 放入群聊或 workflow 节点"],
    signals: ["Agent 是否只调用授权能力", "工具参数是否能被 schema 校验", "失败原因是否能在工具记录或审计里看到", "角色回复是否稳定符合职责"],
    icon: <RobotOutlined />,
  },
  {
    id: "workflow",
    title: "工作流画布",
    description:
      "工作流画布用于把多 Agent 协作从临场聊天变成可复盘的执行图。未启用 workflow 的群聊会由 Team Leader 根据任务选择合适 Agent 并生成进度；启用 workflow 后，保存的画布成为执行计划，节点负责明确输入、配置、输出和失败状态。画布不是为了把所有事情都做成流程，而是把需要稳定复用、需要审计、需要定位失败原因的协作链路显式化。",
    scenario:
      "当一个任务需要先分析、再生成、再审查、最后产出文档或页面时，使用 workflow 能让每个阶段的责任、输出和异常都可见。",
    steps: ["从 start 节点接收任务", "用 agent/tool/skill/mcp 节点处理关键步骤", "用 condition/loop 控制分支与迭代", "用 review 节点做质量把关", "用 artifact/end 节点交付结果并汇总"],
    signals: ["WorkflowRun 是否记录节点状态", "失败节点是否有错误信息和输入输出", "画布保存后是否写入会话 extra.workflow", "运行结果是否能回到聊天时间线"],
    icon: <BranchesOutlined />,
  },
  {
    id: "artifacts",
    title: "文件与产物",
    description:
      "文件和产物贯穿从输入到交付的全过程。上传文件会进入工作区文件边界，并在发送消息时被摘要、解析或作为附件上下文提供给 Agent。产物则代表 Agent 生成的可交付内容，支持预览、修订、版本 Diff、导出和部署预览。文档、表格、PPT、HTML/Web App 都应该通过产物链路交付，而不是只停留在聊天文本里。",
    scenario:
      "当用户要求生成 PRD、报告、表格、PPT、网页或可下载文件时，系统应生成 Artifact，并让用户在 PreviewPanel 中继续检查和导出。",
    steps: ["上传或选择工作区文件", "发送任务并让 Agent 读取上下文", "生成 Artifact 记录和真实文件", "在 PreviewPanel 预览、编辑、Diff", "按格式导出或创建部署预览"],
    signals: ["文件是否落在正确工作区", "预览失败时是否有降级策略", "版本 Diff 是否保留变更过程", "导出文件是否能真实下载和打开"],
    icon: <FileTextOutlined />,
  },
];

export const apiSections: ApiEntry[] = [
  {
    id: "api-auth",
    title: "认证与用户",
    meta: "backend/src/app/api/auth.py",
    icon: <SafetyCertificateOutlined />,
  },
  {
    id: "api-chat",
    title: "会话与消息",
    meta: "backend/src/app/api/conversations.py / messages.py",
    icon: <MessageOutlined />,
  },
  {
    id: "api-capability",
    title: "Agent / Tool / MCP",
    meta: "backend/src/app/api/agents.py / tools.py / mcp.py",
    icon: <ToolOutlined />,
  },
  {
    id: "api-assets",
    title: "文件 / 知识库 / 产物",
    meta: "backend/src/app/api/files.py / knowledge.py / artifacts.py",
    icon: <FileProtectOutlined />,
  },
  {
    id: "api-workflow",
    title: "工作流运行",
    meta: "backend/src/app/api/conversations.py / workflow.py",
    icon: <BranchesOutlined />,
  },
];

export const maintainCards: IconCard[] = [
  {
    id: "sandbox",
    title: "权限与审计",
    description: "权限与审计负责回答“谁做了什么、为什么被允许、失败时如何追踪”。RBAC、角色变更、沙箱命令、远程连接和高风险工具调用都应由后端统一校验，前端只展示当前用户被授权后的操作入口。排查安全问题时优先看审计日志、用户角色和工作区边界，而不是只看页面按钮是否显示。",
    icon: <AuditOutlined />,
  },
  {
    id: "deploy",
    title: "部署预览",
    description: "部署预览用于把 HTML/Web App 类产物从“可预览文件”推进到“可访问版本”。每次预览都应该保留来源 Artifact、创建时间、状态、访问地址和回滚线索，便于演示时快速打开，也便于失败时退回上一个可用版本。",
    icon: <DeploymentUnitOutlined />,
  },
  {
    title: "数据边界",
    description: "数据边界决定上下文是否可信。工作区隔离会话、文件、产物、工具、Skill、MCP、后台任务和运行记录，避免 A 项目的文件被 B 项目的 Agent 误读。新增能力时要先明确数据写入哪个工作区、是否需要成员权限、是否会进入长期记忆或审计记录。",
    icon: <DatabaseOutlined />,
  },
];

export const runtimeEntries: RuntimeEntry[] = [
  {
    name: "mock",
    usage: "无外部模型密钥时的本地演示与前端联调",
    boundary: "不会访问真实供应商，适合验收 UI、路由和基础编排链路。",
  },
  {
    name: "ark",
    usage: "接入火山方舟或兼容 OpenAI 的真实推理服务",
    boundary: "密钥只由后端读取，前端不保存 API Key。",
  },
  {
    name: "auto",
    usage: "根据环境变量自动选择真实模型或 mock 模式",
    boundary: "适合团队共享开发环境，缺少密钥时仍可进入演示闭环。",
  },
];

export const endpointEntries: EndpointEntry[] = [
  { module: "认证", endpoint: "/api/v1/auth/login", purpose: "登录、演示登录、会话恢复" },
  { module: "工作区", endpoint: "/api/v1/workspaces", purpose: "工作区隔离、成员、项目文件入口" },
  { module: "工作区文件", endpoint: "/api/v1/workspace-files", purpose: "文件树、预览、下载、重命名、收藏、移动" },
  { module: "会话", endpoint: "/api/v1/conversations", purpose: "单聊、群聊、成员、分类、归档、工作流保存" },
  { module: "消息", endpoint: "/api/v1/messages", purpose: "消息发送、流式事件、运行事件、停止生成、重试" },
  { module: "Agent", endpoint: "/api/v1/agents", purpose: "官方 Agent、自定义 Agent、权限绑定" },
  { module: "工具", endpoint: "/api/v1/tools", purpose: "内置工具、自定义工具、AI 生成工具、调用测试" },
  { module: "MCP", endpoint: "/api/v1/mcp", purpose: "服务注册、探测、工具调用、调用记录" },
  { module: "文件", endpoint: "/api/v1/files", purpose: "上传、解析、预览、摘要、向量化入口" },
  { module: "产物", endpoint: "/api/v1/artifacts", purpose: "产物创建、版本、Diff、预览、导出" },
  { module: "部署", endpoint: "/api/v1/deployments", purpose: "预览部署、部署记录、回滚入口" },
  { module: "沙箱", endpoint: "/api/v1/sandbox", purpose: "沙箱会话、受限命令、远程连接" },
  { module: "安全", endpoint: "/api/v1/security", purpose: "审计日志、角色、权限、用户权限后台" },
  { module: "任务", endpoint: "/api/v1/tasks", purpose: "后台任务、取消、刷新、运行态展示" },
];

export const integrationSteps = [
  "在全局设置里确认模型供应商、工具目录、Skill 和 MCP 服务状态。",
  "进入 Agent 广场，为目标 Agent 绑定允许调用的工具、Skill 或 MCP。",
  "在单聊中直接验证 Agent 小循环，或在群聊中观察 Team Leader 调度与进度。",
  "需要稳定复用时，再把能力节点接入 workflow 并保存启用。",
  "运行后查看消息、工具摘要、后台任务、审计日志和产物预览。",
];

export const troubleshootingRows = [
  ["登录失败", "检查 auth API、token、本地存储和后端环境变量。"],
  ["模型无响应", "检查 LLM_PROVIDER、ARK_API_KEY、模型配置和后端日志。"],
  ["消息不流式", "检查 SSE/WebSocket、event_bus、messages API 和前端消息 Store。"],
  ["群聊没有进度", "检查 scheduler.decision、control.assign、agent.report 和 RuntimeDecisionStrip。"],
  ["Team Lead 选错 Agent", "检查 AgentHubTeamLeadPolicy、提及范围和 scheduler.decision 事件。"],
  ["Agent 不调用工具", "检查 Agent 权限、tool_loop、工具 schema 和调用参数。"],
  ["工作流不运行", "检查 conversation.extra.workflow、节点类型、边和 workflow run 状态。"],
  ["产物打不开", "检查 artifact 文件路径、预览 URL、导出格式和浏览器控制台错误。"],
  ["Office 预览失败", "检查 LibreOffice / soffice 路径；后端会尝试生成文本降级 PDF。"],
  ["MCP 工具不可用", "先执行 probe，再检查 server 类型、URL、stdio 命令和调用记录。"],
  ["沙箱命令失败", "检查工作区路径、命令白名单、SandboxSession 状态和审计日志。"],
];

export const localBootCode = `cd backend
uv sync --extra dev
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

cd ../frontend
pnpm install
pnpm dev`;

export const apiExampleCode = `const conversation = await api.createConversation({
  workspace_id: activeWorkspaceId,
  chat_type: "single",
  title: "需求评审",
  participant_agent_ids: ["agent-reviewer"]
});

await api.sendMessage(conversation.id, {
  content: "请审查这份需求文档，并生成可执行任务清单。",
  attachments: [uploadedFile]
});`;

export const workflowExampleCode = `{
  "nodes": [
    { "id": "start", "type": "start", "title": "接收任务" },
    { "id": "agent-reviewer", "type": "agent", "title": "Reviewer" },
    { "id": "artifact", "type": "artifact", "title": "生成评审报告" },
    { "id": "end", "type": "end", "title": "汇总输出" }
  ],
  "edges": [
    ["start", "agent-reviewer"],
    ["agent-reviewer", "artifact"],
    ["artifact", "end"]
  ]
}`;

export const updateItems = [
  "新建会话默认只选择 Daily Chat Agent，复杂协作由用户显式添加合适 Agent。",
  "多 Agent 群聊支持 Team Leader 自动规划、短任务进度、合适 Agent 选择和按需最终总结。",
  "Team Leader 最终交付改为聚合来源、链路、校验、产物和风险，不再使用旧兜底转述。",
  "外部 Coding Agent 统一通过 external_agent.invoke 调用，Codex、Claude Code 和兼容适配器共用运行记录。",
  "Docker/PWA、个人签名、工作流运行态和文档页面说明已按当前版本更新。",
];

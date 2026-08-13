# Feature Guide

AgentHub is organized around conversations. A user starts from an IM-style workbench, chooses a workspace and conversation, then collaborates with one or more agents. Files, tools, artifacts, workflows, and deployment records all attach back to the workspace or conversation.

## Authentication And Workspaces

- Users can register, log in, update profile, update profile signature, and change password. Registration assigns `member`; disabled accounts cannot authenticate or reuse an old JWT.
- Workspaces isolate conversations, files, projects, knowledge entries, and audit context.
- The workbench keeps workspace and conversation route state synchronized so direct links can reopen the same context.

Key code:

- Backend: `backend/src/app/api/auth.py`, `backend/src/app/api/workspaces.py`
- Frontend: `frontend/src/features/auth`, `frontend/src/features/workspaceFiles`, `frontend/src/pages/WorkbenchPage`

## Conversations

AgentHub supports:

- Single chat with a selected agent.
- Group chat with multiple agents.
- Conversation categories, pinning, archiving, unread state, members, and metadata.
- Streaming responses over SSE and WebSocket-compatible paths.
- Stop/cancel generation, retry, and persisted message history.

New conversations default to one Daily Chat Agent, including group-capable creation dialogs. Users can add more agents manually when they want collaboration.

Single chat runs the selected agent loop. Group chat chooses scheduling from conversation settings:

- Workflow-enabled group chat runs the saved workflow canvas and persists `WorkflowRun` node state.
- Non-workflow group chat uses `AgentHubTeamLeadPolicy` on the shared Runtime Kernel.
- Simple turns should not receive a trailing Team Leader restatement.
- Complex collaborative turns can show a short plan/progress strip, dispatch a suitable subset of agents, and publish a Team Leader final answer only when a real multi-agent summary is useful.

The current Team Leader final answer is an aggregated deliverable. It should summarize source outputs, dependency chain, checks, final products, and risks without dumping every agent's raw transcript. Artifact and deployment links must come from persisted tool results.

Key code:

- Backend: `backend/src/app/api/conversations.py`, `backend/src/app/api/messages.py`, `backend/src/app/api/websocket.py`
- Frontend: `frontend/src/features/chat`, `frontend/src/api/message.ts`, `frontend/src/api/websocket.ts`

## Agents

Agents can be official seeded agents or user-created agents. They can be configured with:

- Model config
- System prompt and description
- Tools
- Skills
- MCP tools
- Agentic loop settings

Agent tool access is permission-driven. If an agent has default full permissions, backend execution and context summaries now both expose the same effective tool set.

Key code:

- Backend: `backend/src/app/api/agents.py`, `backend/src/app/services/agents`, `backend/src/app/services/tools`
- Frontend: `frontend/src/features/agents`

## Models

The model layer supports owner-scoped provider and model configuration through the app. Ark and generic OpenAI-compatible providers are available alongside DeepSeek V4 Flash/Pro. DeepSeek supports remote model refresh, streaming reasoning, tool calls, and optional `high`/`max` thinking. Provider keys are write-only in the UI and responses expose only whether a key is configured.

Key code:

- Backend: `backend/src/app/api/models.py`, `backend/src/app/services/llm`
- Frontend: `frontend/src/features/settings`

## Tools

The tool catalog includes built-in and custom tools. Built-ins cover:

- File read/write/extract/preview/convert/summarize/embed/upload
- Artifact create/preview/revise/diff/export
- Sandbox command execution for non-interactive one-shot commands
- Interactive terminal sessions for CLI wizards and scaffolding
- Browser preview
- API test and test runner
- Database inspection
- Deployment preview and rollback
- Security audit
- External coding agent probe/run/status/cancel

Tool calls are recorded through persisted invocation records. Agents must use real tool results as facts; UI cards and deployment states should not be faked by text-only answers.

Interactive CLI work uses the terminal tool family:

- `terminal.start`: start a controlled process such as `npm create vue@latest my-vue-app`, `npm init`, or `npx shadcn@latest init`.
- `terminal.wait_for`: wait for prompt text or completion text without killing the process on timeout.
- `terminal.send`: send stdin to the running session.
- `terminal.snapshot` and `terminal.stop`: inspect or terminate the session.

Use `sandbox.run` for commands that can finish without stdin. Use `terminal.*` when a command may ask questions, scaffold projects, or otherwise block waiting for input. Terminal sessions persist every tool call through `ToolInvocation`; the live process itself is currently held in backend memory, so a backend restart or non-sticky multi-worker deployment loses active sessions.

Key code:

- Catalog and execution: `backend/src/app/services/tools`
- Built-ins: `backend/src/app/services/tools/builtins`
- API: `backend/src/app/api/tools.py`, `backend/src/app/api/sandbox.py`

## External Coding Agents

External agent support exposes Codex, Claude Code, and compatible adapters through one unified tool-call target:

- `external_agent.invoke`

The unified tool accepts action-style calls for run/probe/status/cancel while preserving legacy aliases internally for compatibility. The public tool catalog exposes the unified tool so agents do not need to choose provider-specific tool names.

Runs persist provider, command, cwd, stdout, stderr, changed files, status, exit code, duration, and error message. CWD is constrained to workspace-safe paths, and unavailable runtimes degrade explicitly instead of pretending success.

After an Agent has `external_agent.invoke` permission, Codex and Claude Code run non-interactively by default: Codex uses full-auto mode, and Claude Code skips its internal permission confirmation. This only affects the external CLI prompt; AgentHub still enforces tool authorization, workspace cwd isolation, persisted run records, and secret redaction.

Key code:

- Backend: `backend/src/app/services/external_agents`, `backend/src/app/api/external_agents.py`
- Tests: `backend/tests/test_external_agents.py`

## Skills And MCP

Skills can be created, generated, tested, and imported from MCP definitions. MCP servers can be registered, probed, invoked, and audited. HTTP and stdio transports are handled through the MCP service layer; unavailable external runtimes degrade with explicit errors.

Key code:

- Skills: `backend/src/app/api/skills.py`, `backend/src/app/services/skills`
- MCP: `backend/src/app/api/mcp.py`, `backend/src/app/services/mcp`
- Frontend: `frontend/src/features/platform/tabs`

## Files And Knowledge

The file system supports upload, workspace file browsing, preview, download, rename, move, favorite, delete, and bulk delete. Uploaded files can be extracted and included in runtime context. Knowledge features provide lightweight knowledge-base import and retrieval paths.

Office preview can use PDF conversion where the runtime environment provides the required tools; otherwise the backend should return a clear degraded result.

Key code:

- Backend: `backend/src/app/api/files.py`, `backend/src/app/api/workspace_files.py`, `backend/src/app/services/files`
- Frontend: `frontend/src/features/workspaceFiles`

## Artifacts And Preview

Artifacts represent deliverable outputs. HTML/Web app, PDF, DOCX, PPTX, XLSX, and related formats can be created through the tool layer, opened in the preview panel, edited, diffed, exported, and used for deployment preview records.

Key code:

- Backend: `backend/src/app/api/artifacts.py`, `backend/src/app/services/artifacts.py`, `backend/src/app/services/document_model`
- Frontend: `frontend/src/features/preview`

## Workflows

Workflow canvases are saved under conversation metadata and can be generated, edited, enabled, and run. Supported node categories include start, agent, tool, skill, MCP, condition, loop, review, artifact, and end.

Recent behavior:

- AI workflow generation uses the backend generation path instead of static samples.
- Workflow saves normalize canvas-style edges and preserve runtime settings.
- Enabling a saved workflow updates the conversation scheduling mode.
- Run state is persisted and merged during polling so runs no longer appear stuck at an artificial 5%.
- Agent/tool workflow nodes share the same permission, invocation, and result persistence path as chat.

Key code:

- Backend: `backend/src/app/api/conversations.py`, `backend/src/app/services/workflows`
- Frontend: `frontend/src/features/workflow`, `frontend/src/features/platform/tabs/WorkflowBoardPanel`
- Tests: `backend/tests/test_conversation.py`, `frontend/tests/workflow-*.test.ts*`

## Deployment Preview

Deployment preview creates records, health-checks accessible artifacts, supports rollback records, and supports `preview_link`, `static_site`, `source_download`, and `container` modes. In the current runtime, `container` means the artifact is exposed through the running AgentHub app or Docker Compose stack and receives the same health-checked preview URL; it is not a separate production orchestrator.

The Docker stack is for running the AgentHub app itself. Full production cloud deployment automation remains a separate roadmap item.

The Docker stack currently uses the `docker/` build context, backend `PYTHONPATH`, startup migrations, psycopg PostgreSQL URLs, and nginx proxy rules for `/api/`, `/ws`, and streaming traffic. See `docker/README.md` for operational details.

Key code:

- Backend: `backend/src/app/api/deployments.py`, `backend/src/app/services/deployments.py`
- Docker: `docker/`

## Security And Audit

Security operations include audit logs, roles, permissions, and user role updates. Sensitive operations should be routed through backend services and recorded where appropriate. API keys belong in backend configuration or model-provider records, not frontend code.

Key code:

- Backend: `backend/src/app/api/security_ops.py`, `backend/src/app/services/audit.py`, `backend/src/db/models/security.py`
- Frontend: `frontend/src/features/platform/components/SecurityOpsPanel.tsx`

# Backend Architecture

The backend is a FastAPI application under `backend/src`. It is organized by API routers, service domains, SQLAlchemy models, and runtime orchestration modules.

## Request Flow

```text
HTTP/SSE/WebSocket request
  -> app.main router registration
  -> app.api.<domain>
  -> app.services.<domain>
  -> db.models + db.session
  -> response serialization / realtime events
```

Routers should stay thin. Business behavior belongs in services. Database schema belongs in `backend/src/db/models` and migrations in `backend/alembic/versions`.

## Application Entry

- `backend/src/app/main.py`: FastAPI app, middleware, exception handlers, health endpoints, router registration.
- `backend/server.py`: local helper entry used by workspace launch configs.
- `backend/alembic/env.py`: migration import path and metadata setup.

The backend container sets `PYTHONPATH=/app/backend/src`. Local `uv run` commands from `backend` use the project configuration and test `pythonpath` settings.

## Configuration

- App config: `backend/src/app/core/config.py`
- DB config: `backend/src/db/config.py`
- Session factory: `backend/src/db/session.py`

SQLite is common for local development. PostgreSQL URLs are normalized to `postgresql+psycopg://...`, matching the installed dependency.

## Persistence

SQLAlchemy models are grouped by domain:

- Users and security
- Workspaces and projects
- Agents and capabilities
- Conversations and messages
- Runtime runs and context states
- Workflow runs
- Tasks
- Files, knowledge, artifacts, and deployments
- Tool, skill, MCP, model, sandbox, remote, and external agent records

Conversation metadata (`Conversation.extra`) stores workflow, scheduling settings, runtime read models, and other application state. Runtime Blackboard and structured AgentMemory live in `runtime_context_states`; Run terminal state lives in `runtime_runs`.

## Chat Runtime

Message send paths enter through `messages.py` or `websocket.py`, then move through chat services:

- `services/chat/user_messages.py`: save user messages.
- `services/chat/scheduling.py`: resolve single-agent, tech-lead, or workflow scheduling.
- `services/runtime_service.py`: runtime entry point.
- `services/conversation_run_manager.py`: reusable Runtime adapters, active RunHandle ownership, input queueing, and cancellation.
- `services/agents`: agent loops and function/tool calling.
- `services/realtime`: event publication and stream coordination.

Streaming events are consumed by frontend merge logic and should always finish with a terminal success, failure, or cancellation event.

Group chat is coordinated by `AgentHubTeamLeadPolicy` through the same Runtime Kernel used by single-agent and workflow runs. The policy proposes work; the Kernel validates targets, owns Actor tasks, and commits the terminal state.

## Agent Tool Loop

Agent tool execution has three important layers:

- Context: `services/context` builds history, attachments, tool results, workflow variables, blackboard, and effective authorized tools.
- Permission/catalog: `services/tools/catalog.py`, `permissions.py`, and `toolboxes.py`.
- Execution: `services/tools/executor.py` and `services/tools/builtins`.

The model may request a tool call, but backend permission checks and schema validation decide whether it actually executes. Tool results must be persisted and used as the factual source for UI cards, artifact links, and deployment status.

Interactive command execution is split by behavior:

- `sandbox.run` executes non-interactive commands and returns real stdout, stderr, exit code, duration, cwd, and file listings.
- `terminal.start/send/wait_for/snapshot/stop` manages long-lived CLI sessions for tools that prompt on stdin. It captures output tails, stdin events, status, exit code, duration, generated files, and persists each step as a normal tool invocation.

The terminal runtime is intentionally controlled: it uses argv execution rather than shell execution, validates executables and workspace paths, redacts captured input/output, and limits time and buffered output. Active terminal processes are in-memory per backend process; persisted invocation records remain the audit source, but live sessions require sticky routing or a future distributed runtime.

## Workflows

Workflow execution lives in `backend/src/app/services/workflows`.

- Normalize and save canvas data in `api/conversations.py`.
- Store run state in `WorkflowRun`.
- Execute supported node types through workflow services.
- Call agents and tools through the same agent/tool runtime used by chat.
- Merge persisted state back into polling responses.

Workflow-enabled group conversations use the saved workflow as their plan. Non-workflow group conversations use the configured scheduling mode.

## Skills And MCP

Skills live under `services/skills` and MCP under `services/mcp`.

- Skills can run prompt, agent, MCP, or script-oriented runtimes depending on manifest and permissions.
- Script-style skill execution must go through controlled file and sandbox tools.
- MCP probe/invoke paths should record explicit degraded failures instead of pretending unavailable transports worked.

## External Coding Agents

External coding agent adapters live under `services/external_agents`.

They expose probe, run, status, and cancel operations for Codex, Claude Code, and compatible adapters through the unified `external_agent.invoke` tool. Legacy provider-specific tool names are mapped to the unified tool internally for compatibility, while the public catalog exposes the unified tool. Runs persist stdout, stderr, exit code, changed files, cwd, duration, and status. CWD validation protects workspace boundaries.

## Files, Artifacts, And Deployment Preview

- Files: `services/files`, `api/files.py`, `api/workspace_files.py`
- Artifacts: `services/artifacts.py`, `services/document_model`, `api/artifacts.py`
- Deployment preview: `services/deployments.py`, `api/deployments.py`

Deployment preview is a local/Docker-stack preview record system with health checks and rollback records. Supported modes include `preview_link`, `static_site`, `source_download`, and `container`; container mode uses the AgentHub app container stack to expose the artifact preview URL. Production cloud orchestration remains roadmap.

## Safety Boundaries

- API keys must stay in backend configuration or database provider records.
- Frontend never receives raw provider secrets.
- Sandbox and external agents use cwd and command/runtime constraints.
- Audit-worthy actions should be routed through backend services and recorded.
- The deleted legacy backend copy must not be restored; `backend/src` is the only backend source tree.

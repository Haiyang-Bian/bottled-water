# File Map

This map points to the current implementation files. It intentionally omits deleted historical design notes.

## Repository Root

```text
backend/                 FastAPI backend, SQLAlchemy models, Alembic migrations, tests
frontend/                React/Vite frontend, tests, styles, API client
docker/                  Docker Compose stack, nginx config, Dockerfiles
docs/                    Current project documentation
e2e/                     Playwright end-to-end configuration
scripts/                 Utility and acceptance scripts
```

Important root files:

- `README.md`: current project overview.
- `.env.example`: local development environment template.
- `.dockerignore`: root Docker build-context ignore file.
- `agenthub.code-workspace`: VS Code workspace settings.

## Backend

```text
backend/
  alembic/               migration environment and versions
  src/
    app/
      api/               FastAPI routers
      core/              config, errors, security, logging, response helpers
      events/            SSE/WebSocket event sinks
      persistence/       runtime persistence adapters
      schemas/           Pydantic request/response schemas
      services/          business services
    agent_runtime/       orchestration/runtime primitives
    common/              shared helpers
    db/                  database config, session, models
    model_provider/      model provider abstraction
  tests/                 backend pytest suite
  pyproject.toml
  uv.lock
```

Core backend files:

- `backend/src/app/main.py`: FastAPI application, middleware, health checks, router registration.
- `backend/src/app/core/config.py`: app configuration.
- `backend/src/db/config.py`: database URL resolution.
- `backend/src/db/session.py`: async SQLAlchemy engine and sessions.
- `backend/alembic/env.py`: migration import path and metadata setup.

Routers:

- `auth.py`: login, registration, profile, password changes, and active-user checks.
- `workspaces.py`: workspaces, projects, templates, shortcuts.
- `conversations.py`: conversations, members, workflow canvas, workflow runs.
- `messages.py`: message send/list/retry/reply/SSE/cancel.
- `websocket.py`: WebSocket chat and global channel endpoints.
- `agents.py`: agent directory, creation, generation, edit, delete, test.
- `models.py`: model providers and model configs.
- `tools.py`: tool catalog, custom tools, invocation.
- `skills.py`: skill creation, generation, testing, MCP import.
- `mcp.py`: MCP server registration, probe, tools, invocation, records.
- `files.py` and `workspace_files.py`: upload, workspace tree, preview, download, file operations.
- `artifacts.py`: artifact create, preview, version, diff, export.
- `deployments.py`: preview deployment, records, rollback, health checks.
- `external_agents.py`: external coding agent providers, probe, invoke, status, cancel, and run records.
- `sandbox.py`: sandbox sessions and command execution entry points.
- `security_ops.py`: audit logs, roles, permissions, user role updates.
- `knowledge.py`, `tasks.py`, `context.py`, `logs.py`, `orchestrator.py`: supporting platform APIs.

Service domains:

- `services/agents`: agent loop, function calling, direct response, tool-loop glue.
- `services/chat`: send pipeline, prompts, finalization, cancellation, artifacts, scheduling.
- `services/context`: context builder, workspace/conversation context, authorized tool summaries.
- `services/tools`: tool catalog, permissions, execution, built-in tools.
- `services/external_agents`: Codex, Claude Code, OpenCode-compatible provider adapters and run persistence.
- `services/workflows`: workflow normalization, runtime persistence, engine, node execution.
- `services/skills`: skill runtime, dependencies, versions, testing.
- `services/mcp`: MCP discovery, transports, invocation, records.
- `services/files`: upload, preview, extraction, workspace file tree.
- `services/document_model`: structured document rendering for artifacts.
- `services/llm`: Ark/OpenAI-compatible LLM gateway and streaming parser.
- `services/workspaces`: workspace filesystem and boundaries.

Database model groups:

- `users.py`: users and settings.
- `workspaces.py`: workspaces, members, projects, project files, prompt templates, shortcuts.
- `agents.py`: agents and capabilities.
- `conversations.py`: conversations, participants, messages, message versions.
- `workflows.py`: workflow runs.
- `tasks.py`: tasks and dependencies.
- `artifacts.py`: artifacts, versions, deployments.
- `files.py`: file assets, knowledge bases, knowledge documents.
- `capabilities.py`: skills, tools, tool invocations, model configs, MCP servers, sandbox sessions, remote connections, external agent runs.
- `security.py`: audit, roles, permissions, user-role mappings.

## Frontend

```text
frontend/
  src/
    api/                 API client and domain API wrappers
    features/            product features
    hooks/               cross-feature hooks
    lib/                 pure utilities and render helpers
    pages/               route-level pages
    router/              React Router setup
    store/               Zustand stores
    styles/              SCSS partials and global styles
    types/               TypeScript domain types
    utils/               frontend utilities
  tests/                 Vitest tests
```

Core frontend files:

- `frontend/src/api/client.ts`: request helpers, API base, interceptors, SSE helper.
- `frontend/src/api/message.ts`: message send, stream parsing, event dispatch.
- `frontend/src/api/websocket.ts`: conversation WebSocket client.
- `frontend/src/pages/WorkbenchPage`: main IM workbench.
- `frontend/src/features/chat`: chat panel, sidebar, message bubbles, runtime decision/progress strip, drawers.
- `frontend/src/features/agents`: agent directory and configuration UI.
- `frontend/src/features/workflow`: workflow studio and canvas components.
- `frontend/src/features/platform`: platform drawer tabs for tools, skills, MCP, security, workflows, etc.
- `frontend/src/features/preview`: artifact preview/edit/diff/deployment panel.
- `frontend/src/features/workspaceFiles`: workspace file tree and preview flows.

Runtime coordination files:

- `backend/src/agent_runtime/strategies/policies.py`: single-agent, workflow, and generic Team Lead scheduling policies.
- `backend/src/app/services/conversation_run_manager.py`: runtime event persistence, Run lifecycle, input queueing, and Team Leader summary projection.
- `backend/src/app/services/runtime/generation_records.py`: generation event and runtime summary records.
- `frontend/src/lib/runtimeEvents.ts`: frontend runtime event normalization.
- `frontend/src/features/chat/components/ChatPanel/RuntimeDecisionStrip.tsx`: multi-agent plan/progress/summary display.
- `frontend/src/features/chat/components/CreateConversationModal.tsx`: default Daily Agent selection and manual multi-agent selection.

Tests:

- `frontend/tests/workflow-board-panel.test.tsx`
- `frontend/tests/workflow-studio.test.tsx`
- `frontend/tests/workflow-utils.test.ts`
- `frontend/tests/message-stream.test.ts`
- `frontend/tests/external-agents-panel.test.tsx`
- `frontend/tests/security-ops-panel.test.tsx`
- Additional targeted component and utility tests live under `frontend/tests`.

## Docker

- `docker/docker-compose.yml`: nginx, backend, PostgreSQL, Redis.
- `docker/Dockerfile.backend`: backend image, uv sync, Alembic migration, FastAPI start.
- `docker/Dockerfile.frontend`: frontend build and nginx static serving.
- `docker/nginx.conf`: SPA routing, `/api/`, `/ws`, and streaming proxy settings.
- `docker/env.example`: Docker-specific environment template.
- `docker/README.md`: deployment instructions.

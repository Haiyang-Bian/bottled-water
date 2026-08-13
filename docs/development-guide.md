# Development Guide

This guide covers the current local development and deployment workflow.

## Prerequisites

- Python 3.11
- `uv`
- Node.js 20 or newer
- `pnpm`
- Docker Desktop or a compatible Docker engine for container deployment

## Backend Setup

```powershell
cd backend
uv sync --extra dev
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

The backend reads configuration from the repository `.env`, `backend/.env`, and process environment. Local development usually uses SQLite:

```env
DATABASE_URL=sqlite:///./agenthub_dev.db
SECRET_KEY=dev-secret-change-me
LLM_PROVIDER=auto
ENABLE_FUNCTION_CALLING=true
```

For real model calls, configure a user-owned provider in the app UI. Ark environment variables remain a legacy fallback:

```env
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
ARK_API_KEY=...
ARK_ENDPOINT_ID=...
ARK_MODEL=doubao-seed-2-0-lite
```

DeepSeek uses the OpenAI-compatible endpoint `https://api.deepseek.com`. Add it from “全局设置 → 模型 API”; the UI writes the key through the credential endpoint and never reads it back.

## Frontend Setup

```powershell
cd frontend
pnpm install
pnpm dev
```

The Vite dev server proxies API and WebSocket traffic to the backend. The app runs at `http://localhost:5173`.

## Docker Stack

Run the complete stack from the repository root:

```powershell
docker compose -f docker/docker-compose.yml up --build
```

Open `http://localhost`.

To override secrets, host port, or public URL:

```powershell
Copy-Item docker/env.example docker/.env
docker compose --env-file docker/.env -f docker/docker-compose.yml up --build
```

The stack starts nginx, backend, PostgreSQL, and Redis. The backend container runs `alembic upgrade head` before starting FastAPI.

## Tests And Checks

```powershell
.\scripts\run-tests.ps1 -List
.\scripts\run-tests.ps1 -Stack backend -Module security -Type integration
.\scripts\run-tests.ps1 -Stack backend -Module providers -Type unit
.\scripts\run-tests.ps1 -Stack frontend -Module workflow -Type component
```

The runner requires a module and type selector. A complete stack run requires explicit `-All`; live tests require their provider credentials. Run Ruff or ESLint only for touched paths while iterating, and use `pnpm build` as the frontend integration gate.

## Common Development Tasks

Add or change an API:

1. Update the relevant router in `backend/src/app/api`.
2. Put business logic in `backend/src/app/services`.
3. Update models in `backend/src/db/models` and add an Alembic migration when schema changes.
4. Update serialization helpers if the frontend response shape changes.
5. Update `frontend/src/api` and `frontend/src/types`.
6. Add or update tests.

Add or change a tool:

1. Define catalog metadata in `backend/src/app/services/tools/builtins/registry.py`.
2. Implement execution under `backend/src/app/services/tools/builtins` or the matching service domain.
3. Ensure permissions are exposed through agent/tool configuration.
4. Persist execution facts through the tool invocation path.
5. Add tests for success, permission failure, and input validation.

Add or change multi-agent runtime behavior:

1. Keep single-chat behavior isolated from group orchestration changes.
2. Update the relevant `SchedulerPolicy`, Runtime types, and event projection.
3. Ensure simple turns only select the necessary Agent scope.
4. Ensure collaborative reports preserve real artifact/tool references in structured output.
5. Update `frontend/src/lib/runtimeEvents.ts`, `RuntimeDecisionStrip.tsx`, and chat tests when event shape changes.
6. Run the targeted actor-runtime and frontend chat/runtime checks above.

Add or change a workflow node:

1. Update frontend workflow types and node editors in `frontend/src/features/workflow`.
2. Update backend workflow normalization in `backend/src/app/api/conversations.py`.
3. Update execution logic under `backend/src/app/services/workflows`.
4. Persist node state in `WorkflowRun`.
5. Add backend and frontend tests.

## Troubleshooting

- Backend cannot import `app` or `db`: run commands from `backend` through `uv run`, or set `PYTHONPATH=src`.
- Alembic cannot connect to Postgres: use `postgresql+psycopg://...`; plain `postgresql://...` is normalized to psycopg by current config.
- Frontend blank page: run `pnpm build` and check TypeScript errors first.
- Streaming does not finish: inspect `backend/src/app/api/messages.py`, chat finalizer/cancellation services, and frontend running-conversation state.
- Group chat has no visible progress: inspect `scheduler.decision`, `control.assign`, `agent.report`, `frontend/src/lib/runtimeEvents.ts`, and `RuntimeDecisionStrip.tsx`.
- Team Lead selects too many Agents: inspect `AgentHubTeamLeadPolicy`, mentions metadata, and the scheduling proposal event.
- Workflow run looks stuck: check `backend/src/app/services/workflows`, `WorkflowRun.node_states`, and browser network polling responses.
- Docker stack uses the wrong database: use `docker/.env` variables from `docker/env.example`; compose builds its own Postgres URL from `POSTGRES_*`.

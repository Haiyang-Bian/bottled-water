# AgentHub Backend

FastAPI backend for AgentHub.

## Stack

- Python 3.11
- FastAPI
- SQLAlchemy 2 async ORM
- Alembic
- Pydantic
- uv
- SQLite for local development, PostgreSQL for Docker/demo deployment

## Layout

```text
backend/
  alembic/             database migrations
  src/
    app/
      api/             FastAPI routers
      core/            config, security, errors, responses, logging
      events/          realtime event sinks
      persistence/     runtime persistence adapter
      schemas/         Pydantic schemas
      services/        business services
    common/            shared helpers
    db/                database config, session, models
  tests/               pytest suite
```

Shared Runtime, execution, tools and Provider packages live in root `src/` and are supplied by
`agenthub-system`. The uv workspace uses the root `uv.lock`; no backend-local lock or import alias remains.

## Local Run

```powershell
uv sync --package agenthub-backend --extra dev
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

## Tests

Run from the repository root with an explicit module and type:

```powershell
.\scripts\run-tests.ps1 -Stack backend -Module auth -Type integration
.\scripts\run-tests.ps1 -Stack backend -Module providers -Type unit
```

Use `-List` to inspect groups and `-All` only for an intentional full run. Run `uv run ruff check <paths>` inside `backend` for changed Python files.

## Key Domains

- `api/conversations.py`: conversations, participants, workflow canvas, workflow runs.
- `api/messages.py`: message send/list/stream/cancel.
- `api/agents.py`: agent directory and agent configuration.
- `api/tools.py`: tool catalog and invocation.
- `api/skills.py`: skill creation, generation, testing, and MCP import.
- `api/mcp.py`: MCP server management and invocation.
- `api/external_agents.py`: Codex and Claude Code run records.
- `api/files.py` and `api/workspace_files.py`: file upload, workspace file tree, preview, and download.
- `api/artifacts.py`: artifact lifecycle.
- `api/deployments.py`: deployment preview records.
- `api/security_ops.py`: audit, roles, permissions.

## Notes

- Database URLs using `postgresql://` are normalized to `postgresql+psycopg://`.
- When changing nested JSON columns such as `Conversation.extra` or `WorkflowRun.node_states`, use existing runtime helpers or flag changes explicitly.

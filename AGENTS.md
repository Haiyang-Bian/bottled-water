# Repository Guidelines

## Project Structure & Module Organization

AgentHub is a multi-client monorepo. Python code lives in `backend/src`; keep routers in `app/api`, business logic in `app/services`, models in `db/models`, and migrations in `backend/alembic`. The React/Vite app is under `frontend/src`, organized by `features`, `pages`, `api`, `store`, and `types`; tests live in `frontend/tests`. Use `e2e` for Playwright, `docker` for deployment, `scripts` for repository utilities, and `docs` for architecture and operations. Electron and Capacitor/PWA clients live in `desktop-client` and `mobile-client`.

## Build, Test, and Development Commands

Use Python 3.11, `uv`, Node.js 20+, and `pnpm` for the main application.

```powershell
cd backend; uv sync --extra dev; uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
cd ../frontend; pnpm install; pnpm dev
```

```powershell
.\scripts\run-tests.ps1 -List
.\scripts\run-tests.ps1 -Stack backend -Module providers -Type unit
.\scripts\run-tests.ps1 -Stack frontend -Module models -Type component
```

The test runner refuses an unspecified run; use `-All` explicitly for a full stack. Run `uv run ruff check <paths>` or `pnpm exec eslint <paths>` for touched files, and `pnpm build` for frontend integration. Start the full stack with `docker compose --env-file docker/.env -f docker/docker-compose.yml up --build`.

## Coding Style & Naming Conventions

Python uses four-space indentation, type hints where practical, and Ruff’s 100-character line limit. Keep routers thin and add an Alembic migration with every schema change. TypeScript/TSX uses two-space indentation, ESLint, `PascalCase` for components and types, `camelCase` for functions and hooks, and existing SCSS partial conventions. Prefer focused changes within current module boundaries.

## Testing Guidelines

Name backend tests `test_*.py`, frontend tests `*.test.ts(x)`, and browser tests `*.spec.ts`. Classify new coverage in `scripts/test-groups.json` by module and type (`unit`, `integration`, `component`, or `live`). Live tests must require explicit credentials. No numeric coverage threshold is enforced.

## Commit & Pull Request Guidelines

Prefer imperative Conventional Commit subjects used by project guidance, such as `feat(agenthub): add workflow retry` or `docs(agenthub): update setup`. Keep commits small and independently reviewable. Before committing, run `git diff --check` and relevant quality gates. Pull requests should explain scope and validation, link related issues, call out migrations or configuration changes, and include screenshots for visible UI changes.

## Security & Configuration

Copy from `.env.example` or `docker/env.example`; never commit credentials, databases, logs, or generated artifacts. Production rejects placeholder `SECRET_KEY`, `DEBUG=true`, and sample database passwords. Provider keys belong in the encrypted credential field and must never be returned to the frontend.

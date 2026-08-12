# Repository Guidelines

## Project Structure & Module Organization

AgentHub is a multi-client monorepo. Active Python code lives in `backend/src`; keep API routers in `app/api`, business logic in `app/services`, database models in `db/models`, and migrations in `backend/alembic`. `backend/app-old` is historical reference only. The React/Vite application is under `frontend/src`, organized by `features`, `pages`, `api`, `store`, and `types`; its tests are in `frontend/tests`. Electron and Capacitor/PWA clients live in `desktop-client` and `mobile-client`. Use `e2e` for Playwright scenarios, `docker` for deployment files, `scripts` for repository utilities, and `docs` for architecture and operating guidance.

## Build, Test, and Development Commands

Use Python 3.11, `uv`, Node.js 20+, and `pnpm` for the main application.

```powershell
cd backend; uv sync --extra dev
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
uv run ruff check .; uv run pytest -q
```

```powershell
cd frontend; pnpm install; pnpm dev
pnpm lint; pnpm build
pnpm exec vitest run --config tests/vitest.config.ts
```

Run the complete stack from the repository root with `docker compose -f docker/docker-compose.yml up --build`. Client-specific scripts are documented in each client’s `README.md`; for example, `npm run check` validates desktop or mobile JavaScript.

## Coding Style & Naming Conventions

Python uses four-space indentation, type hints where practical, and Ruff’s 100-character line limit. Keep routers thin and add an Alembic migration with every schema change. TypeScript/TSX uses two-space indentation, ESLint, `PascalCase` for components and types, `camelCase` for functions and hooks, and existing SCSS partial conventions. Prefer focused changes within current module boundaries.

## Testing Guidelines

Name backend tests `test_*.py`, frontend tests `*.test.ts` or `*.test.tsx`, and browser tests `*.spec.ts`. Add regression coverage for changed behavior. Run targeted tests while iterating, then the relevant full Ruff/pytest or lint/build/Vitest gate before review. No numeric coverage threshold is enforced.

## Commit & Pull Request Guidelines

Prefer imperative Conventional Commit subjects used by project guidance, such as `feat(agenthub): add workflow retry` or `docs(agenthub): update setup`. Keep commits small and independently reviewable. Before committing, run `git diff --check` and relevant quality gates. Pull requests should explain scope and validation, link related issues, call out migrations or configuration changes, and include screenshots for visible UI changes.

## Security & Configuration

Copy from `.env.example` or `docker/env.example`; never commit credentials, local databases, logs, or generated artifacts. Do not expose provider secrets in frontend code or claim tool/deployment success without a persisted backend result.

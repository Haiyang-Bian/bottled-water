# Current Implementation Status

This file is the compact status reference for current docs. It avoids historical closure notes and only tracks present behavior and known boundaries.

## Stable For Local Development And Demos

- Authentication, open member registration, database-backed RBAC, administrator bootstrap, users, workspaces, projects, and conversation management.
- Single-agent and group conversations with persisted messages and streaming responses.
- New conversation defaults that select one Daily Chat Agent instead of preselecting every specialist agent.
- Actor-runtime multi-agent coordination with Team Leader planning, suitable-agent selection, visible progress, agent reports, and optional aggregated final deliverables.
- Agent directory and configurable model/tool/skill/MCP permissions.
- Tool catalog, built-in tools, invocation records, and permission checks.
- Interactive terminal tools for real CLI scaffolding and prompts, including stdin send, wait, snapshot, stop, and persisted invocation records.
- Unified external coding agent tool invocation for Codex, Claude Code, and compatible adapters, including persisted runs and status/cancel paths.
- Owner-scoped model provider management for Ark, OpenAI-compatible endpoints, and DeepSeek V4 Flash/Pro, including encrypted credentials and optional thinking mode.
- File upload, workspace file tree, preview/download operations, and attachment context.
- Artifact generation, preview, versioning, diff, export, and deployment preview records, including local/Docker-stack container mode.
- Workflow generation, editing, save/enable, run start, polling, runtime state persistence, and real node execution for supported node types.
- Skill and MCP management with probe, invoke, and recorded degraded failures when external runtimes are unavailable.
- Security operations for audit logs, roles, permissions, and user role changes.
- Docker compose deployment for nginx, backend, PostgreSQL, and Redis.

## Implemented With Environment-Dependent Degradation

- Real LLM responses depend on configured model provider credentials. Without keys, local mock/fallback behavior is expected.
- Office/PDF preview quality depends on available conversion tools in the runtime environment.
- Sandbox and external coding agent execution depend on installed command runtimes and workspace-safe cwd constraints.
- Interactive terminal sessions depend on the command runtime being installed and on a single live backend process for the active session.
- MCP stdio/HTTP calls depend on external server availability and declared transport support.
- Deployment preview validates accessible artifacts. Container mode is implemented as an AgentHub app or Docker Compose stack preview endpoint; full production cloud orchestration remains outside the current runtime.

## Recently Hardened

- Duplicate registration no longer logs into an existing account; disabled users and legacy Demo JWTs are rejected.
- Demo authentication and its username permission bypass were removed. Database roles and permissions are now the authorization source of truth, with final-administrator protection.
- Production rejects unsafe debug, secret, and sample database settings; unhandled errors return an `error_id` without exception details.
- Provider credentials are encrypted, owner-scoped, and write-only through the API; model configuration responses do not contain keys.
- Team Leader final messages are now emitted only from `scheduler.summary` when publication is appropriate; the old fallback that synthesized a Team Leader message after any multi-agent completion has been removed.
- Multi-agent progress uses short planned task labels and does not repeat the whole user prompt for every agent.
- Complex collaborative final answers aggregate source outputs, dependency chain, compliance checks, final products, and risks while preserving real artifact references.
- Group scheduling can select a suitable subset of agents instead of always using every participant.
- New chat creation defaults to Daily Chat Agent for both single and group-capable dialogs, leaving multi-agent selection explicit.
- External coding agent tools are unified under `external_agent.invoke`; legacy aliases are compatibility shims.
- User profile signatures are supported in the profile/settings flow.
- Workflow run state no longer relies on static 5% progress and merges persisted node/run state during polling.
- Workflow save accepts canvas object edges and updates conversation runtime mode when enabled.
- AI workflow generation uses backend generation logic instead of static examples.
- Ark streaming tool-call parsing now handles `finish_reason == "tool_calls"` correctly.
- Daily Chat context summaries now reflect default full tool permissions, including Claude Code tools.
- Docker one-command deployment now uses correct build contexts, backend `PYTHONPATH`, startup migrations, psycopg Postgres URLs, nginx streaming/WebSocket proxying, and Docker-specific env isolation.
- Container deployment mode now creates a health-checked artifact preview endpoint instead of failing with "runtime not enabled".
- Interactive CLI/scaffolding requests now prefer `terminal.*` tools over one-shot `sandbox.run`, and the sandbox panel exposes manual start/send/wait/snapshot/stop controls.

## Roadmap / Not A Current Guarantee

- Distributed multi-process runtime coordination without sticky sessions.
- Production-grade remote control and cloud deployment automation.
- Enterprise approval workflows and advanced audit search.
- Fully isolated production container sandbox policy. The current local sandbox has command/cwd/time/output controls, but production isolation belongs to deployment infrastructure.
- Broad document rendering parity across every Office feature without environment-specific conversion dependencies.

## Maintenance Rule

When a feature changes, update the closest source-of-truth document:

- Product flow: `docs/functional-guide.md`
- Code ownership: `docs/file-map.md`
- Backend/runtime design: `docs/backend-architecture.md` or `docs/agent-workflow-runtime.md`
- Events: `docs/event-protocol.md`
- Deployment: `docker/README.md` and `docs/development-guide.md`

# Current Implementation Status

> 2026-09-16：0.2.3 转向[原生使用闭环](./architecture/native-user-experience-0.2.3.md)。普通 CLI 明确使用 user 文件范围与正常网络；共享默认 workspace 不变。L4a/LPAC 暂缓，P3 代码、schema v6 与失败证据保留。下述旧阶段结果不代表强隔离已通过；多助手继续待实现。

This file is the compact status reference for current docs. It avoids historical closure notes and only tracks present behavior and known boundaries.

## Local Agent Environment With Persistent Memory

Originally recorded on 2026-09-15 against shared package 0.1.7 (`d43f07b`): [design](./architecture/local-agent-environment.md) and [development stages](./architecture/local-agent-roadmap.md). L1 global tasks shipped in 0.2.0, L2 foundational memory in 0.2.1, and L3 resource/software continuity in 0.2.2 with local schema v5; see [acceptance](./acceptance/resource-continuity-0.2.2.md). Objective resource observations are automatic; experience and preferences still need user adoption. L4 shared memory with fine-grained OS execution remains **not implemented**. AgentMemory remains scope-bound alongside independent memory and resource stores; native commands run as the current user without OS filesystem/network isolation.

## Architecture Split Status

The OS-style [system architecture](./architecture/README.md), [subsystem catalog](./architecture/subsystems.md), and [migration acceptance plan](./architecture/migration.md) were established on 2026-09-04. The local CLI MVP now uses the root `agenthub-system` distribution and shared Kernel/AgentLoop. It includes persistent sessions, trust, DPAPI/env credentials, file operations, PowerShell/Git, JSONL, replay and managed Windows processes. See [CLI acceptance](./architecture/cli-mvp.md) for measured results and remaining live-service validation. A standalone eval host and non-MVP subsystem extraction remain planned; desktop still packages the full Web host.

Runtime lifecycle and public ports already exist. CLI executes without application context or ORM; Web-specific Skill/MCP and product adapters remain in the Web host. The generic tool user-permission check also retains a warnings-only path; see [capability boundaries](./capability-data-boundaries.md).

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
- System target and module boundaries: `docs/architecture/README.md`, `docs/architecture/subsystems.md`, and `docs/architecture/migration.md`
- Current backend/runtime behavior: `docs/backend-architecture.md`, `docs/agent-workflow-runtime.md`, and `docs/runtime/current-state.md`
- Events: `docs/event-protocol.md`
- Deployment: `docker/README.md` and `docs/development-guide.md`

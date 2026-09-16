# Current Implementation Status

> 2026-09-16：0.2.3 转向[原生使用闭环](./architecture/native-user-experience-0.2.3.md)。普通 CLI 明确使用 user 文件范围与正常网络；共享默认 workspace 不变。L4a/LPAC 暂缓，P3 代码、schema v6 与失败证据保留。下述旧阶段结果不代表强隔离已通过；多助手继续待实现。

This file is the compact status reference for current docs. It avoids historical closure notes and only tracks present behavior and known boundaries.

## Local Agent Environment With Persistent Memory

Current shared package and backend: **0.2.3**; local SQLite: **schema v6**. The [design](./architecture/local-agent-environment.md) began against 0.1.7; that historical baseline is not the current version. L1 global tasks shipped in 0.2.0, L2 foundational memory in 0.2.1, and L3 resource/software continuity in 0.2.2. Objective resource observations are automatic; model-proposed experience and preferences still require adoption. AgentMemory remains scope-bound alongside independent memory and resource stores.

0.2.3 completes the native current-user workflow: cross-directory files and cwd, direct `process.run`, read-only `software.discover`, project interpreters, networking and external caches. Directory trust no longer gates native tasks. Elevated hosts cannot run model tasks or tool management; ordinary scripts are not strongly isolated. Saved LPAC tasks and restricted defaults require explicit conversion and never silently downgrade. L4a is paused; multi-assistant shared memory and handoff remain unimplemented.

The [0.2.3 acceptance record](./acceptance/native-user-experience-0.2.3.md) contains the installed DeepSeek A/B/C task, cancellation/resume, ConPTY, v1–v5 upgrades, Web regression and sidecar results. OpenAI-compatible, other platforms/builds, Docker and full LPAC certification were not run for this release. Test groups overlap and must not be summed. The [archive handoff](./operations/archive-handoff-0.2.3.md) maps source, wheel, local evidence and PR #30; this checkpoint does not imply the PR is merged.

## Architecture Split Status

The OS-style [system architecture](./architecture/README.md), [subsystem catalog](./architecture/subsystems.md), and [migration acceptance plan](./architecture/migration.md) were established on 2026-09-04. The CLI uses root `agenthub-system` and the shared Runtime → SingleAgentPolicy → AgentLoopExecutor chain. It includes global persistent tasks, DPAPI/env credentials, file/process operations, memory/resources, JSONL, replay and managed Windows process trees. CLI explicitly selects `file_access_scope=user`; the shared default stays `workspace`. A standalone eval host and remaining subsystem extraction are still planned; desktop packages the full Web host.

Runtime lifecycle and public ports already exist. CLI executes without application context or ORM; Web-specific Skill/MCP and product adapters remain in the Web host. The generic tool user-permission check also retains a warnings-only path; see [capability boundaries](./capability-data-boundaries.md).

## Web And Desktop: Existing Local Development Features

The following inventory concerns the Web host and its desktop packaging. It is not a claim that every feature is available in the standalone CLI or was retested in the 0.2.3 release.

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

## Web Features With Environment-Dependent Degradation

- Real LLM responses depend on configured provider credentials. Web mock/fallback paths are distinct from CLI behavior: the CLI reports missing configuration or credentials and does not substitute a successful mock task.
- Office/PDF preview quality depends on available conversion tools in the runtime environment.
- Sandbox and external coding agent execution depend on installed command runtimes and workspace-safe cwd constraints.
- Interactive terminal sessions depend on the command runtime being installed and on a single live backend process for the active session.
- MCP stdio/HTTP calls depend on external server availability and declared transport support.
- Deployment preview validates accessible artifacts. Container mode is implemented as an AgentHub app or Docker Compose stack preview endpoint; full production cloud orchestration remains outside the current runtime.

## Earlier Web Hardening

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
- Strong isolation for local native tools. The CLI runs with normal user file/network access; timeout and Job cleanup do not constitute an OS sandbox. Web command/cwd controls and retained LPAC experiments have separate boundaries.
- Broad document rendering parity across every Office feature without environment-specific conversion dependencies.

## Maintenance Rule

When a feature changes, update the closest source-of-truth document:

- Product flow: `docs/functional-guide.md`
- Code ownership: `docs/file-map.md`
- System target and module boundaries: `docs/architecture/README.md`, `docs/architecture/subsystems.md`, and `docs/architecture/migration.md`
- Current backend/runtime behavior: `docs/backend-architecture.md`, `docs/agent-workflow-runtime.md`, and `docs/runtime/current-state.md`
- Events: `docs/event-protocol.md`
- Deployment: `docker/README.md` and `docs/development-guide.md`

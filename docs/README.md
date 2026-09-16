# AgentHub Documentation

This directory contains the current documentation for AgentHub. Current-state documents remain the source of truth; the Runtime incubation area references selected historical commits as design evidence without restoring outdated plans as current behavior.

The [system architecture](./architecture/README.md) defines the split into a Runtime Kernel, reusable subsystems, drivers, and application hosts. The CLI MVP execution path now lives in root `src`; the subsystem catalog distinguishes migrated capabilities from remaining work.

The [local agent environment with persistent memory](./architecture/local-agent-environment.md) separates task continuity, working locations and resource permissions. L1 global tasks, L2 foundational memory and L3 resource/software continuity shipped in 0.2.0–0.2.2. Current **0.2.3 / local schema v6** adds native current-user access across directories, direct executable calls and normal networking. L4a/LPAC is paused; its retained code is not a completed isolation guarantee. Start with the [archive handoff](./operations/archive-handoff-0.2.3.md) for progress, evidence, local artifacts and the next-session checklist.

## Start Here

- [Local CLI](./cli.md): wheel installation without cloning, configuration, global tasks, native tools, troubleshooting and JSONL.
- [0.2.3 release and upgrade](./releases/0.2.3.md): behavior changes, preserved data and rollback.
- [0.2.3 acceptance](./acceptance/native-user-experience-0.2.3.md): installed DeepSeek tasks, terminal, upgrades, Web/sidecar checks and untested items.
- [Resources and software](./resources.md): resource metadata, optional pinned executables, task queries and source boundaries.
- [Foundational memory](./architecture/foundational-memory-l2.md): explicit saves, candidate adoption, cross-task recall, revisions and forgetting in 0.2.1.
- [Historical CLI MVP](./architecture/cli-mvp.md): original milestones and evidence at that release; use the current acceptance record for 0.2.3.
- [System architecture](./architecture/README.md): OS-style responsibilities, public contracts, state ownership, dependency rules, and Web/CLI/eval hosts.
- [Local agent environment design](./architecture/local-agent-environment.md): persistent identity, cross-task memory, resource discovery, working locations, sharing and authority boundaries.
- [Local agent development stages](./architecture/local-agent-roadmap.md): incremental delivery, migration, acceptance gates, failure cases and evidence requirements.
- [Subsystem and module catalog](./architecture/subsystems.md): Kernel, twelve subsystems, drivers, product modules, and their current source locations.
- [Architecture differences and migration](./architecture/migration.md): original coupling, current migration status, old-to-new mapping and acceptance criteria.
- [Development guide](./development-guide.md): local setup, Docker deployment, tests, and common workflows.
- [Product design](./product-design.md): product positioning, user scenarios, capability design, runtime flow, acceptance script, and delivery scope.
- [Feature guide](./functional-guide.md): product capabilities and user-facing flows.
- [File map](./file-map.md): where the important backend, frontend, test, and deployment files live.
- [Backend architecture](./backend-architecture.md): current FastAPI implementation and its integration boundaries; use the system architecture for the target design.
- [Workflow runtime](./agent-workflow-runtime.md): single chat, group chat, workflow canvas, node execution, and persisted run state.
- [Event protocol](./event-protocol.md): SSE/WebSocket event names and frontend merge behavior.
- [Capability and data boundaries](./capability-data-boundaries.md): permissions, data ownership, and runtime safety boundaries.
- [Security and model providers](./security-and-model-providers.md): RBAC, administrator bootstrap, secret handling, and DeepSeek setup.
- [Current status](./implementation-status.md): what is complete, what is hardened enough for demos, and what remains a roadmap item.
- [Runtime design incubation](./runtime/README.md): target architecture, invariants, current implementation gaps, and evidence-based evolution of the multi-agent runtime.
- [AI collaboration record](./ai-collaboration-record/README.md): Feishu-ready collaboration record, Spec, Rules, Skills, artifact links, and review checklist.

## Source Of Truth

- Shared system and CLI source of truth: root `src`; shared tests: root `tests`.
- Web backend source of truth: `backend/src`.
- Frontend source of truth: `frontend/src`.
- Local CLI schema source of truth: `src/agent_adapters/storage`, including its migration and schema modules; v6 retains paused permission tables.
- Web database schema source of truth: `backend/src/db/models` plus `backend/alembic/versions`; it is separate from CLI state.
- Deployment source of truth: `docker/docker-compose.yml`, `docker/Dockerfile.backend`, `docker/Dockerfile.frontend`, and `docker/nginx.conf`.

## Current Architecture At A Glance

The diagram below describes the Web host. The implemented local CLI uses the shared root `src` directly and supports global task recovery and approved cross-task memory. A standalone eval host remains planned. Desktop currently packages the full backend; this release does not enable Web memory management.

```text
React Workbench
  -> API client / SSE / WebSocket
  -> FastAPI routers
  -> chat, workflow, agent, tool, skill, MCP, file, artifact services
  -> SQLAlchemy models and Alembic migrations
  -> model providers, sandbox, external coding agents, deployment preview
```

Single-agent chats run the selected agent's loop. New conversations default to one Daily Chat Agent so ordinary chat starts simply. Group chats use conversation scheduling settings: when `workflow_enabled=true`, the saved workflow canvas is the execution plan; otherwise `AgentHubTeamLeadPolicy` proposes work through the shared Runtime Kernel.

Current multi-agent delivery behavior:

- Simple chat should stay single-agent and stream normally.
- Complex group tasks can be planned into short task steps and assigned to a suitable subset of agents.
- The Team Leader summary is produced by the scheduler summary event, not by a legacy fallback that concatenates every agent message.
- Final collaborative answers should aggregate sources, chain, checks, products, and risks, and include real artifact or deployment links when generated.

## Documentation Rules

- Keep docs tied to current code paths.
- Put long-lived architecture and operating guidance here.
- Avoid adding raw dated closure notes, brainstorming plans, or migration journals as current behavior; summarize durable design evidence separately from implementation status.
- If a roadmap item is not implemented, label it as roadmap instead of describing it as available behavior.

# AgentHub Documentation

This directory contains the current documentation for AgentHub. Current-state documents remain the source of truth; the Runtime incubation area references selected historical commits as design evidence without restoring outdated plans as current behavior.

## Start Here

- [Development guide](./development-guide.md): local setup, Docker deployment, tests, and common workflows.
- [Product design](./product-design.md): product positioning, user scenarios, capability design, runtime flow, acceptance script, and delivery scope.
- [Feature guide](./functional-guide.md): product capabilities and user-facing flows.
- [File map](./file-map.md): where the important backend, frontend, test, and deployment files live.
- [Backend architecture](./backend-architecture.md): FastAPI, persistence, runtime services, tools, skills, MCP, and workflow execution.
- [Workflow runtime](./agent-workflow-runtime.md): single chat, group chat, workflow canvas, node execution, and persisted run state.
- [Event protocol](./event-protocol.md): SSE/WebSocket event names and frontend merge behavior.
- [Capability and data boundaries](./capability-data-boundaries.md): permissions, data ownership, and runtime safety boundaries.
- [Security and model providers](./security-and-model-providers.md): RBAC, administrator bootstrap, secret handling, and DeepSeek setup.
- [Current status](./implementation-status.md): what is complete, what is hardened enough for demos, and what remains a roadmap item.
- [Runtime design incubation](./runtime/README.md): target architecture, invariants, current implementation gaps, and evidence-based evolution of the multi-agent runtime.
- [AI collaboration record](./ai-collaboration-record/README.md): Feishu-ready collaboration record, Spec, Rules, Skills, artifact links, and review checklist.

## Source Of Truth

- Backend source of truth: `backend/src`.
- Frontend source of truth: `frontend/src`.
- Database schema source of truth: `backend/src/db/models` plus `backend/alembic/versions`.
- Deployment source of truth: `docker/docker-compose.yml`, `docker/Dockerfile.backend`, `docker/Dockerfile.frontend`, and `docker/nginx.conf`.

## Current Architecture At A Glance

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

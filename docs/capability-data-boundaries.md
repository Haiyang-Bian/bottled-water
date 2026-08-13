# Capability And Data Boundaries

AgentHub exposes powerful capabilities through agents, tools, skills, MCP servers, sandbox commands, and external coding agents. This document defines the current boundaries.

## Permission Model

Agents can be configured with explicit permissions for:

- Built-in tools
- Custom tools
- Skills
- MCP tools
- Model configuration

When an agent uses default full permissions, backend execution and context summaries should agree on the effective tool list. Explicitly empty tool permissions stay empty.

## Tool Calls

Tool calls must pass:

- Agent authorization
- Tool availability
- Input schema validation
- Runtime safety checks

Successful and failed tool calls should be recorded through invocation records. User-facing claims about files, artifacts, deployment, external agents, or tests should be grounded in those records.

Team Leader final deliverables are subject to the same rule. A collaborative summary can aggregate and explain agent outputs, but artifact IDs, preview URLs, export URLs, deployment links, test results, and external-agent run status must come from persisted tool/runtime records, not from free-form text claims.

## Files And Workspaces

Workspace file operations must remain inside workspace-safe paths. File upload, read, write, preview, move, rename, and delete operations belong to backend file services. Frontend paths are display values, not authority.

## Sandbox

The local sandbox has command, cwd, timeout, and output controls. It is useful for development and demo execution. Production-grade process/container isolation is a deployment-infrastructure responsibility and remains a roadmap boundary.

## External Coding Agents

Codex and Claude Code adapters must:

- Probe runtime availability.
- Restrict cwd to safe workspace paths.
- Persist run status and output.
- Support cancel/status checks.
- Redact resolved command paths in user-facing probe payloads where appropriate.
- Default to non-interactive permission approval inside the external CLI after the AgentHub tool permission check has passed. Codex uses full-auto mode, and Claude Code skips its own interactive permission prompt by default.

The public tool catalog exposes `external_agent.invoke` as the unified entry point for run/probe/status/cancel actions. Legacy provider-specific names are compatibility aliases only. External agents should never be treated as successful unless the run record says so.

Compatible adapters such as OpenCode follow the same boundary: declared provider, safe cwd, persisted run facts, and explicit degraded status when the runtime is unavailable.

## MCP

MCP invocation depends on registered server configuration and transport availability. Probe and invocation failures should return explicit degraded/failure results, not fake successful responses.

## Secrets

Secrets belong in backend environment/configuration or database provider records:

- Model provider keys
- JWT secret
- Database credentials
- External runtime credentials

Frontend code and frontend environment values must not contain real provider secrets. Model provider keys use the encrypted `api_key_ref` column; responses expose `api_key_set`, and key rotation uses the dedicated credential endpoint.

## Conversation And Context Data

Context builders can include:

- Current conversation history
- Current workspace context
- Attachments and extracted text
- Tool results
- Workflow variables
- Shared blackboard facts
- Current agent private context

Context must not include unrelated conversation history unless explicitly intended through workspace memory or user-selected context.

Multi-agent group context must also preserve speaker boundaries. One agent should not impersonate another, and the Team Leader should not copy every raw agent message as a final answer. For simple or single-agent turns, `scheduler.summary.publish_message=false` is the expected way to avoid an unnecessary Team Leader restatement.

## Deployment Preview

Deployment preview records are local/demo deployment facts. They can prove that an artifact preview URL was created and health-checked. They are not proof of production cloud deployment unless a production runtime is explicitly implemented.

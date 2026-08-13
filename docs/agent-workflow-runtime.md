# Agent And Workflow Runtime

This document describes the current chat and workflow runtime behavior.

## Conversation Scheduling

Each conversation has a chat type and scheduling settings.

- Single chat: run the selected agent. New conversation defaults prefer the Daily Chat Agent so ordinary chat remains simple.
- Group chat without workflow enabled: run `AgentHubTeamLeadPolicy` through the shared Runtime Kernel.
- Group chat with `workflow_enabled=true`: run the saved workflow canvas.

The workflow flag and `scheduling_strategy` are synchronized when a workflow is saved and enabled from the UI.

## Single-Agent Chat

```text
user message
  -> save Message
  -> build context for selected Agent
  -> call model
  -> optional tool calls
  -> persist tool invocations and final message
  -> stream events to frontend
```

The context builder includes relevant conversation history, attachments, persisted tool results, workspace context, effective authorized tools, and current agent identity.

## Group Chat

Group chat can use a planning/orchestration path or a workflow path. The runtime must not let one agent impersonate another agent. Group context lists members and current-agent constraints.

`AgentHubTeamLeadPolicy` reads an isolated `PolicySnapshot` and returns a `SchedulingProposal`; it does not own state or publish control events. The Kernel validates the proposal and emits:

- `scheduler.decision`: frontend projection of the validated scheduling proposal.
- `control.assign`: concrete task input for one or more agents.
- `agent.report`: persisted agent work result and tool-event summary.
- `system.session_completed` / `failed` / `cancelled`: projected Run terminal state.

Important behavior:

- Simple requests should select only the necessary Agent scope.
- Mentioned agents restrict the turn to the mentioned scope unless the prompt is explicitly collaborative.
- Complex group turns can select a suitable subset of available agents; the runtime does not have to use every participant.
- Reviewer and deploy-style agents should normally depend on upstream implementation/content outputs.
- Agent reports and final Run output must only reference real Adapter results.

The old SchedulerAgent and synthesized Team Leader fallback have been removed. Run state and `runtime_runs` are the lifecycle truth; generation metadata remains an AgentHub read model.

## Workflow Canvas

A workflow is stored on the conversation and contains:

- `nodes`
- `edges`
- `settings`
- scheduling/workflow metadata

The backend accepts array-style edges and canvas object edges. It normalizes them before persistence and preserves handles/config where needed.

Supported node categories include:

- `start`
- `agent`
- `tool`
- `skill`
- `mcp`
- `condition`
- `loop`
- `review`
- `artifact`
- `end`

## Workflow Generation

AI generation uses the backend workflow generation path. It should produce a real workflow draft based on conversation agents and context, not a static sample that only looks like AI output.

The frontend can still edit JSON/canvas state before saving.

## Workflow Execution

```text
start run
  -> create WorkflowRun
  -> normalize graph
  -> execute nodes
  -> call agents/tools/skills/MCP through shared runtime services
  -> update node_states
  -> publish events and polling state
  -> persist final status
```

Run polling merges persisted run and node state so the UI reflects real progress. Failures should move the run to a terminal failed state with an error message instead of staying at an artificial progress value.

## Tool And Agent Nodes

Agent nodes call the same agent loop used by chat. Tool nodes call the same tool executor used by function calling. This keeps permission checks, invocation records, and result shapes consistent.

External coding agent workflow nodes can call `external_agent.invoke` when the agent has permission. Legacy external-agent tool aliases are mapped internally to the unified invocation path.

## Runtime State

Workflow runtime state is stored in:

- `WorkflowRun.status`
- `WorkflowRun.progress`
- `WorkflowRun.node_states`
- conversation workflow/runtime metadata
- persisted messages and tool invocations

Chat Runtime state is stored separately in `runtime_runs` and `runtime_context_states`.

When updating nested JSON fields, use the runtime helpers that flag SQLAlchemy JSON changes.

## Failure Handling

Expected behavior:

- Invalid workflow input returns validation errors.
- Unsupported node types fail clearly.
- Tool permission failures are recorded as tool failures.
- Node failures update node state.
- Run failures become terminal and visible to polling.

Retry/skip/stop policy support is service-level behavior, not a frontend illusion.

## Frontend Runtime Surfaces

- Embedded workflow studio: `frontend/src/features/workflow`
- Workflow board/settings panel: `frontend/src/features/platform/tabs/WorkflowBoardPanel`
- Workflow API client: `frontend/src/api/workflow.ts`
- Workflow types: `frontend/src/types/workflow.ts`
- Runtime decision/progress strip: `frontend/src/features/chat/components/ChatPanel/RuntimeDecisionStrip.tsx`
- Runtime event normalization: `frontend/src/lib/runtimeEvents.ts`

The frontend should prefer backend run state over local timers for real workflow execution status.

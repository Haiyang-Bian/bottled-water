# Event Protocol

AgentHub uses REST for ordinary state, SSE for streaming assistant responses, and WebSocket for compatible realtime chat paths.

## SSE Endpoint

```text
GET /api/v1/conversations/{conversation_id}/stream
```

The frontend also sends messages through REST endpoints that return/trigger stream events. See `frontend/src/api/message.ts` for merge behavior.

## Common Events

| Event | Purpose |
| --- | --- |
| `message_start` | Create or identify a streaming assistant message. |
| `content_block_delta` | Append text or reasoning delta. |
| `tool_call_start` | Show a tool call in progress. |
| `tool_call_done` | Merge tool result and mark call complete. |
| `message:new` | Insert a newly persisted message, such as an artifact card. |
| `message:updated` | Replace or merge a persisted message update. |
| `generation:failed` | Mark generation failed and clear running state. |
| `generation:cancelled` | Mark generation cancelled and clear running state. |
| `message_stop` | Mark normal streaming completion. |

## Runtime Coordination Events

Actor group chat and workflow-adjacent runtimes also publish structured runtime events. The frontend stores these under the active generation and renders concise progress instead of treating them as ordinary chat text.

| Event | Purpose |
| --- | --- |
| `scheduler.plan` | Team Leader plan for the current turn, including short task labels and target agent IDs. |
| `scheduler.decision` | Scheduler decision after repair/guardrails, such as assign, parallel, wait, or complete. |
| `control.assign` | Runtime assignment sent to one or more agents. |
| `agent.tool_call` | Agent is calling a tool. |
| `agent.tool_result` | Tool result returned and persisted where applicable. |
| `agent.report` | Agent produced a work report for the scheduler. |
| `scheduler.summary` | Runtime summary and optional Team Leader final deliverable. |
| `control.complete` | Scheduler completed the turn and emitted terminal summary metadata. |

`scheduler.summary.publish_message=false` means the summary is runtime metadata only and should not create a visible Team Leader chat bubble. This is expected for simple or single-agent turns. Complex collaborative turns can publish a visible Team Leader message with aggregated source, chain, checks, products, and risks.

Events should include stable IDs when possible:

- `conversation_id`
- `message_id` or `agent_message_id`
- `client_message_id`
- `agent_id`
- `agent_name`
- `tool_call_id`

Stable IDs prevent duplicate bubbles when reconnecting or merging final persisted messages.

## Frontend Merge Rules

Frontend stream handling should:

- Create a placeholder message on `message_start`.
- Append deltas to the same message ID.
- Merge final persisted messages without duplicating the streaming placeholder.
- Clear local running-conversation state on success, failure, or cancellation.
- Keep tool call summaries separate from final user-visible answer text.
- Render runtime plan/progress from `scheduler.plan`, `scheduler.decision`, `agent.report`, and `scheduler.summary` without duplicating raw agent transcripts.
- Prefer backend short task labels for progress rows; avoid repeating the whole user prompt under every agent.

Relevant code:

- `frontend/src/api/message.ts`
- `frontend/src/lib/runtimeEvents.ts`
- `frontend/src/lib/message.ts`
- `frontend/src/lib/runningConversations.ts`
- `frontend/src/store/useMessageStore.ts`

## WebSocket

Conversation WebSocket endpoint:

```text
/ws/conversations/{conversation_id}?token=...
```

Global compatibility endpoint:

```text
/ws
```

The Docker nginx config proxies both `/ws/` and `/ws` to the backend.

## Backend Event Sources

- Message streaming: `backend/src/app/api/messages.py`
- WebSocket chat: `backend/src/app/api/websocket.py`
- Realtime services: `backend/src/app/services/realtime`
- Chat finalization/cancellation: `backend/src/app/services/chat`
- Runtime events: `src/agent_runtime`

## Protocol Contract

- Do not fabricate terminal success. If a model, tool, MCP server, sandbox, or external agent fails, emit a failure event and persist the error where appropriate.
- Tool and artifact cards should come from persisted tool/artifact results.
- Long-running streams must have a terminal event so the frontend can clear running state.

import type { StreamAssistantHandlers, MessageBody } from "@/types/messages";
import type { ChatMessage } from "@/types";
import { API_BASE, get, post, sse } from "./client";
import { getConversationWS } from "./websocket";

type PendingAck = {
  resolve: (value: string) => void;
  timeout: number;
};

type WsStreamSubscription = {
  message: () => void;
  open: () => void;
};

type BufferedRuntimeEvent = {
  event: string;
  data: Record<string, unknown>;
};

type RuntimeCursorState = {
  runId: string;
  lastSequence: number;
  pending: Map<number, BufferedRuntimeEvent>;
  pendingEventIds: Set<string>;
};

type RuntimeEventLogItem = {
  event_id: string;
  run_id: string;
  sequence: number;
  type: string;
  payload: Record<string, unknown>;
  projected_type?: string;
  projected_payload?: Record<string, unknown>;
};

type RuntimeEventPage = {
  items: RuntimeEventLogItem[];
  next_sequence: number;
  last_sequence: number;
  terminal: boolean;
  history_complete: boolean;
};

const wsStreamSubscriptions = new Map<string, WsStreamSubscription>();
const wsStreamHandlers = new Map<string, StreamAssistantHandlers>();
const wsPendingAcks = new Map<string, Map<string, PendingAck>>();
const wsActiveStreams = new Set<string>();
const wsRuntimeCursors = new Map<string, RuntimeCursorState>();
const wsReplayTasks = new Map<string, Promise<void>>();

export async function messages(conversationId: string): Promise<ChatMessage[]> {
  const result = await get<{ items: ChatMessage[] } | ChatMessage[]>(
    `/conversations/${conversationId}/messages`,
  );

  const items = Array.isArray(result) ? result : result.items;
  return items.map(normalizeChatMessage);
}

/**
 * 解析 SSE 流，逐条产出 {event, data}。
 * SSE 格式：event: xxx\ndata: {...}\n\n
 */
interface SSEEvent {
  id?: string;
  event: string;
  data: unknown;
  retry?: number;
}

async function* parseSSEStream(
  reader: ReadableStreamDefaultReader<Uint8Array>,
): AsyncGenerator<SSEEvent> {
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        buffer += decoder.decode();
        break;
      }
      buffer += decoder.decode(value, { stream: true });

      // ✅ 修复：兼容 \r\n\r\n 和 \n\n 两种分隔符
      const events = buffer.split(/\r?\n\r?\n/);
      buffer = events.pop() || "";

      for (const raw of events) {
        if (!raw.trim()) continue;
        const event = parseEvent(raw);
        if (event) yield event;
      }
    }

    if (buffer.trim()) {
      const event = parseEvent(buffer);
      if (event) yield event;
    }
  } finally {
    reader.releaseLock();
  }
}

// ✅ 修复：行分割也兼容 \r\n 和 \n
function parseEvent(raw: string): SSEEvent | null {
  const lines = raw.split(/\r?\n/);
  let event = "message";
  let id: string | undefined;
  const dataLines: string[] = [];

  for (const line of lines) {
    if (line === "" || line.startsWith(":")) continue;

    const colonIdx = line.indexOf(":");
    if (colonIdx === -1) continue;

    const field = line.slice(0, colonIdx);
    const value = line.slice(colonIdx + 1).startsWith(" ")
      ? line.slice(colonIdx + 2)
      : line.slice(colonIdx + 1);

    switch (field) {
      case "event":
        event = value.replace(/\r$/, "");
        break; // 去掉尾部 \r
      case "data":
        dataLines.push(value.replace(/\r$/, ""));
        break;
      case "id":
        id = value.replace(/\r$/, "");
        break;
    }
  }

  if (dataLines.length === 0) return null;

  const dataStr = dataLines.join("\n");
  try {
    return { id, event, data: JSON.parse(dataStr) };
  } catch {
    return { id, event, data: dataStr };
  }
}

/**
 * 分发流式事件到对应的 handlers。
 * SSE 与 WebSocket 共用的事件处理逻辑。
 */
function dispatchStreamEvent(
  event: string,
  data: unknown,
  handlers: StreamAssistantHandlers,
): void {
  switch (event) {
    case "message_start":
      handlers.onMessageStart?.(data as Record<string, unknown>);
      break;
    case "content_block_delta": {
      const payload = data as Record<string, unknown>;
      const delta = payload.delta as Record<string, unknown> | undefined;
      const text = String(delta?.text || "");
      if (!text) break;
      if (delta?.type === "reasoning_delta") {
        handlers.onReasoningDelta?.(text, payload);
      } else {
        handlers.onDelta?.(text, payload);
      }
      break;
    }
    case "message_stop":
      handlers.onMessageEnd?.(data as Record<string, unknown>);
      handlers.onMessageStop?.(data as Record<string, unknown>);
      break;
    case "task:status_changed":
      handlers.onTaskStatusChanged?.(data as Record<string, unknown>);
      break;
    case "message:new":
      handlers.onMessageNew?.(normalizeChatMessage(data as ChatMessage));
      break;
    case "message:updated":
      handlers.onMessageUpdated?.(normalizeChatMessage(data as ChatMessage));
      break;
    case "conversation:updated":
      handlers.onRuntimeEvent?.(event, data as Record<string, unknown>);
      break;
    case "error": {
      const payload =
        data && typeof data === "object"
          ? (data as Record<string, unknown>)
          : { message: String(data || "WebSocket request failed") };
      handlers.onRuntimeEvent?.("generation:failed", {
        status: "failed",
        error: payload.error ?? payload.message ?? "websocket_error",
        ...payload,
      });
      handlers.onDone?.(payload);
      break;
    }
    case "generation_finished":
    case "generation:finished":
    case "generation:cancelled":
    case "generation:failed":
    case "cancelled":
    case "failed":
      handlers.onRuntimeEvent?.(event, data as Record<string, unknown>);
      handlers.onDone?.(data as Record<string, unknown>);
      break;
    case "workflow_completed":
    case "workflow:completed":
    case "workflow:run_completed":
    case "workflow_cancelled":
    case "workflow:cancelled":
    case "workflow_failed":
    case "workflow:failed":
      handlers.onRuntimeEvent?.(event, data as Record<string, unknown>);
      break;

    // 运行时事件：Session 生命周期
    case "system.session_started":
      handlers.onRuntimeEvent?.(event, data as Record<string, unknown>);
      break;
    case "system.session_completed":
    case "system.session_cancelled":
      handlers.onRuntimeEvent?.(event, data as Record<string, unknown>);
      handlers.onDone?.(data as Record<string, unknown>);
      break;
    case "system.session_error":
      handlers.onRuntimeEvent?.(event, data as Record<string, unknown>);
      handlers.onDone?.(data as Record<string, unknown>);
      break;

    // 运行时事件：Round / Agent 生命周期
    case "system.round_started":
      break;
    case "system.agent_started":
      handlers.onRuntimeEvent?.(event, data as Record<string, unknown>);
      break;
    case "system.agent_completed":
    case "system.agent_failed":
      handlers.onRuntimeEvent?.(event, data as Record<string, unknown>);
      break;

    case "scheduler.plan":
    case "scheduler.decision":
    case "scheduler.summary":
    case "agent.state_changed":
      handlers.onRuntimeEvent?.(event, data as Record<string, unknown>);
      break;
    case "agent.report":
    case "agent.failed":
    case "control.complete":
    case "control.cancel":
      handlers.onRuntimeEvent?.(event, data as Record<string, unknown>);
      break;
    case "agent.tool_call":
      handlers.onRuntimeEvent?.(event, data as Record<string, unknown>);
      handlers.onToolCallStart?.(data as Record<string, unknown>);
      break;
    case "agent.tool_result": {
      const payload = data as Record<string, unknown>;
      handlers.onRuntimeEvent?.(event, payload);
      const toolName = String(payload.tool || payload.tool_name || "");
      handlers.onToolCallDone?.({
        tool_name: toolName,
        status: payload.success === false ? "failed" : "succeeded",
        result: payload.result,
        error: payload.error,
      });
      break;
    }

    // 运行时事件：工具调用
    case "agent.tool_calls_executed": {
      const payload = data as Record<string, unknown>;
      const toolEvents = payload.tool_events as Record<string, unknown>[];
      if (Array.isArray(toolEvents)) {
        for (const te of toolEvents) {
          const teRecord = te as Record<string, unknown>;
          const toolCallId = String(teRecord.call_id ?? "");
          const toolName = String(
            teRecord.tool_name ??
              (teRecord.function as Record<string, unknown>)?.name ??
              "",
          );
          if (toolCallId && toolName) {
            handlers.onToolCallStart?.({
              ...payload,
              tool_call_id: toolCallId,
              tool_name: toolName,
            });
            handlers.onToolCallDone?.({
              ...payload,
              tool_call_id: toolCallId,
              tool_name: toolName,
            });
          }
        }
      }
      break;
    }

    // 流式 token 和思考过程（增量追加）
    case "agent.thinking": {
      const pt = data as Record<string, unknown>;
      const agentId = String(pt.agent_id || "");
      const thinking = String(pt.thinking || "");
      if (agentId && thinking) handlers.onThinking?.(agentId, thinking, pt);
      break;
    }
    case "agent.token": {
      const p = data as Record<string, unknown>;
      const agentId = String(p.agent_id || "");

      if (!agentId) break;
      const token = String(p.token || "");
      if (token) handlers.onToken?.(agentId, token, p);
      break;
    }

    // 控制类 / 用户事件：前端暂不直接展示
    case "control.watchdog_triggered":
      handlers.onRuntimeEvent?.(event, data as Record<string, unknown>);
      handlers.onDone?.({
        ...(data as Record<string, unknown>),
        status: "failed",
      });
      break;
    case "control.scheduling_decision":
    case "control.escalation":
    case "user.waiting_for_input":
    case "user.input_received":
    case "user.input_queued":
      handlers.onRuntimeEvent?.(event, data as Record<string, unknown>);
      break;
    case "collaboration.message_created":
    case "collaboration.message_consumed":
    case "collaboration.thread_resolved":
    case "collaboration.rejected":
      handlers.onRuntimeEvent?.(event, data as Record<string, unknown>);
      break;
  }
}

function stringValue(value: unknown): string {
  return typeof value === "string" && value ? value : "";
}

export async function sendMessage(
  conversationId: string,
  body: MessageBody,
  handlers: StreamAssistantHandlers,
): Promise<string> {
  const abortController = new AbortController();
  const stop = () => {
    abortController.abort();
    handlers.onDone?.();
  };

  handlers.onControl?.(stop);

  // 120s 兜底超时
  const timeout = window.setTimeout(() => {
    abortController.abort();
    handlers.onDone?.();
  }, 120000);

  const response = await sse(
    `/conversations/${conversationId}/stream`,
    body,
    abortController,
  );

  if (!response.ok) {
    return `${response.status} ${response.statusText}`;
  }

  if (!response.body) {
    return `no body in response: ${response.status} ${response.statusText}`;
  }

  const reader = response.body.getReader();

  for await (const { event, data } of parseSSEStream(reader)) {
    if (event === "system.session_error") {
      window.clearTimeout(timeout);
    }
    dispatchStreamEvent(event, data, handlers);
  }

  window.clearTimeout(timeout);
  handlers.onDone?.();

  return "ok";
}

export function streamAssistantReply(
  conversationId: string,
  handlers: StreamAssistantHandlers,
): Promise<string> {
  let accumulated = "";
  let settled = false;
  const source = new EventSource(
    `${API_BASE}/conversations/${conversationId}/stream`,
  );
  const finish = (payload?: Record<string, unknown>) => {
    if (settled) return;
    settled = true;
    source.close();
    handlers.onDone?.(payload);
  };

  return new Promise((resolve) => {
    const complete = (payload?: Record<string, unknown>) => {
      finish(payload);
      resolve(accumulated || "ok");
    };
    const handle = (eventName: string) => (event: MessageEvent) => {
      const data = event.data ? JSON.parse(event.data) : {};
      if (eventName === "content_block_delta") {
        const payload = data as Record<string, unknown>;
        const delta = payload.delta as Record<string, unknown> | undefined;
        const text = String(delta?.text || "");
        accumulated += text;
      }
      if (
        eventName === "generation_finished" ||
        eventName === "generation:finished" ||
        eventName === "generation:cancelled" ||
        eventName === "generation:failed" ||
        eventName === "cancelled" ||
        eventName === "failed" ||
        eventName === "system.session_completed" ||
        eventName === "system.session_cancelled" ||
        eventName === "system.session_error"
      ) {
        handlers.onRuntimeEvent?.(eventName, data as Record<string, unknown>);
        complete(data as Record<string, unknown>);
        return;
      }
      dispatchStreamEvent(eventName, data, handlers);
    };

    [
      "message_start",
      "content_block_delta",
      "message_stop",
      "task:status_changed",
      "workflow_completed",
      "workflow:completed",
      "workflow:run_completed",
      "message:updated",
      "scheduler.plan",
      "scheduler.decision",
      "scheduler.summary",
      "agent.state_changed",
      "agent.report",
      "agent.failed",
      "agent.tool_call",
      "agent.tool_result",
      "agent.tool_calls_executed",
      "control.complete",
      "control.cancel",
      "control.watchdog_triggered",
      "generation_finished",
      "generation:finished",
      "generation:cancelled",
      "generation:failed",
      "workflow_cancelled",
      "workflow:cancelled",
      "workflow_failed",
      "workflow:failed",
      "cancelled",
      "failed",
      "system.session_completed",
      "system.session_cancelled",
      "system.session_error",
    ].forEach((eventName) => source.addEventListener(eventName, handle(eventName)));
    source.onerror = () => complete({ reason: "event_source_error" });
  });
}

export async function cancelAssistantReply(
  conversationId: string,
): Promise<{ cancelled: boolean }> {
  return await post<{ cancelled: boolean }>(
    `/conversations/${conversationId}/stream/cancel`,
    {},
  );
}

/**
 * WebSocket 版本的消息发送。
 *
 * 保持与 sendMessage 相同的接口，内部通过 WebSocket 长连接传输。
 */
export async function sendMessageWs(
  conversationId: string,
  body: MessageBody,
  handlers: StreamAssistantHandlers,
): Promise<string> {
  const ws = getConversationWS(conversationId);
  if (ws.readyState !== WebSocket.OPEN) {
    await ws.connect();
  }

  const requestId = `req-${Date.now()}`;
  wsStreamHandlers.set(conversationId, handlers);
  ensureConversationStreamSubscription(conversationId, handlers);

  return new Promise((resolve) => {
    const stop = () => {
      ws.send("chat.cancel", {}, requestId);
      wsActiveStreams.delete(conversationId);
      handlers.onDone?.();
    };
    handlers.onControl?.(stop);

    const timeout = window.setTimeout(() => {
      resolvePendingAck(conversationId, requestId, "timeout");
    }, 15000);
    pendingAcksFor(conversationId).set(requestId, { resolve, timeout });

    ws.send("chat.send", body, requestId);
  });
}

function normalizeChatMessage(message: ChatMessage): ChatMessage {
  const record =
    message && typeof message === "object"
      ? (message as unknown as Record<string, unknown>)
      : {};
  const rawContent =
    (record.rawContent as Record<string, unknown> | undefined) ??
    (record.raw_content as Record<string, unknown> | undefined);
  const clientMessageId = stringValue(
    record.client_message_id ??
      record.clientMessageId ??
      rawContent?.client_message_id ??
      rawContent?.clientMessageId,
  );
  const normalizedRawContent =
    (rawContent || clientMessageId)
      ? {
          ...(rawContent || {}),
          ...(clientMessageId
            ? {
                client_message_id: clientMessageId,
                clientMessageId,
              }
            : {}),
        }
      : undefined;
  const attachments = Array.isArray(record.attachments)
    ? (record.attachments as ChatMessage["attachments"])
    : [];
  const toolEvents = Array.isArray(record.toolEvents)
    ? record.toolEvents
    : Array.isArray(record.tool_events)
      ? record.tool_events
      : undefined;

  return {
    ...(message as ChatMessage),
    id: String(record.id ?? record.message_id ?? ""),
    conversationId: String(
      record.conversationId ?? record.conversation_id ?? "",
    ),
    sender_id:
      typeof record.sender_id === "string"
        ? record.sender_id
        : typeof record.senderId === "string"
          ? record.senderId
          : undefined,
    sender_avatar_url:
      typeof record.sender_avatar_url === "string"
        ? record.sender_avatar_url
        : typeof record.senderAvatarUrl === "string"
          ? record.senderAvatarUrl
          : undefined,
    role: String(record.role ?? "assistant") as ChatMessage["role"],
    kind: String(record.kind ?? record.content_type ?? "text") as ChatMessage["kind"],
    author: String(
      record.author ?? record.sender_name ?? record.senderName ?? record.sender_type ?? "Agent",
    ),
    content: String(record.content ?? ""),
    thinking:
      typeof record.thinking === "string" ? record.thinking : undefined,
    rawContent: normalizedRawContent,
    client_message_id: clientMessageId || undefined,
    clientMessageId: clientMessageId || undefined,
    attachments,
    toolEvents: toolEvents as ChatMessage["toolEvents"],
    createdAt: String(record.createdAt ?? record.created_at ?? new Date().toISOString()),
    quotedMessageId:
      typeof record.quotedMessageId === "string"
        ? record.quotedMessageId
        : typeof record.reply_to_message_id === "string"
          ? record.reply_to_message_id
          : undefined,
    status:
      typeof record.status === "string" ? record.status : undefined,
    streamState:
      typeof record.streamState === "string"
        ? (record.streamState as ChatMessage["streamState"])
        : undefined,
    state:
      record.state === "inactive" ? "inactive" : "active",
  };
}

function runtimeCursorStorageKey(conversationId: string): string {
  return `agenthub:runtime-cursor:${conversationId}`;
}

function readRuntimeCursor(conversationId: string): RuntimeCursorState | undefined {
  try {
    const raw = window.sessionStorage.getItem(runtimeCursorStorageKey(conversationId));
    if (!raw) return undefined;
    const parsed = JSON.parse(raw) as { run_id?: unknown; last_sequence?: unknown };
    const runId = stringValue(parsed.run_id);
    const lastSequence = Number(parsed.last_sequence);
    if (!runId || !Number.isSafeInteger(lastSequence) || lastSequence < 0) {
      return undefined;
    }
    return {
      runId,
      lastSequence,
      pending: new Map(),
      pendingEventIds: new Set(),
    };
  } catch {
    return undefined;
  }
}

function persistRuntimeCursor(conversationId: string, state: RuntimeCursorState): void {
  try {
    window.sessionStorage.setItem(
      runtimeCursorStorageKey(conversationId),
      JSON.stringify({ run_id: state.runId, last_sequence: state.lastSequence }),
    );
  } catch {
    // Storage can be unavailable in privacy-restricted browser contexts.
  }
}

function runtimeMetadata(data: unknown): {
  runId: string;
  eventId: string;
  sequence: number;
} | null {
  if (!data || typeof data !== "object") return null;
  const payload = data as Record<string, unknown>;
  const runId = stringValue(payload.runtime_run_id ?? payload.generation_id);
  const eventId = stringValue(payload.runtime_event_id);
  const sequence = Number(payload.runtime_sequence);
  if (!runId || !eventId || !Number.isSafeInteger(sequence) || sequence < 1) {
    return null;
  }
  return { runId, eventId, sequence };
}

function finishWebSocketEvent(conversationId: string, event: string): void {
  if (!isTerminalWsEvent(event)) return;
  wsActiveStreams.delete(conversationId);
  resolveConversationPendingAcks(conversationId, "completed");
}

function dispatchOrderedRuntimeEvent(
  conversationId: string,
  event: string,
  data: unknown,
  handlers: StreamAssistantHandlers,
): void {
  const metadata = runtimeMetadata(data);
  if (!metadata) {
    dispatchStreamEvent(event, data, handlers);
    finishWebSocketEvent(conversationId, event);
    return;
  }

  let state = wsRuntimeCursors.get(conversationId);
  if (!state || state.runId !== metadata.runId) {
    const stored = readRuntimeCursor(conversationId);
    state =
      stored?.runId === metadata.runId
        ? stored
        : {
            runId: metadata.runId,
            lastSequence: 0,
            pending: new Map(),
            pendingEventIds: new Set(),
          };
    wsRuntimeCursors.set(conversationId, state);
  }

  if (
    metadata.sequence <= state.lastSequence ||
    state.pending.has(metadata.sequence) ||
    state.pendingEventIds.has(metadata.eventId)
  ) {
    return;
  }

  const payload = data as Record<string, unknown>;
  state.pending.set(metadata.sequence, { event, data: payload });
  state.pendingEventIds.add(metadata.eventId);

  let next = state.pending.get(state.lastSequence + 1);
  while (next) {
    const nextSequence = state.lastSequence + 1;
    state.pending.delete(nextSequence);
    const nextEventId = stringValue(next.data.runtime_event_id);
    if (nextEventId) state.pendingEventIds.delete(nextEventId);
    dispatchStreamEvent(next.event, next.data, handlers);
    state.lastSequence = nextSequence;
    persistRuntimeCursor(conversationId, state);
    finishWebSocketEvent(conversationId, next.event);
    next = state.pending.get(state.lastSequence + 1);
  }
}

async function replayRuntimeEvents(
  conversationId: string,
  handlers: StreamAssistantHandlers,
): Promise<void> {
  const existing = wsReplayTasks.get(conversationId);
  if (existing) return existing;

  const task = (async () => {
    let state = wsRuntimeCursors.get(conversationId) ?? readRuntimeCursor(conversationId);
    if (!state || !wsActiveStreams.has(conversationId)) return;
    wsRuntimeCursors.set(conversationId, state);
    const runId = state.runId;

    while (wsActiveStreams.has(conversationId)) {
      const current = wsRuntimeCursors.get(conversationId);
      if (!current || current.runId !== runId) return;
      const after = current.lastSequence;
      const page = await get<RuntimeEventPage>(
        `/conversations/${encodeURIComponent(conversationId)}/runtime/runs/${encodeURIComponent(runId)}/events?after_sequence=${after}&limit=200`,
      );
      if (wsRuntimeCursors.get(conversationId)?.runId !== runId) return;

      for (const item of page.items) {
        if (wsRuntimeCursors.get(conversationId)?.runId !== runId) return;
        const projectedPayload = item.projected_payload ?? {
          ...item.payload,
          runtime_event_id: item.event_id,
          runtime_sequence: item.sequence,
          runtime_run_id: item.run_id,
          runtime_replayed: true,
        };
        dispatchOrderedRuntimeEvent(
          conversationId,
          item.projected_type ?? item.type,
          projectedPayload,
          handlers,
        );
      }

      state = wsRuntimeCursors.get(conversationId)!;
      if (
        !wsActiveStreams.has(conversationId) ||
        state.lastSequence >= page.last_sequence ||
        page.items.length === 0 ||
        page.next_sequence <= after
      ) {
        return;
      }
    }
  })();

  wsReplayTasks.set(conversationId, task);
  try {
    await task;
  } finally {
    if (wsReplayTasks.get(conversationId) === task) {
      wsReplayTasks.delete(conversationId);
    }
  }
}

function ensureConversationStreamSubscription(
  conversationId: string,
  handlers: StreamAssistantHandlers,
): void {
  if (wsStreamSubscriptions.has(conversationId)) return;

  const ws = getConversationWS(conversationId);
  const unsubscribeMessage = ws.onMessage((event, data, requestId) => {
    const activeHandlers = wsStreamHandlers.get(conversationId) ?? handlers;
    if (event === "chat.ack") {
      wsActiveStreams.add(conversationId);
      resolvePendingAck(conversationId, requestId, "ok");
      return;
    }

    dispatchOrderedRuntimeEvent(conversationId, event, data, activeHandlers);
  });
  const unsubscribeOpen = ws.onOpen(() => {
    const activeHandlers = wsStreamHandlers.get(conversationId) ?? handlers;
    void replayRuntimeEvents(conversationId, activeHandlers).catch(() => undefined);
  });
  wsStreamSubscriptions.set(conversationId, {
    message: unsubscribeMessage,
    open: unsubscribeOpen,
  });
}

function pendingAcksFor(conversationId: string): Map<string, PendingAck> {
  let pending = wsPendingAcks.get(conversationId);
  if (!pending) {
    pending = new Map();
    wsPendingAcks.set(conversationId, pending);
  }
  return pending;
}

function resolvePendingAck(
  conversationId: string,
  requestId: string | undefined,
  result: string,
): void {
  const pending = wsPendingAcks.get(conversationId);
  if (!pending || pending.size === 0) return;
  const key = requestId && pending.has(requestId) ? requestId : pending.keys().next().value;
  if (!key) return;
  const ack = pending.get(key);
  if (!ack) return;
  window.clearTimeout(ack.timeout);
  pending.delete(key);
  if (pending.size === 0) {
    wsPendingAcks.delete(conversationId);
  }
  ack.resolve(result);
}

function resolveConversationPendingAcks(
  conversationId: string,
  result: string,
): void {
  const pending = wsPendingAcks.get(conversationId);
  if (!pending || pending.size === 0) return;
  for (const [key, ack] of pending.entries()) {
    window.clearTimeout(ack.timeout);
    ack.resolve(result);
    pending.delete(key);
  }
  wsPendingAcks.delete(conversationId);
}

function isTerminalWsEvent(event: string): boolean {
  return [
    "system.session_completed",
    "generation_finished",
    "generation:finished",
    "generation:cancelled",
    "generation:failed",
    "cancelled",
    "failed",
    "system.session_cancelled",
    "system.session_error",
  ].includes(event);
}

/**
 * WebSocket 版本的取消助手回复。
 */
export function cancelAssistantReplyWs(conversationId: string): void {
  const ws = getConversationWS(conversationId);
  if (ws.readyState === WebSocket.OPEN) {
    ws.send("chat.cancel", {});
  }
}

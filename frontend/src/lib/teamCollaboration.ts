import type { Conversation, TeamMessage } from "@/types";

const pendingByConversation = new Map<string, Map<number, TeamMessage>>();

export function applyTeamCollaborationEvent(
  conversation: Conversation,
  event: string,
  payload: Record<string, unknown>,
): Partial<Conversation> {
  if (event === "collaboration.message_created") {
    const message = teamMessageFromPayload(conversation.id, payload);
    if (!message) return {};
    const current = conversation.team_activity || [];
    const lastSequence = conversation.team_last_sequence ?? maxSequence(current);
    if (
      message.sequence <= lastSequence ||
      current.some((item) => item.message_id === message.message_id)
    ) {
      return {};
    }
    const pending = pendingFor(conversation.id);
    if (
      pending.has(message.sequence) ||
      [...pending.values()].some((item) => item.message_id === message.message_id)
    ) {
      return {};
    }
    pending.set(message.sequence, message);
    const drained = drainPending(conversation.id, lastSequence);
    if (!drained.items.length) return {};
    const activity = dedupeAndSort([...current, ...drained.items]);
    persistTeamCursor(conversation.id, drained.lastSequence);
    return {
      team_activity: activity.slice(-500),
      team_last_sequence: drained.lastSequence,
    };
  }

  if (event === "collaboration.message_consumed") {
    const messageId = stringValue(payload.message_id);
    const agentId = stringValue(payload.agent_id);
    if (!messageId) return {};
    return {
      team_activity: (conversation.team_activity || []).map((item) =>
        item.message_id === messageId
          ? {
              ...item,
              status: "consumed",
              consumed_by: Array.from(new Set([...item.consumed_by, agentId])).filter(Boolean),
            }
          : item,
      ),
    };
  }

  if (event === "collaboration.thread_resolved") {
    const threadId = stringValue(payload.thread_id);
    if (!threadId) return {};
    return {
      team_activity: (conversation.team_activity || []).map((item) =>
        item.thread_id === threadId
          ? {
              ...item,
              status: "resolved",
              resolved_at: new Date().toISOString(),
            }
          : item,
      ),
    };
  }

  return {};
}

export function mergeTeamMessageHistory(
  conversation: Conversation,
  history: TeamMessage[],
): Partial<Conversation> {
  const merged = dedupeAndSort([...(conversation.team_activity || []), ...history]);
  let lastSequence = maxSequence(merged);
  const drained = drainPending(conversation.id, lastSequence);
  if (drained.items.length) {
    merged.push(...drained.items);
    lastSequence = drained.lastSequence;
  }
  const activity = dedupeAndSort(merged).slice(-500);
  persistTeamCursor(conversation.id, lastSequence);
  return { team_activity: activity, team_last_sequence: lastSequence };
}

export function readTeamCursor(conversationId: string): number {
  try {
    const raw = window.sessionStorage.getItem(teamCursorKey(conversationId));
    const value = Number(raw);
    return Number.isSafeInteger(value) && value >= 0 ? value : 0;
  } catch {
    return 0;
  }
}

function teamMessageFromPayload(
  conversationId: string,
  payload: Record<string, unknown>,
): TeamMessage | undefined {
  const messageId = stringValue(payload.message_id);
  const sequence = Number(payload.team_sequence);
  if (!messageId || !Number.isSafeInteger(sequence) || sequence < 1) return undefined;
  return {
    message_id: messageId,
    run_id: stringValue(payload.runtime_run_id ?? payload.generation_id),
    conversation_id: conversationId,
    sequence,
    sender_type: stringValue(payload.sender_type) || "agent",
    sender_id: stringValue(payload.sender_id),
    recipient_agent_ids: stringArray(payload.recipient_agent_ids),
    channel: stringValue(payload.channel) || "broadcast",
    thread_id: optionalString(payload.thread_id),
    reply_to_message_id: optionalString(payload.reply_to_message_id),
    content: stringValue(payload.content),
    expects_reply: Boolean(payload.expects_reply),
    status: stringValue(payload.status) || "pending",
    consumed_by: stringArray(payload.consumed_by),
    created_at: stringValue(payload.created_at) || new Date().toISOString(),
    resolved_at: optionalString(payload.resolved_at),
    replayed: Boolean(payload.runtime_replayed),
  };
}

function pendingFor(conversationId: string): Map<number, TeamMessage> {
  let pending = pendingByConversation.get(conversationId);
  if (!pending) {
    pending = new Map();
    pendingByConversation.set(conversationId, pending);
  }
  return pending;
}

function drainPending(
  conversationId: string,
  startingSequence: number,
): { items: TeamMessage[]; lastSequence: number } {
  const pending = pendingFor(conversationId);
  const items: TeamMessage[] = [];
  let lastSequence = startingSequence;
  let next = pending.get(lastSequence + 1);
  while (next) {
    pending.delete(lastSequence + 1);
    items.push(next);
    lastSequence += 1;
    next = pending.get(lastSequence + 1);
  }
  return { items, lastSequence };
}

function dedupeAndSort(items: TeamMessage[]): TeamMessage[] {
  const byId = new Map<string, TeamMessage>();
  const sequenceIds = new Map<number, string>();
  for (const item of items) {
    if (!item.message_id || byId.has(item.message_id)) continue;
    if (sequenceIds.has(item.sequence)) continue;
    byId.set(item.message_id, item);
    sequenceIds.set(item.sequence, item.message_id);
  }
  return [...byId.values()].sort((left, right) => left.sequence - right.sequence);
}

function maxSequence(items: TeamMessage[]): number {
  return items.reduce((value, item) => Math.max(value, item.sequence), 0);
}

function persistTeamCursor(conversationId: string, sequence: number): void {
  try {
    window.sessionStorage.setItem(teamCursorKey(conversationId), String(sequence));
  } catch {
    // Session storage can be unavailable in privacy-restricted browser contexts.
  }
}

function teamCursorKey(conversationId: string): string {
  return `agenthub:team-cursor:${conversationId}`;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function optionalString(value: unknown): string | null {
  return stringValue(value) || null;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(stringValue).filter(Boolean) : [];
}

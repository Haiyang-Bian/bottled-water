import { get, post, patch, del } from "./client";
import type { Conversation, TeamMessage } from "@/types";

export async function conversations(workspaceId?: string): Promise<Conversation[]> {
  const query = workspaceId
    ? `?workspace_id=${encodeURIComponent(workspaceId)}`
    : "";
  const result = await get<{ items: Conversation[] } | Conversation[]>(
    `/conversations${query}`,
  );
  return Array.isArray(result) ? result : result.items;
}

export async function createConversation(group = false): Promise<Conversation> {
  return await post<Conversation>("/conversations", { chat_type: group ? "group" : "single" });
}

export async function createConversationWithAgents(payload: {
  chat_type: "single" | "group";
  title?: string;
  participant_agent_ids: string[];
  master_enabled?: boolean;
  scheduling_strategy?: "workflow" | "tech_lead" | "single_agent" | "collaborative";
  workflow_enabled?: boolean;
  summary_agent_id?: string;
  live_user_input?: boolean;
  max_collaboration_messages?: number;
  max_agent_turns?: number;
  max_open_threads?: number;
  max_team_message_chars?: number;
  workspace_id?: string;
  folder?: string;
  category?: string;
}): Promise<Conversation> {
  return await post<Conversation>("/conversations", payload);
}

export async function updateConversation(
  id: string,
  patchData: Partial<Conversation>,
): Promise<Conversation> {
  if (
    "scheduling_strategy" in patchData ||
    "workflow_enabled" in patchData ||
    "team_settings" in patchData
  ) {
    const team = patchData.team_settings;
    return await patch<Conversation>(`/conversations/${id}`, {
      action: "runtime",
      scheduling_strategy: patchData.scheduling_strategy,
      workflow_enabled: patchData.workflow_enabled,
      summary_agent_id: team?.summary_agent_id,
      live_user_input: team?.live_user_input,
      max_collaboration_messages: team?.max_collaboration_messages,
      max_agent_turns: team?.max_agent_turns,
      max_open_threads: team?.max_open_threads,
      max_team_message_chars: team?.max_team_message_chars,
    });
  }
  return await patch<Conversation>(`/conversations/${id}`, {
    action:
      "pinned" in patchData
        ? patchData.pinned
          ? "pin"
          : "unpin"
        : "archived" in patchData
          ? patchData.archived
            ? "archive"
            : "unarchive"
          : "rename",
    title: patchData.title,
    folder: patchData.folder,
    category: patchData.category,
    remark: patchData.remark,
  });
}

export interface TeamMessagePage {
  conversation_id: string;
  items: TeamMessage[];
  next_sequence: number;
  last_sequence: number;
}

export async function teamMessages(
  conversationId: string,
  afterSequence = 0,
  limit = 200,
): Promise<TeamMessagePage> {
  return await get<TeamMessagePage>(
    `/conversations/${encodeURIComponent(conversationId)}/team/messages?after_sequence=${afterSequence}&limit=${limit}`,
  );
}

export async function deleteConversation(
  id: string,
): Promise<{ id: string; deleted_at?: string }> {
  return await del<{ id: string; deleted_at?: string }>(`/conversations/${id}`);
}

export async function addParticipants(
  conversationId: string,
  agentIds: string[],
): Promise<Conversation> {
  return await post<Conversation>(
    `/conversations/${conversationId}/participants`,
    { agent_ids: agentIds, role: "member" },
  );
}

export async function removeParticipant(
  conversationId: string,
  participantId: string,
): Promise<Conversation> {
  return await del<Conversation>(
    `/conversations/${conversationId}/participants/${participantId}`,
  );
}

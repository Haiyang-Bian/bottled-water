import { get, post, patch, del } from "./client";
import type { Conversation } from "@/types";

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
  scheduling_strategy?: "workflow" | "tech_lead" | "single_agent";
  workflow_enabled?: boolean;
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
    "workflow_enabled" in patchData
  ) {
    return await patch<Conversation>(`/conversations/${id}`, {
      action: "runtime",
      scheduling_strategy: patchData.scheduling_strategy,
      workflow_enabled: patchData.workflow_enabled,
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

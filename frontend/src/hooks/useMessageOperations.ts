import { api } from "@/api";
import { useCallback, useEffect, useRef } from "react";
import {
  useConversationStore,
  useMessageStore,
} from "@/store";
import { makeMessage } from "@/lib";
import { applyRuntimeEvent } from "@/lib/runtimeEvents";
import { applyTeamCollaborationEvent } from "@/lib/teamCollaboration";
import {
  clearConversationThinkingMode,
  setConversationThinkingMode,
  useStreamingMessages,
} from "./useStreamingMessages";
import type { ChatMessage, Conversation, UploadedFile, MessageAttachment } from "@/types";
import type {
  MessageAgentMention,
  MessageBody,
  MessageFileReference,
  StreamAssistantHandlers,
} from "@/types/messages";

function mergeThinkingFromLocal(
  fetchedMessages: ChatMessage[],
  localMessages: ChatMessage[],
): ChatMessage[] {
  return fetchedMessages.map((message) => {
    if (message.role !== "assistant" || String(message.thinking || "").trim()) {
      return message;
    }
    const local = localMessages.find(
      (item) =>
        item.id === message.id ||
        (item.role === message.role &&
          item.sender_id === message.sender_id &&
          item.content.trim() &&
          item.content.trim() === message.content.trim()),
    );
    const localThinking = String(
      local?.thinking || local?.rawContent?._streamRawThinking || "",
    ).trim();
    if (!localThinking) {
      return message;
    }
    const localThinkingEnabled =
      local?.rawContent?._streamThinkingEnabled === true ||
      local?.rawContent?.thinking_enabled === true;
    return {
      ...message,
      thinking: localThinking,
      rawContent: {
        ...(message.rawContent || {}),
        thinking_enabled:
          message.rawContent?.thinking_enabled ?? localThinkingEnabled,
        _streamThinkingEnabled:
          message.rawContent?._streamThinkingEnabled ?? localThinkingEnabled,
        _streamRawThinking: localThinking,
      },
    };
  });
}

/**
 * 封装聊天消息发送与流式响应状态同步。
 */
function conversationSnapshotFromPayload(
  payload: Record<string, unknown>,
): (Partial<Conversation> & { id: string }) | undefined {
  const nested = payload.conversation;
  const record =
    nested && typeof nested === "object"
      ? (nested as Record<string, unknown>)
      : payload;
  const id =
    typeof record.id === "string"
      ? record.id
      : typeof record.conversation_id === "string"
        ? record.conversation_id
        : "";
  return id ? ({ ...record, id } as Partial<Conversation> & { id: string }) : undefined;
}

export function useMessageOperations(userName?: string, userAvatarUrl?: string) {
  const { activeId } = useConversationStore();
  const { updateMessagesForConversation } = useMessageStore();
  const streaming = useStreamingMessages(activeId);
  const streamingRef = useRef(streaming);

  useEffect(() => {
    streamingRef.current = streaming;
  }, [streaming]);

  const send = useCallback(
    async (
      content: string,
      quoted?: ChatMessage,
      attachments: UploadedFile[] = [],
      thinkingEnabled?: boolean,
      modelConfigId?: string,
      fileReferences: MessageFileReference[] = [],
      agentMentions: MessageAgentMention[] = [],
    ) => {
      if (!activeId) return;

      const conversationId = activeId;
      const activeConversation = useConversationStore
        .getState()
        .conversations.find((item) => item.id === conversationId);
      const workflowEnabled = Boolean(activeConversation?.workflow_enabled);
      const schedulingStrategy =
        activeConversation?.chat_type === "single"
          ? "single_agent"
          : workflowEnabled
            ? "workflow"
            : "tech_lead";
      const localAttachments = [
        ...normalizeAttachments(attachments),
        ...attachmentsFromFileReferences(fileReferences),
      ];
      const clientMessageId = `client-${Date.now()}-${Math.random()
        .toString(16)
        .slice(2)}`;

      const userMessage = makeMessage({
        conversationId,
        role: "user",
        kind: "text",
        author: userName || "我",
        content,
        sender_avatar_url: userAvatarUrl,
        rawContent: {
          client_message_id: clientMessageId,
          clientMessageId,
          attachments: localAttachments,
          file_references: fileReferences,
          agent_mentions: agentMentions,
        },
        clientMessageId,
        client_message_id: clientMessageId,
        streamState: "done",
        state: "active",
        attachments: localAttachments,
        quotedMessageId: quoted?.id,
      });
      updateMessagesForConversation(conversationId, (prev) => [...prev, userMessage]);

      const body: MessageBody = {
        content_type: "text",
        content: {
          text: content,
          attachments: localAttachments.map((file) => ({
            file_id: file.file_id ?? file.id,
            filename: file.filename,
            content_type: file.content_type,
            size: file.size,
          })),
          file_references: fileReferences,
          agent_mentions: agentMentions,
        },
        reply_to_message_id: quoted?.id,
        thinking_enabled: thinkingEnabled,
        model_config_id: modelConfigId,
        client_message_id: clientMessageId,
        scheduling_strategy: schedulingStrategy,
      };

      markConversationRunning(conversationId);
      setConversationThinkingMode(conversationId, Boolean(thinkingEnabled));

      try {
        await api.sendMessageWs(
          conversationId,
          body,
          createStreamHandlers(
            conversationId,
            streamingRef.current.streamHandlers,
            streamingRef.current.waitForConversationStreams,
          ),
        );
      } catch {
        clearConversationThinkingMode(conversationId);
        clearConversationRunning(conversationId);
      }
    },
    [
      activeId,
      updateMessagesForConversation,
      userAvatarUrl,
      userName,
    ],
  );

  const cancel = useCallback(
    async (conversationId = activeId) => {
      if (!conversationId) return;
      try {
        api.cancelAssistantReplyWs(conversationId);
        await api.cancelAssistantReply(conversationId);
      } finally {
        clearConversationThinkingMode(conversationId);
        streamingRef.current.clearPendingStreams(conversationId);
        clearConversationRunning(conversationId);
        updateMessagesForConversation(conversationId, (prev) =>
          prev.map((item) =>
            item.conversationId === conversationId &&
            (item.status === "streaming" || item.streamState === "streaming")
              ? {
                  ...item,
                  status: "cancelled",
                  streamState: "done",
                }
              : item,
          ),
        );
      }
    },
    [activeId, updateMessagesForConversation],
  );

  return {
    send,
    cancel,
    streamingMessages: streaming.streamingMessages,
    displayOrder: streaming.displayOrder,
  };
}

function normalizeAttachments(files: UploadedFile[]): MessageAttachment[] {
  return files.map((file) => ({
    file_id: file.file_id ?? file.id,
    id: file.id,
    filename: file.filename,
    original_filename: file.original_filename,
    content_type: file.content_type,
    size: file.size,
    parse_status: file.parse_status,
    extracted_text: file.extracted_text,
    public_url: file.public_url,
    download_url: file.download_url,
    metadata: file.metadata,
  }));
}

function attachmentsFromFileReferences(
  references: MessageFileReference[],
): MessageAttachment[] {
  return references.map((reference) => {
    const filename =
      reference.filename ?? reference.path.split("/").pop() ?? reference.path;
    return {
      id: reference.node_id ?? reference.file_id ?? `workspace:${reference.path}`,
      file_id: reference.file_id ?? `workspace:${reference.path}`,
      filename,
      original_filename: filename,
      content_type: reference.content_type,
      size: reference.size,
      parse_status: "referenced",
      metadata: {
        reference_type: "workspace_file",
        path: reference.path,
        display_path: reference.display_path,
        source: reference.source,
      },
    };
  });
}

function createStreamHandlers(
  conversationId: string,
  baseHandlers: StreamAssistantHandlers,
  waitForConversationStreams: (
    conversationId?: string,
    timeoutMs?: number,
  ) => Promise<void>,
): StreamAssistantHandlers {
  return {
    ...baseHandlers,
    onRuntimeEvent: (event, payload) => {
      baseHandlers.onRuntimeEvent?.(event, payload);
      const store = useConversationStore.getState();
      if (event === "conversation:updated") {
        const snapshot = conversationSnapshotFromPayload(payload);
        if (snapshot) {
          store.updateConversation(snapshot.id, snapshot);
        }
        return;
      }
      const current = store.conversations.find((item) => item.id === conversationId);
      if (current) {
        const runtimePatch = applyRuntimeEvent(current, event, payload);
        const teamPatch = applyTeamCollaborationEvent(current, event, payload);
        store.updateConversation(
          conversationId,
          { ...runtimePatch, ...teamPatch },
        );
      }
      if (isTerminalRuntimeEvent(event)) {
        clearConversationThinkingMode(conversationId);
        clearConversationRunning(conversationId);
      }
    },
    onDone: (payload) => {
      baseHandlers.onDone?.(payload);
      clearConversationThinkingMode(conversationId);
      clearConversationRunning(conversationId);
      if (useConversationStore.getState().activeId !== conversationId) {
        return;
      }
      waitForConversationStreams(conversationId)
        .then(() => new Promise((resolve) => window.setTimeout(resolve, 350)))
        .then(() => api.messages(conversationId))
        .then((nextMessages) => {
          if (useConversationStore.getState().activeId !== conversationId) return;
          const messageStore = useMessageStore.getState();
          const localMessages =
            messageStore.getCachedMessages(conversationId) ??
            (messageStore.historyConversationId === conversationId
              ? messageStore.historyMessages
              : []);
          messageStore.setMessagesForConversation(
            conversationId,
            mergeThinkingFromLocal(nextMessages, localMessages),
          );
        })
        .catch(() => {
          // keep current optimistic / streaming state if refresh fails
        });
    },
  };
}

function markConversationRunning(conversationId: string) {
  const store = useConversationStore.getState();
  store.updateConversation(conversationId, { generation_status: "running" });
  store.updateLocalRunningConversationIds((current) => {
    const next = new Set(current);
    next.add(conversationId);
    return next;
  });
}

function clearConversationRunning(conversationId: string) {
  const store = useConversationStore.getState();
  store.updateConversation(conversationId, { generation_status: "idle" });
  store.updateLocalRunningConversationIds((current) => {
    const next = new Set(current);
    next.delete(conversationId);
    return next;
  });
}

function isTerminalRuntimeEvent(event: string) {
  return [
    "system.session_completed",
    "system.session_cancelled",
    "system.session_error",
    "generation_finished",
    "generation:finished",
    "generation:cancelled",
    "generation:failed",
    "workflow_completed",
    "workflow:completed",
    "workflow:run_completed",
    "workflow_cancelled",
    "workflow:cancelled",
    "workflow_failed",
    "workflow:failed",
    "cancelled",
    "failed",
    "control.cancel",
    "control.watchdog_triggered",
  ].includes(event);
}

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  CloudUploadOutlined,
  BulbOutlined,
  FileSearchOutlined,
  RobotOutlined,
  SendOutlined,
  StopOutlined,
} from "@ant-design/icons";
import {
  App as AntApp,
  Avatar,
  Button,
  Empty,
  Flex,
  Layout,
  Mentions,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from "antd";
import type { UploadProps } from "antd";
import { api } from "@/api";
import { MessageBubble } from "@/features/chat/components/MessageBubble";
import { RuntimeDecisionStrip } from "@/features/chat/components/ChatPanel/RuntimeDecisionStrip";
import { TeamActivity } from "@/features/chat/components/ChatPanel/TeamActivity";
import {
  displayNodeName,
  displayNodePath,
  walk,
} from "@/features/workspaceFiles/workspaceFileUtils";
import { useMessageStore, useConversationStore } from "@/store";
import { useMessageOperations } from "@/hooks";
import { mergeVisibleMessagesForDisplay } from "@/lib/messageOrder";
import type {
  ChatMessage,
  Conversation,
  ModelConfig,
  UploadedFile,
  WorkspaceFileNode,
} from "@/types";
import type { MessageAgentMention, MessageFileReference } from "@/types/messages";

declare global {
  interface Window {
    agentHubDesktop?: {
      onScreenshotCaptured?: (
        callback: (payload: {
          filename?: string;
          contentType?: string;
          dataUrl?: string;
          prompt?: string;
          capturedAt?: string;
        }) => void,
      ) => () => void;
    };
  }
}

const { Content } = Layout;
const { Text } = Typography;
const AGENT_MODEL_SENTINEL = "__agent_model__";
const QUEUED_SEND_LIMIT = 5;
const UPLOAD_ACCEPT =
  ".txt,.md,.markdown,.json,.csv,.tsv,.html,.htm,.xml,.pdf,.docx,.xlsx,.pptx,.png,.jpg,.jpeg,.gif,.webp,.bmp,.svg,.py,.js,.jsx,.ts,.tsx,.css,.scss,.zip";
const ALLOWED_UPLOAD_EXTENSIONS = new Set(UPLOAD_ACCEPT.split(","));
const BLOCKED_UPLOAD_EXTENSIONS = new Set([
  ".exe",
  ".dll",
  ".bat",
  ".cmd",
  ".ps1",
  ".msi",
  ".com",
  ".scr",
  ".vbs",
  ".lnk",
  ".reg",
]);

type WorkspaceFileOption = {
  value: string;
  label: ReactNode;
  searchText: string;
  node: WorkspaceFileNode;
};

type AgentMentionOption = {
  value: string;
  label: ReactNode;
  searchText: string;
  agent: MessageAgentMention;
};

type QueuedComposerMessage = {
  id: string;
  content: string;
  quoted?: ChatMessage;
  attachments: UploadedFile[];
  thinkingEnabled: boolean;
  modelConfigId?: string;
  fileReferences: MessageFileReference[];
  agentMentions: MessageAgentMention[];
};

export function ChatPanel({
  active,
  loading,
  userName,
  userAvatarUrl,
  defaultModelConfigId,
  onPreviewArtifact,
}: {
  active?: Conversation;
  loading: boolean;
  userName?: string;
  userAvatarUrl?: string;
  defaultModelConfigId?: string;
  onPreviewArtifact?: (message: ChatMessage) => void;
}) {
  const [text, setText] = useState("");
  const [quoted, setQuoted] = useState<ChatMessage>();
  const [pendingFiles, setPendingFiles] = useState<UploadedFile[]>([]);
  const [contextReferences, setContextReferences] = useState<
    MessageFileReference[]
  >([]);
  const [agentMentions, setAgentMentions] = useState<MessageAgentMention[]>([]);
  const [workspaceFileOptions, setWorkspaceFileOptions] = useState<
    WorkspaceFileOption[]
  >([]);
  const [workspaceFilesLoading, setWorkspaceFilesLoading] = useState(false);
  const [contextSelectOpen, setContextSelectOpen] = useState(false);
  const [contextSearchValue, setContextSearchValue] = useState("");
  const [awaitingResponse, setAwaitingResponse] = useState(false);
  const [queuedMessagesByConversation, setQueuedMessagesByConversation] =
    useState<Map<string, QueuedComposerMessage[]>>(new Map());
  const [modelConfigs, setModelConfigs] = useState<ModelConfig[]>([]);
  const composerRef = useRef<HTMLDivElement>(null);
  const queuedMessagesRef = useRef(queuedMessagesByConversation);
  const dispatchingQueuedRef = useRef(false);
  const { message } = AntApp.useApp();
  const { send, cancel, streamingMessages, displayOrder } = useMessageOperations(
    userName,
    userAvatarUrl,
  );
  const localRunningConversationIds = useConversationStore(
    (s) => s.localRunningConversationIds,
  );
  const [stopping, setStopping] = useState(false);

  // 从 Conversation Store 读取当前会话的思考模式状态（持久化）
  const thinkingEnabled = useConversationStore((s) =>
    active ? s.getThinkingEnabled(active.id) : false,
  );
  const setThinkingEnabled = useCallback(
    (enabled: boolean) => {
      if (active) {
        useConversationStore.getState().setThinkingEnabled(active.id, enabled);
      }
    },
    [active],
  );
  const selectedModelConfigId = useConversationStore((s) =>
    active ? s.getSelectedModelConfigId(active.id) : undefined,
  );
  const setSelectedModelConfigId = useCallback(
    (value: string) => {
      if (active) {
        useConversationStore.getState().setSelectedModelConfigId(active.id, value);
      }
    },
    [active],
  );
  const activeConversationId = active?.id;
  const activeGenerationStatus = active?.generation_status;
  const activeQueuedMessages = useMemo(
    () =>
      activeConversationId
        ? (queuedMessagesByConversation.get(activeConversationId) ?? [])
        : [],
    [activeConversationId, queuedMessagesByConversation],
  );

  useEffect(() => {
    queuedMessagesRef.current = queuedMessagesByConversation;
  }, [queuedMessagesByConversation]);

  const commitQueuedMessages = useCallback(
    (next: Map<string, QueuedComposerMessage[]>) => {
      queuedMessagesRef.current = next;
      setQueuedMessagesByConversation(next);
    },
    [],
  );

  useEffect(() => {
    if (!active?.id) return;
    const snippet = useConversationStore.getState().consumeDraftSnippet(active.id);
    if (!snippet) return;
    setText((current) => `${current}${current && !current.endsWith(" ") ? " " : ""}${snippet}`);
  }, [activeConversationId]);

  useEffect(() => {
    setQuoted(undefined);
    setContextReferences([]);
    setAgentMentions([]);
    setContextSelectOpen(false);
    setContextSearchValue("");
  }, [activeConversationId]);

  useEffect(() => {
    if (!active?.workspace_id) {
      setWorkspaceFileOptions([]);
      setContextSelectOpen(false);
      setContextSearchValue("");
      return;
    }
    let alive = true;
    setWorkspaceFilesLoading(true);
    api
      .workspaceFileTree(active.workspace_id)
      .then((tree) => {
        if (!alive) return;
        setWorkspaceFileOptions(workspaceFileOptionsFromTree(tree.root));
      })
      .catch(() => {
        if (alive) setWorkspaceFileOptions([]);
      })
      .finally(() => {
        if (alive) setWorkspaceFilesLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [active?.workspace_id]);

  // 历史消息从 Store 读取
  const historyMessages = useMessageStore((s) => s.historyMessages);
  const historyConversationId = useMessageStore((s) => s.historyConversationId);
  const messageVersions = useMessageStore((s) => s.messageVersions);
  const activeHistoryMessages = useMemo(() => {
    if (!activeConversationId || historyConversationId !== activeConversationId) return [];
    return historyMessages.filter((item) => item.conversationId === activeConversationId);
  }, [activeConversationId, historyConversationId, historyMessages]);

  // 合并所有消息用于 quoted 查找
  const visibleMessages = useMemo(
    () =>
      mergeVisibleMessagesForDisplay(
        activeHistoryMessages,
        streamingMessages,
        displayOrder,
      ),
    [activeHistoryMessages, displayOrder, streamingMessages],
  );
  const chatDefaultModelConfigId =
    defaultModelConfigId ||
    modelConfigs.find((item) => item.status === "active")?.id ||
    modelConfigs[0]?.id;
  const chatModelSelectValue =
    selectedModelConfigId || chatDefaultModelConfigId || AGENT_MODEL_SENTINEL;
  const runtimeModelConfigId =
    chatModelSelectValue === AGENT_MODEL_SENTINEL ? undefined : chatModelSelectValue;
  const modelOptions = useMemo(
    () => [
      ...modelConfigs.map((item) => ({
        label: `${item.name || item.model_id} · ${item.provider_name || item.model_id}`,
        value: item.id,
      })),
      {
        label: "跟随 Agent 绑定模型",
        value: AGENT_MODEL_SENTINEL,
      },
    ],
    [modelConfigs],
  );
  const agentMentionOptions = useMemo(
    () => agentMentionOptionsFromParticipants(active?.participants ?? []),
    [active?.participants],
  );

  const clearComposerState = useCallback(() => {
    setText("");
    setQuoted(undefined);
    setPendingFiles([]);
    setContextReferences([]);
    setAgentMentions([]);
    setContextSelectOpen(false);
    setContextSearchValue("");
  }, []);

  const restoreComposerMessage = useCallback((item: QueuedComposerMessage) => {
    setText(item.content);
    setQuoted(item.quoted);
    setPendingFiles(item.attachments);
    setContextReferences(item.fileReferences);
    setAgentMentions(item.agentMentions);
  }, []);

  const removeQueuedMessage = useCallback(
    (conversationId: string, itemId: string) => {
      const current = queuedMessagesRef.current;
      const queue = current.get(conversationId) ?? [];
      const nextQueue = queue.filter((item) => item.id !== itemId);
      const next = new Map(current);
      if (nextQueue.length) {
        next.set(conversationId, nextQueue);
      } else {
        next.delete(conversationId);
      }
      commitQueuedMessages(next);
    },
    [commitQueuedMessages],
  );

  const enqueueComposerMessage = useCallback(
    (conversationId: string, item: QueuedComposerMessage) => {
      const queue = queuedMessagesRef.current.get(conversationId) ?? [];
      if (queue.length >= QUEUED_SEND_LIMIT) {
        message.warning(`发送队列已满，最多 ${QUEUED_SEND_LIMIT} 条`);
        return false;
      }
      const next = new Map(queuedMessagesRef.current);
      next.set(conversationId, [...queue, item]);
      commitQueuedMessages(next);
      message.info("已加入发送队列");
      return true;
    },
    [commitQueuedMessages, message],
  );

  const sendComposerMessage = useCallback(
    async (item: QueuedComposerMessage, restoreOnError: boolean) => {
      setAwaitingResponse(true);
      try {
        await send(
          item.content,
          item.quoted,
          item.attachments,
          item.thinkingEnabled,
          item.modelConfigId,
          item.fileReferences,
          item.agentMentions,
        );
      } catch (error) {
        setAwaitingResponse(false);
        if (restoreOnError) restoreComposerMessage(item);
        throw error;
      }
    },
    [restoreComposerMessage, send],
  );

  const submit = async () => {
    let value =
      text.trim() || (pendingFiles.length ? "请结合上传附件继续处理。" : "");
    if (!text.trim() && pendingFiles.length) {
      value = "请结合上传附件继续处理。";
    }
    if (!value || !active) return;
    const filesToSend = pendingFiles;
    const refsToSend = contextReferences;
    const mentionsToSend = agentMentions;
    const quotedMessage = quoted;
    const item: QueuedComposerMessage = {
      id: `queued-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      content: value,
      quoted: quotedMessage,
      attachments: filesToSend,
      thinkingEnabled,
      modelConfigId: runtimeModelConfigId,
      fileReferences: refsToSend,
      agentMentions: mentionsToSend,
    };
    if (isWorking || activeQueuedMessages.length > 0 || dispatchingQueuedRef.current) {
      if (enqueueComposerMessage(active.id, item)) {
        clearComposerState();
      }
      return;
    }
    clearComposerState();
    try {
      await sendComposerMessage(item, true);
    } catch (error) {
      throw error;
    }
  };

  const hasActiveStreamingMessage = useMemo(
    () =>
      Array.from(streamingMessages.values()).some(
        (message) =>
          message.streamState === "streaming" || message.status === "streaming",
      ),
    [streamingMessages],
  );
  const isWorking = Boolean(
    active &&
      (awaitingResponse ||
        hasActiveStreamingMessage ||
        localRunningConversationIds.has(active.id) ||
        active.generation_status === "running" ||
        active.generation_status === "executing"),
  );
  useEffect(() => {
    if (
      !activeConversationId ||
      loading ||
      isWorking ||
      streamingMessages.size > 0 ||
      displayOrder.length > 0 ||
      activeQueuedMessages.length === 0 ||
      dispatchingQueuedRef.current
    ) {
      return;
    }

    const timer = window.setTimeout(() => {
      if (dispatchingQueuedRef.current) return;
      if (useConversationStore.getState().activeId !== activeConversationId) return;
      const queue = queuedMessagesRef.current.get(activeConversationId) ?? [];
      const nextItem = queue[0];
      if (!nextItem) return;

      dispatchingQueuedRef.current = true;
      const nextQueue = queue.slice(1);
      const next = new Map(queuedMessagesRef.current);
      if (nextQueue.length) {
        next.set(activeConversationId, nextQueue);
      } else {
        next.delete(activeConversationId);
      }
      commitQueuedMessages(next);

      sendComposerMessage(nextItem, false)
        .catch((error) => {
          message.error(error instanceof Error ? error.message : "队列消息发送失败");
          if (useConversationStore.getState().activeId === activeConversationId) {
            restoreComposerMessage(nextItem);
          }
        })
        .finally(() => {
          dispatchingQueuedRef.current = false;
        });
    }, 180);

    return () => window.clearTimeout(timer);
  }, [
    activeConversationId,
    activeQueuedMessages.length,
    commitQueuedMessages,
    displayOrder.length,
    isWorking,
    loading,
    message,
    restoreComposerMessage,
    sendComposerMessage,
    streamingMessages.size,
  ]);

  const stopResponse = async () => {
    if (!active || stopping) return;
    setStopping(true);
    try {
      await cancel(active.id);
      setAwaitingResponse(false);
    } finally {
      setStopping(false);
    }
  };

  const copy = useCallback(
    async (value: string) => {
      await navigator.clipboard.writeText(value);
      message.success("已复制");
    },
    [message],
  );

  // 用 Map 缓存 quoted 查找，避免每条消息渲染时都做 O(n) 遍历
  const messageById = useMemo(() => {
    const map = new Map<string, ChatMessage>();
    for (const item of visibleMessages) {
      map.set(item.id, item);
    }
    return map;
  }, [visibleMessages]);

  const handleQuote = useCallback((msg: ChatMessage) => setQuoted(msg), []);
  const handleCopy = useCallback((value: string) => copy(value), [copy]);
  const messageListRef = useRef<HTMLDivElement>(null);
  const isAtBottom = useRef(true);

  const handleComposerTextChange = useCallback((value: string) => {
    setText(value);
    setAgentMentions((current) =>
      current.filter((mention) =>
        mention.agent_name ? mentionTokenPresent(value, mention.agent_name) : true,
      ),
    );
  }, []);

  const addAgentMention = useCallback(
    (mention: MessageAgentMention) => {
      setAgentMentions((current) => {
        if (current.some((item) => item.agent_id === mention.agent_id)) {
          return current;
        }
        return [...current, mention];
      });
    },
    [],
  );

  const removeAgentMention = useCallback(
    (agentId: string) => {
      const mention = agentMentions.find((item) => item.agent_id === agentId);
      setAgentMentions((current) =>
        current.filter((item) => item.agent_id !== agentId),
      );
      if (mention?.agent_name) {
        setText((current) => removeMentionToken(current, mention.agent_name || ""));
      }
    },
    [agentMentions],
  );

  const addContextReference = useCallback(
    (value: string) => {
      const option = workspaceFileOptions.find((item) => item.value === value);
      if (!option) return;
      const reference = fileReferenceFromNode(option.node);
      setContextSelectOpen(false);
      setContextSearchValue("");
      setContextReferences((current) => {
        if (current.some((item) => fileReferenceKey(item) === fileReferenceKey(reference))) {
          message.info("该文件已在上下文引用中");
          return current;
        }
        return [...current, reference];
      });
    },
    [message, workspaceFileOptions],
  );

  const removeContextReference = useCallback((key: string) => {
    setContextReferences((current) =>
      current.filter((item) => fileReferenceKey(item) !== key),
    );
  }, []);

  const uploadProps: UploadProps = {
    multiple: true,
    accept: UPLOAD_ACCEPT,
    showUploadList: false,
    beforeUpload: async (file) => {
      if (!active?.id) {
        message.warning("请先选择一个会话");
        return Upload.LIST_IGNORE;
      }
      const suffix = file.name.includes(".")
        ? `.${file.name.split(".").pop()!.toLowerCase()}`
        : "";
      if (
        BLOCKED_UPLOAD_EXTENSIONS.has(suffix) ||
        (suffix && !ALLOWED_UPLOAD_EXTENSIONS.has(suffix))
      ) {
        message.warning("暂不支持上传该文件类型");
        return Upload.LIST_IGNORE;
      }
      try {
        const uploaded = await api.uploadFile(
          file,
          active.id,
          "attachment",
          active.workspace_id,
        );
        setPendingFiles((current) => {
          if (current.some((item) => item.id === uploaded.id)) return current;
          return [...current, uploaded];
        });
        message.success(`${uploaded.original_filename || file.name} 已加入输入框`);
      } catch (error) {
        message.error(error instanceof Error ? error.message : "文件上传失败");
      }
      return Upload.LIST_IGNORE;
    },
  };

  useEffect(() => {
    const unsubscribe = window.agentHubDesktop?.onScreenshotCaptured?.(
      async (payload) => {
        if (!active?.id) {
          message.warning("请先选择一个会话");
          return;
        }
        if (!payload?.dataUrl) {
          message.warning("没有收到截图内容");
          return;
        }
        try {
          const file = dataUrlToFile(
            payload.dataUrl,
            payload.filename || `agenthub-screenshot-${Date.now()}.png`,
            payload.contentType || "image/png",
          );
          const uploaded = await api.uploadFile(
            file,
            active.id,
            "attachment",
            active.workspace_id,
          );
          setPendingFiles((current) => {
            if (current.some((item) => item.id === uploaded.id)) return current;
            return [...current, uploaded];
          });
          if (payload.prompt) {
            setText((current) =>
              current
                ? `${current}${current.endsWith("\n") ? "" : "\n"}${payload.prompt}`
                : payload.prompt || "",
            );
          }
          message.success("截图已加入输入框");
        } catch (error) {
          message.error(error instanceof Error ? error.message : "截图上传失败");
        }
      },
    );
    return () => unsubscribe?.();
  }, [active?.id, active?.workspace_id, message]);

  useEffect(() => {
    setPendingFiles([]);
  }, [activeConversationId]);

  useEffect(() => {
    const el = messageListRef.current;
    if (!el) return;

    const handleScroll = () => {
      const threshold = 50;
      isAtBottom.current =
        el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
    };

    el.addEventListener("scroll", handleScroll, { passive: true });
    return () => el.removeEventListener("scroll", handleScroll);
  }, []);

  useEffect(() => {
    if (!isAtBottom.current) return;
    const el = messageListRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [visibleMessages]);

  // 进入新对话且消息加载完成后，滚动到底部
  useEffect(() => {
    if (!active?.id || loading) return;
    isAtBottom.current = true;
    const el = messageListRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [active?.id, loading]);

  useEffect(() => {
    let alive = true;
    api.modelConfigs()
      .then((items) => {
        if (alive) {
          setModelConfigs(items);
        }
      })
      .catch(() => {
        if (alive) {
          setModelConfigs([]);
        }
      });
    return () => {
      alive = false;
    };
  }, []);

  // 智能体开始回复后，结束等待首个流式事件的状态
  useEffect(() => {
    if (awaitingResponse && (streamingMessages.size > 0 || displayOrder.length > 0)) {
      setAwaitingResponse(false);
    }
  }, [streamingMessages.size, displayOrder.length, awaitingResponse]);

  useEffect(() => {
    if (!activeConversationId || activeGenerationStatus === "idle") {
      setAwaitingResponse(false);
    }
  }, [activeConversationId, activeGenerationStatus]);

  const renderMessageBubble = (item: ChatMessage) => {
    return (
      <MessageBubble
        key={item.id}
        message={item}
        workspaceId={active?.workspace_id}
        currentUserAvatarUrl={userAvatarUrl}
        version={messageVersions.get(item.id) ?? 0}
        quoted={
          item.quotedMessageId
            ? messageById.get(item.quotedMessageId)
            : undefined
        }
        onQuote={handleQuote}
        onCopy={handleCopy}
        onPreview={(message) => onPreviewArtifact?.(message)}
      />
    );
  };

  return (
    <Content className="chat-panel">
      <RuntimeDecisionStrip conversation={active} />
      <TeamActivity conversation={active} />
      <div ref={messageListRef} className="message-list">
        {loading ? (
          <Spin />
        ) : visibleMessages.length ? (
          <>
            {visibleMessages.map(renderMessageBubble)}
          </>
        ) : (
          <Empty description="暂无消息" />
        )}
      </div>
      <div ref={composerRef} className="composer">
        {quoted && (
          <div className="composer-quote">
            <div className="composer-quote-text">
              <Text type="secondary">引用：{quoted.content}</Text>
            </div>
            <Button
              type="text"
              size="small"
              onClick={() => setQuoted(undefined)}
            >
              取消
            </Button>
          </div>
        )}
        {pendingFiles.length > 0 && (
          <div className="composer-files" data-testid="composer-attachments">
            {pendingFiles.map((file) => (
              <Tag
                key={file.id}
                closable
                icon={<CloudUploadOutlined />}
                onClose={() =>
                  setPendingFiles((current) =>
                    current.filter((item) => item.id !== file.id),
                  )
                }
              >
                {file.original_filename} · {Math.ceil(file.size / 1024)}KB ·{" "}
                {file.parse_status}
              </Tag>
            ))}
          </div>
        )}
        {activeQueuedMessages.length > 0 && activeConversationId && (
          <div className="composer-send-queue" data-testid="composer-send-queue">
            <Text type="secondary" className="composer-send-queue-label">
              排队 {activeQueuedMessages.length}/{QUEUED_SEND_LIMIT}
            </Text>
            {activeQueuedMessages.map((item, index) => (
              <Tag
                key={item.id}
                closable
                onClose={() => removeQueuedMessage(activeConversationId, item.id)}
              >
                {index + 1}. {queuePreviewText(item.content)}
              </Tag>
            ))}
          </div>
        )}
        <Mentions
          data-testid="message-input"
          aria-label="message-input"
          className="composer-mentions"
          autoSize={{ minRows: 2, maxRows: 6 }}
          value={text}
          prefix="@"
          options={agentMentionOptions}
          placeholder="输入消息，键入 @ 可选择群聊 Agent；Enter 发送，Shift+Enter 换行"
          onChange={handleComposerTextChange}
          onSelect={(option) => {
            const selected = agentMentionOptions.find(
              (item) => item.value === String(option.value || ""),
            );
            if (selected) addAgentMention(selected.agent);
          }}
          filterOption={(input, option) => {
            const searchText = String(
              (option as AgentMentionOption | undefined)?.searchText ||
                option?.value ||
                "",
            ).toLowerCase();
            return searchText.includes(input.toLowerCase());
          }}
          notFoundContent="暂无可 @ 的 Agent"
          onPressEnter={(event) => {
            if (!event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
        />
        {(contextReferences.length > 0 || agentMentions.length > 0) && (
          <div className="composer-contexts" data-testid="composer-contexts">
            <Text type="secondary" className="composer-contexts-label">
              引用/指定
            </Text>
            {agentMentions.map((mention) => (
              <Tag
                key={mention.agent_id}
                closable
                icon={<RobotOutlined />}
                onClose={() => removeAgentMention(mention.agent_id)}
              >
                {mention.agent_name ?? mention.agent_id}
              </Tag>
            ))}
            {contextReferences.map((reference) => (
              <Tag
                key={fileReferenceKey(reference)}
                closable
                icon={<FileSearchOutlined />}
                onClose={() => removeContextReference(fileReferenceKey(reference))}
              >
                {reference.filename ?? reference.path}
              </Tag>
            ))}
          </div>
        )}
        <Flex justify="space-between" align="center">
          <Space>
            <Upload {...uploadProps}>
              <Button icon={<CloudUploadOutlined />} data-testid="file-upload">
                上传文件
              </Button>
            </Upload>
            <Select
              key={`context-reference-${active?.id ?? "none"}`}
              className="composer-context-select"
              showSearch
              allowClear
              value={undefined}
              open={contextSelectOpen && Boolean(active?.workspace_id)}
              searchValue={contextSelectOpen ? contextSearchValue : ""}
              placeholder="引用文档"
              loading={workspaceFilesLoading}
              disabled={!active?.workspace_id}
              options={workspaceFileOptions}
              optionFilterProp="searchText"
              onOpenChange={(open) => {
                const nextOpen = open && Boolean(active?.workspace_id);
                setContextSelectOpen(nextOpen);
                if (!nextOpen) setContextSearchValue("");
              }}
              onSearch={setContextSearchValue}
              onSelect={(value) => {
                if (typeof value === "string") addContextReference(value);
              }}
              onClear={() => {
                setContextSelectOpen(false);
                setContextSearchValue("");
              }}
              onBlur={() => {
                setContextSelectOpen(false);
                setContextSearchValue("");
              }}
              getPopupContainer={(triggerNode) =>
                triggerNode.parentElement ?? composerRef.current ?? document.body
              }
              popupMatchSelectWidth={420}
              data-testid="context-reference-select"
              suffixIcon={<FileSearchOutlined />}
              notFoundContent={
                workspaceFilesLoading ? "加载中..." : "暂无可引用文档"
              }
            />
            <Select
              className="composer-model-select"
              size="middle"
              value={chatModelSelectValue}
              options={modelOptions}
              onChange={setSelectedModelConfigId}
              disabled={!active || modelOptions.length === 0}
              popupMatchSelectWidth={280}
              data-testid="composer-model-select"
            />
          </Space>
          <Space>
            <Tooltip title={thinkingEnabled ? "已开启思考模式" : "思考模式"}>
              <Button
                type="default"
                className={thinkingEnabled ? "thinking-toggle-button active" : "thinking-toggle-button"}
                icon={<BulbOutlined />}
                onClick={() => setThinkingEnabled(!thinkingEnabled)}
                disabled={!active}
                data-testid="thinking-toggle"
              >
                思考
              </Button>
            </Tooltip>
            {isWorking && (
              <Button
                type="default"
                danger
                icon={<StopOutlined />}
                loading={stopping}
                onClick={stopResponse}
                disabled={!active}
                data-testid="stop-response"
              >
                停止
              </Button>
            )}
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={submit}
              disabled={!active}
              data-testid="send-message"
            >
              发送
            </Button>
          </Space>
        </Flex>
      </div>
    </Content>
  );
}

function workspaceFileOptionsFromTree(root: WorkspaceFileNode): WorkspaceFileOption[] {
  const nodes: WorkspaceFileNode[] = [];
  walk([root], (node) => {
    if (node.type === "file") nodes.push(node);
  });
  return nodes.map((node) => {
    const name = displayNodeName(node);
    return {
      value: node.id || node.path,
      searchText: `${name} ${node.path} ${node.display_path ?? ""}`,
      node,
      label: (
        <div className="composer-context-option">
          <span className="composer-context-option-name">{name}</span>
        </div>
      ),
    };
  });
}

function fileReferenceFromNode(node: WorkspaceFileNode): MessageFileReference {
  const fileId = node.id.startsWith("file:") ? node.id.slice(5) : undefined;
  return {
    path: node.path,
    file_id: fileId,
    node_id: node.id,
    filename: displayNodeName(node),
    content_type: node.mime_type,
    size: node.size,
    source: node.source,
    display_path: node.display_path ?? displayNodePath(node),
  };
}

function fileReferenceKey(reference: MessageFileReference) {
  return reference.file_id
    ? `file:${reference.file_id}`
    : `path:${reference.path}`;
}

function queuePreviewText(value: string) {
  const text = value.replace(/\s+/g, " ").trim();
  return text.length > 28 ? `${text.slice(0, 28)}...` : text;
}

function agentMentionOptionsFromParticipants(
  participants: Conversation["participants"],
): AgentMentionOption[] {
  return participants
    .filter((participant) => (
      participant.participant_type === "agent" &&
      !participant.left_at &&
      Boolean(participant.agent_id)
    ))
    .map((participant) => {
      const agentId = String(participant.agent_id);
      const name = participant.agent_name || participant.nickname || agentId;
      const mention: MessageAgentMention = {
        agent_id: agentId,
        agent_name: name,
        agent_avatar_url: participant.agent_avatar_url,
      };
      return {
        value: name,
        searchText: `${name} ${agentId} ${participant.agent_type ?? ""}`,
        agent: mention,
        label: (
          <div className="composer-agent-mention-option">
            <Avatar
              size={28}
              src={participant.agent_avatar_url}
              className="composer-agent-mention-avatar"
            >
              {name.slice(0, 1).toUpperCase()}
            </Avatar>
            <div className="composer-agent-mention-text">
              <span className="composer-agent-mention-name">{name}</span>
              <span className="composer-agent-mention-meta">
                {participant.agent_type || "Agent"}
              </span>
            </div>
          </div>
        ),
      };
    });
}

function mentionTokenPresent(text: string, name: string) {
  return text.toLowerCase().includes(`@${name}`.toLowerCase());
}

function removeMentionToken(text: string, name: string) {
  const escaped = escapeRegExp(name);
  return text
    .replace(new RegExp(`@${escaped}\\s*`, "gi"), "")
    .replace(/[ \t]{2,}/g, " ")
    .trimStart();
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function dataUrlToFile(dataUrl: string, filename: string, contentType: string) {
  const [meta, payload] = dataUrl.split(",");
  if (!payload) throw new Error("截图数据格式无效");
  const mime = /data:(.*?);base64/.exec(meta)?.[1] || contentType;
  const binary = atob(payload);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return new File([bytes], filename, { type: mime });
}

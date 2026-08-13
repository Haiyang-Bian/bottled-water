import { App as AntApp } from "antd";
import { api } from "@/api";
import {
  useArtifactStore,
  useConversationStore,
  useMessageStore,
  useUIStore,
} from "@/store";
import type { ChatMessage, Conversation, WorkspaceArtifact } from "@/types";

export function useWorkbenchActions(
  activeWorkspaceId: string | undefined,
  conversationCategories: string[],
  saveCategories: (categories: string[]) => void,
  navigateToConversation: (
    workspaceId?: string,
    conversationId?: string,
    replace?: boolean,
  ) => void,
) {
  const { message } = AntApp.useApp();
  const { activeId, setActiveId, updateConversations } = useConversationStore();
  const { setMessagesForConversation } = useMessageStore();
  const {
    artifact,
    files,
    setArtifact,
    setArtifactPanelOpen,
    setFiles,
    setDeployment,
  } = useArtifactStore();
  const { setCreateOpen } = useUIStore();

  const patchConversation = async (
    item: Conversation,
    patch: Partial<Conversation>,
  ) => {
    const updated = await api.updateConversation(item.id, patch);
    const nextCategory =
      patch.folder || patch.category || updated.folder || updated.category;
    if (nextCategory) saveCategories([...conversationCategories, nextCategory]);
    updateConversations((current) =>
      current.map((conversation) =>
        conversation.id === item.id ? { ...conversation, ...updated } : conversation,
      ),
    );
  };

  const createConversation = async (payload: {
    title?: string;
    agentIds: string[];
    group?: boolean;
    masterEnabled: boolean;
    folder: string;
  }) => {
    const created = await api.createConversationWithAgents({
      chat_type: payload.group ? "group" : "single",
      title: payload.title,
      participant_agent_ids: payload.agentIds,
      master_enabled: payload.masterEnabled,
      scheduling_strategy: payload.group ? "tech_lead" : "single_agent",
      workflow_enabled: false,
      folder: payload.folder,
      category: payload.folder,
      workspace_id: activeWorkspaceId,
    });
    saveCategories([...conversationCategories, payload.folder]);
    updateConversations((current) => [created, ...current]);
    setMessagesForConversation(created.id, []);
    setActiveId(created.id);
    navigateToConversation(created.workspace_id || activeWorkspaceId, created.id);
    setCreateOpen({ open: false });
    message.success("会话已创建");
  };

  const saveArtifact = async (next: WorkspaceArtifact) => {
    const saved = await api.saveArtifact(next);
    setArtifact(saved);
    message.success("产物已保存");
  };

  const deploy = async () => {
    if (!activeId) return undefined;
    setDeployment({
      id: "pending",
      status: "building",
      commit: "pending",
      updatedAt: new Date().toISOString(),
    });
    const result = await api.deploy(activeId, artifact?.id);
    setDeployment(result);
    message.success("部署任务已提交");
    return result;
  };

  const uploadFile = async (file: File) => {
    const uploaded = await api.uploadFile(
      file,
      activeId,
      "attachment",
      activeWorkspaceId,
    );
    setFiles([uploaded, ...files]);
    message.success("文件已加入输入框，发送后会进入模型上下文");
    return uploaded;
  };

  const openArtifactPreview = async (source?: ChatMessage) => {
    if (!activeId) return;

    const raw = asRecord(source?.rawContent);
    const nestedArtifact = asRecord(raw.artifact);
    const artifactId =
      stringValue(raw.artifact_id) ||
      stringValue(raw.artifactId) ||
      stringValue(nestedArtifact.artifact_id) ||
      stringValue(nestedArtifact.artifactId) ||
      stringValue(nestedArtifact.id) ||
      (source?.kind === "preview_card" ? stringValue(raw.id) : "");

    if (source?.kind === "preview_card" && !artifactId) {
      message.error("产物卡片缺少 artifact_id，无法打开预览。");
      return;
    }

    const title =
      stringValue(raw.title) ||
      stringValue(nestedArtifact.title) ||
      stringValue(nestedArtifact.name) ||
      source?.content.replace(/^预览产物[:：]\s*/, "") ||
      "产物预览";
    const format =
      stringValue(raw.format) || stringValue(nestedArtifact.format) || undefined;
    const mediaType =
      stringValue(raw.media_type) ||
      stringValue(nestedArtifact.media_type) ||
      undefined;
    const filename =
      stringValue(raw.filename) ||
      stringValue(nestedArtifact.filename) ||
      undefined;

    if (artifactId) {
      setArtifact({
        id: artifactId,
        conversationId: activeId,
        title,
        language: "html",
        code: "<main><h1>正在加载真实产物...</h1><p>正在根据 artifact_id 拉取产物文件。</p></main>",
        previousCode: "",
        updatedAt: new Date().toISOString(),
        content: { format, media_type: mediaType, filename },
        format,
        media_type: mediaType,
        filename,
      });
      setArtifactPanelOpen(true);
    }

    try {
      const current =
        (artifactId ? await api.artifactById(artifactId) : undefined) ??
        artifact ??
        (await api.artifact(activeId));
      if (!current) {
        message.warning("当前会话还没有可预览产物。");
        return;
      }
      setArtifact(current);
      try {
        const deployments = await api.deploymentsForArtifact(current.id);
        setDeployment(deployments.items?.[0]);
      } catch {
        setDeployment(undefined);
      }
      setArtifactPanelOpen(true);
    } catch (error) {
      const reason =
        error instanceof Error ? error.message : "无法加载真实产物";
      if (artifactId) {
        setArtifact({
          id: artifactId,
          conversationId: activeId,
          title: "产物预览失败",
          language: "html",
          code: `<main><h1>产物预览失败</h1><p>${escapeHtml(reason)}</p></main>`,
          previousCode: "",
          updatedAt: new Date().toISOString(),
        });
        setArtifactPanelOpen(true);
      }
      message.error(`预览打开失败：${reason}`);
    }
  };

  return {
    patchConversation,
    createConversation,
    saveArtifact,
    deploy,
    uploadFile,
    openArtifactPreview,
  };
}

function stringValue(value: unknown): string {
  return typeof value === "string" && value ? value : "";
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : {};
}

function escapeHtml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

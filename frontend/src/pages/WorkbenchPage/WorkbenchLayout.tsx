import {
  App as AntApp,
  Button,
  Layout,
  Modal,
  Segmented,
  Select,
  Space,
} from "antd";
import {
  AppstoreOutlined,
  BranchesOutlined,
  CommentOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  RobotOutlined,
  ToolOutlined,
} from "@ant-design/icons";
import { Link } from "react-router-dom";
import { api } from "@/api";
import { ConversationSidebar } from "@/features/chat/components/ConversationSidebar";
import type { Agent, Conversation, User, Workspace } from "@/types";
import { isDesktopRuntime } from "@/config/desktopRuntime";

export interface WorkbenchLayoutProps {
  currentUser: User;
  onLogout: () => void;
  workspaces: Workspace[];
  activeWorkspace: Workspace | undefined;
  activeWorkspaceId: string | undefined;
  selectWorkspace: (workspaceId?: string, replace?: boolean) => void;
  openMainTab: (tab: "agents" | "workspace" | "settings" | "files") => void;
  conversations: Conversation[];
  activeId: string | undefined;
  conversationCategories: string[];
  selectConversation: (conversationId?: string, replace?: boolean) => void;
  setCreateOpen: (value: { open: boolean }) => void;
  addConversationCategory: (name: string) => void;
  patchConversation: (
    item: Conversation,
    patch: Partial<Conversation>,
  ) => Promise<void>;
  updateConversations: (
    updater: (current: Conversation[]) => Conversation[],
  ) => void;
  setActiveId: (id: string | undefined) => void;
  navigateToConversation: (
    workspaceId?: string,
    conversationId?: string,
    replace?: boolean,
  ) => void;
  runningConversationIds: Set<string>;
  agents: Agent[];
  routeTab: string;
  scheduleMode: "chat" | "workflow";
  onScheduleModeChange: (mode: "chat" | "workflow") => void;
  children: React.ReactNode;
}

export function WorkbenchLayout(props: WorkbenchLayoutProps) {
  const { message } = AntApp.useApp();
  const {
    currentUser,
    onLogout,
    workspaces,
    activeWorkspace,
    activeWorkspaceId,
    selectWorkspace,
    openMainTab,
    conversations,
    activeId,
    conversationCategories,
    selectConversation,
    setCreateOpen,
    addConversationCategory,
    patchConversation,
    updateConversations,
    setActiveId,
    navigateToConversation,
    runningConversationIds,
    agents,
    routeTab,
    scheduleMode,
    onScheduleModeChange,
    children,
  } = props;
  const activeConversation = conversations.find(
    (conversation) => conversation.id === activeId,
  );
  const chatModeLabel = activeConversation?.workflow_enabled
    ? "工作流聊天"
    : "自动组织";

  const deleteConversation = (item: Conversation) => {
    Modal.confirm({
      title: "删除归档会话",
      content: `确认删除「${item.title}」？删除后会从列表移除。`,
      okText: "删除",
      okButtonProps: { danger: true },
      onOk: async () => {
        await api.deleteConversation(item.id);
        updateConversations((current) =>
          current.filter((conversation) => conversation.id !== item.id),
        );
        if (activeId === item.id) {
          const nextConversation = conversations.find(
            (conversation) => conversation.id !== item.id,
          );
          setActiveId(nextConversation?.id);
          navigateToConversation(
            nextConversation?.workspace_id || activeWorkspaceId,
            nextConversation?.id,
            true,
          );
        }
        message.success("会话已删除");
      },
    });
  };

  const deleteArchivedConversations = (items: Conversation[]) => {
    const archivedItems = items.filter((item) => item.archived);
    if (!archivedItems.length) {
      return;
    }
    Modal.confirm({
      title: "一键删除归档会话",
      content: `确认删除这 ${archivedItems.length} 个归档会话吗？删除后将从列表移除。`,
      okText: "全部删除",
      okButtonProps: { danger: true },
      onOk: async () => {
        await Promise.all(archivedItems.map((item) => api.deleteConversation(item.id)));
        const deletedIds = new Set(archivedItems.map((item) => item.id));
        updateConversations((current) =>
          current.filter((conversation) => !deletedIds.has(conversation.id)),
        );
        if (activeId && deletedIds.has(activeId)) {
          const remaining = conversations.find(
            (conversation) => !deletedIds.has(conversation.id),
          );
          setActiveId(remaining?.id);
          navigateToConversation(
            remaining?.workspace_id || activeWorkspaceId,
            remaining?.id,
            true,
          );
        }
        message.success("归档会话已删除");
      },
    });
  };

  return (
    <Layout className="workbench">
      <ConversationSidebar
        currentUser={currentUser}
        conversations={conversations}
        activeId={activeId}
        runningConversationIds={runningConversationIds}
        categoryOptions={conversationCategories}
        agents={agents}
        onSelect={selectConversation}
        onCreate={() => setCreateOpen({ open: true })}
        onCreateCategory={addConversationCategory}
        onTogglePin={(item) => patchConversation(item, { pinned: !item.pinned })}
        onToggleArchive={(item) =>
          patchConversation(item, { archived: !item.archived })
        }
        onEdit={(item, patch) => patchConversation(item, patch)}
        onDelete={deleteConversation}
        onDeleteArchived={deleteArchivedConversations}
      />
      <Layout className="center-layout">
        <div className="topbar">
          <Space>
            <Select
              style={{ width: 220 }}
              value={activeWorkspace?.id}
              placeholder="选择工作区"
              onChange={(value) => selectWorkspace(value)}
              options={workspaces.map((workspace) => ({
                label: workspace.name,
                value: workspace.id,
              }))}
            />
            <Button
              icon={<AppstoreOutlined />}
              onClick={() => openMainTab("workspace")}
              data-testid="workspace-panel"
            >
              工作区
            </Button>
            <Button
              icon={<FolderOpenOutlined />}
              onClick={() => openMainTab("files")}
              data-testid="workspace-files"
            >
              工作区文件
            </Button>
          </Space>
          <Space>
            {routeTab === "chat" && (
              <Segmented
                value={scheduleMode}
                onChange={(value) =>
                  onScheduleModeChange(value as "chat" | "workflow")
                }
                options={[
                  { label: <><CommentOutlined /> {chatModeLabel}</>, value: "chat" },
                  { label: <><BranchesOutlined /> 工作流画布</>, value: "workflow" },
                ]}
              />
            )}
            <Button
              icon={<ToolOutlined />}
              onClick={() => openMainTab("settings")}
              data-testid="global-settings"
            >
              设置
            </Button>
            <Button
              icon={<RobotOutlined />}
              onClick={() => openMainTab("agents")}
              data-testid="agent-directory"
            >
              Agent 广场
            </Button>
            <Link to="/docs">
              <Button icon={<FileTextOutlined />} data-testid="docs-link">
                文档
              </Button>
            </Link>
            {!isDesktopRuntime() && <Button onClick={onLogout}>退出</Button>}
          </Space>
        </div>
        <div className="main-content">{children}</div>
      </Layout>
    </Layout>
  );
}

import { useState } from "react";
import {
  App as AntApp,
  Button,
  Drawer,
  Select,
  Space,
  Tabs,
  Tag,
  Typography,
} from "antd";
import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import type { Conversation, Project, Workspace } from "@/types";
import { AssetsPanel } from "../../tabs/AssetsPanel";
import { WorkflowBoardPanel } from "../../tabs/WorkflowBoardPanel";
import { ModelsPanel } from "../../tabs/ModelsPanel";
import { McpPanel } from "../../tabs/McpPanel";
import { ToolsPanel } from "../../tabs/ToolsPanel";
import { SkillsPanel } from "../../tabs/SkillsPanel";
import { SecurityPanel } from "../../tabs/SecurityPanel";
import { SandboxPanel } from "../../tabs/SandboxPanel";
import { isDesktopRuntime } from "@/config/desktopRuntime";

const { Text } = Typography;

interface PlatformControlDrawerProps {
  open?: boolean;
  asPage?: boolean;
  workspaces: Workspace[];
  activeConversation?: Conversation;
  onClose: () => void;
  onCreateWorkspace: (payload: {
    name: string;
    description: string;
    type: string;
    tags: string[];
    config?: Record<string, unknown>;
  }) => Promise<void>;
  onCreateProject: (
    workspaceId: string,
    payload: { name: string; description: string; type: string },
  ) => Promise<Project>;
  onLoadProjects: (workspaceId: string) => Promise<Project[]>;
  onSaveProjectFile: (
    projectId: string,
    payload: { path: string; language: string; content: string },
  ) => Promise<void>;
}

export function PlatformControlDrawer({
  open,
  asPage,
  workspaces,
  activeConversation,
  onClose,
  onCreateWorkspace,
  onCreateProject,
  onLoadProjects,
  onSaveProjectFile,
}: PlatformControlDrawerProps) {
  const { message } = AntApp.useApp();
  const [selectedWorkspace, setSelectedWorkspace] = useState<string>();

  const activeWorkspace =
    workspaces.find((item) => item.id === selectedWorkspace) ?? workspaces[0];

  const handleRefresh = () => {
    message.success("已刷新");
  };

  const tabItems = [
    {
      key: "assets",
      label: "资产",
      children: (
        <AssetsPanel
          workspaces={workspaces}
          onCreateWorkspace={onCreateWorkspace}
          onCreateProject={onCreateProject}
          onLoadProjects={onLoadProjects}
          onSaveProjectFile={onSaveProjectFile}
        />
      ),
    },
    {
      key: "workflow",
      label: "工作流画布",
      children: <WorkflowBoardPanel activeConversation={activeConversation} />,
    },
    {
      key: "models",
      label: "模型",
      children: <ModelsPanel />,
    },
    {
      key: "mcp",
      label: "MCP",
      children: (
        <McpPanel
          activeWorkspace={activeWorkspace}
          activeConversation={activeConversation}
        />
      ),
    },
    {
      key: "tools",
      label: "工具",
      children: (
        <ToolsPanel
          activeWorkspace={activeWorkspace}
          activeConversation={activeConversation}
        />
      ),
    },
    {
      key: "skills",
      label: "Skills",
      children: <SkillsPanel activeWorkspace={activeWorkspace} />,
    },
    {
      key: "security",
      label: "Security",
      children: <SecurityPanel />,
    },
    {
      key: "sandbox",
      label: "沙箱/远程",
      children: <SandboxPanel activeWorkspace={activeWorkspace} />,
    },
  ].filter(
    (item) =>
      !["workflow", "models"].includes(String(item.key)) &&
      !(isDesktopRuntime() && item.key === "security"),
  );

  const content = (
    <>
      <Space wrap className="drawer-toolbar">
        <Select
          style={{ width: 260 }}
          placeholder="选择工作区"
          value={activeWorkspace?.id}
          onChange={setSelectedWorkspace}
          options={workspaces.map((workspace) => ({
            label: workspace.name,
            value: workspace.id,
          }))}
        />
        {activeWorkspace && (
          <>
            <Tag color="blue">{activeWorkspace.type}</Tag>
            <Tag>{activeWorkspace.status}</Tag>
            <Tag>{activeWorkspace.member_count} 成员</Tag>
            <Tag>{activeWorkspace.project_count} 项目</Tag>
          </>
        )}
        <Button icon={<ReloadOutlined />} onClick={handleRefresh}>
          刷新
        </Button>
      </Space>
      <Tabs items={tabItems} />
    </>
  );

  return (
    <>
      {asPage ? (
        <div className="page-view">
          <div className="page-header">
            <Button icon={<ArrowLeftOutlined />} onClick={onClose}>
              返回
            </Button>
            <Text strong>工作区与平台控制面</Text>
          </div>
          <div className="page-content">{content}</div>
        </div>
      ) : (
        <Drawer
          title="工作区与平台控制面"
          width={1040}
          open={open}
          onClose={onClose}
        >
          {content}
        </Drawer>
      )}
    </>
  );
}

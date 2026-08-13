import type { ChangeEvent } from "react";
import { useState } from "react";
import {
  App as AntApp,
  Button,
  Card,
  Empty,
  Input,
  List,
  Segmented,
  Select,
  Space,
  Tag,
} from "antd";
import {
  BranchesOutlined,
  CheckCircleOutlined,
  MessageOutlined,
  PlusOutlined,
  RobotOutlined,
} from "@ant-design/icons";
import { api } from "@/api";
import { useConversationStore } from "@/store";
import type { Conversation, ConversationWorkflow } from "@/types";

interface WorkflowBoardPanelProps {
  activeConversation?: Conversation;
}

export function WorkflowBoardPanel({ activeConversation }: WorkflowBoardPanelProps) {
  const { message } = AntApp.useApp();
  const updateConversation = useConversationStore((state) => state.updateConversation);

  const [conversationWorkflow, setConversationWorkflow] =
    useState<ConversationWorkflow>();
  const [workflowJson, setWorkflowJson] = useState("");
  const [draggingNodeId, setDraggingNodeId] = useState<string>();
  const [routingMode, setRoutingMode] = useState("auto");

  const workflowNodes = conversationWorkflow?.nodes ?? [];
  const workflowEdges = conversationWorkflow?.edges ?? [];

  const setWorkflowDraft = (next: ConversationWorkflow) => {
    setConversationWorkflow(next);
    setWorkflowJson(JSON.stringify(next, null, 2));
  };

  const reorderWorkflowNode = (sourceId: string, targetId: string) => {
    if (!conversationWorkflow || sourceId === targetId) return;
    const nodes = [...conversationWorkflow.nodes];
    const from = nodes.findIndex((node) => node.id === sourceId);
    const to = nodes.findIndex((node) => node.id === targetId);
    if (from < 0 || to < 0) return;
    const [moved] = nodes.splice(from, 1);
    nodes.splice(to, 0, moved);
    const edges = nodes
      .slice(1)
      .map((node, index) => [nodes[index].id, node.id]);
    setWorkflowDraft({ ...conversationWorkflow, nodes, edges });
  };

  const workflowIcon = (role?: string) => {
    if (role === "input") return <MessageOutlined />;
    if (role === "master") return <BranchesOutlined />;
    if (role === "reviewer") return <CheckCircleOutlined />;
    if (role === "artifact") return <RobotOutlined />;
    return <RobotOutlined />;
  };

  return (
    <div className="workflow-board">
      <div className="workflow-canvas">
        {workflowNodes.length ? (
          workflowNodes.map((node, index) => (
            <div
              key={node.id}
              className={`workflow-node workflow-node-${node.status}`}
              draggable
              onDragStart={() => setDraggingNodeId(node.id)}
              onDragOver={(event) => event.preventDefault()}
              onDrop={() => {
                if (draggingNodeId)
                  reorderWorkflowNode(draggingNodeId, node.id);
                setDraggingNodeId(undefined);
              }}
            >
              <div className="workflow-node-icon">
                {workflowIcon(node.role)}
              </div>
              <div className="workflow-node-body">
                <strong>{node.title}</strong>
                <span className="ant-typography ant-typography-secondary">{node.meta}</span>
              </div>
              {index < workflowNodes.length - 1 && (
                <div className="workflow-arrow">→</div>
              )}
            </div>
          ))
        ) : (
          <Empty description="选择一个会话后，可按群聊 Agent 自动生成工作流" />
        )}
      </div>
      <div className="workflow-side">
        <Card title="会话工作流">
          <Space direction="vertical" className="full-width">
            <span className="ant-typography ant-typography-secondary">
              {activeConversation
                ? activeConversation.title
                : "暂无选中会话"}
            </span>
            <Space wrap>
              <Button
                icon={<RobotOutlined />}
                disabled={!activeConversation}
                onClick={async () => {
                  if (!activeConversation) return;
                  const workflow =
                    await api.generateConversationWorkflow(
                      activeConversation.id,
                    );
                  setConversationWorkflow(workflow);
                  setWorkflowJson(JSON.stringify(workflow, null, 2));
                  message.success("AI 已按当前群聊 Agent 生成工作流");
                }}
              >
                AI 生成
              </Button>
              <Button
                icon={<CheckCircleOutlined />}
                disabled={!activeConversation || !workflowJson.trim()}
                onClick={async () => {
                  if (!activeConversation) return;
                  let workflow: ConversationWorkflow;
                  try {
                    workflow = JSON.parse(
                      workflowJson,
                    ) as ConversationWorkflow;
                  } catch {
                    message.error("工作流 JSON 格式不正确");
                    return;
                  }
                  const saved = await api.saveConversationWorkflow(
                    activeConversation.id,
                    workflow,
                  );
                  const enabled = Boolean(saved.settings?.enabled);
                  const updated = await api.updateConversation(activeConversation.id, {
                    scheduling_strategy: enabled ? "workflow" : "tech_lead",
                    workflow_enabled: enabled,
                  });
                  updateConversation(activeConversation.id, updated);
                  setConversationWorkflow(saved);
                  setWorkflowJson(JSON.stringify(saved, null, 2));
                  message.success("工作流已保存");
                }}
              >
                保存
              </Button>
            </Space>
            <Button
              icon={<PlusOutlined />}
              disabled={!conversationWorkflow}
              onClick={() => {
                if (!conversationWorkflow) return;
                const id = `node-${Date.now().toString(36)}`;
                const nodes = [
                  ...conversationWorkflow.nodes,
                  {
                    id,
                    title: "New node",
                    role: "worker",
                    status: "ready",
                    meta: "Manual step",
                  },
                ];
                const edges = nodes
                  .slice(1)
                  .map((node, index) => [nodes[index].id, node.id]);
                setWorkflowDraft({
                  ...conversationWorkflow,
                  nodes,
                  edges,
                });
              }}
            >
              Add node
            </Button>
            <Button
              icon={<BranchesOutlined />}
              disabled={!activeConversation || !conversationWorkflow}
              onClick={async () => {
                if (!activeConversation || !conversationWorkflow)
                  return;
                await api.startWorkflowRun(
                  activeConversation.id,
                  conversationWorkflow,
                );
                message.success("Workflow run started");
              }}
            >
              Run
            </Button>
            <Input.TextArea
              rows={10}
              value={workflowJson}
              onChange={(event: ChangeEvent<HTMLTextAreaElement>) =>
                setWorkflowJson(event.target.value)
              }
              placeholder="工作流 JSON：nodes / edges / settings"
            />
          </Space>
        </Card>
        <Card title="调度参数" className="mt-8">
          <Space direction="vertical" className="full-width">
            <Segmented
              block
              value={routingMode}
              onChange={(value) => setRoutingMode(String(value))}
              options={[
                { label: "自动", value: "auto" },
                { label: "人工确认", value: "human" },
                { label: "成本优先", value: "cost" },
              ]}
            />
            <Select
              defaultValue="review-required"
              options={[
                {
                  label: "Reviewer 必须通过",
                  value: "review-required",
                },
                { label: "低风险跳过", value: "risk-based" },
                { label: "双 Reviewer", value: "dual-review" },
              ]}
            />
            <Select
              defaultValue="summary-window"
              options={[
                { label: "摘要滚动窗口", value: "summary-window" },
                { label: "全量上下文", value: "full-context" },
                { label: "知识库优先", value: "kb-first" },
              ]}
            />
            <Button
              type="primary"
              icon={<BranchesOutlined />}
              onClick={() => {
                setConversationWorkflow((current) =>
                  current
                    ? {
                        ...current,
                        nodes: current.nodes.map((node) =>
                          node.role === "master"
                            ? { ...node, status: "running" }
                            : node,
                        ),
                      }
                    : current,
                );
                window.setTimeout(() => {
                  setConversationWorkflow((current) =>
                    current
                      ? {
                          ...current,
                          nodes: current.nodes.map((node) =>
                            node.role === "master"
                              ? { ...node, status: "ready" }
                              : node,
                          ),
                        }
                      : current,
                  );
                }, 1200);
                message.success(`调度演练已触发：${routingMode}`);
              }}
            >
              调度演练
            </Button>
          </Space>
        </Card>
        <Card title="依赖连线" className="mt-8">
          <List
            size="small"
            dataSource={workflowEdges.filter((edge): edge is string[] => Array.isArray(edge))}
            renderItem={([from, to]) => (
              <List.Item>
                <Tag>{from}</Tag>
                <span>→</span>
                <Tag color="blue">{to}</Tag>
              </List.Item>
            )}
          />
        </Card>
      </div>
    </div>
  );
}

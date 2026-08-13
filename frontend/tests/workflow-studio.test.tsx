import React, { useState } from "react";
import { App as AntApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import { WorkflowStudioContent } from "../src/features/workflow/WorkflowStudioContent";
import type {
  Agent,
  Conversation,
  ConversationWorkflow,
  WorkflowRun,
} from "../src/types";

vi.mock("../src/api", () => ({
  api: {
    agents: vi.fn(),
    conversations: vi.fn(),
    conversationWorkflow: vi.fn(),
    workflowRuns: vi.fn(),
    tools: vi.fn(),
    skills: vi.fn(),
    mcpServers: vi.fn(),
    saveConversationWorkflow: vi.fn(),
    updateConversation: vi.fn(),
    generateConversationWorkflow: vi.fn(),
    startWorkflowRun: vi.fn(),
  },
}));

vi.mock("../src/features/chat/components/drawers/WorkflowCanvas", () => ({
  WorkflowCanvas: ({
    workflow,
    onNodeClick,
  }: {
    workflow: ConversationWorkflow;
    onNodeClick: (node: ConversationWorkflow["nodes"][number]) => void;
  }) => (
    <div data-testid="workflow-canvas">
      {workflow.nodes.map((node) => (
        <button key={node.id} type="button" onClick={() => onNodeClick(node)}>
          {node.title}
        </button>
      ))}
    </div>
  ),
}));

const conversation: Conversation = {
  id: "c1",
  workspace_id: "w1",
  chat_type: "group",
  title: "Demo 群聊",
  participants: [
    { id: "p1", participant_type: "agent", agent_id: "a1", agent_name: "前端" },
  ],
  updatedAt: "2026-05-26T00:00:00Z",
  pinned: false,
  archived: false,
  unread: 0,
  tags: [],
  category: "Default",
};

const agent: Agent = {
  id: "a1",
  name: "Frontend Worker",
  type: "frontend",
  version: "1.0.0",
  capabilities: [],
  description: "前端开发",
  status: "online",
  provider: "official",
  is_official: true,
  response_latency_ms: 0,
  config: {},
};

const workflow: ConversationWorkflow = {
  conversation_id: "c1",
  mode: "all_agents_independent",
  output_mode: "independent_messages",
  settings: { enabled: true, published: false },
  nodes: [
    { id: "start", title: "Start", type: "start", status: "queued" },
    {
      id: "agent_1",
      title: "Frontend Worker",
      type: "agent",
      agent_id: "a1",
      status: "queued",
    },
    { id: "end", title: "End", type: "end", status: "queued" },
  ],
  edges: [
    ["start", "agent_1"],
    ["agent_1", "end"],
  ],
};

const generatedWorkflow: ConversationWorkflow = {
  ...workflow,
  nodes: workflow.nodes.map((node) =>
    node.id === "agent_1" ? { ...node, title: "Generated Agent" } : node,
  ),
};

const disabledWorkflow: ConversationWorkflow = {
  ...workflow,
  settings: { ...workflow.settings, enabled: false },
};

const run: WorkflowRun = {
  id: "run1",
  conversation_id: "c1",
  status: "running",
  mode: "all_agents_independent",
  workflow_snapshot: workflow,
  node_states: [{ ...workflow.nodes[1], status: "running", progress: 30 }],
  edge_states: [{ from: "start", to: "agent_1", status: "completed" }],
  events: [{ type: "node_started", node_id: "agent_1" }],
  progress: 30,
};

function EmbeddedHarness() {
  const [mode, setMode] = useState<"chat" | "workflow">("workflow");
  if (mode === "chat") return <div>聊天内容</div>;
  return (
    <WorkflowStudioContent
      workspaceId="w1"
      conversationId="c1"
      embedded
      onBack={() => setMode("chat")}
      onError={vi.fn()}
      onSuccess={vi.fn()}
    />
  );
}

function renderEmbeddedWorkflow() {
  return render(
    <AntApp>
      <MemoryRouter>
        <EmbeddedHarness />
      </MemoryRouter>
    </AntApp>,
  );
}

describe("embedded workflow studio", () => {
  beforeEach(() => {
    vi.mocked(api.conversations).mockResolvedValue([conversation]);
    vi.mocked(api.agents).mockResolvedValue([agent]);
    vi.mocked(api.conversationWorkflow).mockResolvedValue(workflow);
    vi.mocked(api.workflowRuns).mockResolvedValue([]);
    vi.mocked(api.tools).mockResolvedValue([]);
    vi.mocked(api.skills).mockResolvedValue([]);
    vi.mocked(api.mcpServers).mockResolvedValue([]);
    vi.mocked(api.saveConversationWorkflow).mockResolvedValue(workflow);
    vi.mocked(api.updateConversation).mockResolvedValue({
      ...conversation,
      scheduling_strategy: "workflow",
      workflow_enabled: true,
    });
    vi.mocked(api.generateConversationWorkflow).mockResolvedValue(generatedWorkflow);
    vi.mocked(api.startWorkflowRun).mockResolvedValue(run);
  });

  it("renders inside the chat area and returns without a page topbar", async () => {
    const { container } = renderEmbeddedWorkflow();

    expect(await screen.findByTestId("workflow-canvas")).toBeInTheDocument();
    expect(container.querySelector(".workflow-studio-header")).toBeNull();
    expect(screen.queryByText("conversation.extra.workflow")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /返回聊天/ }));
    expect(await screen.findByText("聊天内容")).toBeInTheDocument();
  });

  it("saves, generates and starts a workflow run from the settings card", async () => {
    renderEmbeddedWorkflow();

    await screen.findByTestId("workflow-canvas");
    fireEvent.click(screen.getByRole("button", { name: "工作流设置" }));
    fireEvent.click(screen.getByTestId("workflow-save"));
    await waitFor(() => {
      expect(api.saveConversationWorkflow).toHaveBeenCalledWith("c1", expect.any(Object));
    });

    fireEvent.click(screen.getByRole("button", { name: /AI\s*生成/ }));
    fireEvent.click(screen.getByTestId("workflow-generate"));
    expect(await screen.findByText("Generated Agent")).toBeInTheDocument();
    expect(api.generateConversationWorkflow).toHaveBeenCalledWith("c1", "");

    fireEvent.click(screen.getByRole("button", { name: "工作流设置" }));
    const workflowRunsBeforeRun = vi.mocked(api.workflowRuns).mock.calls.length;
    fireEvent.click(screen.getByTestId("workflow-run"));
    await waitFor(() => {
      expect(api.startWorkflowRun).toHaveBeenCalledWith("c1", expect.any(Object));
    });
    await waitFor(() => {
      expect(api.workflowRuns).toHaveBeenCalledTimes(workflowRunsBeforeRun + 1);
    });
    expect(api.workflowRuns).toHaveBeenCalledWith("c1");
  });

  it("persists the workflow strategy when the enabled switch is clicked", async () => {
    vi.mocked(api.conversationWorkflow).mockResolvedValue(disabledWorkflow);
    vi.mocked(api.saveConversationWorkflow).mockImplementation(async (_id, next) => next);

    renderEmbeddedWorkflow();

    await screen.findByTestId("workflow-canvas");
    fireEvent.click(screen.getByTestId("workflow-settings"));
    fireEvent.click(screen.getByTestId("workflow-enabled-switch"));

    await waitFor(() => {
      expect(api.saveConversationWorkflow).toHaveBeenCalledWith(
        "c1",
        expect.objectContaining({
          settings: expect.objectContaining({ enabled: true }),
        }),
      );
    });
    await waitFor(() => {
      expect(api.updateConversation).toHaveBeenCalledWith(
        "c1",
        expect.objectContaining({
          scheduling_strategy: "workflow",
          workflow_enabled: true,
        }),
      );
    });
  });
});

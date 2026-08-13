import { App as AntApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import { WorkflowBoardPanel } from "../src/features/platform/tabs/WorkflowBoardPanel";
import type { Conversation, ConversationWorkflow } from "../src/types";

vi.mock("../src/api", () => ({
  api: {
    saveConversationWorkflow: vi.fn(),
    updateConversation: vi.fn(),
    generateConversationWorkflow: vi.fn(),
    startWorkflowRun: vi.fn(),
  },
}));

const conversation: Conversation = {
  id: "c1",
  workspace_id: "w1",
  chat_type: "group",
  title: "Workflow group",
  participants: [],
  updatedAt: "2026-05-26T00:00:00Z",
  pinned: false,
  archived: false,
  unread: 0,
  tags: [],
  category: "Default",
  lastMessage: "",
};

const workflow: ConversationWorkflow = {
  conversation_id: "c1",
  mode: "manual",
  settings: { enabled: true },
  nodes: [
    { id: "start", title: "Start", type: "start" },
    { id: "end", title: "End", type: "end" },
  ],
  edges: [
    {
      source: "start",
      target: "end",
      sourceHandle: "output",
      targetHandle: "input",
    },
  ],
};

describe("workflow board panel", () => {
  beforeEach(() => {
    vi.mocked(api.saveConversationWorkflow).mockImplementation(async (_id, next) => next);
    vi.mocked(api.updateConversation).mockResolvedValue({
      ...conversation,
      scheduling_strategy: "workflow",
      workflow_enabled: true,
    });
    vi.mocked(api.generateConversationWorkflow).mockResolvedValue(workflow);
  });

  it("updates the scheduling strategy when saving an enabled workflow", async () => {
    render(
      <AntApp>
        <WorkflowBoardPanel activeConversation={conversation} />
      </AntApp>,
    );

    fireEvent.click(screen.getByRole("button", { name: /AI 生成/ }));
    expect(await screen.findByDisplayValue(/"enabled": true/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /保存/ }));

    await waitFor(() => {
      expect(api.saveConversationWorkflow).toHaveBeenCalledWith("c1", workflow);
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

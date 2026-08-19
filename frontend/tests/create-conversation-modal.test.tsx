import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CreateConversationModal } from "../src/features/chat/components/CreateConversationModal";
import type { Agent } from "../src/types";

const agents: Agent[] = [
  {
    id: "daily-agent",
    name: "Daily Chat Agent",
    type: "chat",
    version: "1.0.0",
    capabilities: [{ label: "\u65e5\u5e38\u95ee\u7b54", category: "chat", proficiency: 90 }],
    description: "\u65e5\u5e38\u5bf9\u8bdd",
    status: "online",
    provider: "official",
    is_official: true,
    response_latency_ms: 0,
    config: {},
  },
];

const agent = (id: string, name: string, type: string, label: string): Agent => ({
  id,
  name,
  type,
  version: "1.0.0",
  capabilities: [{ label, category: type, proficiency: 90 }],
  description: name,
  status: "online",
  provider: "official",
  is_official: true,
  response_latency_ms: 0,
  config: {},
});

describe("CreateConversationModal", () => {
  it("creates a single chat with the default Daily Chat Agent", async () => {
    const onCreate = vi.fn();

    render(
      <CreateConversationModal
        open
        group={false}
        agents={agents}
        categoryOptions={["Default"]}
        onCancel={vi.fn()}
        onCreate={onCreate}
      />,
    );

    await screen.findByText(/Daily Chat Agent/);
    fireEvent.click(screen.getByTestId("create-conversation-confirm"));

    await waitFor(() => {
      expect(onCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          agentIds: ["daily-agent"],
          group: false,
          folder: "Default",
        }),
      );
    });
  });

  it("defaults a group chat to two peers without assigning a hierarchy", async () => {
    const onCreate = vi.fn();

    render(
      <CreateConversationModal
        open
        group
        agents={[
          agent("master", "Master Agent", "master", "调度/拆解"),
          agent("frontend", "Frontend Worker", "frontend", "前端/React"),
          agent("backend", "Backend Worker", "backend", "后端/API"),
          agent("reviewer", "Reviewer", "reviewer", "审查/质量"),
          agent("daily-agent", "Daily Chat Agent", "chat", "日常问答"),
        ]}
        categoryOptions={["Default"]}
        onCancel={vi.fn()}
        onCreate={onCreate}
      />,
    );

    await screen.findByText(/Master Agent/);
    expect(screen.getByText(/Master Agent/)).toBeInTheDocument();
    expect(screen.getByText(/Frontend Worker/)).toBeInTheDocument();
    expect(screen.queryByText(/Daily Chat Agent/)).toBeNull();

    fireEvent.click(screen.getByTestId("create-conversation-confirm"));

    await waitFor(() => {
      expect(onCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          agentIds: ["master", "frontend"],
          group: true,
          folder: "Default",
        }),
      );
    });
  });
});

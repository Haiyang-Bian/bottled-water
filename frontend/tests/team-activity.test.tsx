import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import { TeamActivity } from "../src/features/chat/components/ChatPanel/TeamActivity";
import type { Conversation } from "../src/types";

const conversation: Conversation = {
  id: "team-activity-conversation",
  chat_type: "group",
  title: "Team",
  participants: [
    { participant_type: "agent", agent_id: "author", agent_name: "Author" },
    { participant_type: "agent", agent_id: "reviewer", agent_name: "Reviewer" },
  ],
  updatedAt: new Date(0).toISOString(),
  pinned: false,
  archived: false,
  unread: 0,
  tags: [],
  lastMessage: "",
  team_last_sequence: 1,
  team_activity: [
    {
      message_id: "message-1",
      run_id: "run",
      conversation_id: "team-activity-conversation",
      sequence: 1,
      sender_type: "agent",
      sender_id: "author",
      recipient_agent_ids: ["reviewer"],
      channel: "direct",
      thread_id: "thread-1",
      content: "Please review the API.",
      expects_reply: true,
      status: "consumed",
      consumed_by: ["reviewer"],
      created_at: new Date(0).toISOString(),
    },
  ],
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("TeamActivity", () => {
  it("keeps auditable team history collapsed by default", async () => {
    vi.spyOn(api, "teamMessages").mockImplementation(() => new Promise(() => {}));

    render(<TeamActivity conversation={conversation} />);
    expect(screen.queryByText("Please review the API.")).toBeNull();
    fireEvent.click(screen.getByText("团队动态"));
    expect(await screen.findByText("Please review the API.")).toBeInTheDocument();
    expect(screen.getByText("Author")).toBeInTheDocument();
    expect(screen.getByText(/Reviewer/)).toBeInTheDocument();
  });
});

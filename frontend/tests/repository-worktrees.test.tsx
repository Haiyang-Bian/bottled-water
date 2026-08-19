import { App as AntApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/api";
import { RepositoryWorktreesSection } from "@/features/chat/components/drawers/RepositoryWorktreesSection";
import type { Agent, Conversation, ConversationRepositoryState } from "@/types";

vi.mock("@/api", () => ({
  api: {
    conversationRepository: vi.fn(),
    bindConversationRepository: vi.fn(),
    createAgentWorktree: vi.fn(),
    releaseAgentWorktree: vi.fn(),
    integrateAgentWorktree: vi.fn(),
  },
}));

function agent(id: string, name: string): Agent {
  return {
    id,
    name,
    type: "custom",
    version: "1.0",
    capabilities: [],
    description: "",
    status: "online",
    provider: "custom",
    is_official: false,
    response_latency_ms: 0,
    config: {},
  };
}

const agents: Agent[] = [agent("author", "Author"), agent("reviewer", "Reviewer")];

const conversation: Conversation = {
  id: "conversation",
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
};

const repositoryState: ConversationRepositoryState = {
  repository: {
    id: "repository",
    conversation_id: conversation.id,
    repository_path: "C:/project",
    base_commit: "0123456789abcdef",
    require_user_approval: true,
    status: "active",
    created_at: new Date(0).toISOString(),
    updated_at: new Date(0).toISOString(),
  },
  worktrees: [
    {
      id: "author-worktree",
      conversation_id: conversation.id,
      agent_id: "author",
      path: "C:/data/worktrees/author",
      branch: "agenthub/team/author",
      base_commit: "0123456789abcdef",
      head_commit: "abcdef0123456789",
      mode: "managed",
      status: "ready",
      dirty: false,
      merge_status: "idle",
      created_at: new Date(0).toISOString(),
      updated_at: new Date(0).toISOString(),
    },
  ],
};

function renderSection() {
  return render(
    <AntApp>
      <RepositoryWorktreesSection conversation={conversation} agents={agents} />
    </AntApp>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("RepositoryWorktreesSection", () => {
  it("binds a server-visible repository without assuming browser filesystem access", async () => {
    vi.mocked(api.conversationRepository).mockResolvedValue({
      repository: null,
      worktrees: [],
    });
    vi.mocked(api.bindConversationRepository).mockResolvedValue(repositoryState);
    renderSection();

    fireEvent.change(await screen.findByLabelText("仓库路径"), {
      target: { value: "C:/project" },
    });
    fireEvent.click(screen.getByRole("switch", { name: "合并需用户批准" }));
    fireEvent.click(screen.getByRole("button", { name: "绑定仓库" }));

    await waitFor(() =>
      expect(api.bindConversationRepository).toHaveBeenCalledWith("conversation", {
        repository_path: "C:/project",
        base_commit: undefined,
        require_user_approval: true,
      }),
    );
    expect(await screen.findByText("agenthub/team/author")).toBeInTheDocument();
  });

  it("creates missing managed worktrees and exposes branch status", async () => {
    vi.mocked(api.conversationRepository).mockResolvedValue(repositoryState);
    vi.mocked(api.createAgentWorktree).mockResolvedValue({
      ...repositoryState,
      worktrees: [
        ...repositoryState.worktrees,
        {
          ...repositoryState.worktrees[0],
          id: "reviewer-worktree",
          agent_id: "reviewer",
          path: "C:/data/worktrees/reviewer",
          branch: "agenthub/team/reviewer",
        },
      ],
    });
    renderSection();

    expect(await screen.findByText("agenthub/team/author")).toBeInTheDocument();
    const createButtons = screen.getAllByRole("button", { name: "创建工作树" });
    fireEvent.click(createButtons[0]);

    await waitFor(() =>
      expect(api.createAgentWorktree).toHaveBeenCalledWith("conversation", {
        agent_id: "reviewer",
        mode: "managed",
        path: undefined,
      }),
    );
    expect(await screen.findByText("agenthub/team/reviewer")).toBeInTheDocument();
  });
});

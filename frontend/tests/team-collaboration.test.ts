import { beforeEach, describe, expect, it } from "vitest";
import {
  applyTeamCollaborationEvent,
  mergeTeamMessageHistory,
  readTeamCursor,
} from "../src/lib/teamCollaboration";
import type { Conversation, TeamMessage } from "../src/types";

function conversation(id = "conversation-ordering"): Conversation {
  return {
    id,
    chat_type: "group",
    title: "Team",
    participants: [],
    updatedAt: new Date(0).toISOString(),
    pinned: false,
    archived: false,
    unread: 0,
    tags: [],
    lastMessage: "",
  };
}

function payload(sequence: number, id = `message-${sequence}`) {
  return {
    message_id: id,
    team_sequence: sequence,
    runtime_run_id: "run",
    sender_type: "agent",
    sender_id: "author",
    recipient_agent_ids: ["reviewer"],
    channel: "direct",
    content: `message ${sequence}`,
    expects_reply: true,
    status: "pending",
    runtime_event_id: `event-${sequence}`,
  };
}

beforeEach(() => {
  window.sessionStorage.clear();
});

describe("team collaboration ordering", () => {
  it("buffers out-of-order messages and drains only a continuous sequence", () => {
    const base = conversation();
    expect(
      applyTeamCollaborationEvent(
        base,
        "collaboration.message_created",
        payload(2),
      ),
    ).toEqual({});

    const patch = applyTeamCollaborationEvent(
      base,
      "collaboration.message_created",
      payload(1),
    );
    expect(patch.team_activity?.map((item) => item.sequence)).toEqual([1, 2]);
    expect(patch.team_last_sequence).toBe(2);
    expect(readTeamCursor(base.id)).toBe(2);

    const updated = { ...base, ...patch };
    expect(
      applyTeamCollaborationEvent(
        updated,
        "collaboration.message_created",
        payload(2),
      ),
    ).toEqual({});
  });

  it("applies consumed and resolved statuses without exposing another context", () => {
    const base = conversation("conversation-status");
    const created = {
      ...base,
      ...applyTeamCollaborationEvent(
        base,
        "collaboration.message_created",
        { ...payload(1), thread_id: "thread-1" },
      ),
    };
    const consumed = {
      ...created,
      ...applyTeamCollaborationEvent(
        created,
        "collaboration.message_consumed",
        { message_id: "message-1", agent_id: "reviewer" },
      ),
    };
    expect(consumed.team_activity?.[0].consumed_by).toEqual(["reviewer"]);
    const resolved = applyTeamCollaborationEvent(
      consumed,
      "collaboration.thread_resolved",
      { thread_id: "thread-1" },
    );
    expect(resolved.team_activity?.[0].status).toBe("resolved");
  });

  it("merges replay pages by message id and team sequence", () => {
    const base = conversation("conversation-history");
    const history: TeamMessage[] = [
      {
        message_id: "history-1",
        run_id: "run",
        conversation_id: base.id,
        sequence: 1,
        sender_type: "agent",
        sender_id: "author",
        recipient_agent_ids: ["reviewer"],
        channel: "direct",
        content: "audit",
        expects_reply: false,
        status: "consumed",
        consumed_by: ["reviewer"],
        created_at: new Date(0).toISOString(),
      },
    ];
    const patch = mergeTeamMessageHistory(base, [...history, ...history]);
    expect(patch.team_activity).toHaveLength(1);
    expect(patch.team_last_sequence).toBe(1);
  });
});

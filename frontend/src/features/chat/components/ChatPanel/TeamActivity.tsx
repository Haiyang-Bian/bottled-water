import { useEffect, useMemo } from "react";
import { Badge, Collapse, Empty, Space, Tag, Typography } from "antd";
import { api } from "@/api";
import { mergeTeamMessageHistory } from "@/lib/teamCollaboration";
import { useConversationStore } from "@/store";
import type { Conversation, TeamMessage } from "@/types";

const { Text, Paragraph } = Typography;

export function TeamActivity({ conversation }: { conversation?: Conversation }) {
  const updateConversation = useConversationStore((state) => state.updateConversation);
  const conversationId = conversation?.id;
  const chatType = conversation?.chat_type;
  const messages = conversation?.team_activity || [];
  const names = useMemo(() => {
    const values = new Map<string, string>();
    for (const participant of conversation?.participants || []) {
      if (participant.agent_id) {
        values.set(
          participant.agent_id,
          participant.agent_name || participant.nickname || participant.agent_id.slice(0, 8),
        );
      }
    }
    return values;
  }, [conversation?.participants]);

  useEffect(() => {
    if (!conversationId || chatType !== "group") return;
    let alive = true;
    void (async () => {
      let after = 0;
      const history: TeamMessage[] = [];
      for (let pageIndex = 0; pageIndex < 20; pageIndex += 1) {
        const page = await api.teamMessages(conversationId, after, 500);
        if (!alive) return;
        history.push(...page.items.map((item) => ({ ...item, replayed: true })));
        if (
          page.items.length === 0 ||
          page.next_sequence <= after ||
          page.next_sequence >= page.last_sequence
        ) {
          break;
        }
        after = page.next_sequence;
      }
      if (!alive) return;
      const current = useConversationStore
        .getState()
        .conversations.find((item) => item.id === conversationId);
      if (current) updateConversation(conversationId, mergeTeamMessageHistory(current, history));
    })().catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [chatType, conversationId, updateConversation]);

  if (!conversation || conversation.chat_type !== "group") return null;
  const item = {
    key: "team-activity",
    label: (
      <Space>
        <Text strong>团队动态</Text>
        <Badge count={messages.length} showZero color="#64748b" />
      </Space>
    ),
    children: messages.length ? (
      <div className="team-activity-list" data-testid="team-activity-list">
        {messages.map((message) => (
          <TeamActivityItem key={message.message_id} message={message} names={names} />
        ))}
      </div>
    ) : (
      <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无团队消息" />
    ),
  };
  return <Collapse className="team-activity" ghost items={[item]} />;
}

function TeamActivityItem({
  message,
  names,
}: {
  message: TeamMessage;
  names: Map<string, string>;
}) {
  const sender = message.sender_type === "user" ? "用户" : names.get(message.sender_id) || message.sender_id;
  const target =
    message.channel === "broadcast"
      ? "全体成员"
      : message.recipient_agent_ids.map((id) => names.get(id) || id).join("、");
  const color =
    message.status === "resolved"
      ? "green"
      : message.status === "interrupted"
        ? "red"
        : message.status === "consumed"
          ? "blue"
          : "default";
  return (
    <div className="team-activity-item" data-sequence={message.sequence}>
      <Space size={6} wrap>
        <Text strong>{sender}</Text>
        <Text type="secondary">→ {target}</Text>
        <Tag color={color}>{message.status}</Tag>
        {message.thread_id && <Tag>线程 {message.thread_id.slice(0, 8)}</Tag>}
        {message.reply_to_message_id && <Text type="secondary">回复</Text>}
      </Space>
      <Paragraph className="team-activity-content">{message.content}</Paragraph>
    </div>
  );
}

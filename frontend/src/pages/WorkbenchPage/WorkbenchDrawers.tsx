import { App as AntApp } from "antd";
import { api } from "@/api";
import { CreateConversationModal } from "@/features/chat/components/CreateConversationModal";
import { MembersDrawer } from "@/features/chat/components/drawers/MembersDrawer";
import { ConversationSettingsDrawer } from "@/features/chat/components/drawers/ConversationSettingsDrawer";
import type { Agent, Conversation } from "@/types";

export interface WorkbenchDrawersProps {
  // MembersDrawer
  membersOpen: boolean;
  activeConversation: Conversation | undefined;
  activeConversationId: string | undefined;
  onCloseMembers: () => void;
  onUpdateConversations: (
    updater: (current: Conversation[]) => Conversation[],
  ) => void;

  // ConversationSettingsDrawer
  conversationSettingsOpen: boolean;
  onCloseConversationSettings: () => void;
  conversationCategories: string[];
  onPatchConversation: (
    item: Conversation,
    patch: Partial<Conversation>,
  ) => Promise<void>;

  // CreateConversationModal
  createOpen: { open: boolean };
  onCancelCreate: () => void;
  onCreateConversation: (payload: {
    title?: string;
    agentIds: string[];
    group?: boolean;
    summaryAgentId?: string;
    folder: string;
  }) => Promise<void>;

  // Agents (for MembersDrawer)
  agents?: Agent[];
}

export function WorkbenchDrawers(props: WorkbenchDrawersProps) {
  const { message } = AntApp.useApp();

  const {
    membersOpen,
    activeConversation,
    activeConversationId,
    onCloseMembers,
    onUpdateConversations,

    conversationSettingsOpen,
    onCloseConversationSettings,
    conversationCategories,
    onPatchConversation,

    createOpen,
    onCancelCreate,
    onCreateConversation,

    agents = [],
  } = props;

  return (
    <>
      <MembersDrawer
        open={membersOpen}
        active={activeConversation}
        agents={agents}
        onClose={onCloseMembers}
        onAddAgents={async (ids) => {
          if (!activeConversationId) return;
          try {
            const updated = await api.addParticipants(activeConversationId, ids);
            onUpdateConversations((current) =>
              current.map((item) => (item.id === activeConversationId ? updated : item)),
            );
            message.success("成员已加入");
          } catch (error) {
            message.error(
              error instanceof Error ? error.message : "成员加入失败",
            );
          }
        }}
        onRemoveParticipant={async (participant) => {
          if (!activeConversationId || !participant.id) return;
          const updated = await api.removeParticipant(activeConversationId, participant.id);
          onUpdateConversations((current) =>
            current.map((item) => (item.id === activeConversationId ? updated : item)),
          );
          message.success("成员已移除");
        }}
      />
      <ConversationSettingsDrawer
        open={conversationSettingsOpen}
        active={activeConversation}
        agents={agents}
        categoryOptions={conversationCategories}
        onClose={onCloseConversationSettings}
        onSaveConversation={onPatchConversation}
      />
      <CreateConversationModal
        open={createOpen.open}
        agents={agents}
        categoryOptions={conversationCategories}
        onCancel={onCancelCreate}
        onCreate={onCreateConversation}
      />
    </>
  );
}

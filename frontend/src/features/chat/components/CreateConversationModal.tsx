import { useEffect, useMemo, useRef } from "react";
import { Form, Input, Modal, Select } from "antd";
import { normalizeConversationCategory } from "@/lib/conversation";
import type { Agent } from "@/types";

const COPY = {
  createChat: "\u521b\u5efa\u804a\u5929",
  conversationName: "\u4f1a\u8bdd\u540d\u79f0",
  groupPlaceholder: "\u591a Agent \u534f\u4f5c\u4f1a\u8bdd",
  singlePlaceholder: "\u5355\u804a\u4f1a\u8bdd",
  folder: "\u5206\u7c7b/\u6587\u4ef6\u5939",
  folderPlaceholder: "\u9009\u62e9\u5de6\u4fa7\u5206\u7c7b",
  chooseGroupAgent: "\u9009\u62e9 2-8 \u4e2a Agent",
  chooseSingleAgent: "\u9009\u62e9 1 \u4e2a Agent",
  groupAgentRequired: "\u9700\u8981\u9009\u62e9 2-8 \u4e2a Agent",
  singleAgentRequired: "\u5355\u804a\u9700\u8981\u9009\u62e9 1 \u4e2a Agent",
  agentPlaceholder: "\u4ece Agent \u901a\u8baf\u5f55\u9009\u62e9",
  summaryAgent: "\u6700\u7ec8\u6c47\u603b Agent\uff08\u53ef\u9009\uff09",
  dailyChat: "\u65e5\u5e38\u804a\u5929",
};

export function CreateConversationModal({
  open,
  group = true,
  agents,
  categoryOptions,
  onCancel,
  onCreate,
}: {
  open: boolean;
  group?: boolean;
  agents: Agent[];
  categoryOptions: string[];
  onCancel: () => void;
  onCreate: (payload: {
    title?: string;
    agentIds: string[];
    group?: boolean;
    summaryAgentId?: string;
    folder: string;
  }) => void;
}) {
  const [form] = Form.useForm();
  const onlineAgents = useMemo(
    () => agents.filter((agent) => agent.status === "online"),
    [agents],
  );
  const maxAgentCount = group ? 8 : 1;
  const initializedRef = useRef(false);
  const selectedAgentIdsRef = useRef<string[]>([]);
  const categorySelectOptions = categoryOptions.map((name) => ({
    label: name,
    value: name,
  }));

  const setSelectedAgentIds = (ids: string[]) => {
    const next = group ? ids.slice(0, maxAgentCount) : ids.slice(0, 1);
    selectedAgentIdsRef.current = next;
    form.setFieldValue("agentIds", next);
  };

  useEffect(() => {
    if (!open) {
      initializedRef.current = false;
      selectedAgentIdsRef.current = [];
      form.resetFields();
      return;
    }
    if (initializedRef.current) return;
    if (onlineAgents.length === 0) return;
    initializedRef.current = true;

    const defaultAgentIds = pickDefaultAgentIds(onlineAgents, maxAgentCount, group);
    selectedAgentIdsRef.current = defaultAgentIds;
    form.setFieldsValue({
      agentIds: defaultAgentIds,
      folder: "Default",
    });
  }, [open, onlineAgents, form, group, maxAgentCount]);

  const submit = async () => {
    const latestAgentIds =
      selectedAgentIdsRef.current.length > 0
        ? selectedAgentIdsRef.current
        : form.getFieldValue("agentIds") || [];
    form.setFieldValue("agentIds", latestAgentIds);
    const values = await form.validateFields();
    onCreate({
      title: values.title,
      agentIds: latestAgentIds,
      group,
      summaryAgentId: values.summaryAgentId || undefined,
      folder: normalizeConversationCategory(values.folder),
    });
    selectedAgentIdsRef.current = [];
    form.resetFields();
  };

  return (
    <Modal
      title={COPY.createChat}
      open={open}
      onCancel={onCancel}
      onOk={submit}
      okText={COPY.createChat}
      okButtonProps={{ "data-testid": "create-conversation-confirm" }}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{ folder: "Default" }}
      >
        <Form.Item name="title" label={COPY.conversationName}>
          <Input placeholder={group ? COPY.groupPlaceholder : COPY.singlePlaceholder} />
        </Form.Item>
        <Form.Item name="folder" label={COPY.folder}>
          <Select options={categorySelectOptions} placeholder={COPY.folderPlaceholder} />
        </Form.Item>
        <Form.Item
          name="agentIds"
          label={group ? COPY.chooseGroupAgent : COPY.chooseSingleAgent}
          rules={[
            {
              validator: async (_, value?: string[]) => {
                const count = value?.length ?? 0;
                if (group && (count < 2 || count > maxAgentCount)) {
                  throw new Error(COPY.groupAgentRequired);
                }
                if (!group && count !== 1) {
                  throw new Error(COPY.singleAgentRequired);
                }
              },
            },
          ]}
        >
          <Select
            data-testid="agent-select"
            mode="multiple"
            maxCount={maxAgentCount}
            placeholder={COPY.agentPlaceholder}
            onChange={(value) => setSelectedAgentIds(value)}
            onSelect={(_, option) => {
              if (!group) setSelectedAgentIds([String(option.value)]);
            }}
            options={onlineAgents.map((agent) => ({
              label: `${agent.name} \u00b7 ${agent.capabilities
                .slice(0, 2)
                .map((cap) => cap.label)
                .join("/")}`,
              value: agent.id,
            }))}
          />
        </Form.Item>
        {group && (
          <Form.Item
            name="summaryAgentId"
            label={COPY.summaryAgent}
            dependencies={["agentIds"]}
            rules={[
              {
                validator: async (_, value?: string) => {
                  const selected = form.getFieldValue("agentIds") || [];
                  if (value && !selected.includes(value)) {
                    throw new Error("\u6c47\u603b Agent \u5fc5\u987b\u662f\u5df2\u9009\u7fa4\u804a\u6210\u5458");
                  }
                },
              },
            ]}
          >
            <Select
              allowClear
              placeholder="\u4e0d\u6307\u5b9a\u65f6\uff0c\u56e2\u961f\u7ed3\u675f\u540e\u76f4\u63a5\u6536\u655b"
              options={onlineAgents.map((agent) => ({ label: agent.name, value: agent.id }))}
            />
          </Form.Item>
        )}
      </Form>
    </Modal>
  );
}

function pickDefaultAgentIds(
  agents: Agent[],
  maxAgentCount: number,
  group: boolean,
): string[] {
  if (group) {
    return agents.slice(0, Math.min(2, maxAgentCount)).map((agent) => agent.id);
  }
  const dailyAgent = agents.find((agent) => {
    const lowerName = agent.name.toLowerCase();
    return lowerName.includes("daily chat") || agent.name.includes(COPY.dailyChat);
  });
  const fallback = dailyAgent ?? agents[0];
  return fallback ? [fallback.id] : [];
}

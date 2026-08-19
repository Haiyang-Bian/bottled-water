import { useEffect, useMemo } from "react";
import {
  App as AntApp,
  Button,
  Divider,
  Drawer,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
} from "antd";
import { mergeConversationCategories } from "@/lib/conversation";
import type { Agent, Conversation } from "@/types";
import { RepositoryWorktreesSection } from "./RepositoryWorktreesSection";

const { Text } = Typography;
const { TextArea } = Input;

export function ConversationSettingsDrawer({
  open,
  active,
  agents,
  categoryOptions,
  onClose,
  onSaveConversation,
}: {
  open: boolean;
  active?: Conversation;
  agents: Agent[];
  categoryOptions: string[];
  onClose: () => void;
  onSaveConversation: (
    conversation: Conversation,
    patch: Partial<Conversation>,
  ) => Promise<void>;
}) {
  const [form] = Form.useForm();
  const { message } = AntApp.useApp();

  const categorySelectOptions = useMemo(
    () =>
      mergeConversationCategories(categoryOptions, [
        active?.folder || active?.category || "Default",
      ]).map((name) => ({ label: name, value: name })),
    [active?.category, active?.folder, categoryOptions],
  );

  const activeAgentIds = new Set(
    active?.participants
      .map((item) => item.agent_id)
      .filter(Boolean) as string[],
  );
  const activeAgents = agents.filter((agent) => activeAgentIds.has(agent.id));
  const isGroup = active?.chat_type === "group";
  const titleLabel = isGroup ? "群聊名称" : "会话名称";

  useEffect(() => {
    if (!open) return;
    form.setFieldsValue({
      title: active?.title,
      conversation_number: active?.conversation_number || active?.group_number || "",
      folder: active?.folder || active?.category || "Default",
      remark: active?.remark || "",
      scheduling_strategy: active?.scheduling_strategy || (isGroup ? "collaborative" : "single_agent"),
      summary_agent_id: active?.team_settings?.summary_agent_id || undefined,
      live_user_input: active?.team_settings?.live_user_input ?? true,
      max_collaboration_messages: active?.team_settings?.max_collaboration_messages ?? 64,
      max_agent_turns: active?.team_settings?.max_agent_turns ?? 12,
      max_open_threads: active?.team_settings?.max_open_threads ?? 24,
      max_team_message_chars: active?.team_settings?.max_team_message_chars ?? 8000,
    });
  }, [active, form, isGroup, open]);

  const save = async (values: {
    title: string;
    folder: string;
    remark?: string;
    scheduling_strategy?: string;
    summary_agent_id?: string;
    live_user_input?: boolean;
    max_collaboration_messages?: number;
    max_agent_turns?: number;
    max_open_threads?: number;
    max_team_message_chars?: number;
  }) => {
    if (!active) return;
    await onSaveConversation(active, {
      title: values.title,
      folder: values.folder,
      category: values.folder,
      remark: values.remark || "",
    });
    if (isGroup) {
      await onSaveConversation(active, {
        scheduling_strategy: values.scheduling_strategy || "collaborative",
        workflow_enabled: false,
        team_settings: {
          summary_agent_id: values.summary_agent_id || null,
          live_user_input: values.live_user_input ?? true,
          max_collaboration_messages: values.max_collaboration_messages ?? 64,
          max_agent_turns: values.max_agent_turns ?? 12,
          max_open_threads: values.max_open_threads ?? 24,
          max_team_message_chars: values.max_team_message_chars ?? 8000,
        },
      });
    }
    message.success(isGroup ? "群聊信息已保存" : "会话信息已保存");
  };

  return (
    <Drawer title={isGroup ? "群聊设置" : "会话设置"} width={560} open={open} onClose={onClose}>
      <Form form={form} layout="vertical" onFinish={save}>
        <Form.Item
          name="title"
          label={titleLabel}
          rules={[{ required: true, message: `请输入${titleLabel}` }]}
        >
          <Input maxLength={80} placeholder={`请输入${titleLabel}`} />
        </Form.Item>

        <Form.Item name="conversation_number" label="聊天编号">
          <Input disabled />
        </Form.Item>

        <Form.Item name="folder" label="分类/文件夹">
          <Select options={categorySelectOptions} placeholder="选择分类" />
        </Form.Item>

        <Form.Item name="remark" label="备注">
          <TextArea rows={4} maxLength={300} placeholder="记录这个群聊的用途、范围或注意事项" />
        </Form.Item>

        {isGroup && (
          <>
            <div className="conversation-settings-members">
              <Text strong>当前 Agent</Text>
              <Space size={[6, 6]} wrap className="conversation-settings-members-list">
                {activeAgents.length ? (
                  activeAgents.map((agent) => (
                    <Tag key={agent.id} color="blue">
                      {agent.name}
                    </Tag>
                  ))
                ) : (
                  <Text type="secondary">暂无 Agent 成员</Text>
                )}
              </Space>
            </div>
            <Divider orientation="left">团队协作</Divider>
            <Form.Item name="scheduling_strategy" label="调度方式">
              <Select
                options={[
                  { label: "平权消息协作", value: "collaborative" },
                  { label: "旧技术负责人策略", value: "tech_lead" },
                ]}
              />
            </Form.Item>
            <Form.Item name="summary_agent_id" label="最终汇总 Agent">
              <Select
                allowClear
                placeholder="不指定时直接收敛"
                options={activeAgents.map((agent) => ({
                  label: agent.name,
                  value: agent.id,
                }))}
              />
            </Form.Item>
            <Form.Item
              name="live_user_input"
              label="运行中实时插话"
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
            <Space size="middle" wrap align="start">
              <Form.Item name="max_collaboration_messages" label="Agent 消息上限">
                <InputNumber min={1} max={512} />
              </Form.Item>
              <Form.Item name="max_agent_turns" label="每 Agent 轮次">
                <InputNumber min={1} max={100} />
              </Form.Item>
              <Form.Item name="max_open_threads" label="开放线程上限">
                <InputNumber min={1} max={128} />
              </Form.Item>
              <Form.Item name="max_team_message_chars" label="单条字符上限">
                <InputNumber min={100} max={32000} step={100} />
              </Form.Item>
            </Space>
            {active && (
              <>
                <Divider orientation="left">代码协作</Divider>
                <RepositoryWorktreesSection conversation={active} agents={agents} />
              </>
            )}
          </>
        )}

        <Space>
          <Button type="primary" htmlType="submit" disabled={!active}>
            保存资料
          </Button>
          <Button onClick={onClose}>关闭</Button>
        </Space>
      </Form>
    </Drawer>
  );
}

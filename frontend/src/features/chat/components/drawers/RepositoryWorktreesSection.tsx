import { useEffect, useMemo, useState } from "react";
import { FolderOpenOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Input,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
} from "antd";
import { api } from "@/api";
import { isDesktopRuntime } from "@/config/desktopRuntime";
import { selectDesktopDirectory } from "@/lib/desktopDirectory";
import type {
  Agent,
  AgentWorktree,
  Conversation,
  ConversationRepositoryState,
} from "@/types";

const { Paragraph, Text } = Typography;

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "工作树操作失败";
}

export function RepositoryWorktreesSection({
  conversation,
  agents,
}: {
  conversation: Conversation;
  agents: Agent[];
}) {
  const { message } = AntApp.useApp();
  const [state, setState] = useState<ConversationRepositoryState>({
    repository: null,
    worktrees: [],
  });
  const [loading, setLoading] = useState(false);
  const [busyKey, setBusyKey] = useState("");
  const [repositoryPath, setRepositoryPath] = useState("");
  const [baseCommit, setBaseCommit] = useState("");
  const [requireApproval, setRequireApproval] = useState(false);
  const [modes, setModes] = useState<Record<string, "managed" | "adopted">>({});
  const [adoptedPaths, setAdoptedPaths] = useState<Record<string, string>>({});
  const [integrationSources, setIntegrationSources] = useState<Record<string, string>>({});
  const desktop = isDesktopRuntime();

  const memberAgents = useMemo(() => {
    const ids = new Set(
      conversation.participants
        .map((participant) => participant.agent_id)
        .filter(Boolean) as string[],
    );
    return agents.filter((agent) => ids.has(agent.id));
  }, [agents, conversation.participants]);

  const applyState = (next: ConversationRepositoryState) => {
    setState(next);
    if (next.repository) {
      setRepositoryPath(next.repository.repository_path);
      setBaseCommit(next.repository.base_commit);
      setRequireApproval(next.repository.require_user_approval);
    }
  };

  const load = async () => {
    setLoading(true);
    try {
      applyState(await api.conversationRepository(conversation.id));
    } catch (error) {
      message.error(errorText(error));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let current = true;
    setLoading(true);
    api
      .conversationRepository(conversation.id)
      .then((next) => {
        if (current) applyState(next);
      })
      .catch((error) => {
        if (current) message.error(errorText(error));
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [conversation.id, message]);

  const chooseDirectory = async (setValue: (value: string) => void) => {
    try {
      const selected = await selectDesktopDirectory();
      if (selected) setValue(selected);
    } catch (error) {
      message.error(errorText(error));
    }
  };

  const bind = async () => {
    if (!repositoryPath.trim()) {
      message.warning("请输入服务器可访问的 Git 仓库路径");
      return;
    }
    setBusyKey("repository");
    try {
      applyState(
        await api.bindConversationRepository(conversation.id, {
          repository_path: repositoryPath.trim(),
          base_commit: baseCommit.trim() || undefined,
          require_user_approval: requireApproval,
        }),
      );
      message.success("仓库绑定已保存");
    } catch (error) {
      message.error(errorText(error));
    } finally {
      setBusyKey("");
    }
  };

  const create = async (agentId: string) => {
    const mode = modes[agentId] || "managed";
    setBusyKey(`create:${agentId}`);
    try {
      applyState(
        await api.createAgentWorktree(conversation.id, {
          agent_id: agentId,
          mode,
          path: mode === "adopted" ? adoptedPaths[agentId]?.trim() : undefined,
        }),
      );
      message.success("Agent 工作树已就绪");
    } catch (error) {
      message.error(errorText(error));
    } finally {
      setBusyKey("");
    }
  };

  const release = async (worktree: AgentWorktree) => {
    setBusyKey(`release:${worktree.id}`);
    try {
      applyState(await api.releaseAgentWorktree(conversation.id, worktree.id));
      message.success("工作树绑定已释放");
    } catch (error) {
      message.error(errorText(error));
    } finally {
      setBusyKey("");
    }
  };

  const integrate = async (target: AgentWorktree) => {
    const sourceAgentId = integrationSources[target.id];
    if (!sourceAgentId) {
      message.warning("请选择要合并的来源 Agent");
      return;
    }
    setBusyKey(`integrate:${target.id}`);
    try {
      const next = await api.integrateAgentWorktree(
        conversation.id,
        target.id,
        sourceAgentId,
      );
      applyState(next);
      const integrationStatus = String(next.integration?.status || "succeeded");
      if (integrationStatus === "conflict") {
        message.warning("合并冲突已安全撤销，请让团队根据冲突摘要继续讨论");
      } else {
        message.success("分支已合并到目标 Agent 工作树");
      }
    } catch (error) {
      message.error(errorText(error));
    } finally {
      setBusyKey("");
    }
  };

  const worktreeByAgent = new Map(state.worktrees.map((item) => [item.agent_id, item]));

  return (
    <div className="repository-worktrees" data-testid="repository-worktrees">
      <Space className="repository-worktrees-header">
        <Text strong>仓库与 Agent 工作树</Text>
        <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={load}>
          刷新
        </Button>
      </Space>
      <Alert
        type="info"
        showIcon
        message="每个 Agent 只在自己的 Git worktree 中执行；AgentHub 不会 push、rebase、reset 或修改绑定仓库的当前分支。"
      />
      <Space.Compact block className="repository-path-input">
        <Input
          aria-label="仓库路径"
          value={repositoryPath}
          placeholder={desktop ? "选择本机 Git 仓库" : "服务器本地 Git 仓库路径"}
          onChange={(event) => setRepositoryPath(event.target.value)}
        />
        {desktop && (
          <Button
            icon={<FolderOpenOutlined />}
            onClick={() => chooseDirectory(setRepositoryPath)}
          >
            选择
          </Button>
        )}
      </Space.Compact>
      <Space wrap className="repository-binding-options">
        <Input
          aria-label="基准提交"
          value={baseCommit}
          placeholder="基准提交，留空使用当前 HEAD"
          onChange={(event) => setBaseCommit(event.target.value)}
        />
        <Space>
          <Switch
            aria-label="合并需用户批准"
            checked={requireApproval}
            onChange={setRequireApproval}
          />
          <Text>合并需用户批准</Text>
        </Space>
        <Button type="primary" loading={busyKey === "repository"} onClick={bind}>
          {state.repository ? "保存仓库设置" : "绑定仓库"}
        </Button>
      </Space>
      {state.repository && (
        <Paragraph type="secondary" copyable={{ text: state.repository.repository_path }}>
          基准 {state.repository.base_commit.slice(0, 12)} · {state.repository.repository_path}
        </Paragraph>
      )}

      {state.repository &&
        memberAgents.map((agent) => {
          const worktree = worktreeByAgent.get(agent.id);
          if (!worktree) {
            const mode = modes[agent.id] || "managed";
            return (
              <Card key={agent.id} size="small" title={agent.name} className="worktree-card">
                <Space wrap>
                  <Select
                    aria-label={`${agent.name} 工作树模式`}
                    value={mode}
                    options={[
                      { label: "AgentHub managed", value: "managed" },
                      { label: "采用现有 worktree", value: "adopted" },
                    ]}
                    onChange={(value) => setModes((current) => ({ ...current, [agent.id]: value }))}
                  />
                  {mode === "adopted" && (
                    <Space.Compact>
                      <Input
                        aria-label={`${agent.name} worktree 路径`}
                        value={adoptedPaths[agent.id] || ""}
                        placeholder="现有 Git worktree 路径"
                        onChange={(event) =>
                          setAdoptedPaths((current) => ({
                            ...current,
                            [agent.id]: event.target.value,
                          }))
                        }
                      />
                      {desktop && (
                        <Button
                          icon={<FolderOpenOutlined />}
                          onClick={() =>
                            chooseDirectory((value) =>
                              setAdoptedPaths((current) => ({ ...current, [agent.id]: value })),
                            )
                          }
                        />
                      )}
                    </Space.Compact>
                  )}
                  <Button loading={busyKey === `create:${agent.id}`} onClick={() => create(agent.id)}>
                    创建工作树
                  </Button>
                </Space>
              </Card>
            );
          }
          const sourceOptions = state.worktrees
            .filter((item) => item.agent_id !== worktree.agent_id)
            .map((item) => ({
              value: item.agent_id,
              label: memberAgents.find((candidate) => candidate.id === item.agent_id)?.name || item.branch,
            }));
          return (
            <Card key={agent.id} size="small" title={agent.name} className="worktree-card">
              <Space size={[6, 6]} wrap>
                <Tag color={worktree.mode === "managed" ? "blue" : "purple"}>{worktree.mode}</Tag>
                <Tag color={worktree.dirty ? "orange" : "green"}>
                  {worktree.dirty ? "dirty" : "clean"}
                </Tag>
                <Tag>{worktree.merge_status}</Tag>
                <Text code>{worktree.branch}</Text>
                <Text type="secondary">{worktree.head_commit.slice(0, 12)}</Text>
              </Space>
              <Paragraph type="secondary" ellipsis={{ rows: 1 }} copyable={{ text: worktree.path }}>
                {worktree.path}
              </Paragraph>
              {worktree.last_error && <Alert type="warning" message={worktree.last_error} />}
              <Space wrap>
                {sourceOptions.length > 0 && (
                  <>
                    <Select
                      aria-label={`${agent.name} 合并来源`}
                      placeholder="选择来源 Agent"
                      options={sourceOptions}
                      value={integrationSources[worktree.id]}
                      onChange={(value) =>
                        setIntegrationSources((current) => ({
                          ...current,
                          [worktree.id]: value,
                        }))
                      }
                    />
                    <Button
                      loading={busyKey === `integrate:${worktree.id}`}
                      onClick={() => integrate(worktree)}
                    >
                      批准并合并
                    </Button>
                  </>
                )}
                <Button
                  danger
                  disabled={worktree.dirty}
                  loading={busyKey === `release:${worktree.id}`}
                  onClick={() => release(worktree)}
                >
                  释放
                </Button>
              </Space>
            </Card>
          );
        })}
    </div>
  );
}

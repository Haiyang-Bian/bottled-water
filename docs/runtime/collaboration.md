# 平权团队协作语义

## 边界

同一个群聊 `Conversation` 构成团队协作域，同时最多存在一个活跃 Run。Run 内的 AgentActor 可以并行执行并跨轮交换消息，但彼此没有代码层面的主从关系。Leader、Reviewer、Integrator 等称谓只影响提示词和工具权限；Kernel 始终验证相同的生命周期、预算与租约规则。

## 消息协议

`TeamMessage` 支持私信、广播、回复与讨论线程。私信只投递给 `recipient_agent_ids`；未指定目标时广播给发送者之外的全部成员。`expects_reply` 创建或延续开放线程，但发送操作立即返回，不同步等待接收者。

每次 Agent 执行只在 `AgentExecutionRequest.inbox` 中获得本轮未读消息。`CollaborativeTeamPolicy` 首轮调度被用户 `@` 的 Agent，否则并行调度全部成员；后续优先调度有未读消息的成员。活跃 Run 中的用户插话在当前模型调用结束后的安全检查点投递，不创建第二个 Run。

讨论结束必须调用 `team.resolve_thread` 并提交结论。没有未读消息后，若设置 `summary_agent_id`，Kernel 只执行一次最终汇总；汇总 Agent 可读取脱敏团队记录与未解决线程列表，但不能继续发送团队消息，也不拥有额外终态权限。

## 持久化与隐私

`TeamJournal` 在一个事务中写入团队消息和 `collaboration.*` Runtime Event，提交后才通知 AgentHub 投影与客户端。团队消息采用 Conversation 级单调序号，断线重放按序去重；用户可审计私信内容，但普通 Agent 不能读取不属于自己的私信。

Agent 的 reasoning、完整提示词、临时工具帧和私有上下文不进入 TeamMessage。取消、失败或 `process_lost` 会把未消费消息标记为 `interrupted`，防止下一 Run 无意续跑旧意图。

## 预算与失败

默认限制为 64 条 Agent 消息、每 Agent 12 轮、24 个开放线程、单条消息 8,000 字符。消息预算和轮次耗尽分别收敛为 `collaboration_message_budget_exhausted` 与 `agent_turn_budget_exhausted`；非法目标、自发自收、超长消息或无效线程收敛为 `collaboration_protocol_error`。这些限制与 Token、wall-clock、idle 和无进展预算同时生效。

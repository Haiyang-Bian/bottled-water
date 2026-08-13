# Runtime 不变量

> `MUST` 表示不可违反，`SHOULD` 表示除非有记录充分的理由否则必须遵守，`MAY` 表示可选行为。

## 生命周期与终态

1. 每个 `Run` **MUST** 具有唯一 `run_id`，并归属一个稳定 `context_scope_id`。
2. Run **MUST** 只收敛到 `completed`、`failed` 或 `cancelled` 中的一个终态；内存状态与 `RunJournal` **MUST** 使用一次性终态 CAS，且终态更新与唯一终态事件 **MUST** 同事务提交。
3. `completed` **MUST** 来自通过 Watchdog 验证的完成提案，且不存在仍可写状态的工作。
4. 只有调用者主动取消 **MAY** 产生 `cancelled`。超时、预算、无进展、策略失败、上下文冲突和 Runtime 关闭 **MUST** 产生 `failed`。
5. 终态后的控制事件、模型结果和工具结果 **MUST NOT** 改变 Context、输出或终态；只有持有 Kernel 权限的迟到拒绝观测事件 **MAY** 继续追加。

## 事件与因果关系

1. `EventEnvelope` **MUST** 包含 `event_id`、`run_id`、`context_scope_id`、Run 内单调 `sequence`、`type`、`source`、`target`、`payload`、`correlation_id`、`causation_id` 和 `occurred_at`。
2. 控制事件与观测事件 **MUST** 分离；Policy 提案不是已执行事实。
3. Runtime **MUST** 只保证单个 Run 内顺序，不声明跨 Run 全局顺序。
4. EventEnvelope **MUST** 先持久化，再通知 `RunHandle` 和实时 Sink；提交前客户端 **MUST NOT** 观察到该事件。
5. 同一 `event_id` 的相同重试 **MUST** 幂等；同一 Run/sequence 内容不一致 **MUST** 失败为 `event_sequence_conflict`。
6. Event Log **MUST** 只追加，修正通过新事件表达；消费者 **MUST** 以持久游标和事件 ID 幂等处理至少一次投递。
7. `thinking`、`reasoning`、`reasoning_content` 和凭据字段 **MUST** 在持久化前递归脱敏；原始内容 **MUST NOT** 进入 Event Log、Message、generation 读模型或长期 Context。
8. 旧 Run 缺少完整 Journal 时 **MUST NOT** 伪造历史，并 **MUST** 返回 `history_complete=false`。

## ContextScope 与共享状态

1. ContextScope **MUST** 只跨 Run 保存消息、Blackboard 和结构化 `AgentMemory`。
2. Blackboard 与 ContextStore 写入 **MUST** 携带期望版本，并在锁或事务内 CAS；冲突 **MUST** 显式失败为 `context_conflict`。
3. Agent 私有推理、思考草稿和临时工具帧 **MUST NOT** 被其他 Agent 直接读取，也 **MUST NOT** 提升为长期记忆。
4. 需要共享的结果 **MUST** 先转化为报告、事实、产物引用或 Blackboard 更新。
5. 进程崩溃后若没有安全检查点，遗留 Run **MUST** 失败为 `process_lost`，不得伪装为续跑或成功。

## 调度、预算与用量

1. `SchedulerPolicy.propose(snapshot, trigger)` **MUST** 只返回提案；不得投递事件、修改 Actor、写数据库或提交终态。
2. Kernel **MUST** 校验目标 Agent、权限、状态和预算后，才将提案转换为 Mailbox 控制事件。
3. Watchdog **MUST** 独立限制 wall-clock、idle、调度提案数、总 Token 和连续无进展次数。Token 流 **MUST NOT** 重置 idle deadline。
4. Model 用量 **SHOULD** 优先采用 Provider usage；缺失时 **MUST** 标记为估算。流式输出耗尽剩余预算时 **MUST** 主动关流。
5. 稳定失败原因 **MUST** 包括 `wall_time_exceeded`、`idle_timeout`、`decision_budget_exhausted`、`token_budget_exhausted`、`no_progress`、`policy_error`、`context_conflict`、`adapter_timeout`、`event_store_error`、`event_sequence_conflict`、`runtime_shutdown` 和 `internal_error`。

## 取消、租约与 Adapter

1. `RunHandle.cancel()` **MUST** 幂等、传播 `CancellationScope`，并等待唯一终态。
2. 取消 **MUST** 到达 Policy、AgentActor、Model、Tool 和受管 Task；宽限期后 Kernel **MUST** 撤销 `RunLease`。
3. 有外部副作用的阻塞 Adapter **MUST** 提供可终止边界；否则返回 `adapter_not_cancellable`。
4. 纯读取操作 **MAY** 在线程中继续，但租约失效后其结果 **MUST NOT** 提交。
5. Actor **MUST** 在模型、工具和事件提交边界提供协作式检查点；这不构成强抢占承诺。

## 背压与故障隔离

1. Mailbox **MUST** 有容量边界和明确的满队列策略；控制事件不得静默丢弃。
2. EventSink **MUST** 在 Journal 提交后接收事件；慢 EventSink **MUST NOT** 无限阻塞 Actor、Watchdog 或终态提交。
3. 失败订阅者 **MUST** 与其他 Sink 隔离并留下可观测记录。
4. 外部 Adapter **MUST** 设置超时并返回可分类错误。

新实现进入主路径前，必须用确定性测试证明相关不变量；真实 Provider 测试不能替代状态机与并发测试。

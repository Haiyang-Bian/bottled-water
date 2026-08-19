# Runtime 当前实现对照

> 快照日期：2026-08-20。本文只描述当前分支中可由源码和测试验证的状态，不构成产品能力声明。

状态仅使用：`已实现`、`部分实现`、`未实现`、`遗留路径`。

| 目标契约 | 当前机制 | 状态 | 已知差距 |
| --- | --- | --- | --- |
| `ContextScope + Run` 双层生命周期 | [`RuntimeEngine`、`RunHandle`、`RunKernel`](../../backend/src/agent_runtime/runtime/engine.py) 与 [`run_types.py`](../../backend/src/agent_runtime/core/run_types.py) | 已实现 | 公共 API 仍处于 V1；Runtime 长期位于当前 monorepo |
| 唯一终态与稳定 reason code | Kernel 锁与 [`RunJournal.try_finish`](../../backend/src/agent_runtime/core/ports.py) 原子提交终态 CAS 和终态事件 | 已实现 | 尚无跨进程实时事件协调；SQL 行锁依赖数据库事务语义 |
| Run 拥有 Actor Task 与 Mailbox | [`AgentActor`](../../backend/src/agent_runtime/runtime/agent_actor.py) 按 Run 创建，控制提案经 [`Mailbox`](../../backend/src/agent_runtime/runtime/mailbox.py) 投递 | 已实现 | Actor 控制仍是协作式；阻塞本地代码无法获得操作系统级抢占 |
| wall/idle/决策/Token/无进展预算 | [`RunWatchdog`](../../backend/src/agent_runtime/runtime/run_watchdog.py) 使用单调时钟，Kernel 统一失败收敛 | 已实现 | 暂未引入费用预算或租户级配额 |
| Provider 用量与流式硬上限 | [`AgentLoopExecutor`](../../backend/src/agent_runtime/runtime/agent_executor.py) 汇总 usage；[`streaming.py`](../../backend/src/model_provider/core/streaming.py) 主动关闭超限流 | 已实现 | Provider 未返回 usage 时只能估算，精度取决于 tokenizer |
| 单 Agent、Workflow、Team Lead、平权团队共用 Policy 接口 | [`policies.py`](../../backend/src/agent_runtime/strategies/policies.py) 与 [`CollaborativeTeamPolicy`](../../backend/src/agent_runtime/strategies/collaborative.py) 实现同一 `SchedulerPolicy`；产品规则位于应用层 | 已实现 | Workflow 遍历器内部仍保留可变游标，尚未做到事件回放重建 |
| Agent 私信、广播、回复与讨论线程 | [`TeamMessenger`](../../backend/src/agent_runtime/core/ports.py)、[`RunKernel`](../../backend/src/agent_runtime/runtime/engine.py) 和 [`TeamJournal`](../../backend/src/agent_runtime/runtime/team_collaboration.py) 提供主动共享、目标收件箱与显式结论 | 已实现 | V1 仅限同一 Conversation；外部 CLI Agent 仍只保留 Port 接入边界 |
| 团队消息持久化和用户审计 | [`SQLTeamJournal`](../../backend/src/app/persistence/team_journal.py) 原子保存加密消息与 Runtime Event；团队消息 API 和前端团队动态按序补拉 | 已实现 | 尚无跨进程实时广播；团队动态当前是只读审计视图 |
| 团队预算与非正常中断 | Kernel 限制 64 条 Agent 消息、每 Agent 12 轮、24 个开放线程和单条 8,000 字符；取消、失败和进程丢失将待处理消息标记为 `interrupted` | 已实现 | 技术预算可由 Conversation 设置调整，但尚无租户级总配额 |
| 平权职责与最终汇总 | Kernel 不保存角色枚举；`summary_agent_id` 仅获得一次脱敏团队记录和最终汇总调用 | 已实现 | 提示词质量和工具授权由 AgentHub 配置负责，不属于 Kernel 判定 |
| Conversation 仓库与 Agent 工作树 | [`WorktreeService`](../../backend/src/app/services/worktrees.py) 管理单仓库、基准提交以及 managed/adopted 工作树；路径与分支唯一绑定 | 已实现 | V1 只支持单仓库；归档不自动释放工作树 |
| 可信执行根隔离 | [`SQLExecutionRootPort`](../../backend/src/app/services/execution_roots.py) 和不可由 JSON 伪造的 [`TrustedExecutionRoot`](../../backend/src/app/services/tools/execution_root.py) 约束文件、终端、沙箱和外部 CLI Agent | 已实现 | 操作系统级沙箱仍取决于具体 Adapter；未绑定仓库时保留旧执行根语义 |
| 安全 Git 协作 | [`git_collaboration.py`](../../backend/src/app/services/tools/git_collaboration.py) 提供 status、diff、commit 和 integrate；冲突 abort 后以团队消息回传 | 已实现 | 不提供 push、reset、rebase 或历史改写；语义审查依赖 Agent 与用户流程 |
| 版本化 Blackboard 与跨 Run Context CAS | [`VersionedBlackboard`](../../backend/src/agent_runtime/context/scope_store.py) 和 [`SQLContextStore`](../../backend/src/app/persistence/runtime_store.py) 检查期望版本 | 已实现 | ContextState 当前按 Conversation 建模，尚未抽象其他 Scope 类型 |
| 结构化长期 AgentMemory | `AgentLoopExecutor` 只提交摘要、完成任务和阻塞项；[`runtime_context_states`](../../backend/src/db/models/runtime.py) 持久化结构化值 | 已实现 | facts 与 output_refs 需要更多 Adapter 生产调用；不支持中途 PrivateContext 检查点 |
| 取消传播和迟到写隔离 | [`CancellationScope`、`RunLease`](../../backend/src/agent_runtime/runtime/cancellation.py) 与 [`adapter_isolation.py`](../../backend/src/agent_runtime/runtime/adapter_isolation.py) | 部分实现 | Model/Tool Adapter 尚未全部声明可终止边界；纯同步阻塞实现仍需子进程隔离 |
| 持久 EventEnvelope | [`RunJournal`](../../backend/src/agent_runtime/runtime/run_journal.py) 在通知 Handle/Sink 前保存稳定 ID、Run 内序号、因果字段和脱敏载荷 | 已实现 | `correlation_id`/`causation_id` 尚未覆盖所有 Adapter 事件 |
| 完整持久 Event Log 与至少一次投递 | [`runtime_events` 与 consumer cursor](../../backend/src/db/models/runtime.py) 支持有序分页、幂等追加、显式冲突和投影补偿 | 已实现 | 旧 Run 返回 `history_complete=false`；尚无压缩、自动过期、失败队列和跨进程实时广播 |
| Mailbox 与 Sink 背压隔离 | Mailbox 使用有界 `asyncio.Queue`；事件持久化后才调用 Sink，Sink 失败不改变 Kernel 终态 | 部分实现 | 满 Mailbox 会等待；慢 Sink 仍可能阻塞事件生产，失败未进入隔离队列 |
| AgentHub Run 管理 | [`ConversationRunManager`](../../backend/src/app/services/conversation_run_manager.py) 每个 Conversation 最多一个活跃 Handle，排队后续输入，不缓存终止 Run | 已实现 | 队列仅在单进程内存中，进程丢失时未提交输入会丢失 |
| `generation_id == run_id` 与前端协议投影 | [`event_projection.py`](../../backend/src/app/services/runtime/event_projection.py) 保持现有协议；[`runtime_events.py`](../../backend/src/app/api/runtime_events.py) 提供鉴权分页重放 | 已实现 | 前端消费带 Runtime 元数据的兼容投影，而非直接解释原始 Envelope |
| 前端断线重放 | [`message.ts`](../../frontend/src/api/message.ts) 持久保存连续序号、缓存乱序实时事件、丢弃重复并分页补拉缺口 | 已实现 | cursor 位于 `sessionStorage`；跨标签页、跨设备和跨进程实时广播未实现 |
| Run、Event 与 Context 数据真源 | [`runtime_runs`、`runtime_events`、`runtime_context_states`](../../backend/src/db/models/runtime.py) 保存终态、加密输出、Journal 和连续状态 | 已实现 | generation 继续作为前端读模型，但由持久事件消费者幂等更新 |
| 进程丢失处理 | 应用启动时把遗留 Run 收敛为 `failed/process_lost` | 已实现 | V1 不恢复运行中的 Actor 或工具调用 |
| Kernel 不理解产品交付规则 | 全栈顺序、文档和部署启发式已迁入 AgentHub Policy | 部分实现 | [`AgentLoop`](../../backend/src/agent_runtime/runtime/agent_loop.py) 仍含产物生成、部署验证和应用特定提示逻辑 |
| 单一 Runtime 主链与依赖方向 | 旧 Orchestrator 已删除；AST 测试禁止 `agent_runtime` 导入 `app` 或 `db` | 已实现 | AgentLoop 仍含产物、全栈和部署启发式；应用层实时事件总线仍作为 Journal 后的投影通道 |

## 已移除入口

`Session`、`Orchestrator`、`ActorOrchestrator`、`SchedulerAgent`、旧 Watchdog 和旧 Scheduler 不再从 `agent_runtime` 导出，也没有兼容别名。请求中的 `runtime_mode` 以 HTTP 422 和 `runtime_mode_removed` 拒绝；策略只由聊天类型、`scheduling_strategy` 与 `workflow_enabled` 决定。

## 后续范围

1. 为 EventSink 增加有界队列、慢订阅者隔离与失败队列。
2. 增加日志压缩、保留策略和跨进程实时广播；现阶段不自动删除 Journal。
3. 引入 Run 检查点和可恢复 PrivateContext；在此之前保持 `process_lost` 失败语义。
4. 继续将 AgentLoop 中的产物、全栈和部署逻辑迁往 AgentHub Policy/Tool。
5. 为外部 CLI Agent 实现持久连接，并在不放宽 `ExecutionRootPort` 的前提下接入同一团队协议。

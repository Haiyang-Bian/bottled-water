# Runtime 当前实现对照

> 快照日期：2026-08-13。本文只描述当前分支中可由源码和测试验证的状态，不构成产品能力声明。

状态仅使用：`已实现`、`部分实现`、`未实现`、`遗留路径`。

| 目标契约 | 当前机制 | 状态 | 已知差距 |
| --- | --- | --- | --- |
| `ContextScope + Run` 双层生命周期 | [`RuntimeEngine`、`RunHandle`、`RunKernel`](../../backend/src/agent_runtime/runtime/engine.py) 与 [`run_types.py`](../../backend/src/agent_runtime/core/run_types.py) | 已实现 | 公共 API 仍处于 V1，尚未独立发包 |
| 唯一终态与稳定 reason code | Kernel 锁与 [`RunStore.try_finish`](../../backend/src/agent_runtime/core/ports.py) 共同执行终态 CAS | 已实现 | 尚无跨进程事件协调；SQL 行锁依赖数据库事务语义 |
| Run 拥有 Actor Task 与 Mailbox | [`AgentActor`](../../backend/src/agent_runtime/runtime/agent_actor.py) 按 Run 创建，控制提案经 [`Mailbox`](../../backend/src/agent_runtime/runtime/mailbox.py) 投递 | 已实现 | Actor 控制仍是协作式；阻塞本地代码无法获得操作系统级抢占 |
| wall/idle/决策/Token/无进展预算 | [`RunWatchdog`](../../backend/src/agent_runtime/runtime/run_watchdog.py) 使用单调时钟，Kernel 统一失败收敛 | 已实现 | 暂未引入费用预算或租户级配额 |
| Provider 用量与流式硬上限 | [`AgentLoopExecutor`](../../backend/src/agent_runtime/runtime/agent_executor.py) 汇总 usage；[`streaming.py`](../../backend/src/model_provider/core/streaming.py) 主动关闭超限流 | 已实现 | Provider 未返回 usage 时只能估算，精度取决于 tokenizer |
| 单 Agent、Workflow、Team Lead 共用 Policy 接口 | [`policies.py`](../../backend/src/agent_runtime/strategies/policies.py) 实现三类 `SchedulerPolicy`；产品规则位于 [`AgentHubTeamLeadPolicy`](../../backend/src/app/services/runtime/policies.py) | 已实现 | Workflow 遍历器内部仍保留可变游标，尚未做到事件回放重建 |
| 版本化 Blackboard 与跨 Run Context CAS | [`VersionedBlackboard`](../../backend/src/agent_runtime/context/scope_store.py) 和 [`SQLContextStore`](../../backend/src/app/persistence/runtime_store.py) 检查期望版本 | 已实现 | ContextState 当前按 Conversation 建模，尚未抽象其他 Scope 类型 |
| 结构化长期 AgentMemory | `AgentLoopExecutor` 只提交摘要、完成任务和阻塞项；[`runtime_context_states`](../../backend/src/db/models/runtime.py) 持久化结构化值 | 已实现 | facts 与 output_refs 需要更多 Adapter 生产调用；不支持中途 PrivateContext 检查点 |
| 取消传播和迟到写隔离 | [`CancellationScope`、`RunLease`](../../backend/src/agent_runtime/runtime/cancellation.py) 与 [`adapter_isolation.py`](../../backend/src/agent_runtime/runtime/adapter_isolation.py) | 部分实现 | Model/Tool Adapter 尚未全部声明可终止边界；纯同步阻塞实现仍需子进程隔离 |
| 最小 EventEnvelope | 每个事件有稳定 ID、Run 内序号和因果字段，并可由 `RunHandle.events()` 观察 | 已实现 | `correlation_id`/`causation_id` 尚未覆盖所有 Adapter 事件 |
| 完整持久 Event Log 与至少一次投递 | AgentHub 仍把事件投影到 generation 读模型 | 未实现 | 没有通用事件表、重放游标、幂等消费者和失败队列 |
| Mailbox 与 Sink 背压隔离 | Mailbox 使用有界 `asyncio.Queue`；EventSink 失败不会改变 Kernel 终态 | 部分实现 | 满 Mailbox 会等待；慢 Sink 仍可能阻塞事件生产，失败只被吞掉而未进入隔离队列 |
| AgentHub Run 管理 | [`ConversationRunManager`](../../backend/src/app/services/conversation_run_manager.py) 每个 Conversation 最多一个活跃 Handle，排队后续输入，不缓存终止 Run | 已实现 | 队列仅在单进程内存中，进程丢失时未提交输入会丢失 |
| `generation_id == run_id` 与前端协议投影 | [`event_projection.py`](../../backend/src/app/services/runtime/event_projection.py) 保持现有 WebSocket/SSE 名称 | 已实现 | 前端尚未原生消费 EventEnvelope |
| Run 与 Context 数据真源 | [`runtime_runs`、`runtime_context_states`](../../backend/alembic/versions/d4e5f6a7b8c9_runtime_kernel_v1.py) 迁移并保存终态、限制、用量和版本 | 已实现 | generation 读模型仍与 Runtime 表双写，需继续校验一致性 |
| 进程丢失处理 | 应用启动时把遗留 Run 收敛为 `failed/process_lost` | 已实现 | V1 不恢复运行中的 Actor 或工具调用 |
| Kernel 不理解产品交付规则 | 全栈顺序、文档和部署启发式已迁入 AgentHub Policy | 部分实现 | [`AgentLoop`](../../backend/src/agent_runtime/runtime/agent_loop.py) 仍含产物生成、部署验证和应用特定提示逻辑 |
| 单一 Runtime 主链 | 旧 `Session`、轮询 Orchestrator、ActorOrchestrator、SchedulerAgent 和早期自持 Agent 已删除 | 已实现 | 应用中仍有多套用于前端投影的事件总线，尚未统一为持久 Event Log |

## 已移除入口

`Session`、`Orchestrator`、`ActorOrchestrator`、`SchedulerAgent`、旧 Watchdog 和旧 Scheduler 不再从 `agent_runtime` 导出，也没有兼容别名。请求中的 `runtime_mode` 以 HTTP 422 和 `runtime_mode_removed` 拒绝；策略只由聊天类型、`scheduling_strategy` 与 `workflow_enabled` 决定。

## 下一阶段

1. 持久化完整 Event Log，并实现至少一次投递、幂等消费与重放。
2. 为 Mailbox 和 EventSink 增加显式背压、慢订阅者隔离与失败队列。
3. 引入 Run 检查点和可恢复 PrivateContext；在此之前保持 `process_lost` 失败语义。
4. 继续将 AgentLoop 中的产物、全栈和部署逻辑迁往 AgentHub Policy/Tool。

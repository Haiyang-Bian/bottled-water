# Runtime 当前实现对照

> 快照日期：2026-08-13。本文只描述当前仓库中可由源码验证的状态，不构成产品能力声明。目标契约见[运行时不变量](./invariants.md)。

状态只使用以下四类：

- `已实现`：主干代码中存在对应机制，结论不依赖未来计划。
- `部分实现`：已有骨架或局部闭环，但尚未满足目标不变量。
- `未实现`：当前生产路径中没有对应机制。
- `遗留路径`：仍存在或被测试覆盖，但不应作为目标架构继续扩展。

## 对照表

| 目标契约 | 当前机制 | 状态 | 已知差距 |
| --- | --- | --- | --- |
| Session 统一生命周期边界 | [`Session`](../../backend/src/agent_runtime/runtime/session.py) 根据配置创建旧 `Orchestrator` 或 `ActorOrchestrator` | 部分实现 | 同一入口下存在两套生命周期和调度语义，尚未收敛为一条 Actor 主链 |
| Agent 是长期运行的 Actor | [`AgentActor`](../../backend/src/agent_runtime/runtime/agent_actor.py) 作为独立 `asyncio.Task` 运行 | 已实现 | 任务执行仍整体委托给大型 `AgentLoop`，Actor 自身的细粒度状态有限 |
| 控制通过有界 Mailbox 投递 | [`Mailbox`](../../backend/src/agent_runtime/runtime/mailbox.py) 使用有最大容量的 `asyncio.Queue` 并支持定向订阅 | 部分实现 | 满队列会等待，但没有超时、优先级或控制事件保留策略 |
| Agent 可在步骤间响应控制 | [`AgentStepper`](../../backend/src/agent_runtime/runtime/agent_stepper.py) 在事件、模型和工具边界检查控制消息 | 部分实现 | 属于协作式检查点，不是强抢占；阻塞 Provider 或 Tool 内部无法立即响应 |
| Scheduler 是可替换、无副作用的 Policy | [`SchedulerAgent`](../../backend/src/agent_runtime/strategies/scheduler_agent.py) 订阅报告并调用 `TechLeadScheduler` | 部分实现 | `SchedulerAgent` 继承 Actor 但绕开普通 `AgentLoop`；同时自行归档、投递控制并维护大量产品策略，不是纯提案接口 |
| Watchdog 对所有主路径执行硬限制 | 旧 [`Orchestrator`](../../backend/src/agent_runtime/runtime/orchestrator.py) 使用 [`Watchdog`](../../backend/src/agent_runtime/runtime/watchdog.py) 检查轮次、Token 和无进展 | 部分实现 | [`ActorOrchestrator`](../../backend/src/agent_runtime/runtime/actor_orchestrator.py) 只有会被活动重置的 idle deadline，没有完整 wall-clock、轮次、Token 和活锁限制 |
| EventEnvelope 具有顺序与幂等身份 | 当前 [`Event`](../../backend/src/agent_runtime/core/types.py) 包含 type、payload、source、target、channel、correlation_id 和 timestamp | 部分实现 | 缺少稳定 event_id、session_id、会话 sequence、causation_id 和明确重复投递语义 |
| 单一事件总线和故障隔离 | Runtime [`EventDispatcher`](../../backend/src/agent_runtime/runtime/event_dispatcher.py) 支持订阅、目标过滤和 Sink | 部分实现 | Sink 仍逐个等待；应用层还同时存在 [`AppEventBus`](../../backend/src/app/events/__init__.py) 和 [`services.realtime.EventBus`](../../backend/src/app/services/realtime/event_bus.py) |
| Blackboard 是版本化共享事实层 | [`BlackboardManager`](../../backend/src/agent_runtime/context/blackboard.py) 保存历史、摘要、KV 和递增版本 | 部分实现 | 版本只递增，没有 compare-and-swap；`add_summary`、`update_kv` 的生产调用不足，主要由测试覆盖 |
| 私有上下文隔离并可恢复 | [`AgentContext`](../../backend/src/agent_runtime/context/agent_ctx.py) 按 Agent/Conversation 管理帧；SQLAlchemy Adapter 支持保存 | 部分实现 | 保存调用仅出现在旧 Orchestrator；Actor 路径创建内存 ContextManager，但未形成加载、保存和恢复闭环 |
| 取消传播到所有外部执行 | Actor、Stepper 和 Session 存在 cancel/control 事件 | 部分实现 | 取消依赖下一检查点；Model/Tool Adapter 没有统一取消令牌、收敛期限和孤儿 Task 证明 |
| 外部依赖通过端口注入 | [`core/interfaces.py`](../../backend/src/agent_runtime/core/interfaces.py) 定义 Persistence、EventSink、Tool 和 Context Provider 接口 | 已实现 | 接口形状仍受现有应用适配约束，尚未形成独立版本化 Runtime API |
| Kernel 不理解具体产品任务 | Runtime 仍包含通用 Actor、调度、上下文和工具抽象 | 部分实现 | [`AgentLoop`](../../backend/src/agent_runtime/runtime/agent_loop.py) 含强制项目交付与产物逻辑；`SchedulerAgent` 含全栈、文档和部署启发式 |
| 只有一条 Agent 执行抽象 | [`runtime/agent.py`](../../backend/src/agent_runtime/runtime/agent.py) 保留早期自持 Agent，Actor 路径使用另一套 `AgentActor` | 遗留路径 | 早期 `Agent` 只被自身测试引用，内部仍标注 AgentLoop 真正步进化为未来工作 |
| 只有一条多 Agent 编排主链 | 旧轮询 Orchestrator、ActorOrchestrator 和独立 Workflow Engine 并存 | 遗留路径 | 兼容需求使状态、事件与持久化行为分散，后续不应继续向旧轮询路径增加语义 |

## 已确认的架构资产

当前代码不是空壳。以下资产可以作为后续收敛的起点：

- 纯 Python 的核心类型和外部依赖接口。
- AgentActor、Mailbox、EventDispatcher 和协作式控制检查点。
- Blackboard 与 Agent 私有帧的分层模型。
- LLM Scheduler 与确定性 fallback 的组合。
- 模拟 Model、Tool、Persistence 的 Runtime 单元和集成测试基础。

这些资产是否保留，应由目标不变量和新的失败测试决定，而不是因为已有代码量较大就默认继承。

## 下一阶段优先级

实现阶段应先修复系统不变量，再迁移产品能力：

1. 为 Actor 主链接入完整 Watchdog，并区分 wall-clock 与 idle timeout。
2. 定义有序、幂等、可关联的 EventEnvelope 和终态提交规则。
3. 统一取消传播和最大收敛时间。
4. 补齐 Actor 私有上下文与 Blackboard 的并发持久化闭环。
5. 抽取纯 `SchedulerPolicy`，再把全栈交付、产物和部署规则移出 Kernel。
6. 收敛事件总线与遗留 Orchestrator，避免继续双轨扩张。

每一步应先添加针对不变量的确定性测试，且只运行 Runtime 对应分组。

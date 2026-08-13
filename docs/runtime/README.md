# Runtime 设计孵化区

本目录定义 AgentHub Runtime 的架构契约并记录实现差距。它面向 Runtime 实现者和 Coding Agent，不是产品能力清单。当前仓库仍是孵化载体；待公共 API、依赖方向和恢复语义稳定后，再评估拆分为独立仓库。

## 阅读顺序

1. [目标架构](./architecture.md)：解释 `ContextScope + Run` 生命周期、状态所有权和分层边界。
2. [运行时不变量](./invariants.md)：规定实现不得破坏的硬约束。
3. [当前实现对照](./current-state.md)：逐项核对 V1 已实现能力与剩余差距。
4. [架构演化](./evolution.md)：通过 Git 历史说明设计来源，仅作为证据。

## 文档效力

- `architecture.md` 和 `invariants.md` 是规范；修改内核语义时必须同步更新。
- `current-state.md` 是源码快照；不能把路线图写成现有能力。
- `evolution.md` 只解释过去；历史类名和方案不构成兼容承诺。

V1 已提供 `RuntimeEngine`、`RunHandle`、`RunRequest`、`RunState`、`RuntimeLimits`、`ContextSnapshot`、`EventEnvelope`、`SchedulerPolicy`、`CancellationScope` 和 `RunLease`。公开导出以 [`agent_runtime/__init__.py`](../../backend/src/agent_runtime/__init__.py) 为准，不再提供旧 `Session` 或 Orchestrator 兼容入口。

## 当前阶段

Runtime Kernel V1 已收敛生命周期、Watchdog、Actor/Mailbox、策略和 ContextStore 主链。下一阶段集中处理完整 Event Log、有界背压、慢订阅者隔离、中途检查点和安全续跑，不扩张 AgentHub 平台功能。

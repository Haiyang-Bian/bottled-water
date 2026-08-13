# Runtime 设计与实现边界

本目录定义 AgentHub Runtime 的架构契约并记录实现差距。它面向 Runtime 实现者和 Coding Agent，不是产品能力清单。Runtime 长期保留在当前 monorepo；`agent_runtime` 通过公开 Port 与 AgentHub Adapter 集成，并由依赖边界测试阻止反向导入 `app` 或 `db`。

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

Runtime 已收敛生命周期、Watchdog、Actor/Mailbox、ContextStore 和持久 Event Log 主链，AgentHub 支持幂等投影与前端断线补拉。后续集中处理有界 Sink 背压、失败队列、日志压缩、跨进程实时广播、中途检查点和安全续跑，不扩张 AgentHub 平台功能。

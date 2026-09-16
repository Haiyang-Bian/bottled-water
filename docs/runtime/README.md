# Runtime 设计与实现边界

本目录定义 AgentHub Runtime 的架构契约并记录实现差距。它面向 Runtime 实现者和 Coding Agent，不是产品能力清单。Runtime 长期保留在当前 monorepo；`agent_runtime` 通过公开 Port 与 AgentHub Adapter 集成，并由依赖边界测试阻止反向导入 `app` 或 `db`。

从全项目视角，请先阅读[系统架构](../architecture/README.md)：Runtime Kernel 是内核，默认 AgentLoop、模型、上下文、工具、MCP、Skill 等是子系统；Web 和已实现的 CLI 是宿主，独立 eval 仍待实现。CLI 0.2.3 已有独立 wheel 和真实任务验收，但不意味着所有 Web 子系统已迁出。当前 `agent_runtime` 仍包含部分非 Kernel 模块，[模块目录](../architecture/subsystems.md)和[迁移说明](../architecture/migration.md)记录这些差距。

## 阅读顺序

1. [目标架构](./architecture.md)：解释 `ContextScope + Run` 生命周期、状态所有权和分层边界。
2. [运行时不变量](./invariants.md)：规定实现不得破坏的硬约束。
3. [平权协作语义](./collaboration.md)：定义团队消息、讨论线程、隐私和汇总边界。
4. [工作树与 Git 协作](./worktrees.md)：定义执行根隔离、工作树生命周期和安全合并边界。
5. [当前实现对照](./current-state.md)：逐项核对 V1 已实现能力与剩余差距。
6. [架构演化](./evolution.md)：通过 Git 历史说明设计来源，仅作为证据。

## 文档效力

- `architecture.md` 和 `invariants.md` 是规范；修改内核语义时必须同步更新。
- `../architecture/` 定义系统级职责与宿主边界；既有生命周期、Journal、隐私与工作树不变量在拆分中继续生效。
- `current-state.md` 是源码快照；不能把路线图写成现有能力。
- `evolution.md` 只解释过去；历史类名和方案不构成兼容承诺。

V1 已提供 `RuntimeEngine`、`RunHandle`、`RunRequest`、`RunState`、`RuntimeLimits`、`ContextSnapshot`、`EventEnvelope`、`SchedulerPolicy`、`CancellationScope` 和 `RunLease`。公开导出以 [`agent_runtime/__init__.py`](../../src/agent_runtime/__init__.py) 为准，不再提供旧 `Session` 或 Orchestrator 兼容入口。

## 当前阶段

Runtime 已收敛生命周期、Watchdog、Actor/Mailbox、ContextStore、持久 Event Log 和 Conversation 内的平权团队通信。AgentHub 支持幂等投影、前端断线补拉、实时用户插话、可审计团队动态，以及 Conversation 绑定仓库下的独立 Agent 工作树。有界 Sink 背压、跨进程实时广播、中途检查点和安全续跑仍属于后续阶段。

同一执行链已通过本地 CLI 的独立安装、跨目录操作、记忆/资源、取消与恢复验证，见[0.2.3 证据](../acceptance/native-user-experience-0.2.3.md)。剩余子系统迁移和独立 eval 需要另行确定实施范围；LPAC 暂缓，不因为保留实验接口就自动进入强隔离开发。归档后从[交接清单](../operations/archive-handoff-0.2.3.md)核对分支、安装与证据。

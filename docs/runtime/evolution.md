# Runtime 架构演化

> 本文通过 Git 历史解释设计如何形成。它不记录个人归属，不恢复已过时的长篇计划，也不把历史方案视为当前规范。当前目标以[目标架构](./architecture.md)和[运行时不变量](./invariants.md)为准。

## 演化主线

### 1. 从产品编排中分离 Runtime

提交 `963c43a`（`feat(agent_runtime): 实现多智能体运行时核心`）首次引入独立、纯 Python 的 `agent_runtime`：核心类型、Persistence/Event/Tool 接口、Blackboard、AgentContext、Scheduler、AgentLoop、Orchestrator 和 Watchdog 被放入同一运行时边界。

紧随其后的 `1bf48a8` 保存了第一版角色调度式多智能体设计。它提出 Session/Agent/Scheduler/Infrastructure 四层结构、共享 Blackboard 与私有上下文、LLM 建议权与 Watchdog 否决权。这些是当前重新提炼 Runtime 的主要历史依据，但原文中的计划和工期不再代表现状。

### 2. 修正操作系统类比的字面含义

提交 `f33dede` 在初版实现后立即将 AgentContext 从 LIFO “栈”改成按时间追加的帧列表，并删除 `pop` 语义。这一变化说明进程、线程、堆和栈是用于发现状态所有权的类比，而不是必须逐字复制的实现模型。

### 3. 从接口骨架走向可运行闭环

提交 `6f127b5` 集中补齐了流式输出、控制/观测事件分类、MCP 工具接入、Agent 自持实体、Token 滑动窗口、并行执行和 Watchdog 修复。它也暴露了第一版架构的真实缺口：上下文存在但未消费、状态报告可能是假的、所谓并行可能仍是串行、正常完成可能被误判为死锁。

这一阶段确立了一条长期原则：架构名称本身没有价值，只有进入主链、被状态机消费并由测试覆盖的机制才算实现。

### 4. 从同步轮询转向事件驱动 Actor

提交 `94ea4b6` 增加 `EventBus` 发布订阅、Mailbox、AgentStepper、AgentActor、SchedulerAgent 和 ActorOrchestrator，使工作 Agent 与调度员能够作为独立 `asyncio.Task` 运行。

提交 `88a589a` 随后补充 Actor 控制检查点，明确 pause、resume、cancel 和 complete 只能在 AgentLoop 暴露的协作式边界生效。历史收口说明也承认：当底层模型长时间没有增量时，这不是操作系统级抢占。

### 5. 比赛交付推动产品策略侵入内核

提交 `5898418` 为多 Agent 协调增加了计划、状态恢复、汇总、前后端进度展示和大量测试，同时让 `SchedulerAgent` 和 `AgentLoop` 快速承载具体交付规则。后续全栈依赖、文档产物、部署预览和“禁止假成功”等修复继续强化了产品闭环，但也模糊了 Kernel、Policy 与 Reference Application 的边界。

这不是否定交付修复的必要性，而是说明新 Runtime 不能把这些业务规则继续当成内核语义。

### 6. 当前文档替代历史设计稿

提交 `687b2af` 以当前项目文档替换了 V2 设计、异步改造计划、事件参考和收口说明。这样降低了过时文档冒充现状的风险，但也使设计动机和未兑现边界难以追溯。

本目录采用折中方式：不恢复旧长稿，只保留从 Git 可验证的演化结论，并重新建立面向未来实现的规范。

### 7. Runtime Kernel V1 收敛生命周期

提交 `ec70e8e` 与 `32a8c58` 引入 `ContextScope + Run`、唯一终态 CAS、最小 EventEnvelope、Watchdog、取消作用域和写租约。`97029d6` 将单 Agent、Workflow 与 Team Lead 收敛到同一 `SchedulerPolicy`；`3da5366` 把 AgentHub generation、数据库和前端事件投影接到 `RunHandle`。

提交 `cd54bf3` 删除旧 Session、轮询 Orchestrator、ActorOrchestrator、SchedulerAgent 和早期 Agent 入口；`99c98f8` 进一步让 Run 真正拥有 AgentActor/Mailbox，并补齐 Runtime shutdown 与流式预算关流。旧类名在本文前几节只用于描述历史，不再是当前接口。

## 历史材料查看方式

需要核对原文时，通过 Git 读取，不把快照复制回当前文档树：

```powershell
git show 1bf48a8:docs/architecture/multi-agent-v2-design.md
git show "94ea4b6:docs/多智能体运行时异步架构改造实施计划.md"
git show 88a589a:docs/runtime-async-closure-2026-06-05.md
```

关键提交均可使用以下方式查看完整说明和 diff：

```powershell
git show <commit>
```

## 从历史中保留的判断

- 操作系统类比用于划分生命周期、状态所有权和资源控制，不约束具体数据结构。
- LLM 适合语义提案，不适合作为权限、资源、存活和终态真源。
- 动态决策、外部执行和结果反馈之间的闭环必须由框架维护。
- 异步并发只有在事件顺序、取消、持久化和背压都有契约时才是运行时能力。
- 产品特例应进入 Policy、Tool 或参考应用，而不是继续扩大 Kernel。
- 历史代码是证据和实验材料，不是必须保留的兼容包袱。

# Runtime 设计孵化区

本目录用于重新定义 AgentHub 多智能体 Runtime 的长期架构。它服务于未来实现者和 Coding Agent，不是产品能力清单，也不表示目标设计已经落地。

当前仓库只是孵化载体。是否将 Runtime 迁移为独立项目，将在架构边界、授权方式和最小可验证内核稳定后决定。

## 阅读顺序

1. [目标架构](./architecture.md)：解释 Runtime 要解决的问题、核心模型和层次边界。
2. [运行时不变量](./invariants.md)：规定后续实现不可破坏的硬约束。
3. [当前实现对照](./current-state.md)：用现有代码核对目标契约，区分已实现、部分实现、未实现和遗留路径。
4. [架构演化](./evolution.md)：通过 Git 提交说明设计如何形成，仅作为历史证据。

## 文档效力

- `architecture.md` 描述**目标结构**，用于判断一个设计是否属于 Runtime 内核。
- `invariants.md` 描述**目标契约**，后续代码和测试应以它为验收依据。
- `current-state.md` 描述**当前快照**，结论必须能由现有源码验证。
- `evolution.md` 描述**历史来源**，历史方案不自动成为当前规范。

当目标文档与代码不一致时，不把目标写成现有能力；应先在 `current-state.md` 记录差距，再通过测试和增量实现收敛。这里出现的 `Session`、`AgentActor`、`SchedulerPolicy`、`EventEnvelope` 等名称是架构角色，不承诺存在同名稳定公共 API。

## 下一阶段入口

第一阶段只恢复设计主权，不修改 Runtime。进入实现阶段前，至少应从不变量中提取可失败的测试，覆盖终态唯一性、事件顺序、取消收敛、Watchdog、上下文隔离和并发写入。

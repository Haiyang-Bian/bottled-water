# Runtime 不变量

> 状态：目标契约。本文使用 RFC 风格关键词：`MUST` 表示不可违反，`SHOULD` 表示除非有记录充分的理由否则必须遵守，`MAY` 表示可选行为。

不变量先于具体类名和实现方式。后续重构若不能保持这些约束，应先修改设计并补充决策记录，而不是用兼容分支静默绕过。

## 生命周期与状态

1. 每个 `Session` **MUST** 拥有稳定且唯一的标识，并与其他 Session 的状态、事件和上下文隔离。
2. 每个 Session **MUST** 只收敛到一个终态：`completed`、`failed` 或 `cancelled`。
3. 终态提交后，Runtime **MUST NOT** 再接受会改变业务结果的控制事件；迟到事件只能被拒绝或作为审计记录保存。
4. 所有状态转换 **MUST** 经过 Kernel 定义的状态机；Policy、Model 和 Tool **MUST NOT** 直接改写状态。
5. Session 恢复后 **MUST** 能判断上一次执行已经终止、可以继续，还是需要显式失败，不得默认伪装为成功。

## 事件与因果关系

1. 每个 `EventEnvelope` **MUST** 包含稳定 `event_id`、`session_id`、会话级单调 `sequence`、`type`、`source`、`payload` 和时间戳。
2. 由其他事件触发的事件 **MUST** 记录 `correlation_id` 和 `causation_id`，使调度、工具调用和最终结果可以组成因果链。
3. Runtime **MUST** 只保证单个 Session 内的顺序，不声明跨 Session 的全局顺序。
4. 事件交付 **SHOULD** 按至少一次语义设计；消费者 **MUST** 使用 `event_id` 或业务幂等键处理重复投递。
5. 控制事件与观测事件 **MUST** 分离：控制事件请求状态转换，观测事件描述已经发生的事实。
6. Event Log **MUST** 只追加。修正历史状态应通过新事件表达，不得覆盖旧事件。

## 上下文与共享状态

1. `Blackboard` **MUST** 只保存允许团队共享的事实、成果和结构化状态，不得成为所有 Agent 私有过程的混合消息池。
2. Blackboard 写入 **MUST** 携带期望版本或等价并发令牌；冲突 **MUST** 显式失败或重试，不得静默覆盖。
3. `PrivateContext` **MUST** 由对应 Agent 独占。其他 Agent 和 Policy **MUST NOT** 直接读取其思考草稿、工具中间态或私有帧。
4. 需要共享的私有结果 **MUST** 先转化为明确的报告或 Blackboard 更新。
5. 持久化启用时，Blackboard 和 PrivateContext **MUST** 在定义的提交点一起形成可恢复边界；不能只恢复其中一半却报告 Session 可继续。
6. 上下文裁剪 **SHOULD** 依据 Token 或模型预算，并保留任务定义、关键工具结果和未完成因果链。

## 调度与硬控制

1. `SchedulerPolicy` **MUST** 将运行时快照映射为调度提案；它 **MUST NOT** 直接投递任务、持久化状态或修改 Actor。
2. 所有调度提案 **MUST** 经过 `Watchdog` 和权限校验后才能成为控制事件。
3. Policy 调用失败、超时或返回无效提案时，Kernel **MUST** 采用确定性失败或明确的降级策略。
4. Watchdog **MUST** 同时限制 wall-clock 总时间、idle 时间、调度轮次、Token/费用预算和连续无进展循环。
5. Agent 自报告 **MAY** 参与语义调度，但 **MUST NOT** 成为存活状态、资源占用或任务完成的唯一真源。

## 取消与协作式执行

1. 取消请求 **MUST** 具有稳定原因和关联标识，并传播到 AgentActor、ModelPort、ToolPort 及其后台 Task。
2. 不支持立即取消的 Adapter **MUST** 声明最迟检查点和最大收敛时间。
3. Actor **MUST** 在模型调用前后、工具调用前后和事件提交前后提供协作式检查点。
4. Runtime **MUST** 等待或强制处理遗留 Task 后再提交 `cancelled` 终态，不得留下仍可写入状态的孤儿任务。

## 背压与故障隔离

1. Mailbox **MUST** 有容量边界及明确的满队列策略；控制事件不得被静默丢弃。
2. 慢 `EventSink` **MUST NOT** 无限阻塞 Actor、Watchdog 或终态提交。
3. 一个 Sink 或订阅者失败 **MUST NOT** 阻止其他 Sink 接收事件；失败必须可观测。
4. 并行 Agent **MUST** 使用隔离的执行与错误收集；一个 Agent 失败不得抹去其他 Agent 已完成的事实。
5. 外部 Model、Tool 或 Persistence Adapter **MUST** 设置超时，并返回可分类的失败结果。

## 可观测与恢复

1. 调度提案、Watchdog 裁决、控制投递、Agent 报告、工具结果和终态 **MUST** 可通过事件关联。
2. Runtime **SHOULD** 能从持久化快照与其后的 Event Log 重建 Session 状态。
3. 面向用户的流式消息 **MAY** 是多个底层步骤的投影，但不得伪造底层未发生的成功。
4. Reference UI **MUST** 将后端终态作为运行状态真源，不得用本地计时器模拟完成。

## 实现准入规则

新实现进入主路径前，必须用确定性测试证明它保持相关不变量。真实模型测试只能补充验证 Adapter 兼容性，不能替代 Runtime 状态机和并发测试。

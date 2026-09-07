# 子系统与模块目录

> 本文定义目标职责，基于 2026-09-07 的源码核对。编号用于职责和迁移追踪；MVP 迁移现状见下表，其余描述仍是目标职责。总体依赖与状态规则见[系统架构](./README.md)，旧结构证据与验收见[迁移说明](./migration.md)。

模块按可维护的职责划分，不要求每行对应一个文件或一个发行包。每个子系统都应具备：通用输入/输出、明确依赖、资源关闭责任、可检查的失败状态和不依赖 Web 的契约测试。以下“迁出”指通用机制，“保留”指 AgentHub 业务适配；不能把一个现有大文件整体移动就视为完成。

## 本次迁移现状

| 层/子系统 | 已实现的公共位置 | 仍由原宿主或旧内部模块拥有的内容 |
| --- | --- | --- |
| 契约 | `src/agent_contracts`：执行上下文、grant、工具规格、授权/凭据/进程 Port、公共错误、ExecutionLimits/Observer、ContextBudget、ContinuationReader、RunCompletionPort | 既有 Run/Context 契约继续在 `agent_runtime/core`，不重复定义 |
| Kernel | `src/agent_runtime`：Run、Actor、Watchdog、Journal、取消、阶段期限、单次请求用量结算及原子完成协调 | 旧团队/Workflow 策略仍位于该包内部，未宣称全部纯化 |
| S1 执行 | `agent_subsystems/execution`：AgentLoop、AgentLoopExecutor、产品扩展接口 | Web 注入 `app/services/execution_extension.py`；旧 `services/agents/function_loop.py` 仍待后续收敛 |
| S2 调度 | `agent_subsystems/scheduling/single_agent.py` | 复杂团队、Workflow 策略待迁移 |
| S3 模型 | `src/model_provider`，SDK 延迟加载、usage 与流关闭 | UI Provider 目录在 `app/services/provider_catalog.py`，拥有者与加密字段在 Web |
| S4 上下文 | `agent_subsystems/context` 消费 ContextSnapshot、每次请求预算整理、失败/取消续接快照 | SQL/附件/知识库 contributors 待迁移 |
| S5 工具 | `agent_subsystems/tools` 注册表、执行器、schema/授权边界、同 scope 工具记录检索 | Web 工具 CRUD、Skill/MCP 组装仍由 Web 持有 |
| S6 工作空间 | `agent_subsystems/workspaces` 规范路径、分层忽略与源码发现；`agent_adapters/local` 文件、PowerShell、Git、Job Object | Web 工作树、常驻终端和产品文件树待迁移 |
| S7/S8/S9/S10/S11 | MCP、Skill、Workflow、内容、外部 Agent 均仅建立职责目录 | 原 Web 功能继续使用原实现 |
| S12 观测 | `agent_subsystems/observability` 脱敏；SQLite 保存事件、CLI 消费 | Web 审计/实时投影/业务统计仍属宿主 |
| 驱动 | `agent_adapters/storage` SQLite v2、事务完成、迁移/会话锁，`credentials` DPAPI/env，`local` 本机操作 | 不提供 AppContainer、受限 Token 或网络隔离 |
| H2 CLI | `src/agent_cli` 初始化、信任、草稿/会话选择、历史、Rich/纯文本/JSONL、doctor/replay | 独立 eval 宿主与高级多 Agent 治理待实现 |

## K. Runtime Kernel

**职责：** 管理运行的控制权。接收 `RunRequest`，通过 `RunHandle` 提供事件、结果、取消与快照。内核决定何时工作可以开始、何时必须停止、哪些状态可以提交，不决定任务应生成网页还是文档。

| 模块 | 功能与状态 | 当前来源 / 目标边界 |
| --- | --- | --- |
| K1 Engine 与 Handle | 创建 Run、维护活跃 Handle、关闭运行时；终止 Run 不永久缓存在 Engine | [`engine.py`](../../src/agent_runtime/runtime/engine.py)；只注入 Port |
| K2 Run 状态机 | 验证控制提案、管理状态转换、分配事件序号、唯一终态 CAS | `RunKernel`；保留单一终态权威 |
| K3 Actor 与 Mailbox | Run 内的 Agent 任务、控制消息与协作式执行 | [`agent_actor.py`](../../src/agent_runtime/runtime/agent_actor.py)、[`mailbox.py`](../../src/agent_runtime/runtime/mailbox.py)；不承载业务 AgentLoop |
| K4 Watchdog 与预算 | wall/idle/决策数/Token/无进展限制；验证用量并收敛失败 | [`run_watchdog.py`](../../src/agent_runtime/runtime/run_watchdog.py)；不把文字输出等同于有效进展 |
| K5 取消与写租约 | 传播取消、隔离迟到结果、防止终态后写 Context/输出 | [`cancellation.py`](../../src/agent_runtime/runtime/cancellation.py)、[`adapter_isolation.py`](../../src/agent_runtime/runtime/adapter_isolation.py)；不能强制抢占任意同步代码 |
| K6 Scope 提交 | 加载快照、协调消息/Blackboard/AgentMemory 增量的版本 CAS | [`scope_store.py`](../../src/agent_runtime/context/scope_store.py)与 `ContextStore`；不查询 Conversation |
| K7 团队通信控制 | 校验成员、收件人、线程与团队预算；处理消费和中断 | [`team_collaboration.py`](../../src/agent_runtime/runtime/team_collaboration.py)、`TeamMessenger` / `TeamJournal`；调度偏好归 S2 |
| K8 事件提交 | 先 Journal 后通知、终态事件原子提交、稳定 ID 和失败原因 | [`run_journal.py`](../../src/agent_runtime/runtime/run_journal.py)与 Journal Port；SQL 实现不进入内核 |

**约束：** Policy、Executor 和宿主都不能直接修改 Run 终态。预算耗尽、超时、上下文冲突和进程丢失不能伪装成成功。精确语义沿用 [Runtime 不变量](../runtime/invariants.md)。Kernel 对 SDK 异常的依赖应收敛到通用错误契约。

## S1. 智能体执行子系统

**职责：** 实现 `AgentExecutor`，完成一次 Agent 工作中的“构建上下文—调用模型—调用工具—报告结果”循环。它是可替换的执行环境，不属于 Run 控制内核。

| 模块 | 功能 / 输入输出 | 当前来源与拆分要求 |
| --- | --- | --- |
| S1.1 执行适配器 | `AgentExecutionRequest` → 输出、报告、usage、记忆增量；执行检查点校验取消与租约 | [`agent_executor.py`](../../src/agent_subsystems/execution/agent_executor.py)；保留 `AgentExecutor` 契约 |
| S1.2 默认 AgentLoop | 模型/工具轮次、流式响应、工具结果回填、终止条件 | [`agent_loop.py`](../../src/agent_subsystems/execution/agent_loop.py)；拆出通用循环，产品交付启发式交 H1 |
| S1.3 调用帧与消息转换 | 消息、工具 schema、工具调用/响应、模型流片段的归一化 | [`core/types.py`](../../src/agent_runtime/core/types.py)、[`services/agents`](../../backend/src/app/services/agents)；避免保留两套通用 loop |
| S1.4 状态报告与进展 | 将可观察工作结果转换为结构化 report 和 usage；不从措辞推定工具成功 | [`status_report.py`](../../src/agent_runtime/runtime/status_report.py)；业务交付校验为可选扩展 |
| S1.5 执行上下文桥接 | 显式使用 Scope 历史、AgentMemory、Blackboard、当前输入与 inbox | 当前 `AgentLoopExecutor` 主要传入 Blackboard 和 metadata；必须补足通用历史消费路径 |

**依赖：** S3 模型接口、S4 上下文接口、S5 工具调用接口及 Kernel 执行契约。只持有当次调用帧，不自行保存长期私有推理。嵌套模型/工具调用的用量和取消必须回到同一预算约束。

**留在宿主：** “先前端后后端”、HTML/Office 交付选择、部署验证提示、聊天默认 Agent 等产品偏好。上下文构建失败的回退应可观察；不能依赖宿主补历史而声称独立执行已经完整。

## S2. 调度策略与协作子系统

**职责：** 根据不可变快照建议下一步工作，组织团队沟通和汇总；不执行 Kernel 控制操作。

| 模块 | 功能 / 输入输出 | 当前来源与拆分要求 |
| --- | --- | --- |
| S2.1 单 Agent 策略 | 从初始输入和执行状态产生执行或完成提案 | [`single_agent.py`](../../src/agent_subsystems/scheduling/single_agent.py)，失败/阻塞提出失败终态 |
| S2.2 Team Lead 策略 | 选择成员、生成子任务、等待报告与汇总 | 同上；通用调度与 AgentHub 交付规则分离 |
| S2.3 平权协作策略 | 根据 inbox、开放线程和成员进展建议调度 | [`collaborative.py`](../../src/agent_runtime/strategies/collaborative.py)；角色名不授予资源控制权 |
| S2.4 Workflow 策略桥 | 把就绪节点转换为调度提案，将执行结果交回 S9 | [`strategies/workflow.py`](../../src/agent_runtime/strategies/workflow.py)及 `policies.py`；不另建父 Run 状态机 |
| S2.5 团队工具桥 | send/broadcast/reply/resolve 等动作转成 `TeamMessenger` 调用 | [`team_tools.py`](../../src/agent_runtime/runtime/team_tools.py)；权限与消息持久化仍由 Kernel 控制 |

**状态与错误：** 策略状态应可由受控快照表达；当前 Workflow 游标不能据此宣称可重放恢复。非法目标、越权提案和策略异常交 Kernel 统一处理。`summary_agent_id` 是汇总目标，不是特权身份。

**留在宿主：** [`app/services/runtime/policies.py`](../../backend/src/app/services/runtime/policies.py) 中的产品规划与交付扩展；UI 任务标题、默认成员、发布汇总的业务选择。团队协议见[协作语义](../runtime/collaboration.md)。

## S3. 模型访问子系统

**职责：** 为调用方提供统一模型能力、流式消息、工具调用和用量反馈，隔离 Provider 差异。

| 模块 | 功能 / 输入输出 | 当前来源与拆分要求 |
| --- | --- | --- |
| S3.1 模型规格与接口 | 消息、工具 schema、模型能力、请求限制和结果类型 | [`model_provider/core`](../../src/model_provider/core)；延续已有接口，避免平行再造 |
| S3.2 Provider 工厂 | 根据通用配置选择驱动，报告能力和缺失依赖 | [`factory.py`](../../src/model_provider/factory.py)；SDK 按需加载，前端表单元数据另行适配 |
| S3.3 流与用量 | 合并文本/tool call 片段、usage 归一化、输出超限关流 | [`streaming.py`](../../src/model_provider/core/streaming.py)；估算值明确标记 |
| S3.4 Provider 驱动 | Ark、OpenAI-compatible、DeepSeek 的请求、流、错误转换 | [`providers`](../../src/model_provider/providers)；外部异常转为可分类领域错误 |
| S3.5 配置与凭据桥 | 宿主配置和凭据引用 → 通用模型规格与可调用实例 | [`model_config_resolver.py`](../../backend/src/app/services/model_config_resolver.py)；DB 查询/解密留宿主适配器 |

**状态与资源：** 请求帧属于调用，连接池属于驱动/宿主生命周期。重试不得超过剩余时间与预算；认证失败、超限、限流、取消、缺失能力和 mock 结果必须可区分。

**留在宿主：** Provider 所有者、模型管理 API、加密字段、用户默认模型、UI 目录；[`services/llm`](../../backend/src/app/services/llm) 内与产品相关的兼容网关和 HTML 规则。Skill 和 Agent 使用相同模型接口，不应固定调用某个宿主厂商单例。

## S4. 上下文、记忆与检索子系统

**职责：** 将已授权的输入来源组成预算内的模型上下文，并产生结构化记忆候选。持久提交权仍在 Kernel/ContextStore。

| 模块 | 功能 / 输入输出 | 当前来源与拆分要求 |
| --- | --- | --- |
| S4.1 上下文装配器 | Scope 快照、Agent 规格、任务、来源片段 → 消息列表和来源信息 | [`context/builder.py`](../../backend/src/app/services/context/builder.py)；去除 Session 与 Conversation 参数 |
| S4.2 预算与压缩 | Token 估计、优先级、裁剪、片段拼装、预留输出空间 | [`compression.py`](../../backend/src/app/services/context/compression.py)；纯算法优先抽取 |
| S4.3 消息与记忆映射 | Scope history、Blackboard、AgentMemory → Agent 可见上下文；生成有类型的增量 | [`agent_runtime/context`](../../src/agent_runtime/context)、[`context/memory.py`](../../backend/src/app/services/context/memory.py)；不得隐式跨 scope 取数据 |
| S4.4 Context contributors | 附件、工作空间、任务、运行态、变量、团队成员等来源适配 | [`services/context`](../../backend/src/app/services/context)；通用 contributor 接口迁出，DB 来源留 H1 |
| S4.5 检索与索引 | 分块、检索接口、打分、来源引用，按授权返回片段 | [`knowledge.py`](../../backend/src/app/services/knowledge.py)；当前为词项/相似度方案，不能标作已实现向量语义检索 |

**状态与错误：** 检索索引独立于 ContextStore；知识库权限和文档归属由宿主控制。缺失来源、截断和上下文构建失败要有可诊断状态。临时工具帧、原始推理和密钥不进入长期记忆。

**留在宿主：** 群成员介绍、Conversation 历史查询、工作区产品资料、附件访问授权、任务/画布数据读取。CLI 使用本地 Scope 与显式文件来源，不造一个假的 Conversation 数据库来复用构建器。

## S5. 工具与能力授权子系统

**职责：** 将“允许什么、存在什么、如何执行、如何记录”收敛成统一工具调用入口。它管理执行能力，不管理登录和用户角色。

| 模块 | 功能 / 输入输出 | 当前来源与拆分要求 |
| --- | --- | --- |
| S5.1 工具目录与注册表 | 通用 `ToolSpec`、名称/别名、schema、能力需求、handler 注册 | [`agent_runtime/tools`](../../src/agent_subsystems/tools)、[`services/tools/registry.py`](../../backend/src/app/services/tools/registry.py)、[`catalog.py`](../../backend/src/app/services/tools/catalog.py)；目录数据与 DB CRUD 分开 |
| S5.2 Schema 与参数处理 | 校验模型参数、规范化别名、拒绝未声明输入 | [`schema.py`](../../backend/src/app/services/tools/schema.py)；可信授权信息走独立参数 |
| S5.3 授权执行 | 校验有效 grants、工具可见性、嵌套调用和资源范围 | [`permissions.py`](../../backend/src/app/services/tools/permissions.py)、[`agents/permission_guard.py`](../../backend/src/app/services/agents/permission_guard.py)；用户 RBAC → grants 由宿主转换 |
| S5.4 调用调度 | 统一 sync/async handler 调用、取消、deadline、调用 ID、结果归一化 | [`executor.py`](../../backend/src/app/services/tools/executor.py)；移除 `db, user` 入口 |
| S5.5 调用记录 | started/result/error/denied/cancelled 事实与时长、证据引用 | [`runs.py`](../../backend/src/app/services/tools/runs.py)；经 Record Port 写入，不在通用执行器创建 ORM |
| S5.6 扩展注册 | 基础工具、自定义工具、Skill、MCP、外部 Agent 的注册包装 | [`builtins`](../../backend/src/app/services/tools/builtins)、[`custom.py`](../../backend/src/app/services/tools/custom.py)；实现由宿主连接，禁止实现导入环 |

**授权规则：** 宿主发放有效授权，子系统在实际执行点落实；Agent 或模型只能使用、不能扩展授权。显式空权限保持为空。目标入口应把拒绝表达成可记录的失败，不能只输出 warning 后继续。当前 `check_user_tool_permissions(..., strict=False)` 存在保留旧行为的告警路径，不能描述为所有入口均已强制拒绝；迁移时需单独核对各调用路径。

**留在宿主：** 工具创建/编辑 API、用户拥有的自定义定义、RBAC 查询、会话归属、工具面板。通用 handler 返回真实结果及资源引用，产物卡片由 H1 投影。

## S6. 工作空间、文件与进程子系统

**职责：** 管理智能体可访问的本机资源，并给工具提供受限、可关闭、可观察的执行能力。它与 S5 分工：S5 决定调用是否被授权，S6 落实具体资源边界。

| 模块 | 功能 / 输入输出 | 当前来源与拆分要求 |
| --- | --- | --- |
| S6.1 执行根与路径 | 通用 scope/agent → 可信根；规范化路径、限制逃逸 | [`execution_roots.py`](../../backend/src/app/services/execution_roots.py)、[`tools/execution_root.py`](../../backend/src/app/services/tools/execution_root.py)、[`workspaces/filesystem.py`](../../backend/src/app/services/workspaces/filesystem.py)；DB 绑定与通用路径机制分离 |
| S6.2 文件资源 | 列举、读写、移动、复制、删除、目录与文件引用 | [`services/files`](../../backend/src/app/services/files)、[`builtins/file`](../../backend/src/app/services/tools/builtins/file)；上传/下载 URL 与产品节点模型留宿主 |
| S6.3 单次进程 | argv/cwd/env/stdin/timeout → exit code/stdout/stderr/截断信息 | [`sandbox/runner.py`](../../backend/src/app/services/tools/builtins/sandbox/runner.py)与 `policy.py`；明确进程树终止能力 |
| S6.4 交互终端 | start/send/wait_for/snapshot/stop，输出缓冲、退出状态与活句柄 | [`terminal/executor.py`](../../backend/src/app/services/tools/builtins/terminal/executor.py)；将 TerminalManager/Process 与 SandboxSession/User 拆开 |
| S6.5 仓库与工作树 | 仓库探测、基准提交、managed/adopted 工作树、生命周期 | [`worktrees.py`](../../backend/src/app/services/worktrees.py)；Conversation 绑定和归档策略留宿主 |
| S6.6 Git 协作 | status/diff/commit/integrate、安全前置检查与冲突回传 | [`git_collaboration.py`](../../backend/src/app/services/tools/git_collaboration.py)；不引入隐式 push 或历史改写 |
| S6.7 隔离与运行环境 | 声明本机/子进程/容器可用能力，解析依赖、执行取消与资源清理 | 当前 sandbox、terminal 与外部进程机制分散；统一契约不等于已实现生产沙箱 |

**状态与错误：** 活进程只能由持有驱动管理。路径越界、权限拒绝、命令缺失、超时、输出截断和取消需要区分；跨进程重启不能从旧记录恢复活句柄。用户文件和工作树不随 Run 终止自动删除。

**留在宿主：** workspace/project 树、仓库成员绑定、文件拥有者、附件关系、远程连接配置、发布预览的服务进程策略。强隔离驱动为后续能力；当前可信根和命令限制的范围见[工作树契约](../runtime/worktrees.md)。

## S7. MCP 子系统

**职责：** 将 MCP 服务配置转为可发现、可调用的外部能力，统一传输资源的生命周期与失败表达。

| 模块 | 功能 / 输入输出 | 当前来源与拆分要求 |
| --- | --- | --- |
| S7.1 规格与发现 | server spec、工具列表、schema、能力探测 | [`mcp/schema.py`](../../backend/src/app/services/mcp/schema.py)、[`discovery.py`](../../backend/src/app/services/mcp/discovery.py)；存储配置转通用值对象 |
| S7.2 调用服务 | 工具选择、allowlist、调用 ID、超时与结果 | [`invocation.py`](../../backend/src/app/services/mcp/invocation.py)；不再接收 ORM server/invocation/user |
| S7.3 HTTP/事件传输 | 请求、响应、流和连接错误转换 | [`transports/http.py`](../../backend/src/app/services/mcp/transports/http.py)、[`sse_ws.py`](../../backend/src/app/services/mcp/transports/sse_ws.py)；协议覆盖须由契约测试证明 |
| S7.4 stdio 传输 | 服务进程、环境白名单、stdio 消息与退出清理 | [`transports/stdio.py`](../../backend/src/app/services/mcp/transports/stdio.py)、[`common.py`](../../backend/src/app/services/mcp/transports/common.py)；复用受控进程接口 |
| S7.5 连接与记录 | 连接建立/关闭、取消、不支持能力与调用记录 | 当前分散在 invocation/transports；连接复用是待完善设计，不预设已支持完整持久会话 |

**依赖：** S5 提供授权后的调用入口，S6 提供进程边界，存储与凭据通过 Port。发现到一个工具不代表它获得执行权限。服务不可达、握手或 schema 失败均保留明确结果，不能转成伪造成功。

**留在宿主：** MCP 管理 API、所有者、注册数据库、审计页面、UI 可用性提示和配置解密。

## S8. Skill 与扩展包子系统

**职责：** 将 Skill 作为有版本、依赖、入口和权限需求的可安装能力单元，支持组合执行与测试。

| 模块 | 功能 / 输入输出 | 当前来源与拆分要求 |
| --- | --- | --- |
| S8.1 Manifest | 解析、规范化与验证包描述、入口和能力需求 | [`skills/manifest.py`](../../backend/src/app/services/skills/manifest.py)、[`adapters/legacy.py`](../../backend/src/app/services/skills/adapters/legacy.py)；旧格式兼容局限在入口 |
| S8.2 包与资源 | 读取包、校验资源路径、安装/卸载、文件引用 | [`package.py`](../../backend/src/app/services/skills/package.py)；通用解析与用户拥有的安装记录分开 |
| S8.3 版本与依赖 | 版本选择、依赖探测、环境能力与锁定信息 | [`versions.py`](../../backend/src/app/services/skills/versions.py)、[`dependencies.py`](../../backend/src/app/services/skills/dependencies.py)；可复现安装和隔离需要另行验收 |
| S8.4 Skill Runtime | spec/input/context → 标准结果；分派 runner、记录运行事实 | [`runtime.py`](../../backend/src/app/services/skills/runtime.py)、[`execution.py`](../../backend/src/app/services/skills/execution.py)；去除 SkillRun ORM 创建 |
| S8.5 Runners | prompt 使用 S3；agent 使用 S1/受控运行入口；mcp 使用 S7；script 使用 S5/S6 | [`runners`](../../backend/src/app/services/skills/runners)；共享调用预算与授权，禁止硬接宿主工具单例 |
| S8.6 测试与上下文 | 固定输入、能力约束、结果断言、Skill 上下文贡献 | [`testing.py`](../../backend/src/app/services/skills/testing.py)、[`context.py`](../../backend/src/app/services/skills/context.py)；实测与 mock/fallback 分开 |

**状态与错误：** 包安装状态与执行状态分离。依赖缺失、权限不足、入口错误、执行失败和显式降级必须保留。当前 Runtime 会返回带 `fallback:*`、`mock-skill-execution` 的结果；评测不能把它计为真实完成，也不能在迁移时丢掉这些标记。

**留在宿主：** Skill 创建/编辑/发布 API、用户目录、产品测试面板与所有权记录。Agent runner 是调用已注入的执行能力；不能藏一套不受父 Run 控制的调度内核。

## S9. Workflow 子系统

**职责：** 表达、验证和执行显式任务图，为 Kernel/S2 提供就绪工作及节点结果。产品画布是图的编辑器，不是底层图模型。

| 模块 | 功能 / 输入输出 | 当前来源与拆分要求 |
| --- | --- | --- |
| S9.1 图与定义 | 节点、边、输入输出、变量、版本化定义 | [`agent_runtime/workflow`](../../src/agent_runtime/workflow)、[`workflows/definition.py`](../../backend/src/app/services/workflows/definition.py)、[`graph.py`](../../backend/src/app/services/workflows/graph.py)；统一通用图，画布映射留宿主 |
| S9.2 静态校验 | 节点类型、连接、引用、循环限制和可执行性 | [`validator.py`](../../backend/src/app/services/workflows/validator.py)、`conditions.py`；拒绝无效图 |
| S9.3 调度与状态 | 就绪节点、依赖、分支/循环、节点结果、重规划输入 | [`scheduler.py`](../../backend/src/app/services/workflows/scheduler.py)、[`engine.py`](../../backend/src/app/services/workflows/engine.py)及 runtime workflow 模块；不并存独立父 Run 终态机制 |
| S9.4 节点适配器 | start/end、agent、tool、skill、mcp、condition、loop、artifact | [`nodes`](../../backend/src/app/services/workflows/nodes)；只调用对应公开能力接口 |
| S9.5 I/O 与记录 | 输入解析、结果绑定、节点事件、持久快照 Port | [`io.py`](../../backend/src/app/services/workflows/io.py)、[`events.py`](../../backend/src/app/services/workflows/events.py)、[`runtime.py`](../../backend/src/app/services/workflows/runtime.py)；DB 锁和模型更新迁到宿主适配器 |
| S9.6 计划生成 | 用户目标与能力 → 候选图，随后走相同校验 | [`planner.py`](../../backend/src/app/services/workflows/planner.py)、[`planning.py`](../../backend/src/app/services/workflows/planning.py)；产品规划偏好为扩展 |

**状态与错误：** 节点失败、跳过、阻塞和运行终态不是同一个状态。图层不能吞掉子系统失败，也不能因为一个节点返回文字就标记整个 Run 成功。持久节点状态不等于中途 crash resume；现有可变游标仍是重放重建的差距。

**留在宿主：** Conversation 的 `workflow_enabled`、画布存取、Agent ID 映射、WorkflowRun ORM、轮询 API 与 UI 进度。S9 图执行与 Kernel Policy 的接合应收敛现有多处机制，迁移时更新画布调用方并验证已有功能。

## S10. 内容、文档与产物子系统

**职责：** 生成和转换可复用内容。产物的业务归属、卡片、版本发布和 URL 属于宿主。

| 模块 | 功能 / 输入输出 | 当前来源与拆分要求 |
| --- | --- | --- |
| S10.1 内容模型 | 文档结构、段落/表格/列表、模板数据与规范化 | [`document_model/schema.py`](../../backend/src/app/services/document_model/schema.py)、[`markdown.py`](../../backend/src/app/services/document_model/markdown.py)；通用结构与产品文案/模板选择分离 |
| S10.2 渲染器 | 文档模型 → HTML/预览、PDF、DOCX 等内容 | [`document_model`](../../backend/src/app/services/document_model)、[`artifact/renderers.py`](../../backend/src/app/services/tools/builtins/artifact/renderers.py)；渲染依赖按格式可选 |
| S10.3 提取与转换 | 文件内容 → 文本/结构/预览，返回转换限制与失败 | [`file/extractors.py`](../../backend/src/app/services/tools/builtins/file/extractors.py)、[`converters.py`](../../backend/src/app/services/tools/builtins/file/converters.py)、[`files/previewers`](../../backend/src/app/services/files/previewers) |
| S10.4 内容写入与引用 | 生成字节/文件 → 通用资源引用、媒体类型、摘要 | [`artifact/storage.py`](../../backend/src/app/services/tools/builtins/artifact/storage.py)中的生成部分；文件写入通过 S6，Blob/文件存储经 Port |
| S10.5 导出与工具包装 | 内容导出、格式选择、工具结果证据 | [`artifact/export.py`](../../backend/src/app/services/tools/builtins/artifact/export.py)、[`artifact_exports.py`](../../backend/src/app/services/artifact_exports.py)；产品下载链接单独映射 |

**状态与错误：** 输出引用对应实际生成的内容；转换工具缺失或格式能力不足需明确报告。真实文档内容、文件路径、产品 Artifact ID 和公开 URL 是不同对象，不应混成一个固定 Web payload。

**留在宿主：** [`artifacts.py`](../../backend/src/app/services/artifacts.py)、[`deployments.py`](../../backend/src/app/services/deployments.py) 中的所有者、版本历史、差异展示、访问授权、部署记录、预览 URL 和发布流程。通用内容层不启动部署，也不承诺所有 Office 特性一致。

## S11. 外部 Agent 与环境集成子系统

**职责：** 将外部编码 Agent 的启动、观察、状态和取消转换成系统内可验证的能力；它们是执行适配器，不是 Kernel 的同义词。

| 模块 | 功能 / 输入输出 | 当前来源与拆分要求 |
| --- | --- | --- |
| S11.1 外部 Agent 契约 | provider、输入、执行根、限制 → probe/run/event/result | [`external_agents/base.py`](../../backend/src/app/services/external_agents/base.py)；移除接口中的 User、ExternalAgentRun、db |
| S11.2 注册与驱动 | Codex、Claude Code、OpenCode 等 CLI 参数与能力适配 | [`registry.py`](../../backend/src/app/services/external_agents/registry.py)、[`adapters`](../../backend/src/app/services/external_agents/adapters)；不把厂商特例写进 Kernel |
| S11.3 进程与会话 | 进程启动、输出、运行状态、取消与清理 | [`process_manager.py`](../../backend/src/app/services/external_agents/process_manager.py)、[`workspace.py`](../../backend/src/app/services/external_agents/workspace.py)；通过 S6 使用可信资源 |
| S11.4 事件桥与工具包装 | 外部状态 → 标准结果/事件，注册统一 invoke 工具 | [`events.py`](../../backend/src/app/services/external_agents/events.py)、[`builtins/external_agent.py`](../../backend/src/app/services/tools/builtins/external_agent.py)；不把外部文字直接当工具成功 |
| S11.5 环境探测扩展 | API/浏览器可用性探测、环境诊断的独立 handler | [`api_probe.py`](../../backend/src/app/services/tools/api_probe.py)、[`browser_probe.py`](../../backend/src/app/services/tools/browser_probe.py)；归 S5 注册，不要求每次启动都加载 |

**状态与错误：** 运行记录与活会话分离，工具可用、进程成功启动、任务成功完成是三个阶段。外部 Agent 持久团队连接尚未实现；未来接入 `TeamMessenger` 也不得放宽执行根与隐私约束。

**留在宿主：** 外部 Agent 管理 API、用户运行记录、远程配置和状态面板。当前外部 CLI 的非交互权限默认值是 AgentHub 兼容策略；新宿主必须显式选择授权策略，不能将跳过交互权限提示当作系统通用安全保证。

## S12. 记录、持久化与可观测性子系统

**职责：** 实现或组合记录存储与消费能力，让运行可检查、可重放、可评测。Kernel 定义的 Journal 原子性是前提；业务投影由宿主负责。

| 模块 | 功能 / 输入输出 | 当前来源与拆分要求 |
| --- | --- | --- |
| S12.1 Journal 驱动 | Run/Event/Team 的创建、追加、终态 CAS、游标查询 | [`app/persistence`](../../backend/src/app/persistence)与内存 Journal；SQL 产品模型适配留宿主，独立本地驱动待实现 |
| S12.2 Context 存储驱动 | Scope 加载、版本 CAS、结构化状态 | [`runtime_store.py`](../../backend/src/app/persistence/runtime_store.py)、内存 ContextStore；通用 scope 不应强制 Conversation 外键 |
| S12.3 消费与投递 | 游标、幂等、补拉、订阅隔离、失败处理 | [`conversation_run_manager.py`](../../backend/src/app/services/conversation_run_manager.py)、[`realtime`](../../backend/src/app/services/realtime)；可复用消费机制与 Web 投影拆开 |
| S12.4 脱敏与审计 | 事件/调用 payload 脱敏、审计 Port、结果导出 | [`run_journal.py`](../../src/agent_runtime/runtime/run_journal.py)、[`audit.py`](../../backend/src/app/services/audit.py)、[`output_filter.py`](../../backend/src/app/services/output_filter.py)；产品审计查询留宿主 |
| S12.5 日志与用量 | 结构化日志、关联 ID、latency、usage、估算标记 | [`common/logger.py`](../../backend/src/common/logger.py)、generation 记录与模型 usage；由宿主配置输出目标 |
| S12.6 Eval 证据导出 | 配置版本、代码版本、任务输入、事件引用、结果、失败与环境能力 | 独立 eval 宿主待实现；不得把 mock 结果混入真实模型结果 |

**状态与错误：** Journal 保存失败必须显式处理；实时 Sink 失败不改写已经提交的 Run 事实。当前慢 Sink 仍可能阻塞生产、输入队列与终端活句柄仍依赖单进程，不能宣称分布式可靠调度已完成。

**留在宿主：** generation/message/read model、Web consumer cursor、用户审计权限、加密字段配置、SSE/WS/HTTP 传输与公开 URL。通用记录默认持久化前脱敏；日志文件不得保存原始思考和密钥。

## D. 驱动与适配器的放置规则

驱动是上面各子系统的实现插槽，不是第十三个全能业务子系统。

| 驱动族 | 提供的能力 | 配置与关闭者 |
| --- | --- | --- |
| 模型与协议 | Provider SDK、HTTP、MCP stdio/流 | 宿主创建；所属子系统管理连接使用，宿主关闭共享资源 |
| 本机资源 | 文件系统、进程树、终端、Git、可选容器 | 宿主提供根与授权；资源子系统负责句柄和取消 |
| 通用存储 | 内存测试驱动、计划中的本地持久 Scope/Journal/内容存储 | 宿主选择；遵守各自 CAS、原子性和迁移版本 |
| AgentHub 数据 | SQLAlchemy、User/Conversation、加密内容、API DTO 转换 | 放在 `app`/`db` 边界，不能成为通用库隐式依赖 |
| 凭据与运行配置 | 环境或凭据库解析、仅在调用末端注入凭据 | 宿主负责来源和权限；不经模型文本传递 |
| 内容与外部工具 | Office/PDF/OCR、外部编码 CLI、浏览器依赖 | 按能力启用；缺失时仅禁用对应能力 |

每个驱动必须声明同步/异步、可取消程度、流式支持、输出上限和重试边界。接口可调用不代表可以安全终止，记录持久不代表进程可恢复。

## H. 宿主与产品模块

### H1. AgentHub Web 宿主

| 模块 | 留在应用中的功能 | 当前位置 |
| --- | --- | --- |
| H1.1 组合与启动 | 配置加载、数据库初始化、构建通用 spec、连接各子系统、关闭资源 | [`app/main.py`](../../backend/src/app/main.py)、[`runtime_service.py`](../../backend/src/app/services/runtime_service.py)、`app/core` |
| H1.2 身份与权限来源 | 注册/登录、RBAC、管理员初始化、单用户桌面身份、凭据所有权 | `app/core/security.py`、`services/access_control.py`、`audit.py`、`admin_bootstrap.py`、`desktop_identity.py`、`db/models/users.py` / `security.py` |
| H1.3 工作区与业务资源 | Workspace/Project、成员、模板、快捷方式、文件/知识库拥有者、仓库绑定 | `api/workspaces.py`、`api/repositories.py`、`services/files` 的产品部分、`db/models/workspaces.py` / `repositories.py` / `files.py` |
| H1.4 会话与 Agent 产品 | Conversation、参与者、默认 Agent、消息编辑/重试、输入排队、工具与模型配置 | `api/conversations.py`、`api/agents.py`、`services/chat`、`conversation_identity.py`、`conversation_run_manager.py` |
| H1.5 运行投影 | Run 事件 → generation、消息、任务进度与团队动态；鉴权补拉 | [`runtime/event_projection.py`](../../backend/src/app/services/runtime/event_projection.py)、[`generation_records.py`](../../backend/src/app/services/runtime/generation_records.py)、`api/runtime_events.py` |
| H1.6 任务与画布 | 产品 Task/Subtask、规划展示、Workflow 画布存取、节点配置和轮询 | `services/tasks`、`services/workflows` 的产品部分、`db/models/tasks.py` / `workflows.py` |
| H1.7 产物与发布 | Artifact 归属/版本/差异/下载、部署预览、回滚、健康检查和 URL | `services/artifacts.py`、`artifact_exports.py` 的产品部分、`deployments.py`、`backend_process_manager.py` |
| H1.8 能力管理 | 模型/工具/Skill/MCP/外部 Agent 的 CRUD、用户安装记录、探测与管理界面 API | 对应 `app/api`、catalog 服务和 `db/models/capabilities.py` |
| H1.9 传输与运维 | API/SSE/WS、错误到 HTTP 的映射、审计/日志页面、种子数据、应用队列和健康检查 | `app/api`、`services/realtime`、`queue.py`、`seed.py`、`system_seed.py`、`app/core` |
| H1.10 产品执行扩展 | 全栈交付/文档/部署规则、AgentHub 提示词和汇总发布选择 | `services/execution_extension.py` 注入公共 Loop；其余 `services/runtime/policies.py`、`services/chat`、common/llm 规则仍在宿主 |

H1 可以保留 SQLAlchemy、FastAPI 和产品加密模型，但职责应逐步收敛为“产品数据映射 + 通用能力调用 + 业务结果呈现”。当前同名 `OrchestratorService` 属于宿主服务，不应因 Runtime 移除了旧 Orchestrator 导出而误删。

### H2–H5. 其他宿主与客户端

| 模块 | 功能与边界 | 当前状态 / 位置 |
| --- | --- | --- |
| H2.1 CLI 配置与组装 | 解析命令、本地身份/授权/模型/执行根、驱动选择；直接使用共享执行链 | 已实现于 [`agent_cli`](../../src/agent_cli)；当前 `app.cli` 仍是 Web 管理员工具 |
| H2.2 CLI 输入与输出 | 单次运行、交互 REPL、Ctrl+C、文本/JSONL、stderr 日志、退出码 | 已实现；通过独立 wheel 和真实本机工具验证 |
| H2.3 CLI Scope 与记录 | 本地持久 Context/Journal、事件查询、下一 Run | 已实现 SQLite + 跨进程锁；replay 只读，崩溃不恢复执行位置 |
| H3.1 Eval 场景与依赖 | 固定输入、临时工作目录、fake Provider、故障注入、真实模型配置 | 独立宿主待实现；复用 [`backend/tests/test_agent_runtime`](../../backend/tests/test_agent_runtime) 的语义基线 |
| H3.2 Eval 断言与报告 | 任务结果、事件顺序、权限、取消、资源清理、用量与耗时 | 待实现；所有失败/降级/未执行均记录 |
| H4.1 React 工作台 | routes/pages、features、API、store、types、事件归并、文件与产物展示 | [`frontend/src`](../../frontend/src)；UI 状态不是 Kernel 状态真源 |
| H4.2 Desktop 包装 | Tauri 窗口、本地后端 sidecar、启动/关闭、打包与系统集成 | [`desktop-client`](../../desktop-client)；当前复用完整后端 |
| H4.3 Mobile 客户端 | PWA 缓存、移动 API、Capacitor 原生包装与交互 | [`mobile-client`](../../mobile-client)；复用后端事件和数据契约 |
| H5.1 发行与运行环境 | Docker/nginx、迁移入口、开发启动、可选依赖打包、配置隔离 | [`docker`](../../docker)、根 `pyproject.toml`/`uv.lock`、`backend/pyproject.toml`、各客户端构建脚本 |
| H5.2 工程验证 | 分组测试、契约门禁、前后端回归、桌面 smoke、可复现任务报告 | [`scripts`](../../scripts)、[`e2e`](../../e2e)、各模块 tests；不是运行内核的一部分 |

## 模块间调用规则速查

| 调用方 | 可以使用 | 必须避免 |
| --- | --- | --- |
| K Kernel | AgentExecutor、SchedulerPolicy、ContextStore、Journal、受控 EventSink 等 Port | 具体 Provider、Web 会话服务、产品提示词 |
| S1 执行 | S3/S4/S5 的公开接口，Kernel 执行上下文 | SQL 历史查询、Artifact UI 数据结构 |
| S2 策略 | 快照、提案类型、S9 就绪节点视图 | 直接派发 Actor、调用工具副作用、提交终态 |
| S5 工具 | 授权接口、S6 资源接口、已注册能力 handler | 导入所有 Skill/MCP 实现、让模型修改 grants |
| S7 MCP / S8 Skill / S11 外部 Agent | 注入的模型/工具/资源/记录接口 | 自建无限预算 loop、全局 User/Session |
| S9 Workflow | 调度契约、节点能力接口、图状态 Port | 并行维护第二套父 Run 终态 |
| S10 内容 | 通用文档结构、S6 资源和可选渲染驱动 | 创建 Conversation/Artifact ORM 或公共 URL |
| S12 可观测性 | 已提交事件、记录 Port、脱敏与游标 | 从 UI 倒推执行成功、落盘私有推理 |
| H 宿主 | 上述公开接口和自己的产品适配器 | 通过私有方法绕过 Kernel 状态、授权或日志边界 |

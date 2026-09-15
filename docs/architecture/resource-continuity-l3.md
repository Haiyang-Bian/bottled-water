# AgentHub 0.2.2：L3 资源与工作连续性

本轮在 L2 之上增加资源目录、Python/uv/Git 登记及受控调用、旧任务查询与来源关联。
客观路径、hash 和工具事实自动登记，经验及偏好仍需用户采纳；资源元数据不是文件授权。
普通对话可检索有界旧任务摘要，完整会话通过 `/resume 查询` 选择后恢复。

## 阶段

P0 已核对 PR #27 的精确 head `ce730d7` 和用户验收，以 merge commit
`7d7616663c10c26b11428a7e00f24184188b6c1c` 合入；本任务旧分支清理后建立
`codex/resource-continuity-l3`。保留用户的 `frontend/pnpm-workspace.yaml`。

P1：contracts、逻辑独立资源记录/修订/观察、CAS、schema v5 与身份边界。
P2：有界终态 outbox、客观投影、任务/日期检索、选择器、实际上下文使用清单。
P3：软件发现、登记验证、argv 调用、产物观察、取消和未知结果。
P4：隔离安装、v1–v4 升级、真实 DeepSeek/ConPTY、Web/sidecar、文档与开发 PR。
完成状态以最终验收记录为准；开发测试不等于发行通过。

P1 开发检查：资源、L2 记忆、环境和失败续接共 43 项通过（`var/l3-p1.xml`）。

P1–P4 已完成：实现、安装和实测证据已记录，本地标签已创建，开发分支已推送并创建 [PR #28](https://github.com/Haiyang-Bian/bottled-water/pull/28)，尚未合入。详见[最终验收记录](../acceptance/resource-continuity-0.2.2.md)和[操作说明](../resources.md)。
提交边界：`c4be552` 资源/schema，`5e59ffb` 公共执行与任务资料，`fcac01c` CLI，
`233e273` 停用验证与回归补充，`9de5f6d` 0.2.2 发行源码与验收脚本。
源码文件、最终 wheel 和隔离安装文件由 `record-cli-release.py` 逐一核对；文档提交不改发行源码。

## 模块和状态归属

| 位置 | 本轮职责 |
| --- | --- |
| `agent_contracts/resources.py` | 资源、修订、来源、观察、软件、访问上下文及读写/处理接口 |
| `workspaces/resources.py` | 可接受的结构化观察、字段白名单、路径与词项排序 |
| `workspaces/resource_context.py`、`resource_tools.py` | 每 Run 检索、每请求复核、模型查询及软件调用组合 |
| `workspaces/task_queries.py` | 本机日期词与明确日期范围、确定性词项匹配 |
| `adapters/storage/resources.py`、`tasks.py` | 身份先行过滤、资源 CAS、独立观察、来源幂等、任务摘要 |
| `adapters/local/resources.py` | 有界元数据探测、明确范围索引、软件发现/验证/argv 执行、输出观察 |
| `subsystems/context/assembler.py` | 资源/任务资料先于记忆和历史裁剪，不改写成功任务历史 |
| `agent_cli/resources.py`、`sessions.py`、`selection.py` | 管理入口、候选列表、任务查询和原锁恢复流程 |

与 L2 的差别是新增逻辑独立的资源事实库，而非把路径与软件事实混进长期记忆表。
与 L1 的差别是可以按资料和日期找到旧任务，而非只按位置、时间和任务标题浏览。
Kernel 仍只协调终态；本地终态事务登记资源 outbox，不让处理失败改变已完成的 Run。

## 不变量

- contracts 定义资源、观察、来源、软件和访问接口；规则及处理算法属于 workspaces，
  SQLite/文件/进程实现在适配器，CLI 只负责交互组装。
- 观察与用户修订分开；同一实际路径复用位置记录，不凭 hash 合并不同路径，移动须显式 CAS。
- 只消费成功文件工具和已检查软件输出的结构化事实，不从 stdout、文件正文或模型答复猜测产物。
- 终态事务登记待办，处理游标与索引/事实一起提交；每次最多 20 个 Run / 1000 个事件。
- 索引需用户明确范围，默认 10000 条目 / 30 秒，不读取全文；部分扫描明确标记。
- 软件启用由用户决定，每次使用验证路径和指纹；参数数组、cwd、120 秒默认超时与 Job 生命周期复用现有驱动。
- 每 Run 资源摘要最多 10 项 / 4000 字符，先于记忆和历史裁剪；记录真正发送的版本和观察引用。
- v1–v4 使用迁移锁、会话锁和一次 backup API 备份，单事务直达 v5。v4 记忆和抑制记录不重建。
- 不扫描全盘、不建立常驻服务、不实现 GUI 软件操控或 L4 强隔离。

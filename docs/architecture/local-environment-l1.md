# AgentHub 0.2.0：L1 实施计划与实现边界

本轮按用户批准的 L1 计划实施。完整方向见[设计](./local-agent-environment.md)，实际验收及限制见[0.2.0 验收](../acceptance/local-environment-0.2.0.md)。L2–L4 不在本轮范围。

## 1. 目标和固定行为

任务属于一个 `AGENTHUB_HOME` 对应的本机环境。启动目录提供新任务的位置，不决定旧任务身份。用户从 B 启动选择 A 的任务后，执行位置仍为 A；只有显式 `--cwd` 或 `/cd` 改变保存位置。模型单次命令的 cwd 不会回写任务。

| 入口 | 行为 |
| --- | --- |
| 普通启动 | 启动目录的新草稿，首次有效请求才保存任务 |
| `-c` | 环境最近有 Run 的任务，使用保存位置；占用时报错，不跳过 |
| `-r` / `resume` / `/resume` | 环境任务选择器，不要求先进入原目录 |
| `--here` / `/resume --here` | 按启动目录或当前任务位置精确筛选保存位置，不包含子目录 |
| `--resume ID` / `resume ID` | 指定任务；不能与 `--here` 同用 |
| `--cwd PATH` | 相对启动位置解析，在任务获准范围内显式覆盖保存位置 |
| `/cd [PATH]` | 查看位置和修订，或相对当前任务位置切换；不新增授权 |
| `/add-dir PATH` | 用户显式添加授权根并处理持久化信任 |
| `/new` | 保留位置和授权，新建无历史草稿 |
| `sessions [--here\|--all]` | 只读索引；默认与 `--all` 都是当前环境全部任务 |
| `history [ID]` | 只读历史；无 ID 使用同一选择器，不要求原位置仍有效 |
| `state upgrade` / `doctor` | 显式升级 / 只读报告，不因诊断写入状态 |

恢复回显最近三轮，不重新写入模型上下文。不带 ID 的选择需要交互终端，JSON/重定向模式先查询索引再指定 ID。位置、信任及配置错误为 2；锁忙为 3；现有 Run 终态、预算、取消 130 保持不变。

## 2. 分阶段提交

| 阶段 | 实施内容 | 产物与门槛 |
| --- | --- | --- |
| P0 上阶段收尾 | 重核远程默认分支、旧 wheel、共享与 Web 回归；建立并合入旧阶段 PR #25 | merge commit `cc7d3bf`，保留版本提交；核对远程旧 tip 后清理旧分支，新建 `codex/local-environment-l1` |
| P1 身份和迁移 | 本机身份驱动、v3 schema、旧记录只读投影、备份事务、位置 CAS 和控制事件 | `9a2044e`；v1/v2、WAL、身份不匹配、锁忙和事务故障测试 |
| P2 位置和执行 | 不可变位置、独立资源根、文件/进程/上下文传递及实际位置记录 | `fa333eb`；实际文件读写、授权拒绝、并发独立 cwd，无全局 chdir |
| P3 全局交互 | 全局列表/恢复、位置筛选、`/cd`、历史、草稿和恢复失败保留 | `f7f09ba`；共享 90 项初次回归，随后补充事务回滚测试 |
| P4 安装和验收 | 版本/锁文件、双旧 wheel 升级、真实模型/终端、宿主回归、文档和证据 | 0.2.0 wheel、SHA-256、验收记录、本地标签和开发 PR；具体结果见验收文档 |

每个阶段先通过相关 Ruff、测试与 diff 检查再提交。不全量暂存、不自动 stash；用户的 `frontend/pnpm-workspace.yaml` 保留。未推送标签或软件包，不创建远程 Release。

## 3. 模块与状态所有权

| 层 | 实现和责任 |
| --- | --- |
| contracts | `identity.py` 的 LocalEnvironment / PlatformIdentity；`execution.py` 的 ExecutionLocation、WorkspaceSpec、ResourceGrant、ExecutionContext |
| local adapters | 当前用户 SID、MachineGuid 摘要；非 Windows UID/主机名提示性绑定；文件和进程使用位置快照 |
| storage adapters | schema v3、环境验证、候选查询、事务更新；旧 v1/v2 只读内存投影 |
| workspaces | 显式位置解析、规范化实际路径、授权根校验、失效目录诊断 |
| context | 根据已提交任务历史和当前保存位置构造请求，提醒历史可能来自旧位置 |
| CLI | 环境入口、草稿、会话锁、信任确认、位置变更、只读视图和错误反馈 |
| Kernel / AgentLoop | 保留生命周期和唯一终态；Run 开始及工具记录携带执行位置，不拥有可变任务位置 |

`WorkspaceSpec.roots` 只包含资源根。`ExecutionLocation(cwd, version)` 不可变，Run 开始冻结；原始身份标识不进入模型或公共事件。助手身份由 `environment_id + local` 组成，旧 AgentMemory 的 local 键保留。

每次准备执行重新验证保存根的存在性、真实路径与信任。无效根保留为诊断项，不删除记录或自动重新授权。cwd 必须位于有效集合中。信任表本身不增加任务的显式授权根。文件/cwd 检查不是 OS 沙箱；PowerShell 仍使用当前用户权限，Job Object 只管理进程生命周期。

## 4. 本地 schema v3 与更新事务

- `local_environment`：单个稳定 UUID、拥有者/机器摘要、绑定类型、默认助手 local。
- `sessions`：原 Session ID、environment_id、origin_root、cwd、granted_roots、workspace_version，保留原创建/更新时间。
- `session_events`：按 session/version 唯一记录用户修改位置或授权的事实，独立于 Run 事件。
- 原 runs/events/contexts/trusted/continuation_metadata 数据保留；旧事件没有位置时显示“未保存”。

升级时持有迁移锁及现有会话锁，先用 SQLite backup API 保存一致性备份，再在一个事务里完成 v1/v2→v3。失败回滚，忙时退出 3。只读浏览不绑定旧库，也不迁移；执行写入和显式初始化/升级才创建绑定。绑定不匹配拒绝接管，本轮不提供跨用户/跨设备导入接口。

持久化位置或授权更新要求目标锁且没有活动 Run。校验通过后，在同一事务中检查版本、更新保存值并追加控制事件；提交成功才替换控制器状态。它不更新最近活动时间、不创建虚假 Run。草稿位置只保存在内存。

任务切换按只读选择、获取目标锁、重读校验、检查位置/信任、恢复遗留 Run、提交切换、释放原锁进行。失败仍保留原任务。成功上下文/终态事务未被拆开；失败续接不自动重放副作用。

本轮不改变 Web 表结构，因此不新增 Alembic 迁移。根包和后端均为 0.2.0，后端精确依赖共享包；前端/桌面客户端发行版本保持原值，sidecar 按新源码和锁文件指纹构建。

## 5. 放行与停止

验收包括全局发现、任务锁、历史不重复、中文空格和链接路径、并发 cwd、身份边界、迁移/控制事务故障、只读展示和信息保护。确定性模型捕获真实请求验证历史；实际文件与 PowerShell 验证落点和清理。

独立安装后使用显式 default/DeepSeek 配置运行 A→B 恢复、授权 C→切换 C、重启续聊、位置失效/修复、JSON/纯文本及 ConPTY；不会升级日常状态。OpenAI-compatible 未配置时明确记为未执行。Web 聊天、取消和存储以及桌面 sidecar 构建启动分别验收。

出现位置错误、授权扩大、身份串读、锁丢失、终态/事务不一致或迁移丢失时停止发行并保留失败证据，修复后再验收。通过 L1 不代表长期记忆或 Windows 强隔离已经完成。

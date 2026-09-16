# AgentHub 0.2.3 归档与后续交接

文档收尾日期：2026-09-17；版本验收与本次核对始于 2026-09-16。本记录汇总当前可用版本、已验证范围和暂停边界，供归档后继续开发使用。它不替代原始验收，也不授权开始下一阶段。

## 当前结论

AgentHub 已形成可安装的本机 CLI 闭环：普通用户运行，跨目录操作文件，调用本机或项目虚拟环境中的软件，正常联网，退出后找回任务；长期记忆和资源目录独立于各任务历史。共享执行链继续供 CLI、Web 和桌面使用。

共享包和后端为 **0.2.3**，本地数据库为 **schema v6**。Windows LPAC 强隔离暂缓，原型、P3 代码、数据表及失败记录保留；本版验收成功不代表这些实验已完成。没有目录黑名单、不可绕过的细粒度禁止或多助手共享与交接。

## 阶段进度

| 阶段 | 当前成果 | 状态与证据 |
| --- | --- | --- |
| 0.1.0 公共系统与 CLI | 根级发行包、共用 Runtime/AgentLoop、配置凭据、文件与命令、本地持久化 | 已交付；[早期 MVP 记录](../architecture/cli-mvp.md)保留当时结论 |
| 0.1.1–0.1.5 Harness | 明确停止原因、阶段期限、上下文预算、结果检索、失败续接及原子完成 | 已交付；[版本记录](../architecture/harness-releases.md) |
| 0.1.6–0.1.7 交互 | 无 ID 会话选择、历史回显、Markdown、动态状态、输入编辑、工具详情 | 已交付；当前用法见 [CLI 手册](../cli.md) |
| L1 / 0.2.0 | 环境身份、全局任务发现、工作位置持久化和任务锁 | 已交付；[验收](../acceptance/local-environment-0.2.0.md) |
| L2 / 0.2.1 | 用户保存、模型候选与采纳、来源修订、检索、停用和遗忘抑制 | 已交付；[验收](../acceptance/foundational-memory-0.2.1.md) |
| L3 / 0.2.2 | 资源目录、客观观察、软件登记、旧任务查询与工作连续性 | 已交付；[验收](../acceptance/resource-continuity-0.2.2.md) |
| 原生体验 / 0.2.3 | 普通用户跨目录访问、直接软件调用、软件发现、网络及项目外缓存 | 已构建并完成本版验收；[证据](../acceptance/native-user-experience-0.2.3.md) |
| L4a LPAC | 原生原型、受限 Runtime 注入及部分持久化/协调代码 | 暂缓，P3/P4 未完成正式放行；保留旧成功子集和失败证据 |
| L4b 及后续 | 多助手共享记忆、交接、强隔离产品化 | 待实现；不作为本版隐含能力 |

## 已固定的产品与架构边界

- 普通 CLI 显式使用 `file_access_scope=user`。相对路径由任务位置决定，目录不再是普通用户任务的访问边界；`/add-dir` 是参考目录入口，旧 trust 不能用于撤权。
- 公共 `ResourceGrant` 默认仍为 `workspace`，没有顺带放宽 Web 或保留的受限驱动。普通任务不自动提权；提升宿主不能执行模型任务和工具管理。任意脚本防提权并不是本版保证。
- `process.run` 使用程序和参数数组；`software.discover` 只读发现项目虚拟环境、PATH 和 CLI 解释器。登记的 `software.run` 仍是可选的固定程序调用方式。
- 任务、模型请求、工具调用是不同计数层级。Run 保留总时间和 token 预算；取消清理受管理进程，但不会回滚已经发生的文件修改。
- 历史按任务隔离。用户保存的长期记忆立即生效；模型只能提出候选。资源自动积累只消费已确认的客观工具事实，不把任意模型文字当成事实。
- 已保存的受限任务或受限默认环境不静默降级；需用户显式指定 `--sandbox current-user`。正常启动、升级和这次归档不执行 UAC、修改业务 ACL 或安装开机项。
- Python 3.11 主链继续保留。CLI → `RuntimeEngine` → `SingleAgentPolicy` → `AgentLoopExecutor`，没有第二套 CLI 循环。MCP、Skill、Workflow、内容及外部 Agent 的通用部分仍有迁移工作，现有 Web 功能不等于 CLI 已接入。

源码职责以[子系统目录](../architecture/subsystems.md)为准：`src/agent_contracts` 为契约，`agent_runtime` 为内核，`agent_subsystems` 为公共算法，`agent_adapters` 为本机/存储驱动，`agent_cli` 为配置、交互和组装；`backend/src` 保留 Web 业务与 ORM。

## 交付物与源码对应

| 项目 | 归档值 |
| --- | --- |
| 生产源码提交 | `24a415371edbb169bbda1483a15b4478c63ba519` |
| 本地版本标签 | `agenthub-v0.2.3`，指向 `cc2e0cc8c22341f4286c71ecef7fc7c283a013fe` |
| wheel | `dist/agenthub_system-0.2.3-py3-none-any.whl` |
| wheel SHA-256 | `38f78a150c6ee147d826c1e794dd32a235f5d8c05931a325180feffc609b0880` |
| 验收清单 | `dist/agenthub_system-0.2.3-acceptance.json` |
| 清单 SHA-256 | `22d22a43b6bdcd8f4b4929db93c9a124d3a5afc33c50d155b17739613beb110e` |
| 系统环境 | Windows 11 25H2 x64，build `26200.9457`，Python 3.11 |
| 真实 Provider | 显式 `default / deepseek / deepseek-v4-flash` |

`24a4153` 之后至标签提交仅有文档/测试收尾，不改变 wheel 中的生产源码；记录器已经核对 wheel、独立安装与该源码提交一致。本次归档文档继续提交在标签之后，**不重建 wheel、不移动标签**。源码版本、安装版本和文档提交应分别记录，不能只用最新 HEAD 猜测产物来源。

## 验收范围

以下是已有报告的汇总，本次文档归档没有重跑真实模型或重新生成测试结果。分组重叠，不能相加为独立测试总数。

| 检查 | 保存结果 |
| --- | --- |
| 共享系统 | 140 passed；Runtime、上下文、任务、记忆、资源、文件与进程 |
| 原生行为针对性回归 | 44 passed；跨目录、旧模式转换、提升宿主和信任入口等 |
| 展示修复 | 14 passed；修复 Provider 复用消息 ID 导致最终正文重复 |
| 保留的实验边界逻辑 | 74 passed；不等于 LPAC 原生放行 |
| Web | 39 passed；聊天、取消、持久化相关回归 |
| 独立安装 | 14 passed；仓库外运行已安装 wheel |
| 旧状态升级 | 0.1.0/v1、0.1.7/v2、0.2.0/v3、0.2.1/v4、0.2.2/v5 分别到 v6 |
| DeepSeek | 跨 A/B/C 读取、修改、测试、真实网络依赖获取、diff、退出恢复、取消及再次续接 |
| 终端 | 真实 ConPTY；选择、窄屏、滚动、工具详情、取消后恢复输入；plain/NO_COLOR/JSONL 边界另有测试 |
| 桌面 | 最终 sidecar 构建与启动检查；不是完整 NSIS 安装器或所有桌面 UI 验收 |

原始报告路径、Run ID、用量及失败修复见[本版验收记录](../acceptance/native-user-experience-0.2.3.md)。未执行项包括未配置的 OpenAI-compatible、其他 OS build/平台、Docker，以及暂停的 LPAC 完整门槛。Web/桌面局部回归不能替代这些项目。

## Git 与本地材料

本次文档收尾前的核对点：开发分支 `codex/native-user-experience`，HEAD `cc2e0cc`；[PR #30](https://github.com/Haiyang-Bian/bottled-water/pull/30) **OPEN、未合并**，目标 `main` 当时为 `8bafe871cae9f7f5f417f302a9b9bfbb663978ee`。平台没有返回检查项，不能称为 GitHub CI 全部通过。本次文档提交更新同一分支和 PR，不执行合并。

保留 `codex/standing-permissions-p3`（核对点 `13cf45b`）及其他任务分支；本分支从 P3 开发状态延续，不代表 P3 已被单独验收合入。保留未跟踪的用户文件 `frontend/pnpm-workspace.yaml`，不全量暂存、不自动 stash。

归档材料分两处：

| 位置 | 内容与保存方式 |
| --- | --- |
| Git / PR | 源码、测试、脚本、架构、手册、验收摘要和本交接记录 |
| 本地 `dist/` | wheel、校验值、验收清单；同事用的 `agenthub-0.2.3-windows-guide.md`，旧 `agenthub-0.2.2-windows-guide.md` 继续保留且不进 PR |
| 本地 `var/` | 原始测试 XML、真实运行与 ConPTY 报告、sidecar 日志、历史失败证据；具体路径见验收记录 |
| 用户状态目录 | 配置、加密凭据及私人任务数据；不是仓库交付物，本次不迁移、不打包 |

`dist/`、`var/` 被 Git 忽略，**仅推送 PR 不会备份这些材料**。若需要迁移机器，应另行保存 wheel、校验值及必要证据；日志仍可能含项目内容，不能把整个状态目录或凭据当作同事安装包。没有上传软件包、创建远程 Release 或推送本地版本标签。

## 归档后的使用与继续开发

日常用法见 [CLI 手册](../cli.md)，安装、升级与回退见 [0.2.3 说明](../releases/0.2.3.md)。同事无需克隆源码，接收 wheel 与校验值后安装，使用自己的模型配置；不要复制原用户的 `.agenthub`。

下次继续时先核对：

1. 当前工作树、分支、PR #30 是否已合并及远程 tip；不要依据本快照直接合并、删分支或重置用户文件。
2. `agenthub --version`、`agenthub doctor` 的实际安装位置、schema 和模式；源码环境、全局安装与隔离验收环境可能不同。
3. 按本交接记录和验收报告找到对应 wheel、源码及失败记录；新测试使用新的隔离状态，避免修改日常数据。
4. 先根据真实使用反馈确定下一项体验改进，再决定是否继续子系统迁移或独立评测宿主；这些是待评估方向，不是本轮已批准的实施任务。
5. 如果重启 L4，重新评审需求和放行清单。不要因保留了代码与 schema v6 就默认启用 LPAC，或追溯把未完成的原生验收改成通过。

当前可归档的是 **0.2.3 原生使用闭环及其证据**；PR 合入、更多平台验证与后续能力仍各有独立完成条件。

本次文档收尾检查包括修改文档的本地链接与代码围栏、31 组 CLI 参数示例（仅解析，不执行）、11 份已有 XML 报告的结果及 wheel/清单哈希。`git diff --check` 通过。没有新增模型调用、状态迁移、产物重建或功能测试结论。

# AgentHub 本地 CLI

本地 CLI 直接使用共享 Runtime、SingleAgentPolicy 和 AgentLoop。它不需要启动 Web 服务或产品数据库。当前源码版本为 `agenthub-system 0.2.2`，主要验证平台为 Windows、Python 3.11；本机实际安装版本用 `agenthub --version` 核对。

> 0.2.0 实现 L1 全局任务与独立位置，0.2.1 实现 L2 基础记忆。0.2.2 增加 L3 资源、软件、任务查询和 schema v5，见[资源操作说明](./resources.md)及[验收记录](./acceptance/resource-continuity-0.2.2.md)。`-c` 恢复本机最近任务；`/resume 昨天的实验` 搜索选择，`--here` 限定目录。L4 强隔离仍待实现。

## 安装与首次使用

L2 基础记忆的接口及边界见[0.2.1 实施文档](./architecture/foundational-memory-l2.md)，实测范围见[验收记录](./acceptance/foundational-memory-0.2.1.md)。
`/memory` 浏览，`/memory add` 直接保存，`/memory candidates` 采纳模型候选；
`edit` 创建新修订，`disable`/`enable` 停用或恢复，`forget` 遗忘，`used` 查看实际使用清单。
脚本入口为 `agenthub memory …`；保存须提供 `--title`、`--body`，修改须提供 ID 和 `--revision`，
遗忘还须提供 `--yes`。记忆许可不授予文件权限；遗忘保留原会话、Run 和备份。

在源码根目录运行：

```powershell
uv tool install --python 3.11 ".[cli]"
agenthub init
cd D:\Work\my-project
agenthub
```

也可以安装构建产物：

```powershell
uv build --package agenthub-system --wheel
uv tool install --force --python 3.11 ".\dist\agenthub_system-0.2.2-py3-none-any.whl[cli]"
```

`init` 询问 Provider、模型 ID、base URL 和隐藏输入的 API Key；凭据使用当前 Windows 用户的 DPAPI 加密。模型请求只在执行任务或显式运行 `agenthub model check` 时发起。若终端找不到命令，运行 `uv tool update-shell` 后重新打开终端。

首次在目录中运行会询问信任；接受后持久保存。信任意味着允许智能体自动调用工具，以当前 Windows 用户权限执行 PowerShell/Git。**这不是 OS 沙箱：PowerShell 可以访问所列目录之外的文件和网络。** 文件工具与命令 `cwd` 的路径检查，以及 Job Object 的进程管理，都不提供文件或网络强隔离。

## 配置

默认目录为 `%USERPROFILE%\.agenthub`，可用 `AGENTHUB_HOME` 指向另一套独立配置：

```text
.agenthub/
  config.toml       命名模型 profile、默认 profile、运行限制
  credentials/      当前用户 DPAPI 密文
  state.sqlite3     环境身份、任务、Run、记忆、修订、候选和处理队列（v4）
  locks/            持有会话的跨进程文件锁
  logs/             脱敏轮转诊断日志
  tmp/              预留的受管理临时目录
```

自动化可引用现有环境变量，不把 API Key 写进配置文件：

```powershell
# MY_MODEL_KEY 应由用户在外部安全配置。
agenthub init --profile work --provider openai_compatible --model YOUR_MODEL_ID `
  --base-url https://your-provider.example/v1 --credential-env MY_MODEL_KEY

agenthub init --profile deepseek --provider deepseek --model YOUR_DEEPSEEK_MODEL_ID `
  --credential-env DEEPSEEK_API_KEY

agenthub config show
agenthub doctor
agenthub model check --profile work
```

再次 `init --profile NAME` 更新该 profile，并将其设为默认。运行时用 `--profile NAME` 选择其他配置。`doctor` 只读检查 schema、环境绑定、升级需求、配置、凭据及驱动，不调用模型、不创建或升级状态。显式运行 `agenthub state upgrade` 可创建环境或升级旧库。

配置示例（只保存引用）：

```toml
default_profile = "work"
# 可选：绝对路径覆盖；默认优先 pwsh.exe，再回退到 Windows PowerShell。
# powershell = 'C:\Program Files\PowerShell\7\pwsh.exe'

[profiles.work]
provider = "openai_compatible"
model = "YOUR_MODEL_ID"
base_url = "https://your-provider.example/v1"
credential_ref = "env:MY_MODEL_KEY"
max_tokens = 4096
timeout_seconds = 120
max_history_chars = 64000

[limits]
wall_time_seconds = 1200
idle_time_seconds = 180
max_total_tokens = 500000
max_decisions = 50
cancellation_grace_seconds = 5

[execution]
# 可选；省略表示不限制模型请求轮数，最终答复也计一轮。
# max_model_turns = 40
```

`--max-turns N` 覆盖本次运行的模型请求轮数，`--max-turns unlimited` 取消配置中的轮数限制。总时间与累计 token 预算仍生效。达到限制后不额外调用模型总结，也不会自动启动新 Run。终端与 JSONL 区分模型请求数、工具轮数和工具调用次数。

空闲看门狗监测未处于有效执行阶段的异常空闲。模型和工具阶段有独立截止时间，并受 Run 总期限约束；流式 token 不视为任务进展。模型轮数、总 token、单次输出截断、模型超时和协议错误保留独立原因码。

`max_tokens` 限制单次模型输出，`timeout_seconds` 为 Provider 请求超时。`max_history_chars` 是历史和当前请求的字符预算：按完整历史轮次裁剪，当前请求始终保留，裁剪信息写入 `agent.context_built`。它不是准确 tokenizer 上限，不自动调用摘要模型。Runtime 总预算与命令超时另行约束；未知用量明确标为估算，实际 usage 优先使用 Provider 值。

## 会话、跨目录与批处理

```powershell
agenthub                                      # 新会话草稿，提交任务后保存
agenthub -c                                    # 本机环境最近有执行记录的任务
agenthub -r                                    # 列表选择，不需要记住会话 ID
agenthub resume                                # 同样打开选择器
agenthub --continue                           # 与 -c 相同
agenthub --resume SESSION_ID                  # 环境内指定任务，在其保存位置执行
agenthub --continue --add-dir D:\Work\shared   # 添加第二个根目录
agenthub -p "修复问题并执行相关测试"             # 一次任务后退出
agenthub --continue --json -p "检查 Git diff"  # JSONL
agenthub sessions                            # 本机环境任务表，展示保存位置
agenthub --json sessions                     # 脚本使用的会话索引
agenthub sessions --all                       # 所有目录的会话索引
agenthub -c --here                           # 保存位置精确匹配启动目录的最近任务
agenthub sessions --here                     # 只列出该保存位置的任务
agenthub history                            # 列表选择后只读查看历史
agenthub history SESSION_ID                 # 无须信任原目录即可读取
agenthub --resume SESSION_ID --cwd D:\Work\shared # 显式改变位置，必须已获授权
agenthub replay RUN_ID                        # 当前环境内的已保存事件 JSONL
```

交互命令：`/help`、`/session`、`/resume [--here]`、`/new`、`/history`、`/cd [PATH]`、`/add-dir PATH`、`/exit`。运行时 `Ctrl+C` 取消当前 Run 并返回输入界面；输入时 `Ctrl+D` 退出。取消不回滚已经发生的文件修改或命令副作用。

每个 `AGENTHUB_HOME` 有独立环境 UUID；默认助手为该环境的 `local`，切换模型不会更换助手。Windows 绑定当前用户 SID 与机器标识的摘要；不匹配时拒绝接管。非 Windows 开发路径用 UID/主机名作提示性绑定。绑定不替代 ACL 或 OS 沙箱。

任务历史始终独立。全局发现不会把其他任务全文自动加入上下文，也不会增加目录授权。位置使用规范化实际路径，不自动提升到 Git 根；`--here` 按保存位置精确匹配，不包括子目录，不能与显式任务 ID 同用。打开任务、`/cd`、增加目录不改变基于最近 Run 的排序。

`/cd` 显示位置与修订号；`/cd PATH` 在现有授权根内切换，`/add-dir PATH` 才能增加授权。交互相对路径以当前任务位置解析，`--cwd` 相对路径以启动位置解析；空格路径可加引号。`/new` 保留位置与显式授权，创建无历史的草稿。每个 Run 冻结位置和有效根集合，模型单次命令的 cwd 或 PowerShell 内的 cd 不修改任务位置。

授权根不会随 cwd 扩缩。每次执行重新检查存在性、实际路径及信任；失效记录保留并显示，cwd 必须处于有效根内。符号链接与 junction 按实际目标校验。`trust add` 不会单独将目录加入已有任务的授权集合。

批处理不会隐式接受信任。预先由用户明确管理：

```powershell
agenthub trust add D:\Work\my-project
agenthub trust add D:\Work\shared
agenthub trust remove D:\Work\shared
```

授权入口只由用户命令维护，模型没有修改信任表的工具。当前用户脚本仍能触达用户可读写资源，不能把应用层授权接口当作对恶意脚本的隔离。

同一会话只允许一个持有者，不同会话可并行。崩溃后再次取得会话锁，才将该会话未结束的 Run 标为 `failed/process_lost`。`--continue` 从已提交历史发起新 Run，不恢复崩溃时的指令位置，也不自动重做副作用。保存的工作目录会恢复。

## 基础记忆（0.2.1）

`/memory` 用列表浏览和选择；`/memory add` 打开表单，确认后立即保存。
普通对话中的“记住……”由模型提出候选，结束时提示数量；使用 `/memory candidates`
核对来源后采纳、拒绝或修改后采纳，也可批量处理当前页的可采纳项。
浏览和用户管理不需要模型凭据，不创建任务。

```powershell
agenthub memory add --title "语言偏好" --body "默认用中文解释" --kind preference --basic
agenthub --json memory search "中文"
agenthub memory show MEMORY_ID
agenthub memory edit MEMORY_ID --revision 1 --body "默认用中文解释，先给结论"
agenthub memory disable MEMORY_ID --revision 2
agenthub memory enable MEMORY_ID --revision 3
agenthub memory forget MEMORY_ID --revision 4 --yes
agenthub --json memory candidates
agenthub memory adopt CANDIDATE_ID --revision 1
agenthub memory process
agenthub memory used --run RUN_ID
```

以上 ID 和修订号应从上一步结果读取；交互终端可省略 ID 使用列表选择。
`/memory used` 查看当前任务最近 Run 的实际使用清单；`show --revision N` 查看仍保留的旧修订。
`search` 支持中文短词、英文词、标题、别名、标签及路径，使用 `--offset`、`--limit`
翻页，每页最多 20 项。`--alias`、`--tag` 可重复；`--directory "路径"` 设置目录及子目录适用范围，
`--global` 改为全局，`--basic` / `--no-basic` 控制基础资料。正文最多 2000 字符。

基础资料每 Run 默认最多 2000 字符，相关检索最多 8000 字符，计入总上下文预算。
目录事实只有位置匹配或请求明确提及名称、别名、路径时自动纳入；模型也能显式搜索。
记忆是带来源的资料，不能保证模型每次都遵循其表述。未采纳候选不会进入跨任务召回。

停用可以恢复；遗忘移除普通记忆查询可见的正文和索引，并抑制旧来源再次生成相同知识。
原会话、Run 和备份保留，已发送给模型的内容不追溯撤回。记忆许可与文件许可独立：
撤销来源目录信任后，已采纳知识仍可使用；如需撤销知识，须另行停用或遗忘。
显式操作失败会报错；后台确定性整理失败不改变原 Run 终态，用 `memory process` 重试。

## 工具与输出

| 工具 | 行为 |
| --- | --- |
| `file.list` / `file.search` | 列举默认浅层、搜索默认递归；`recursive` 控制递归；返回文件、目录、分页及发现范围信息 |
| `file.read` | 按行读文本并返回 SHA-256；支持 UTF-8、UTF-8 BOM 和带 BOM 的 UTF-16；文本上限 8 MiB |
| `file.write` | 新建用 `expected_hash="new"`；覆盖必须提供最近读取的 hash |
| `file.edit` | 精确匹配一次旧文本；检测外部修改，保留编码、BOM 和换行风格 |
| `powershell.run` | 真正的多行脚本、管道；非交互；默认超时 120 秒且受剩余 Run 时间限制 |
| `git.run` | 独立参数数组，不拼接 shell；不自动提交、推送或重置 |
| `memory.search` / `memory.read` | 读取本环境默认助手获准的有效知识；不扩大文件授权 |
| `memory.propose` | 保存待校验候选，只有用户采纳才能长期生效 |

进程 stdout/stderr 合计保留最多 64 KiB，超限后仍排空管道，并返回 `truncated`。文件读取内容及搜索/列举分页受 64 KiB 上限约束。Job Object 采用挂起创建、加入后运行；加入失败报错，超时、取消或宿主退出清理该 Job 的进程树。本期没有 PTY 或跨任务常驻终端。

文件发现继承授权根内的 `.gitignore`，默认过滤 `.git`、`.venv`、`node_modules`、`__pycache__`、`.next`、`target`。已被 Git 跟踪的文件仍可发现。`include_ignored=true` 显式查看被忽略内容；直接 `file.read` 不受发现规则限制，仍检查授权路径。索引不可用或截断通过 `discovery_degraded` 和 `index_state` 报告，分页结果不代表完整扫描。

文件更新使用乐观 hash 检查和临时文件替换，检测到版本冲突时要求重新读取；它不锁住所有外部编辑器。环境变量过滤和日志脱敏减少意外泄漏，不能向同一用户运行的任意脚本隐藏所有凭据。

JSONL 模式 stdout 只含结构化事件/结果，诊断走 stderr。工具开始记录调用 ID、参数，结束记录结果、退出码和失败原因；所有持久记录经过脱敏。私有模型推理不展示、不持久保存。`replay` 只读取已保存事件。

| 退出码 | 含义 |
| --- | --- |
| 0 | Kernel 提交成功 |
| 1 | Run 或模型执行失败 |
| 2 | 配置、能力、目录信任等启动错误 |
| 3 | 会话已被其他进程占用 |
| 130 | 用户取消 |

## 开发验证

### 0.2.2 升级、位置修复与续接

保留原 `.agenthub`，退出使用该状态目录的所有 CLI 后安装 0.2.2 wheel，再执行 `agenthub state upgrade`。配置、profile、凭据引用、信任和 Session/Run ID 不重新初始化。首次必要写入也能触发升级；仅浏览草稿、列表、历史、replay、旧库记忆/资源查询或 doctor 不升级。

从 v1–v4 直接升至 v5：持有迁移锁及现有会话锁，使用 SQLite backup API 保存包含 WAL 的一致性 `.v版本-时间戳.bak`，在单个事务中升级，不先提交中间版本。其他会话占用返回 3，不中断运行者。v1/v2 的旧 `root` 成为创建/保存位置，`root + dirs` 成为显式授权；v3 已有环境 UUID 原样保留，v4 的记忆、候选和遗忘抑制原样保留。资源目录初始为空，不回扫旧 Run。

保存位置失效时，可先运行 `agenthub history SESSION_ID` 查看，再执行 `agenthub --resume SESSION_ID --add-dir "有效目录" --cwd "有效目录"` 修复。非交互模式需先 `agenthub trust add "有效目录"`；若修复位置已经获准，不必重复添加。位置变更事务失败时保留原任务及位置。

`--continue`、`--resume ID` 和交互模式下一次输入都会开始新的 Run，载入成功历史与尚未消费的失败/取消观察。工具已经开始但没有保存结果时标为未知，需要先核实当前文件或进程状态；不会自动重放副作用。成功上下文、续接游标和成功终态一起提交。

升级失败保留旧库及备份。回退时先退出所有实例，保存升级后的数据库，再使用对应旧 wheel 和升级前 `.bak` 恢复；旧二进制不能打开 v5，备份不包含升级后的会话、记忆和资源。回退应使用生成该备份的旧 wheel。不要只复制运行中数据库的主文件；应使用 SQLite backup API 或在所有连接关闭后操作配套备份。本轮没有 Web schema 变化；此前完成事务的 Alembic 迁移 `b8c9d0e1f2a3` 保留。

```powershell
uv sync --all-packages --all-extras
.\scripts\run-tests.ps1 -Stack system -Module cli -Type unit
.\scripts\run-tests.ps1 -Stack system -Module cli -Type integration
.\scripts\verify-cli-install.ps1
```

最后一个脚本在 `var/cli-install-validation` 中构建、隔离安装 wheel，并在仓库外运行完整工具循环、会话恢复及 Windows 进程故障测试；不改用户全局 `uv tool` 安装。共享工作区测试环境会随所选 package 同步；桌面打包使用独立环境。

真实服务测试必须显式启用：设置 `AGENTHUB_LIVE_HOME` 指向用户配置目录，并设置 `AGENTHUB_LIVE_OPENAI_PROFILE` 或 `AGENTHUB_LIVE_DEEPSEEK_PROFILE`，再运行 `system/providers/live` 分组。测试使用独立临时项目，会产生真实模型费用。当前自动 live 场景覆盖修复和续聊；完整八项验收清单及未执行项见 [实施记录](./architecture/cli-mvp.md)。

本期未实现 MCP/Skill 接入、AppContainer/受限 Token、多 Agent 权限治理、完整终端模拟或崩溃原地续跑。剩余源代码归属见[子系统目录](./architecture/subsystems.md)。

## 会话与界面（0.1.6—0.1.7）

直接运行 `agenthub` 进入新会话草稿，首次提交任务后才保存。`agenthub -r`、
`agenthub resume` 或交互命令 `/resume` 打开本机环境的选择列表，无需记忆 ID。
输入文字过滤，方向键选择，PageUp/PageDown 翻页，Enter 确认，Esc 返回。
`agenthub -c` 继续最近有执行记录的会话；失败、取消会话也可以继续，空会话不参与排序。
恢复后回显最近 3 轮，超过 12,000 字符会提示截断；`/history` 分页查看完整保存记录。
回显不会再追加到模型上下文。`/new` 创建草稿，`/session` 查看诊断 ID 和目录。

界面默认显示 Markdown、工具摘要和动态状态。普通工具输出展示最多 6 行；
`/tools` 通过两级列表选择 Run 和工具，分页读取已保存结果，不会重新执行。
原结果被驱动截断时，未保存部分不能通过详情恢复。

- Enter 发送，Alt+Enter 或 Ctrl+J 换行；支持多行粘贴。
- Tab 补全命令及 `/add-dir`、`/cd` 路径；上下键浏览当前会话输入。
- `--verbose` 或 `/verbose on|off` 控制详细工具输出和内部阶段信息。
- `--plain` 禁用装饰、颜色及运行动画；`--no-color` 或 `NO_COLOR` 禁用颜色。
- 重定向自动使用纯文本；`--json` 保持 JSONL，不混入界面或历史回显。
- 执行中 Ctrl+C 取消当前任务并清理工具进程，然后恢复输入；已产生的文件修改保留。

`agenthub --json sessions` 用于脚本查询；非交互恢复必须提供 `--resume ID`。
恢复列表默认覆盖本机环境；`--here` 显式限定保存位置。`sessions --all` 是本机环境全部任务的只读索引，不合并其他 home 或扩大授权。
界面使用 prompt_toolkit 与 Rich，未引入全屏 TUI，也未改变本机工具的非交互进程模型。

## Harness 0.1.1—0.1.5

默认模型请求轮数无限制，单 Run 仍受 1,200 秒和 500,000 累计 token 限制。使用 `--max-turns 25` 或 `--max-turns unlimited` 覆盖当前运行；也可在全局配置中设置 `[execution].max_model_turns`。达到限制后不追加总结请求，不自动创建新 Run。

每个 profile 支持 `max_history_chars`、`max_context_chars`（默认 64,000）和可选 `context_window_tokens`；配置窗口后预留 `max_tokens` 输出额度。上下文按完整历史轮次裁剪，再缩减工具输出，保留来源引用。模型可以使用同会话的 `run.read_tool_result` 读取保存记录。

`file.list` 默认浅层列出文件与目录，显式 `recursive=true` 才递归；`file.search` 默认递归。发现默认排除生成目录并遵守分层 `.gitignore`，已跟踪源码保持可发现性；`include_ignored=true` 查看被忽略内容。显式读取已授权路径不受发现忽略规则限制。

`--version` 读取发行元数据。`doctor` 无需模型请求即可显示安装位置、schema、有效配置、限制和本机能力；缺失配置时仍提供部分诊断。`replay` 可只读打开旧 schema，不触发迁移；旧字段缺失显示 `null`，不能补造历史事实。

安装验收：`scripts/verify-cli-install.ps1`；真实旧版本升级：`scripts/verify-cli-upgrade.ps1`；完整真实服务验收：`scripts/accept-harness-live.py --source-home <配置目录> --profile <显式profile> --python <已安装Python> --output <报告路径>`。后者会产生费用，工作项目和状态目录均为独立临时目录。具体通过、失败及未执行项见 [Harness 版本记录](./architecture/harness-releases.md)。

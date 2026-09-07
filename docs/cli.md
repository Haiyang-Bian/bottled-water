# AgentHub 本地 CLI

本地 CLI 直接使用共享 Runtime、SingleAgentPolicy 和 AgentLoop。它不需要启动 Web 服务或产品数据库。当前发行版本为 `agenthub-system 0.1.1`，主要验证平台为 Windows、Python 3.11。

## 安装与首次使用

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
uv tool install --python 3.11 ".\dist\agenthub_system-0.1.1-py3-none-any.whl[cli]"
```

`init` 询问 Provider、模型 ID、base URL 和隐藏输入的 API Key；凭据使用当前 Windows 用户的 DPAPI 加密。模型请求只在执行任务或显式运行 `agenthub model check` 时发起。若终端找不到命令，运行 `uv tool update-shell` 后重新打开终端。

首次在目录中运行会询问信任；接受后持久保存。信任意味着允许智能体自动调用工具，以当前 Windows 用户权限执行 PowerShell/Git。**这不是 OS 沙箱：PowerShell 可以访问所列目录之外的文件和网络。** 文件工具与命令 `cwd` 的路径检查，以及 Job Object 的进程管理，都不提供文件或网络强隔离。

## 配置

默认目录为 `%USERPROFILE%\.agenthub`，可用 `AGENTHUB_HOME` 指向另一套独立配置：

```text
.agenthub/
  config.toml       命名模型 profile、默认 profile、运行限制
  credentials/      当前用户 DPAPI 密文
  state.sqlite3     信任、会话、已提交上下文、Run 和有序事件
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

再次 `init --profile NAME` 更新该 profile，并将其设为默认。运行时用 `--profile NAME` 选择其他配置。`doctor` 检查配置、凭据、PowerShell、Git 和状态目录，不调用模型。

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
agenthub                                      # 当前目录新会话
agenthub --continue                           # 当前目录最近会话
agenthub --resume SESSION_ID                  # 当前目录指定会话
agenthub --continue --add-dir D:\Work\shared   # 添加第二个根目录
agenthub -p "修复问题并执行相关测试"             # 一次任务后退出
agenthub --continue --json -p "检查 Git diff"  # JSONL
agenthub sessions                            # 当前目录会话
agenthub sessions --all                       # 所有目录的会话索引
agenthub replay RUN_ID                        # 已保存的事件 JSONL
```

交互命令：`/help`、`/session`、`/add-dir PATH`、`/exit`。运行时 `Ctrl+C` 取消当前 Run 并返回输入界面；输入时 `Ctrl+D` 退出。取消不回滚已经发生的文件修改或命令副作用。

目录身份使用启动目录的规范化实际路径，不自动提升到 Git 根。不同目录拥有独立历史；信任一个父目录不会自动信任其所有子目录。附加目录保存在该会话中，后续恢复仍检查其信任。符号链接和 junction 按实际目标验证，不能借链接扩大文件工具的授权范围。

批处理不会隐式接受信任。预先由用户明确管理：

```powershell
agenthub trust add D:\Work\my-project
agenthub trust add D:\Work\shared
agenthub trust remove D:\Work\shared
```

授权入口只由用户命令维护，模型没有修改信任表的工具。当前用户脚本仍能触达用户可读写资源，不能把应用层授权接口当作对恶意脚本的隔离。

同一会话只允许一个持有者，不同会话可并行。崩溃后再次取得会话锁，才将该会话未结束的 Run 标为 `failed/process_lost`。`--continue` 从已提交历史发起新 Run，不恢复执行位置，也不自动重做副作用。

## 工具与输出

| 工具 | 行为 |
| --- | --- |
| `file.list` / `file.search` | 路径模式/字面文本搜索；`offset/limit` 分页；返回 `next_offset` 和截断标记 |
| `file.read` | 按行读文本并返回 SHA-256；支持 UTF-8、UTF-8 BOM 和带 BOM 的 UTF-16；文本上限 8 MiB |
| `file.write` | 新建用 `expected_hash="new"`；覆盖必须提供最近读取的 hash |
| `file.edit` | 精确匹配一次旧文本；检测外部修改，保留编码、BOM 和换行风格 |
| `powershell.run` | 真正的多行脚本、管道；非交互；默认超时 120 秒且受剩余 Run 时间限制 |
| `git.run` | 独立参数数组，不拼接 shell；不自动提交、推送或重置 |

进程 stdout/stderr 合计保留最多 64 KiB，超限后仍排空管道，并返回 `truncated`。文件读取内容及搜索/列举分页受 64 KiB 上限约束。Job Object 采用挂起创建、加入后运行；加入失败报错，超时、取消或宿主退出清理该 Job 的进程树。本期没有 PTY 或跨任务常驻终端。

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

```powershell
uv sync --all-packages --all-extras
.\scripts\run-tests.ps1 -Stack system -Module cli -Type unit
.\scripts\run-tests.ps1 -Stack system -Module cli -Type integration
.\scripts\verify-cli-install.ps1
```

最后一个脚本在 `var/cli-install-validation` 中构建、隔离安装 wheel，并在仓库外运行完整工具循环、会话恢复及 Windows 进程故障测试；不改用户全局 `uv tool` 安装。共享工作区测试环境会随所选 package 同步；桌面打包使用独立环境。

真实服务测试必须显式启用：设置 `AGENTHUB_LIVE_HOME` 指向用户配置目录，并设置 `AGENTHUB_LIVE_OPENAI_PROFILE` 或 `AGENTHUB_LIVE_DEEPSEEK_PROFILE`，再运行 `system/providers/live` 分组。测试使用独立临时项目，会产生真实模型费用。当前自动 live 场景覆盖修复和续聊；完整八项验收清单及未执行项见 [实施记录](./architecture/cli-mvp.md)。

本期未实现 MCP/Skill 接入、AppContainer/受限 Token、多 Agent 权限治理、完整终端模拟或崩溃原地续跑。剩余源代码归属见[子系统目录](./architecture/subsystems.md)。

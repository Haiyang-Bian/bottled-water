# 工作树与安全 Git 协作

## 生命周期与两种模式

一个团队 `Conversation` 最多绑定一个 Git 仓库和一个基准提交。每个成员 Agent 可拥有一个跨 Run 复用的独立工作树，且路径、分支和 Agent 绑定关系唯一。

- managed：AgentHub 在应用数据目录创建工作树和 `agenthub/<conversation-short>/<agent-short>` 分支。
- adopted：用户绑定同一 Git common dir 中已经由 Git 注册的独立工作树；仓库主工作树不能被冒充为 Agent 工作树。

Conversation 归档不删除工作树。显式释放时，脏工作区或相对基准存在未集成提交都会被拒绝；adopted 模式只解除绑定，不删除用户目录或分支。

## 可信执行根

`ExecutionRootPort` 按 Conversation 和 Agent 解析执行目录。Adapter 将结果包装为进程内 `TrustedExecutionRoot` 能力，再交给文件、终端、沙箱、外部 CLI Agent 和 Git 工具。模型提供的 cwd、绝对路径或 `..` 不是授权信息，越界请求必须失败。

绑定仓库后，未分配工作树的 Agent 不能退回仓库主目录执行。该约束保护用户当前分支，也避免 Agent 通过共享目录读取彼此尚未主动公开的修改。

## Git 工具

Agent 可使用 `git.status`、`git.diff`、`git.commit` 和 `git.integrate`。不提供 push、reset、rebase、amend、强制操作或任意 Git 子命令入口。

`git.integrate(source_agent_id)` 只能把同一 Conversation 成员的分支合并到调用者自己的分支。目标工作树必须干净；若启用 `require_user_approval`，模型不能自行构造批准能力。发生冲突时 Adapter 执行 merge abort，确认 HEAD 与工作区恢复，再把冲突文件摘要作为团队消息发送给来源 Agent。

Kernel 不硬编码 Integrator 或 Reviewer。谁可提交或合并由 Agent 提示词与工具权限决定，用户也可通过设置界面执行一次明确批准的合并。

## 前端与桌面边界

桌面端使用 Tauri 原生目录选择器；Web 端输入的是后端服务器可访问的本地路径，并不代表浏览器电脑目录。设置面板只展示分支、HEAD、dirty 和合并状态，所有路径归属与 Git 校验仍由后端完成。

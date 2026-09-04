# 本地 CLI MVP 实施记录

用户批准基线：2026-09-04。采用 Python 3.11、uv 独立安装、Windows 当前用户执行；每个实际目录首次信任，信任持久保存。会话按启动目录隔离，跨目录由用户显式添加。共享实现供 Web、桌面和 CLI 使用，不保留迁移后的旧导入别名。

## 阶段

| 阶段 | 交付 | 状态 |
| --- | --- | --- |
| M0 | 目标目录、职责、实施基线 | 完成 |
| M1 | 根级共享发行包、workspace、Kernel 导入边界 | 完成 |
| M2 | 公共执行循环、配置、信任、SQLite、会话历史及失败语义 | 进行中 |
| M3 | 文件、PowerShell、Git、进程生命周期 | 待实施 |
| M4 | 独立安装、真实/确定性验收、Web/桌面回归、文档 | 待实施 |

## 接口和验收约定

- 安装：`uv tool install --python 3.11 ".[cli]"`，命令 `agenthub`。
- 全局数据默认 `%USERPROFILE%\.agenthub`，可由 `AGENTHUB_HOME` 覆盖。
- `init`、`config show`、`doctor`、`trust add/remove`、`sessions`、`replay`；普通启动创建会话，`--continue`/`--resume` 继续，`--add-dir` 添加资源目录，`-p` 单次运行，`--json` 输出 JSONL。
- stdout 的 JSONL 不混入日志、提示或私有推理。退出码：0 成功，1 运行失败，2 配置/能力/信任错误，3 会话占用，130 取消。
- `ContextStore` 和 `RunJournal` 为唯一上下文/运行记录接口；失败不得转成成功，崩溃仅标记 `process_lost`，不自动重放副作用。
- Windows Job Object 用于管理进程生命周期，不提供文件/网络强隔离。任意 PowerShell 拥有当前用户权限。
- 文件工具验证目录和版本，64 KiB 输出限制；命令默认 120 秒且受 Run deadline 限制。
- 配置保存凭据引用；Windows 持久凭据采用当前用户 DPAPI；环境变量引用可用于自动化。

## 本期范围

迁移 execution、单 Agent scheduling、context、tools、workspaces、模型访问和观测记录的实际调用链，新增本地驱动及 CLI 宿主。MCP、Skill、Workflow、内容处理、外部 Agent 和高级多智能体权限治理仅标明归属。已有功能未触及时保持原实现。

## 验证记录

基线：`0b2d957`，原有未提交 `frontend/pnpm-workspace.yaml` 不属于本次工作。后续记录实际执行的检查，真实 Provider 未验证时不得以替身结果替代。

M1：内核、调度策略和依赖边界 16 项通过；F821/F822 静态检查通过。使用原后端锁文件保留依赖版本，仅增加 CLI 所需依赖。公共 AgentLoop 已迁出 Kernel，Web 产品规则由 `WebExecutionExtension` 注入。

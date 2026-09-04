# 本地 CLI MVP 实施记录

用户批准基线：2026-09-04。采用 Python 3.11、uv 独立安装、Windows 当前用户执行；每个实际目录首次信任，信任持久保存。会话按启动目录隔离，跨目录由用户显式添加。共享实现供 Web、桌面和 CLI 使用，不保留迁移后的旧导入别名。

## 阶段

| 阶段 | 交付 | 状态 |
| --- | --- | --- |
| M0 | 目标目录、职责、实施基线 | 完成 |
| M1 | 根级共享发行包、workspace、Kernel 导入边界 | 完成 |
| M2 | 公共执行循环、配置、信任、SQLite、会话历史及失败语义 | 确定性闭环完成，真实 Provider 待配置 |
| M3 | 文件、PowerShell、Git、进程生命周期 | 实现和确定性故障验收完成 |
| M4 | 独立安装、真实/确定性验收、Web/桌面回归、文档 | 实现及本地验收完成；真实 Provider、Docker 构建待外部条件 |

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

M2/M3：11 项本机功能测试通过（含实际 Windows 进程、PowerShell/Git、DPAPI、文件编码、SQLite CAS 和失败终态）。端到端测试经真实 SDK 连接本地 HTTP 替身，完成代码读取、hash 修改、PowerShell 断言、Git diff、退出续聊、目录隔离、显式跨目录和网络失败。替身不计为真实 Provider 验收。

## 最终实现与验收记录（2026-09-04）

实现提交：M0 `67db041`，M1 `4eaafdf`，M2/M3 主链 `8911f16`，M3 故障与边界 `35e9e8f`；M4 的打包、文档和验证入口与本文一同提交。实现过程的一次性搬迁脚本已移除，避免重复执行；可通过各阶段 Git 提交查看迁移。

| 验证 | 实际结果 | 证据/边界 |
| --- | --- | --- |
| Web/Runtime/Provider 回归 | 173 通过、7 跳过 | `var/web-cli-migration.xml`；Runtime 全组、Provider 单元、会话管理、聊天稳定性、WebSocket、desktop entry |
| 最终共享链与预算回归 | 71 通过、3 跳过 | `var/final-shared-cli.xml`；含 Kernel、Watchdog、Loop/Stepper、SQLite、Windows 本机工具；与上一组部分重叠，不累计为独立用例数 |
| 独立 wheel 安装 | 成功 | `uv tool install` 到 `var/cli-install-validation`，通过已安装 Python 在临时工作目录运行；无 FastAPI、SQLAlchemy、Redis、OCR、Office 依赖 |
| 已安装 CLI 完整链 | 3 通过 | `var/cli-install-validation/installed-cli.xml`；SDK/本地 HTTP 替身→真实文件/PowerShell/Git→Kernel；另含取消、崩溃、跨进程锁、并行会话 |
| 信息保护及信任 | 2 通过 | `var/cli-information-protection.xml`；已配置测试密钥及私有推理未出现在 JSONL、SQLite、replay 和日志；接受信任后重新打开数据库不再询问 |
| 桌面 sidecar | PyInstaller 编译和启动检查通过 | 新源码根和根锁纳入内容指纹；迁移、本地身份、健康检查。首次冒烟发现测试脚本只关闭 bootloader，已改为关闭整个测试进程树并复验 |
| 静态质量 | 通过 | 新共享/CLI/驱动/测试及 Web 扩展 Ruff；共享源码 F821/F822；`git diff --check` |
| Docker | 已修正构建路径，未运行镜像构建 | 当前 Docker Desktop Linux 引擎管道不存在；不把静态配置检查计为容器通过 |

构建产物：`dist/agenthub_system-0.1.0-py3-none-any.whl`。SHA-256：

```text
5da07a872bc3e3a046ba38899a7cf537cf8c23353b810fc2ab90f5986e358baf
```

本地 XML、构建日志、独立测试环境、临时项目和生成二进制位于 Git 忽略目录，未提交。复现入口为 [verify-cli-install.ps1](../../scripts/verify-cli-install.ps1)、[测试分组](../../scripts/test-groups.json)及[使用说明](../cli.md)。确定性 HTTP 服务只是可控 Provider 响应，不是远程模型质量验证。

### 已验证的故障语义

- 模型 HTTP 503 为 `failed/model_error`；Provider `finish_reason=length` 为 `failed/token_budget_exhausted`，保留已报告 usage。
- `FAILED/BLOCKED` 由 SingleAgentPolicy 提出失败，Kernel 提交唯一终态。CLI 只映射结果到退出码。
- SQLite 终态写入失败回滚终态事件；Kernel 返回失败。落盘失败无法保证保存失败本身，重启取得会话锁后把遗留 Run 标为 `process_lost`。
- 取消及强制退出实际清理父/子工具进程，已在外层 Job 宿主中验证。其他会话仍能执行；占用会话返回退出码 3。
- UTF-8 BOM/UTF-16/CRLF、中文空格路径、hash 冲突、Windows junction、超长搜索分页及过程输出截断均有针对性断言。
- 失败不自动重试工具副作用，不回滚用户文件；已提交历史用于下一 Run，完整工具明细保留在 Journal。

### 真实模型验收：尚未执行

| Provider | 状态 | 原因 |
| --- | --- | --- |
| OpenAI-compatible 自定义服务 | 未验证 | 未提供显式授权的 profile / 凭据引用 |
| DeepSeek | 未验证 | 未提供显式授权的 profile / 凭据引用 |

已加入 opt-in `tests/test_cli_live.py`，没有配置时跳过，当前自动 live 用例覆盖修复项目与重启续聊。取得显式配置后，仍应分别完成用户批准的八项真实服务验收，不能用上述替身结果填写“真实通过”：

1. 读取小项目并解释指定模块。
2. 修复预设错误、执行测试，并根据真实退出码报告。
3. 读取 Git diff 并准确解释修改。
4. 退出后续聊并完成后续修改。
5. 切换目录，确认会话隔离。
6. 显式添加第二目录并完成两个目录的任务。
7. 取消长命令，确认相关进程清理。
8. 模拟断连和进程崩溃，查询失败记录。

每次记录 Provider/profile（无密钥）、模型 ID、源码版本、任务 ID、终态/退出码、工具结果、实际或估算 usage 和未完成项。八项远程验收和 Docker 构建仍是 M4 的外部验收缺口；其余非 MVP 子系统迁移保持待办，不宣称整体架构已经拆完。

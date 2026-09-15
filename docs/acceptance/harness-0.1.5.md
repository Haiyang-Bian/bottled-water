# AgentHub 0.1.5 安装与真实服务验收

2026-09-07；源码标签 `agenthub-v0.1.5`，最终源码提交与逐版 SHA 见 `dist/harness-releases.json`。
验收 wheel：`agenthub_system-0.1.5-py3-none-any.whl`；SHA-256：`6249c7dc7136e167dd9ff3f7a5c99fd216dd349754c28f71b036d821c14ae610`。

实际安装：`agenthub 0.1.5`；Provider `deepseek`；模型 `deepseek-v4-flash`。
使用现有显式 `default` profile，凭据只解析使用。所有项目、Git 仓库和状态目录均为独立临时目录。

| 任务 | Run ID | 终态 / 原因 | 退出码 | 秒 | 已知输入 / 输出 token |
| --- | --- | --- | --- | --- | --- |
| inspect | `307f0c69-1eaf-4bd9-8393-60c6a71c0a89` | `completed/completed` | 0 | 18.51 | 16228 / 1264 |
| repair_and_test | `40333723-07f9-4307-acee-9be66d00403f` | `completed/completed` | 0 | 8.95 | 8724 / 604 |
| explain_diff | `14a5eb3c-340f-4c66-b1ba-b6309f755b8a` | `completed/completed` | 0 | 5.53 | 3807 / 209 |
| restart_followup | `ba514797-cddb-47f8-9dbd-7604adb748c5` | `completed/completed` | 0 | 13.48 | 18854 / 1081 |
| directory_isolation | `4f750799-b701-49f1-91ce-d6de134aae39` | `completed/completed` | 0 | 3.45 | 1297 / 79 |
| cross_directory | `03c1316d-f2e7-4df0-a88a-7610198bf7b3` | `completed/completed` | 0 | 5.14 | 4844 / 315 |
| cancel | `4e549d6c-f964-49be-a826-a2bb5bf427a3` | `cancelled/user_cancelled` | 130 | 3.97 | 1342 / 128 |
| cancel_continuation | `53b0a0a2-50df-4f5f-9930-a526feaab987` | `completed/completed` | 0 | 14.84 | 16249 / 1328 |
| crash | `0d84636a-f9d2-402f-92d1-fa0b26b83476` | `failed/process_lost` | 1 | 3.86 | 1343 / 112 |
| crash_continuation | `66968ebe-1ccc-4013-be16-9f4740d55b95` | `completed/completed` | 0 | 9.50 | 9278 / 792 |
| injected_transport_outage | `4ce8cc21-13fd-4a9b-bbd6-21fc069e7d7d` | `failed/model_timeout` | 1 | 4.75 | 0 / 0 |

工具开始、结果、退出码、计数和完整用量细分保存在 `var/harness-015-accepted.json`。
崩溃行用量来自最后一条已确认请求事件，仅为已知小计；取消或连接中断后的未报告消耗不能视为零。缓存 token 是输入的细分，不重复求和，也未估算费用。

## 确定性与安装证据

- 共享测试：53 通过、3 按独立入口跳过，`var/harness-015-final.xml`。
- 已安装 CLI：7 通过，`var/install-015-final.log`。
- Web / Runtime / 桌面入口：131 通过、1 缺少凭据跳过，`var/web-015.xml`。
- 从真实 0.1.0 wheel 创建旧状态并升级：1 通过，`var/upgrade-015.xml`；配置、DPAPI 引用、信任、历史保持，旧二进制拒绝 v2。
- 桌面 sidecar 构建、实际 Alembic 迁移及启动通过，`var/sidecar-015-final-build.log`、`var/sidecar-015-final-smoke.log`。

## 负结果与未执行项

- 首轮 DeepSeek 取消记录报告被模型自报告为 blocked；提示明确当前任务后，取消报告通过。另一次单纯描述崩溃记录仍被自报告为 failed。原始失败保留在 `var/live-015-deepseek.json` 和 `var/live-015-deepseek-recovery.json`，不改写为成功。最终通过的是带明确文件修复和测试目标的续接；不宣称所有描述性任务的语义判定已经可靠。
- 第二轮验收脚本把只读 PowerShell 核实误判为重放；已检查实际参数，仅执行读取与 Get-Process。修正后通过 PID 文件修改时间、命令内容和实际进程句柄验证，允许只读核实。前六项成功来自 `var/live-015-deepseek-final.json`，恢复与故障来自 `var/live-015-deepseek-repair-recovery.json`，均使用上述同一 wheel。
- 全共享测试首次有一项超过脚本 45 秒外层等待；未确认根因。外层期限改为覆盖产品 120 秒请求期限，最终独立安装复验通过；产品请求超时未放宽。
- 0.1.4 的 Web 迁移接在较早 revision 导致多 head，最终桌面验收发现后在 0.1.5 修正，并增加单 head 检查。0.1.4 的 SQL 功能回归不等同于部署迁移验收。
- 注入连接故障使用真实适配器和不可连接的本地地址，属于受控故障，不是 DeepSeek 服务发生中断的证据。
- OpenAI-compatible：无显式 profile，真实验收未执行；兼容 SDK 的本地 HTTP 替身测试另列，不能代替真实服务。
- Docker：客户端存在，但 Docker Desktop Linux engine 未运行，未执行构建或启动。

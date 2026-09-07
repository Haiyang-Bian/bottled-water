# Harness 0.1.1—0.1.5 实施与验收

用户批准计划：2026-09-07。保留用户配置、信任和会话，采用五个独立安装版本。

| 版本 | 内容 | 状态 |
| --- | --- | --- |
| 0.1.1 | 无隐式轮数限制、类型化停止、执行阶段与用量 | 实现、确定性及安装验收通过 |
| 0.1.2 | 源码发现、忽略规则、浅层与递归 | 实现、确定性及安装验收通过 |
| 0.1.3 | 运行中上下文预算、工具记录检索 | 实现、确定性及安装验收通过 |
| 0.1.4 | 续接、schema v2、原子完成提交 | 实现、确定性及安装验收通过 |
| 0.1.5 | 诊断、升级与完整安装验收 | 实现、安装、已配置 DeepSeek 任务与桌面验收通过；已知限制见下文 |

每版同步 Python 发行版本、后端精确依赖及根锁，保留客户端独立版本。每版执行针对性测试、Web 回归、独立 wheel 安装、Ruff 与 diff 检查，通过后提交并创建本地标签。不推送或上传发布。

## 0.1.0 外部失败基线

2026-09-05 的 DeepSeek 外部运行 `0af42b56-f839-4f36-81f3-b9e00900d917`：检查项目任务约 40 秒，执行 10 个工具轮次、30 次工具调用，其中 28 次成功。停止报告为 `tool_round_budget_exhausted`，最终 Run 被归为 `failed/agent_failed`。额外总结请求输出了原始 DSML 工具帧。

记录中的累计 usage 为 186,106 输入、2,127 输出 token；这是多次请求总量，不是上下文窗口或精确费用。文件发现包含大量 `.next`、`target` 条目。失败会话没有提交对话上下文，但原请求与工具事件已在 Journal 保存。

这是一条真实服务失败案例，不代表 Provider 完整验收通过，也不改写 2026-09-04 的历史验收结论。

## 默认行为

模型请求轮数默认无限制；`[execution].max_model_turns` 或 `--max-turns N|unlimited` 显式控制。最终答复也计一次模型请求。Run 默认 1,200 秒、500,000 累计 token；模型默认 120 秒，阶段期限受 Run 截止时间约束。预算耗尽不追加总结请求，不自动开新 Run。停止仍由 Kernel 提交，CLI 保留原退出码。

本地证据保存在 Git 忽略的 `var`；wheel 保存在 `dist`。真实服务只在显式 profile 启用时运行，未执行项单独记录。

## 0.1.1 验收

- 共享与 CLI：34 通过，`var/harness-011.xml`；最终停止语义补充复验 8 通过。
- 独立 wheel：4 通过，`var/install-011-final.log`；校验 wheel 内所有 Python 文件与当前源码逐字节一致。
- 首次安装验收一项在模型请求阶段、尚未收到本地 HTTP 请求时超过测试的 45 秒期限；其余 3 项通过。最终构建复验 4 项全部通过。保留 `var/install-011.log`，不把这次超时归为已确认的模型或看门狗缺陷。
- 桌面 sidecar：构建成功，迁移及单用户启动检查通过，见 `var/sidecar-011-build.log`、`var/sidecar-011-smoke.log`。
- wheel SHA-256：`19684f4bdc04e612e63402b635a93521bff62955a37b7c9fa9dc184a65e826db`。
- Web / Runtime / Provider / 桌面入口回归：170 通过、1 跳过，`var/web-011.xml`。真实 Provider 本版未调用，Docker 本版未构建。

## 0.1.2 验收

- 文件发现、原有本机边界及依赖隔离：28 通过，`var/harness-012.xml`。覆盖真实 Git 索引、生成目录中的已跟踪源码、嵌套忽略、取反、分页及授权。
- 已安装 CLI：4 通过，`var/install-012.log`；Web 正常聊天及取消：2 通过，`var/web-012.xml`。
- 新工作空间策略源码与根锁均位于现有 sidecar 内容指纹输入内；本版未重复构建桌面二进制。
- wheel SHA-256：`2ab47cc09ba5f8386599e4fccde9add9d5a6403b3b38afbc94cd0aa46fa98d7b`。
- 真实 Provider 与 Docker 本版未执行。

## 0.1.3 验收

- 上下文、执行限制与本机边界：35 通过，`var/harness-013.xml`；补充完整 CLI 回归 17 通过，`var/harness-013-cli.xml`。
- Web / Kernel / AgentLoop 回归：40 通过、1 项缺少真实凭据跳过，`var/web-013.xml`。
- 独立安装：4 通过，`var/install-013.log`。wheel SHA-256：`a28f7cc281f47841ab9afa1589f7eb6c3b72ea3dee0f2b4bc1841c209d31521b`。
- 每次请求同时计入系统提示、消息及工具 schema，默认限制 64,000 字符。完整历史轮次先裁剪，再缩减工具输出；当前请求超限明确失败。可配置模型窗口与输出预留，token 估算明确标识。
- 保存的工具结果可在同会话按引用分页检索；上下文缩减不改变 Journal。缓存用量不重复累计，取消后的未知消耗明确标记。
- sidecar 输入指纹包含新源码；真实 Provider、Docker 及桌面二进制重建本版未执行。

## 0.1.4 验收

- 共享系统：32 通过，`var/harness-014.xml`。覆盖失败后重启、游标去重、跨 scope 隔离、未知操作、三个事务故障边界和提交期间取消。
- SQLite v1 升级先取得迁移与会话锁，使用 backup API 包含已提交 WAL 内容，事务化创建续接元数据；锁忙与迁移回滚测试通过。
- 独立安装：4 通过，`var/install-014.log`，包括真实 Windows 进程取消及崩溃恢复。wheel SHA-256：`7ae832e168ab6fbc9ec234a87c656fb463e9f1fc62aea0d429f26514bc1c2134`。
- Kernel / 原有 SQL 存储回归 13 通过，`var/web-014-a.xml`；新增 SQL 原子回滚与团队宿主通过。初次聊天测试因测试组装仍混用内存上下文与 SQL Journal 失败，已同步改为 SQL 原子完成端口并复验。
- 最终 Web 聊天完成与取消复验 2 通过，`var/web-014-manager.xml`。
- sidecar 内容指纹已包含新源码和 Alembic 迁移，本版未重复构建二进制。真实 Provider 与 Docker 本版未执行。

0.1.5 桌面验收随后发现本版新增 Alembic 迁移接在较早 revision，造成多 head；在 0.1.5 修正并补充单 head 检查。本版 SQL 功能回归与 CLI 安装结果保持原结论，不能据此宣称本版 Web 部署迁移通过。

## 0.1.5 验收

- 共享系统 53 通过、3 按独立入口跳过，`var/harness-015-final.xml`；已安装 CLI 7 通过，`var/install-015-final.log`。
- Web / Runtime / 桌面入口 131 通过、1 缺少凭据跳过，`var/web-015.xml`。
- 使用真实 0.1.0 wheel 创建包含 DPAPI 凭据、信任、成功历史的旧状态，独立升级验收 1 通过，`var/upgrade-015.xml`。
- 最终 sidecar 构建及实际 Alembic 迁移、单用户启动通过，`var/sidecar-015-final-build.log`、`var/sidecar-015-final-smoke.log`。
- wheel SHA-256：`6249c7dc7136e167dd9ff3f7a5c99fd216dd349754c28f71b036d821c14ae610`。
- DeepSeek `deepseek-v4-flash`：真实项目检查、修复/测试、diff、重启续聊、目录隔离、跨目录读取通过；取消与崩溃后的修复/测试续接、进程清理及无重放检查通过。受控连接故障记录为 failed，可 replay 查询。
- 保留纯描述性恢复任务被模型自报告为 failed/blocked 的负结果，不宣称模型语义判断完全可靠；不将内核预算停止改判为成功。首次安装/全组测试的外层等待超时、验收脚本只读命令误判及迁移链问题均在[详细验收报告](../acceptance/harness-0.1.5.md)记录。
- OpenAI-compatible 没有配置真实 profile，未执行真实验收；Docker engine 未运行，未执行构建。

每个 wheel 的最终源码提交与校验值保存在 `dist/harness-releases.json`；`scripts/verify-release-history.py` 用不可变本地标签逐文件校验共享源码。安装和升级方法见 [CLI 文档](../cli.md)。

# AgentHub 0.2.3 原生使用闭环验收

2026-09-16。验收宿主为 Windows 11 25H2 x64，OS build **26200.9457**；Python 3.11。
本版验收普通用户模式。LPAC 强隔离暂停，旧 P3 的未完成项及失败证据不改写为通过。

## 产物与对应关系

- 共享源码提交：`24a415371edbb169bbda1483a15b4478c63ba519`。后续文档/测试提交不改变 wheel 内源码。
- 共享包与后端：`0.2.3`；本地 schema：`6`；前端/桌面客户端版本未变。
- wheel：`dist/agenthub_system-0.2.3-py3-none-any.whl`。
- SHA-256：`38f78a150c6ee147d826c1e794dd32a235f5d8c05931a325180feffc609b0880`。
- 配套文件：`dist/agenthub_system-0.2.3-py3-none-any.whl.sha256`、`dist/agenthub_system-0.2.3-acceptance.json`。
- 验收清单 SHA-256：`22d22a43b6bdcd8f4b4929db93c9a124d3a5afc33c50d155b17739613beb110e`。
- recorder 已逐文件核对 wheel、独立安装和上述 Git 提交的共享 Python 源码一致。
- 独立安装位于 `var/cli-install-validation`，命令在新建临时项目执行；未修改日常 `.agenthub` 或用户全局 tool 安装。

## 通过的检查

各组有重叠，不相加为无重复总数。

| 证据 | 结果 |
| --- | --- |
| `var\native-shared-release-20260916.xml` | 140 passed，0 skipped |
| `var\native-render-fix-20260916.xml` | 14 passed，0 skipped |
| `var\native-stage3-final-20260916.xml` | 44 passed，0 skipped |
| `var\native-preserved-boundary-20260916.xml` | 74 passed，0 skipped |
| `var\native-web-20260916.xml` | 39 passed，0 skipped |
| `var\cli-install-validation\installed-cli.xml` | 14 passed，0 skipped |
| `var\upgrade-0.1.0-to-0.2.3.xml` | 1 passed，0 skipped |
| `var\upgrade-0.1.7-to-0.2.3.xml` | 1 passed，0 skipped |
| `var\upgrade-0.2.0-to-0.2.3.xml` | 1 passed，0 skipped |
| `var\upgrade-0.2.1-to-0.2.3.xml` | 1 passed，0 skipped |
| `var\upgrade-0.2.2-to-0.2.3.xml` | 1 passed，0 skipped |

共享回归包括 Runtime、预算、上下文、会话、L2 记忆及遗忘、L3 资源、文件与进程。
针对性测试覆盖 user/workspace 分离、旧受限任务显式转换、提升宿主拒绝、信任入口停用，
以及中文/空格、BOM/CRLF、hash 冲突、网络与外部缓存、取消/崩溃后进程树清理。
74 项保留边界测试为纯策略、清单及适配器回归，不是 LPAC 原生安全认证。

五个旧 wheel 分别生成真实 v1–v5 状态，再使用已安装 0.2.3 升级。
配置、DPAPI 凭据、旧 trust、任务/历史、环境身份、L2 遗忘抑制以及 v5 资源记录保持；旧二进制拒绝新 schema。
原子迁移、WAL 备份、忙锁和失败回滚另在权限存储及事务组覆盖。

## 已安装 DeepSeek 真实闭环

显式来源 profile 为 `default`，Provider `deepseek`，模型 `deepseek-v4-flash`。
只读取该 profile 与凭据；新 home、资料 A、启动位置 B、项目 C 和缓存均位于独立临时树。
第一轮从 B 读取 A 的要求并修复 C，使用 C 的虚拟环境运行 unittest；使用本机 uv
从 PyPI 安装明确指定的 colorama 0.4.6，验证导入并读取 Git diff。
新缓存位于项目外，未创建 trust 或软件登记。模型先观察到测试失败，再修复为通过。
退出后从 A 用无 ID 列表恢复，回显历史；/cd 切至 C，重跑测试和 diff。
随后运行真实长命令并 Ctrl+C，OS 进程句柄确认父子进程退出，终端回到输入。
再次从其他目录使用 plain 恢复，读取保存位置 C 的文件，失败续接与已修复内容保持。

| Run | 状态 / 退出 | 模型 / 工具调用 | 已确认输入+输出 token |
| --- | --- | --- | --- |
| `01ce502e-d9a8-4f72-8be2-3a66a2532382` | completed / 0 | 9 / 13 | 61260 |
| `e93bb0df-5063-4726-892b-41fd645dff41` | completed / 交互任务；宿主正常退出 0 | 3 / 4 | 18322 |
| `03cdc4ca-3b4e-4c91-a768-7f4f1b76edcc` | cancelled / 交互取消；返回输入界面 | 1 / 1 | 5790 |
| `91de9770-317b-4054-affb-cdd2a4eb1402` | completed / 0 | 2 / 1 | 11572 |

实际 Run 开始记录 `current_user`、`file_access_scope=user`、`network=available`；
位置修订从 B/0 更新到 C/1。工具记录保存实际 Python、uv、Git 路径、cwd、退出码及文件 hash。
详细事实保存在 `var/native-live-final-20260916/first.jsonl` 及同目录每个 Run ID 的 replay JSONL。
缓存 token 是输入的细分，表中未重复累加。已保存日志和终端记录经过脱敏。

## 终端与桌面

- `var/native-terminal-final-20260916/acceptance.json`：真实 ConPTY，列表、窄屏、长代码滚动、只读工具详情、取消及正常退出。
- 已检查 `var/native-live-final-20260916/03-resumed-cwd.png`，最终正文不再重复。
- `var/native-terminal-final-20260916/05-narrow-selector.png` 等为捕获的 VT 单元格渲染，不是模拟模型界面的截图。
- plain/NO_COLOR/重定向/JSONL 的输出边界由展示与安装测试覆盖。
- `var/native-sidecar-build-final-20260916.log`：最终源码、根锁文件及后端输入完整构建；未提交二进制。
- `var/native-sidecar-smoke-20260916.log`：健康检查、SQLite 迁移、本机身份和认证检查通过，测试进程退出。

## 保留的失败与限制

1. 首轮源码验收开发环境仍装 0.2.2，诊断出现版本不一致；同步发行元数据后 140 项通过。
2. v5 资源升级初次断言使用大小写敏感路径比较；按 Windows normcase 修正后通过，原报告保留。
3. 首次 sidecar 经 pnpm 启动的 Windows PowerShell 缺少 Get-FileHash；改用实际发现的 PowerShell 7 执行原脚本后完整构建与启动通过。
4. 真实终端发现 Provider 在工具轮次间复用消息 ID，最终结果再次打印。24a4153 修正，14 项展示检查及最终真实终端复验通过。
上述失败的证据路径、哈希和修复说明见 `var/native-known-failures-20260916.json`，旧报告不覆盖。
一次最终实时复验被自动审批误判可能写日常状态；核查只读 profile/凭据路径及独立 home 后获准执行。

未执行：OpenAI-compatible（未配置明确 profile）、其他 OS build/平台、Docker、暂停的 LPAC 完整门槛。
普通用户运行仍不是文件或网络强隔离；本版没有目录黑名单或任意脚本防提权保证。
Web 的 39 项回归和桌面启动通过不代表这些未执行项目通过。

## 交付边界

继续使用 `codex/native-user-experience`，保留 P3 分支、提交及旧证据；没有预先合入未验收的 P3。
保留用户的 `frontend/pnpm-workspace.yaml` 和仅本地同事安装指南。
创建本地 `agenthub-v0.2.3` 和开发 PR；不自动合并、不上传包、不发布远程 Release。
安装与回退操作见[0.2.3 说明](../releases/0.2.3.md)。

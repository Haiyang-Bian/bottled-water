# 资源、软件与旧任务（0.2.3）

资源目录保存“知道什么在哪里”；普通用户任务可核实当前 OS 用户可访问的文件，无需逐目录授权。
元数据查询本身不扫描文件。资源信息是带来源的资料，不能替代对当前文件状态的验证。

## 本机调用与可选登记

原生任务可直接使用 `process.run`：程序、参数数组、cwd、timeout、outputs，无需软件 ID。
`software.discover` 列出项目虚拟环境、PATH 和 CLI 解释器并标明来源；不执行、登记或启用候选。
显式程序路径优先，PATH 名称解析后记录实际路径；WindowsApps 别名返回诊断。
软件登记仍作为固定版本/指纹调用方式保留，以下操作均可选。


```powershell
agenthub software discover --kind python
agenthub software discover --kind uv
agenthub software discover --kind git
agenthub software add --kind python --path 'C:\实际路径\python.exe' --name Python
agenthub software add --kind uv --path 'C:\实际路径\uv.exe'
agenthub software add --kind git --path 'C:\实际路径\git.exe'
agenthub software
```

发现仅查看显式路径、当前 `.venv`、CLI 解释器和 PATH；不执行、不启用候选。
`add` 是用户选择并授权验证的入口，通过 10 秒内的版本与文件指纹检查后启用。
WindowsApps 的 Python 入口会被拒绝，应选择实际解释器。交互终端中省略 `--path`
会打开候选列表；脚本必须提供路径和类型。软件探测属于独立管理操作，不创建聊天 Run。

模型使用 `software.search/read/run`。`run` 接收软件 ID、参数数组、可选 cwd、timeout、outputs，
通过现有进程驱动执行，默认 120 秒且不超过 Run 剩余时间。程序缺失、指纹变化或停用时拒绝执行，
不会改用 PATH 的另一程序。软件只是明确选择的可执行文件，当前用户权限执行仍不是 OS 沙箱。

```powershell
agenthub --json software list
agenthub software show SOFTWARE_ID
agenthub software disable SOFTWARE_ID --revision 2
agenthub software verify SOFTWARE_ID --revision 3
agenthub software enable SOFTWARE_ID --revision 4
```

`verify`/`enable` 都重新探测后提交新修订。首次验证失败的登记可用
`software verify ID --revision N --kind python` 重试。`software show` 提供版本、指纹、验证时间和启用状态。
取消会清理本次 Job 内进程，不回滚已发生的文件修改，也不自动重跑命令。

## 资源操作

```powershell
agenthub resources search '平方结果'
agenthub resources add --name '实验报告' --path '.\report.json' --kind artifact --alias '平方结果'
agenthub resources show RESOURCE_ID
agenthub resources edit RESOURCE_ID --revision 1 --name '新名称' --kind dataset
agenthub resources relocate RESOURCE_ID --revision 2 --path 'D:\获准位置\report.json'
agenthub resources verify RESOURCE_ID --revision 3
agenthub resources disable RESOURCE_ID --revision 3
agenthub resources enable RESOURCE_ID --revision 4
```

管理无需模型凭据。无 ID 选择仅在交互终端使用；脚本修改要求 ID 和当前修订号，冲突时重新读取。
`/resources` 可浏览列表与详情；`/resources add/edit/...` 使用同样参数。
查询页最多 20 项，`--offset` 和 `--limit` 控制分页。

支持 file、directory、project、dataset、artifact、software。自动登记仅产生普通 file/directory；
项目、数据集、产物等语义类别须用户明确指定。同一规范化实际路径复用记录，不覆盖用户名称和类别；
不同路径即使 hash 相同也不合并。移动须 `relocate`，复制登记为另一资源。逻辑 ID 不等于文件系统对象 ID。
停用保留历史来源，重新处理或重建索引不自动启用。

`verify` 和 `index` 使用当前用户的文件访问权限，不查询旧 trust 表。索引仅遍历用户指定范围，仍受忽略规则、数量与时间限制；不扫描整台电脑。
查询已保存元数据本身不要求来源目录仍存在，也不授予文件访问权。

```powershell
agenthub resources index 'D:\获准项目' --max-entries 10000 --timeout 30
agenthub resources index 'D:\获准项目' --include-ignored
agenthub resources process
agenthub resources rebuild
```

显式索引只记录元数据，复用 `.gitignore`、默认生成目录过滤和 Git 已跟踪文件例外；不读取业务文件正文。
Git 索引不可用时返回发现范围降级信息。链接和 junction 不递归遍历。
到达条目或时间限制返回 `partial`、范围和最后观察位置；调整限制重新运行，不宣称首次结果完整。
`rebuild` 仅重建已有记录的词项投影，不重新扫描磁盘。

文件读取、创建和修改的成功结构化记录自动进入终态 outbox；软件只对显式声明的 outputs
记录执行前后存在性、大小、mtime 和可取得的 hash。文件存在、hash 未变或退出码 0 都不能单独证明
产物正确或由本次命令生成。大于 64 MiB 的普通文件不自动计算 hash，标明 `size_limit`。
未知或失败检查保留在工具日志；取消而没有结果的操作在任务摘要中标为 unknown。
任意 stdout、文件正文、模型答复、list/search 命中不用于自动登记。

## 找回任务

```powershell
agenthub resume --query '昨天的实验'
agenthub resume --query '实验' --since 2026-09-01 --until 2026-09-15
```

交互时可输入 `/resume 昨天的实验` 或 `/resume --here 实验`。日期词支持今天、昨天、前天、最近 N 天，
按本机时区解释；显式结束日期包含当天。即使唯一候选，也由用户选择；取消保留原任务。
`-c` 和显式 ID 恢复规则不变，恢复后工具使用任务保存位置。

普通对话可通过 `task.search/read` 看有界摘要：原请求、停止原因、已确认操作、资源关联、未知操作；
旧助手答复明确标为助手陈述。不会切换任务或把旧任务全部历史接入当前任务。
详细工具结果的 `run.read_tool_result` 仍只允许当前会话。

每个 Run 初次检索最多 10 条、4000 字符相关资源，之后每次请求复核状态和修订。
资源与任务摘要先于 L2 记忆及本任务历史被裁剪，保持当前请求和工具配对；资料不写回成功历史。
`agent.resources_used` 记录实际发送的资源修订、观察引用、任务引用与裁剪信息，可通过 `replay RUN_ID` 查看。

## 升级与回退

0.2.3 使用 schema v6，保留环境、任务、信任、L2 记忆、候选及遗忘抑制。
退出使用同一状态目录的所有 CLI 后，安装 wheel，再执行 `agenthub state upgrade`。
v1–v5 直接升级，一次 backup API 备份、一个事务；锁忙返回 3，升级失败回滚并保留备份。
只读列表、历史、doctor 和旧库资源查询不自动升级；新资源写入可以触发升级。
旧二进制拒绝 v6。回退需匹配旧 wheel 和升级前 `.bak`，不会包含升级后新增的任务和资源。

发行证据见 [L3 验收记录](./acceptance/resource-continuity-0.2.2.md)。L4、多助手共享、全盘扫描、
常驻监视、GUI 自动化和强 OS 隔离不在 0.2.3 范围内。

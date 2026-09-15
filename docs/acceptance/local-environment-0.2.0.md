# AgentHub 0.2.0 L1 验收记录

验收日期：2026-09-15。范围是本机环境中的全局任务恢复、独立工作位置和 schema v3；L2–L4 尚未实现。开发顺序和状态所有权见 [L1 实施记录](../architecture/local-environment-l1.md)，操作与回退见 [CLI 说明](../cli.md)。

## 发行对应关系

- 发行代码提交：`4f5d8e3a0abba5f6fc69816130b2d3f6462450ae`。后续文档提交不改变 wheel 源码。
- wheel：`dist/agenthub_system-0.2.0-py3-none-any.whl`；独立安装显示 `agenthub 0.2.0`。
- SHA-256：`1aeeecf2809fa80e3fec2ae9d9ad0b767460a6a64b4db2a411f7944879b37d9a`。
- 源码、wheel 和隔离安装文件通过逐文件核对；机器可读证据由 `scripts/record-cli-release.py` 生成到 `dist/agenthub_system-0.2.0-acceptance.json`。
- 根包与后端精确依赖同步为 0.2.0，根 uv.lock 已刷新。前端/桌面发行版本保留；sidecar 嵌入 0.2.0 后端。
- 日常 `.agenthub` 没有升级；真实 profile 只读解析后在隔离进程内通过环境变量使用。测试项目与状态都独立于用户项目。

## 确定性、升级和安装验收

| 检查 | 结果 | 本地证据 |
| --- | --- | --- |
| 共享系统（最终源码） | 91 通过，1 跳过 | `var/l1-system-release.xml` |
| Web 聊天、取消、事务和桌面入口 | 18 通过，0 跳过 | `var/l1-web-release.xml` |
| 独立安装 CLI | 9 通过，0 跳过 | `var/cli-install-validation/installed-cli.xml` |
| 不同目录双 CLI 并行、锁、取消/崩溃 | 2 通过，0 跳过 | `var/l1-parallel-installed.xml` |
| 0.1.0/v1 → v3 | 1 通过，0 跳过 | `var/upgrade-0.1.0-to-0.2.0.xml` |
| 0.1.7/v2 → v3 | 1 通过，0 跳过 | `var/upgrade-0.1.7-to-0.2.0.xml` |
| Windows ConPTY | 通过 | `var/terminal-020-release/acceptance.json` |
| sidecar 完整构建 | 通过，新输入指纹 | `var/sidecar-020-final-build.log` |
| sidecar 启动、迁移和本机身份 | 通过 | `var/sidecar-020-final-smoke.log` |
| Ruff、diff 检查 | 通过 | 版本提交前执行 |

共享系统的一项跳过是需要显式旧/新 wheel 环境的升级用例，已通过上述两个独立安装升级执行补齐；两个 live Provider 用例没有混入此确定性分组。真实 DeepSeek 的结果单独列在下节。

覆盖内容包括：从 B 发现 A、空任务排除和稳定排序、目标锁占用、失败/取消候选、位置失效但历史可读、事务失败保留原任务和锁、运行中拒绝修改位置；模型请求包含原历史一次，新任务不带旧任务全文；cwd 与单次命令位置独立，真实文件及进程并发无全局 chdir；中文/空格、junction、授权拒绝、信任撤销与失效根保留。

身份测试覆盖不同 home、拥有者/机器不匹配拒绝读写、默认助手保留 local。v1/v2 只读查询不绑定、不生成备份；真实旧 wheel 创建的 profile、DPAPI 凭据、信任、Session ID、历史均保留，doctor 不迁移，显式升级使用一致性备份，旧二进制拒绝 v3。故障注入验证 WAL 备份、升级回滚、位置 CAS 与控制事件原子性。

ConPTY 使用真实 Windows 终端后端；图片渲染自捕获的 VT 单元格缓冲。已检查选择器、中文与空格路径、恢复后的历史、位置切换、长 Markdown 滚动、窄屏、工具详情和取消后的输入提示。普通、NO_COLOR、plain、非 TTY、JSON 及控制序列/凭据脱敏由对应逻辑和安装用例覆盖。取消测试取得实际子进程句柄，断言受管理进程退出。

## 真实 Provider

显式来源 profile：`default`；Provider：`deepseek`；模型：`deepseek-v4-flash`。测试 profile 使用同一模型参数，改为隔离凭据引用。六个 Run 均 completed，脚本进程与交互退出码为 0；总耗时 53.03 秒，累计 33185 token（Provider 实际 usage，缓存是输入细分，没有重复累加）。

| 任务 | Run ID | 位置修订 | 终态 / 退出码 | 工具次数 | 输入+输出 token |
| --- | --- | --- | --- | --- | --- |
| A 修复、测试及 diff | `a9883dc8-97bf-47fb-9b00-86cc4670b99f` | 0 | completed / 0 | 7 | 12931 |
| B 选择恢复 A | `1adfb1ed-04ec-4c96-9548-9ff4af502585` | 0 | completed / 0 | 2 | 4222 |
| 添加 C、/cd 后写入 | `c27a8a6a-37a0-4672-85d6-9d0bc7997e5c` | 2 | completed / 0 | 2 | 4775 |
| 重启 -c（plain） | `3484d4b0-c0a1-489f-aef6-2eb8a9321705` | 2 | completed / 0 | 1 | 4776 |
| 新 B 独立任务 | `eb56b297-fc96-463f-a73b-991448fa3551` | 0 | completed / 0 | 0 | 1353 |
| 失效位置修复到 D | `7661b4d6-523b-4441-b1ab-228ff8db7e2e` | 3 | completed / 0 | 1 | 5128 |

证据：`var/live-l1-020-confirmed/acceptance.json` 保存脱敏请求、Run 结果、位置修订、控制事件和工具结果；同目录保存 JSON/plain 输出及 ConPTY 截图。任务 A 经 no-ID 选择从 B 恢复，Git/PowerShell 实际在 A 执行；添加 C 再切换产生两个位置/授权修订，文件实际落在 C，重启保留位置。B 新任务使用不同 scope。C 改名失效后，恢复退出 2，没有新模型 Run；只读 history 正常，显式添加 D 和位置覆盖、用户请求明确新位置后恢复成功。

OpenAI-compatible **未执行**：重新核对配置，只存在 DeepSeek profile。没有用 HTTP 替身结果冒充真实 OpenAI-compatible 验收。Docker **未执行**；本轮没有修改其构建配置。没有发布远程 Release 或上传软件包。

## 发现的问题与证据边界

- 开发期目录筛选曾遗漏 Windows 大小写规范化，命令补全曾使 `/res` 歧义；已修复。原失败报告保留在 `var/l1-p3.xml`，最终回归全部通过。
- 工具结果元数据改为执行前解析位置，避免命令删除位置后再解析而误报。
- 首次真实验收发现 Run 请求保存了位置，但 Run-start JSONL 事件没有同样字段；已补齐并重新构建、安装及执行真实/终端验收。先前 `*-first` 证据保留，不用于最终 wheel 对应关系。
- 最终 wheel 的第一次真实运行在位置失效修复步骤出现模型阻塞：Run `7ff7b2df-6e8e-4101-b84e-6bbd7802ea57` 的启动位置为 D、修订 3，但模型仍读取旧 C 的绝对路径，被正确拒绝后返回 `failed/agent_blocked`、退出 1。原始证据保留在 `var/live-l1-020-release/11-repair-location.txt` 与 `acceptance.json`。之后明确告诉模型已迁移到 D、要求读取相对路径，再完整验收通过。未改变权限、未自动重新授权 C，也未将这次失败改写为通过。这表明准确传递 cwd 不能保证模型永远不沿用历史绝对路径；必要时用户需明确纠正。
- 应用层资源校验和环境身份绑定不提供文件/网络强隔离。PowerShell 使用当前用户权限，Job Object 只控制进程生命周期。
- 新任务仍没有跨任务长期记忆。位置恢复不等于崩溃原地续跑，失败副作用不会自动回放。
- 自动验收使用独立临时项目，不能代替在所有用户项目或终端主题上的长期使用；非 Windows 身份路径尚未做实机验收。

## 复现入口

```powershell
.\scripts\verify-cli-install.ps1
.\scripts\verify-cli-upgrade.ps1
.\.venv\Scripts\python.exe -B scripts/accept-local-environment.py `
  --source-home "$env:USERPROFILE\.agenthub" --profile default `
  --python var/cli-install-validation/tools/agenthub-system/Scripts/python.exe `
  --output var/new-l1-live-evidence
.\.venv\Scripts\python.exe -B scripts/accept-cli-terminal.py `
  --python var/cli-install-validation/tools/agenthub-system/Scripts/python.exe `
  --output var/new-terminal-evidence
```

真实验收会调用显式配置的 Provider；新的输出目录必须独立。原始数据库、凭据、日志、测试目录、wheel 和 sidecar 不纳入 Git；本报告保留版本、哈希、Run ID 及可复现入口。

# L4a / 0.2.3 目标版本：P1 原生实验记录

更新：2026-09-16。**当前：工具链基础矩阵及并行/生命周期原生子集通过，完整 P1 仍未通过。**
0.2.3 尚未交付。本记录包含原型成功与失败证据，不是安装验收或安全认证。
实施范围见[设计与阶段状态](../architecture/windows-permissions-l4a.md)。

## 基线与源码

- PR #28 原 head：`3b5c37622a6e8e57bc82e666b9a861d4e723b846`。
- 核对允许的合并方式、远程 head 及既有 L3 验收后，以 merge commit
  `36135cf5ceaa2151901554614bde98933406a466` 合入；确认 ancestry 及远程 tip 后清理本任务 L3 分支。
- 从最新 main 建立 `codex/windows-permissions-l4a`，原型源码提交：
  `774a9a184564592b0b487eaa1c9516365122bdd5`。
- 原型仅位于 `scripts`；共享包、后端和根锁仍为 0.2.2，SQLite schema 仍为 v5。
- 保留用户的 `frontend/pnpm-workspace.yaml`、其他分支和仅本地的
  `dist/agenthub-0.2.2-windows-guide.md`；没有升级日常 `.agenthub`。

实验主机：Windows 11 25H2，x64，build **26200.9457**；Python 3.11.15。
选定工具为 PowerShell 7.6.5、Git 2.55.0.windows.3、uv 0.11.21；另测试系统 PowerShell 5.1。
这些版本仅代表本机本次结果，不扩展到其他 OS build 或工具版本。

## 首轮保留的失败证据

证据保存在本地忽略目录，不提交原始日志、身份 SID、数据库或复制的程序。

| 文件 | SHA-256 |
| --- | --- |
| `var/l4a-lpac-probe-19/probe.json` | `95fe613214d14a0c409f016d8dd76d79b21daf3aa2b5cd60f27bc3e5d1de8755` |
| `var/l4a-checkpoint-tests.xml` | `36745fa57b21df398a7ace0b1a376c5b034c9e5610dd937ff21eb6a29c460b39` |
| `var/l4a-validator-final.xml` | `2ad18a85650d801ac67a84c48c74f32a1e4d8455969ce06aa464e331035b59d4` |

最终原生报告包含源码提交、四个实验文件各自的 SHA-256、每项程序退出码、LPAC 令牌检查、
耗时、输出、Job 清空状态、ACL 清理和 profile 删除状态。没有真实模型 Run ID，因为没有运行模型。
该脚本返回 **1**，`passed=false`，`p1_release_gate=not_passed`。

复现命令在源码根执行，输出目录须全新：

```powershell
.\.venv\Scripts\python.exe scripts/probe-windows-lpac.py `
  --output var/l4a-reproduce-01 `
  --toolchain --registry-read --full-toolchain --instrumentation
```

需要 Windows、pywin32、PATH 中的 pwsh/uv/Git 及当前真实 Python 基础运行时。
脚本复制运行组件到新夹具，只改变该夹具中 A、B、R 的 ACL；不改变已安装组件或系统对象 ACL。
R 为只读运行依赖，A 为只读业务目录，B 为可修改目录，C 不授权。
LPAC 实验使用当前非管理员 Windows 用户，没有修改系统对象权限。
Codex 对本机测试的沙箱外执行许可不等同于 Windows 提权。

为核实后续初始化条件，另发起一次 UAC 管理员只读预检：仅尝试以 READ_CONTROL / WRITE_DAC
打开 `\GLOBAL??`、`\GLOBAL??\D:`、`\GLOBAL??\MountPointManager`，读取权限并关闭句柄，
不调用权限修改 API。用户确认 UAC 后，已读取实际结果：三个对象均以状态 0 打开，
`elevated=true`、`acl_mutations=0`。目标结果文件为
`var/l4a-init-access-admin.json`。预检脚本 SHA-256 为
`abf7b274b0b58180279829946150291c917ef34bd5d12bcc563702c6c3cf27ec`。
该结果只证明本机管理员可取得所需句柄，后续实际修改与工具测试单独记录如下。

## 受控系统对象初始化与 Git 复测

使用独立能力 `AgentHub.Probe.Namespace.<随机 UUID>`，与业务目录的 LPAC package SID 分离。
管理员 helper 不启动程序，只为下面五个固定对象添加不继承的查询 ACE；普通用户宿主运行工具。

| 对象 | 掩码 | 目的 |
| --- | --- | --- |
| `\GLOBAL??` | `0x20003` | 查询和遍历命名空间 |
| `\GLOBAL??\C:`、`\GLOBAL??\D:` | `0x20001` | 查询选定盘符映射 |
| `\GLOBAL??\MountPointManager` | `0x20001` | 查询系统链接 |
| `\\.\MountPointManager` | `0x120089` | FILE_GENERIC_READ，用于卷名转换 |

只改 DACL，不修改所有者，不授权个人目录，不添加网络能力或使用通用 AppContainer SID。
所有句柄在首个修改前核实，意图先落盘；退出或十分钟超时仅移除精确匹配的本次 ACE。
如果本次 ACE 被其他写入者改动，拒绝猜测清理。没有创建服务、启动项或计划任务。
这是短时实验，尚未证明系统重启后的初始化、安装保护和崩溃修复；不视为正式 `sandbox setup`。

已执行 `scripts/probe-lpac-namespace.ps1`，实验编号 `a89eb72a4c73488288adcff743124737`：

- Git 读 A 返回 0，改 A 返回 255 并报告 Permission denied；改 B 返回 0；读 C 返回 128 并拒绝访问。
- Git diff 返回 0，真实输出包含 `-B-fixture` 和 `+powershell-fixture`。
- Python、其子进程、PowerShell 7 和 uv 的 A/B/C 矩阵仍通过；A 内容未变，B 修改核实存在。
- DOS/GUID 路径查询修复；IPv4/IPv6 TCP/UDP 回环仍被 OS 拒绝，宿主正对照成功。
- 工具宿主未提权；每个工具实际 LPAC 身份和 Job 清空核实通过；业务 ACE/profile 清理完成。
- 五个系统 ACE 均核实移除，`cleanup_errors=[]`；本次读回的每个 DACL 与修改前一致。
  清理使用当前 ACL 精确删项，并未用旧完整 DACL 恢复。
- 脚本退出 0、基础检查全部通过；`p1_release_gate=not_passed` 仍保留，因为对抗/生命周期组尚未补齐。
- 系统 PowerShell 5.1 仍超时，本结论仅支持本机 PowerShell 7.6.5，不将 5.1 标成可用。

| 新证据文件 | SHA-256 |
| --- | --- |
| `var/l4a-namespace-a89eb72a4c73488288adcff743124737.json` | `e8ad2292022a07b5c5121383a9e2a480375c0c3f09065de2829cca462e8969ad` |
| `var/l4a-lpac-namespace-a89eb72a4c73488288adcff743124737/probe.json` | `568a46efffc9796dd771c3487bbf142d205728a55b8b49e8cf18d929599b209b` |
| `var/l4a-namespace-tests.xml` | `436a0012ea259f1131cd6a4ea571ee0f10effb4012840d7fa7033bd3cef637d4` |

复测报告记录源文件哈希；helper 的 SHA-256 为
`ab6d2c40e229398f9babeb43e210b5541e44466e0a6c59512eb7d602d5eb6283`。
初始化清单及清理判定测试 27 项通过，其中包含原有 16 项，不能相加计算。

## 首轮已观察的结果（保留原结论）

| 检查 | 实际结果 | 可支持的结论 |
| --- | --- | --- |
| 挂起创建与令牌核实 | 恢复执行前检查 IsAppContainer、`WIN://NOALLAPPPKG` 和实际 package SID | 已核实本次进程为预期 LPAC；没有普通令牌回退 |
| cmd 直接文件操作 | 读 A / 写 B 为 0；写 A / 读 C 为 1；A 内容未变，B 内容正确 | 基本 OS 文件拒绝成立 |
| Python 文件操作和 Python 子进程 | 读 A、写 B 成功；写 A、读 C、读模拟宿主状态、改运行脚本均为 errno 13 | 本组父子进程受相同文件约束 |
| PowerShell 7.6.5 | Get-Content A、Set-Content B 成功；写 A、读 C 返回拒绝访问 HResult | 真实 PowerShell 文件命令通过此矩阵 |
| uv 离线调用 Python | 显式选定复制的解释器，父子文件矩阵均通过 | uv 可在本夹具中离线启动受限 Python |
| Git 版本检查 | `--version` 返回 0 | 只证明程序能够启动 |
| Git show/config | 在 A、B 均返回 128，提示无法读取工作目录 | **不通过**；不能将 Git 的负向操作错误计为权限通过 |
| 网络 | IPv4/IPv6 TCP/UDP 回环请求均返回 WinError 10013 | 本组套接字访问被 OS 拒绝；不是连接超时 |
| 宿主网络正对照 | 同一监听端点 TCP 建连与 UDP 收包均成功 | 排除端点不可用导致假阴性 |
| 超时进程与清理 | 系统 PowerShell 超时后 Job 终止；最终每个已启动进程的 Job ActiveProcesses 为 0 | 本组清理完成，不代表所有生命周期场景通过 |
| ACL/profile 清理 | 逐对象核实本次 SID 的 ACE 已移除，再删除 profile | 最终夹具清理核实完成；没有恢复旧完整 DACL |

网络组未覆盖外网端点、子进程套接字及全部网络 API，不能称为完整禁网验收。
模拟宿主状态读取被拒绝，不等同于真实凭据、继承句柄和宿主进程内存攻击已经测试。

## Git 阻塞的具体证据

在同一个获准 B 目录，Python 的 `os.getcwd()` 成功，但原生路径 API 呈现：

| API / 参数 | 返回 |
| --- | --- |
| `GetFinalPathNameByHandleW` / DOS（0） | WinError 5 |
| 同 API / Volume GUID（1） | WinError 5 |
| 同 API / NT（2） | 成功 |
| 同 API / 无卷名（4） | 成功 |
| 同 API / opened + DOS（8） | WinError 5 |
| `GetLongPathNameW` | WinError 5 |

Git for Windows 的 `mingw_getcwd` 先调用 DOS 形式，再回退到 GetLongPathName，两条路径均失败。
`git show` 和应当成功的 `git config` 在访问 Git 数据前已中止，因此不能计为完整工具链通过。
[Git 上游实现](https://github.com/git-for-windows/git/blob/main/compat/mingw.c)支持这一定位，
原生 API 结果来自本机实验。

[microsoft/mxc #694](https://github.com/microsoft/mxc/issues/694)报告相同的 DOS/NT 差异，列出了
`\GLOBAL??`、盘符符号链接、MountPointManager 链接及设备对象的访问需求。
本次重新读取 issue：2026-09-13 已关闭，但维护者说明涉及 Tier 3 弃用，不能把关闭解释成问题已修复。
上游列出的权限只是下一步验证候选，本项目没有据此直接修改这些系统对象。

只读 `inspect-lpac-namespace.py` 进一步确认本机相关 NT 对象的 DACL；
其保护和权限变更不能由业务目录授权过程替代。
下一门槛是审查最小系统对象初始化、补偿和重启有效性，然后重跑 Git 正负向测试。
现阶段没有采用普通令牌、全目录权限或全权限代理绕过此问题。

## 其他失败与修正记录

早期记录保留在 `var/l4a-lpac-probe-01` 至 `-18`，不覆盖原结果：

- 最初修正 pywin32 Job 参数、SID 比较及严格环境下的 CreateProcess 错误。
- 本机 TokenIsLessPrivilegedAppContainer 信息类查询返回不支持；改为查询实际令牌安全属性
  `WIN://NOALLAPPPKG`，并要求肯定结果，不能因为查询失败就跳过验证。
- 未添加 `registryRead` 时 Python 在启动阶段被拒绝；增加此明确能力后父子文件矩阵通过。
- PowerShell 的 ETW 初始化返回拒绝访问；显式测试 `lpacInstrumentation` 后 7.6.5 可用。
  5.1 仍超时，未将它标成受支持或用全权限方式回退。
- 初次 .cmd 测试受命令引用和启动兼容问题影响，改用明确 cmd 参数；早期错误不计为 OS 拒绝证据。
- 超时报告补充了保留输出及 Job 清空核实；Git 阴性判定要求正对照先成功，防止误报。

能力选择参考 [Microsoft LPAC 文档](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer)
及 [Chromium LPAC 能力定义](https://chromium.googlesource.com/chromium/src/+/refs/tags/128.0.6613.171/sandbox/policy/win/lpac_capability.h)。
令牌属性检测参考 [Google Project Zero NtToken](https://github.com/googleprojectzero/sandbox-attacksurface-analysis-tools/blob/main/NtCoreLib/NtToken.cs)。
引用只说明机制和定位依据，不扩展测试覆盖。

## 代码检查与未执行项

2026-09-16 增加普通用户侧 `scripts/probe-lpac-isolation.py`：每次新建夹具和两个独立 LPAC，
不提权、不改系统 ACL。最终从显式启用的 pytest 入口执行，27 项判定测试 + 1 项原生子集测试
共 **28 通过**（11.97 秒）；原生子集内部 13 个检查点通过。

| 本轮原生检查 | 结果与范围 |
| --- | --- |
| 两个并行身份 | 用单调时钟窗口确认执行重叠；各自目录读写成功，另一个身份目录读写为 errno 13 |
| 宿主进程访问 | PROCESS_VM_READ、DUP_HANDLE、CREATE_THREAD、TERMINATE 都为 WinError 5 |
| 显式句柄列表 | 宿主故意持有可继承的私有文件句柄；LPAC 访问该未列入句柄时报无效句柄 |
| 工具环境 | 宿主注入的测试用私有环境变量没有进入工具进程；没有使用真实凭据 |
| 取消与超时 | 确认子进程、孙进程活着后取消/超时；之前取得的进程句柄均变为已退出，Job 清空 |
| 宿主强制退出 | 终止普通测试宿主后，持有的子/孙进程句柄确认退出；没有仅按 PID 或时间推断 |
| 启动半途失败 | 注入 Job 分配失败及令牌验证失败；挂起进程均已终止，不恢复执行 |
| 子进程回环网络 | 父子 IPv4/IPv6 TCP/UDP 都为 WinError 10013；相同监听器的宿主正对照成功 |
| junction、硬链接、位置替换 | 通过受授权目录内的别名读取 C 均为 errno 13；子进程新建指向 C 的硬链接被拒绝 |
| 授权前检查与清理 | fixture walker 拒绝已有 junction/多硬链接；仅移除已知测试别名，C 未修改；两个身份及 ACE 清理核实 |

`var/l4a-isolation-04` 样本中，从测试取消信号到确认进程退出约 31 毫秒。这不是尚未实现的
权限撤销接口的延迟保证。部分宿主退出样本的计时为 0，表示低于当时的时钟分辨率，不表示瞬时终止。

| 证据 | SHA-256 |
| --- | --- |
| `var/l4a-isolation-04/report.json` | `5921d65968f9f4b70e0026cd7d04b82373581268271a572537d31fa85f9eb1ce` |
| `var/l4a-isolation-test-eec078607fa9450a9fe77d46e0b78db1/report.json` | `126881d9d8d63bde99d5b878b4eed30126f318f1a74bfcef9594d6f05e847658` |
| `var/l4a-p1-expanded-tests.xml` | `41c9de4016dcc7b53cb976d016bf6e1989e74c9a9ddd34b470cad26c63486fe9` |

原始失败保留：`l4a-isolation-01` 未处理 ctypes 的 STATUS_INVALID_HANDLE，导致探针提前退出；
修正仅接受明确无效句柄代码，其余异常仍失败。`l4a-isolation-03` 创建真实符号链接夹具时返回
WinError 1314。后续没有开启 Developer Mode 或提权，而是将该项明确记录为 `not_executed`。
真实符号链接、动态竞态和所有重解析标签的安全性不能由 junction 子集结果代替。

可从项目测试分组运行（必须明确启用本机原生操作）：

```powershell
$env:AGENTHUB_RUN_LPAC_NATIVE = '1'
.\scripts\run-tests.ps1 -Stack system -Module sandbox -Type integration
```

该组有意称为 subset，仍要求 `p1_release_gate=not_passed`，不能拿 pytest 成功代表完整 P1。
最小系统初始化已保存于提交 `46f5fff`；本轮新增探针与代码哈希随报告及后续源码提交保留。

开机初始化与保护设计见[待确认的安装行为](../architecture/windows-sandbox-initialization-review.md)。
当前没有安装计划任务、服务或开机组件，临时系统 ACE 已移除。

先前检查（与新增测试重叠，不相加）：

- P0 额外 L1/L3 针对性回归：15 通过。
- 新判定测试与同组回归：31 通过（16 新增 + 15 已有）。
- 最终 Git 假阳性修正后的判定测试：16 通过。这些与上一组重叠，不相加当作独立覆盖数。
- 相关 Ruff 与 `git diff --check` 通过。判定测试是实验判定逻辑测试，不能代替原生权限测试。

**未执行 / 未实现：**真实符号链接、完整路径竞态与继承句柄攻击覆盖、嵌套桌面 Job、外网/全部网络 API、
重启初始化与受保护安装组件、ACL 清理失败/崩溃 repair、环境授权与撤销、完成事务竞争、文件 worker、v6 迁移、
真实 DeepSeek/OpenAI-compatible、ConPTY、独立 wheel 安装、Web 和 sidecar 的 0.2.3 回归。
既有 L3 的通过记录不能转为这些检查的通过结果。

没有创建 `agenthub-v0.2.3` 标签、0.2.3 wheel、远程 Release 或新版本 PR。
保留本地原型提交和证据，先解决 P1 门槛，再进入公共执行链和发行工作。

## 长期授权修订后的追加记录

2026-09-16 的[长期授权与保护区试验](standing-permissions-p1b-0.2.3.md)扩展了业务 capability
与内部保护区域。原定保持继承方式的尝试出现真实越权；显式继承屏障只有夹具子集通过，尚未作为产品采用。
上述历史通过项保持原含义，不能转为新权限模型通过。P1b 及 0.2.3 发行门槛仍未满足。

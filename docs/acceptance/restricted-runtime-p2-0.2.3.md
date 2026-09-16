# L4a P2：受限 Runtime 接入与阶段证据

2026-09-16。分支 `codex/windows-permissions-l4a`；公共适配器检查点 `ec2aa25`。
发行保持 0.2.2/schema v5。本阶段通过专用验收入口注入驱动，普通 CLI 的默认模式不变。

**P1b 有限清单已通过；P2 Python、PowerShell 工具循环和故障子集通过，完整软件链及真实模型尚未放行。**
本记录不替代[完整 P1b 证据](standing-permissions-completion-0.2.3.md)，也不宣称 P3/P4 完成。

## 实现边界

- 原 `scripts/lpac_probe/native.py` 和 `jobs.py` 已迁至本机适配器；探针与受限驱动使用同一启动原语。
- `FileOperationsPort` 覆盖 resolve、stat/hash、读写、发现与产物核实；受限路径解析在 worker 内完成。
- 每次操作启动一个 worker。长度前缀 UTF-8 JSON：请求 32 MiB、响应 1 MiB，拒绝重复字段、
  非有限数、错误协议版本、请求 ID 和不完整响应；不接受 pickle 或宿主回调。
- Python/Pwsh/Git/uv 使用受控副本映射和参数数组，软件指纹、Git 索引及输出检查在受限侧执行。
  缺少副本返回 `software_incompatible`；不查询宿主同名软件代替。
- 子进程继承已核实的 LPAC 令牌及 Job；每个 Run 有父 Job，每次操作有命令 Job，无 breakaway。
  [Microsoft 嵌套 Job 文档](https://learn.microsoft.com/en-us/windows/win32/procthread/nested-jobs)
  规定关联须从外层到内层，驱动按此顺序执行并核实实际成员关系。
- `run_turn(execution=...)` 注入文件、进程、授权与软件入口；缺失受控端口拒绝启动。
  同一 RuntimeEngine/SingleAgentPolicy/AgentLoopExecutor 执行，未复制执行循环。
- 执行器交回结果前排空执行组；普通越权和命令超时可由模型处理，协议/隔离/租约失败提交具体原因。
  Run 开始记录模式、策略和依赖摘要，不把 SID、句柄或凭据放入模型输入。
- Run 私有准备先写 profile/宿主/Run 意图，再应用自有 ACE；清理只删除当前 ACL 中的自有条目。
  后处理清理失败记为 `repair_required` 并提示，不倒改已经提交的终态。
- 100 ms 进程内租约检查只在活动 Run 中运行。持久化权限管理、跨宿主撤销、完成事务内的
  策略复核、正式业务目录准备与修复入口留 P3。

## 可重复入口

以下命令均使用新建仓库内 NTFS 夹具；输出目录必须不存在。请以普通用户执行。

```powershell
.venv\Scripts\python.exe scripts/probe-restricted-runtime.py --output var/l4a-runtime-NEW
.venv\Scripts\python.exe scripts/probe-restricted-runtime.py --powershell --output var/l4a-runtime-pwsh-NEW
.venv\Scripts\python.exe scripts/probe-restricted-failures.py --output var/l4a-faults-NEW
$env:AGENTHUB_RUN_LPAC_NATIVE = '1'
.venv\Scripts\python.exe -m pytest tests/test_restricted_runtime_native.py tests/test_restricted_boundary.py
```

完整软件链依赖同一固定五对象初始化。以下入口先跑确定性工具链，成功后才执行显式 profile：

```powershell
.\scripts\probe-lpac-namespace.ps1 -Probe runtime `
  -LiveProfile default -SourceHome "$env:USERPROFILE\.agenthub"
```

管理员步骤仅准备固定系统查询 ACE，最长 600 秒；业务工具始终普通用户 LPAC。
不安装开机项、不重启。只读使用源配置/凭据，隔离数据库位于新夹具，不升级日常状态。
省略 LiveProfile/SourceHome 不调用模型。初始化清理报告必须另行核对为 `cleaned`。

`--powershell` 单独验证 PowerShell 和 Python，不触发管理员初始化或模型请求；仍保留
`full_toolchain_requires_initialization` 未执行标记。可用于先修复普通接入缺陷，减少完整批次的 UAC。
当前固定系统初始化是有界实验，每批结束撤销自己的 ACE，不能当作正式安装器的常驻准备。
Codex 请求在开发沙箱外执行原生测试，与 Windows UAC 是两个不同层次；普通 LPAC 测试不提权。

## 已通过的实际受限循环

Windows 11 x64，build 26200。最终原生子集：

- 报告：`var/l4a-runtime-native-fd64f76dc47a4ddeb74a8cbd6803b44c/report.json`
- SHA-256：`4879297a17ff47af313b41b952123c117ac0db9e9ccfd7541b1c863e9d8f563b`
- Run：`6d63f3f0-2f1c-4f07-9639-2c4099cdb68e`；completed/0；清理核实。
- 读取 Archive，在 Work 修复 calc、运行真实 Python 断言并检查产物，执行列表和搜索；
  Private 文件工具拒绝后继续，Python 原生读取 Private/写 Archive 得到 Windows 拒绝。
- 资源 verify、软件 fingerprint、输出前后检查均经过 worker。拦截宿主业务树 open/stat/lstat/
  listdir/scandir，`host_bypasses=[]`。此拦截检查补充 OS 测试，不作为独立沙箱证明。
- UTF-8 BOM、CRLF、中文空格路径及外部 hash 冲突检查通过。上下文仅在成功时增加一版。
- 13 次确定性模型请求、12 次工具调用；用量是夹具返回值，不能作为真实模型费用。

每份报告记录运行时源码文件 SHA-256、依赖清单摘要、策略摘要和 OS build。
报告生成时存在尚未提交的宿主接入代码，应以 `source_files` 核对，不能只用 `source_commit`。

## 故障、取消和终态

报告：`var/l4a-runtime-faults-native-d48c2c3872694a9bafcc9f23f189b653/report.json`。
SHA-256：`cdaddc9f033337522072e2f7b85cabc33dd15d835fde67c8437e50fb8de72b41`。

| 场景 | 结果 | Run |
| --- | --- | --- |
| 命令超时，模型继续 | 工具 `process_timeout`，Run completed/0 | `0f728554-608c-4bd7-8232-69a1c3287e24` |
| 实际 SIGINT 取消 | cancelled/user_cancelled/130 | `72d543b8-299f-48c7-af7c-9d476d613cb4` |
| 长命令期间租约失效 | failed/permission_lease_invalid/1 | `1aa12c11-0f5c-4f3d-9e85-cda2bbdc997a` |
| 注入损坏响应 | failed/worker_protocol_error/1 | `eb28e0de-32a6-4b3c-acef-4afacd6308ff` |
| worker 实际提前退出 77 | failed/worker_protocol_error/1 | `013cf70c-e701-4573-99ff-ee283f633fb8` |
| 注入清理未确认 | failed/isolation_cleanup_unconfirmed/1 | `e466e6ee-48f6-4806-8bf4-290bc2188a1f` |
| 模型返回与租约失效竞争 | failed/permission_lease_invalid/1 | `6a92c1af-f143-4b57-bea0-c8595d6b7a74` |

每例只有一个终态；失败/取消不提交成功上下文。前三例核对真实子进程及孙进程已退出。
所有例子 Run profile、Job、私有/依赖 ACE、业务准备均核实清理；同一 PreparedPolicy 服务七个 Run。
故障注入与真实操作在报告中分开标记，未将注入结果冒充自然出现的 OS 故障。

## 保留的失败与修复

1. `l4a-runtime-first-01`：软件登记使用未规范化大小写路径，测试夹具 CAS/路径校验失败，修正后继续。
2. `l4a-runtime-first-02/-03`：worker 导入 asyncio 时 `_overlapped` 遭 WinError 10013；Run 正确失败。
   worker 改为单操作、即时检查点，不导入网络事件循环；没有放宽禁网策略。
3. `l4a-runtime-faults-01`：重复登记用旧资源修订号，夹具被 CAS 拒绝，修正读取当前修订后重跑。
4. `l4a-runtime-faults-02`：租约失败覆盖取消异常，Actor 回到邮箱等待，Run 未结束。
   修复 Actor 在执行前后核对共享取消状态，并增加确定性回归。停止仅属本测试的宿主后，核实
   目录对象、唯一 profile 与已登记业务 SID，删除自有 ACE；`repair.json` 保存核实结果，未恢复旧完整 DACL。
   同时补齐 profile 创建前意图和清理审计，避免新 Run 缺少可恢复身份。原失败报告不改写为通过。
5. `l4a-runtime-namespace-9dce4d06c0a94ee5bf2e9ade3ef3365f`：此前记录等待 UAC，之后已确认并执行。
   Runtime 组装遗漏 P1 已验证的 `lpacInstrumentation` 能力，PowerShell ETW 初始化拒绝。
   Run `0201f498-4656-4aa3-9833-aff4de1c9e63` 失败；修复为仅包含 PowerShell 副本时传入该能力。
   此能力不增加业务目录授权。该批次 Run 与五个系统对象均完成清理。
6. 用户明确要求再次申请后，批次 `6cb98da21aa3435fabeb663f48526f02` 初始化成功。
   PowerShell 起始位置错误导致查找 `C:\calc.py`，Run `d14751d6-8452-48fa-9a36-502fe779f857`
   失败；私有 PowerShell 缓存路径超过 MAX_PATH，又导致 Run ACE 清理失败。
   五个系统对象已正常清理，Run 记录保留 `repair_required`。确认原宿主退出、目录对象身份
   与审计一致后，使用扩展绝对路径遍历，移除该 Run/准备实例的专属 ACE 并删除其 profile；
   独立 `repair.json` 记录清理核实，保留原失败报告。没有恢复完整旧 DACL。
7. 不再触发 UAC，改用 PowerShell 子集。批次 `a24b62a6da7c4915a56ba05eb9ef3500`
   证明 `-WorkingDirectory` 仍尝试检查未授权的祖先目录，不能直接作为修复。
   改用临时 `AgentHub:` PSDrive，根为 worker 已校验的 cwd，不向祖先授予访问。
   [Microsoft New-PSDrive 文档](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/new-psdrive)
   描述了以指定目录建立临时 PowerShell 工作盘的机制。对外提供真实路径的是
   `(Get-Location).ProviderPath`；子进程继续使用实际文件系统 cwd。
8. 批次 `11cc3839fa7749bd9e31b3b5a72d6911` 中 PowerShell 返回 0，但缺少预期的 Python
   子进程输出；验收检查判失败，未把退出码独立当作执行证据。隔离环境补充固定 PATHEXT
   白名单后通过下一节的子进程断言。以上两次非 UAC 失败均已核实清理。

## PowerShell 修复后的原生子集

- 报告：`var/l4a-runtime-powershell-native-3b5f0897e66942ebaacbeb8286960120/report.json`。
- SHA-256：`1e99b9646557d8473b58d427fd16b760f4cc1ab522e712f48cd24c4b8b45d4d3`。
- Run：`988534da-6c4e-4ab4-98a8-12cc2ec8ec9a`；completed/0；15 次确定性请求、14 次工具调用。
- Python/文件/资源权限循环、BOM/CRLF 和 hash 冲突继续通过；PowerShell 正确相对读取 calc.py。
  指定含中文、空格、单引号的子目录后，PowerShell 相对读取与 Python 子进程断言均通过。
- `host_bypasses=[]`、唯一终态、Run 私有/依赖 ACE、profile 及业务准备清理全部核实。
- OS build 26200，source_commit 为 `e4d4199`；实际修复源码由报告 `source_files` 逐文件固定。
  后续工具说明文字调整不改变上述执行证据；此批次没有调用 Git、uv 或真实模型。
- `var/l4a-p2-powershell-final.xml`：1 项原生参数场景通过，23.76 秒；
  `var/l4a-p2-powershell-regression.xml`：36 项 current-user、组装、协议/终态回归通过。
  Ruff 与 `git diff --check` 通过。原测试分组已包含此原生测试文件，新增参数场景沿用该分组。

## 回归和未完成

- 共享 CLI/Runtime/上下文/L2/L3/原 current-user：135 项通过，`var/l4a-p2-system.xml`。
- 协议/准备/原型/Actor 取消边界：114 项通过，`var/l4a-p2-adapters.xml`。
- 最终真实 LPAC Runtime + 故障 + 协议：22 项通过，59.27 秒，`var/l4a-p2-native.xml`。
  各组有重叠，不相加为不同测试的总数。
- 最后宿主接入及当前用户路径回归：21 项通过，`var/l4a-p2-assembly.xml`，含缺失端口、
  跨环境策略在调用模型前拒绝；与共享组重叠，不相加。
- Web 聊天、取消、持久化及桌面入口：49 项通过，732.35 秒，`var/l4a-p2-web.xml`。
- 桌面打包输入只读核查包含新共享 worker、适配器及根锁文件；完整构建留 P4。
- P2 完整 Python/Pwsh7/Git/uv：上述两个批次都在 PowerShell 接入检查处失败，不能宣称通过。
  对应 `var/l4a-namespace-<批次ID>.json` 均为 `cleaned`、`cleanup_errors=[]`。
  非 UAC PowerShell 修复子集通过后，尚未重跑完整批次。不存在仍等待本次确认的 UAC 请求。
  DeepSeek 入口由确定性检查成功后才启动，因此这两批都未调用真实模型。
  已确认唯一 profile 为 default/deepseek/deepseek-v4-flash；OpenAI-compatible 未配置、未执行。
- P3/P4、schema v6、wheel/tag/Release 均未进行。P2 完整验收结束前不推进正式权限管理。

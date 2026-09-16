# L4a P2：受限 Runtime 接入与阶段证据

2026-09-16。分支 `codex/windows-permissions-l4a`；公共适配器检查点 `ec2aa25`。
发行保持 0.2.2/schema v5。本阶段通过专用验收入口注入驱动，普通 CLI 的默认模式不变。

**P1b 有限清单及 P2 专用入口验收已通过：完整工具链和已配置的 DeepSeek 均完成真实任务。**
下文保留首轮真实失败及打印错误，不将失败记录追溯改为通过。正式权限 CLI、持久化与发行仍待 P3/P4。
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

## 首轮完整链与修复

2026-09-16 后续批次 `9932f9be970d44b8abaa194b8767ca21`（源码 `ef6b70c`）：

- 确定性完整链通过：Run `d918581d-ecff-4a66-b333-4cb1e9bc329c`，completed/0，
  17 次请求、16 次工具调用；Python、PowerShell、Git、uv、拒绝、无宿主绕过与清理检查通过。
  报告 `var/l4a-runtime-namespace-9932f9be970d44b8abaa194b8767ca21/report.json`，
  SHA-256 `55f8a2cf1e28e030deba7fe0d5ec91fa168d22cfbae4cedf57dfa7ab6446c3b1`。
- 首次 DeepSeek 实际执行**验收失败**：Run `0d8b9f0f-abda-422e-9f6d-d39a6d3d0aaf`，
  9 次请求、21 次工具调用，67.66 秒；输入 69,934、输出 3,589、输入缓存 52,864 token。
  缓存是输入的细分，不重复计数。文件修改、Python 断言、产物、PowerShell 和 Private 原生拒绝成功。
  模型将 Git cwd 设为未授权父目录，uv 未禁用配置发现而读取祖先 pyproject.toml 被 OS 拒绝。
  模型诚实列出未完成项，但 Run 为 completed/0；独立任务验收仍为失败，不能以 Run 状态代替任务证据。
  报告 `var/l4a-runtime-namespace-9932f9be970d44b8abaa194b8767ca21-live/report.json`，
  SHA-256 `a473c0a74466a9807f98d190d123dfcbc34882daad3c6997946b0e7553668fed`。
- 两个 Run 清理核实；固定五对象报告为 cleaned、host_requested、cleanup_errors=[]，
  五处前后 DACL 相同。初始化报告 SHA-256
  `8d410bcb508202aede0ae696b921327953709aedc42898f85a6855515d563dc1`。
- 修复：受限环境设置 UV_NO_CONFIG、UV_NO_MANAGED_PYTHON，并以 UV_PYTHON 选择已登记隔离副本；
  不扩大祖先目录权限。[uv 官方环境变量文档](https://docs.astral.sh/uv/reference/environment/)
  说明 UV_NO_CONFIG 禁用当前、祖先与用户目录的配置发现。上下文说明独立脚本应使用
  `uv run --offline --no-project -- python ...`；权限错误补充实际目标与 cwd。
  验收请求澄清 Work 就是 Git 根，避免把夹具父目录误当仓库；没有提供修复代码答案。
- 无 UAC uv 子集通过：`var/l4a-runtime-uv-native-2c3262105f644f7f94fc387e28103461/report.json`，
  SHA-256 `4dec243370bf12a6ac96116bf81b9db6ad377a041253777c6b3785e68b552439`，
  Run `3b3f4d8b-3d60-406e-a141-5337db868a09`，completed/0，清理核实。
  命令不再手工带 `--no-config` 或解释器路径，实测验证驱动默认行为。
  `var/l4a-p2-uv-regression.xml` 中 41 项工具、上下文、组装与终态回归通过。
  新入口：`probe-restricted-runtime.py --uv --output var/NEW`，不需要管理员初始化。

## 最终 P2 验收

批次 `8cf5e29326204aab81676cbf45bbe4c1`，源码 `a53ee88`，Windows 11 x64 build 26200。
入口为前述 `probe-lpac-namespace.ps1 -Probe runtime -LiveProfile default ...`，使用新的隔离状态。
未修改日常 `.agenthub`。真实 Provider 为 default/deepseek/deepseek-v4-flash。

| 执行 | Run | 结果 | 请求 / 工具 | 耗时 |
| --- | --- | --- | --- | --- |
| 确定性 Provider + 真实受限工具 | `ab2d57dd-9504-43e9-8ef5-25ff8ac10ace` | completed / 0；独立验收通过 | 17 / 16 | 28.34 秒 |
| DeepSeek + 真实受限工具 | `8265ab0e-178f-4421-bbf6-ac3c31f0991f` | completed / 0；独立验收通过 | 6 / 12 | 71.62 秒 |

DeepSeek 实际读取 Archive、以 hash 前置条件修复 Work/calc.py、保留 BOM/CRLF，使用登记 Python
通过 add(2,3)==5 断言并生成 output.txt，检查实际产物。随后 Private 文件工具拒绝、Python
open 得到 PermissionError，任务继续；resource.verify、PowerShell 读取、真实 git diff、uv
离线断言均成功。模型答复与保存工具结果分别核对，未以“模型声称遵守权限”代替原生证据。

真实用量：输入 39,959、输出 2,094 token；输入缓存 26,624 属于输入的细分。没有估算费用，
也不以这两次不同轨迹比较推广 token 节省比例。两个 Run 均只有一个成功终态、上下文版本 1，
`host_bypasses=[]`；所有 Run profile、Job、私有/依赖 ACE、业务准备都完成清理。
固定系统初始化为 cleaned/host_requested，cleanup_errors=[]，五个对象前后 DACL 相同。

证据路径及 SHA-256：

- 确定性报告 `var/l4a-runtime-namespace-8cf5e29326204aab81676cbf45bbe4c1/report.json`：
  `d79f1e228fe4b15beb251ab50b200063abedc797b0cd715d4daff76cbcfca082`。
- 真实报告 `var/l4a-runtime-namespace-8cf5e29326204aab81676cbf45bbe4c1-live/report.json`：
  `03bdf476ebb19d333392a2584dbec666d7eb5e25ad5d1f15a578f6c00a71c2e2`。
- 系统清理 `var/l4a-namespace-8cf5e29326204aab81676cbf45bbe4c1.json`：
  `983c52294cbdfb886e948b531e628c0ef8fa912fafde0962d4875af7f47882f9`。
- 独立汇总 `var/l4a-p2-final-evidence.json`：
  `7cfb9e76db5123991df51093c19dcd250ee065b5206066583442b412d08c4ed1`。
  该汇总还记录各 Run 的策略/依赖摘要及四个隔离可执行文件 SHA-256；原报告保持不变。

**报告打印故障单独保留：**真实 Run、检查和清理已完成，UTF-8 report.json 已保存，
但原验收进程在最后向 GBK 管道打印模型的 emoji 时抛 UnicodeEncodeError，外层进程退出码为 1。
它与表中的 Runtime 退出码 0 分开记录。已将摘要改为脱敏后 ASCII JSON 转义；
`var/l4a-p2-gbk-reporting.xml` 的三项真实 GBK 子进程测试验证中文/emoji、脱敏与成功/失败退出码。
使用原真实报告重新打印，`var/l4a-p2-gbk-rerender.json` 可正确解码且打印进程退出 0。
没有为打印问题重跑模型、工具或 UAC，没有改写原报告或把原进程退出码记作 0。
正式 CLI 已有 UTF-8 输出配置，本修复只涉及验收脚本。

## 回归与后续边界

以下前序回归仍保留各自执行时的证据；本轮新增 41 项回归和 3 项编码回归，不冒充重新执行全部宿主测试：

- 共享 CLI/Runtime/上下文/L2/L3/原 current-user：135 项通过，`var/l4a-p2-system.xml`。
- 协议/准备/原型/Actor 取消边界：114 项通过，`var/l4a-p2-adapters.xml`。
- 最终真实 LPAC Runtime + 故障 + 协议：22 项通过，59.27 秒，`var/l4a-p2-native.xml`。
  各组有重叠，不相加为不同测试的总数。
- 最后宿主接入及当前用户路径回归：21 项通过，`var/l4a-p2-assembly.xml`，含缺失端口、
  跨环境策略在调用模型前拒绝；与共享组重叠，不相加。
- Web 聊天、取消、持久化及桌面入口：49 项通过，732.35 秒，`var/l4a-p2-web.xml`。
- 本轮修改局限于本机适配器、CLI 上下文及验收脚本，没有新增共享 Kernel 接口变更；Web 不引用这些本机适配器。
- 桌面打包输入最终只读核查为 431 项，包含受限 worker、启动适配器及根锁文件；
  `var/l4a-p2-sidecar-inputs-final.json` 的指纹为
  `62A45900A0B90A91F8A3D345141422FB1596A2ACF3D6DF43F16DC0DF05F1DB06`。
  完整构建仍留 P4，未把输入核查当成构建成功。
- OpenAI-compatible 未配置、未执行；其他 OS build、任意本机软件及项目虚拟环境未验收。
- P2 仅通过专用入口注入驱动。正式 CLI 默认 current-user 行为不变，权限准备仍只在本进程复用。
- P3/P4、schema v6、wheel/tag/Release 均未进行。下一步是持久化权限管理与跨宿主空闲转换，
  不是宣称目前的临时管理员实验已成为正式安装器。

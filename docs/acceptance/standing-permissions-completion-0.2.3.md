# L4a P1b：有限原生放行清单

2026-09-16，从 `8f36b460f4ab8c09de9873d1d9569c7cb97c6ab4` 继续。
代码与本文同提交；各原生报告另存实际执行源码哈希，不能仅用提交号代替工作树内容。

**当前有限 P1b 门槛已通过，P2 可以开始。** 下文保留首次未放行及 UAC 取消记录，随后用户明确
要求重新请求；源码 `381fdd342e2fe180886b378fb76f8a783419bea7` 的完整结果追加于文末。
发行仍为 0.2.2，数据库仍为 v5；原生通过不等于正式 Runtime 已接入。

## 固定范围

- 普通 Windows 用户运行 LPAC，Windows 11 x64 / build 26200 / 本地 NTFS。
- 使用隔离工具副本；不支持任意本机解释器或项目虚拟环境。
- Work 可修改，独立 Archive 只读，Private 未授权；保留显式停机撤权时序。
- 同一进程复用已准备策略，每次启动复核；不实现实时移动监视或跨进程复用。
- 所有操作针对新建仓库内夹具；不读取日常 `.agenthub`，不调用模型、不修改发行版本。

## 本次实现

`DependencyManifest` 保存隔离副本的目录、对象身份、文件大小和 SHA-256。
每次执行登记复核清单；缺失、新增、内容变动、同内容替换、硬链接或祖先 junction 均拒绝复用。
这是执行开始前的核实，不宣称能够监视或锁住运行期间的整个文件系统。

原生启动原语新增父 Job 注入。进程保持挂起，按外到内关联并核实 Job，完成已有令牌及显式
句柄清单检查后再恢复；不设置 breakaway。新检查覆盖外层宿主 Job、Run Job、命令 Job。
这些仍是原生探针；尚未迁移为正式 Runtime 驱动。

新增 `probe-lpac-completion.py` 汇总有限清单，`finalize-lpac-completion.py` 在管理员辅助程序
退出并清理后核对同一实验身份。只有全部必要项为布尔真、没有未执行项、五个固定对象均清理，
且符号链接检查完成，才写入 `gate=passed`。子检查成功但初始化尚未清理时只能为
`pending_initialization_cleanup`。遗漏、失败或清理未确认均不能放行。

## 已验证场景

| 检查组 | 本次实际结果 |
| --- | --- |
| 不同策略并行 | 完整与收窄策略真实重叠运行；令牌不携带对方业务 capability；Work 修改权限按各自策略执行，Archive/Private 不越权 |
| Run 私有目录 | 各自可读自己的临时资料，不能读取对方资料 |
| 同策略复用及停用 | 多个执行可登记；活动时拒绝清理，停用后不再登记；诊断性直接使用旧身份也被 OS 拒绝 |
| 依赖异常 | 修改、缺失、新增使准备实例进入待修复，无活动租约；恢复夹具并核实后才能清理 |
| 授权根替换 | 新根不能复用旧准备；无法核实对象时拒绝猜测清理，恢复原对象后清理通过 |
| 特殊继承 | 预先关闭继承的子目录按策略读写；准备及清理保留原继承设置 |
| 嵌套 Job | 外层宿主约束保留；单个命令超时终止其子孙进程，另一命令继续存活；Run Job 终止全部剩余进程 |
| 原有边界 | 新 Job 结构下，宿主进程访问、未列入的继承句柄、私密环境哨兵、父子进程 IPv4/IPv6 TCP/UDP 回环拒绝通过；网络保留可达正对照 |
| 异常生命周期 | 取消、超时、宿主退出、启动故障、准备补偿、清理失败及重新核实通过；本次夹具清理已确认 |
| 对象别名及 ACL | junction、多硬链接、路径替换及目录分区原有 ACL 操作拒绝检查通过；真实符号链接仍缺创建权限，未计为通过 |
| 新结构的 Python/Pwsh7/Git/uv 完整矩阵 | 待受控初始化后复验，未执行；以前已通过的旧结构软件链证据保持有效但不能替代本项 |

策略报告的原有 14 个检查点及新增 12 个检查点均为真；隔离报告的原有 13 个检查点为真。
这些是场景检查，不与 pytest 数量相加。

最终子集复验中首次策略 ACL 准备约 0.272 秒、28 次对象访问（含重复访问）。同一实例各次根与
依赖核实约 0.229–0.407 秒，每次核实两个业务根和 890 个依赖对象。复用不重扫业务树，但每次
仍完整校验隔离工具副本；未证明总启动加速。
旧报告的 `recursive_scans=0` 仅指业务树，新代码分别记录 `business_recursive_scans` 和
`dependency_objects_rechecked`，避免把依赖扫描遗漏在成本说明之外。时间不含工具复制。

项目相关回归 **126 项通过，66.23 秒**（JUnit 记录 66.189 秒），含确定性测试与真实原生子集，
无跳过项。随后补齐实验身份不匹配判定及依赖祖先被 junction 替换的用例，最终针对性测试
**18 项通过，0.73 秒**。两组有重叠，不能相加为 144 项，也未将最后的单元修改描述为重新跑完
整组原生验收。相关 Ruff、PowerShell 语法及 Git diff 检查作为提交前检查执行。
最后又单独执行 `--remaining-gate`，原有 14 项及新增 12 项全部通过，五个 profile 均删除、
业务 ACL 清理核实；这次复验不包含需要初始化的完整软件链或真实符号链接。

## 本次未完成及失败记录

1. 最初特殊 ACL 夹具把无符号高位常量传入 pywin32，引发 `OverflowError`，尚未进入 LPAC
   准备阶段。改为 pywin32 的 `PROTECTED_DACL_SECURITY_INFORMATION` 后原生复验通过。
   `var/l4a-completion-20260916-01`、`-02` 的报告和日志保留。
2. 无初始化汇总按预期返回退出码 1，`gate=not_passed`。完整软件链标为未执行；真实符号链接
   创建返回 WinError 1314，未启用 Developer Mode 或修改系统用户权限。
3. 实验 `bea68f38449740579a1dfe52a44c3176` 的 Windows UAC 启动返回“操作已被用户取消”。
   管理员辅助程序未启动，核实无该实验的初始化报告，也没有创建目标符号链接。只留下普通用户
   新建的固定测试目录和 `Private/sample.txt`。没有自动重试管理员操作。

下次完整测试的管理员部分仍仅临时处理已核对的五个系统查询对象，另在该次新建测试夹具中
创建一个固定符号链接。Git 需要前者完成真实文件正对照；后者用于补齐普通用户目前不能创建的
对象别名测试。辅助程序最多 600 秒，结束移除自己的 ACE 和未移交的夹具链接；普通权限探针
自行清理已移交的链接。清理必须由最终汇总确认，不把管理员步骤退出等同于清理成功。

没有开机任务、系统重启、日常状态升级或权限设置变更。旧移动残留权限失败记录保持原结论。

## 复现与下一步

在普通用户终端中执行，完整入口会请求 Windows UAC：

```powershell
# 完整门槛：固定初始化、普通用户原生探针、清理和最终判定
.\scripts\probe-lpac-namespace.ps1 -Probe completion

# 无 UAC 的自动化回归：明确检查缺初始化不能被汇总为通过
$env:AGENTHUB_RUN_LPAC_NATIVE = '1'
.\scripts\run-tests.ps1 -Stack system -Module sandbox -Type integration
```

只有完整汇总记录 `gate=passed`，才进入 P2：迁入共用启动原语、一次操作一个 JSON 文件 worker、
统一资源与软件探测，再通过 `run_turn` 注入现有 Runtime。当前还没有受限 CLI 工具循环、DeepSeek
受限任务、无宿主文件绕过检查或 P2 宿主回归结果。P3/v6、P4/wheel/sidecar 继续待实现。

## 证据索引

原始报告含实验身份及本机路径，保存在忽略目录，不提交原始日志。下列哈希绑定本次实际文件。

| 证据 | SHA-256 |
| --- | --- |
| `var/l4a-completion-test-da5f4d9dbe954f20b9e64f3500a4f446/report.json`（缺项，未放行） | `8210c2caa471a2c0072aa48e751369ffca639c488e954e49a86bc47ca2108aad` |
| `var/l4a-quiescent-completion-42272c0113594a17a35fca09f2f6cd4c/report.json` | `45c003d08b7826bdbac246486f552f3fe06a544b803a0590db6437cbbed7ad5a` |
| `var/l4a-isolation-completion-39e33393c6004194b5babbd3aa283f4d/report.json` | `e5bdc9629bebc0d2f6ee363210165149e7e0521342398916ce449dba85e4c7e8` |
| `var/l4a-standing-completion-4647d67f7984476b94f1adf68ab1bc48/report.json` | `f50348bfd962bf1b9997aa4092aa884a143f65cda3304df5c5acf171d7f4a9a3` |
| `var/l4a-completion-regression-20260916.xml` | `32df77fc771310d153e20d7853bda1b4e8a90853e6dd28defb6ed8f51afa69af` |
| `var/l4a-completion-final-unit-20260916.xml` | `3eb49607d346fc8f4e21ffc0bab98b22feb32b12f6f06e0ece3f530ec9bce061` |
| `var/l4a-quiescent-final-20260916-01/report.json`（最终策略/Job 子集） | `4b9e4003c12246573de2ed230f5a61ff8df9266ba7d2629c15ac1e48a0e8851c` |

此前初始化后的四工具结果和清理记录见[停机撤权验收](standing-permissions-quiescent-0.2.3.md)，
不追溯修改为新 Job 结构通过，也不为更新本文重复执行原试验。

## 用户重新确认 UAC 后的完整结果

实验 `a91325af5c5d4aab9d079df8e4e6b30f` 在同一 build 26200 运行，源码为 `381fdd3`。
此项是新的实际执行，不覆盖上面的未执行或失败记录。

- 汇总七组必要检查全部通过：策略、完整工具链、隔离、外层 Job 继承、Run Job 清理、真实
  符号链接以及对象/ACL 边界；`not_executed=[]`、`coverage_limits={}`。
- Python、PowerShell 7、Git、uv 通过新 Job 结构下的修改与只读策略矩阵；未回退普通令牌。
- 符号链接真实创建，LPAC 拒绝未授权目标，夹具链接清理确认；目标内容保持原样。
- 五个系统对象的本次 ACE 均为 `removed`，前后 DACL 相同；`cleanup_errors=[]`。
  管理员辅助程序因 `host_requested` 正常清理退出，没有等到 600 秒上限。
- 最终判定在清理后完成：`gate=passed`、`gate_errors=[]`，入口退出码为 0。

| 证据 | SHA-256 |
| --- | --- |
| `var/l4a-completion-a91325af5c5d4aab9d079df8e4e6b30f/report.json` | `354dab2ec44e987015aa31d4eb82687ee66794f07c16c2e5c81aeb153c957c20` |
| `var/l4a-namespace-a91325af5c5d4aab9d079df8e4e6b30f.json` | `6c3ba87d77ac1b913a658eb1639d683ed08579afe901575bffd479d168d30fb3` |
| `var/l4a-quiescent-completion-2df3aa5386ab487fb559bbbe9e662feb/report.json` | `5af1619cabdbe0ee9fffb9447cf0a24174bfae5ea002cce4dd68a072c3146107` |
| `var/l4a-isolation-completion-230cb4c7281949dea5f6dafa05329eaf/report.json` | `8f0cfa89b15b6d3ee48d10f41d1be445c0cd4994691adb5a9aee65d736084a99` |
| `var/l4a-standing-completion-781b32fb519147069a284232318ea7f5/report.json` | `97d624302b3d2372fbfee5ab44b3c46f9aeff3341ab062cb1c092e1dde98bac6` |

后续 P2 必须复用这些启动原语，并单独验证文件 worker、宿主无绕过、真实工具循环和终态；
本记录不替代 P2、P3、P4 验收。开发继续当前分支，不合并、不切分支、不创建发行物。

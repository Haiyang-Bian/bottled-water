# L4a P1b：显式停机撤权与重新准备

2026-09-16。本记录采用用户新确认的时序：此前可修改的内容，在用户明确停止相关执行并撤权后，
才转换为只读或移出授权范围。**移动文件本身不代表撤权。**

这是原生诊断和公共生命周期协调器的阶段交付。正式 CLI 未接入，版本保持 0.2.2 / schema v5。
完整 P1b 尚未通过，不能据此进入 P2 或宣称 0.2.3 已交付。

## 范围与实现

- `permission_preparation.py`：同一宿主进程内的不可变准备实例及执行登记。
- 状态为 new / preparing / ready / retiring / retired / repair_required；停用不可逆。
- 有活动登记时拒绝停用，返回 `permission_busy`，不删除 ACL；驱动确认 Job 清空后才能释放登记。
- Job 退出不明、准备补偿或清理失败时保留待修复，不能重新发放执行租约。
- `lpac_probe/quiescent.py`：仅用于新建、自有完整测试树的 ACL 后端；先记录意图，再修改和验证。
- `probe-lpac-quiescent.py`：两个并发 LPAC、取消、旧身份停用、用户移动、新只读策略和故障注入。
- `lpac_probe/toolchain.py`：对选定 Python、PowerShell 7、uv、Git 做版本与实际文件操作检查，
  记录绝对来源路径和复制后全部运行组件 SHA-256。Git 阴性必须先有成功的读写正对照。

准备实例不是操作系统权限的替代物；fixture 后端也不是生产 NTFS 驱动。
未实现正式持久化修复、跨 CLI 排他、完整依赖复核和外部移动竞态处理。测试树包含未授权 Private，
是为了诊断清理；正式适配器不能据此扫描任意用户目录或猜测被移动对象的位置。

## 原生结果

Windows 11 x64，build 26200，NTFS。工具为普通用户下的 LPAC，未使用普通令牌回退。
源码基线 `7caab5494fda356deb4185902ccdced097a878da`，加本记录同提交中的文件；报告另存探针源码哈希。

| 场景 | 结果 |
| --- | --- |
| Work 可修改、Archive 可读、Private 拒绝 | Python 基线通过 |
| 同一策略两个 LPAC 同时活动 | 确认重叠；停用被拒绝，权限尚未撤销 |
| 取消两个执行 | 两个 Job 都清空，之后才释放登记 |
| 旧身份停用 | 自有 ACE 清理核实；宿主拒绝再次登记 |
| 绕过协调器，诊断性地再次启动旧 capability | OS 拒绝全部业务路径，证明不是只靠宿主拒绝 |
| 停用后用户移动文件/目录到 Archive、Private | 新只读身份不能写入 Work/Archive，不能读取 Private |
| 新准备实例 | 新业务身份，令牌不包含旧业务 capability |
| 首次 ACL 应用后注入故障 | 补偿核实，实例不可用 |
| 清理验证故障 | 进入待修复，拒绝执行；显式重试清理成功后永久停用 |
| 无关 ACL 和用户继承设置 | 清理前后逐对象比较，保留其他 ACE 和继承设置 |

基础原生子集的 **14 个检查点全部通过**。项目回归 **109 项通过 / 22.77 秒**，包括 106 项
确定性测试以及 3 项原生子集。检查点和 pytest 项目不能相加当作独立测试数量。
相关 Ruff 与 diff 检查通过。没有调用模型、升级日常状态、安装开机组件或创建发行标签。

## 软件链补验

未初始化系统查询权限的完整软件试验仍为失败，原始记录保留：

| 软件 | 本次版本 | 修改阶段与只读阶段 |
| --- | --- | --- |
| Python | 3.11.15 | 两阶段文件权限矩阵通过 |
| PowerShell | 7.6.5 | 两阶段文件权限矩阵通过；不回退 5.1 |
| uv | 0.11.21 | 离线调用指定 Python，两阶段文件权限矩阵通过 |
| Git | 2.55.0.windows.3 | 版本启动成功，实际操作失败；不能计为权限测试通过 |

Git 返回 `Unable to read current working directory: Permission denied`。这是既有固定系统查询
初始化缺失，不是 Archive/Private 阴性通过。`probe-lpac-namespace.ps1 -Probe quiescent` 可以复用
已核对的五对象、最长 600 秒的初始化试验；只有辅助初始化提权，实际软件操作仍在普通用户 LPAC 中。
默认不安装开机任务。最初提交时补验仍等待 Windows UAC 确认；后续进程已完成，结果追加如下。

### UAC 确认后的实际结果

同日重新检查运行会话，发现之前的进程已正常结束，退出码为 **0**，不需要再次修改系统权限。
该次运行的源码为 `9a07117ee39033075ec4549fb011169000d5bf95`，报告中的 8 个源码文件哈希与
当前工作树一致。运行环境仍为 Windows 11 x64 / build 26200，软件版本与上表一致。

- 修改阶段、撤权后的只读阶段，各 **13 个软件检查点全部通过**，包括 Python、PowerShell 7、
  uv 离线 Python 调用和 Git 的实际操作。此前 14 个生命周期检查点也全部通过。
- Git 在 Work 的 `show`、`config --local` 和 `diff` 成功，读 Archive 成功；写 Archive 返回
  255 / `could not lock config file .git/config: Permission denied`。
- Git 访问 Private 返回 128 / `cannot change ... Permission denied`；改用新的只读策略后，
  Work 的 `config --local` 同样返回 255 / Permission denied，正常读取与 diff 仍成功。
- 所有 5 个 LPAC profile 已删除；4 次业务身份准备均已停用并核实 ACL 清理；无清理错误。
- 固定系统初始化报告为 `cleaned`，5 个对象的本次 ACE 均已移除；各对象的前后 DACL 相同，
  `cleanup_errors` 为空，停止原因是 `host_requested`，并非超时强制停止。

这证明**本次工具链和显式停机撤权子集通过**，不把缺初始化的历史失败改写成成功。
报告继续保留 `gate=not_passed` 和 `full_P1b` 未完成标记；完整原生门槛、正式驱动和 Runtime 接入
不能由这些软件检查点替代。此次仅核对已完成进程及证据，没有重复弹出 UAC 或重复应用系统 ACE。

## 复用成本与结论边界

`l4a-quiescent-test-ff4ccca7d799419fa3a51ba1a7e7b132` 的首次准备约 0.380 秒，访问计数 24 次；
同一实例各次根核实约 0.25–0.64 毫秒，每次检查两个根、递归扫描数为零。
计数含重复访问，不是 24 个唯一对象。时间不含完整运行组件复制，不代表整个生产启动成本。
核实当前仅覆盖夹具根及自有 ACE，不能从这组数字推导正式依赖与变更验证后的加速比例。

## 可复现入口

```powershell
# 普通 Windows 用户；不在管理员终端运行
$env:AGENTHUB_RUN_LPAC_NATIVE = '1'
.\scripts\run-tests.ps1 -Stack system -Module sandbox -Type integration

# 完整软件矩阵需要受控的 UAC 初始化；固定范围，自动清理
.\scripts\probe-lpac-namespace.ps1 -Probe quiescent
```

旧移动失败使用单独的 `AGENTHUB_RUN_LPAC_MOVEMENT_GATE=1`，不混入新时序的通过项。
[旧失败记录](standing-permissions-movement-0.2.3.md)及其原始报告保持不变。

## 证据索引

原始报告含实验身份和 ACL，保存在本地忽略目录，不提交原始日志。

| 证据 | SHA-256 |
| --- | --- |
| `var/l4a-quiescent-20260916-01/report.json` | `249e14c4a6ab48a7a072625a75eb7ee48dbec4e7601dab1456cd8d8b6ec6dca0` |
| `var/l4a-quiescent-toolchain-20260916-01/report.json`（缺初始化，失败） | `10e7df64f5056fcd826e949ea15eecc1ab8bd7efbada4e6e2dc5c1d66c1ca1d3` |
| `var/l4a-quiescent-test-ff4ccca7d799419fa3a51ba1a7e7b132/report.json` | `0510ea5fc3be40956e0bcfb35b4131400e67ccd19daf5ee8fee3c25c6ed32a54` |
| `var/l4a-quiescent-regression-20260916.xml` | `0933f9f48f1a12e36cbfd59d46c945602d0866a80e850efcf2fa2e1deaaeb507` |
| `var/l4a-quiescent-namespace-c3009db9b9314600b1d086b524ccea90/report.json`（初始化后通过） | `1031d93573d2c097d211959d4cb3e7b6c2b682b8b1080a570ca6ec16338d9331` |
| `var/l4a-namespace-c3009db9b9314600b1d086b524ccea90.json`（系统 ACE 已清理） | `84de82f421022056d30fecff9b0c6d887cdb2235e4fe7edf4a41c0f5517da05a` |

固定初始化后的完整软件正负向矩阵已经完成；下一门槛为有效策略之间的隔离、准备和依赖异常、嵌套 Job 等
尚未覆盖的原生条件。生产文件 worker、Runtime 组装、持久化管理与完成事务仍未接入。
实时移动撤权按新取舍暂缓，不使用它掩盖明确停机撤权的清理缺陷。

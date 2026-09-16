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
默认不安装开机任务。当前补验仍等待 Windows UAC 确认，尚无本次系统对象变更和清理记录。

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

下一门槛为固定初始化后的完整软件正负向矩阵、有效策略之间的隔离、准备和依赖异常、嵌套 Job 等
尚未覆盖的原生条件。生产文件 worker、Runtime 组装、持久化管理与完成事务仍未接入。
实时移动撤权按新取舍暂缓，不使用它掩盖明确停机撤权的清理缺陷。

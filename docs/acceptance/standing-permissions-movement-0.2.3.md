# L4a P1b：同卷移动与旧策略身份复用失败记录

2026-09-16。结论：**P1b 未通过，停止 P2 Runtime 接入。**
本轮只扩展原生诊断、证据判定和测试入口，未交付准备缓存或正式受限驱动。
共享包/后端仍为 0.2.2，SQLite 仍为 v5；没有安装、升级日常状态、创建发行标签或调用模型。

## 1. 已确认的复用范围与本次问题

用户同意首版仅在同一 CLI 进程内复用准备结果；跨进程、跨重启不复用业务身份。
长期授权配置与原生准备缓存是两件事，重新准备不要求重新批准同一范围。
目录布局继续采用相互分离的 Work（modify）、Archive（read）、Private（未授权）。
用户自行移动资料，工具不替用户搬移文件，不改变用户 ACL 继承设置。

本次诊断直接使用现有原型启动器，故意把同一个业务 capability 发给移动后的新 Package SID。
**它尚未经过准备实例的失效管理器。** 实验检查的是这一原生复用方式能否单独保障路径边界，
不是已发布 0.2.2 的回归，也不代表已经实现的失效管理器发生了故障。

结果表明：仅限制在同一 CLI 内、仅核实授权根、或只换 Run 的 Package SID 均不够。
同卷移动可保留原对象的 capability ACE。旧 capability 仍可打开已经移出授权范围的对象。
[Microsoft 关于复制和移动权限的说明](https://learn.microsoft.com/en-us/troubleshoot/windows-client/windows-security/permissions-on-copying-moving-files)
解释了为何需要这个原生实验；本结论以本机实际访问结果为证据。

## 2. 环境、操作与结果

- Windows 11 x64，实际 build 26200，NTFS，普通用户，未提权为管理员。
- Python 3.11.15，复制的解释器 SHA-256：
  `749a54c7896d14138d74c6e35f89cb4f8fb1c59d1dd6ddaeaa8107b2a8e0aba2`。
- 源码基线 `e956f778731e424fb3618e33b1071087ba3d919b` 加本提交中的诊断文件。
  报告记录实际运行的五个 Python 源文件 hash，源码身份不只依赖基线提交。
- 只使用新建 `var/l4a-movement-*` 夹具、四个临时 Package SID 和两个业务 capability。
- 首次准备只给 Work 和 Archive 添加对应权限；Private 不添加业务 ACE。
- 记录移动/复制前后的卷号、文件 ID、已有内容 hash 和旧 capability ACE。

| 阶段 | 实际结果 | 判定 |
| --- | --- | --- |
| 基线 | Work 可读写；Archive 可读不可写；Private 不可读写 | 通过 |
| 文件移入 Archive | 旧 capability 仍可修改该文件 | 越权，失败 |
| 文件移入 Private | 旧 capability 仍可读写该文件 | 越权，失败 |
| 子目录移入 Private | 旧 capability 仍可读写其中原有文件 | 越权，失败 |
| 普通复制正反对照 | Archive 中副本只读；Private 中副本拒绝读写 | 通过 |
| Work 根移到 DetachedWork，原位置新建 Work | 旧身份仍可读写移走的对象，不能访问新建替代根 | 路径缓存失效；越权，失败 |
| 换新业务 capability，只准备当前 Work/Archive | 当前 Work 可读写，Archive 只读；Private 和 DetachedWork 拒绝 | 对照通过，不代表失效管理已完成 |

第一次子对象移动测试时，Work 根对象并未改变，仍复现五项越权操作。
随后根替换阶段复现七项越权操作，以及两项对新根的正对照失败。
所有 rename 的源/目标文件 ID 相同，copy 的文件 ID 不同。
不是文件不存在、工作目录错误、解释器失败或普通令牌回退造成的假拒绝。

四次进程均正常结束、LPAC/Package SID/capability 核实成功，Job 确认排空。
四个隔离身份均已删除；夹具内包括移动后对象的自有 ACE 已移除；
清理核实保留了其他主体的 ACL 条目及继承标志。没有遗留待修复项目。

新身份对照并未终止一个仍活动的旧进程，因此**没有验证移动发生时的实时撤销或竞态**。
报告中的准备耗时包含证据落盘，且两次目录树不同，不用于宣称复用性能。

## 3. 可重复入口及检查

新增原型：`scripts/probe-lpac-movement.py`；受限侧固定尝试：
`scripts/lpac-movement-payload.py`；证据判定：`scripts/lpac_probe/movement.py`。

```powershell
# 每次使用新的、尚不存在的仓库内夹具目录。
.venv\Scripts\python.exe scripts/probe-lpac-movement.py --output var/l4a-movement-new-attempt

# 显式原生门槛；当前结果应是失败，不使用 xfail 把它标成放行。
$env:AGENTHUB_RUN_LPAC_MOVEMENT_GATE = '1'
.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/test_lpac_movement_native.py -q
```

脚本先核实新路径属于仓库 var、本地 NTFS 和非管理员用户。
新身份对照通过不会擦除旧身份越权；脚本退出码为 1，`subset_passed=false`、
`gate=not_passed`、`stop_reason=stale_policy_access`。

| 验证 | 本次结果 |
| --- | --- |
| 修改文件 Ruff | 通过 |
| 既有权限/证据测试及新增证据判定测试 | **95 passed**，其中新增 27 项 |
| 原身份/生命周期与目录分区原生子集 | **2 passed** |
| 新增移动与复用原生门槛 | **1 failed**，复现实际越权 |
| 合并原生测试执行 | **1 failed, 2 passed / 18.71 s** |
| Runtime 受限工具循环、完整软件链、DeepSeek、Web/sidecar | 本轮未执行，P1b 停止门槛已触发 |

确定性测试通过只说明证据判定正确，不能改称本机隔离通过。
新原生入口加入 `sandbox:integration`，必须显式设置环境变量；未启用时显示跳过。

## 4. 原始证据

原始报告/夹具/JUnit 留在被忽略的 var 中，不纳入源码提交。

| 文件 | SHA-256 |
| --- | --- |
| `var/l4a-movement-20260916-01/report.json`（首次复现） | `6736bd07cc52fef3c66126b716a036acd65bca23f3dbfed5cebcf7cf9afce678` |
| `var/l4a-movement-test-34b1c54266034e438ea0a0b1a3af4fc4/report.json`（测试入口复现） | `6c2cbfa4829140d1ec8062141b769698be1e83ed981c00197ddfdddb75e908121` |
| `var/l4a-movement-native-20260916.xml` | `a77f8a575fee8dd9569895298f40a432c54796179c1384ad48660f2bdff95b48` |

## 5. 下一步的必要条件

先在 P1b 内实现并验证准备身份的完整生命周期，再恢复 P2：

1. 每个准备实例有独立代际身份和不可重新启用的失效状态，登记当前持有者及活动 Job。
2. 核实根对象身份，同时处理后代对象移动及安全属性变化；仅检查根或路径字符串不够。
3. 失效后禁止新 Run 取得旧 capability，撤销相关执行并确认 Job 排空后才准备新身份。
4. 移动后遗留 ACE 按本工具登记的对象核实和清理；不能用旧整份 DACL 覆盖当前权限。
5. 无法证明清理与对象归属时保留待修复状态。缓存通知溢出或中断必须失效，不能默认为未变。
6. 原始直接访问试验作为负对照保留；后续门槛还要证明实际准备管理器拦截了旧身份启动，
   并验证活动进程的撤销时间、移动竞态和未知结果，不能只让原始试验换一个新 SID。

目录通知用于失效判定，不是访问控制边界；它可能溢出，且不报告被监视根自身的变化。
[Microsoft ReadDirectoryChangesW 文档](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-readdirectorychangesw)
规定了需重新核实的情况。仍不引入常驻服务或为了通过测试修改用户全局权限。

完整软件链、初始化、并行策略、别名、失败补偿门槛仍须补齐。
本次未合入或推送，不创建 0.2.3 wheel/标签/PR；用户文件与仅本地的同事安装指南保留。

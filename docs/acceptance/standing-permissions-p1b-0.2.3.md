# 长期授权与保护区：P1b 原生试验记录

**历史记录：**下文对应内部保护区需求仍在讨论时的结果。用户随后决定由自己将归档移出可修改范围，
当前实现改为目录分区，不采用继承屏障。旧失败保持原结论；当前测试入口及证据见
[自然继承验收](standing-permissions-separate-roots-0.2.3.md)。

2026-09-16；分支 `codex/windows-permissions-l4a`。本记录是开发门槛证据，**不是 0.2.3 发行验收**。
P1a 提交为 `74266b3`。本记录及探针的后续提交保留在同一分支；各原始报告内保存四个探针源文件的
SHA-256，因此提交前运行的证据也能对应实际脚本。原始文件在忽略目录 `var` 中，不随代码提交。

## 结论及停止位置

- 纯长期策略、任务收窄、保护优先级及撤销比较已经实现；没有接入正式 CLI。
- 两种保持原有 ACL 继承方式的尝试都出现真实越权：保护区可修改，禁止区可读取。
- 第三种“继承屏障”在新建夹具中通过了三次顺序执行的读写矩阵与清理检查，但它会冻结
  **所有主体**在边界处的 ACL 继承，超出了只管理 AgentHub 自有 ACE 的准备/清理边界。
- 第三种尝试目前只作为诊断选项，不是已采用的产品策略。不能用这个局部正例宣称 P1b 或 L4a 通过。
- P2–P4 暂停：共享包/后端仍为 **0.2.2**，schema **v5**；没有 0.2.3 wheel、标签、PR 或发布。

没有修改日常 `.agenthub`、用户项目 ACL、系统对象 ACL、启动项或服务。全部文件操作发生在本轮新建的
仓库内夹具中；未执行模型请求。PowerShell/Git/uv、完整网络矩阵和正式宿主本轮未用此新策略验证。

## 实际试验

平台：Windows 11 x64，内核报告 build **26200**，Python 3.11，普通用户启动。
目录为 `Tree/Work`（修改）、`Tree/Group/Archive`（只读）、`Tree/Private`（禁止）。
额外的 `Group` 用于验证保护区祖先不能被重命名。每个 Run 使用独立 Package SID、Job 和 Scratch；
业务 capability 与只读运行依赖分开。启动前从实际令牌读取 capability 列表并与请求集合精确比较，
然后恢复挂起进程。没有普通令牌回退。

| 尝试 | 结果 | 实际证据 |
| --- | --- | --- |
| `l4a-standing-02` / deny-aces | 失败 | 广泛 capability ALLOW 加具体 DENY 后，仍能读写 Private、修改/创建/删除 Archive 内容、重命名 Archive 和 Group |
| `l4a-standing-03` / allow-only | 失败 | 只移除或缩小自有 ALLOW，保留原继承；父目录的修改 ACE 重新继承到保护区，仍能读写 Private、修改及创建 Archive 内容 |
| `l4a-standing-test-bc3db22e11fc458fbd84addfe2bd87bf` | 失败复现 | 默认 deny-aces 从 pytest 入口重现七项越权；启动前 ACL 审计也报告过宽 ALLOW |
| `l4a-standing-05` / inheritance-barrier | 未通过 | 第一轮保护正确；第二轮夹具目标已存在导致 rename 正对照失败；清理后用户 ACL 多出显式副本 |
| `l4a-standing-05/repair.json` | 夹具修复完成 | Job 已确认清空后，清理可核对的转换副本、删除三个原型身份；原 ACL 和继承状态恢复，原失败报告未改写 |
| `l4a-standing-06` / inheritance-barrier | **局部通过** | 两个独立 Package SID 顺序复用同一策略，再运行独立收窄策略；全部读写判断、内容保持和夹具清理通过 |
| `l4a-standing-07` / inheritance-barrier | **局部复验通过** | 最终脚本增加 NTFS 前置检查；实际报告为 NTFS，三轮及清理通过，四个源文件哈希与提交前工作树逐项一致 |

`02` 的二次报告异常是保护目录已被重命名后，宿主还去读取原路径；它没有改变前面的越权事实。
`01` 的 pywin32 `AddAce` 不存在、`04` 的 SECURITY_INFORMATION 有符号参数溢出都是探针实现错误，
不计为 OS 拒绝。原始失败留存。`03` 的早期 `business_acl_prepared_once=true` 只检查了准备次数，
当时只有一轮执行，**不证明复用**；后续要求至少两轮。以上历史报告都没有追溯改成成功。

## 权限矩阵及证据含义

`06` 的每个负对照要求 WinError 5 或 errno 13；文件不存在、解析失败、程序未启动都不算拒绝。
正对照读取必须得到预设内容。三轮之间重新建立普通区的 rename/delete 目标，避免目标冲突误报。

| 操作 | 完整策略 | 收窄到 Work 只读 |
| --- | --- | --- |
| 读取 Work | 允许，内容正确 | 允许，内容正确 |
| 修改/创建/重命名/删除 Work 普通文件 | 允许 | OS 拒绝 |
| 读取 Archive | 允许，内容正确 | OS 拒绝 |
| 写入/创建/删除 Archive 内容 | OS 拒绝 | OS 拒绝 |
| 读取/写入 Private | OS 拒绝 | OS 拒绝 |
| 重命名 Archive / Group | OS 拒绝 | OS 拒绝 |
| 请求父目录 FILE_DELETE_CHILD | OS 拒绝 | OS 拒绝 |
| 请求 WRITE_DAC / WRITE_OWNER | OS 拒绝，包括受限进程新建的文件 | OS 拒绝 |
| 读取本次 Scratch / 其他 Run Scratch | 本次允许，其他拒绝 | 本次允许，其他拒绝 |

实际 token 为 LPAC，携带且只携带选定业务 capability 与已要求的系统查询 capability。
Job 在每次进程返回后确认清空。这里是三个**顺序** Run；没有把顺序隔离称为并行证明。
业务 ACL 准备一次；Package SID 的运行依赖/Scratch ACL 仍逐 Run 准备。

`06` 夹具准备耗时约 **3.000 s**，三次执行约 **0.891 / 0.375 / 0.359 s**。
这些不是工作盘首次准备基准、缓存核实耗时或性能保证；尚未实现生产策略缓存、复用成本测试和持续撤销。
`07` 再次新建夹具准备约 0.969 s，执行约 0.531 / 0.375 / 0.313 s；同样不据此计算缓存提速比例。

## 为什么暂不采用继承屏障

观察到的 ACL 与真实拒绝相互印证：`03` 中 Archive 显式只读 mask 为 `0x1200a9`，
但还存在继承修改 mask `0x1201bf`。具体 DENY 不能作为这个过宽 capability ALLOW 已被抵消的证据。
这只是上述配置在 build 26200 的实测，不是对所有 Windows token/ACE 的普遍结论。

Microsoft 说明 `SetNamedSecurityInfo` 会按继承规则向已有子对象传播可继承 ACE，
而 `PROTECTED_DACL_SECURITY_INFORMATION` 阻止 DACL 继承 ACE。
参见 [SetNamedSecurityInfo](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setnamedsecurityinfow)
和 [SECURITY_INFORMATION](https://learn.microsoft.com/en-us/windows/win32/secauthz/security-information)。

第三种尝试在授权根、必要祖先和保护区设置屏障，祖先自身不给 DELETE，给普通后代的修改权采用
INHERIT_ONLY ACE，保护区采用自己的精确 ALLOW。它没有授予 WRITE_DAC、WRITE_OWNER 或 DELETE_CHILD。
这一方式能消除测试中的继承越权，但边界并非仅针对 AgentHub：其他用户/程序后续修改父目录权限时，
也不会再自动传播到这些边界。Windows 还把当时的继承 ACE 变成显式条目；只恢复继承开关会留下副本。

夹具修复只删除此次转换的可核对副本、读取当前 ACL 后恢复原控制位，不回放旧完整 DACL。
**这一算法仅适用于新建、没有其他 ACL 写入者的夹具。** 它不能解决真实目录中其他程序恰好新增相同 ACE、
移动节点、变更父目录权限、半途崩溃等归属冲突。当前 `repair-lpac-standing-fixture.py` 不是用户目录 repair。

其他实现也采用继承屏障，例如 [Sandy 的模式文档](https://ahrvoje.github.io/sandy_cli/modes.html)
描述以收窄允许项并保护 DACL 实现其 AppContainer 拒绝策略；这是其自身实现说明，不是 AgentHub 的安全证明。

下一步需要先明确是否接受上述继承副作用：

1. 若接受，应修订准备清单，展示全部受影响节点；实现每个节点的身份、原继承状态、转换条目归属、
   活动策略引用及冲突处理。清理从当前 ACL 出发，仅补偿本工具的变化，冲突转待修复，不能猜测或覆盖。
2. 如果必须保留其他主体的动态 ACL 继承，则不能采用该屏障。继续研究原生身份/权限组合，重新走 P1b；
   不以用户态路径检查、全权限代理或普通 token 作为替代。
3. 两条路线都必须补足并行策略、已有特殊 ACL、新建目录、外部变更/清理冲突、别名与路径竞态、
   PowerShell 7/Git/uv、禁网、初始化/重启及撤销门槛。通过前不做 P2 正式接入。

## 检查与复现

纯策略和证据判定测试 **58 通过**；它们不模拟或证明 Windows 隔离。
原生 pytest 本轮为 **1 通过、1 失败（14.44 s）**：原有身份/生命周期子集通过，新增默认保护区门槛失败。
失败没有用 xfail、缺字段默认成功或排除该测试来隐藏。默认探针仍保留原定“不改变用户继承”路线。
`inheritance-barrier` 只能显式选择，结果里的完整 `gate` 始终为 `not_passed`。

```powershell
.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/test_permission_policy.py tests/test_lpac_probe.py tests/test_lpac_standing_probe.py -q
$env:AGENTHUB_RUN_LPAC_NATIVE = '1'
.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/test_lpac_native.py -q
# 原生操作只能指向全新仓库 var 子目录；保留旧报告，不复用夹具。
.venv\Scripts\python.exe scripts/probe-lpac-standing.py --output var/l4a-standing-recheck --acl-mode inheritance-barrier
```

第二条测试目前预期返回非零，代表原计划门槛未满足。第三条只有夹具子集意义，不能触发发行放行。
以上是当时的入口行为；需求调整后第二条改为新的目录分区门槛。复现旧失败应显式运行
`scripts/probe-lpac-standing.py --output var/l4a-standing-old-recheck --acl-mode deny-aces`。
Windows 符号链接权限不足、完整软件链、撤销/完成事务、v6 迁移、真实 Provider、ConPTY、Web/sidecar
和独立安装本轮均未宣称通过。继续保留用户 frontend 文件及本地同事安装指南。

## 不可变证据索引

| 仓库内位置 | SHA-256 |
| --- | --- |
| `var/l4a-standing-02/report.json` | `11d7894b7db0c6579f6d72c3669c323c9228d9b624d26e47631d19e4ec4bb251` |
| `var/l4a-standing-03/report.json` | `910ed6a913ab76ca20bbbc915314c808fc152596ec0aabf8debe9a99c63d82ba` |
| `var/l4a-standing-test-bc3db22e11fc458fbd84addfe2bd87bf/report.json` | `296c20e8b249aeec26066ddafbd5bb1bb54d0a2e56f442b7e624266afb34b9c6` |
| `var/l4a-standing-native-before-barrier.xml` | `666cf03f8e4a191b0bbdd9cd6e35265768bc79060b162b3f2ef3407b9917020b` |
| `var/l4a-standing-05/report.json` | `dd7f12d4d27131963643bdd703d35b6d8c505809fbc81888f35716a436f301d9` |
| `var/l4a-standing-05/repair.json` | `9f658f05722f0183c99ad4a38ef7e969187e1058f5e9d2524a322437248885b1` |
| `var/l4a-standing-06/report.json` | `16765897e95c12b27a0e88d397411236e03d79810909a1e59e50e65d130620bb` |
| `var/l4a-standing-07/report.json` | `cee5b02f35f61c1cb5e236ac5b7a4283fb9a2681ed15c56ac81fd06a49dbd87b` |

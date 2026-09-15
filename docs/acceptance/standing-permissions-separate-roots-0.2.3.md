# 长期授权：目录分区与自然继承验收

2026-09-16。用户在了解继承屏障的影响后决定：遵循 Windows 特性，需要隔离的归档由用户移走。
本轮沿用 `codex/windows-permissions-l4a`，在 `b5d8235` 上增量实现；不合并、切换或清理其他分支。

## 规则变化

采用独立的可修改工作区和只读资料区。私人资料不授权，任务默认继承长期授权，custom 只收窄范围。
不支持可修改父目录中的只读/禁止访问例外，不改变用户 ACL 继承，不自动移动用户文件。
这次通过是**需求调整后的目录分区子集**，不是把原来的嵌套保护失败改成成功。

`freeze_policy` 增加布局校验；`inheritable_roots` 将有效规则编译为仅增加权限的授权根。
自然继承无法表达的下降边界在产生快照前返回 `permission_layout_conflict`，携带冲突路径和父授权。
错误提示要求移出资料或缩小上层授权。系统强制保护参与相同计算，不能借宽泛授权忽略状态/凭据边界。

允许只读父目录下进一步授权一个可修改项目；允许将整个授权根改为只读。
自定义任务可选择不含冲突的更小范围，或整体收窄为只读。目录移动仍由用户明确操作。
纯编译不操作文件系统、不创建资源记录、不授予 ACL；真实路径、NTFS、链接与对象身份仍归适配器核实。

## 本轮验证

- 纯策略及原型判定：**68 通过**，包括布局冲突、任务收窄、系统保护、自然增加权限及旧失败判定。
- 原生 pytest：**2 通过，16.64 s**。包括原有身份/进程生命周期子集和新的目录分区子集。
- 最终同一次执行汇总：**70 通过，23.76 s**（68 项判定 + 2 项原生）；与以上检查重叠，不累加。
- 相关 Ruff 和 `git diff --check` 通过。共享包/后端仍为 0.2.2，SQLite schema 仍为 v5。

原生平台为 Windows 11 x64 / build **26200**，文件系统报告 **NTFS**，Python 3.11，非管理员。
原型直接调用公共策略编译器，避免测试夹具和公共代码各自实现不同授权逻辑。
全部夹具为新建仓库 `var` 子目录；没有访问或修改真实资料目录、日常状态、系统 ACL 或启动项。

| 检查 | 实测 |
| --- | --- |
| 完整策略 | Work=modify，Archive=read，Private 无 capability 授权 |
| 相同策略复用 | 两个独立 Package SID 顺序使用同一个业务 capability，业务 ACL 只准备一次 |
| 收窄任务 | 第三个 Package SID 只携带独立的 Work=read capability，不能访问 Archive |
| Work 普通文件 | 完整策略可读、写、创建、重命名、删除；收窄策略仅可读 |
| 新建子目录 | 完整策略可创建目录及文件、读取、删除，证明新对象继承可用；收窄策略创建被拒 |
| Archive / Private | 归档读取正对照内容正确，修改/创建/删除被拒；私人文件读写被拒 |
| 根和安全管理 | Work 根 DELETE、DELETE_CHILD、重命名被拒；WRITE_DAC/WRITE_OWNER 被拒，包括新建文件 |
| Run 私有目录 | 本次 Scratch 可读，其他 Run Scratch 被拒 |
| 原 ACL 保持 | 准备期间逐条排除本工具新增 SID 后，与原 ACL 条目和继承开关相同；未调用继承屏障 |
| 清理 | Job 确认清空后仅删除本工具 SID，三个身份删除；保留对象的原 ACL 与继承开关核实一致 |

负对照要求明确 WinError 5 或 errno 13，不能用不存在/未启动代替拒绝。
实际令牌与要求的 capability 集合精确匹配。原有生命周期子集继续验证并发独立身份、子进程禁网、
取消、超时、宿主退出及启动半途失败；它不等价于新的共享策略并行验收。
新分区子集三轮为顺序执行，不宣称跨进程权限撤销、生产缓存或真实工具链已完成。

本次小夹具的策略准备约 0.265 s，三次执行约 0.609 / 0.328 / 0.219 s。
这些数字不代表整盘准备或缓存性能。运行依赖和每个 Run 私有目录仍需单独准备。

## 复现和证据

```powershell
.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/test_permission_policy.py tests/test_lpac_probe.py tests/test_lpac_standing_probe.py -q
$env:AGENTHUB_RUN_LPAC_NATIVE = '1'
.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/test_lpac_native.py -q
# 必须使用不存在的新目录；默认是 separate-roots。
.venv\Scripts\python.exe scripts/probe-lpac-standing.py --output var/l4a-separated-recheck
```

旧 deny-aces、allow-only、inheritance-barrier 仅保留为显式历史诊断模式；正式权限编译不使用它们。
试验报告的 `gate` 保持 `not_passed`，通过子集不允许升级版本或发行。

| 文件 | SHA-256 |
| --- | --- |
| `var/l4a-standing-test-dfc4b792b501404a8faa85b0557ae047/report.json` | `fc4a4eb586e0889dfc42244a9ed5612f29b4476c6a77af6418c99a14f945c0b3` |
| `var/l4a-separate-roots-native-tests.xml` | `5c515a13508766116179ef9b9e063da58fc431a3c040386dca41c43812668476` |
| `var/l4a-standing-test-f031eddafa8c4a49b498c770fbf6d514/report.json` | `96f9295ea24ff0cd8321405f93c811dd51df091dcce3081b58360f77b2b89b77` |
| `var/l4a-separate-roots-final-tests.xml` | `29373b37b6d422737cdbe7694e4fa65a3522aecbeb769434e06b2734493763c7` |

报告记录四个探针源文件的 SHA-256；本次新增依赖的公共权限模块与对应源码提交一同保留。
原始报告、测试数据位于忽略的 `var`，不随代码提交；记录中没有真实凭据。

## 仍未完成

完整 P1b 还需覆盖选定 PowerShell 7/Git/uv 的统一策略、运行依赖初始化、不同共享策略并行、
已有特殊 ACL、完整别名和路径竞态、准备失败补偿及复用状态核实。
P2 文件 worker、P3 长期状态/默认任务继承/撤销与完成事务、P4 安装和真实 Provider 仍待实现。
不会用目录分区子集替代这些门槛，未创建 0.2.3 wheel、标签、远程 PR 或 Release。
本地同事安装指南及用户 `frontend/pnpm-workspace.yaml` 保持原状。

# AgentHub 0.2.3：L4a 原始方案与原生基线

> 2026-09-16：用户已批准[长期授权与受保护区域修订方案](./standing-permissions-l4a.md)。
> 后续实现以修订方案为准。本文保留原始方案、已有原型边界和实验来源；下文关于逐任务选择、
> 拒绝父修改/子只读、每 Run 独立准备业务 ACE 及待确认安装方式的旧约定已被替代。
> 当前没有正式 CLI 沙箱、schema v6 或 0.2.3 发行，不能把纯策略测试当作原生隔离通过。

更新：2026-09-16。**当前完成 P0，P1 原生子集通过；完整 P1 尚未通过，不发行 0.2.3。**
共享包与后端仍为 0.2.2，数据库仍为 v5。下面的产品行为、接口和 v6 变化均为已批准目标，
尚未接入 CLI 或 Runtime。实测结论见[原生实验记录](../acceptance/windows-lpac-p1-0.2.3.md)。

## 1. 范围与门槛

本阶段只处理单助手在 Windows 上的文件和进程权限。保留 Python 3.11、现有执行循环、
Provider、Run 终态、会话锁和成功完成事务；多助手身份、共享记忆与交接划入 L4b。
不引入 Rust、GUI 自动化、常驻服务、网络代理、全盘扫描或跨设备同步。

目标场景是同一任务读取 A、修改 B、无法读取 C，且约束覆盖文件操作、PowerShell、Python、
Git、uv 和子进程。受限工具禁网，包括回环；模型连接、凭据、记忆和 Journal 留在可信宿主。
目录 `modify` 包含读取、创建、编辑、重命名及删除，没有“可写但不可删除”模式。

每个 Run 必须使用独立 LPAC 身份和 Job。显式要求受限模式却无法满足策略时拒绝运行。
旧任务不自动转换；已批准记忆的复用权限仍独立于文件授权。

P1 是正式接入的前置门槛。必须先在真实 Windows 上证明工具链可用且 OS 实际拒绝越权，
不能把命令未启动、程序报错或模型愿意配合计为权限通过。Git 已通过最小系统对象初始化复测；
并行身份、部分对抗与进程生命周期子集已通过，完整安装及重启等门槛尚未完成，因此 P2–P4 未启动。
目前的证据没有证明 LPAC 路线不可行，也没有证明完整隔离已经成立。

## 2. 权限计算

- 环境授权是上限，任务保存自己选择的根及 `read/modify` 等级，有效范围取二者交集。
- 环境根首版互不嵌套。任务允许父目录只读、子目录可修改；拒绝父目录可修改、子目录只读。
- `cwd` 必须可读，改变位置不改变许可。新受限任务的启动目录默认只读。
- 只接受本地 NTFS；拒绝 UNC、设备路径、不支持的文件系统和不能安全处理的链接对象。
- 状态目录、凭据、权限数据库及可信组件不能作为业务根，也不能被宽泛的父目录授权覆盖。
- 环境权限只能由用户入口改变。脚本修改提供明确修订号；交互入口读取版本并提交 CAS。
- 旧 `trust` 记录只对旧模式有效，不转成受限读写许可；从旧模式转换时原目录仅为只读候选。

授权目录预检不跟随 reparse point；多硬链接对象在安全性无法保证时拒绝。
路径算法中的逻辑判断仍存在，但实际执行和探测须经过 OS 边界，不能靠工具名称授权。

## 3. 计划中的入口

以下命令**尚未实现，当前安装版本不能使用**。

| 入口 | 目标行为 |
| --- | --- |
| `sandbox setup` | 展示并初始化明确的受保护运行组件；必要时进行受控管理员步骤 |
| `sandbox doctor` | 只读检查 OS build、依赖、隔离能力与待清理资源 |
| `sandbox self-test` | 隔离夹具原生自检，不调用模型 |
| `sandbox repair` | 校验归属与进程状态后清理本工具遗留资源 |
| `permissions` | 展示环境许可、任务有效范围及修订 |
| `permissions grant PATH --access read\|modify` | 用户授予环境许可；修改已有记录须 CAS |
| `permissions revoke PATH` | 撤销并停止受影响的受限 Run |
| `--sandbox windows` / `--sandbox current-user` | 用户显式选择或切换执行模式，显示边界变化 |
| `--read-dir` / `--write-dir` | 选择并保存任务的动作范围 |
| `/permissions` | 查看当前执行模式、有效范围及限制来源 |
| `/add-dir PATH --access read\|modify` | 空闲时选择额外范围，仍受环境许可约束 |

受限模式中的旧 `--add-dir` 或未指定等级的 `/add-dir` 默认只读。
交互入口可以确认尚未授予的环境许可；批处理不询问，返回 2 并给出管理命令。
`/new` 继承模式、位置和授权，不复制历史。恢复任务沿用保存模式，不静默扩权。

## 4. 执行链与公共接口

```text
CLI / Runtime / Provider / SQLite（可信宿主）
  → 权限快照、位置快照、执行租约
  → Windows LPAC 驱动 + Job
      → 固定 JSON 协议文件 worker
      → PowerShell / Python / Git / uv / 子进程
  → 有界工具结果和 Journal
```

文件读取、写入、搜索、忽略文件、Git 索引、资源验证和产物检查都必须进入受限侧。
复用现有 hash 前置条件、编码、BOM、换行和输出限制。worker 不接受 pickle、任意宿主回调、
全权限命令代理或授权修改请求。模型参数不能触发宿主绕过边界的 stat、读取或 hash。

| 契约 | 计划责任 |
| --- | --- |
| `PathPermission` | 规范化根与动作等级 |
| `ExecutionPolicySnapshot` | 模式、任务范围、环境修订、禁网策略和依赖清单 |
| `ExecutionIsolationPort` | 准备、能力报告、撤销与关闭 |
| `FileOperationsPort` | 现有文件算法的宿主独立接口 |
| `ResourceGrant` 扩展 | 操作及实际目标资源的授权判断 |
| `RunHandle.fail(reason_code)` | 转交 Kernel 既有失败路径，不另建终态机制 |

进程挂起创建，限定继承的 stdio 句柄，加入 Job 并核实令牌后才恢复。工具环境使用白名单、
独立缓存和临时目录，不传宿主密钥；禁用用户 PowerShell profile、Git 用户配置及凭据助手。
软件仍检查启用状态与指纹，兼容性另行记录；不能悄悄替换程序。

普通越权返回 `permission_denied` 给模型继续处理；隔离失效或撤销以明确原因结束为 failed/1。
用户取消保持 cancelled/130，配置错误为 2，会话占用为 3。
宿主每次调用前检查权限，运行中以不超过 250 ms 的间隔检查撤销。
最终成功事务再次核实授权，以提交次序裁定完成与撤销竞争。

## 5. 初始化、补偿和数据

依赖清单只包含选定解释器、标准库、PowerShell/Git/uv 组件及必要系统对象。
初始化只运行已核对的组件准备代码，不执行项目或模型脚本；日常任务不提权。
业务 ACL 只添加本次独立 SID 的 ACE，不使用 Everyone 或所有 AppContainer 通用身份。

先记录意图，再应用和核实权限。清理在 Job 终止后只移除本工具 ACE，保留其他程序的新修改，
不以旧完整 DACL 覆盖当前状态。跨 SQLite 与 Windows 的修改采用补偿，不宣称原子事务。
崩溃后必须核对身份及存活状态；时间流逝不是清理授权，无法确认时保持待修复并拒绝重用范围。

计划中的 schema v6 保存环境许可与版本、任务模式与动作范围、管理审计及隔离清理状态。
v1–v5 直接升级继续使用迁移锁、会话锁、SQLite backup API 和一个事务；不自动生成许可，
不在升级时改业务 ACL，不自动升级日常 `.agenthub`。Web 不启用本机权限配置，不增加 Web 表。
L2 遗忘抑制、L3 待办、Context CAS、续接游标和唯一终态必须保持。

## 6. 实施状态与继续工作的顺序

| 阶段 | 当前状态与下一门槛 |
| --- | --- |
| P0 收尾 L3 | PR #28 精确 head 核对后合入；从最新 main 建立 `codex/windows-permissions-l4a` |
| P1 原生边界 | 工具链、并行身份和进程生命周期子集通过；重启/安装、符号链接及完整对抗门槛未通过 |
| P2 公共执行接入 | 未开始；P1 完整通过后接入文件 worker、资源探测和进程驱动 |
| P3 状态与交互 | 未开始；v6、许可管理、切换、撤销与完成事务竞争 |
| P4 安装交付 | 未开始；版本、wheel、升级、真实 Provider、ConPTY、Web/sidecar |

已验证临时系统对象初始化，继续推进 LPAC 路线：

1. 已为五个固定 NT 对象添加专用 capability 查询 ACE，与业务 SID 分离；Git 正负向及 diff 通过。
2. 核实对象权限的修改条件、最小权限、并发补偿、清理归属及重启后的有效性。
3. 如需开机初始化组件或其他新生命周期机制，先补充安装/卸载及保护设计，再评审其范围；
   本轮未创建服务、启动项或计划任务；临时系统 ACE 已逐项清理核实。
4. 已保持 A 只读、B 可修改、C 不可读及回环禁网；继续补全部网络与子进程验证。
5. 已补并行身份、句柄/宿主访问、硬链接/junction/路径替换、超时及强制退出子集；继续补齐未执行项。
6. 通过门槛后才进入 P2；若需要放宽个人目录、广泛高权限代理或普通令牌，回到设计评审。

当前待确认的是[有界开机初始化任务及其受保护组件](./windows-sandbox-initialization-review.md)。
这是新增安装行为；没有将一次 UAC 实验默认为已授权持久化安装。

完整放行还需撤销竞争、v1–v5 迁移、旧模式回归，以及独立安装的真实 DeepSeek A/B/C 任务。
OpenAI-compatible 未配置则记未执行；不同 OS build 与平台不自动继承验证结论。
发行通过才更新 0.2.3、schema v6、wheel 校验值和本地标签，不发布远程 Release 或软件包。

## 7. 原型位置与边界

`scripts/lpac_probe/native.py` 是未接入产品的 ctypes/pywin32 实验；
`scripts/probe-windows-lpac.py` 创建新的仓库内夹具；
`scripts/lpac-probe-payload.py` 提供纯标准库子进程探针；
`scripts/lpac_probe/validation.py` 严格判定证据。
两个 `inspect-lpac-*.py` 脚本只查询相关权限或注册能力，不修改系统 ACL。
`probe-lpac-namespace.ps1` 在普通用户侧编排复测；`probe-lpac-namespace-admin.py` 是有界的
管理员 ACL 实验，固定对象、不接受外部命令、最长十分钟后清理，不是正式安装组件。

原型只用于自己创建的、没有其他写入者的夹具，不能作为任意业务目录的安全授权器。
不提供正式崩溃 repair，不声称解决并发 ACL 竞争、恶意路径替换或完整继承句柄攻击。
`probe-lpac-isolation.py` 和显式启用的 `tests/test_lpac_native.py` 记录并行/生命周期/别名子集，
其中未能创建的真实符号链接仍标为未执行，不能替代完整门槛。
未通过的代码留在 `scripts`，共享包不包含它，也没有 `--sandbox` 可用入口。

相关机制参考：[Microsoft AppContainer](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer)、
[进程属性](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute)、
[Git for Windows 路径实现](https://github.com/git-for-windows/git/blob/main/compat/mingw.c)、
[微软仓库的同类路径问题](https://github.com/microsoft/mxc/issues/694)。上游报告提供定位线索，不代替本机验收。

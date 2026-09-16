# P3 长期权限与正式 CLI：开发和验收记录

日期：2026-09-16。发行版本保持 0.2.2；本地开发 schema 为 v6。
P3 尚未完成验收，不进入 P4，不生成 0.2.3 wheel 或标签。

## 基线与分支

- P2 精确 head `3319c9f412ece4309982bcc2711ca955b06f10d4`。
- PR #29 在重新核对远程规则、精确 head 和既有原生证据后以 merge commit 合入。
- 合入提交 `8bafe871cae9f7f5f417f302a9b9bfbb663978ee`。
- P3 分支 `codex/standing-permissions-p3`。旧分支清理一度被自动审批服务容量故障阻止；
  随后重新核实 PR 与远程 tip，通过精确 tip 租约删除了本任务旧分支，其他任务分支未动。
- 用户的 frontend/pnpm-workspace.yaml 和本地同事安装指南未纳入开发改动。

## 已验证范围

v6 增加策略、任务模式、宿主、准备、租约、转换和管理事件；迁移不生成授权、不改 ACL。
跨宿主转换意图先落盘，SQL 事务不跨 IPC/ACL 操作。忙时保留旧策略，清理失败保留待修复。
Run 成功事务在写 Context 前核实持久租约及现行策略，保留既有终态和 outbox 事务。

命名管道使用固定协议、显式 owner/SYSTEM ACL，拒绝远程与 AppContainer，核实进程创建信息。
发现并修正拒绝请求后的重建空档：保留管道对象，响应确认后断开，再接收下一客户端。
正式 NTFS 后端使用对象句柄及身份，登记意图，只移除独有 capability 的 ACE，不恢复旧 DACL。

测试组存在重叠，不相加为无重复总数：

| 测试组 | 结果 | 范围限制 |
| --- | --- | --- |
| P2 基线精选 | 69 passed | 不替代 P3 原生门槛 |
| permission_transactions + policy + preparation | 46 passed | 内存事务及纯策略 |
| permission_storage/control 与既有 memory/continuation/upgrade/environment/resources | 58 passed, 1 skipped | 仓库内隔离数据库 |
| permission_backend_native | 4 passed | 真实 NTFS 准备/复用/继承/补偿/其他 ACE 保留；不包含工具进程矩阵 |
| backend_native + hosts_native + control + transactions 最终复验 | 14 passed | 两真实宿主空闲转换、忙时拒绝、实际宿主强退和显式修复 |
| hosts_native 补充复验 | 4 passed | 加入“不受影响的收窄任务仍运行时，可清理空闲的更宽策略” |
| permission_cli + transactions + storage 最终复验 | 19 passed | 默认继承、显式转换、修订、已采用策略禁用后不隐式退回完整用户权限 |
| permission_storage v6 故障复验 | 12 passed | 增加 v1–v5 在 v6 提交前故障、全库回滚、WAL 一致备份和重试 |
| sandbox_initialization（显式启用隔离解释器预检） | 7 passed | 普通用户查询五对象、拒绝项目内提权组件、隔离解释器完整依赖；不修改系统 ACL |
| Web Kernel/SQL/Run manager/chat stability | 39 passed | 聊天、取消、Context/终态和投影事务；921.20 秒 |
| 正式 current-user CLI/SDK/HTTP 回归 | 1 passed | 实际修改、测试、续聊、跨目录、模型失败/截断；39.62 秒；使用确定性 Provider |

初次管道测试 8 passed / 1 failed，原因是拒绝错误环境后监听重建竞态；修复后的两项管道测试通过。
之前普通执行沙箱拒绝测试文件或管道，审批服务一度报 Selected model is at capacity；这些失败单独保留。

两次旧版强退夹具失败使用了 venv 启动器 PID；实际 Python 宿主仍存活，修复器正确拒绝接管。
改为在该测试实际宿主中注入 `os._exit(41)` 后，真实退出与恢复通过；未把 PID 消失当作退出证明。
进程对象在退出后仍可能可查询，因此存活检查同时检查 Windows 进程对象的信号状态。

阶段提交：`ba616f5` 保存策略/v6/完成事务；`78571e1` 保存原生准备、跨窗口协调与租约记录。
后续 CLI 和初始化仍为开发实现，不能以这两个提交的通过项目替代完整 P3 放行。

## 正式初始化的失败与修复

1. `var/p3-cli-setup-20260916.jsonl`：复制已安装 Git 时发现硬链接 DLL，停止于普通用户准备阶段。
   修正为逐字节复制到全新独立文件，并核对源稳定性与目标单链接；不将硬链接带入隔离副本。
2. `var/p3-cli-setup-20260916-b.jsonl`：管理员组件执行失败，缺少 `pywintypes` Python 导入入口。
   失败记录保留在第一套隔离 home；普通用户只读核查五个固定对象，本次 capability 的 ACE 数均为 0。
   没有修改业务目录权限，不能把组件已复制当作初始化成功。
3. 增加固定路径的扩展模块导入桥接，并在提权前使用隔离解释器执行只读预检。
   预检已通过。使用新隔离 home 的重试记录为 `var/p3-cli-setup-20260916-c.jsonl`；
   当前仍等待 Windows UAC 确认，尚无正式初始化成功结论。

失败受保护组件为 `C:\Program Files\AgentHub\Sandbox\9c878ae75ede41d989ba9cd4851b9ba2`，
不自动删除或覆写以掩盖问题。保留失败组件、清单和初始化记录供审查。

## 可复核证据

- `var/p3-coordination-20260916.xml`：14 项，SHA-256
  `0f87dfb18c420610fc6d901a64be7980446d0e632a180da138e3eb5d23d98b1d`。
- `var/p3-permission-final-20260916.xml`、`var/p3-web-20260916.xml`、
  `var/p3-initializer-preflight-20260916.xml` 保存对应最后检查。
- `var/p3-affected-scope-20260916.xml` 和 `var/p3-v6-faults-20260916.xml`
  保存新增的范围相关协调和五版本迁移故障测试。
- `var/p3-checkpoint-20260916.json` 保存当时源码、449 项 sidecar 输入及上述报告哈希；
  SHA-256 `14e4af549f5817bfaba3b8d427eb534471cfe3e4b741d66ebabe47a5d2adc85c`。
  该汇总明确记录 dirty 工作树，不声称后续编辑与这份快照字节相同；未执行 sidecar 构建。
- `scripts/record-permission-p3-evidence.py` 可为后续检查点生成新的记录，拒绝覆盖已有证据。

## 尚需验收

正式 CLI 四工具权限矩阵、真实交互窗口保留输入、初始化组件保护及完整故障恢复、
LPAC 控制管道拒绝、DeepSeek 和真实终端尚需新实现证据。
两进程协调已有独立测试证据，但不能代替真实终端中正在编辑输入的场景。
现有 P2 实验初始化和旧 DeepSeek 成功不作为正式安装器或 P3 的通过证明。

不升级日常 .agenthub；不安装开机项，不重启，不发布远程 Release。
移动本身不代表撤权：先停机、核实撤权，再由用户整理资料。不同进程不复用业务身份。

## 接口依据

[Microsoft 命名管道访问位](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights)
说明默认 ACL 范围及 FILE_CREATE_PIPE_INSTANCE 与追加位重叠；实现使用显式 ACL 和独立客户端访问位。
[DisconnectNamedPipe](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-disconnectnamedpipe)
说明断开会丢弃未读数据，因此协议在断开前确认响应已读，保持 I/O 等待有界。

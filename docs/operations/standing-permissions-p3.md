# 长期权限开发入口（P3，尚未发行）

此文对应源码中的 v6 开发状态。包版本仍为 0.2.2，不代表已经发布 0.2.3。
请用独立 `AGENTHUB_HOME` 验收，不升级日常状态；完整初始化及正式 CLI 原生验收通过前不用于个人资料。

## 初始化与策略

`sandbox setup` 先展示具体 Python 3.11、PowerShell 7、Git、uv 及五个固定系统查询对象。
批处理增加 `--apply` 才执行。隔离副本位于状态目录，管理员组件位于
`C:\Program Files\AgentHub\Sandbox`；先核对逐文件哈希和受保护 ACL，再运行固定组件。
初始化需要 Windows UAC 确认，普通任务不会请求提权。默认没有开机项，不自动重启。

```powershell
$env:AGENTHUB_HOME = 'D:\AgentHubTest\Home'
agenthub --json sandbox setup
agenthub --json sandbox setup --apply
agenthub --json sandbox doctor
agenthub --json sandbox self-test
agenthub --json permissions setup --revision 0 --write-dir D:\AgentHubTest\Work --read-dir D:\AgentHubTest\Archive
agenthub --json permissions check D:\AgentHubTest\Work\example.py --operation modify
agenthub --cwd D:\AgentHubTest\Work
```

Work 和 Archive 应为独立根。Private 不加入授权即可保持拒绝。继续拒绝可修改父目录中的只读/禁止子树。
状态、凭据、宿主解释器、共享模块和可信初始化组件强制保护；包含它们的宽泛可写根不能静默跳过保护。
初始化不生成业务授权；旧 trust 不转换成新权限。

策略启用后，新任务自动继承；旧任务仍沿用保存模式。
禁用已采用的长期策略不会自动让新任务回到完整用户权限；此时受限执行拒绝启动，需重新启用或显式选择 current-user。
显式 `--sandbox windows` 转换旧任务；显式 `--sandbox current-user` 回到当前用户边界。
`--permissions custom --read-dir ... --write-dir ...` 只允许收窄。`/new` 继承选择方式，`/cd` 不增加授权。
`/permissions` 是只读展示，不创建模型客户端，也不为了查看权限迁移数据库。

## 空闲转换与修复

所有脚本修改都需 `--revision N`，先 `permissions` 读取版本。
`grant/protect/unprotect/revoke/enable/disable` 使用 CAS，不按时间戳覆盖他人的修改。
存在受影响活动 Run 时返回占用（3），用户取消后重试。空闲窗口通过本机管道清理旧身份并保持打开。
清理未确认不会报告改权成功；策略和失败证据保留，相关实例禁止再次发放租约。

`sandbox repair` 只处理有持久记录、进程身份和锁均核实已退出的宿主。
它不扫描全盘找移动对象，不覆盖旧 DACL，不通过猜测 PID 杀进程。
移动文件不代表撤权；先停止执行、核实旧身份清理，再由用户整理资料。

`sandbox uninstall --apply` 撤销本组件的固定系统查询 ACE；要求策略禁用且准备实例全部清理。
当前实现保留状态、历史、隔离副本和受保护组件文件，不能把该命令描述为物理删除全部安装文件。
被中断的部分组件安装若缺少可核实文件，会拒绝执行；保留登记信息后人工审查，不自动覆盖它。

## 数据与测试

v1–v5 经原有迁移锁、会话锁检查、SQLite backup 和单事务升级 v6。迁移不修改业务 ACL。
旧客户端拒绝新 schema；回退必须使用匹配的旧程序及升级前备份，新会话不会出现在旧备份中。

```powershell
.\scripts\run-tests.ps1 -Stack system -Module sandbox -Type unit
.\scripts\run-tests.ps1 -Stack system -Module sandbox -Type integration
```

完整正式 CLI 测试需要显式 `AGENTHUB_P3_SETUP_HOME` 指向已经完成正式初始化的隔离状态，
然后运行 `tests/test_permission_cli_native.py`。不自动调用 UAC，不使用真实模型凭据。
该测试使用真实 CLI/SDK/HTTP/受限工具和确定性模型响应；它不替代 DeepSeek 与真实终端验收。

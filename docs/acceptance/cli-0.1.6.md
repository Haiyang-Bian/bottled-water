# AgentHub 0.1.6 会话交互验收

本版新增 `-c`、`-r` / `--resume` 无参数选择器、`resume` 子命令、`/resume`、
`/new` 和 `/history`。新会话在首次任务前只存在于内存，续聊按实际 Run 排序。
使用现有 schema v2，旧配置和会话无需重新初始化。

会话选择器支持方向键、搜索、翻页、Enter 和 Esc；恢复后回显最近 3 轮，
最多 12,000 字符，完整记录可以分页查看。浏览不构造模型客户端。

## 验证证据

- 会话、原有工具执行和诊断测试：17 通过，`var/cli-016.xml`。
- 新增完整键盘恢复测试：1 通过，`var/cli-016-keyboard.xml`。使用真实 SDK、
  本机工具及确定性模型服务，验证历史进入下一请求且不重复。
- 独立安装：8 通过，`var/install-016.log`，包含无 ID 键盘恢复入口。
- Web/桌面入口回归：13 通过，`var/web-016.xml`。
- 键盘测试使用 prompt_toolkit pipe input，不等于真实终端视觉验收。
- 本版未调用真实 Provider，未重建桌面二进制；留至 0.1.7 最终验收。

## 安装

```powershell
uv tool install --force --python 3.11 ".\dist\agenthub_system-0.1.6-py3-none-any.whl[cli]"
```

保留现有 `.agenthub`。不要删除状态库来解决会话选择问题。
wheel 校验信息存放在 `dist/agenthub_system-0.1.6.json`。

# AgentHub 0.1.7 终端交互验收

0.1.7 保留滚动式 CLI，使用 prompt_toolkit 输入和 Rich 展示。会话恢复默认回显
最近 3 轮；流式消息、工具摘要、动态阶段、只读工具详情、多行编辑均在 CLI 宿主实现。
Runtime、Web 执行循环和 SQLite schema v2 保持原有职责与兼容边界。

## 可复现入口

```powershell
.\scripts\verify-cli-install.ps1
.\scripts\verify-cli-upgrade.ps1
uv pip install --python .venv/Scripts/python.exe pywinpty pyte Pillow
.venv\Scripts\python.exe -B scripts/accept-cli-terminal.py --python var/cli-install-validation/tools/agenthub-system/Scripts/python.exe --output var/terminal-017-color
```

ConPTY 验收使用真实 Windows 终端和本机进程，模型为确定性 HTTP fixture。
PNG 根据捕获的 VT 字符及颜色缓冲重建，不是桌面截图。脚本显式设置现代终端能力；
无色降级另行检查，避免继承执行环境的 `NO_COLOR=1` / `TERM=dumb` 而误报彩色通过。

真实 Provider 使用 `scripts/accept-cli-live-ui.py`，必须显式指定 `--source-home`、
`--profile`、已安装 `--python` 及 `--output`。它在独立临时 Git 项目执行修复和 unittest，
再通过无 ID 列表恢复并执行后续任务。凭据不进入命令行和验收记录。

## 验证结果与证据边界

- 键盘、渲染及会话逻辑：22 通过，`var/cli-017-unit-final.xml`。
- 独立 wheel 安装和真实工具循环：8 通过，`var/install-017-final.log`。
- 上下文/续接/诊断/依赖边界：17 通过、1 项升级测试由独立入口执行，`var/shared-017.xml`。
- 从真实 0.1.0 wheel 创建的状态升级：1 通过，`var/upgrade-0.1.7.xml`。
- Web/桌面入口：13 通过，`var/web-017.xml`。
- ConPTY 行为及画面：`var/terminal-017-release`，含真实进程树取消、窗口缩放、
  彩色显示及滚动历史断言。终态后代码末尾与输入框仍然可见。
- 真实 DeepSeek：`var/live-ui-017-release` 和 `var/live-plain-017-release`，
  分别记录 JSONL 修复、无 ID 恢复后的工具任务，以及纯文本修复/测试/diff。
- 桌面 sidecar：`var/sidecar-017-release-build.log` 和 `var/sidecar-017-release-smoke.log`。
- OpenAI-compatible 未配置真实 profile；确定性 HTTP fixture 不代表真实 Provider 验收。
- 初次终端脚本误选 WinPTY 后已改为明确 ConPTY；初次真实验收脚本的 SQLite
  只读连接未显式关闭，导致临时目录清理失败。保留初次失败记录，并以修正后复验为准。

版本与 wheel SHA-256 通过 `scripts/verify-release-history.py --through 7` 绑定本地标签。
`scripts/record-cli-release.py` 对照已安装的每个 Python 文件与 wheel 字节，汇总
提交、校验值、测试、真实 Provider Run ID/终态/用量到
`dist/agenthub_system-0.1.7-acceptance.json`。本轮未执行 Docker 构建。

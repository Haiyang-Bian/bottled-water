# agent_cli

本地 CLI 宿主、输入输出及依赖组装。

`app` / `sessions` 管理会话草稿、只读目录与历史、跨进程锁和恢复。
`selection` / `input` 使用 prompt_toolkit 实现局部列表与输入编辑。
`presentation` 从已有事件派生显示状态，`rendering` 区分 Rich、纯文本及 JSONL。
Rich 与输入界面交替控制终端；历史回显及工具详情不会追加模型上下文或执行副作用。
SQLite 查询位于存储适配器，Run 生命周期、终态和取消仍由 Runtime 管理。

不得依赖 Web 的 app、db 或 ORM。

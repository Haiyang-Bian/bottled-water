"""Create the approved subsystem skeleton without touching existing implementations."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AREAS = {
    "agent_contracts": "跨领域执行、授权和资源契约。",
    "agent_subsystems": "可复用领域子系统命名空间。",
    "agent_subsystems/execution": "公共 AgentLoop 和 AgentExecutor。",
    "agent_subsystems/scheduling": "通用调度策略；本期抽取 SingleAgentPolicy。",
    "agent_subsystems/context": "会话上下文装配和预算。",
    "agent_subsystems/tools": "工具注册、验证、授权和调用。",
    "agent_subsystems/workspaces": "目录、文件、进程和 Git 资源接口。",
    "agent_subsystems/observability": "事件消费、脱敏和公共日志。",
    "agent_subsystems/mcp": "待迁移：MCP 连接及调用生命周期。",
    "agent_subsystems/skills": "待迁移：Skill 包、依赖和运行器。",
    "agent_subsystems/workflow": "待迁移：Workflow 图和执行。",
    "agent_subsystems/content": "待迁移：文档及产物处理。",
    "agent_subsystems/external_agents": "待迁移：外部智能体集成。",
    "agent_adapters": "宿主显式选择和注入的具体驱动。",
    "agent_adapters/local": "本地文件、Windows 进程和 Git。",
    "agent_adapters/storage": "本地 SQLite 和会话互斥。",
    "agent_adapters/credentials": "凭据引用、环境变量和 Windows DPAPI。",
    "agent_cli": "本地 CLI 宿主、输入输出及依赖组装。",
}

for name, description in AREAS.items():
    directory = ROOT / "src" / name
    directory.mkdir(parents=True, exist_ok=True)
    for filename, contents in {
        "README.md": f"# {name}\n\n{description}\n\n不得依赖 Web 的 app、db 或 ORM。\n",
        "__init__.py": f'"""{description}"""\n',
    }.items():
        target = directory / filename
        if not target.exists():
            target.write_text(contents, encoding="utf-8")

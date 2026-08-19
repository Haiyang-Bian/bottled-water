from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.tools.toolboxes import TOOLBOXES as TOOLBOXES
from app.services.tools.toolboxes import get_official_toolbox as get_official_toolbox

@dataclass(frozen=True)
class BuiltinTool:
    name: str
    display_name: str
    category: str
    description: str
    permissions: tuple[str, ...]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.name,
            "tool_id": self.name,
            "name": self.name,
            "display_name": self.display_name,
            "category": self.category,
            "description": self.description,
            "type": "builtin",
            "status": "active",
            "version": "1.0.0",
            "permissions": list(self.permissions),
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "tags": list(self.tags),
            "config": {"builtin": True},
            "is_builtin": True,
        }


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or []}


def _document_artifact_schema() -> dict[str, Any]:
    return _schema(
        {
            "conversation_id": {"type": "string"},
            "title": {"type": "string"},
            "subtitle": {"type": "string"},
            "body": {"type": "string"},
            "template": {
                "type": "string",
                "description": "正式文档模板：report/proposal/prd/meeting/lab_report",
            },
            "content_model": {
                "type": "object",
                "description": (
                    "结构化 DocumentModel，含 cover/toc/metadata/sections/blocks/tables/"
                    "callouts/signatures/appendix；blocks 支持 paragraph/heading/list/"
                    "table/callout/quote/image/divider/page_break。"
                ),
            },
        },
        ["conversation_id"],
    )


BUILTIN_TOOLS: dict[str, BuiltinTool] = {
    "file.upload": BuiltinTool(
        "file.upload",
        "上传文件",
        "file",
        "接收用户文件并写入 FileAsset，供消息、知识库和工具链复用。",
        ("file:upload",),
        _schema({"conversation_id": {"type": "string"}, "purpose": {"type": "string"}}),
        _schema({"file": {"type": "object"}}),
        ("upload", "attachment"),
    ),
    "file.extract_text": BuiltinTool(
        "file.extract_text",
        "提取文本",
        "file",
        "从 PDF、Word、Excel、PPT、Markdown、HTML、代码和图片入口提取可供模型读取的文本。",
        ("file:read",),
        _schema({"file_id": {"type": "string"}}, ["file_id"]),
        _schema({"text": {"type": "string"}, "metadata": {"type": "object"}}),
        ("pdf", "docx", "xlsx", "pptx", "ocr-entry"),
    ),
    "file.preview": BuiltinTool(
        "file.preview",
        "文件预览",
        "file",
        "返回文件预览文本、预览模式和下载地址。",
        ("file:read",),
        _schema({"file_id": {"type": "string"}}, ["file_id"]),
        _schema({"preview_text": {"type": "string"}, "mode": {"type": "string"}}),
    ),
    "file.convert": BuiltinTool(
        "file.convert",
        "文件转换",
        "file",
        "把上传文件转换为 PDF、DOCX、XLSX、PPTX、Markdown、HTML、JSON 或 CSV。",
        ("file:read", "file:write"),
        _schema({"file_id": {"type": "string"}, "format": {"type": "string"}}, ["file_id", "format"]),
        _schema({"filename": {"type": "string"}, "media_type": {"type": "string"}, "size": {"type": "integer"}}),
    ),
    "file.summarize": BuiltinTool(
        "file.summarize",
        "文件摘要",
        "file",
        "基于提取文本生成短摘要。",
        ("file:read",),
        _schema({"file_id": {"type": "string"}, "max_chars": {"type": "integer"}}),
        _schema({"summary": {"type": "string"}}),
    ),
    "file.embed": BuiltinTool(
        "file.embed",
        "文件向量化",
        "file",
        "生成本地确定性向量表示，便于演示知识库索引流程。",
        ("file:read",),
        _schema({"file_id": {"type": "string"}}, ["file_id"]),
        _schema({"embedding": {"type": "array"}}),
    ),
    "file.read": BuiltinTool(
        "file.read",
        "读取工作区文件",
        "filesystem",
        "读取后端工作区中被授权的项目文件或上传文件。",
        ("file:read",),
        _schema({"path": {"type": "string"}, "file_id": {"type": "string"}}),
        _schema({"content": {"type": "string"}}),
    ),
    "file.write": BuiltinTool(
        "file.write",
        "写入工作区文件",
        "filesystem",
        "在受控工作区写入 AI 构建工具或项目快照文件。",
        ("file:write",),
        _schema({"path": {"type": "string"}, "content": {"type": "string"}}),
        _schema({"path": {"type": "string"}, "size": {"type": "integer"}}),
    ),
    "artifact.create_pdf": BuiltinTool(
        "artifact.create_pdf",
        "生成 PDF",
        "artifact",
        "生成真实 PDF 文件并创建聊天产物卡片。",
        ("artifact:create", "artifact:export"),
        _document_artifact_schema(),
        _schema({"artifact": {"type": "object"}, "export_url": {"type": "string"}}),
    ),
    "artifact.create_docx": BuiltinTool(
        "artifact.create_docx",
        "生成 Word",
        "artifact",
        "生成 DOCX 产物并提供预览与导出。",
        ("artifact:create",),
        _document_artifact_schema(),
        _schema({"artifact": {"type": "object"}}),
    ),
    "artifact.create_xlsx": BuiltinTool(
        "artifact.create_xlsx",
        "生成 Excel",
        "artifact",
        "生成 XLSX 表格产物并提供预览与导出。",
        ("artifact:create",),
        _schema({"conversation_id": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"}}, ["conversation_id"]),
        _schema({"artifact": {"type": "object"}}),
    ),
    "artifact.create_pptx": BuiltinTool(
        "artifact.create_pptx",
        "生成 PPT",
        "artifact",
        "生成 PPTX 演示产物并提供预览与导出。",
        ("artifact:create",),
        _schema({"conversation_id": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"}}, ["conversation_id"]),
        _schema({"artifact": {"type": "object"}}),
    ),
    "artifact.create_html": BuiltinTool(
        "artifact.create_html",
        "生成 HTML",
        "artifact",
        "创建 HTML/Web 产物。",
        ("artifact:create",),
        _schema({"conversation_id": {"type": "string"}, "title": {"type": "string"}, "html": {"type": "string"}}, ["conversation_id"]),
        _schema({"artifact": {"type": "object"}}),
    ),
    "artifact.create_web_app": BuiltinTool(
        "artifact.create_web_app",
        "生成 Web App",
        "artifact",
        "创建可预览、编辑、Diff、部署的 Web 应用产物。",
        ("artifact:create",),
        _schema({"conversation_id": {"type": "string"}, "title": {"type": "string"}, "html": {"type": "string"}}, ["conversation_id"]),
        _schema({"artifact": {"type": "object"}}),
    ),
    "artifact.export": BuiltinTool(
        "artifact.export",
        "导出产物",
        "artifact",
        "返回产物导出地址和默认格式。",
        ("artifact:export",),
        _schema({"artifact_id": {"type": "string"}, "format": {"type": "string"}}, ["artifact_id"]),
        _schema({"export_url": {"type": "string"}}),
    ),
    "artifact.preview": BuiltinTool(
        "artifact.preview",
        "预览产物",
        "artifact",
        "返回产物预览地址。",
        ("artifact:read",),
        _schema({"artifact_id": {"type": "string"}}, ["artifact_id"]),
        _schema({"preview_url": {"type": "string"}}),
    ),
    "artifact.revise": BuiltinTool(
        "artifact.revise",
        "修订产物",
        "artifact",
        "更新产物文件并产生新版本。",
        ("artifact:update",),
        _schema({"artifact_id": {"type": "string"}, "files": {"type": "object"}, "summary": {"type": "string"}}, ["artifact_id", "files"]),
        _schema({"artifact": {"type": "object"}}),
    ),
    "artifact.diff": BuiltinTool(
        "artifact.diff",
        "产物 Diff",
        "artifact",
        "计算当前产物与上一版本差异。",
        ("artifact:read",),
        _schema({"artifact_id": {"type": "string"}}, ["artifact_id"]),
        _schema({"diff": {"type": "object"}}),
    ),
    "sandbox.run": BuiltinTool(
        "sandbox.run",
        "沙箱运行",
        "runtime",
        "在受控沙箱中执行命令，返回真实 stdout/stderr/exit_code。",
        ("sandbox:run",),
        _schema(
            {
                "command": {"type": "string"},
                "sandbox_id": {"type": "string"},
                "workdir": {"type": "string"},
                "timeout": {"type": "integer"},
            },
            ["command"],
        ),
        _schema(
            {
                "stdout": {"type": "string"},
                "stderr": {"type": "string"},
                "exit_code": {"type": "integer"},
                "capability_level": {"type": "string"},
            }
        ),
    ),
    "terminal.start": BuiltinTool(
        "terminal.start",
        "Interactive terminal start",
        "runtime",
        "Start a controlled interactive CLI process in the workspace sandbox. Use this for init/scaffold commands that prompt for input.",
        ("sandbox:run",),
        _schema(
            {
                "command": {"type": "string"},
                "sandbox_id": {"type": "string"},
                "workspace_id": {"type": "string"},
                "conversation_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "task_id": {"type": "string"},
                "workdir": {"type": "string"},
                "timeout": {"type": "integer"},
                "timeout_seconds": {"type": "integer"},
                "env": {"type": "object"},
            },
            ["command"],
        ),
        _schema(
            {
                "session_id": {"type": "string"},
                "session_status": {"type": "string"},
                "transport": {"type": "string"},
                "stdout_tail": {"type": "string"},
                "stderr_tail": {"type": "string"},
                "exit_code": {"type": "integer"},
                "files": {"type": "array"},
            }
        ),
        ("terminal", "interactive", "cli", "scaffold", "init"),
    ),
    "terminal.send": BuiltinTool(
        "terminal.send",
        "Interactive terminal send",
        "runtime",
        "Send stdin text to a running controlled interactive terminal session.",
        ("sandbox:run",),
        _schema({"session_id": {"type": "string"}, "input": {"type": "string"}}, ["session_id", "input"]),
        _schema(
            {
                "session_id": {"type": "string"},
                "session_status": {"type": "string"},
                "stdout_tail": {"type": "string"},
                "stderr_tail": {"type": "string"},
                "exit_code": {"type": "integer"},
            }
        ),
        ("terminal", "interactive", "stdin"),
    ),
    "terminal.wait_for": BuiltinTool(
        "terminal.wait_for",
        "Interactive terminal wait",
        "runtime",
        "Wait until a running interactive terminal prints one of the requested text patterns, or until the wait times out.",
        ("sandbox:run",),
        _schema(
            {
                "session_id": {"type": "string"},
                "patterns": {"type": "array", "items": {"type": "string"}},
                "timeout_ms": {"type": "integer"},
                "timeout": {"type": "integer"},
            },
            ["session_id", "patterns"],
        ),
        _schema(
            {
                "matched": {"type": "boolean"},
                "matched_pattern": {"type": "string"},
                "session_status": {"type": "string"},
                "stdout_tail": {"type": "string"},
                "stderr_tail": {"type": "string"},
                "exit_code": {"type": "integer"},
            }
        ),
        ("terminal", "interactive", "wait"),
    ),
    "terminal.snapshot": BuiltinTool(
        "terminal.snapshot",
        "Interactive terminal snapshot",
        "runtime",
        "Return current output, status, exit code, duration, and generated files for a running terminal session.",
        ("sandbox:run",),
        _schema({"session_id": {"type": "string"}}, ["session_id"]),
        _schema(
            {
                "session_id": {"type": "string"},
                "session_status": {"type": "string"},
                "stdout_tail": {"type": "string"},
                "stderr_tail": {"type": "string"},
                "exit_code": {"type": "integer"},
                "files": {"type": "array"},
            }
        ),
        ("terminal", "interactive", "status"),
    ),
    "terminal.stop": BuiltinTool(
        "terminal.stop",
        "Interactive terminal stop",
        "runtime",
        "Terminate a running interactive terminal session and return its final output snapshot.",
        ("sandbox:run",),
        _schema({"session_id": {"type": "string"}}, ["session_id"]),
        _schema(
            {
                "session_id": {"type": "string"},
                "session_status": {"type": "string"},
                "stdout_tail": {"type": "string"},
                "stderr_tail": {"type": "string"},
                "exit_code": {"type": "integer"},
                "files": {"type": "array"},
            }
        ),
        ("terminal", "interactive", "stop"),
    ),
    "browser.preview": BuiltinTool(
        "browser.preview",
        "浏览器预览",
        "runtime",
        "为 Web 产物返回浏览器预览地址。",
        ("browser:preview",),
        _schema({"artifact_id": {"type": "string"}, "url": {"type": "string"}}),
        _schema({"preview_url": {"type": "string"}}),
    ),
    "db.inspect": BuiltinTool(
        "db.inspect",
        "数据库检查",
        "backend",
        "检查当前数据库表结构摘要。",
        ("db:inspect",),
        _schema({}),
        _schema({"tables": {"type": "array"}}),
    ),
    "api.test": BuiltinTool(
        "api.test",
        "API 测试",
        "backend",
        "执行真实 HTTP/ASGI API 调用并返回状态断言。",
        ("api:test",),
        _schema(
            {
                "method": {"type": "string"},
                "path": {"type": "string"},
                "headers": {"type": "object"},
                "body": {},
                "expected_status": {"type": "integer"},
            }
        ),
        _schema({"status": {"type": "string"}, "status_code": {"type": "integer"}}),
    ),
    "test.run": BuiltinTool(
        "test.run",
        "运行测试",
        "qa",
        "在受控沙箱中执行 pytest/ruff/npm test/pnpm test 等真实测试命令。",
        ("test:run",),
        _schema(
            {
                "command": {"type": "string"},
                "sandbox_id": {"type": "string"},
                "workdir": {"type": "string"},
                "timeout": {"type": "integer"},
            },
            ["command"],
        ),
        _schema(
            {
                "status": {"type": "string"},
                "stdout": {"type": "string"},
                "stderr": {"type": "string"},
                "exit_code": {"type": "integer"},
            }
        ),
    ),
    "git.status": BuiltinTool(
        "git.status",
        "Git 状态",
        "git",
        "读取当前 Agent 独立工作树的分支、HEAD 和变更摘要。",
        ("file:read",),
        _schema({}),
        _schema(
            {
                "branch": {"type": "string"},
                "head_commit": {"type": "string"},
                "dirty": {"type": "boolean"},
                "summary": {"type": "string"},
            }
        ),
        ("git", "worktree", "status"),
    ),
    "git.diff": BuiltinTool(
        "git.diff",
        "Git 差异",
        "git",
        "读取当前 Agent 工作树内的未提交或已暂存差异。",
        ("file:read",),
        _schema({"staged": {"type": "boolean"}, "path": {"type": "string"}}),
        _schema({"patch": {"type": "string"}, "truncated": {"type": "boolean"}}),
        ("git", "worktree", "diff"),
    ),
    "git.commit": BuiltinTool(
        "git.commit",
        "Git 提交",
        "git",
        "在当前 Agent 独立分支创建普通提交；不支持 amend、rebase 或历史改写。",
        ("file:read", "file:write"),
        _schema(
            {
                "message": {"type": "string", "maxLength": 200},
                "paths": {"type": "array", "items": {"type": "string"}},
            },
            ["message"],
        ),
        _schema({"branch": {"type": "string"}, "head_commit": {"type": "string"}}),
        ("git", "worktree", "commit"),
    ),
    "git.integrate": BuiltinTool(
        "git.integrate",
        "Git 协作合并",
        "git",
        "把同一 Conversation 中另一 Agent 的分支安全合并到调用者自己的分支。",
        ("file:read", "file:write"),
        _schema({"source_agent_id": {"type": "string"}}, ["source_agent_id"]),
        _schema(
            {
                "status": {"type": "string"},
                "head_commit": {"type": "string"},
                "conflict_files": {"type": "array"},
                "merge_aborted": {"type": "boolean"},
            }
        ),
        ("git", "worktree", "merge", "collaboration"),
    ),
    "external_agent.invoke": BuiltinTool(
        "external_agent.invoke",
        "调用外部智能体",
        "external_agent",
        (
            "统一调用 Codex、Claude Code、OpenCode 等外部合规 AI Agent。"
            "通过 action=run/probe/cancel/status 与 provider=codex/claude_code/opencode 区分底层算力端口，"
            "后续新增 provider 只需注册适配器，无需新增工具。"
            "AgentHub 授权后，Codex/Claude Code CLI 内部权限确认默认自动同意，但运行目录仍限制在当前工作区。"
        ),
        ("external_agent:run", "external_agent:read", "file:write"),
        _schema(
            {
                "action": {
                    "type": "string",
                    "enum": ["run", "probe", "cancel", "status"],
                    "description": "动作：run 启动任务，probe 探测端口，cancel 取消任务，status 查询任务。",
                },
                "provider": {
                    "type": "string",
                    "description": "外部智能体端口，如 codex、claude_code、opencode；可扩展为其他注册 provider。",
                },
                "prompt": {"type": "string"},
                "task": {"type": "string"},
                "run_id": {"type": "string"},
                "conversation_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "workspace_id": {"type": "string"},
                "cwd": {"type": "string"},
                "timeout_ms": {"type": "integer"},
                "timeout": {"type": "integer"},
                "wait": {"type": "boolean"},
            },
        ),
        _schema(
            {
                "status": {"type": "string"},
                "provider": {"type": "string"},
                "run_id": {"type": "string"},
                "providers": {"type": "array"},
                "degraded": {"type": "array"},
                "events": {"type": "array"},
                "changed_files": {"type": "array"},
                "stdout_tail": {"type": "string"},
                "stderr_tail": {"type": "string"},
                "exit_code": {"type": "integer"},
                "duration_ms": {"type": "integer"},
                "error": {"type": "string"},
            }
        ),
        ("codex", "claude-code", "opencode", "coding-agent", "external-agent", "probe", "cancel"),
    ),
    "external_agent.probe": BuiltinTool(
        "external_agent.probe",
        "探测外部 Coding Agent",
        "external_agent",
        "探测本机 Codex / Claude Code CLI 安装状态，缺失时返回 degraded 和配置提示。",
        ("external_agent:read",),
        _schema({"provider": {"type": "string", "enum": ["codex", "claude_code"]}}),
        _schema({"providers": {"type": "array"}, "degraded": {"type": "array"}}),
        ("codex", "claude-code", "probe"),
    ),
    "external_agent.run_codex": BuiltinTool(
        "external_agent.run_codex",
        "运行 Codex",
        "external_agent",
        "在当前工作区受控目录中启动 Codex CLI 长任务，默认使用 full-auto 自动同意 CLI 内部权限确认，记录输出、文件变更和退出状态。",
        ("external_agent:run", "file:write"),
        _schema(
            {
                "prompt": {"type": "string"},
                "conversation_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "workspace_id": {"type": "string"},
                "cwd": {"type": "string"},
                "timeout_ms": {"type": "integer"},
                "wait": {"type": "boolean"},
            },
            ["prompt"],
        ),
        _schema({"run_id": {"type": "string"}, "status": {"type": "string"}, "changed_files": {"type": "array"}}),
        ("codex", "coding-agent"),
    ),
    "external_agent.run_claude_code": BuiltinTool(
        "external_agent.run_claude_code",
        "运行 Claude Code",
        "external_agent",
        "在当前工作区受控目录中启动 Claude Code CLI 长任务，默认跳过 Claude Code 内部权限确认，记录输出、文件变更和退出状态。",
        ("external_agent:run", "file:write"),
        _schema(
            {
                "prompt": {"type": "string"},
                "conversation_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "workspace_id": {"type": "string"},
                "cwd": {"type": "string"},
                "timeout_ms": {"type": "integer"},
                "wait": {"type": "boolean"},
            },
            ["prompt"],
        ),
        _schema({"run_id": {"type": "string"}, "status": {"type": "string"}, "changed_files": {"type": "array"}}),
        ("claude-code", "coding-agent"),
    ),
    "external_agent.cancel": BuiltinTool(
        "external_agent.cancel",
        "取消外部 Coding Agent",
        "external_agent",
        "取消正在运行的 Codex / Claude Code 任务，并收敛运行记录状态。",
        ("external_agent:run",),
        _schema({"run_id": {"type": "string"}}, ["run_id"]),
        _schema({"run_id": {"type": "string"}, "status": {"type": "string"}}),
        ("cancel",),
    ),
    "external_agent.status": BuiltinTool(
        "external_agent.status",
        "查询外部 Coding Agent 状态",
        "external_agent",
        "查询 Codex / Claude Code 运行记录、输出摘要、文件变更和错误信息。",
        ("external_agent:read",),
        _schema({"run_id": {"type": "string"}}, ["run_id"]),
        _schema({"run_id": {"type": "string"}, "status": {"type": "string"}, "events": {"type": "array"}}),
        ("status",),
    ),
    "security.audit": BuiltinTool(
        "security.audit",
        "安全审计",
        "qa",
        "对操作、产物或配置执行基础风险审计。",
        ("security:audit",),
        _schema({"target": {"type": "string"}}),
        _schema({"risk_score": {"type": "number"}, "findings": {"type": "array"}}),
    ),
    "document.review": BuiltinTool(
        "document.review",
        "文档审查",
        "qa",
        "审查文档结构、遗漏和交付风险。",
        ("document:review",),
        _schema({"text": {"type": "string"}}),
        _schema({"findings": {"type": "array"}}),
    ),
    "deploy.preview": BuiltinTool(
        "deploy.preview",
        "预览部署",
        "deploy",
        "为产物创建预览部署记录。",
        ("deploy:preview",),
        _schema({"artifact_id": {"type": "string"}}, ["artifact_id"]),
        _schema({"deployment": {"type": "object"}}),
    ),
    "deploy.rollback": BuiltinTool(
        "deploy.rollback",
        "部署回滚",
        "deploy",
        "创建回滚记录并返回当前可用预览地址。",
        ("deploy:rollback",),
        _schema({"deployment_id": {"type": "string"}}, ["deployment_id"]),
        _schema({"status": {"type": "string"}}),
    ),
}

LEGACY_BUILTIN_TOOL_ALIASES: dict[str, str] = {
    "external_agent.probe": "external_agent.invoke",
    "external_agent.run_codex": "external_agent.invoke",
    "external_agent.run_claude_code": "external_agent.invoke",
    "external_agent.cancel": "external_agent.invoke",
    "external_agent.status": "external_agent.invoke",
}


def active_builtin_tool_names() -> list[str]:
    return [name for name in BUILTIN_TOOLS if name not in LEGACY_BUILTIN_TOOL_ALIASES]


def builtin_tool_dicts() -> list[dict[str, Any]]:
    return [
        tool.to_dict()
        for name, tool in BUILTIN_TOOLS.items()
        if name in active_builtin_tool_names()
    ]

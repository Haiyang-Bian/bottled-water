"""Pure artifact intent helpers shared by AgentHub and the Runtime adapter loop."""

from __future__ import annotations

from typing import Any


HTML_ARTIFACT_TOOLS = {"artifact.create_html", "artifact.create_web_app"}

ARTIFACT_TOOL_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "artifact.create_html",
        (
            "html",
            "网页",
            "页面",
            "网站",
            "web app",
            "webapp",
            "web project",
            "game",
            "gomoku",
            "gobang",
            "五子棋",
            "小游戏",
            "计算器",
            "表单",
            "看板",
            "登录页",
            "演示页",
        ),
    ),
    ("artifact.create_pdf", ("pdf", "pdf文档", "pdf文件", "预览卡片")),
    ("artifact.create_docx", ("word", "docx", "word文档", "文档", "报告")),
    ("artifact.create_xlsx", ("excel", "xlsx", "表格", "电子表格")),
    ("artifact.create_pptx", ("ppt", "pptx", "幻灯片", "演示文稿")),
)

ARTIFACT_VERBS = (
    "生成",
    "创建",
    "导出",
    "制作",
    "写一个",
    "写一份",
    "做一个",
    "做一份",
    "给我一个",
    "来一个",
    "create",
    "generate",
    "export",
    "make",
)


def detect_artifact_tool(prompt: str) -> str | None:
    """Detect explicit artifact creation intent from the latest user prompt."""

    lower = _normalize_text(prompt)
    has_create_intent = any(_normalize_text(verb) in lower for verb in ARTIFACT_VERBS)
    if not has_create_intent:
        return None
    for tool_name, patterns in ARTIFACT_TOOL_PATTERNS:
        if any(_normalize_text(pattern) in lower for pattern in patterns):
            return tool_name
    if any(token in lower for token in ("说明文档", "项目说明", "说明书", "documentation", "readme")):
        return "artifact.create_pdf"
    return None


def artifact_arguments(tool_name: str, prompt: str) -> dict[str, Any]:
    title = _title_from_prompt(prompt)
    if tool_name in HTML_ARTIFACT_TOOLS:
        return {"title": title, "body": prompt}
    args: dict[str, Any] = {"title": title, "body": prompt}
    if tool_name in {"artifact.create_pdf", "artifact.create_docx"}:
        args["content_model"] = _document_content_model(title, prompt)
    return args


def _document_content_model(title: str, prompt: str) -> dict[str, Any]:
    body = (prompt or title).strip()
    return {
        "title": title,
        "template": "report",
        "cover": {"title": title, "subtitle": "AgentHub 自动生成示例文档"},
        "toc": True,
        "metadata": {"source": "agenthub_forced_artifact_call"},
        "sections": [
            {
                "title": "摘要",
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": (
                            f"本文档根据用户请求“{body[:120]}”生成，用于演示 "
                            "AgentHub 真实产物生成、预览卡片和下载导出链路。"
                        ),
                    }
                ],
            },
            {
                "title": "示例内容",
                "blocks": [
                    {"type": "heading", "level": 3, "text": "核心目标"},
                    {
                        "type": "list",
                        "items": [
                            "生成真实文件，而不是聊天文本模拟。",
                            "产物卡片绑定 artifact_id，可点击预览。",
                            "导出入口返回真实 PDF/Word 文件。",
                        ],
                    },
                    {
                        "type": "table",
                        "headers": ["模块", "演示结果"],
                        "rows": [
                            ["Agent Function Call", "自动调用 artifact.create_* 工具"],
                            ["Artifact Runtime", "持久化真实文件与 HTML/PDF 预览"],
                            ["Chat UI", "显示可点击预览卡片"],
                        ],
                    },
                ],
            },
            {
                "title": "结论",
                "blocks": [
                    {
                        "type": "callout",
                        "text": (
                            "该示例用于验证 AgentHub 的端到端产物交付闭环：用户请求、"
                            "工具调用、文件生成、卡片展示、预览和下载。"
                        ),
                    }
                ],
            },
        ],
    }


def _title_from_prompt(prompt: str) -> str:
    title = (prompt or "").strip().splitlines()[0][:60]
    return title or "AgentHub 产物"


def _normalize_text(value: str) -> str:
    return "".join(str(value or "").lower().split())

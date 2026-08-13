"""
测试 AgentLoop

覆盖：
- 基本执行流程
- 工具调用循环
- 状态报告解析
"""

import pytest

from model_provider import ChatResponse, StreamChunk
from agent_runtime.core.interfaces import AgentContextBuildResult
from agent_runtime.runtime.agent_loop import AgentLoop, _StatusReportStreamFilter
from agent_runtime.core.types import AgentConfig, AgentState, AgentWill

import logging

logger = logging.getLogger(__name__)


class TestAgentLoopBasic:
    """测试 AgentLoop 基本功能"""

    @pytest.fixture
    def agent_config(self):
        return AgentConfig(
            id="coder",
            name="程序员",
            system_prompt="你是一个程序员。",
        )

    @pytest.mark.asyncio
    async def test_run_without_tools(self, agent_config, provider):
        """测试无工具时的基本执行"""
        loop = AgentLoop(agent_config, provider)
        result = await loop.run(
            task="写一个函数",
            blackboard_view={},
            tool_executor=None,
        )

        logger.info(result["work_product"])
        report = result["status_report"]
        logger.info(report.rationale)

        assert report.agent_id == "coder"
        assert report.state == AgentState.COMPLETED
        assert report.will == AgentWill.COMPLETE

    @pytest.mark.asyncio
    async def test_run_parses_status_report(self, agent_config, mock_provider):
        """测试状态报告解析"""
        mock_provider.responses = [
            ChatResponse(
                content='```status_report\n{"state": "running", "will": "execute", "rationale": "继续工作", "confidence": 0.9}\n```'
            ),
        ]

        loop = AgentLoop(agent_config, mock_provider)
        result = await loop.run("任务", {}, None)

        logger.info(result["work_product"])

        report = result["status_report"]

        logger.info(report.rationale)

        assert report.state == AgentState.RUNNING
        assert report.will == AgentWill.EXECUTE
        assert report.confidence == 0.9

    @pytest.mark.asyncio
    async def test_plain_final_text_without_status_report_completes(
        self,
        agent_config,
        mock_provider,
    ):
        mock_provider.responses = [
            ChatResponse(content="这是一个正常的聊天回答。"),
        ]

        loop = AgentLoop(agent_config, mock_provider)
        result = await loop.run("讲一个笑话", {}, None)

        assert result["work_product"] == "这是一个正常的聊天回答。"
        assert result["status_report"].state == AgentState.COMPLETED
        assert result["status_report"].will == AgentWill.COMPLETE

    @pytest.mark.asyncio
    async def test_run_with_tool_calls(self, agent_config, mock_provider, mock_tool_executor):
        """测试工具调用循环"""
        mock_provider.responses = [
            # 第一轮：LLM 返回 tool_calls
            ChatResponse(
                content="",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "file_read", "arguments": '{"path": "/tmp/test.txt"}'},
                    }
                ],
            ),
            # 第二轮：LLM 总结工具结果
            ChatResponse(
                content='文件内容已读取。\n```status_report\n{"state": "completed", "will": "complete"}\n```'
            ),
        ]

        mock_tool_executor._tools = [
            {
                "type": "function",
                "function": {"name": "file_read", "description": "读文件"},
            }
        ]

        loop = AgentLoop(agent_config, mock_provider)
        result = await loop.run("读文件", {}, mock_tool_executor)

        assert len(result["tool_events"]) == 1
        assert result["tool_events"][0]["agent_id"] == "coder"
        assert result["status_report"].state == AgentState.COMPLETED
        assert mock_provider.call_count == 2

    @pytest.mark.asyncio
    async def test_run_uses_context_provider_messages(self, agent_config, mock_provider):
        captured: dict = {}

        async def chat_stream(messages=None, system_prompt=None, tools=None, temperature=0.7, max_tokens=None):
            captured["messages"] = list(messages or [])
            captured["system_prompt"] = system_prompt
            yield StreamChunk(
                content='context ok\n```status_report\n{"state": "completed", "will": "complete"}\n```',
                finish_reason="stop",
            )

        class Provider:
            async def build_agent_context(self, request):
                captured["request"] = request
                return AgentContextBuildResult(
                    messages=[
                        {"role": "system", "content": "context system"},
                        {"role": "assistant", "content": "remembered turn"},
                        {"role": "user", "content": "context user"},
                    ]
                )

        mock_provider.chat_stream = chat_stream
        loop = AgentLoop(agent_config, mock_provider)
        result = await loop.run(
            "runtime prompt with attachment context",
            {},
            None,
            context_provider=Provider(),
            context_metadata={
                "conversation_id": "conv-1",
                "user_message_id": "msg-1",
                "visible_content": "visible user text",
            },
        )

        assert result["status_report"].state == AgentState.COMPLETED
        assert captured["system_prompt"] == "context system"
        assert [message.content for message in captured["messages"]] == [
            "remembered turn",
            "context user",
        ]
        assert captured["request"].metadata["user_message_id"] == "msg-1"
        assert "visible user text" in captured["request"].base_user_prompt
        assert "runtime prompt with attachment context" not in captured["request"].base_user_prompt

    @pytest.mark.asyncio
    async def test_run_dedupes_repeated_artifact_create_calls(
        self,
        agent_config,
        mock_provider,
        mock_tool_executor,
    ):
        mock_provider.responses = [
            ChatResponse(
                content="",
                tool_calls=[
                    {
                        "id": "call_docx_1",
                        "type": "function",
                        "function": {"name": "artifact.create_docx", "arguments": '{"title": "demo"}'},
                    }
                ],
            ),
            ChatResponse(
                content="",
                tool_calls=[
                    {
                        "id": "call_docx_2",
                        "type": "function",
                        "function": {"name": "artifact.create_docx", "arguments": '{"title": "demo 2"}'},
                    }
                ],
            ),
        ]
        mock_tool_executor._tools = [
            {
                "type": "function",
                "function": {"name": "artifact.create_docx", "description": "create docx"},
            }
        ]

        loop = AgentLoop(agent_config, mock_provider)
        result = await loop.run("create a demo docx", {}, mock_tool_executor)

        assert [call["tool_name"] for call in mock_tool_executor.calls] == ["artifact.create_docx"]
        assert len(result["tool_events"]) == 1
        assert mock_provider.call_count == 2
        assert "Word" in result["work_product"]
        assert "已生成真实" not in result["work_product"]
        assert "产物 产物" not in result["work_product"]
        assert "产物产物" not in result["work_product"]

    @pytest.mark.asyncio
    async def test_run_tool_not_found(self, agent_config, mock_provider):
        """测试工具未找到的情况"""
        mock_provider.responses = [
            ChatResponse(
                content="",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "unknown_tool", "arguments": "{}"},
                    }
                ],
            ),
            ChatResponse(
                content='工具未找到。\n```status_report\n{"state": "completed", "will": "complete"}\n```'
            ),
        ]

        loop = AgentLoop(agent_config, mock_provider)
        result = await loop.run("调用未知工具", {}, None)

        # 没有 tool_executor，工具调用会被跳过
        assert result["status_report"].state == AgentState.COMPLETED

    @pytest.mark.asyncio
    async def test_run_recovers_bad_html_tool_arguments_without_template_fallback(
        self,
        agent_config,
        mock_provider,
        mock_tool_executor,
    ):
        mock_provider.responses = [
            ChatResponse(
                content="",
                tool_calls=[
                    {
                        "id": "call_bad_html",
                        "type": "function",
                        "function": {
                            "name": "artifact.create_html",
                            "arguments": '{"title": "broken", "html": "<html>',
                        },
                    }
                ],
            ),
            ChatResponse(content=""),
        ]
        mock_tool_executor._tools = [
            {
                "type": "function",
                "function": {"name": "artifact.create_html", "description": "create html"},
            }
        ]

        loop = AgentLoop(agent_config, mock_provider)
        result = await loop.run("生成一个企业知识库问答 HTML 页面", {}, mock_tool_executor)

        assert mock_tool_executor.calls
        assert mock_tool_executor.calls[0]["tool_name"] == "artifact.create_html"
        assert mock_tool_executor.calls[0]["parameters"] == {
            "title": "生成一个企业知识库问答 HTML 页面",
            "body": "生成一个企业知识库问答 HTML 页面",
        }
        assert result["tool_events"][0]["results"][0]["success"] is True
        assert result["status_report"].state == AgentState.COMPLETED

    @pytest.mark.asyncio
    async def test_run_marks_empty_output_failed_when_tools_fail(
        self,
        agent_config,
        mock_provider,
        mock_tool_executor,
    ):
        mock_provider.responses = [
            ChatResponse(
                content="",
                tool_calls=[
                    {
                        "id": "call_file",
                        "type": "function",
                        "function": {"name": "file_read", "arguments": '{"path": "/missing"}'},
                    }
                ],
            ),
            ChatResponse(
                content='```status_report\n{"state":"completed","will":"complete"}\n```'
            ),
        ]
        mock_tool_executor._tools = [
            {
                "type": "function",
                "function": {"name": "file_read", "description": "read file"},
            }
        ]

        async def fail_execute(_tool_call):
            from agent_runtime import ToolResult

            return ToolResult(
                call_id="call_file",
                success=False,
                result=None,
                error="file not found",
            )

        mock_tool_executor.execute = fail_execute

        loop = AgentLoop(agent_config, mock_provider)
        result = await loop.run("读取缺失文件", {}, mock_tool_executor)

        assert result["status_report"].state == AgentState.FAILED
        assert "file_read 执行失败" in result["work_product"]

    @pytest.mark.asyncio
    async def test_run_max_tool_rounds(self, agent_config, mock_provider):
        """测试工具调用轮数上限"""
        # 模拟 LLM 总是返回 tool_calls
        responses = []
        for _ in range(AgentLoop.MAX_TOOL_ROUNDS + 1):
            responses.append(
                ChatResponse(
                    content="",
                    tool_calls=[
                        {
                            "id": f"call_{_}",
                            "type": "function",
                            "function": {"name": "noop", "arguments": "{}"},
                        }
                    ],
                )
            )
        mock_provider.responses = responses

        loop = AgentLoop(agent_config, mock_provider)
        result = await loop.run("无限工具调用", {}, None)

        assert len(result["tool_events"]) == AgentLoop.MAX_TOOL_ROUNDS


class TestAgentLoopPromptBuilding:
    """测试提示词构建"""

    @pytest.fixture
    def loop(self, mock_provider):
        config = AgentConfig(id="test", name="测试", system_prompt="你是一个测试助手。")
        return AgentLoop(config, mock_provider)

    def test_build_prompt(self, loop):
        bb_view = {
            "recent_history": [
                {"type": "user_message", "content": "你好"},
            ],
            "kv_state": {"status": "ok"},
            "version": 1,
        }
        prompt = loop._build_prompt("写一个函数", bb_view)
        assert "写一个函数" in prompt
        assert "近期历史" in prompt
        assert "状态变量" in prompt
        assert "```status_report" in prompt

    def test_format_blackboard_empty(self, loop):
        text = loop._format_blackboard({})
        assert text == "（无）"

    def test_format_blackboard_full(self, loop):
        bb = {
            "recent_history": [
                {"type": "agent_work", "content": "完成代码"},
                {"type": "user_input", "content": "很好"},
            ],
            "kv_state": {"progress": 50},
            "structured_summaries": [
                {"content": "第一轮摘要"},
                {"content": "第二轮摘要"},
                {"content": "第三轮摘要"},
            ],
            "version": 5,
        }
        text = loop._format_blackboard(bb)
        assert "完成代码" in text
        assert "progress" in text
        assert "历史摘要" in text
        assert "5" in text  # version


class TestAgentLoopStatusParsing:
    """测试状态报告解析"""

    @pytest.fixture
    def loop(self, mock_provider):
        config = AgentConfig(id="test", name="测试", system_prompt="prompt")
        return AgentLoop(config, mock_provider)

    def test_extract_from_code_block(self, loop):
        content = '工作完成。\n```status_report\n{"state": "completed", "will": "complete", "rationale": "搞定了", "confidence": 0.95}\n```'
        report = loop._extract_status_report(content)
        assert report.state == AgentState.COMPLETED
        assert report.will == AgentWill.COMPLETE
        assert report.confidence == 0.95

    def test_extract_invalid_json(self, loop):
        content = "```status_report\nnot json\n```"
        report = loop._extract_status_report(content)
        assert report.state == AgentState.UNKNOWN
        assert report.will == AgentWill.WAIT

    def test_extract_status_report_normalizes_aliases_and_bounds(self, loop):
        content = "\n".join(
            [
                "done",
                "```status_report",
                '{"state": "done", "will": "finish", "blockers": "none", "priority": 99, "confidence": 1.5}',
                "```",
            ]
        )
        report = loop._extract_status_report(content)

        assert report.state == AgentState.COMPLETED
        assert report.will == AgentWill.COMPLETE
        assert report.blockers == ["none"]
        assert report.priority == 10
        assert report.confidence == 1.0

    def test_extract_status_report_from_plain_json_object(self, loop):
        content = 'work result {"state": "error", "will": "blocked", "rationale": "tool failed"}'
        report = loop._extract_status_report(content)

        assert report.state == AgentState.FAILED
        assert report.will == AgentWill.BLOCKED
        assert report.rationale == "tool failed"

    def test_extract_missing_block(self, loop):
        content = "没有任何状态报告"
        report = loop._extract_status_report(content)
        assert report.state == AgentState.UNKNOWN

    def test_remove_status_report(self, loop):
        content = '工作成果。\n```status_report\n{"state": "completed"}\n```\n一些其他内容'
        cleaned = loop._remove_status_report(content)
        assert "工作成果" in cleaned
        assert "status_report" not in cleaned

    def test_stream_filter_hides_generic_status_report_fence(self):
        stream_filter = _StatusReportStreamFilter()

        assert stream_filter.push("visible answer\n") == "visible answer"
        assert stream_filter.push("```\n") == ""
        assert stream_filter.push('status_report\n{"state":"completed","will":"complete"}\n') == ""
        assert stream_filter.push("```\n") == ""
        assert stream_filter.push("final answer") == "\nfinal answer"

    def test_remove_status_report_shaped_json_block(self, loop):
        content = "\n".join(
            [
                "work product",
                "```json",
                '{"state":"completed","will":"complete","confidence":0.95}',
                "```",
            ]
        )

        assert loop._remove_status_report(content) == "work product"

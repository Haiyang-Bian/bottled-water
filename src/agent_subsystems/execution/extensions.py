"""Host execution hooks. Hooks never control the Run lifecycle."""

import json
from dataclasses import replace

from agent_runtime.core.types import AgentState, AgentWill


class ExecutionExtension:
    def __init__(self, agent):
        self.agent = agent

    def build_prompt(self, task, blackboard_view, task_input=None):
        return (
            f"{task}\n\nContext: {json.dumps(blackboard_view, ensure_ascii=False, default=str)}\n"
            "Use tools when needed. Report actual results and failures honestly. "
            "End with a ```status_report JSON block containing state (completed or failed), "
            "will (complete or blocked), blockers and confidence. Do not include private reasoning."
        )

    async def direct_execution(self, task, tool_executor, emit):
        return None

    def select_tools(self, task, tools):
        return tools

    def tools_for_round(self, tools, results):
        return tools or None

    def prepare_calls(self, task, tools, results, messages, content, calls, metadata):
        return content, calls

    def recover_arguments(self, tool_name, task):
        return None

    def after_tool(self, tool_name, result):
        pass

    def summary_instruction(self, results):
        return "Summarize the actual results. State any unresolved failures explicitly."

    def finalize(self, task, text, report, results):
        if report.state == AgentState.UNKNOWN:
            failed = not text.strip() or bool(results and not results[-1].get("success"))
            report = replace(
                report,
                state=AgentState.FAILED if failed else AgentState.COMPLETED,
                will=AgentWill.BLOCKED if failed else AgentWill.COMPLETE,
                blockers=["No final answer or an unresolved tool failure"] if failed else [],
            )
        return text, report

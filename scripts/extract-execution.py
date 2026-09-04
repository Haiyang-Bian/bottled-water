"""Extract the existing loop and its Web product policies without duplicating execution."""
import ast
import re
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source_path = ROOT / "src/agent_runtime/runtime/agent_loop.py"
source = source_path.read_text(encoding="utf-8")
lines = source.splitlines(keepends=True)
cls = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == "AgentLoop")
methods = {n.name: n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def method_text(name):
    node = methods[name]
    start = min([node.lineno, *[d.lineno for d in node.decorator_list]]) - 1
    return "".join(lines[start:node.end_lineno]) + "\n"


product_names = [n for n, node in methods.items() if 875 <= node.lineno <= 1649 and n not in {"_normalize_tool_calls", "_emit_text_response"}]
product_names += ["_build_prompt", "_format_assignment_context", "_format_blackboard"]
helpers = "\n".join(method_text(n) for n in product_names).replace("AgentLoop.", "WebExecutionExtension.")
prepare_start = source.index("            if tool_calls and self._artifact_tool_succeeded")
prepare_end = source.index("            # 如果没有工具调用", prepare_start)
prepare = textwrap.dedent(source[prepare_start:prepare_end])
prepare = prepare.replace("forced_artifact_tool_name", "self.forced_artifact_tool_name").replace("executed_artifact_tools", "self.executed_artifact_tools")
final_start = source.index("        if self._artifact_tool_succeeded(tool_results) and (")
final_end = source.index("        if agent_ctx:", final_start)
final = textwrap.dedent(source[final_start:final_end])
# Rendering stays in the shared loop. Product hooks return values only.
tree = ast.parse(final)
tree.body = [n for n in tree.body]
class RemoveRendering(ast.NodeTransformer):
    def visit_If(self, node):
        if ast.unparse(node.test) == "self.use_streaming":
            return None
        return self.generic_visit(node)
final = ast.unparse(RemoveRendering().visit(tree)).replace("forced_artifact_tool_name", "self.forced_artifact_tool_name")
web = '''"""AgentHub product delivery rules injected into the shared execution loop."""
import json
import re
import uuid
from typing import Any, Dict, List, Optional
from common.artifact_heuristics import HTML_ARTIFACT_TOOLS, artifact_arguments, detect_artifact_tool
from agent_contracts.logging import get_logger
from agent_runtime.core.types import AgentReport, AgentState, AgentWill, ToolCall, ToolResult
from agent_runtime.core.interfaces import ToolExecutor
from model_provider.core.interfaces import ChatMessage
from agent_subsystems.execution.extensions import ExecutionExtension

logger = get_logger(__name__)

class WebExecutionExtension(ExecutionExtension):
    def __init__(self, agent):
        super().__init__(agent)
        self.forced_artifact_tool_name = None
        self.executed_artifact_tools = set()

    build_prompt = lambda self, *args: self._build_prompt(*args)
    select_tools = lambda self, *args: self._filter_tools_for_task(*args)
    summary_instruction = lambda self, results: self._summary_instruction(results)
    recover_arguments = lambda self, *args: self._recover_tool_call_arguments(*args)

    async def direct_execution(self, task, tool_executor, emit):
        if self.agent.system_prompt == "" and self.agent.role.endswith("_executor"):
            return await self._execute_virtual_agent(task, tool_executor, emit)
        return None

    def tools_for_round(self, tools, results):
        return None if self._artifact_tool_succeeded(results) else (tools or None)

    def after_tool(self, name, result):
        if name.startswith("artifact.create_") and result.success:
            self.executed_artifact_tools.add(name)
            self.forced_artifact_tool_name = name

    def prepare_calls(self, task, tools, tool_results, messages, content, tool_calls, context_metadata):
'''
web += textwrap.indent(prepare, "        ") + "        return content, tool_calls\n\n"
web += "    def finalize(self, task, work_product, status_report, tool_results):\n" + textwrap.indent(final, "        ") + "\n        return work_product, status_report\n\n" + helpers
# A helper name includes 'executed_artifact_tools'; only locals are rewritten above.
web = web.replace("self._drop_self.executed_artifact_tools", "self._drop_executed_artifact_tools")
(ROOT / "backend/src/app/services/execution_extension.py").write_text(web, encoding="utf-8")

updated = source
for name in product_names + ["_build_initial_thinking"]:
    updated = updated.replace(method_text(name), "")
updated = re.sub(r"from common.artifact_heuristics import \([\s\S]*?\)\n", "", updated)
updated = updated.replace("from ..", "from agent_runtime.")
updated = updated.replace("from .status_report", "from agent_runtime.runtime.status_report")
updated = updated.replace("logger = get_logger(__name__)", "from .extensions import ExecutionExtension\n\nlogger = get_logger(__name__)")
updated = updated.replace("        max_output_tokens: int | None = None,\n", "        max_output_tokens: int | None = None,\n        extension_factory=ExecutionExtension,\n", 1)
updated = updated.replace("        self.agent = agent_config", "        self.extension = extension_factory(agent_config)\n        self.context_snapshot = None\n        self.agent = agent_config", 1)
updated = updated.replace("self._build_initial_thinking(task)", '"Preparing model input"')
updated = updated.replace('        if self.agent.system_prompt == "" and self.agent.role.endswith("_executor"):\n            return await self._execute_virtual_agent(task, tool_executor, _emit)', '        direct = await self.extension.direct_execution(task, tool_executor, _emit)\n        if direct is not None:\n            return direct')
updated = updated.replace("self._filter_tools_for_task(task, tools)", "self.extension.select_tools(task, tools)")
updated = updated.replace("        forced_artifact_tool_name: str | None = None\n        executed_artifact_tools: set[str] = set()\n", "")
updated = updated.replace("None if self._artifact_tool_succeeded(tool_results) else (tools if tools else None)", "self.extension.tools_for_round(tools, tool_results)")
updated = updated.replace(source[prepare_start:prepare_end], "            content, tool_calls = self.extension.prepare_calls(\n                task, tools, tool_results, messages, content, tool_calls, context_metadata\n            )\n\n")
updated = updated.replace("self._recover_tool_call_arguments", "self.extension.recover_arguments")
start = updated.index('                if (\n                    str(tool_name).startswith("artifact.create_")')
end = updated.index("                tool_results.append(", start)
updated = updated[:start] + "                self.extension.after_tool(str(tool_name), result)\n" + updated[end:]
start = updated.index("            if self._artifact_tool_succeeded(tool_results):")
end = updated.index("            await self._run_checkpoint(", start)
updated = updated[:start] + updated[end:]
updated = updated.replace("self._summary_instruction(tool_results)", "self.extension.summary_instruction(tool_results)")
updated = updated.replace(source[final_start:final_end], '''        original_product = work_product
        work_product, status_report = self.extension.finalize(task, work_product, status_report, tool_results)
        if tool_round >= self.MAX_TOOL_ROUNDS and messages and messages[-1].role == "tool":
            status_report.state = AgentState.FAILED
            status_report.will = AgentWill.BLOCKED
            status_report.blockers = ["tool_round_budget_exhausted"]
        if self.use_streaming and work_product != original_product:
            await self._emit_text_response(_emit, stream_message_id, work_product,
                stream_context=self._stream_context_payload(context_metadata))

''')
updated = updated.replace('                final_content = "工具调用完成，但总结失败。"', "                raise")
updated = updated.replace("self._build_prompt(", "self.extension.build_prompt(")
updated = updated.replace('content=str(result.result if isinstance(result, ToolResult) else result),', 'content=json.dumps({"success": result.success, "result": result.result, "error": result.error}, ensure_ascii=False, default=str),')
updated = updated.replace('                metadata=metadata,\n', '                metadata=metadata,\n                context_snapshot=self.context_snapshot,\n')
updated = updated.replace("        messages = self._agent_context_messages(agent_ctx)", '        messages = self._agent_context_messages(agent_ctx)\n        if self.context_snapshot is not None:\n            messages = [ChatMessage(role=m["role"], content=m["content"]) for m in self.context_snapshot.messages]')
ast.parse(updated)
target = ROOT / "src/agent_subsystems/execution/agent_loop.py"
target.write_text(updated, encoding="utf-8")
source_path.unlink()

# Move executor and generic tools; preserve their implementation, replace all callers.
moves = {
    "agent_runtime.runtime.agent_executor": "agent_subsystems.execution.agent_executor",
    "agent_runtime.runtime.agent_loop": "agent_subsystems.execution.agent_loop",
    "agent_runtime.tools.executor": "agent_subsystems.tools.executor",
    "agent_runtime.tools.registry": "agent_subsystems.tools.registry",
}
for old, new in moves.items():
    old_path = ROOT / "src" / (old.replace(".", "/") + ".py")
    new_path = ROOT / "src" / (new.replace(".", "/") + ".py")
    if old_path.exists():
        code = old_path.read_text(encoding="utf-8").replace("from ..", "from agent_runtime.")
        code = code.replace("from .team_tools", "from agent_runtime.runtime.team_tools")
        new_path.write_text(code, encoding="utf-8")
        old_path.unlink()
for directory in (ROOT / "src", ROOT / "backend/src", ROOT / "backend/tests"):
    for path in directory.rglob("*.py"):
        code = path.read_text(encoding="utf-8-sig")
        new = code
        for old, replacement in moves.items():
            new = new.replace(old, replacement)
        if path.name == "agent_stepper.py":
            new = new.replace("from .agent_loop", "from agent_subsystems.execution.agent_loop")
        if new != code:
            path.write_text(new, encoding="utf-8")

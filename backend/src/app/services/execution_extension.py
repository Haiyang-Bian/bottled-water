"""AgentHub product delivery rules injected into the shared execution loop."""
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
        if tool_calls and self._artifact_tool_succeeded(tool_results):
            artifact_tool_name = self._artifact_tool_name(tool_results) or self.forced_artifact_tool_name
            if self._only_artifact_create_calls(tool_calls):
                logger.info(
                    "Agent skipped duplicate artifact tool calls",
                    agent_id=self.agent.id,
                    tool=artifact_tool_name,
                )
                tool_calls = None
                if not content.strip() or self._looks_like_artifact_argument_fragment(content):
                    content = self._artifact_completion_message(
                        artifact_tool_name,
                        tool_results,
                        task,
                    )
        if not tool_calls:
            forced_tool_call = self._forced_project_delivery_tool_call(
                task,
                tools,
                tool_results,
                messages,
                metadata=context_metadata,
            )
            if forced_tool_call is None and self.forced_artifact_tool_name is None:
                forced_tool_call = self._forced_artifact_tool_call(task, tools)
            if forced_tool_call:
                forced_name = str(forced_tool_call.get("function", {}).get("name") or "")
                if forced_name.startswith("artifact.create_"):
                    self.forced_artifact_tool_name = forced_name
                logger.info(
                    "Agent forced required delivery tool call",
                    agent_id=self.agent.id,
                    tool=forced_name,
                )
                content = ""
                tool_calls = [forced_tool_call]
            elif self._artifact_tool_succeeded(tool_results):
                if not content.strip() or self._looks_like_artifact_argument_fragment(content):
                    content = self._artifact_completion_message(
                        self.forced_artifact_tool_name,
                        tool_results,
                        task,
                    )

        if tool_calls:
            tool_calls = self._dedupe_artifact_tool_calls(tool_calls)
            tool_calls = self._drop_executed_artifact_tool_calls(
                tool_calls,
                self.executed_artifact_tools,
            )
            if not tool_calls:
                logger.info(
                    "Agent skipped already executed artifact tool calls",
                    agent_id=self.agent.id,
                )
                tool_calls = None
                if not content.strip() or self._looks_like_artifact_argument_fragment(content):
                    content = self._artifact_completion_message(
                        self.forced_artifact_tool_name or self._artifact_tool_name(tool_results),
                        tool_results,
                        task,
                    )

        return content, tool_calls

    def finalize(self, task, work_product, status_report, tool_results):
        if self._artifact_tool_succeeded(tool_results) and (not work_product.strip() or self._looks_like_artifact_argument_fragment(work_product)):
            work_product = self._artifact_completion_message(self._artifact_tool_name(tool_results) or self.forced_artifact_tool_name, tool_results, task)
        elif not work_product.strip() and self._has_failed_tool_results(tool_results):
            work_product = self._tool_failure_message(tool_results)
            if status_report.state != AgentState.FAILED:
                status_report = AgentReport(agent_id=status_report.agent_id, state=AgentState.FAILED, will=AgentWill.BLOCKED, target_task=status_report.target_task, blockers=[work_product], priority=status_report.priority, confidence=0.0, rationale=status_report.rationale or 'Tool execution failed and no user-visible work product was produced.', expected_duration=status_report.expected_duration)
        if status_report.state == AgentState.UNKNOWN and work_product.strip() and (not tool_results):
            status_report = AgentReport(agent_id=status_report.agent_id, state=AgentState.COMPLETED, will=AgentWill.COMPLETE, target_task=status_report.target_task, blockers=status_report.blockers, priority=status_report.priority, confidence=max(status_report.confidence, 0.8), rationale='Direct response produced without tool execution.', expected_duration=status_report.expected_duration)
        if self._artifact_tool_succeeded(tool_results) and status_report.state == AgentState.UNKNOWN and work_product.strip():
            status_report = AgentReport(agent_id=status_report.agent_id, state=AgentState.COMPLETED, will=AgentWill.COMPLETE, target_task=status_report.target_task, blockers=status_report.blockers, priority=status_report.priority, confidence=max(status_report.confidence, 0.9), rationale=status_report.rationale or 'Artifact tool succeeded.', expected_duration=status_report.expected_duration)
        runtime_failure = self._runtime_validation_failure_message(tool_results)
        project_runtime_failure = self._project_runtime_validation_failure(task, tool_results)
        if project_runtime_failure:
            runtime_failure = f'{runtime_failure}；{project_runtime_failure}' if runtime_failure else project_runtime_failure
        if runtime_failure and status_report.state == AgentState.COMPLETED:
            if work_product.strip():
                work_product = f'{work_product.rstrip()}\n\n注意：{runtime_failure}'
            else:
                work_product = runtime_failure
            status_report = AgentReport(agent_id=status_report.agent_id, state=AgentState.FAILED, will=AgentWill.BLOCKED, target_task=status_report.target_task, blockers=[runtime_failure], priority=status_report.priority, confidence=0.0, rationale='Runtime validation tools failed; successful file/artifact creation alone does not prove the project ran.', expected_duration=status_report.expected_duration)
        missing_delivery = self._project_delivery_missing_result(task, tool_results)
        if missing_delivery:
            work_product = missing_delivery
            status_report = AgentReport(agent_id=status_report.agent_id, state=AgentState.FAILED, will=AgentWill.BLOCKED, target_task=status_report.target_task, blockers=[missing_delivery], priority=status_report.priority, confidence=0.0, rationale='Project delivery requires persisted tool results; narration alone is not accepted.', expected_duration=status_report.expected_duration)
        return work_product, status_report

    @staticmethod
    def _artifact_tool_succeeded(tool_results: List[Dict[str, Any]]) -> bool:
        return any(
            str(result.get("tool", "")).startswith("artifact.create_")
            and result.get("success") is True
            for result in tool_results
        )


    @staticmethod
    def _artifact_tool_name(tool_results: List[Dict[str, Any]]) -> str | None:
        for result in reversed(tool_results):
            tool_name = str(result.get("tool") or "")
            if tool_name.startswith("artifact.create_") and result.get("success") is True:
                return tool_name
        return None


    @staticmethod
    def _has_failed_tool_results(tool_results: List[Dict[str, Any]]) -> bool:
        return any(result.get("success") is False for result in tool_results)


    @staticmethod
    def _tool_failure_message(tool_results: List[Dict[str, Any]]) -> str:
        failures = [
            result
            for result in tool_results
            if result.get("success") is False
        ]
        if not failures:
            return "工具执行没有产出可见结果，需要重新执行或人工复核。"
        first = failures[0]
        tool = str(first.get("tool") or first.get("tool_name") or "工具")
        error = str(first.get("error") or "执行失败").strip()
        total = len(failures)
        suffix = f"；本轮共有 {total} 个工具调用失败。" if total > 1 else ""
        return f"{tool} 执行失败，未生成可交付产物：{error}{suffix}"


    @classmethod
    def _summary_instruction(cls, tool_results: List[Dict[str, Any]]) -> str:
        deploy_success = cls._latest_successful_deployment_output(tool_results)
        if deploy_success:
            deployment_url = str(
                deploy_success.get("url") or deploy_success.get("public_url") or ""
            ).strip()
            backend_path = str(
                deploy_success.get("validated_backend_path")
                or deploy_success.get("backend_health_url")
                or ""
            ).strip()
            return (
                "deploy.preview 已经成功创建真实部署。请只依据下面这些工具返回的事实回复用户，"
                "不要手写、改写或重新拼接 deployment_id / artifact_id / URL；必须原样复制部署地址。"
                "不要声明未被工具明确验证的接口、CRUD 流程或健康检查。"
                f"\n部署地址：{deployment_url}"
                f"\n后端验证路径：{backend_path or '以 deploy.preview health checks 为准'}"
            )
        instruction = (
            "工具已经执行完成。请用自然中文给用户一个简洁最终回复，"
            "说明你完成了什么，以及用户可以通过产物卡片预览或下载。"
            "不要再次调用工具，不要输出 JSON 或代码块。"
        )
        runtime_failure = cls._runtime_validation_failure_message(tool_results)
        if runtime_failure:
            instruction += (
                "\n\n注意：本轮存在运行、测试、部署或终端工具失败。最终回复必须如实说明失败项和剩余风险；"
                "不得声称依赖已安装、服务已启动、测试通过、健康检查通过或部署成功，除非对应工具结果明确成功。"
                f"\n失败摘要：{runtime_failure}"
            )
        return instruction


    @staticmethod
    def _runtime_validation_failure_message(tool_results: List[Dict[str, Any]]) -> str | None:
        runtime_tools = {
            "sandbox.run",
            "terminal.start",
            "terminal.send",
            "terminal.wait_for",
            "terminal.stop",
            "api.test",
            "browser.open",
            "deploy.preview",
            "test.run",
        }
        failures: list[str] = []
        deploy_succeeded = WebExecutionExtension._tool_succeeded(tool_results, "deploy.preview")
        successful_runtime_validation = WebExecutionExtension._has_successful_non_install_runtime_validation(tool_results)
        for result in tool_results:
            tool = str(result.get("tool") or result.get("tool_name") or "")
            if tool not in runtime_tools:
                continue
            if deploy_succeeded and tool.startswith("terminal."):
                continue
            command = WebExecutionExtension._runtime_command(result)
            failed = result.get("success") is False
            output = result.get("result")
            if isinstance(output, dict):
                status = str(output.get("status") or "").lower()
                failed = failed or status in {"failed", "error", "timeout"}
                nested = output.get("output")
                if isinstance(nested, dict):
                    nested_status = str(nested.get("status") or "").lower()
                    failed = failed or nested_status in {"failed", "error", "timeout"}
                    if nested.get("assertion_passed") is False:
                        failed = True
            if not failed:
                continue
            if successful_runtime_validation and WebExecutionExtension._is_package_install_command(command):
                continue
            error = str(result.get("error") or "").strip()
            if not error and isinstance(output, dict):
                error = str(output.get("error") or output.get("message") or "").strip()
                nested = output.get("output")
                if not error and isinstance(nested, dict):
                    error = str(
                        nested.get("error")
                        or nested.get("stderr")
                        or nested.get("response_summary")
                        or nested.get("message")
                        or ""
                    ).strip()
            failures.append(f"{tool}: {error or '执行结果未通过'}")
        if not failures:
            return None
        suffix = f"；另有 {len(failures) - 1} 个运行/验证工具失败" if len(failures) > 1 else ""
        return f"{failures[0]}{suffix}"


    @staticmethod
    def _latest_successful_deployment_output(tool_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        for item in reversed(tool_results):
            if str(item.get("tool") or item.get("tool_name") or "") != "deploy.preview":
                continue
            if item.get("success") is not True:
                continue
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            output = result.get("output") if isinstance(result.get("output"), dict) else None
            candidate = output if isinstance(output, dict) else result
            if str(candidate.get("status") or "").lower() in {"failed", "error", "timeout"}:
                continue
            if candidate.get("url") or candidate.get("public_url"):
                return candidate
        return {}


    @staticmethod
    def _has_successful_non_install_runtime_validation(tool_results: List[Dict[str, Any]]) -> bool:
        validation_tools = {"sandbox.run", "terminal.start", "api.test", "browser.open", "deploy.preview", "test.run"}
        for result in tool_results:
            tool = str(result.get("tool") or result.get("tool_name") or "")
            if tool not in validation_tools or WebExecutionExtension._is_package_install_command(WebExecutionExtension._runtime_command(result)):
                continue
            if result.get("success") is False:
                continue
            output = result.get("result")
            if isinstance(output, dict):
                statuses = [str(output.get("status") or "").lower()]
                nested = output.get("output")
                if isinstance(nested, dict):
                    statuses.append(str(nested.get("status") or "").lower())
                    if nested.get("assertion_passed") is False:
                        continue
                if any(status in {"failed", "error", "timeout"} for status in statuses):
                    continue
            return True
        return False


    @staticmethod
    def _runtime_command(result: Dict[str, Any]) -> str:
        args = result.get("arguments") if isinstance(result.get("arguments"), dict) else None
        command = str((args or {}).get("command") or "").strip()
        output = result.get("result")
        if not command and isinstance(output, dict):
            nested = output.get("output")
            if isinstance(nested, dict):
                command = str(nested.get("command") or "").strip()
        return command


    @staticmethod
    def _project_runtime_validation_failure(
        task: str,
        tool_results: List[Dict[str, Any]],
    ) -> str | None:
        if not WebExecutionExtension._looks_like_project_code_delivery(task):
            return None
        for result in tool_results:
            if str(result.get("tool") or result.get("tool_name") or "") != "api.test":
                continue
            output = result.get("result")
            if isinstance(output, dict) and isinstance(output.get("output"), dict):
                output = output["output"]
            if not isinstance(output, dict):
                continue
            if output.get("is_platform_app_probe") is True:
                return "api.test tested AgentHub's own app, not the generated backend service"
        return None


    @staticmethod
    def _is_package_install_command(command: str) -> bool:
        normalized = " ".join(str(command or "").strip().lower().split())
        return bool(
            normalized.startswith("pip install ")
            or normalized.startswith("pip3 install ")
            or normalized.startswith("python -m pip install ")
            or normalized.startswith("py -m pip install ")
        )


    @staticmethod
    def _only_artifact_create_calls(tool_calls: List[Dict[str, Any]]) -> bool:
        if not tool_calls:
            return False
        for tool_call in tool_calls:
            name = str(tool_call.get("function", {}).get("name") or "")
            if not name.startswith("artifact.create_"):
                return False
        return True


    @staticmethod
    def _dedupe_artifact_tool_calls(tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen_artifact_tools: set[str] = set()
        deduped: List[Dict[str, Any]] = []
        for tool_call in tool_calls:
            name = str(tool_call.get("function", {}).get("name") or "")
            if name.startswith("artifact.create_"):
                if name in seen_artifact_tools:
                    continue
                seen_artifact_tools.add(name)
            deduped.append(tool_call)
        return deduped


    @staticmethod
    def _drop_executed_artifact_tool_calls(
        tool_calls: List[Dict[str, Any]],
        executed_artifact_tools: set[str],
    ) -> List[Dict[str, Any]]:
        if not executed_artifact_tools:
            return tool_calls
        return [
            tool_call
            for tool_call in tool_calls
            if str(tool_call.get("function", {}).get("name") or "") not in executed_artifact_tools
        ]


    @staticmethod
    def _looks_like_artifact_argument_fragment(content: str) -> bool:
        normalized = content.strip().lower()
        if normalized in {"0", ">", "<", "li", "ul", "html", "body", "head", "script"}:
            return True
        return bool(len(normalized) <= 3 and re.fullmatch(r"[<>/a-z0-9]+", normalized))


    @staticmethod
    def _artifact_completion_message(
        tool_name: str | None,
        tool_results: List[Dict[str, Any]] | None = None,
        task: str = "",
    ) -> str:
        label = {
            "artifact.create_pdf": "PDF",
            "artifact.create_docx": "Word",
            "artifact.create_pptx": "PPT",
            "artifact.create_xlsx": "Excel",
            "artifact.create_html": "HTML",
            "artifact.create_web_app": "HTML",
        }.get(tool_name or "", "产物")
        output = WebExecutionExtension._latest_artifact_output(tool_results or [], tool_name)
        title = WebExecutionExtension._artifact_display_title(output, task)
        title_part = f"《{title}》" if title else ""
        if label == "HTML":
            return (
                f"我已经把{title_part or '你要的页面'}做成可运行的 HTML 页面了，"
                "可以在下面的产物卡片里直接预览运行效果，也可以下载源文件继续修改。"
            )
        if label == "Excel":
            return (
                f"我已经为你生成好{title_part} Excel 表格了，"
                "可以在下面的产物卡片里预览并下载真实文件。"
            )
        if label == "PPT":
            return (
                f"我已经为你生成好{title_part} PPT 演示文稿了，"
                "可以在下面的产物卡片里预览并下载真实文件。"
            )
        if label in {"PDF", "Word"}:
            return (
                f"我已经为你生成好{title_part} {label} 文档了，"
                "可以在下面的产物卡片里预览排版效果，也可以直接下载。"
            )
        return "我已经完成产物生成，可以在下面的产物卡片里预览和下载。"


    @staticmethod
    def _latest_artifact_output(
        tool_results: List[Dict[str, Any]],
        tool_name: str | None,
    ) -> Dict[str, Any]:
        for item in reversed(tool_results):
            item_tool = str(item.get("tool") or item.get("tool_name") or "")
            if tool_name and item_tool != tool_name:
                continue
            if not item_tool.startswith("artifact.create_"):
                continue
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            output = result.get("output") if isinstance(result.get("output"), dict) else None
            if isinstance(output, dict):
                return output
            if isinstance(result, dict):
                return result
        return {}


    @staticmethod
    def _artifact_display_title(output: Dict[str, Any], task: str = "") -> str:
        artifact = output.get("artifact") if isinstance(output.get("artifact"), dict) else {}
        raw_title = (
            output.get("title")
            or output.get("name")
            or artifact.get("name")
            or artifact.get("title")
            or output.get("filename")
            or ""
        )
        title = str(raw_title).strip()
        if "." in title:
            title = title.rsplit(".", 1)[0]
        if not title and task:
            title = WebExecutionExtension._title_from_task(task)
        return title[:80]


    @staticmethod
    def _title_from_task(task: str) -> str:
        title = re.sub(r"\s+", " ", task or "").strip()
        title = re.sub(r"^(请|帮我|帮忙|麻烦)?(生成|创建|做|制作)(一个|一份|一下)?", "", title)
        title = re.sub(r"(pdf|word|docx|pptx?|excel|xlsx|html|网页|页面|文档)$", "", title, flags=re.IGNORECASE)
        return title.strip(" ：:，,。")[:40]


    def _forced_artifact_tool_call(
        self,
        task: str,
        tools: List[Dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not tools:
            return None
        if self._looks_like_project_code_delivery(task):
            return None
        available = {
            str(tool.get("function", {}).get("name") or "")
            for tool in tools
            if isinstance(tool, dict)
        }
        requested = detect_artifact_tool(task)
        if (
            requested == "artifact.create_html"
            and requested not in available
            and "artifact.create_web_app" in available
        ):
            requested = "artifact.create_web_app"
        if not requested or requested not in available:
            return None
        return {
            "id": f"call_forced_{requested.replace('.', '_')}_{uuid.uuid4().hex[:8]}",
            "type": "function",
            "function": {
                "name": requested,
                "arguments": json.dumps(artifact_arguments(requested, task), ensure_ascii=False),
            },
        }


    def _forced_project_delivery_tool_call(
        self,
        task: str,
        tools: List[Dict[str, Any]],
        tool_results: List[Dict[str, Any]],
        messages: List[ChatMessage] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Enforce real workspace/code delivery for project-building turns.

        The scheduler can ask Backend/Frontend/Deploy agents to build an actual
        project. If the model only narrates completion, force the minimum real
        tool action required for that role instead of accepting a hallucinated
        report.
        """

        if not tools or not self._looks_like_project_code_delivery(task):
            return None
        available = {
            str(tool.get("function", {}).get("name") or "")
            for tool in tools
            if isinstance(tool, dict)
        }
        role = f"{self.agent.name} {self.agent.role}".lower()
        is_release = any(token in role for token in ("deploy", "release", "ops", "部署", "发布", "上线"))
        if is_release and "deploy.preview" in available and not self._tool_succeeded(tool_results, "deploy.preview"):
            artifact_id = self._latest_artifact_id(tool_results, messages or [], task)
            if artifact_id:
                deploy_args: dict[str, Any] = {
                    "artifact_id": artifact_id,
                    "mode": "preview_link",
                }
                # 传递 conversation_id 以支持全栈部署（检测后端工作区文件）
                if metadata:
                    conv_id = str(metadata.get("conversation_id") or metadata.get("session_id") or "")
                    if conv_id:
                        deploy_args["conversation_id"] = conv_id
                return self._tool_call(
                    "deploy.preview",
                    deploy_args,
                    prefix="project_deploy",
                )
        return None


    @staticmethod
    def _latest_artifact_id(
        tool_results: List[Dict[str, Any]],
        messages: List[ChatMessage],
        task: str,
    ) -> str | None:
        for item in reversed(tool_results):
            result = item.get("result")
            if isinstance(result, dict):
                artifact_id = result.get("artifact_id") or (result.get("artifact") or {}).get("id")
                if artifact_id:
                    return str(artifact_id)
        text_parts = [task or ""]
        for message in reversed(messages[-12:]):
            text_parts.append(str(getattr(message, "content", "") or ""))
        haystack = "\n".join(text_parts)
        patterns = (
            r"artifact_id\s*[:=]\s*['\"]?([0-9a-fA-F-]{16,})",
            r'"artifact_id"\s*:\s*"([0-9a-fA-F-]{16,})"',
            r"/artifacts/([0-9a-fA-F-]{16,})/",
        )
        for pattern in patterns:
            matches = re.findall(pattern, haystack)
            if matches:
                return str(matches[-1])
        return None


    @staticmethod
    def _tool_succeeded(tool_results: List[Dict[str, Any]], tool_name: str) -> bool:
        return any(
            str(result.get("tool") or "") == tool_name and result.get("success") is True
            for result in tool_results
        )


    def _project_delivery_missing_result(
        self,
        task: str,
        tool_results: List[Dict[str, Any]],
    ) -> str | None:
        if not self._looks_like_project_code_delivery(task):
            return None
        role = f"{self.agent.name} {self.agent.role}".lower()
        is_backend = any(token in role for token in ("back", "backend", "api", "server", "后端", "服务端", "接口"))
        is_frontend = any(token in role for token in ("front", "frontend", "ui", "web", "前端", "页面"))
        is_release = any(token in role for token in ("deploy", "release", "ops", "部署", "发布", "上线"))
        has_file_write = self._tool_succeeded(tool_results, "file.write")
        has_artifact = self._artifact_tool_succeeded(tool_results)
        has_deploy = self._tool_succeeded(tool_results, "deploy.preview")
        has_run = self._tool_succeeded(tool_results, "sandbox.run")
        has_terminal = any(
            str(result.get("tool") or "").startswith("terminal.") and result.get("success") is True
            for result in tool_results
        )
        if is_backend and not has_file_write:
            return "后端项目交付需要通过 file.write 写入真实后端代码文件；本轮没有检测到文件写入结果，已拒绝口头完成。"
        if is_frontend and not (has_file_write or has_artifact):
            return "前端项目交付需要写入真实前端文件，或生成真实可运行的 HTML/Web 产物；本轮没有检测到真实文件或产物，已拒绝口头完成。"
        if is_release and not (has_deploy or has_run or has_terminal):
            return "部署交付需要真实部署预览、沙箱运行或终端服务验证结果；本轮没有检测到成功的部署/运行记录，已拒绝口头完成。"
        return None


    @staticmethod
    def _tool_call(tool_name: str, arguments: dict[str, Any], *, prefix: str) -> dict[str, Any]:
        return {
            "id": f"call_forced_{prefix}_{uuid.uuid4().hex[:8]}",
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": json.dumps(arguments, ensure_ascii=False),
            },
        }


    @staticmethod
    def _project_slug(task: str) -> str:
        text = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "-", str(task or "").lower()).strip("-")
        if not text:
            return "agenthub-project"
        if any(word in text for word in ("国际象棋", "chess")):
            return "classical-chess"
        if any(word in text for word in ("五子棋", "gomoku")):
            return "gomoku-project"
        return text[:48].strip("-") or "agenthub-project"


    def _filter_tools_for_task(
        self,
        task: str,
        tools: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Narrow visible tools for project-code delivery turns.

        Agents may have broad permissions, but a frontend/backend project task should not
        accidentally become a PDF/Word/PPT artifact just because those tools are also
        authorized. The scheduler still decides who acts; this only removes misleading
        document tools from the current role's tool view.
        """

        if not self._looks_like_project_code_delivery(task):
            return tools
        role = f"{self.agent.name} {self.agent.role}".lower()
        is_frontend = any(token in role for token in ("front", "frontend", "ui", "ux", "前端", "页面", "界面"))
        is_backend = any(token in role for token in ("back", "backend", "api", "server", "后端", "服务端", "接口"))
        is_release = any(token in role for token in ("deploy", "release", "ops", "部署", "发布", "上线"))
        if not (is_frontend or is_backend or is_release):
            return tools

        blocked_document_tools = {
            "artifact.create_pdf",
            "artifact.create_docx",
            "artifact.create_pptx",
            "artifact.create_xlsx",
        }
        filtered: List[Dict[str, Any]] = []
        for tool in tools:
            name = str(tool.get("function", {}).get("name") or "") if isinstance(tool, dict) else ""
            if name in blocked_document_tools:
                continue
            if is_backend and name.startswith("artifact.create_"):
                continue
            if is_backend and name == "api.test":
                continue
            if is_release and name.startswith("terminal."):
                continue
            if is_release and name == "sandbox.run":
                continue
            filtered.append(tool)
        return filtered


    @staticmethod
    def _looks_like_project_code_delivery(task: str) -> bool:
        normalized = "".join(str(task or "").lower().split())
        if not normalized:
            return False
        project_markers = (
            "前后端",
            "前端后端",
            "前端",
            "后端",
            "api",
            "接口",
            "应用",
            "项目",
            "游戏",
            "网页",
            "页面",
            "工作区",
            "文件夹",
            "代码",
            "试一下",
            "体验",
            "部署",
            "project",
            "app",
            "game",
            "frontend",
            "backend",
            "web",
            "deploy",
        )
        doc_only_markers = ("说明文档", "pdf说明", "报告", "文档")
        has_project = any(marker in normalized for marker in project_markers)
        has_code_or_delivery = any(
            marker in normalized
            for marker in (
                "前端",
                "后端",
                "api",
                "接口",
                "代码",
                "工作区",
                "文件夹",
                "试一下",
                "体验",
                "部署",
                "frontend",
                "backend",
                "web",
                "deploy",
            )
        )
        return has_project and (has_code_or_delivery or not any(marker in normalized for marker in doc_only_markers))


    @staticmethod
    def _recover_tool_call_arguments(tool_name: str, task: str) -> dict[str, Any] | None:
        if tool_name not in HTML_ARTIFACT_TOOLS:
            return None
        return artifact_arguments(tool_name, task)


    async def _execute_virtual_agent(
        self,
        task: str,
        tool_executor: Optional[ToolExecutor],
        _emit,
    ) -> Dict[str, Any]:
        """虚拟工具智能体执行：跳过 LLM，直接调用工具。

        从 model_config 读取节点配置，构造 ToolCall 并执行。
        """
        node_config = self.agent.model_config.get("node_config", {})
        node_type = self.agent.model_config.get("node_type", "")

        tool_name = ""
        arguments: Dict[str, Any] = {}

        if node_type == "tool":
            tool_name = node_config.get("tool_name", "") or (self.agent.tools[0] if self.agent.tools else "")
            arguments = node_config.get("arguments", {})
        elif node_type == "mcp":
            tool_name = node_config.get("tool_name", "")
            arguments = node_config.get("arguments", {})
        else:
            # skill / artifact 暂走 LLM 路径（在 _execute_loop 中不应走到这里）
            logger.warning("虚拟智能体类型不支持直接执行", agent_id=self.agent.id, node_type=node_type)
            return {
                "work_product": f"虚拟智能体 {self.agent.name} 暂不支持直接执行",
                "status_report": AgentReport(
                    agent_id=self.agent.id,
                    state=AgentState.COMPLETED,
                    will=AgentWill.COMPLETE,
                    rationale=f"节点类型 {node_type} 不走虚拟执行路径",
                    confidence=1.0,
                ),
                "tool_events": [],
            }

        if not tool_name or not tool_executor:
            return {
                "work_product": "工具执行失败：未配置工具名或执行器",
                "status_report": AgentReport(
                    agent_id=self.agent.id,
                    state=AgentState.FAILED,
                    will=AgentWill.BLOCKED,
                    rationale="缺少工具名或工具执行器",
                    confidence=1.0,
                ),
                "tool_events": [],
            }

        logger.info("虚拟智能体直接执行工具", agent_id=self.agent.id, tool=tool_name)
        await _emit(
            "agent.tool_call",
            {
                "agent_id": self.agent.id,
                "tool_count": 1,
                "tools": [tool_name],
            },
        )

        # 构造 ToolCall 并执行
        tool_call = ToolCall(
            call_id=f"virtual_{self.agent.id}_{uuid.uuid4().hex[:8]}",
            tool_name=tool_name,
            parameters=arguments,
        )

        try:
            bound_executor = tool_executor
            bind_agent = getattr(tool_executor, "bind_agent", None)
            if callable(bind_agent):
                bound_executor = bind_agent(self.agent.id)
            result = await bound_executor.execute(tool_call)
            if not isinstance(result, ToolResult):
                result = ToolResult(
                    call_id=tool_call.call_id,
                    success=True,
                    result=result,
                )
        except Exception as e:
            logger.error("虚拟智能体工具执行失败", agent_id=self.agent.id, tool=tool_name, error=str(e))
            result = ToolResult(
                call_id=tool_call.call_id,
                success=False,
                result=None,
                error=str(e),
            )

        await _emit(
            "agent.tool_result",
            {
                "agent_id": self.agent.id,
                "tool": tool_name,
                "success": result.success,
                "result": result.result,
                "error": result.error,
            },
        )

        tool_events = [
            {
                "agent_id": self.agent.id,
                "round": 1,
                "results": [
                    {
                        "tool": tool_name,
                        "success": result.success,
                        "result": result.result,
                    }
                ],
            }
        ]

        work_product = str(result.result) if result.success else f"工具执行失败: {result.error}"

        return {
            "work_product": work_product,
            "status_report": AgentReport(
                agent_id=self.agent.id,
                state=AgentState.COMPLETED if result.success else AgentState.FAILED,
                will=AgentWill.COMPLETE if result.success else AgentWill.BLOCKED,
                rationale=f"工具 {tool_name} 执行{'成功' if result.success else '失败'}",
                confidence=1.0,
            ),
            "tool_events": tool_events,
        }


    def _build_prompt(
        self,
        task: str,
        blackboard_view: dict,
        task_input: Any | None = None,
    ) -> str:
        """构建用户提示词"""
        # 构建 Blackboard 视图文本
        bb_text = self._format_blackboard(blackboard_view)
        assignment_context = self._format_assignment_context(task_input)

        return f"""你是当前 Agent：{self.agent.name}
角色：{self.agent.role}

请只代表你自己发言，不要冒充其他 Agent。若任务需要协作，只描述你负责的部分和可交付成果。
工具使用规则：
- 用户明确要求生成 PDF、Word、PPT、Excel、HTML、网页、可下载文件、预览卡片或正式产物时，才调用 artifact.create_* 工具。
- 用户只是要求“写一段、介绍、说明、回答、总结文字”，或明确说“直接回复、不需要产物”时，必须直接用聊天文本回答，不要调用 artifact 工具。
- 如果已经调用工具并成功生成产物，仍要给用户一段自然语言回复，说明完成了什么以及如何查看卡片。
- sandbox.run、terminal.start 的 command 不经过 shell，只能是一条可执行命令；不能写 cd、&&、;、|、>、< 或换行。需要进入子目录时使用 workdir 参数，例如 command="python main.py", workdir="backend"。
- 如果运行、测试、部署或终端工具失败，最终回复必须如实说明失败项；不能声称依赖已安装、服务已启动、测试通过或部署成功。

你的当前任务：
{task}

Assignment context (read-only; use upstream_outputs when coordinating with other Agents):
{assignment_context}

全局上下文（只读）：
{bb_text}

请完成以下工作，并在最后输出你的状态报告：

---
工作状态报告（必须包含，格式如下）：
```status_report
{{
  "state": "running|ready|waiting|completed",
  "will": "execute|wait|delegate|complete|blocked",
  "rationale": "简要说明你的当前状态和下一步计划",
  "blockers": [],
  "priority": 1,
  "confidence": 0.95
}}
```
"""


    def _format_assignment_context(self, task_input: Any | None) -> str:
        if not isinstance(task_input, dict) or not task_input:
            return "(none)"
        safe: dict[str, Any] = {}
        for key in ("user_request", "assigned_task", "rationale"):
            if task_input.get(key):
                safe[key] = task_input.get(key)
        plan = task_input.get("plan")
        if isinstance(plan, list):
            safe["plan"] = [
                {
                    "agent_id": item.get("agent_id"),
                    "agent_name": item.get("agent_name"),
                    "role": item.get("role"),
                    "stage": item.get("stage"),
                    "depends_on": item.get("depends_on"),
                    "status": item.get("status"),
                    "task": item.get("task"),
                    "output_preview": item.get("output_preview"),
                }
                for item in plan
                if isinstance(item, dict)
            ]
        upstream = task_input.get("upstream_outputs")
        if isinstance(upstream, dict):
            safe["upstream_outputs"] = {
                agent_id: {
                    "task": value.get("task"),
                    "status": value.get("status"),
                    "output": value.get("output_preview") or value.get("output"),
                    "rationale": value.get("rationale"),
                    "confidence": value.get("confidence"),
                    "blockers": value.get("blockers"),
                }
                for agent_id, value in upstream.items()
                if isinstance(value, dict)
            }
        if not safe:
            return "(none)"
        return json.dumps(safe, ensure_ascii=False, indent=2, default=str)[:12000]


    def _format_blackboard(self, blackboard_view: dict) -> str:
        """格式化 Blackboard 分层视图为文本"""
        parts = []

        # 1. 结构化摘要（替代早期历史）
        summaries = blackboard_view.get("structured_summaries", [])
        if summaries:
            parts.append("历史摘要：")
            for s in summaries[-3:]:
                content = s.get("content", "") if isinstance(s, dict) else str(s)
                parts.append(f"  - {content[:120]}")

        # 2. 近期原始记录（保留细节）
        recent_history = blackboard_view.get("recent_history", [])
        if recent_history:
            parts.append("近期历史：")
            for entry in recent_history[-5:]:
                parts.append(
                    f"  - [{entry.get('type', '?')}] {str(entry.get('content', ''))[:100]}"
                )

        # 3. 结构化键值状态
        kv_state = blackboard_view.get("kv_state", {})
        if kv_state:
            parts.append(f"状态变量：{json.dumps(kv_state, ensure_ascii=False, indent=2)}")

        version = blackboard_view.get("version", 0)
        if version:
            parts.append(f"版本：{version}")

        return "\n".join(parts) if parts else "（无）"


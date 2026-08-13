"""AgentHub-specific scheduling policy for the generic Runtime Kernel."""

from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.core.run_types import EventEnvelope, PolicySnapshot, SchedulingProposal
from agent_runtime.core.types import AgentConfig, AgentState, AgentWill


@dataclass(frozen=True)
class _PlanItem:
    agent_id: str
    stage: int
    dependencies: tuple[str, ...]
    task: str


class AgentHubTeamLeadPolicy:
    """Keeps product delivery heuristics outside ``agent_runtime``.

    The policy only reads a snapshot and proposes work. Runtime state, events,
    persistence, permissions and terminal transitions remain Kernel-owned.
    """

    async def propose(
        self, snapshot: PolicySnapshot, trigger: EventEnvelope | None
    ) -> SchedulingProposal:
        plan = self._build_plan(snapshot)
        reports = {report.agent_id: report for report in snapshot.reports}
        completed = {
            agent_id
            for agent_id, report in reports.items()
            if report.state is AgentState.COMPLETED or report.will is AgentWill.COMPLETE
        }
        blocked = {
            agent_id
            for agent_id, report in reports.items()
            if report.state is AgentState.FAILED or report.will is AgentWill.BLOCKED
        }
        pending = [item for item in plan if item.agent_id not in completed | blocked]
        if not pending:
            return SchedulingProposal(action="complete", rationale="AgentHub delivery plan converged")

        ready = [item for item in pending if set(item.dependencies) <= completed]
        if not ready:
            return SchedulingProposal(action="complete", rationale="all remaining work is blocked")
        stage = min(item.stage for item in ready)
        stage_items = [item for item in ready if item.stage == stage]
        if len(stage_items) > 1:
            return SchedulingProposal(
                action="parallel",
                target_agent_ids=tuple(item.agent_id for item in stage_items),
                task=snapshot.input,
                rationale=f"run independent AgentHub delivery stage {stage}",
                metadata={"tasks": {item.agent_id: item.task for item in stage_items}},
            )
        item = stage_items[0]
        return SchedulingProposal(
            action="assign",
            target_agent_ids=(item.agent_id,),
            task=item.task,
            rationale=f"run AgentHub delivery stage {item.stage}",
        )

    def _build_plan(self, snapshot: PolicySnapshot) -> list[_PlanItem]:
        agents = {agent.id: agent for agent in snapshot.agents}
        mentioned = [
            str(value)
            for value in snapshot.metadata.get("mentioned_agent_ids", [])
            if str(value) in agents
        ]
        if mentioned:
            selected = mentioned
        elif self._is_fullstack_request(snapshot.input):
            selected = self._fullstack_targets(snapshot.input, tuple(agents.values()))
        else:
            matched = [
                agent.id
                for agent in agents.values()
                if self._agent_matches(snapshot.input, self._kind(agent))
            ]
            selected = matched or [next(iter(agents))]
            if not self._is_collaboration_request(snapshot.input):
                selected = selected[:1]

        selected = list(dict.fromkeys(selected))
        kinds = {agent_id: self._kind(agents[agent_id]) for agent_id in selected}
        plan: list[_PlanItem] = []
        for agent_id in selected:
            kind = kinds[agent_id]
            stage = self._stage(kind, self._is_fullstack_request(snapshot.input))
            dependencies = tuple(
                other_id
                for other_id in selected
                if self._stage(kinds[other_id], self._is_fullstack_request(snapshot.input)) < stage
            )
            plan.append(
                _PlanItem(
                    agent_id=agent_id,
                    stage=stage,
                    dependencies=dependencies,
                    task=self._task_for(snapshot.input, kind),
                )
            )
        return sorted(plan, key=lambda item: (item.stage, selected.index(item.agent_id)))

    def _fullstack_targets(
        self, task: str, agents: tuple[AgentConfig, ...]
    ) -> list[str]:
        by_kind: dict[str, list[str]] = {}
        for agent in agents:
            by_kind.setdefault(self._kind(agent), []).append(agent.id)
        selected: list[str] = []
        for kind in ("backend", "frontend"):
            selected.extend(by_kind.get(kind, [])[:1])
        normalized = task.lower()
        if any(word in normalized for word in ("文档", "pdf", "word", "documentation")):
            selected.extend(by_kind.get("documentation", [])[:1])
        if any(word in normalized for word in ("测试", "审查", "验收", "review", "qa")):
            selected.extend(by_kind.get("review", [])[:1])
        if any(word in normalized for word in ("部署", "发布", "上线", "deploy", "release")):
            selected.extend(by_kind.get("release", [])[:1])
        return selected or [agent.id for agent in agents[:1]]

    @staticmethod
    def _kind(agent: AgentConfig) -> str:
        value = f"{agent.name} {agent.role}".lower()
        categories = (
            ("backend", ("backend", "back-end", "server", "api", "后端", "服务端")),
            ("frontend", ("frontend", "front-end", "ui", "ux", "web", "前端", "界面")),
            ("documentation", ("writer", "documentation", "docs", "文档", "写作")),
            ("review", ("review", "test", "qa", "audit", "审查", "测试", "验收")),
            ("release", ("deploy", "release", "ops", "部署", "发布", "上线")),
            ("planning", ("plan", "product", "planner", "规划", "产品")),
        )
        for kind, tokens in categories:
            if any(token in value for token in tokens):
                return kind
        return "general"

    @staticmethod
    def _stage(kind: str, fullstack: bool) -> int:
        if fullstack:
            return {
                "backend": 1,
                "frontend": 2,
                "documentation": 3,
                "review": 4,
                "release": 5,
            }.get(kind, 2)
        return {"planning": 1, "review": 3, "release": 4}.get(kind, 2)

    @staticmethod
    def _is_fullstack_request(task: str) -> bool:
        value = task.lower()
        return any(token in value for token in ("全栈", "full-stack", "fullstack")) or (
            any(token in value for token in ("前端", "frontend", "页面", "ui"))
            and any(token in value for token in ("后端", "backend", "api", "服务端"))
        )

    @staticmethod
    def _is_collaboration_request(task: str) -> bool:
        value = task.lower()
        return any(token in value for token in ("协作", "多智能体", "team", "together", "共同"))

    @staticmethod
    def _agent_matches(task: str, kind: str) -> bool:
        value = task.lower()
        keywords = {
            "backend": ("后端", "api", "服务", "backend"),
            "frontend": ("前端", "页面", "ui", "web", "frontend"),
            "documentation": ("文档", "pdf", "word", "write"),
            "review": ("测试", "审查", "验收", "review", "qa"),
            "release": ("部署", "发布", "上线", "deploy", "release"),
            "planning": ("计划", "规划", "方案", "plan"),
        }
        return any(token in value for token in keywords.get(kind, ()))

    @staticmethod
    def _task_for(task: str, kind: str) -> str:
        guidance = {
            "backend": "先交付数据模型、服务接口、存储逻辑与前端对接契约。",
            "frontend": "基于已完成的后端契约实现真实可运行前端。",
            "documentation": "基于真实代码产物生成说明文档，不得代替实现。",
            "review": "复核上游产物的一致性、可运行性与风险。",
            "release": "在实现与审查完成后执行部署并验证真实可访问性。",
        }.get(kind, "完成与职责匹配的可见交付。")
        return f"用户需求：{task}\n\n{guidance}"

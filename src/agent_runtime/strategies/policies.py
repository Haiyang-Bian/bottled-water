"""SchedulerPolicy implementations shared by every Runtime Kernel run."""

from __future__ import annotations

from typing import Any

from ..core.run_types import EventEnvelope, PolicySnapshot, SchedulingProposal
from ..core.types import AgentConfig, AgentState, AgentWill, SchedulingDecision
from ..workflow.scheduler import WorkflowScheduler as WorkflowTraversal


def proposal_from_decision(decision: SchedulingDecision) -> SchedulingProposal:
    targets = tuple(decision.target_agent_ids)
    if not targets and decision.target_agent_id:
        targets = (decision.target_agent_id,)
    action = decision.decision_type
    if action in {"escalate", "user_input"}:
        action = "wait"
    return SchedulingProposal(
        action=action,
        target_agent_ids=targets,
        task=decision.task_description or decision.task,
        rationale=decision.rationale,
        metadata={
            "requires_verification": decision.requires_verification,
            "verification_agents": list(decision.verification_agents),
        },
    )


class WorkflowPolicy:
    """Adapts graph traversal to SchedulerPolicy without runtime side effects."""

    def __init__(
        self,
        *,
        workflow: dict[str, Any],
        agents: tuple[AgentConfig, ...],
        input: str,
    ) -> None:
        self._traversal = WorkflowTraversal(agents={agent.id: agent for agent in agents})
        self._traversal.set_workflow_context(workflow, input)

    @property
    def agents(self) -> tuple[AgentConfig, ...]:
        return tuple(self._traversal.get_all_agents().values())

    async def propose(
        self, snapshot: PolicySnapshot, trigger: EventEnvelope | None
    ) -> SchedulingProposal:
        decision = await self._traversal.make_decision(
            blackboard=snapshot.context.blackboard,
            agent_reports=list(snapshot.reports),
            conversation_context={
                "current_task": snapshot.input,
                "round": snapshot.decision_count,
                "run_id": snapshot.run_id,
            },
        )
        return proposal_from_decision(decision)


class TeamLeadPolicy:
    """Generic deterministic team policy; product heuristics belong to the host app."""

    async def propose(
        self, snapshot: PolicySnapshot, trigger: EventEnvelope | None
    ) -> SchedulingProposal:
        completed = {
            report.agent_id
            for report in snapshot.reports
            if report.state is AgentState.COMPLETED or report.will is AgentWill.COMPLETE
        }
        blocked = {
            report.agent_id
            for report in snapshot.reports
            if report.state is AgentState.FAILED or report.will is AgentWill.BLOCKED
        }
        available = [
            agent for agent in snapshot.agents if agent.id not in completed and agent.id not in blocked
        ]
        if not available:
            return SchedulingProposal(action="complete", rationale="all selected Agents reported")

        mentioned = {
            str(item)
            for item in snapshot.metadata.get("mentioned_agent_ids", [])
            if str(item).strip()
        }
        if mentioned:
            available = [agent for agent in available if agent.id in mentioned]
            if not available:
                return SchedulingProposal(action="complete", rationale="mentioned Agents reported")

        target = available[0]
        return SchedulingProposal(
            action="assign",
            target_agent_ids=(target.id,),
            task=snapshot.input,
            rationale="assign the next available team member",
        )

"""Single-agent scheduling with explicit success and failure proposals."""

from agent_runtime.core.run_types import SchedulingProposal
from agent_runtime.core.types import AgentState, AgentWill


class SingleAgentPolicy:
    async def propose(self, snapshot, trigger):
        if len(snapshot.agents) != 1:
            raise ValueError("SingleAgentPolicy requires exactly one Agent")
        if not snapshot.reports:
            return SchedulingProposal(
                action="assign", target_agent_ids=(snapshot.agents[0].id,), task=snapshot.input
            )
        report = snapshot.reports[-1]
        if report.state == AgentState.FAILED or report.will == AgentWill.BLOCKED:
            return SchedulingProposal(action="fail", reason_code="agent_failed")
        if report.state == AgentState.COMPLETED or report.will == AgentWill.COMPLETE:
            return SchedulingProposal(action="complete")
        if report.will == AgentWill.WAIT:
            return SchedulingProposal(action="wait")
        return SchedulingProposal(
            action="assign", target_agent_ids=(snapshot.agents[0].id,),
            task=report.target_task or snapshot.input,
        )

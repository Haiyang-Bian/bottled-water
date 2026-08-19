"""Neutral peer-team scheduling policy."""

from __future__ import annotations

from ..core.run_types import EventEnvelope, PolicySnapshot, SchedulingProposal


class CollaborativeTeamPolicy:
    """Schedules peers by unread work and delegates final synthesis without privileges."""

    async def propose(
        self, snapshot: PolicySnapshot, trigger: EventEnvelope | None
    ) -> SchedulingProposal:
        collaboration = snapshot.collaboration
        if collaboration is None:
            raise RuntimeError("CollaborativeTeamPolicy requires collaboration state")

        agent_ids = tuple(agent.id for agent in snapshot.agents)
        if snapshot.decision_count == 0:
            mentioned = tuple(
                agent_id
                for agent_id in snapshot.metadata.get("mentioned_agent_ids", ())
                if agent_id in agent_ids
            )
            targets = mentioned or agent_ids
            return SchedulingProposal(
                action="parallel" if len(targets) > 1 else "assign",
                target_agent_ids=targets,
                task=snapshot.input,
                rationale="start mentioned peers or the whole configured team",
            )

        unread = tuple(
            agent_id
            for agent_id in agent_ids
            if collaboration.unread_by_agent.get(agent_id)
        )
        if unread:
            return SchedulingProposal(
                action="parallel" if len(unread) > 1 else "assign",
                target_agent_ids=unread,
                task="处理团队收件箱中的新消息，并继续推进当前目标。",
                rationale="deliver unread peer messages at the next safe checkpoint",
            )

        summary_agent_id = collaboration.summary_agent_id
        if summary_agent_id and not collaboration.summary_scheduled:
            return SchedulingProposal(
                action="assign",
                target_agent_ids=(summary_agent_id,),
                task=(
                    "汇总本轮团队工作。只依据用户需求、可见产出和团队消息记录给出最终答复；"
                    "明确列出仍未解决的讨论线程。"
                ),
                rationale="route one final synthesis turn to the configured agent",
                metadata={"collaboration_summary": True},
            )

        if summary_agent_id and not collaboration.summary_completed:
            return SchedulingProposal(action="wait", rationale="final synthesis is still running")

        return SchedulingProposal(action="complete", rationale="team collaboration converged")

from __future__ import annotations

import pytest

from agent_runtime import (
    AgentConfig,
    AgentReport,
    AgentState,
    AgentWill,
    RunRequest,
    RunState,
    RuntimeEngine,
    SchedulingProposal,
    SingleAgentPolicy,
    TeamLeadPolicy,
    WorkflowPolicy,
)
from agent_runtime.core.run_types import AgentExecutionResult
from app.services.runtime.policies import AgentHubTeamLeadPolicy


pytestmark = [pytest.mark.unit, pytest.mark.runtime]


class CompletingExecutor:
    def __init__(self) -> None:
        self.executed: list[str] = []

    async def execute(self, request, *, emit, cancellation, lease):
        self.executed.append(request.agent.id)
        return AgentExecutionResult(
            agent_id=request.agent.id,
            report=AgentReport(
                agent_id=request.agent.id,
                state=AgentState.COMPLETED,
                will=AgentWill.COMPLETE,
            ),
            output=f"{request.agent.id} done",
        )


def _agent(agent_id: str) -> AgentConfig:
    return AgentConfig(id=agent_id, name=agent_id, system_prompt="work")


async def _run(policy, agents, executor, run_id):
    engine = RuntimeEngine(agent_executor=executor)
    handle = await engine.start(
        RunRequest(
            run_id=run_id,
            context_scope_id=run_id,
            input="do the work",
            agents=agents,
            policy=policy,
        )
    )
    return await handle.result()


async def test_single_agent_policy_runs_on_runtime_kernel():
    executor = CompletingExecutor()

    result = await _run(SingleAgentPolicy(), (_agent("solo"),), executor, "single")

    assert result.state is RunState.COMPLETED
    assert executor.executed == ["solo"]


async def test_team_lead_policy_runs_all_selected_agents_on_runtime_kernel():
    executor = CompletingExecutor()
    agents = (_agent("planner"), _agent("builder"))

    result = await _run(TeamLeadPolicy(), agents, executor, "team")

    assert result.state is RunState.COMPLETED
    assert executor.executed == ["planner", "builder"]


def _workflow() -> dict:
    return {
        "nodes": [
            {"id": "start", "type": "start", "title": "Start"},
            {
                "id": "build",
                "type": "agent",
                "title": "Build",
                "agent_id": "builder",
                "config": {"agent_id": "builder"},
            },
            {"id": "end", "type": "end", "title": "End"},
        ],
        "edges": [["start", "build"], ["build", "end"]],
    }


async def test_workflow_policy_runs_graph_on_runtime_kernel():
    executor = CompletingExecutor()
    agents = (_agent("builder"),)
    policy = WorkflowPolicy(workflow=_workflow(), agents=agents, input="build")

    result = await _run(policy, policy.agents, executor, "workflow")

    assert result.state is RunState.COMPLETED
    assert executor.executed == ["builder"]


async def test_policy_receives_an_isolated_agent_snapshot():
    class MutatingPolicy:
        async def propose(self, snapshot, trigger):
            snapshot.agents[0].name = "mutated"
            return await SingleAgentPolicy().propose(snapshot, trigger)

    executor = CompletingExecutor()
    original = _agent("solo")

    result = await _run(MutatingPolicy(), (original,), executor, "isolated")

    assert result.state is RunState.COMPLETED
    assert original.name == "solo"


async def test_agenthub_team_lead_keeps_fullstack_product_order_outside_kernel():
    executor = CompletingExecutor()
    agents = (
        AgentConfig(id="frontend", name="Frontend", role="frontend", system_prompt="ui"),
        AgentConfig(id="release", name="Deploy", role="release", system_prompt="deploy"),
        AgentConfig(id="backend", name="Backend", role="backend", system_prompt="api"),
        AgentConfig(id="docs", name="Writer", role="documentation", system_prompt="docs"),
    )
    engine = RuntimeEngine(agent_executor=executor)
    handle = await engine.start(
        RunRequest(
            run_id="agenthub-team",
            context_scope_id="agenthub-team",
            input="构建全栈页面和 API，生成文档并部署",
            agents=agents,
            policy=AgentHubTeamLeadPolicy(),
        )
    )

    result = await handle.result()

    assert result.state is RunState.COMPLETED
    assert executor.executed == ["backend", "frontend", "docs", "release"]


async def test_kernel_rejects_policy_target_outside_allowed_agents():
    class ForbiddenPolicy:
        async def propose(self, snapshot, trigger):
            return SchedulingProposal(action="assign", target_agent_ids=("blocked",))

    executor = CompletingExecutor()
    engine = RuntimeEngine(agent_executor=executor)
    handle = await engine.start(
        RunRequest(
            run_id="permission-check",
            context_scope_id="permission-check",
            input="work",
            agents=(_agent("allowed"), _agent("blocked")),
            policy=ForbiddenPolicy(),
            metadata={"allowed_agent_ids": ["allowed"]},
        )
    )

    result = await handle.result()

    assert result.state is RunState.FAILED
    assert result.reason_code == "policy_error"
    assert executor.executed == []

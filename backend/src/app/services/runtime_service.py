"""
运行时服务 - 统一编排入口

管理 agent_runtime Session 的生命周期，统一 tech_lead 调度策略。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agent_runtime import (
    AgentConfig,
    AgentLoopExecutor,
    AgentContextBuildRequest,
    AgentContextBuildResult,
    Session as AgentSession,
    ToolCall,
    RuntimeEngine,
    SingleAgentPolicy,
    WorkflowPolicy,
)
from agent_runtime.core.interfaces import ToolExecutor
from agent_runtime.core.types import Event as RuntimeEvent
from agent_runtime.workflow.replanner import sanitize_workflow
from model_provider import create_provider
from model_provider.core.interfaces import BaseModelProvider, ChatMessage, ChatResponse, StreamChunk

from db.models import Agent, Artifact, Conversation, Message, User
from db.session import AsyncSessionLocal
from app.persistence.sqlalchemy_backend import SQLAlchemyBackend
from app.persistence.runtime_store import SQLContextStore, SQLRunStore
from app.events import SseSink, WebSocketSink
from app.services.context.builder import ContextBuilder
from app.services.agents.capability_permissions import (
    agent_uses_default_full_permissions,
    configured_tool_names,
)
from app.services.chat.scheduling import resolve_scheduling_strategy
from app.services.runtime.policies import AgentHubTeamLeadPolicy
from app.services.model_config_resolver import normalize_provider_type
from app.services.serialization import artifact_to_dict
from common.logger import get_logger

logger = get_logger("app.services.runtime_service")


@dataclass(frozen=True)
class RuntimeBinding:
    engine: RuntimeEngine
    agents: tuple[AgentConfig, ...]
    policy_factory: Callable[[], Any]
    scheduling_strategy: str

    def create_policy(self):
        return self.policy_factory()


class _MockModelProvider(BaseModelProvider):
    """Mock 模型提供者，用于单 Agent 纯代码模式（不实际调用 LLM）"""

    def __init__(self):
        super().__init__({"model": "mock"})

    async def chat(
        self,
        messages: List[ChatMessage],
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> ChatResponse:
        last_message = messages[-1] if messages else None
        content = last_message.content if last_message else "Mock response"
        return ChatResponse(
            content=content,
            finish_reason="stop",
        )

    async def chat_stream(
        self,
        messages: List[ChatMessage],
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[StreamChunk]:
        last_message = messages[-1] if messages else None
        content = last_message.content if last_message else "Mock response"
        yield StreamChunk(
            content=content,
            finish_reason="stop",
        )


class _ContextBuilderProvider:
    """App-layer bridge that feeds ContextBuilder output into agent_runtime."""

    def __init__(self, session_factory=AsyncSessionLocal):
        self._session_factory = session_factory

    async def build_agent_context(
        self,
        request: AgentContextBuildRequest,
    ) -> AgentContextBuildResult | None:
        metadata = request.metadata or {}
        conversation_id = str(metadata.get("conversation_id") or request.session_id or "")
        user_message_id = str(metadata.get("user_message_id") or "")
        if not conversation_id or not user_message_id:
            return None

        async with self._session_factory() as db:
            return await db.run_sync(
                lambda sync_db: self._build_sync(
                    sync_db,
                    request=request,
                    conversation_id=conversation_id,
                    user_message_id=user_message_id,
                )
            )

    @staticmethod
    def _build_sync(
        sync_db,
        *,
        request: AgentContextBuildRequest,
        conversation_id: str,
        user_message_id: str,
    ) -> AgentContextBuildResult | None:
        conversation = sync_db.get(Conversation, conversation_id)
        user_message = sync_db.get(Message, user_message_id)
        agent = sync_db.get(Agent, request.agent.id)
        if not conversation or not user_message or not agent:
            return None

        bundle = ContextBuilder(sync_db).build_agent_messages(
            conversation=conversation,
            user_message=user_message,
            agent=agent,
            system_prompt=request.base_system_prompt,
            prompt=request.base_user_prompt,
            mode="agent_runtime",
        )
        return AgentContextBuildResult(
            messages=bundle.messages,
            diagnostics={
                **bundle.diagnostics,
                "sections": bundle.sections,
                "source": "ContextBuilder",
            },
        )


class OrchestratorService:
    """
    统一编排服务。

    根据 Agent 数量自动选择调度器：
    - 单 Agent：SingleAgentScheduler（纯代码，无 LLM 开销）
    - 多 Agent：TechLeadScheduler（LLM 驱动的协调调度）
    """

    @staticmethod
    async def _get_conversation_agents(db: AsyncSession, conversation: Conversation) -> list[Agent]:
        """获取会话中的 Agent"""
        from db.models import ConversationParticipant

        participant_agent_ids = [
            item.agent_id
            for item in (
                await db.scalars(
                    select(ConversationParticipant).where(
                        ConversationParticipant.conversation_id == conversation.id,
                        ConversationParticipant.participant_type == "agent",
                        ConversationParticipant.left_at.is_(None),
                        ConversationParticipant.agent_id.is_not(None),
                    )
                )
            ).all()
            if item.agent_id
        ]
        base_query = select(Agent).where(Agent.deleted_at.is_(None), Agent.status.in_(["online", "degraded"]))
        if participant_agent_ids:
            agents = (await db.scalars(base_query.where(Agent.id.in_(participant_agent_ids)))).all()
            order = {agent_id: index for index, agent_id in enumerate(participant_agent_ids)}
            return sorted(agents, key=lambda a: order.get(a.id, 999))
        return (await db.scalars(base_query.where(Agent.type != "custom"))).all()

    @staticmethod
    async def create_provider_from_config(db: AsyncSession, model_config_id: str) -> Any:
        """根据 ModelConfig 创建模型提供者"""
        from db.models import ModelConfig as DBModelConfig
        from app.services.model_config_resolver import create_provider_from_env_fallback, resolve_api_key
        from model_provider.core.config import ModelConfig as MPModelConfig

        config = await db.scalar(
            select(DBModelConfig)
            .options(selectinload(DBModelConfig.provider))
            .where(DBModelConfig.id == model_config_id, DBModelConfig.deleted_at.is_(None))
        )
        if not config:
            logger.warning(f"ModelConfig not found: {model_config_id}")
            return create_provider_from_env_fallback()

        provider = config.provider
        if not provider or provider.status != "active":
            logger.warning(f"Provider not active for config: {model_config_id}")
            return create_provider_from_env_fallback()

        api_key = await resolve_api_key(provider, config)

        if not api_key:
            logger.warning(f"No API key for provider: {provider.name}")
            return create_provider_from_env_fallback()

        return create_provider(MPModelConfig(
            provider=normalize_provider_type(provider.provider_type),
            model=config.model_id,
            api_key=api_key,
            base_url=provider.base_url or None,
        ))

    @staticmethod
    async def create_session(
        db: AsyncSession,
        conversation: Conversation,
        agents: list[Agent],
        model_config_id: str | None = None,
        event_sink=None,
        scheduling_strategy: str | None = None,
    ) -> AgentSession:
        """创建 AgentSession（不运行）。

        调度器由 AgentSession 内部根据配置创建，从属于会话生命周期。
        支持三种调度策略：
        - single_agent：单 Agent 纯代码调度
        - tech_lead：多 Agent LLM 协调调度
        - workflow：Workflow 图遍历调度
        """
        user = await db.get(User, conversation.creator_id)
        agent_count = len(agents)

        primary_agent = agents[0] if agents else None
        agent_model_config_id = (
            (primary_agent.config or {}).get("model_config_id")
            if primary_agent is not None
            else None
        )
        selected_model_config_id = model_config_id or agent_model_config_id

        # 构建统一的模型提供者。优先级：聊天框选择 > Agent 绑定模型 > 用户默认/环境默认。
        if selected_model_config_id:
            model_provider = await OrchestratorService.create_provider_from_config(
                db,
                str(selected_model_config_id),
            )
        else:
            from app.services.model_config_resolver import create_provider_from_db
            default_id = (user.extra or {}).get("default_model_config_id") if user else None
            model_provider = await create_provider_from_db(db, default_id)

        # 统一构建 AgentConfig
        agent_configs = [
            AgentConfig(
                id=agent.id,
                name=agent.name,
                system_prompt=(agent.config or {}).get("system_prompt", "") or agent.description or f"你是 {agent.name}。",
                role=agent.type or "worker",
                model_config={**(agent.config or {}), "avatar_url": agent.avatar_url},
                tools=_runtime_agent_tools(agent),
            )
            for agent in agents
        ]

        # 判断调度策略
        resolved_strategy = resolve_scheduling_strategy(conversation, scheduling_strategy)

        has_workflow = (
            conversation.extra
            and isinstance(conversation.extra, dict)
            and isinstance(conversation.extra.get("workflow"), dict)
        )
        runtime_mode = ""
        if conversation.extra and isinstance(conversation.extra, dict):
            runtime_mode = str(
                conversation.extra.get("runtime")
                or conversation.extra.get("runtime_mode")
                or ""
            ).strip()
        if not runtime_mode and conversation.chat_type == "group" and resolved_strategy == "tech_lead":
            runtime_mode = "actor"

        tool_executor = _ToolExecutorAdapter(
            db, primary_agent, user, conversation, {agent.id: agent for agent in agents}
        ) if primary_agent else None
        context_provider = _ContextBuilderProvider()

        # ---- Workflow 模式 ----
        # Strategy resolution is centralized in services.chat.scheduling.
        if resolved_strategy == "workflow":
            from agent_runtime.workflow.replanner import _fallback_workflow

            if has_workflow:
                raw_workflow = conversation.extra["workflow"]
            else:
                # 无 workflow 定义时构建默认 workflow
                raw_workflow = _fallback_workflow(
                    conversation.id,
                    [
                        {
                            "id": agent.id,
                            "name": agent.name,
                            "type": agent.type,
                            "description": agent.description,
                            "config": agent.config,
                        }
                        for agent in agents
                    ],
                )

            # 清理 workflow
            workflow = sanitize_workflow(
                raw_workflow,
                conversation_id=str(conversation.id),
                available_agents=[
                    {"id": agent.id, "name": agent.name, "type": agent.type}
                    for agent in agents
                ],
            )

            return AgentSession.create(
                agents=agent_configs,
                scheduler_config={
                    "strategy": "workflow",
                    "workflow": workflow,
                    "prompt": "",
                },
                model_provider=model_provider,
                persistence=SQLAlchemyBackend(db),
                event_sink=event_sink,
                tool_executor=tool_executor,
                context_provider=context_provider,
                session_id=str(conversation.id),
            )

        # ---- 单 Agent 模式 ----
        if agent_count == 1:
            return AgentSession.create(
                agents=[agent_configs[0]],
                scheduler_config={"strategy": "single_agent"},
                model_provider=model_provider,
                persistence=SQLAlchemyBackend(db),
                event_sink=event_sink,
                tool_executor=tool_executor,
                context_provider=context_provider,
                session_id=str(conversation.id),
            )

        # ---- 多 Agent TechLead 模式 ----
        return AgentSession.create(
            agents=agent_configs,
            scheduler_config={
                "strategy": "tech_lead",
                **({"runtime": "actor"} if runtime_mode == "actor" else {}),
            },
            model_provider=model_provider,
            persistence=SQLAlchemyBackend(db),
            event_sink=event_sink,
            tool_executor=tool_executor,
            context_provider=context_provider,
            session_id=str(conversation.id),
        )

    @staticmethod
    async def create_engine(
        db: AsyncSession,
        conversation: Conversation,
        agents: list[Agent],
        model_config_id: str | None = None,
        scheduling_strategy: str | None = None,
        session_factory=AsyncSessionLocal,
    ) -> RuntimeBinding:
        """Create reusable adapters plus a fresh-policy factory for one conversation."""

        user = await db.get(User, conversation.creator_id)
        primary_agent = agents[0] if agents else None
        agent_model_config_id = (
            (primary_agent.config or {}).get("model_config_id") if primary_agent else None
        )
        selected_model_config_id = model_config_id or agent_model_config_id
        if selected_model_config_id:
            provider = await OrchestratorService.create_provider_from_config(
                db, str(selected_model_config_id)
            )
        else:
            from app.services.model_config_resolver import create_provider_from_db

            default_id = (user.extra or {}).get("default_model_config_id") if user else None
            provider = await create_provider_from_db(db, default_id)

        agent_configs = tuple(
            AgentConfig(
                id=agent.id,
                name=agent.name,
                system_prompt=(agent.config or {}).get("system_prompt", "")
                or agent.description
                or f"你是 {agent.name}。",
                role=agent.type or "worker",
                model_config={**(agent.config or {}), "avatar_url": agent.avatar_url},
                tools=_runtime_agent_tools(agent),
            )
            for agent in agents
        )
        strategy = resolve_scheduling_strategy(conversation, scheduling_strategy)
        if strategy == "workflow":
            from agent_runtime.workflow.replanner import _fallback_workflow

            raw_workflow = (conversation.extra or {}).get("workflow") or _fallback_workflow(
                str(conversation.id),
                [
                    {
                        "id": agent.id,
                        "name": agent.name,
                        "type": agent.type,
                        "description": agent.description,
                        "config": agent.config,
                    }
                    for agent in agents
                ],
            )
            workflow = sanitize_workflow(
                raw_workflow,
                conversation_id=str(conversation.id),
                available_agents=[
                    {"id": agent.id, "name": agent.name, "type": agent.type}
                    for agent in agents
                ],
            )

            def policy_factory():
                return WorkflowPolicy(workflow=workflow, agents=agent_configs, input="")

            policy_agents = policy_factory().agents
        elif len(agent_configs) == 1:
            policy_factory = SingleAgentPolicy
            policy_agents = agent_configs
            strategy = "single_agent"
        else:
            policy_factory = AgentHubTeamLeadPolicy
            policy_agents = agent_configs

        tool_executor = (
            _ToolExecutorAdapter(
                db,
                primary_agent,
                user,
                conversation,
                {agent.id: agent for agent in agents},
            )
            if primary_agent and user
            else None
        )
        engine = RuntimeEngine(
            agent_executor=AgentLoopExecutor(
                model_provider=provider,
                tool_executor=tool_executor,
                context_provider=_ContextBuilderProvider(session_factory),
                use_streaming=True,
            ),
            context_store=SQLContextStore(session_factory),
            run_store=SQLRunStore(session_factory),
        )
        return RuntimeBinding(
            engine=engine,
            agents=policy_agents,
            policy_factory=policy_factory,
            scheduling_strategy=strategy,
        )

    @staticmethod
    async def run(db: AsyncSession, conversation: Conversation, message: Message, strategy: str, model_config_id: str | None = None) -> None:
        """运行编排（SSE 兼容路径，deprecated）。

        Args:
            model_config_id: 用户选择的模型配置ID，优先于 agent 配置
        """
        agents = await OrchestratorService._get_conversation_agents(db, conversation)
        if not agents:
            logger.warning(f"会话没有可用 Agent conversation_id={conversation.id}")
            return

        session = await OrchestratorService.create_session(
            db,
            conversation,
            agents,
            model_config_id,
            event_sink=SseSink(conversation_id=str(conversation.id)),
            scheduling_strategy=strategy,
        )
        prompt = (
            message.content.get("text", "")
            if isinstance(message.content, dict)
            else str(message.content)
        )
        resolved_strategy = resolve_scheduling_strategy(conversation, strategy)
        logger.info(
            "OrchestratorService run",
            conversation_id=str(conversation.id),
            strategy=resolved_strategy,
            agent_count=len(agents),
        )
        async for event in session.run(prompt):
            logger.debug("OrchestratorService event", type=event.type)


def _runtime_agent_tools(agent: Agent) -> list[str]:
    if agent_uses_default_full_permissions(agent):
        return []
    return list(dict.fromkeys(configured_tool_names(agent)))


class _ToolExecutorAdapter(ToolExecutor):
    """ToolExecutor 适配器，将 agent_runtime 的工具执行请求路由到 app 层工具注册表"""

    def __init__(
        self,
        db: AsyncSession,
        agent: Agent,
        user: User,
        conversation: Conversation,
        agents_by_id: dict[str, Agent] | None = None,
    ):
        self.agent_id = str(agent.id)
        self.user_id = str(user.id)
        self.conversation_id = str(conversation.id)
        self.agent_ids = set(agents_by_id or {agent.id: agent})
        self._test_db_override = None if isinstance(db, AsyncSession) else db
        self._test_agent = agent
        self._test_user = user
        self._test_conversation = conversation

    def bind_agent(self, agent_id: str) -> "_ToolExecutorAdapter":
        clone = object.__new__(_ToolExecutorAdapter)
        clone.agent_id = agent_id if agent_id in self.agent_ids else self.agent_id
        clone.user_id = self.user_id
        clone.conversation_id = self.conversation_id
        clone.agent_ids = self.agent_ids
        clone._test_db_override = self._test_db_override
        clone._test_agent = self._test_agent
        clone._test_user = self._test_user
        clone._test_conversation = self._test_conversation
        return clone

    async def list_tools(self) -> list[dict]:
        from app.services.agents.async_tool_loop import build_tools_for_agent
        if self._test_db_override is not None:
            return await build_tools_for_agent(self._test_db_override, self._test_agent)
        async with AsyncSessionLocal() as db:
            agent = await db.get(Agent, self.agent_id)
            if not agent:
                return []
            return await build_tools_for_agent(db, agent)

    async def execute(self, tool_call: ToolCall) -> Any:
        from app.services.agents.async_tool_loop import execute_tool_by_name
        if self._test_db_override is not None:
            result = await execute_tool_by_name(
                self._test_db_override,
                agent=self._test_agent,
                user=self._test_user,
                conversation=self._test_conversation,
                tool_name=tool_call.tool_name,
                arguments=tool_call.parameters,
            )
            await self._publish_artifact_messages(self._test_db_override, result)
            return result
        async with AsyncSessionLocal() as db:
            agent = await db.get(Agent, self.agent_id)
            user = await db.get(User, self.user_id)
            conversation = await db.get(Conversation, self.conversation_id)
            if not agent or not user or not conversation:
                raise ValueError("Runtime tool context is no longer available")
            result = await execute_tool_by_name(
                db,
                agent=agent,
                user=user,
                conversation=conversation,
                tool_name=tool_call.tool_name,
                arguments=tool_call.parameters,
            )
            await self._publish_artifact_messages(db, result)
            return result

    async def _publish_artifact_messages(self, db: AsyncSession, result: Any) -> None:
        if not isinstance(result, dict):
            return
        output = result.get("output") if isinstance(result.get("output"), dict) else {}
        artifact_id = str(output.get("artifact_id") or "")
        if not artifact_id:
            return

        sink = WebSocketSink(self.conversation_id)
        artifact = await db.get(Artifact, artifact_id)
        if artifact:
            await sink.emit(RuntimeEvent(type="artifact:created", payload=artifact_to_dict(artifact)))

        # The preview card is persisted by the artifact tool itself. Do not
        # emit it here: agent_runtime will publish persisted preview cards
        # after the agent report message so the chat order stays natural
        # (assistant reply first, artifact card second).


class SingleAgentOrchestrator:
    """单 Agent 编排器，使用 SingleAgentScheduler（纯代码，无 LLM 开销）。

    保留用于 SSE 兼容路径，新代码应使用 ConversationSessionManager。
    """

    def __init__(self, db: AsyncSession, conversation: Conversation, message: Message, channel: str, agents: list[Agent], model_config_id: str | None = None):
        self.db = db
        self.conversation = conversation
        self.message = message
        self.channel = channel
        self.agents = agents
        self.model_config_id = model_config_id

    async def run(self) -> None:
        session = await OrchestratorService.create_session(
            self.db,
            self.conversation,
            self.agents,
            self.model_config_id,
            event_sink=SseSink(conversation_id=str(self.conversation.id)),
        )

        logger.info(f"SingleAgentOrchestrator 启动 session_id={session.session_id} agent_id={self.agents[0].id}")

        prompt = (
            self.message.content.get("text", "")
            if isinstance(self.message.content, dict)
            else str(self.message.content)
        )

        async for event in session.run(prompt):
            logger.debug(f"SingleAgentOrchestrator 事件 type={event.type}")
            # 运行时事件通过 EventDispatcher -> SseSink 自动推送，
            # 不再手动 event_bus.publish


class TechLeadOrchestrator:
    """多 Agent 编排器，使用 TechLeadScheduler（LLM 驱动协调）。

    保留用于 SSE 兼容路径，新代码应使用 ConversationSessionManager。
    """

    def __init__(self, db: AsyncSession, conversation: Conversation, message: Message, channel: str, agents: list[Agent], model_config_id: str | None = None):
        self.db = db
        self.conversation = conversation
        self.message = message
        self.channel = channel
        self.agents = agents
        self.model_config_id = model_config_id

    async def run(self) -> None:
        session = await OrchestratorService.create_session(
            self.db,
            self.conversation,
            self.agents,
            self.model_config_id,
            event_sink=SseSink(conversation_id=str(self.conversation.id)),
        )

        logger.info(f"TechLeadOrchestrator 启动 session_id={session.session_id} agent_count={len(self.agents)}")

        prompt = (
            self.message.content.get("text", "")
            if isinstance(self.message.content, dict)
            else str(self.message.content)
        )

        async for event in session.run(prompt):
            logger.debug(f"TechLeadOrchestrator 事件 type={event.type}")
            # 运行时事件通过 EventDispatcher -> SseSink 自动推送，
            # 不再手动 event_bus.publish

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.services.seed import (
    DEFAULT_AGENTS,
    DEFAULT_PERMISSIONS,
    DEFAULT_ROLE_MAP,
    DEFAULT_SKILLS,
)
from app.services.tools.catalog import sync_builtin_tool_definitions
from app.services.tools.permissions import normalize_tool_names
from db.models import (
    Agent,
    ModelConfig,
    ModelProvider,
    Permission,
    Role,
    RolePermission,
    Skill,
    User,
    UserRole,
    utcnow,
)


ROLE_NAMES = {
    "ROLE_USER": "Member",
    "ROLE_AGENT_PROVIDER": "Agent Provider",
    "ROLE_DEVELOPER": "Developer",
    "ROLE_ADMIN": "Administrator",
}


async def _seed_agents(db: AsyncSession) -> None:
    names = [spec["name"] for spec in DEFAULT_AGENTS]
    existing = {
        (agent.name, agent.type): agent
        for agent in (await db.scalars(select(Agent).where(Agent.name.in_(names)))).all()
    }
    for spec in DEFAULT_AGENTS:
        key = (spec["name"], spec["type"])
        agent = existing.get(key)
        if not agent:
            agent = Agent(name=spec["name"], type=spec["type"])
            db.add(agent)
        current_config = dict(agent.config or {})
        agent.owner_id = None
        agent.status = "online"
        agent.description = spec["description"]
        agent.capabilities = spec["capabilities"]
        agent.last_heartbeat_at = utcnow()
        agent.config = {
            "supports_streaming": True,
            "supports_tool_use": True,
            "supports_file_upload": True,
            "agentic_loop": {
                "enabled": True,
                "max_steps": 2,
                "tool_policy": "short_safe_loop",
            },
            "system_prompt": spec["system_prompt"],
            **current_config,
            "tools": normalize_tool_names(
                [*spec["tools"], *(current_config.get("tools") or [])]
            ),
        }
        agent.extra = {
            **(agent.extra or {}),
            "display_name": spec["name"],
            "provider": spec["provider"],
            "avatar_color": spec["avatar_color"],
        }


async def _seed_skills(db: AsyncSession) -> None:
    existing = {
        item.name: item
        for item in (await db.scalars(select(Skill).where(Skill.source == "system"))).all()
    }
    for spec in DEFAULT_SKILLS:
        if spec["name"] in existing:
            continue
        db.add(
            Skill(
                owner_id=None,
                workspace_id=None,
                name=spec["name"],
                description=spec["description"],
                category=spec["category"],
                source="system",
                status="active",
                version="1.0.0",
                content=spec["content"],
                prompt=spec["prompt"],
                tags=spec["tags"],
                tools=spec["tools"],
                config={"builtin": True, "auto_select": True},
            )
        )


async def _seed_access_control(db: AsyncSession) -> None:
    permissions = {item.code: item for item in (await db.scalars(select(Permission))).all()}
    for code in DEFAULT_PERMISSIONS:
        if code not in permissions:
            resource, action = code.split(":", 1)
            permission = Permission(
                code=code,
                resource=resource,
                action=action,
                description=code,
            )
            db.add(permission)
            permissions[code] = permission

    roles = {item.code: item for item in (await db.scalars(select(Role))).all()}
    for code in DEFAULT_ROLE_MAP:
        role = roles.get(code)
        if not role:
            role = Role(code=code, name=ROLE_NAMES[code], is_system=True)
            db.add(role)
            roles[code] = role
        role.name = ROLE_NAMES[code]
        role.is_system = True
        role.deleted_at = None
    await db.flush()

    for role_code, permission_codes in DEFAULT_ROLE_MAP.items():
        role = roles[role_code]
        await db.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
        for permission_code in permission_codes:
            db.add(
                RolePermission(
                    role_id=role.id,
                    permission_id=permissions[permission_code].id,
                )
            )


async def _backfill_user_roles(db: AsyncSession) -> None:
    roles = {role.code: role for role in (await db.scalars(select(Role))).all()}
    assigned_user_ids = set((await db.scalars(select(UserRole.user_id).distinct())).all())
    users = (
        await db.scalars(
            select(User).where(User.deleted_at.is_(None), User.status == "active")
        )
    ).all()
    for user in users:
        if user.id in assigned_user_ids:
            continue
        primary_code = {
            "member": "ROLE_USER",
            "agent_provider": "ROLE_AGENT_PROVIDER",
            "developer": "ROLE_DEVELOPER",
            "admin": "ROLE_ADMIN",
        }.get(user.role, "ROLE_USER")
        role_codes = (
            ["ROLE_USER"] if primary_code == "ROLE_USER" else ["ROLE_USER", primary_code]
        )
        for code in role_codes:
            role = roles.get(code)
            if role:
                db.add(UserRole(user_id=user.id, role_id=role.id, assigned_by=None))


async def _seed_environment_provider(db: AsyncSession) -> None:
    settings = get_settings()
    if not settings.ark_api_key and not settings.use_mock_llm:
        return
    provider = await db.scalar(
        select(ModelProvider).where(
            ModelProvider.owner_id.is_(None),
            ModelProvider.name == "火山方舟 OpenAI 兼容",
            ModelProvider.deleted_at.is_(None),
        )
    )
    if not provider:
        provider = ModelProvider(
            owner_id=None,
            name="火山方舟 OpenAI 兼容",
            provider_type="openai_compatible",
            base_url=settings.ark_base_url,
            api_key_ref="mock" if settings.use_mock_llm else "env:ARK_API_KEY",
            default_model=(
                settings.ark_endpoint_id
                or settings.ark_model
                or "doubao-seed-2-0-lite"
            ),
        )
        db.add(provider)
        await db.flush()
    provider.base_url = settings.ark_base_url
    provider.api_key_ref = "mock" if settings.use_mock_llm else "env:ARK_API_KEY"
    provider.default_model = (
        settings.ark_endpoint_id or settings.ark_model or "doubao-seed-2-0-lite"
    )
    provider.supports_streaming = True
    provider.supports_embeddings = False
    provider.status = "active"
    provider.config = {"source": "env", "api_key_env": "ARK_API_KEY"}
    config = await db.scalar(
        select(ModelConfig).where(
            ModelConfig.provider_id == provider.id,
            ModelConfig.purpose == "chat",
            ModelConfig.deleted_at.is_(None),
        )
    )
    if not config:
        db.add(
            ModelConfig(
                provider_id=provider.id,
                name="默认豆包对话模型",
                model_id=provider.default_model,
                purpose="chat",
                context_window=128000,
                max_output_tokens=8192,
            )
        )
    else:
        config.model_id = provider.default_model


async def _migrate_model_credentials(db: AsyncSession) -> None:
    providers = {
        provider.id: provider for provider in (await db.scalars(select(ModelProvider))).all()
    }
    configs = (await db.scalars(select(ModelConfig))).all()
    for config in configs:
        raw = dict(config.config or {})
        api_key = raw.pop("api_key", None) or raw.pop("apikey", None)
        provider = providers.get(config.provider_id)
        if api_key and provider and not provider.api_key_ref:
            provider.api_key_ref = str(api_key)
        if raw != (config.config or {}):
            config.config = raw


async def ensure_system_data(db: AsyncSession) -> None:
    await db.run_sync(sync_builtin_tool_definitions)
    await _seed_agents(db)
    await _seed_skills(db)
    await _seed_access_control(db)
    await _backfill_user_roles(db)
    await _seed_environment_provider(db)
    await _migrate_model_credentials(db)
    await db.commit()

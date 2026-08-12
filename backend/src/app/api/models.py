from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ForbiddenError, NotFoundError, ValidationAppError
from app.core.response import ok
from app.deps import get_current_user
from db import get_db
from db.models import ModelConfig, ModelProvider, User, utcnow
from app.schemas.common import ApiResponse, ModelConfigOut, ModelProviderOut
from app.schemas.requests import (
    CreateModelConfigRequest,
    CreateModelProviderRequest,
    TestModelRequest,
    UpdateModelProviderCredentialRequest,
    UpdateModelConfigRequest,
)
from app.services.llm.gateway import test_model_config
from app.services.model_config_resolver import normalize_provider_type
from app.services.serialization import model_config_to_dict, model_provider_to_dict, strip_sensitive
from model_provider import get_builtin_providers


router = APIRouter(tags=["model-management"])


async def ensure_model_tables(db: AsyncSession) -> None:
    for table in (ModelProvider.__table__, ModelConfig.__table__):
        await db.run_sync(lambda sess: table.create(bind=sess.get_bind(), checkfirst=True))


async def _get_provider(db: AsyncSession, user: User, provider_id: str) -> ModelProvider:
    await ensure_model_tables(db)
    provider = await db.scalar(
        select(ModelProvider)
        .options(selectinload(ModelProvider.models))
        .where(ModelProvider.id == provider_id, ModelProvider.deleted_at.is_(None))
    )
    if not provider:
        raise NotFoundError("模型供应商不存在")
    if provider.owner_id not in {None, user.id} and user.role != "admin":
        raise ForbiddenError("无权访问该模型供应商")
    return provider


async def _resolve_provider_by_type(
    db: AsyncSession, user: User, provider_type: str
) -> ModelProvider:
    """根据 provider_type 查找或自动创建内置 ModelProvider 记录。"""
    await ensure_model_tables(db)
    from model_provider import get_builtin_providers

    # 内置元数据只是模板；每个用户持有自己的 provider 与凭据。
    provider = await db.scalar(
        select(ModelProvider)
        .where(
            ModelProvider.provider_type == provider_type,
            ModelProvider.owner_id == user.id,
            ModelProvider.deleted_at.is_(None),
        )
        .order_by(ModelProvider.created_at.asc())
    )
    if provider:
        return provider

    # 根据内置元数据自动创建
    builtin = {b["provider_type"]: b for b in get_builtin_providers()}
    meta = builtin.get(provider_type)
    if not meta:
        raise ValidationAppError(f"不支持的 provider 类型: {provider_type}")

    provider = ModelProvider(
        owner_id=user.id,
        name=meta["name"],
        provider_type=provider_type,
        base_url=meta.get("base_url", "").rstrip("/"),
        api_key_ref=None,
        default_model=meta.get("default_model", ""),
        supports_streaming=meta.get("supports_streaming", True),
        supports_embeddings=meta.get("supports_embeddings", False),
        status="active",
    )
    db.add(provider)
    await db.flush()
    return provider


@router.get("/model-providers", response_model=ApiResponse[dict])
async def list_model_providers(
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    await ensure_model_tables(db)
    providers = (
        await db.scalars(
            select(ModelProvider)
            .options(selectinload(ModelProvider.models))
            .where(ModelProvider.deleted_at.is_(None))
            .where((ModelProvider.owner_id == user.id) | (ModelProvider.owner_id.is_(None)))
            .order_by(ModelProvider.created_at.desc())
        )
    ).all()
    return ok(
        {"items": [model_provider_to_dict(item) for item in providers], "total": len(providers)}
    )


@router.post("/model-providers", response_model=ApiResponse[ModelProviderOut])
async def create_model_provider(
    payload: CreateModelProviderRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await ensure_model_tables(db)
    provider = ModelProvider(
        owner_id=user.id,
        name=payload.name,
        provider_type=payload.provider_type,
        base_url=payload.base_url.rstrip("/"),
        api_key_ref=payload.api_key or None,
        default_model=payload.default_model,
        supports_streaming=payload.supports_streaming,
        supports_embeddings=payload.supports_embeddings,
        config=strip_sensitive(payload.config),
        status="active",
    )
    db.add(provider)
    await db.flush()
    model = ModelConfig(
        provider_id=provider.id,
        name=payload.default_model,
        model_id=payload.default_model,
        purpose="chat",
        config={"created_from_provider": True},
    )
    db.add(model)
    await db.commit()
    await db.refresh(provider)
    return ok(
        model_provider_to_dict(await _get_provider(db, user, provider.id)), "模型供应商已创建"
    )


@router.patch("/model-providers/{provider_id}", response_model=ApiResponse[ModelProviderOut])
async def update_model_provider(
    provider_id: str,
    payload: CreateModelProviderRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    provider = await _get_provider(db, user, provider_id)
    if provider.owner_id != user.id and user.role != "admin":
        raise ForbiddenError("只有创建者可修改模型供应商")
    provider.name = payload.name
    provider.provider_type = payload.provider_type
    provider.base_url = payload.base_url.rstrip("/")
    if payload.api_key:
        provider.api_key_ref = payload.api_key
    provider.default_model = payload.default_model
    provider.supports_streaming = payload.supports_streaming
    provider.supports_embeddings = payload.supports_embeddings
    provider.config = strip_sensitive(payload.config)
    await db.commit()
    return ok(model_provider_to_dict(provider), "模型供应商已更新")


@router.patch(
    "/model-providers/{provider_id}/credential",
    response_model=ApiResponse[dict],
)
async def update_model_provider_credential(
    provider_id: str,
    payload: UpdateModelProviderCredentialRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    provider = await _get_provider(db, user, provider_id)
    if provider.owner_id != user.id and user.role != "admin":
        raise ForbiddenError("只有创建者或管理员可更新模型凭据")
    provider.api_key_ref = payload.api_key.strip()
    if not provider.api_key_ref:
        raise ValidationAppError("API Key 不能为空")
    await db.commit()
    return ok({"id": provider.id, "api_key_set": True}, "模型凭据已更新")


@router.delete("/model-providers/{provider_id}", response_model=ApiResponse[dict])
async def delete_model_provider(
    provider_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    provider = await _get_provider(db, user, provider_id)
    if provider.owner_id != user.id and user.role != "admin":
        raise ForbiddenError("只有创建者可删除模型供应商")
    provider.deleted_at = utcnow()
    provider.status = "deleted"
    await db.commit()
    return ok({"id": provider.id, "deleted": True})


@router.get("/model-providers/builtin", response_model=ApiResponse[dict])
async def list_builtin_providers():
    """返回 model_provider 模块内置支持的厂商列表。

    前端"新增模型"下拉菜单应从此端点获取可用厂商，
    而非从 /model-providers 读取用户自建记录。
    """
    return ok({"items": get_builtin_providers()})


@router.get("/model-configs", response_model=ApiResponse[dict])
async def list_model_configs(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await ensure_model_tables(db)
    configs = (
        await db.scalars(
            select(ModelConfig)
            .options(selectinload(ModelConfig.provider))
            .join(ModelProvider)
            .where(ModelConfig.deleted_at.is_(None), ModelProvider.deleted_at.is_(None))
            .where((ModelProvider.owner_id == user.id) | (ModelProvider.owner_id.is_(None)))
            .order_by(ModelConfig.updated_at.desc())
        )
    ).all()
    return ok({"items": [model_config_to_dict(item) for item in configs], "total": len(configs)})


@router.post("/model-configs", response_model=ApiResponse[ModelConfigOut])
async def create_model_config(
    payload: CreateModelConfigRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if payload.provider_id:
        provider = await _get_provider(db, user, payload.provider_id)
    elif payload.provider_type:
        provider = await _resolve_provider_by_type(db, user, payload.provider_type)
    else:
        raise ValidationAppError("provider_id 与 provider_type 至少提供一个")
    config = ModelConfig(
        provider_id=provider.id,
        name=payload.name,
        model_id=payload.model_id,
        purpose=payload.purpose,
        context_window=payload.context_window,
        max_output_tokens=payload.max_output_tokens,
        temperature_default=payload.temperature_default,
        config=strip_sensitive(payload.config),
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return ok(model_config_to_dict(config), "模型配置已创建")


@router.patch("/model-configs/{config_id}", response_model=ApiResponse[ModelConfigOut])
async def update_model_config(
    config_id: str,
    payload: UpdateModelConfigRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    config = await db.scalar(
        select(ModelConfig)
        .options(selectinload(ModelConfig.provider))
        .where(ModelConfig.id == config_id, ModelConfig.deleted_at.is_(None))
    )
    if not config:
        raise NotFoundError("模型配置不存在")
    if config.provider.owner_id != user.id and user.role != "admin":
        raise ForbiddenError("无权修改该模型配置")
    if payload.name is not None:
        config.name = payload.name
    if payload.model_id is not None:
        config.model_id = payload.model_id
    if payload.purpose is not None:
        config.purpose = payload.purpose
    if payload.context_window is not None:
        config.context_window = payload.context_window
    if payload.max_output_tokens is not None:
        config.max_output_tokens = payload.max_output_tokens
    if payload.temperature_default is not None:
        config.temperature_default = payload.temperature_default
    if payload.config is not None:
        config.config = strip_sensitive(payload.config)
    await db.commit()
    await db.refresh(config)
    return ok(model_config_to_dict(config), "模型配置已更新")


@router.delete("/model-configs/{config_id}", response_model=ApiResponse[dict])
async def delete_model_config(
    config_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    config = await db.scalar(
        select(ModelConfig)
        .options(selectinload(ModelConfig.provider))
        .where(ModelConfig.id == config_id, ModelConfig.deleted_at.is_(None))
    )
    if not config:
        raise NotFoundError("模型配置不存在")
    if config.provider.owner_id != user.id and user.role != "admin":
        raise ForbiddenError("无权删除该模型配置")
    config.deleted_at = utcnow()
    config.status = "deleted"
    await db.commit()
    return ok({"id": config.id, "deleted": True}, "模型配置已删除")


@router.post("/model-configs/{config_id}/activate", response_model=ApiResponse[dict])
async def activate_model_config(
    config_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """将指定模型配置设为当前用户的默认模型。"""
    config = await db.scalar(
        select(ModelConfig)
        .options(selectinload(ModelConfig.provider))
        .where(ModelConfig.id == config_id, ModelConfig.deleted_at.is_(None))
    )
    if not config:
        raise NotFoundError("模型配置不存在")
    if (
        config.provider.owner_id != user.id
        and config.provider.owner_id is not None
        and user.role != "admin"
    ):
        raise ForbiddenError("无权使用该模型配置")

    user.extra = {**(user.extra or {}), "default_model_config_id": config_id}
    await db.commit()
    return ok(
        {"default_model_config_id": config_id, "name": config.name, "model_id": config.model_id},
        f"已启用模型：{config.name}",
    )


@router.post("/model-configs/test", response_model=ApiResponse[dict])
async def test_model(
    payload: TestModelRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await ensure_model_tables(db)
    model_id = payload.model_config_id
    if model_id:
        selected = await db.scalar(
            select(ModelConfig)
            .options(selectinload(ModelConfig.provider))
            .where(ModelConfig.id == model_id, ModelConfig.deleted_at.is_(None))
        )
        if not selected:
            raise NotFoundError("模型配置不存在")
        if selected.provider.owner_id not in {None, user.id} and user.role != "admin":
            raise ForbiddenError("无权测试该模型配置")
    else:
        model = await db.scalar(
            select(ModelConfig)
            .join(ModelProvider)
            .where(ModelConfig.deleted_at.is_(None), ModelProvider.deleted_at.is_(None))
            .where((ModelProvider.owner_id == user.id) | (ModelProvider.owner_id.is_(None)))
            .order_by(ModelConfig.created_at.asc())
        )
        if not model:
            raise NotFoundError("暂无可测试模型")
        model_id = model.id
    result = await test_model_config(db, model_id, payload.prompt)
    return ok(
        {"response": result.text, "model": result.model, "usage": result.usage}, "模型测试完成"
    )


@router.get("/models/available", response_model=ApiResponse[dict])
async def list_available_models(
    force_refresh: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """返回所有可用的模型（来自数据库配置）

    force_refresh=True 时，向服务商请求最新模型列表并同步到数据库。
    """
    await ensure_model_tables(db)

    providers = (
        await db.scalars(
            select(ModelProvider)
            .where(ModelProvider.deleted_at.is_(None), ModelProvider.status == "active")
            .where((ModelProvider.owner_id == user.id) | (ModelProvider.owner_id.is_(None)))
        )
    ).all()

    if force_refresh:
        for provider in providers:
            api_key = ""
            if provider.api_key_ref == "env:ARK_API_KEY":
                from app.core.config import get_settings

                api_key = getattr(get_settings(), "ark_api_key", "") or ""
            elif provider.api_key_ref and provider.api_key_ref != "mock":
                api_key = provider.api_key_ref

            try:
                from model_provider import create_provider
                from model_provider.core.config import ModelConfig as MPModelConfig

                mp_config = MPModelConfig(
                    provider=normalize_provider_type(provider.provider_type),
                    model=provider.default_model or "gpt-4o",
                    api_key=api_key,
                    base_url=provider.base_url or None,
                )
                prov = create_provider(mp_config)
                remote_models = await prov.list_models()
            except Exception as e:
                import logging

                logging.getLogger(__name__).warning(
                    f"list_models 失败 provider={provider.name} error={e}"
                )
                remote_models = []

            # 同步到数据库： upsert ModelConfig
            existing = {
                c.model_id: c
                for c in (
                    await db.scalars(
                        select(ModelConfig).where(ModelConfig.provider_id == provider.id)
                    )
                ).all()
            }

            for rm in remote_models:
                model_id = rm.get("id") or rm.get("model_id") or rm.get("name", "")
                if not model_id:
                    continue
                if model_id in existing:
                    existing[model_id].name = rm.get("name", model_id)
                    existing[model_id].status = rm.get("status", "active")
                else:
                    new_cfg = ModelConfig(
                        provider_id=provider.id,
                        name=rm.get("name", model_id),
                        model_id=model_id,
                        purpose="chat",
                        context_window=rm.get("context_window", 128000),
                        status=rm.get("status", "active"),
                        config={"source": "remote", "remote_id": rm.get("id")},
                    )
                    db.add(new_cfg)

            for model_id, cfg in existing.items():
                if model_id not in {
                    rm.get("id") or rm.get("model_id") or rm.get("name", "") for rm in remote_models
                }:
                    cfg.status = "unavailable"

            await db.flush()

    items = []
    for provider in providers:
        configs = (
            await db.scalars(
                select(ModelConfig).where(
                    ModelConfig.provider_id == provider.id, ModelConfig.deleted_at.is_(None)
                )
            )
        ).all()

        for config in configs:
            items.append(
                {
                    "provider_id": provider.id,
                    "provider_name": provider.name,
                    "model_id": config.model_id,
                    "config_id": config.id,
                    "name": config.name,
                    "context_window": config.context_window,
                    "status": config.status,
                }
            )

    await db.commit()
    return ok({"items": items, "total": len(items)})

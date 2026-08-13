from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import agents, artifacts, auth, context, conversations, deployments, external_agents, files, knowledge, logs, mcp, messages, models, orchestrator, sandbox, security_ops, skills, tasks, tools, websocket, workspace_files, workspaces
from app.core.config import get_settings
from app.core.errors import AppError
from db.session import AsyncSessionLocal
from app.core.logging_config import configure_logging
from app.core.response import fail, ok
from app.services.runtime.generation_records import fail_abandoned_generation_records
from app.persistence.runtime_store import SQLRunStore
from app.services.admin_bootstrap import bootstrap_admin_from_settings
from app.services.system_seed import ensure_system_data
from common.logger import get_logger


configure_logging()
settings = get_settings()
logger = get_logger("app.main")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with AsyncSessionLocal() as db:
        await ensure_system_data(db)
        await bootstrap_admin_from_settings(db, settings)
        try:
            recovered = await fail_abandoned_generation_records(db, reason="process_lost")
            await SQLRunStore().recover_process_lost()
            if recovered:
                logger.info("Recovered abandoned generations", count=len(recovered))
        except Exception as exc:
            await db.rollback()
            logger.warning("Abandoned generation recovery skipped", error=str(exc))
        yield


app = FastAPI(title="AgentHub API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def app_error_handler(_request: Request, exc: AppError):
    return JSONResponse(status_code=exc.status_code, content=fail(exc.code, exc.message))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content=fail(1002, "参数校验失败", {"errors": exc.errors()}),
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    error_id = uuid4().hex
    logger.exception(
        "Unhandled request exception",
        error_id=error_id,
        path=request.url.path,
        method=request.method,
    )
    return JSONResponse(
        status_code=500,
        content=fail(5000, "Internal server error", {"error_id": error_id}),
    )


@app.get("/api/v1/health")
async def health():
    return ok({"status": "ok", "provider": "mock" if settings.use_mock_llm else "ark"})


@app.get("/health")
async def root_health():
    return {"status": "ok", "provider": "mock" if settings.use_mock_llm else "ark"}


for router in [
    auth.router,
    agents.router,
    conversations.router,
    messages.router,
    artifacts.router,
    deployments.router,
    external_agents.router,
    files.router,
    knowledge.router,
    models.router,
    mcp.router,
    orchestrator.router,
    tasks.router,
    skills.router,
    tools.router,
    sandbox.router,
    workspaces.router,
    workspace_files.router,
    security_ops.router,
    context.router,
    logs.router,
]:
    app.include_router(router, prefix=settings.api_prefix)

for router in [
    auth.compat_router,
    conversations.compat_router,
    messages.compat_router,
    artifacts.compat_router,
    deployments.compat_router,
    orchestrator.compat_router,
]:
    app.include_router(router)

app.include_router(websocket.router)

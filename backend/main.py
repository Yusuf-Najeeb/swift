
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.agents.providers import configure_openrouter
from backend.api import router as api_router
from backend.config import Settings, get_settings
from backend.dependencies import require_api_bearer
from backend.mcp import MCPBearerAuthMiddleware, get_mcp_server

log = logging.getLogger("swift.main")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    mcp_asgi = None
    if settings.mcp_server_enabled:
        mcp_asgi = get_mcp_server().http_app(path="/")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_openrouter()
        if mcp_asgi is not None:
            if not settings.mcp_server_bearer_token:
                log.warning(
                    "MCP HTTP is mounted without SWIFT_MCP_SERVER_BEARER_TOKEN — "
                    "unsafe on a public network; set a token in production"
                )
            async with mcp_asgi.router.lifespan_context(app):
                yield
        else:
            yield

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Swift — a technical writer AI platform.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if settings.mcp_server_enabled and settings.mcp_server_bearer_token:
        app.add_middleware(
            MCPBearerAuthMiddleware,
            mount_path=settings.mcp_server_mount_path,
            token=settings.mcp_server_bearer_token,
        )

    @app.get("/", tags=["meta"])
    async def root() -> dict[str, str]:
        return {
            "service": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
        }

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": settings.app_name}

    @app.get(
        "/config",
        tags=["meta"],
        dependencies=[Depends(require_api_bearer)],
    )
    async def config_snapshot() -> dict[str, str | bool | float]:

        return {
            "orchestrator_model": settings.orchestrator_model,
            "writer_model": settings.writer_model,
            "evaluator_model": settings.evaluator_model,
            "image_agent_model": settings.image_agent_model,
            "article_storage": (
                "azure_blob"
                if (settings.azure_storage_connection_string or "").strip()
                else "local"
            ),
            "openrouter_base_url": settings.openrouter_base_url,
            "mcp_server_enabled": settings.mcp_server_enabled,
            "mcp_server_mount_path": settings.mcp_server_mount_path,
            "mcp_server_bearer_required": bool(
                settings.mcp_server_bearer_token
            ),
            "sse_keep_alive_seconds": settings.sse_keep_alive_seconds,
        }

    app.include_router(api_router)

    if mcp_asgi is not None:
        app.mount(settings.mcp_server_mount_path, mcp_asgi)

    return app


app = create_app()

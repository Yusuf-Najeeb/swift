"""FastAPI entry point for Swift Writer."""

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

    # Build the MCP ASGI sub-app *before* the FastAPI app so we can
    # pass its lifespan through. FastMCP's Streamable-HTTP transport
    # initialises session-store state in its own lifespan; if we skip
    # it, the first tool call 500s with a cryptic missing-state error.
    # Only pay this cost when MCP is actually enabled.
    mcp_asgi = None
    if settings.mcp_server_enabled:
        # ``path='/'`` means the MCP transport serves its routes
        # relative to the mount point — so requests to
        # ``<host><mount_path>`` land on the MCP root rather than
        # being double-prefixed.
        mcp_asgi = get_mcp_server().http_app(path="/")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Wire the Agents SDK to OpenRouter once at startup. We do
        # this before entering the MCP lifespan so that any tool
        # invocation fired during MCP startup (there shouldn't be,
        # but belt-and-braces) already has a configured client.
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

    # Add the MCP bearer middleware *before* mounting the ASGI app.
    # Starlette middleware wraps the whole application, so order
    # doesn't matter for dispatch, but adding the auth middleware
    # first makes the surface order read top-down as: CORS → auth →
    # routes, which matches how a debug reader expects it to flow.
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
        """Expose non-secret configuration for quick diagnostics."""

        return {
            "orchestrator_model": settings.orchestrator_model,
            "writer_model": settings.writer_model,
            "evaluator_model": settings.evaluator_model,
            "image_agent_model": settings.image_agent_model,
            "openrouter_base_url": settings.openrouter_base_url,
            "mcp_server_enabled": settings.mcp_server_enabled,
            "mcp_server_mount_path": settings.mcp_server_mount_path,
            "mcp_server_bearer_required": bool(
                settings.mcp_server_bearer_token
            ),
            "sse_keep_alive_seconds": settings.sse_keep_alive_seconds,
        }

    # Mount the API router (SSE streaming endpoint, and any future
    # REST routes) before the MCP ASGI sub-app so its paths are
    # dispatched by FastAPI's router rather than swallowed by a
    # permissive MCP mount.
    app.include_router(api_router)

    if mcp_asgi is not None:
        # Mount last so FastAPI's own meta routes (``/``, ``/health``,
        # ``/config``) take precedence if someone sets the MCP mount
        # path to ``/`` (they shouldn't, but fail-safely).
        app.mount(settings.mcp_server_mount_path, mcp_asgi)

    return app


app = create_app()

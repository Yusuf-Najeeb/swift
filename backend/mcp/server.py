
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator, List, Optional

from fastmcp import FastMCP

from backend.agents import (
    ArticleBrief,
    FinalArticle,
    illustrate_article,
    orchestrate_article,
)
from backend.agents.providers import configure_openrouter
from backend.config import Settings, get_settings

if TYPE_CHECKING:  # pragma: no cover
    from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger("swift.mcp.server")

MCP_SERVER_NAME = "swift-writer"
MCP_SERVER_INSTRUCTIONS = (
    "Swift is a multi-agent AI article writer. Call ``write_article`` "
    "with a topic (and optional tone/length/keywords/audience/notes) "
    "to run the full Writer ↔ Evaluator revision loop and get back an "
    "illustrated, publishable article in Markdown."
)


def build_mcp_server(
    *,
    name: str = MCP_SERVER_NAME,
    instructions: str = MCP_SERVER_INSTRUCTIONS,
) -> FastMCP:

    mcp = FastMCP(name=name, instructions=instructions)

    @mcp.tool(
        name="write_article",
        description=(
            "Generate a full-length, illustrated article on a given "
            "topic. Runs Swift's Writer ↔ Evaluator revision loop and "
            "the Image Agent end-to-end; returns Markdown plus a list "
            "of image URLs and any Mermaid diagrams the Writer chose "
            "to include."
        ),
    )
    async def write_article(
        topic: str,
        tone: str = "professional",
        length: str = "medium",
        keywords: Optional[List[str]] = None,
        audience: Optional[str] = None,
        extra_notes: Optional[str] = None,
        max_retries: Optional[int] = None,
    ) -> FinalArticle:

        log.info(
            "write_article invoked: topic=%r tone=%r length=%r keywords=%s",
            topic,
            tone,
            length,
            keywords,
        )
        brief = ArticleBrief(
            topic=topic,
            tone=tone,
            length=length,  # type: ignore[arg-type]
            keywords=keywords or [],
            audience=audience,
            extra_notes=extra_notes,
        )
        run = await orchestrate_article(brief, max_retries=max_retries)
        final = await illustrate_article(run.final_draft)
        log.info(
            "write_article complete: approved=%s iterations=%d images=%d diagrams=%d",
            run.approved,
            run.iterations,
            len(final.images),
            len(final.diagrams),
        )
        return final

    return mcp


_SERVER_SINGLETON: Optional[FastMCP] = None


def get_mcp_server() -> FastMCP:

    global _SERVER_SINGLETON
    if _SERVER_SINGLETON is None:
        _SERVER_SINGLETON = build_mcp_server()
    return _SERVER_SINGLETON




class MCPBearerAuthMiddleware:

    def __init__(
        self,
        app: "ASGIApp",
        *,
        mount_path: str,
        token: str,
    ) -> None:
        if not token:
            raise ValueError("MCPBearerAuthMiddleware requires a non-empty token")
        if not mount_path.startswith("/"):
            raise ValueError("mount_path must start with '/'")
        self.app = app
        self._mount_path = mount_path.rstrip("/") or "/"
        self._token = token

    async def __call__(
        self,
        scope: "Scope",
        receive: "Receive",
        send: "Send",
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if not self._is_in_scope(path):
            await self.app(scope, receive, send)
            return

        if not self._is_authenticated(scope):
            await self._send_unauthorized(send)
            return

        await self.app(scope, receive, send)

    def _is_in_scope(self, path: str) -> bool:
        mount = self._mount_path
        if mount == "/":
            return True
        return path == mount or path.startswith(mount + "/")

    def _is_authenticated(self, scope: "Scope") -> bool:
        for raw_name, raw_value in scope.get("headers", []):
            if raw_name == b"authorization":
                value = raw_value.decode("latin-1")
                prefix, _, token = value.partition(" ")
                if prefix.lower() == "bearer" and token == self._token:
                    return True
                return False
        return False

    async def _send_unauthorized(self, send: "Send") -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (
                        b"www-authenticate",
                        b'Bearer realm="swift-writer-mcp"',
                    ),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": (
                    b'{"error":"unauthorized",'
                    b'"detail":"missing or invalid bearer token for MCP endpoint"}'
                ),
            }
        )




@asynccontextmanager
async def _stdio_lifespan() -> AsyncIterator[None]:

    configure_openrouter()
    yield


def run_stdio(*, settings: Optional[Settings] = None) -> None:

    settings = settings or get_settings()
    log.info(
        "starting Swift MCP server in stdio mode (enabled=%s, bearer=%s)",
        settings.mcp_server_enabled,
        bool(settings.mcp_server_bearer_token),
    )
    configure_openrouter()
    get_mcp_server().run()


if __name__ == "__main__":  # pragma: no cover
    run_stdio()

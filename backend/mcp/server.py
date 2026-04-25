"""FastMCP server exposing Swift as an MCP tool provider.

This module lets Swift run in two modes that share the same
:class:`~fastmcp.FastMCP` instance and therefore the same tool
registry:

* **Stdio mode** — for local clients like Claude Desktop or the
  ``mcp`` CLI. Launched with ``python -m backend.mcp.server``; the
  process reads MCP JSON-RPC over stdin and writes responses to
  stdout. The OS process boundary is the only trust boundary; the
  ``SWIFT_MCP_SERVER_BEARER_TOKEN`` setting is deliberately ignored
  in this mode.
* **HTTP mode** — for cloud deployments and browser-adjacent
  clients. :func:`backend.main.create_app` mounts the FastMCP
  Streamable-HTTP ASGI app under ``SWIFT_MCP_SERVER_MOUNT_PATH``
  (``/mcp`` by default). :class:`MCPBearerAuthMiddleware` enforces
  ``Authorization: Bearer <token>`` on every request that lands
  below the mount path when a token is configured.

Tool surface
------------
One tool for now: ``write_article``. It accepts the flat set of
fields that make up an :class:`~backend.agents.schemas.ArticleBrief`
(chosen over a single ``brief`` object parameter because MCP clients
expose tools more clearly when top-level arguments are primitives —
Claude Desktop's UI, for instance, reads the docstring line for
each parameter). The tool runs the Orchestrator's revision loop and
then the Image Agent, and returns the final
:class:`~backend.agents.schemas.FinalArticle`.

``get_article`` is intentionally *not* implemented here — Swift has
no article storage yet (see Step 9). Adding it now would mean
shipping a placeholder that always 404s, which is worse than simply
not advertising the tool. The Step 9 scaffolding adds it.
"""

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
    """Construct a fresh ``FastMCP`` instance with Swift's tools wired.

    Exposed as a builder (not just a module-level singleton) so tests
    can spin up an isolated server instance per test case without
    state leaking across suites. :func:`get_mcp_server` is what the
    FastAPI app and the stdio entrypoint use at runtime.
    """

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
        """Write and illustrate an article.

        Args:
            topic: Subject line the article is about. Required.
            tone: Voice the Writer should adopt (``professional``,
                ``casual``, ``academic``, ``conversational``, ...).
            length: ``short`` (~400 words), ``medium`` (~800),
                ``long`` (~1500+).
            keywords: Words/phrases that must appear naturally in the
                text. Empty list means none required.
            audience: Intended reader (e.g. ``backend engineers``).
                ``None`` = informed general reader.
            extra_notes: Anything else the Writer should know —
                background URLs the Evaluator should verify, angle
                hints, stylistic preferences.
            max_retries: Override for how many revision attempts the
                Orchestrator makes before giving up. ``None`` = use
                the server-side default (``SWIFT_ORCHESTRATOR_MAX_RETRIES``).

        Returns:
            A :class:`FinalArticle` with ``body_markdown`` (with image
            markers already substituted for Pollinations URLs), an
            ``images`` list (description + URL + alt text per image),
            and a ``diagrams`` list (Mermaid source per diagram block).
        """

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
    """Return a process-wide ``FastMCP`` instance.

    Lazy-built so importing :mod:`backend.mcp` doesn't force agent
    wiring to happen at collection time. Subsequent calls return the
    same instance — the FastAPI ASGI mount and the stdio entrypoint
    must share a tool registry, otherwise tools registered in one
    mode would be invisible in the other.
    """

    global _SERVER_SINGLETON
    if _SERVER_SINGLETON is None:
        _SERVER_SINGLETON = build_mcp_server()
    return _SERVER_SINGLETON


# ─── HTTP-mount auth middleware ─────────────────────────────────────


class MCPBearerAuthMiddleware:
    """Scoped bearer-token check for the MCP HTTP mount.

    Applied at the FastAPI app level but only enforces on requests
    whose path starts with ``mount_path``. We do path-scoped auth
    here (rather than mounting FastMCP behind a FastAPI dependency)
    because the FastMCP Streamable-HTTP sub-app is a standalone ASGI
    application — FastAPI's dependency-injection machinery doesn't
    reach across the mount boundary. A small middleware that inspects
    ``scope["path"]`` is the portable way.

    Unauthenticated requests get a 401 with a short JSON body.
    Unrelated paths pass through untouched so the REST endpoints and
    healthchecks stay open.
    """

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
        # Strip a trailing slash if present — Starlette's router uses
        # the exact form we configured on the mount, but we compare
        # prefix-wise so the normalisation has to match config.
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
        # Match ``/mcp`` *and* ``/mcp/...`` but NOT ``/mcps``.
        return path == mount or path.startswith(mount + "/")

    def _is_authenticated(self, scope: "Scope") -> bool:
        for raw_name, raw_value in scope.get("headers", []):
            if raw_name == b"authorization":
                value = raw_value.decode("latin-1")
                # "Bearer" is case-sensitive per RFC 6750 §2.1, but in
                # practice clients send mixed case; accept both.
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
                    # Tell the client what auth we expected — handy
                    # for debugging, and per RFC 6750 §3.
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


# ─── stdio entrypoint (for Claude Desktop et al.) ──────────────────


@asynccontextmanager
async def _stdio_lifespan() -> AsyncIterator[None]:
    """One-shot async context that mirrors the FastAPI lifespan for
    the stdio server: configures OpenRouter once before the loop
    starts. We deliberately do not start any MCP subprocesses here —
    agent MCP clients are spun up per-request inside the Orchestrator
    so cold-start cost is pay-as-you-go."""

    configure_openrouter()
    yield


def run_stdio(*, settings: Optional[Settings] = None) -> None:
    """Run the Swift MCP server over stdio.

    Invoked by ``python -m backend.mcp.server``; the function exists
    as a named entrypoint so tests (and future CLI wrappers) can
    drive it without relying on ``__main__``-only semantics.
    """

    settings = settings or get_settings()
    log.info(
        "starting Swift MCP server in stdio mode (enabled=%s, bearer=%s)",
        settings.mcp_server_enabled,
        bool(settings.mcp_server_bearer_token),
    )
    configure_openrouter()
    # ``FastMCP.run`` is synchronous and manages its own event loop.
    # The stdio transport is the library default so no kwargs needed.
    get_mcp_server().run()


if __name__ == "__main__":  # pragma: no cover
    run_stdio()

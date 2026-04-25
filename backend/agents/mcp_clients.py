"""MCP client factories for Swift's agents.

Swift is both an MCP *server* (Step 6, exposes ``write_article`` /
``get_article`` to external MCP clients) and — as of Step 2b — an MCP
*consumer*. The Writer agent can be wired to one or more external MCP
servers so it can reach for live tools (URL fetch, web search, ...)
while drafting an article.

This module is the thin factory layer between Swift's declarative
:class:`~backend.agents.schemas.MCPServerSpec` config and the Agents
SDK's concrete ``MCPServer`` implementations. Keeping the concrete
objects behind a factory means:

* Tests can assert on specs without spinning up subprocesses.
* The orchestrator / FastAPI handler can manage the ``connect()`` /
  ``cleanup()`` lifecycle around a single run.
* Swapping the default research stack (e.g. Tavily instead of Fetch)
  is a one-line change to :data:`DEFAULT_WRITER_FETCH_SPEC`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Iterable, List, Optional

from agents.mcp import (
    MCPServer,
    MCPServerSse,
    MCPServerStdio,
    MCPServerStreamableHttp,
)

from backend.config import MCPServerSpec, Settings, get_settings

log = logging.getLogger("swift.mcp")


def _fetch_spec() -> MCPServerSpec:
    """Build a fresh ``mcp-server-fetch`` spec.

    We instantiate a new spec per call rather than sharing a module-level
    singleton because each agent owns its own ``MCPServer`` subprocess
    lifetime. Reusing one spec across agents is fine — the actual
    ``MCPServer`` instances built from it are independent — but fresh
    instances also make it safe for callers to mutate (e.g. bump the
    timeout) without surprising each other.
    """

    return MCPServerSpec(
        name="fetch",
        transport="stdio",
        command="uvx",
        args=["mcp-server-fetch"],
        # Stops the huge progress bar / "Resolving dependencies…" UI from
        # painting into the Uvicorn terminal when ``uvx`` cold-starts the
        # tool. Does not remove the *time* the first download takes — see
        # README for pre-warm and for disabling MCP in local dev.
        env={"UV_NO_PROGRESS": "1"},
    )


DEFAULT_WRITER_FETCH_SPEC = _fetch_spec()
"""Default research tool for the Writer: the reference ``mcp-server-fetch``.

Zero-API-key URL reader maintained by the MCP team that turns any HTTP
URL into Markdown. Using ``uvx`` means the user doesn't need to
pre-install the package — uv fetches it on first launch.
"""


DEFAULT_EVALUATOR_FETCH_SPEC = _fetch_spec()
"""Default fact-checking tool for the Evaluator.

Same server as the Writer uses. The Writer is instructed to cite URLs
inline, so giving the Evaluator fetch lets it click through to the
source and verify claims — no API key required.
"""


SERPER_MCP_PACKAGE = "serper-search-scrape-mcp-server"
"""Pinned npm package that provides ``google_search`` + ``scrape`` tools.

See https://www.npmjs.com/package/serper-search-scrape-mcp-server. We
launch it via ``npx -y`` so users don't need a global install, and we
inject ``SERPER_API_KEY`` into the subprocess env rather than relying
on process-wide env leakage — that way the spec is self-contained and
the key is present only in the subprocess that needs it.
"""


def build_serper_spec(api_key: str) -> MCPServerSpec:
    """Produce a ``serper-search-scrape-mcp-server`` stdio spec.

    The caller is responsible for checking that the key is present —
    we deliberately don't pull from ``Settings`` here so tests can
    build a spec with a throwaway key without reaching into config.
    """

    return MCPServerSpec(
        name="serper",
        transport="stdio",
        command="npx",
        args=["-y", SERPER_MCP_PACKAGE],
        env={
            "SERPER_API_KEY": api_key,
            # Keeps `npx` from spamming the Uvicorn TTY on first install.
            "NPM_CONFIG_PROGRESS": "false",
        },
    )


def build_mcp_server(spec: MCPServerSpec, *, cache_tools_list: bool = True) -> MCPServer:
    """Turn a :class:`MCPServerSpec` into a concrete ``MCPServer`` instance.

    ``cache_tools_list`` defaults to ``True`` because Swift runs the
    Writer in a revision loop: fetching the tool list on every iteration
    would add unnecessary round-trips.
    """

    common_kwargs: Dict[str, Any] = {
        "name": spec.name,
        "cache_tools_list": cache_tools_list,
        "client_session_timeout_seconds": spec.client_session_timeout_seconds,
    }

    if spec.transport == "stdio":
        if not spec.command:
            raise ValueError(
                f"MCP server {spec.name!r} uses stdio transport but has no 'command'."
            )
        params: Dict[str, Any] = {"command": spec.command, "args": list(spec.args)}
        if spec.env:
            params["env"] = dict(spec.env)
        return MCPServerStdio(params=params, **common_kwargs)  # type: ignore[arg-type]

    if spec.transport == "http":
        if not spec.url:
            raise ValueError(
                f"MCP server {spec.name!r} uses http transport but has no 'url'."
            )
        http_params: Dict[str, Any] = {"url": spec.url}
        if spec.headers:
            http_params["headers"] = dict(spec.headers)
        return MCPServerStreamableHttp(params=http_params, **common_kwargs)  # type: ignore[arg-type]

    if spec.transport == "sse":
        if not spec.url:
            raise ValueError(
                f"MCP server {spec.name!r} uses sse transport but has no 'url'."
            )
        sse_params: Dict[str, Any] = {"url": spec.url}
        if spec.headers:
            sse_params["headers"] = dict(spec.headers)
        return MCPServerSse(params=sse_params, **common_kwargs)  # type: ignore[arg-type]

    raise ValueError(f"Unknown MCP transport: {spec.transport!r}")


def writer_mcp_specs(settings: Optional[Settings] = None) -> List[MCPServerSpec]:
    """Return the resolved list of specs that should feed the Writer.

    Order: the default ``fetch`` server (if enabled) first, then any
    user-declared extras from ``SWIFT_WRITER_MCP_SERVERS``.
    """

    settings = settings or get_settings()
    specs: List[MCPServerSpec] = []
    if settings.writer_mcp_fetch_enabled:
        specs.append(DEFAULT_WRITER_FETCH_SPEC)
    specs.extend(settings.writer_mcp_servers)
    return specs


def build_writer_mcp_servers(settings: Optional[Settings] = None) -> List[MCPServer]:
    """Construct the list of live ``MCPServer`` instances for the Writer.

    These are *not* connected yet — the caller is responsible for
    ``await server.connect()`` before a run and ``await server.cleanup()``
    afterwards, or for using ``async with server`` context managers.
    """

    return [build_mcp_server(spec) for spec in writer_mcp_specs(settings)]


def evaluator_mcp_specs(settings: Optional[Settings] = None) -> List[MCPServerSpec]:
    """Return the resolved list of specs that should feed the Evaluator.

    Resolution order (matches the attach order on the agent):

    1. ``mcp-server-fetch`` — when
       ``settings.evaluator_mcp_fetch_enabled``. Zero-config URL reader
       for following the Writer's citations.
    2. ``serper-search-scrape-mcp-server`` — when
       ``settings.evaluator_mcp_serper_enabled`` *and*
       ``settings.serper_api_key`` is non-empty. Skipped silently when
       the key is missing so users without a Serper account don't see
       subprocess failures at startup.
    3. User-declared extras from ``settings.evaluator_mcp_servers``
       (``SWIFT_EVALUATOR_MCP_SERVERS``) for things like Tavily/Brave
       or private MCP endpoints.
    """

    settings = settings or get_settings()
    specs: List[MCPServerSpec] = []
    if settings.evaluator_mcp_fetch_enabled:
        specs.append(DEFAULT_EVALUATOR_FETCH_SPEC)
    if settings.evaluator_mcp_serper_enabled and settings.serper_api_key:
        specs.append(build_serper_spec(settings.serper_api_key))
    specs.extend(settings.evaluator_mcp_servers)
    return specs


def build_evaluator_mcp_servers(settings: Optional[Settings] = None) -> List[MCPServer]:
    """Construct live ``MCPServer`` instances for the Evaluator.

    Same connection-lifecycle rules as :func:`build_writer_mcp_servers`.
    """

    return [build_mcp_server(spec) for spec in evaluator_mcp_specs(settings)]


async def safe_cleanup_mcp_servers(
    servers: Iterable[MCPServer],
    *,
    timeout: float = 5.0,
) -> None:
    """Best-effort cleanup of a batch of MCP servers.

    Node-based MCP servers launched via ``npx`` (looking at you,
    serper-search-scrape) occasionally do not exit cleanly when their
    stdin is closed — the cleanup coroutine then hangs awaiting
    ``Process.wait()`` and asyncio eventually cancels the enclosing
    task. We've already gotten the agent's result by the time cleanup
    runs, so a subprocess that refuses to die should never poison the
    overall result.

    Each server's cleanup is:

    * Shielded (:func:`asyncio.shield`) from outer cancellation so we
      don't leak zombie subprocesses when the orchestrator is itself
      being cancelled (e.g. HTTP client disconnect).
    * Bounded by ``timeout`` seconds.
    * Silent on failure — we log at WARNING and move on to the next
      server, so one stuck server can't prevent others from cleaning
      up.
    """

    for server in servers:
        try:
            await asyncio.wait_for(
                asyncio.shield(server.cleanup()),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            log.warning(
                "MCP cleanup timed out after %.1fs for server %r; "
                "subprocess may linger briefly",
                timeout,
                getattr(server, "name", "?"),
            )
        except asyncio.CancelledError:
            # Outer cancellation reached us through the shield (rare
            # on Python 3.13+) — log and keep draining the list so
            # other servers still get a chance to clean up.
            log.warning(
                "MCP cleanup cancelled for server %r",
                getattr(server, "name", "?"),
            )
        except Exception:  # noqa: BLE001 — cleanup must never raise
            log.exception(
                "MCP cleanup raised for server %r",
                getattr(server, "name", "?"),
            )


__all__ = [
    "DEFAULT_EVALUATOR_FETCH_SPEC",
    "DEFAULT_WRITER_FETCH_SPEC",
    "SERPER_MCP_PACKAGE",
    "build_evaluator_mcp_servers",
    "build_mcp_server",
    "build_serper_spec",
    "build_writer_mcp_servers",
    "evaluator_mcp_specs",
    "safe_cleanup_mcp_servers",
    "writer_mcp_specs",
]

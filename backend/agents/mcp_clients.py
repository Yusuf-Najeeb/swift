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
    return MCPServerSpec(
        name="fetch",
        transport="stdio",
        command="uvx",
        args=["mcp-server-fetch"],
        env={"UV_NO_PROGRESS": "1"},
    )


DEFAULT_WRITER_FETCH_SPEC = _fetch_spec()

DEFAULT_EVALUATOR_FETCH_SPEC = _fetch_spec()

SERPER_MCP_PACKAGE = "serper-search-scrape-mcp-server"


def build_serper_spec(api_key: str) -> MCPServerSpec:

    return MCPServerSpec(
        name="serper",
        transport="stdio",
        command="npx",
        args=["-y", SERPER_MCP_PACKAGE],
        env={
            "SERPER_API_KEY": api_key,
            "NPM_CONFIG_PROGRESS": "false",
        },
    )


def build_mcp_server(spec: MCPServerSpec, *, cache_tools_list: bool = True) -> MCPServer:
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
    settings = settings or get_settings()
    specs: List[MCPServerSpec] = []
    if settings.writer_mcp_fetch_enabled:
        specs.append(DEFAULT_WRITER_FETCH_SPEC)
    specs.extend(settings.writer_mcp_servers)
    return specs


def build_writer_mcp_servers(settings: Optional[Settings] = None) -> List[MCPServer]:
    return [build_mcp_server(spec) for spec in writer_mcp_specs(settings)]


def evaluator_mcp_specs(settings: Optional[Settings] = None) -> List[MCPServerSpec]:
  
    settings = settings or get_settings()
    specs: List[MCPServerSpec] = []
    if settings.evaluator_mcp_fetch_enabled:
        specs.append(DEFAULT_EVALUATOR_FETCH_SPEC)
    if settings.evaluator_mcp_serper_enabled and settings.serper_api_key:
        specs.append(build_serper_spec(settings.serper_api_key))
    specs.extend(settings.evaluator_mcp_servers)
    return specs


def build_evaluator_mcp_servers(settings: Optional[Settings] = None) -> List[MCPServer]:

    return [build_mcp_server(spec) for spec in evaluator_mcp_specs(settings)]


async def safe_cleanup_mcp_servers(
    servers: Iterable[MCPServer],
    *,
    timeout: float = 5.0,
) -> None:

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

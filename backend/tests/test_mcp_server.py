"""Tests for the FastMCP server layer (Step 6).

We split the surface into three independent concerns:

1. **Tool registration** — ``write_article`` is advertised with the
   right name, schema, and delegation behaviour.
2. **Bearer-token middleware** — the path-scoped auth check lets
   unrelated routes through and gates MCP routes correctly.
3. **FastAPI wiring** — :func:`backend.main.create_app` honours the
   ``mcp_server_*`` settings: mounts when enabled, skips when not,
   wires auth when a token is set.

The live smoke test (``test_mcp_write_article_live``) is gated on
``OPENROUTER_LIVE=1`` because it runs the full Orchestrator pipeline
end-to-end through the MCP tool call path.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.agents import ArticleBrief, ArticleRun, FinalArticle, WriterOutput
from backend.agents.schemas import EvaluatorFeedback, RevisionAttempt
from backend.config import Settings, get_settings
from backend.mcp.server import (
    MCPBearerAuthMiddleware,
    build_mcp_server,
    get_mcp_server,
)

LIVE = os.getenv("OPENROUTER_LIVE") == "1"


# ─── Tool registration & schema ───────────────────────────────────────


def test_build_mcp_server_advertises_write_article_tool() -> None:
    mcp = build_mcp_server()
    tools = asyncio.run(mcp.list_tools())

    names = {t.name for t in tools}
    assert "write_article" in names, (
        f"expected 'write_article' in advertised tools, got {sorted(names)}"
    )


def test_write_article_tool_schema_uses_flat_primitives() -> None:
    """The tool's input schema must expose brief fields as top-level
    primitives rather than a single nested ``brief`` object. MCP
    client UIs (Claude Desktop, Cursor) render each top-level
    parameter as its own form field — nesting hides required
    parameters behind a ``{...}`` blob that users can't fill in."""

    mcp = build_mcp_server()
    tool = next(
        t for t in asyncio.run(mcp.list_tools()) if t.name == "write_article"
    )
    params = tool.parameters

    properties = params.get("properties", {})
    # Flat shape expected.
    assert "topic" in properties
    assert "tone" in properties
    assert "length" in properties
    assert "keywords" in properties
    assert "audience" in properties
    assert "extra_notes" in properties
    assert "max_retries" in properties
    # ... and definitely not a nested brief blob:
    assert "brief" not in properties

    # Only ``topic`` must be required; everything else has a default.
    assert params.get("required") == ["topic"]


def test_get_mcp_server_is_idempotent_singleton() -> None:
    a = get_mcp_server()
    b = get_mcp_server()
    assert a is b, (
        "get_mcp_server must return a stable singleton so the stdio "
        "entrypoint and the FastAPI mount share the same tool registry"
    )


# ─── Tool delegation ──────────────────────────────────────────────────


def _fake_article_run(brief: ArticleBrief) -> ArticleRun:
    draft = WriterOutput(
        title="Fake Draft",
        summary="A placeholder article used for delegation tests.",
        body_markdown=f"# {brief.topic}\n\n[IMAGE: a minimalist vector illustration]",
        image_placeholder_count=1,
    )
    feedback = EvaluatorFeedback(
        score=9,
        strengths=["clear"],
        weaknesses=[],
        suggestions=[],
        approved=True,
    )
    return ArticleRun(
        brief=brief,
        attempts=[RevisionAttempt(iteration=1, draft=draft, feedback=feedback)],
    )


def test_write_article_tool_delegates_to_orchestrator_and_image_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end through the MCP tool dispatch path, but with the
    heavy agents mocked out. Verifies the tool:

    * builds an ``ArticleBrief`` from the flat primitives,
    * awaits ``orchestrate_article`` with the brief + retries,
    * passes the final draft to ``illustrate_article``,
    * returns the ``FinalArticle`` to the MCP client."""

    captured: dict[str, Any] = {}

    async def _fake_orchestrate(brief, *, max_retries=None, **kw):  # type: ignore[no-untyped-def]
        captured["brief"] = brief
        captured["max_retries"] = max_retries
        return _fake_article_run(brief)

    async def _fake_illustrate(draft, *, settings=None):  # type: ignore[no-untyped-def]
        captured["draft_title"] = draft.title
        return FinalArticle(
            title=draft.title,
            summary=draft.summary,
            body_markdown=draft.body_markdown.replace(
                "[IMAGE: a minimalist vector illustration]",
                "![a minimalist vector illustration](https://example.test/img.png)",
            ),
            images=[],
            diagrams=[],
            image_placeholder_count=1,
        )

    monkeypatch.setattr(
        "backend.mcp.server.orchestrate_article", _fake_orchestrate
    )
    monkeypatch.setattr(
        "backend.mcp.server.illustrate_article", _fake_illustrate
    )

    mcp = build_mcp_server()
    result = asyncio.run(
        mcp.call_tool(
            "write_article",
            {
                "topic": "Edge inference on mobile",
                "tone": "casual",
                "length": "short",
                "keywords": ["efficiency", "privacy"],
                "audience": "mobile engineers",
                "max_retries": 2,
            },
        )
    )

    # Brief was constructed correctly.
    brief = captured["brief"]
    assert isinstance(brief, ArticleBrief)
    assert brief.topic == "Edge inference on mobile"
    assert brief.tone == "casual"
    assert brief.length == "short"
    assert brief.keywords == ["efficiency", "privacy"]
    assert brief.audience == "mobile engineers"
    assert captured["max_retries"] == 2

    # Orchestrator → Image Agent handoff happened.
    assert captured["draft_title"] == "Fake Draft"

    # FastMCP returns a ToolResult. Grab the structured payload and
    # verify it round-trips into a FinalArticle.
    structured = result.structured_content
    assert structured is not None, (
        "expected structured_content on ToolResult — FastMCP should "
        "serialise the returned FinalArticle via its Pydantic schema"
    )
    final = FinalArticle.model_validate(structured)
    assert final.title == "Fake Draft"
    assert "example.test" in final.body_markdown


def test_write_article_tool_omits_max_retries_when_not_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``max_retries=None`` must reach the Orchestrator unchanged so
    the server-side default (``SWIFT_ORCHESTRATOR_MAX_RETRIES``) takes
    effect. If we forwarded 0 or some other sentinel, callers relying
    on the default would silently get zero retries."""

    captured: dict[str, Any] = {}

    async def _fake_orchestrate(brief, *, max_retries=None, **kw):  # type: ignore[no-untyped-def]
        captured["max_retries"] = max_retries
        return _fake_article_run(brief)

    async def _fake_illustrate(draft, *, settings=None):  # type: ignore[no-untyped-def]
        return FinalArticle(
            title=draft.title,
            summary=draft.summary,
            body_markdown=draft.body_markdown,
            images=[],
            diagrams=[],
            image_placeholder_count=1,
        )

    monkeypatch.setattr("backend.mcp.server.orchestrate_article", _fake_orchestrate)
    monkeypatch.setattr("backend.mcp.server.illustrate_article", _fake_illustrate)

    mcp = build_mcp_server()
    asyncio.run(mcp.call_tool("write_article", {"topic": "anything"}))

    assert captured["max_retries"] is None


# ─── MCPBearerAuthMiddleware unit tests ───────────────────────────────


def _make_scope(path: str, *, headers: list[tuple[bytes, bytes]] | None = None) -> dict[str, Any]:
    return {
        "type": "http",
        "path": path,
        "headers": headers or [],
        "method": "POST",
    }


def test_bearer_middleware_allows_matching_token() -> None:
    called: list[dict[str, Any]] = []

    async def _inner(scope, receive, send):  # type: ignore[no-untyped-def]
        called.append(scope)

    middleware = MCPBearerAuthMiddleware(
        _inner, mount_path="/mcp", token="secret"
    )

    async def _receive() -> dict[str, Any]:
        return {"type": "http.request"}

    scope = _make_scope(
        "/mcp/call",
        headers=[(b"authorization", b"Bearer secret")],
    )
    asyncio.run(middleware(scope, _receive, lambda _: asyncio.sleep(0)))

    assert called == [scope], "matching token must reach the inner app"


def test_bearer_middleware_rejects_missing_token() -> None:
    sent_messages: list[dict[str, Any]] = []

    async def _inner(scope, receive, send):  # type: ignore[no-untyped-def]
        pytest.fail("unauthorised request must not reach the inner app")

    async def _send(message: dict[str, Any]) -> None:
        sent_messages.append(message)

    middleware = MCPBearerAuthMiddleware(
        _inner, mount_path="/mcp", token="secret"
    )

    async def _receive() -> dict[str, Any]:
        return {"type": "http.request"}

    scope = _make_scope("/mcp/call")
    asyncio.run(middleware(scope, _receive, _send))

    assert sent_messages[0]["status"] == 401
    body = sent_messages[1]["body"]
    assert b"unauthorized" in body
    www_auth = dict(sent_messages[0]["headers"]).get(b"www-authenticate", b"")
    assert b"Bearer" in www_auth


def test_bearer_middleware_rejects_wrong_token() -> None:
    sent_messages: list[dict[str, Any]] = []

    async def _inner(scope, receive, send):  # type: ignore[no-untyped-def]
        pytest.fail("unauthorised request must not reach the inner app")

    async def _send(message: dict[str, Any]) -> None:
        sent_messages.append(message)

    middleware = MCPBearerAuthMiddleware(
        _inner, mount_path="/mcp", token="secret"
    )

    async def _receive() -> dict[str, Any]:
        return {"type": "http.request"}

    scope = _make_scope(
        "/mcp/call",
        headers=[(b"authorization", b"Bearer wrong")],
    )
    asyncio.run(middleware(scope, _receive, _send))

    assert sent_messages[0]["status"] == 401


def test_bearer_middleware_passes_through_unrelated_paths() -> None:
    called: list[str] = []

    async def _inner(scope, receive, send):  # type: ignore[no-untyped-def]
        called.append(scope["path"])

    middleware = MCPBearerAuthMiddleware(
        _inner, mount_path="/mcp", token="secret"
    )

    async def _receive() -> dict[str, Any]:
        return {"type": "http.request"}

    for path in ["/health", "/", "/config", "/mcps"]:
        scope = _make_scope(path)
        asyncio.run(middleware(scope, _receive, lambda _: asyncio.sleep(0)))

    # Note: "/mcps" must NOT match the "/mcp" prefix.
    assert set(called) == {"/health", "/", "/config", "/mcps"}


def test_bearer_middleware_accepts_case_insensitive_scheme() -> None:
    """RFC 6750 §2.1 technically mandates 'Bearer' with that exact
    capitalisation, but real-world clients (including curl's default
    ``--oauth2-bearer`` on some versions) send ``bearer``. Accepting
    both avoids a frustrating class of auth failures for no real
    security gain."""

    called: list[str] = []

    async def _inner(scope, receive, send):  # type: ignore[no-untyped-def]
        called.append(scope["path"])

    middleware = MCPBearerAuthMiddleware(
        _inner, mount_path="/mcp", token="secret"
    )

    async def _receive() -> dict[str, Any]:
        return {"type": "http.request"}

    scope = _make_scope(
        "/mcp/call",
        headers=[(b"authorization", b"bearer secret")],
    )
    asyncio.run(middleware(scope, _receive, lambda _: asyncio.sleep(0)))

    assert called == ["/mcp/call"]


def test_bearer_middleware_passes_non_http_scopes() -> None:
    """Websocket and lifespan scopes must flow through unchanged so
    startup/shutdown events and any future WS endpoints aren't broken
    by the auth check."""

    received: list[str] = []

    async def _inner(scope, receive, send):  # type: ignore[no-untyped-def]
        received.append(scope["type"])

    middleware = MCPBearerAuthMiddleware(
        _inner, mount_path="/mcp", token="secret"
    )

    async def _noop(*args, **kwargs):  # type: ignore[no-untyped-def]
        return None

    for scope_type in ["lifespan", "websocket"]:
        asyncio.run(
            middleware({"type": scope_type}, _noop, _noop)  # type: ignore[arg-type]
        )

    assert received == ["lifespan", "websocket"]


def test_bearer_middleware_rejects_empty_token_at_construction() -> None:
    with pytest.raises(ValueError):
        MCPBearerAuthMiddleware(lambda *a, **k: None, mount_path="/mcp", token="")


# ─── Settings wiring ──────────────────────────────────────────────────


def _settings_with_mcp(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "OPENROUTER_API_KEY": "sk-test-dummy",
        "SWIFT_MCP_SERVER_ENABLED": True,
        "SWIFT_MCP_SERVER_MOUNT_PATH": "/mcp",
        "SWIFT_MCP_SERVER_BEARER_TOKEN": None,
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


def test_settings_mcp_defaults() -> None:
    settings = _settings_with_mcp()
    assert settings.mcp_server_enabled is True
    assert settings.mcp_server_mount_path == "/mcp"
    assert settings.mcp_server_bearer_token is None


def test_settings_mcp_mount_path_rejects_bare_name() -> None:
    with pytest.raises(Exception):
        _settings_with_mcp(SWIFT_MCP_SERVER_MOUNT_PATH="mcp")


def test_settings_mcp_mount_path_strips_trailing_slash() -> None:
    settings = _settings_with_mcp(SWIFT_MCP_SERVER_MOUNT_PATH="/mcp/")
    assert settings.mcp_server_mount_path == "/mcp"


# ─── FastAPI wiring ──────────────────────────────────────────────────


def test_create_app_exposes_meta_endpoints_when_mcp_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.config import get_settings as _get_settings
    from backend.main import create_app

    _get_settings.cache_clear()
    monkeypatch.setenv("SWIFT_MCP_SERVER_ENABLED", "true")
    app = create_app(_settings_with_mcp())

    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "service": "Swift Writer"}

        r = client.get("/config")
        assert r.status_code == 200
        snap = r.json()
        assert snap["mcp_server_enabled"] is True
        assert snap["mcp_server_mount_path"] == "/mcp"
        assert snap["mcp_server_bearer_required"] is False


def test_create_app_mounts_mcp_path_when_enabled() -> None:
    """The MCP Streamable-HTTP transport requires POSTs — a plain GET
    should still reach the mounted app (and return whatever FastMCP's
    sub-app serves), not a 404 from FastAPI."""

    from backend.main import create_app

    app = create_app(_settings_with_mcp())

    with TestClient(app) as client:
        r = client.get("/mcp/")
        # FastMCP returns 406/405 for non-POST methods on its JSON-RPC
        # endpoint. The important thing is it's NOT 404 — if it were,
        # the mount didn't take.
        assert r.status_code != 404, (
            f"expected MCP mount to handle /mcp/, got {r.status_code}: "
            f"{r.text[:200]}"
        )


def test_create_app_skips_mcp_when_disabled() -> None:
    from backend.main import create_app

    app = create_app(_settings_with_mcp(SWIFT_MCP_SERVER_ENABLED=False))

    with TestClient(app) as client:
        r = client.get("/mcp/")
        # No mount → FastAPI 404.
        assert r.status_code == 404

        r = client.get("/config")
        assert r.status_code == 200
        assert r.json()["mcp_server_enabled"] is False


def test_create_app_enforces_bearer_on_mcp_paths() -> None:
    token = "swift-test-token"
    app = create_app_with_token(token)

    with TestClient(app) as client:
        # No token → 401 on /mcp/*.
        r = client.get("/mcp/")
        assert r.status_code == 401
        assert r.json()["error"] == "unauthorized"

        # Wrong token → 401.
        r = client.get("/mcp/", headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401

        # Correct token → passes through to FastMCP (not 401).
        r = client.get("/mcp/", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code != 401

        # Meta routes stay open regardless.
        r = client.get("/health")
        assert r.status_code == 200


def create_app_with_token(token: str):
    from backend.main import create_app

    settings = _settings_with_mcp(SWIFT_MCP_SERVER_BEARER_TOKEN=token)
    return create_app(settings)


def test_create_app_config_surface_advertises_bearer_requirement() -> None:
    from backend.main import create_app

    app = create_app(_settings_with_mcp(SWIFT_MCP_SERVER_BEARER_TOKEN="t"))

    with TestClient(app) as client:
        snap = client.get("/config").json()
        assert snap["mcp_server_bearer_required"] is True


# ─── Opt-in live end-to-end ──────────────────────────────────────────


@pytest.mark.skipif(
    not LIVE, reason="Set OPENROUTER_LIVE=1 to hit OpenRouter."
)
def test_mcp_write_article_live() -> None:
    """Drive the full pipeline through the MCP tool dispatcher.

    This is the only live test in the file; it runs the Orchestrator,
    the Writer, the Evaluator, and the Image Agent through the same
    code path Claude Desktop / Cursor would exercise. MCP servers for
    the Evaluator are left at settings default (fetch enabled; Serper
    iff SERPER_API_KEY is present) to reflect realistic deployments.
    """

    from backend.agents.providers import configure_openrouter

    configure_openrouter()
    mcp = build_mcp_server()

    result = asyncio.run(
        mcp.call_tool(
            "write_article",
            {
                "topic": "Why small language models are having a moment",
                "tone": "conversational",
                "length": "short",
                "keywords": ["efficiency", "on-device"],
                "audience": "software engineers",
                "max_retries": 1,
            },
        )
    )

    structured = result.structured_content
    assert structured is not None, (
        "FastMCP must serialise the returned FinalArticle as structured_content"
    )
    final = FinalArticle.model_validate(structured)

    # MCP-layer contract: tool dispatched, pipeline ran, result
    # round-tripped through Pydantic. We deliberately do NOT assert on
    # the Writer's prompt adherence here (visual content, image counts,
    # etc.) — that's the Writer live tests' job. Duplicating those
    # assertions makes this test flake when the Writer has a bad run
    # without exercising any MCP code path differently.
    assert final.title.strip()
    assert final.body_markdown.strip()
    # The FinalArticle invariant: image_placeholder_count matches
    # images list length (produced by the Image Agent, not the LLM).
    # This DOES verify the Orchestrator → Image Agent handoff
    # actually happened inside the tool.
    assert final.image_placeholder_count == len(final.images)


# Silence unused-import lint for tests that consume structured JSON via
# model_validate — pyright/mypy can see the usage, but linting tools
# that only scan top-level names sometimes miss it.
_ = json

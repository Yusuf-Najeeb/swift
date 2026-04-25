"""Tests for the Writer agent (Steps 2 + 2b).

The default suite runs entirely offline — it introspects the ``Agent``
object produced by :func:`build_writer_agent` and asserts on the
instruction contract, schema, and MCP wiring. For real end-to-end checks
set ``OPENROUTER_LIVE=1`` (and provide a real ``OPENROUTER_API_KEY``) to
run the gated smoke tests at the bottom of this file. One smoke test
additionally requires ``uvx`` on PATH so it can spawn ``mcp-server-fetch``.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import Any

import pytest
from agents.mcp import MCPServerStdio, MCPServerStreamableHttp
from pydantic import ValidationError

from backend.agents.mcp_clients import (
    DEFAULT_WRITER_FETCH_SPEC,
    build_writer_mcp_servers,
    writer_mcp_specs,
)
from backend.agents.providers import _build_client
from backend.agents.schemas import ArticleBrief, WriterOutput
from backend.agents.writer import (
    WRITER_AGENT_NAME,
    WRITER_INSTRUCTIONS,
    build_writer_agent,
)
from backend.config import MCPServerSpec, Settings


@pytest.fixture(autouse=True)
def _reset_client_cache() -> None:
    _build_client.cache_clear()
    yield
    _build_client.cache_clear()


# ─── Offline unit tests — core agent shape ─────────────────────────────


def test_build_writer_agent_has_expected_identity() -> None:
    agent = build_writer_agent(mcp_servers=[])

    assert agent.name == WRITER_AGENT_NAME == "Writer"
    assert agent.instructions == WRITER_INSTRUCTIONS


def test_build_writer_agent_uses_configured_writer_model() -> None:
    agent = build_writer_agent(mcp_servers=[])

    assert getattr(agent.model, "model", None) == "openai/gpt-4o-mini"


def test_build_writer_agent_respects_settings_override() -> None:
    custom = Settings(
        _env_file=None,  # type: ignore[call-arg]
        OPENROUTER_API_KEY="sk-test-dummy",
        SWIFT_WRITER_MODEL="x-ai/grok-4",
    )

    agent = build_writer_agent(settings=custom, mcp_servers=[])

    assert getattr(agent.model, "model", None) == "x-ai/grok-4"


def test_build_writer_agent_wires_structured_output() -> None:
    agent = build_writer_agent(mcp_servers=[])

    assert agent.output_type is WriterOutput


def test_writer_instructions_encode_contract() -> None:
    # These are the non-negotiable contract points the Orchestrator and
    # downstream Image Agent rely on. If the prompt ever loses any of
    # them, this test flags it immediately.
    contract_fragments = [
        "WriterOutput",
        "body_markdown",
        "image_placeholder_count",
        "[IMAGE:",
        "brief",
        "feedback",
        "self-check",
    ]
    lowered = WRITER_INSTRUCTIONS.lower()
    for fragment in contract_fragments:
        assert fragment.lower() in lowered, f"Missing contract fragment: {fragment!r}"


def test_writer_output_rejects_negative_placeholder_count() -> None:
    with pytest.raises(ValidationError):
        WriterOutput(
            title="t",
            summary="s",
            body_markdown="body",
            image_placeholder_count=-1,
        )


def test_article_brief_defaults() -> None:
    brief = ArticleBrief(topic="The rise of edge inference")

    assert brief.tone == "professional"
    assert brief.length == "medium"
    assert brief.keywords == []
    assert brief.audience is None


# ─── Offline unit tests — MCP consumer wiring (Step 2b) ────────────────


def _settings_with_mcp(
    *,
    fetch: bool = True,
    extra: list[dict[str, Any]] | None = None,
) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        OPENROUTER_API_KEY="sk-test-dummy",
        SWIFT_WRITER_MCP_FETCH_ENABLED=fetch,
        SWIFT_WRITER_MCP_SERVERS=extra or [],
    )


def test_writer_instructions_mention_mcp_tooling() -> None:
    lowered = WRITER_INSTRUCTIONS.lower()
    for fragment in ["mcp", "fetch", "source]("]:
        assert fragment in lowered, f"Missing MCP guidance fragment: {fragment!r}"


def test_writer_instructions_teach_mermaid_vs_image_split() -> None:
    """Regression guard: the Writer must be explicitly told when to use
    Mermaid diagrams vs [IMAGE: ...] markers, and must be warned that
    image generators can't render flowcharts. Losing any of these
    fragments silently regresses output quality for technical articles."""

    lowered = WRITER_INSTRUCTIONS.lower()
    contract = [
        "mermaid",
        "flowchart",
        "sequencediagram",
        "style hint",
        "editorial illustration",
    ]
    for fragment in contract:
        assert fragment in lowered, f"Missing visual-content fragment: {fragment!r}"


def test_writer_mcp_specs_defaults_to_fetch() -> None:
    specs = writer_mcp_specs(_settings_with_mcp())

    assert len(specs) == 1
    assert specs[0] == DEFAULT_WRITER_FETCH_SPEC
    assert specs[0].name == "fetch"
    assert specs[0].transport == "stdio"


def test_writer_mcp_specs_fetch_can_be_disabled() -> None:
    specs = writer_mcp_specs(_settings_with_mcp(fetch=False))

    assert specs == []


def test_writer_mcp_specs_includes_user_declared_extras() -> None:
    extra = [
        {
            "name": "example",
            "transport": "http",
            "url": "https://mcp.example.com/",
            "headers": {"Authorization": "Bearer abc"},
        }
    ]
    specs = writer_mcp_specs(_settings_with_mcp(extra=extra))

    assert [s.name for s in specs] == ["fetch", "example"]
    assert specs[1].transport == "http"
    assert specs[1].url == "https://mcp.example.com/"


def test_writer_mcp_servers_env_var_parses_json(monkeypatch: pytest.MonkeyPatch) -> None:
    # Building Settings with `_env_file=None` bypasses .env, so set
    # the required API key explicitly — LIVE mode's conftest doesn't
    # inject the dummy.
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-dummy")
    monkeypatch.setenv(
        "SWIFT_WRITER_MCP_SERVERS",
        json.dumps(
            [
                {
                    "name": "search",
                    "transport": "stdio",
                    "command": "uvx",
                    "args": ["some-mcp-server"],
                }
            ]
        ),
    )

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.writer_mcp_servers[0].name == "search"
    assert settings.writer_mcp_servers[0].command == "uvx"


def test_build_writer_mcp_servers_returns_sdk_instances() -> None:
    servers = build_writer_mcp_servers(
        _settings_with_mcp(
            extra=[
                {
                    "name": "http-example",
                    "transport": "http",
                    "url": "https://mcp.example.com/",
                }
            ]
        )
    )

    assert len(servers) == 2
    assert isinstance(servers[0], MCPServerStdio)
    assert servers[0].name == "fetch"
    assert isinstance(servers[1], MCPServerStreamableHttp)


def test_mcp_server_spec_stdio_requires_command() -> None:
    from backend.agents.mcp_clients import build_mcp_server

    with pytest.raises(ValueError, match="command"):
        build_mcp_server(MCPServerSpec(name="broken", transport="stdio"))


def test_build_writer_agent_attaches_default_mcp_servers() -> None:
    agent = build_writer_agent(settings=_settings_with_mcp())

    assert len(agent.mcp_servers) == 1
    assert agent.mcp_servers[0].name == "fetch"


def test_build_writer_agent_mcp_override_wins() -> None:
    agent = build_writer_agent(settings=_settings_with_mcp(), mcp_servers=[])

    assert agent.mcp_servers == []


# ─── Live smoke tests (opt-in) ─────────────────────────────────────────

LIVE = os.getenv("OPENROUTER_LIVE") == "1"
UVX_AVAILABLE = shutil.which("uvx") is not None


def _short_brief() -> ArticleBrief:
    return ArticleBrief(
        topic="Why small language models are having a moment",
        tone="conversational",
        length="short",
        keywords=["efficiency", "on-device"],
        audience="software engineers",
    )


def _assert_writer_output_is_structurally_valid(output: Any) -> None:
    """Minimum viable check: the SDK coerced into a ``WriterOutput``
    and the agent produced non-empty prose. Used by the MCP-enabled
    live variant, where the contract is "the tool-use integration
    didn't crash the run", not "the prompt was followed perfectly"."""

    assert isinstance(output, WriterOutput)
    assert output.title.strip()
    assert output.body_markdown.strip()


def _assert_valid_writer_output(output: Any) -> None:
    _assert_writer_output_is_structurally_valid(output)

    # The Writer has two valid visual primitives (see WRITER_INSTRUCTIONS
    # — "VISUAL CONTENT — TWO DISTINCT TOOLS"): ``[IMAGE: ...]`` markers
    # for editorial illustration and ``` ```mermaid ``` `` fenced blocks
    # for diagrams. For technical topics the Writer legitimately prefers
    # the latter and may emit zero ``[IMAGE: ...]`` markers. The contract
    # we actually want to enforce is "at least one visual of *some* kind
    # appeared" — that's what we assert here.
    #
    # We deliberately don't demand ``image_placeholder_count`` match the
    # actual scan: small LLMs periodically miscount by 1, and the
    # downstream Image Agent recomputes from the body directly, so the
    # field is observability-only. The strict equality check lives in
    # the unit tests, where we control the fixture.
    actual_image_markers = output.body_markdown.count("[IMAGE:")
    actual_mermaid_fences = output.body_markdown.count("```mermaid")
    assert actual_image_markers + actual_mermaid_fences >= 1, (
        "Writer emitted neither [IMAGE: ...] markers nor mermaid "
        "diagrams — every article should have at least one visual."
    )
    # ``image_placeholder_count`` must match what we found in the body
    # *in kind*: if the Writer put images in, it must claim at least
    # one; if it went Mermaid-only, zero is a valid count.
    assert output.image_placeholder_count >= (
        1 if actual_image_markers else 0
    )


@pytest.mark.skipif(not LIVE, reason="Set OPENROUTER_LIVE=1 to hit OpenRouter.")
def test_writer_generates_draft_live() -> None:
    """Opt-in end-to-end check against OpenRouter — pure LLM, no MCP.

    We pass ``mcp_servers=[]`` so the Writer runs without any MCP
    subprocesses. That keeps this test fast and side-effect free; the
    companion MCP-enabled variant below covers the tool-use path.
    """

    from backend.agents.providers import configure_openrouter
    from agents import Runner

    configure_openrouter()
    agent = build_writer_agent(mcp_servers=[])

    payload: dict[str, Any] = {"brief": _short_brief().model_dump(), "feedback": None}

    result = Runner.run_sync(agent, input=json.dumps(payload))
    _assert_valid_writer_output(result.final_output)


@pytest.mark.skipif(
    not (LIVE and UVX_AVAILABLE),
    reason="Requires OPENROUTER_LIVE=1 and `uvx` on PATH.",
)
def test_writer_generates_draft_with_fetch_mcp_live() -> None:
    """Opt-in end-to-end check with the default fetch MCP attached.

    Spawns ``mcp-server-fetch`` via ``uvx`` for the duration of one run.
    We don't require the writer to actually call fetch — the point is
    that the MCP handshake completes and the agent still produces a
    valid ``WriterOutput`` when tools are advertised. The brief
    explicitly gives it a URL so it has a reason to reach for fetch.
    """

    from backend.agents.providers import configure_openrouter
    from agents import Runner

    configure_openrouter()

    brief = ArticleBrief(
        topic="What is Model Context Protocol and why should engineers care?",
        tone="professional",
        length="short",
        keywords=["MCP", "tools"],
        audience="software engineers",
        extra_notes="Background reading: https://modelcontextprotocol.io/",
    )
    payload: dict[str, Any] = {"brief": brief.model_dump(), "feedback": None}

    async def _run() -> Any:
        servers = build_writer_mcp_servers()
        assert servers, "expected at least the default fetch server"
        # Connect every server, run the agent, then clean up.
        for server in servers:
            await server.connect()
        try:
            agent = build_writer_agent(mcp_servers=servers)
            result = await Runner.run(agent, input=json.dumps(payload))
            return result.final_output
        finally:
            for server in servers:
                await server.cleanup()

    output = asyncio.run(_run())
    # Use the weaker assertion here: this test's contract is strictly
    # "the MCP handshake worked and the agent returned a valid
    # ``WriterOutput``". Prompt-level guarantees (visual content, etc.)
    # are covered by the vanilla live test above; here the agent is
    # juggling tool-use and may legitimately skip nice-to-have rules.
    _assert_writer_output_is_structurally_valid(output)

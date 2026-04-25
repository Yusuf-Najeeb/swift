"""Tests for the Evaluator agent (Step 3).

The offline suite asserts on the agent's identity, model slug,
structured output wiring, MCP client wiring, and the contract fragments
baked into the system prompt. Two ``OPENROUTER_LIVE``-gated smoke tests
exercise the real Evaluator — one without MCP (pure LLM rubric) and one
with the default fetch MCP attached so we cover the evidence-gathering
path end-to-end.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import Any, Optional

import pytest
from agents.mcp import MCPServerStdio, MCPServerStreamableHttp
from pydantic import ValidationError

from backend.agents.evaluator import (
    APPROVAL_THRESHOLD,
    EVALUATOR_AGENT_NAME,
    EVALUATOR_INSTRUCTIONS,
    build_evaluator_agent,
)
from backend.agents.mcp_clients import (
    DEFAULT_EVALUATOR_FETCH_SPEC,
    SERPER_MCP_PACKAGE,
    build_evaluator_mcp_servers,
    build_serper_spec,
    evaluator_mcp_specs,
)
from backend.agents.providers import _build_client
from backend.agents.schemas import ArticleBrief, EvaluatorFeedback, WriterOutput
from backend.config import MCPServerSpec, Settings


@pytest.fixture(autouse=True)
def _reset_client_cache() -> None:
    _build_client.cache_clear()
    yield
    _build_client.cache_clear()


# ─── Offline unit tests ────────────────────────────────────────────────


def test_build_evaluator_agent_has_expected_identity() -> None:
    agent = build_evaluator_agent(mcp_servers=[])

    assert agent.name == EVALUATOR_AGENT_NAME == "Evaluator"
    assert agent.instructions == EVALUATOR_INSTRUCTIONS


def test_build_evaluator_agent_uses_configured_evaluator_model() -> None:
    # Default bumped from llama-3.1-8b to gpt-4o when the Evaluator
    # gained MCP tool access — small models proved unreliable at both
    # structured output and multi-tool reasoning.
    agent = build_evaluator_agent(mcp_servers=[])

    assert getattr(agent.model, "model", None) == "openai/gpt-4o"


def test_build_evaluator_agent_respects_settings_override() -> None:
    custom = Settings(
        _env_file=None,  # type: ignore[call-arg]
        OPENROUTER_API_KEY="sk-test-dummy",
        SWIFT_EVALUATOR_MODEL="anthropic/claude-3.5-sonnet",
    )

    agent = build_evaluator_agent(settings=custom, mcp_servers=[])

    assert getattr(agent.model, "model", None) == "anthropic/claude-3.5-sonnet"


def test_build_evaluator_agent_wires_structured_output() -> None:
    agent = build_evaluator_agent(mcp_servers=[])

    assert agent.output_type is EvaluatorFeedback


def test_evaluator_instructions_encode_contract() -> None:
    lowered = EVALUATOR_INSTRUCTIONS.lower()
    for fragment in [
        "evaluatorfeedback",
        "never rewrite",
        "strengths",
        "weaknesses",
        "suggestions",
        "approved",
        "rubric",
        "[image:",
        "brief",
        "draft",
        str(APPROVAL_THRESHOLD),
    ]:
        assert fragment in lowered, f"Missing contract fragment: {fragment!r}"


def test_evaluator_instructions_mention_mcp_evidence_gathering() -> None:
    # The Evaluator gained MCP access so it could fact-check the Writer
    # against cited URLs. If this section disappears from the prompt,
    # the agent will either ignore the tools or misuse them — both
    # regressions worth catching here.
    lowered = EVALUATOR_INSTRUCTIONS.lower()
    for fragment in ["mcp", "fetch", "fact-check", "budget"]:
        assert fragment in lowered, f"Missing evidence-gathering fragment: {fragment!r}"


def test_approval_threshold_is_seven_matches_setup_plan() -> None:
    # The article-writer setup plan hard-codes 'Loops until Evaluator
    # score >= 7 or max 3 retries' — make that constant explicit and
    # pin it so a future refactor can't silently drift.
    assert APPROVAL_THRESHOLD == 7


def test_evaluator_instructions_force_approved_equals_score_gte_threshold() -> None:
    # The orchestrator relies on approved and score being redundant —
    # no "approved=false with score>=7" escape hatch. Changing the
    # prompt to weaken this invariant should fail this test.
    lowered = EVALUATOR_INSTRUCTIONS.lower()
    assert "approved" in lowered and "redundant" in lowered
    assert "must equal" in lowered


def test_evaluator_feedback_enforces_score_bounds() -> None:
    # Upper bound
    with pytest.raises(ValidationError):
        EvaluatorFeedback(score=11, strengths=[], weaknesses=[], suggestions=[], approved=True)
    # Lower bound
    with pytest.raises(ValidationError):
        EvaluatorFeedback(score=-1, strengths=[], weaknesses=[], suggestions=[], approved=False)
    # Edges OK (approved is coerced — see separate test)
    for score in (0, APPROVAL_THRESHOLD, 10):
        EvaluatorFeedback(
            score=score, strengths=[], weaknesses=[], suggestions=[], approved=score >= APPROVAL_THRESHOLD
        )


def test_evaluator_feedback_coerces_approved_from_score() -> None:
    # Small evaluator models periodically set `approved` inconsistently
    # with `score`. The model validator makes this impossible downstream:
    # no matter what the LLM emits, the final object has
    # approved == (score >= APPROVAL_THRESHOLD).
    mismatched_low = EvaluatorFeedback(
        score=5,
        strengths=[], weaknesses=[], suggestions=[],
        approved=True,  # LLM lied (approved a bad draft)
    )
    assert mismatched_low.approved is False

    mismatched_high = EvaluatorFeedback(
        score=8,
        strengths=[], weaknesses=[], suggestions=[],
        approved=False,  # LLM lied (rejected a good draft)
    )
    assert mismatched_high.approved is True

    # At exactly the threshold, approved must be True.
    at_threshold = EvaluatorFeedback(
        score=APPROVAL_THRESHOLD,
        strengths=[], weaknesses=[], suggestions=[],
        approved=False,
    )
    assert at_threshold.approved is True


# ─── Offline unit tests — MCP consumer wiring ─────────────────────────


def _settings_with_mcp(
    *,
    fetch: bool = True,
    serper: bool = True,
    serper_key: Optional[str] = None,
    extra: list[dict[str, Any]] | None = None,
) -> Settings:
    """Build a Settings instance with the MCP-relevant knobs in one place.

    Default: fetch on, serper flag on, no Serper key → only fetch
    should resolve. Pass ``serper_key="..."`` to flip Serper on.
    """

    kwargs: dict[str, Any] = {
        "_env_file": None,
        "OPENROUTER_API_KEY": "sk-test-dummy",
        "SWIFT_EVALUATOR_MCP_FETCH_ENABLED": fetch,
        "SWIFT_EVALUATOR_MCP_SERPER_ENABLED": serper,
        "SWIFT_EVALUATOR_MCP_SERVERS": extra or [],
    }
    if serper_key is not None:
        kwargs["SERPER_API_KEY"] = serper_key
    return Settings(**kwargs)  # type: ignore[call-arg]


def test_evaluator_mcp_specs_defaults_to_fetch_only_without_serper_key() -> None:
    # No SERPER_API_KEY → Serper is silently skipped even though the
    # flag defaults to True. Users without an account must not see a
    # broken npx subprocess at startup.
    specs = evaluator_mcp_specs(_settings_with_mcp())

    assert len(specs) == 1
    assert specs[0] == DEFAULT_EVALUATOR_FETCH_SPEC
    assert specs[0].name == "fetch"
    assert specs[0].transport == "stdio"


def test_evaluator_mcp_specs_fetch_can_be_disabled() -> None:
    specs = evaluator_mcp_specs(_settings_with_mcp(fetch=False))

    assert specs == []


def test_evaluator_mcp_specs_includes_serper_when_key_and_flag_are_set() -> None:
    specs = evaluator_mcp_specs(_settings_with_mcp(serper_key="sk-serper-dummy"))

    assert [s.name for s in specs] == ["fetch", "serper"]
    serper = specs[1]
    assert serper.transport == "stdio"
    assert serper.command == "npx"
    assert SERPER_MCP_PACKAGE in serper.args
    # Key is injected into the subprocess env, not leaked via argv.
    assert serper.env == {
        "SERPER_API_KEY": "sk-serper-dummy",
        "NPM_CONFIG_PROGRESS": "false",
    }
    assert "sk-serper-dummy" not in " ".join(serper.args)


def test_evaluator_mcp_specs_skips_serper_when_flag_disabled() -> None:
    # Explicit opt-out wins over key presence — useful when a user
    # has a key in their shell for other tools but doesn't want
    # Swift using it.
    specs = evaluator_mcp_specs(
        _settings_with_mcp(serper=False, serper_key="sk-serper-dummy")
    )

    assert [s.name for s in specs] == ["fetch"]


def test_evaluator_mcp_specs_stacks_serper_before_user_extras() -> None:
    # Ordering matters: fetch → Serper → user extras. Agents SDK
    # presents tools in attach order, which biases the model's
    # selection; we want the zero-config research tools first.
    specs = evaluator_mcp_specs(
        _settings_with_mcp(
            serper_key="sk-serper-dummy",
            extra=[
                {
                    "name": "tavily",
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "tavily-mcp"],
                }
            ],
        )
    )

    assert [s.name for s in specs] == ["fetch", "serper", "tavily"]


def test_build_serper_spec_embeds_key_in_subprocess_env() -> None:
    # Regression guard: if someone refactors this to pass the key as
    # a CLI arg, it'd leak into ps(1) / process listings.
    spec = build_serper_spec("sk-test-12345")

    assert spec.env["SERPER_API_KEY"] == "sk-test-12345"
    assert all("sk-test-12345" not in arg for arg in spec.args)


def test_evaluator_mcp_specs_includes_user_declared_extras() -> None:
    # Typical usage: user stacks a search MCP (Brave/Tavily) on top of
    # the default fetch server for deeper verification.
    extra = [
        {
            "name": "brave",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-brave-search"],
            "env": {"BRAVE_API_KEY": "dummy"},
        }
    ]
    specs = evaluator_mcp_specs(_settings_with_mcp(extra=extra))

    assert [s.name for s in specs] == ["fetch", "brave"]
    assert specs[1].transport == "stdio"
    assert specs[1].command == "npx"


def test_evaluator_mcp_servers_env_var_parses_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Building Settings with `_env_file=None` bypasses .env, so set
    # the required API key explicitly — LIVE mode's conftest doesn't
    # inject the dummy.
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-dummy")
    monkeypatch.setenv(
        "SWIFT_EVALUATOR_MCP_SERVERS",
        json.dumps(
            [
                {
                    "name": "tavily",
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "tavily-mcp"],
                }
            ]
        ),
    )

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.evaluator_mcp_servers[0].name == "tavily"
    assert settings.evaluator_mcp_servers[0].command == "npx"


def test_build_evaluator_mcp_servers_returns_sdk_instances() -> None:
    servers = build_evaluator_mcp_servers(
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


def test_build_evaluator_agent_attaches_default_mcp_servers() -> None:
    agent = build_evaluator_agent(settings=_settings_with_mcp())

    assert len(agent.mcp_servers) == 1
    assert agent.mcp_servers[0].name == "fetch"


def test_build_evaluator_agent_mcp_override_wins() -> None:
    # Passing an explicit (even empty) list must bypass settings —
    # otherwise unit tests would keep spawning real subprocesses.
    agent = build_evaluator_agent(settings=_settings_with_mcp(), mcp_servers=[])

    assert agent.mcp_servers == []


def test_evaluator_and_writer_fetch_specs_are_independent_instances() -> None:
    # We deliberately instantiate a fresh spec per agent so callers
    # can mutate one (e.g. bump its timeout) without surprising the
    # other. They should be equal-by-value but not `is`-identical.
    from backend.agents.mcp_clients import DEFAULT_WRITER_FETCH_SPEC

    assert DEFAULT_EVALUATOR_FETCH_SPEC == DEFAULT_WRITER_FETCH_SPEC
    assert DEFAULT_EVALUATOR_FETCH_SPEC is not DEFAULT_WRITER_FETCH_SPEC


# ─── Live smoke tests (opt-in) ─────────────────────────────────────────

LIVE = os.getenv("OPENROUTER_LIVE") == "1"
UVX_AVAILABLE = shutil.which("uvx") is not None
NPX_AVAILABLE = shutil.which("npx") is not None


def _serper_key_available() -> bool:
    # The key usually lives in `.env` rather than the shell env, so we
    # consult Settings (which reads .env via pydantic-settings) instead
    # of os.getenv. Swallow errors so test collection doesn't fail on
    # a misconfigured environment — the gate simply evaluates False.
    try:
        from backend.config import get_settings

        return bool(get_settings().serper_api_key)
    except Exception:
        return False


SERPER_KEY_AVAILABLE = _serper_key_available()

_SAMPLE_BRIEF = ArticleBrief(
    topic="Why small language models are having a moment",
    tone="conversational",
    length="short",
    keywords=["efficiency", "on-device"],
    audience="software engineers",
)

_SAMPLE_DRAFT = WriterOutput(
    title="The Rise of Small Language Models",
    summary=(
        "Discover how small language models are revolutionizing "
        "efficiency and on-device processing in software engineering."
    ),
    body_markdown=(
        "# The Rise of Small Language Models\n\n"
        "In the rapidly evolving field of AI, small language models are "
        "having a moment. Their unique combination of efficiency and "
        "on-device capabilities makes them appealing to software "
        "engineers.\n\n"
        "## What Are Small Language Models?\n\n"
        "Compact AI systems that process text without needing the "
        "compute budget of a frontier model.\n\n"
        "[IMAGE: a smartphone running a local language model inference engine]\n\n"
        "## The Efficiency Factor\n\n"
        "Small models are faster, cheaper to host, and responsive on "
        "commodity hardware — critical for chatbots and interactive "
        "tooling.\n\n"
        "## On-Device Capabilities\n\n"
        "Running locally improves privacy and lets apps work offline. "
        "Personalisation can happen without sending user data anywhere.\n\n"
        "[IMAGE: an engineer deploying an LLM to a laptop CPU]\n\n"
        "## Conclusion\n\n"
        "For software engineers chasing both performance and privacy, "
        "small language models deserve a serious look.\n"
    ),
    image_placeholder_count=2,
)


def _assert_valid_feedback(feedback: Any) -> None:
    assert isinstance(feedback, EvaluatorFeedback)
    assert 0 <= feedback.score <= 10
    # Model validator on the schema guarantees this, but assert
    # explicitly so a regression there doesn't quietly pass here.
    assert feedback.approved == (feedback.score >= APPROVAL_THRESHOLD), (
        f"approved={feedback.approved} disagrees with score={feedback.score} "
        f"(threshold={APPROVAL_THRESHOLD})."
    )
    # The Evaluator should always articulate *something* substantive —
    # at minimum the strengths that justify the score. Suggestions are
    # not strictly required: a cleanly approved draft can legitimately
    # leave the suggestions list empty. We saw gpt-4o produce exactly
    # that (score=7, weaknesses=[], suggestions=[]) in CI, and failing
    # on it was overly strict.
    assert feedback.strengths or feedback.weaknesses or feedback.suggestions, (
        "Evaluator returned no strengths, weaknesses, or suggestions — "
        "the rubric requires at least one category to be populated."
    )


@pytest.mark.skipif(not LIVE, reason="Set OPENROUTER_LIVE=1 to hit OpenRouter.")
def test_evaluator_scores_draft_live() -> None:
    """Run a real Evaluator pass on a hand-written sample draft.

    Pure LLM path — no MCP. Structural checks only; the MCP-enabled
    companion below covers the tool-use path.
    """

    from agents import Runner

    from backend.agents.providers import configure_openrouter

    configure_openrouter()
    agent = build_evaluator_agent(mcp_servers=[])

    payload: dict[str, Any] = {
        "brief": _SAMPLE_BRIEF.model_dump(),
        "draft": _SAMPLE_DRAFT.model_dump(),
    }
    result = Runner.run_sync(agent, input=json.dumps(payload))
    _assert_valid_feedback(result.final_output)


@pytest.mark.skipif(
    not (LIVE and UVX_AVAILABLE),
    reason="Requires OPENROUTER_LIVE=1 and `uvx` on PATH.",
)
def test_evaluator_scores_draft_with_fetch_mcp_live() -> None:
    """Opt-in end-to-end check with the default fetch MCP attached.

    Spawns ``mcp-server-fetch`` via ``uvx`` for the duration of one
    run. We don't *require* the Evaluator to actually call fetch — the
    point is that the MCP handshake completes and the agent still
    produces a well-formed ``EvaluatorFeedback`` when tools are
    advertised. We rig the sample draft with a citation URL so it has
    a reason to reach for fetch.
    """

    from agents import Runner

    from backend.agents.providers import configure_openrouter

    configure_openrouter()

    # Draft with a real citation link — gives the Evaluator a concrete
    # target for the fetch tool.
    draft_with_citation = WriterOutput(
        title=_SAMPLE_DRAFT.title,
        summary=_SAMPLE_DRAFT.summary,
        body_markdown=(
            _SAMPLE_DRAFT.body_markdown
            + "\n\n[source](https://modelcontextprotocol.io/)\n"
        ),
        image_placeholder_count=_SAMPLE_DRAFT.image_placeholder_count,
    )

    payload: dict[str, Any] = {
        "brief": _SAMPLE_BRIEF.model_dump(),
        "draft": draft_with_citation.model_dump(),
    }

    # Scope this test to fetch-only, even if the ambient environment
    # has SERPER_API_KEY set. Otherwise build_evaluator_mcp_servers()
    # would also spin up the Serper subprocess and turn this into a
    # two-MCP test — which the companion test below covers explicitly.
    from backend.config import get_settings

    fetch_only_settings = get_settings().model_copy(
        update={"evaluator_mcp_serper_enabled": False}
    )

    async def _run() -> Any:
        servers = build_evaluator_mcp_servers(fetch_only_settings)
        assert [s.name for s in servers] == ["fetch"], (
            f"fetch-only test spawned unexpected servers: {[s.name for s in servers]}"
        )
        for server in servers:
            await server.connect()
        try:
            agent = build_evaluator_agent(
                settings=fetch_only_settings, mcp_servers=servers
            )
            result = await Runner.run(agent, input=json.dumps(payload))
            return result.final_output
        finally:
            for server in servers:
                await server.cleanup()

    _assert_valid_feedback(asyncio.run(_run()))


@pytest.mark.skipif(
    not (LIVE and UVX_AVAILABLE and NPX_AVAILABLE and SERPER_KEY_AVAILABLE),
    reason=(
        "Requires OPENROUTER_LIVE=1, SERPER_API_KEY set, and both "
        "`uvx` and `npx` on PATH."
    ),
)
def test_evaluator_scores_draft_with_serper_mcp_live() -> None:
    """End-to-end check with fetch + Serper both attached.

    Exercises the full research path: the Evaluator can pick between
    fetch (known URLs) and google_search (discovery). We rig the draft
    with a deliberately fishy-sounding statistic and no citation — the
    agent may or may not reach for search, but either way the MCP
    handshake with `npx serper-search-scrape-mcp-server` must complete
    and the output must still be a well-formed ``EvaluatorFeedback``.
    """

    from agents import Runner

    from backend.agents.providers import configure_openrouter

    configure_openrouter()

    # Draft with an unverified specific claim — a juicy target for
    # google_search. We don't assert that the model calls search
    # (model behaviour varies), only that the pipeline works and
    # produces valid structured output.
    draft = WriterOutput(
        title=_SAMPLE_DRAFT.title,
        summary=_SAMPLE_DRAFT.summary,
        body_markdown=(
            _SAMPLE_DRAFT.body_markdown
            + "\n\nRecent research shows that 87% of Fortune 500 "
            "companies are actively deploying small language models "
            "on device in 2026.\n"
        ),
        image_placeholder_count=_SAMPLE_DRAFT.image_placeholder_count,
    )

    payload: dict[str, Any] = {
        "brief": _SAMPLE_BRIEF.model_dump(),
        "draft": draft.model_dump(),
    }

    async def _cleanup_best_effort(server: Any) -> None:
        # Node-based MCP servers launched via `npx` sometimes don't
        # terminate cleanly on stdin close — their cleanup coroutine
        # can hang waiting for the subprocess to exit, and asyncio
        # then cancels our task while we're inside aclose(). That
        # manifests as a CancelledError in teardown *after* the agent
        # has already returned a valid result. Shield + timeout the
        # cleanup so it can't poison an otherwise-passing test.
        try:
            await asyncio.wait_for(
                asyncio.shield(server.cleanup()), timeout=5.0
            )
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            pass

    async def _run() -> Any:
        servers = build_evaluator_mcp_servers()
        # Sanity: both fetch and serper should resolve when the key
        # is present.
        names = {s.name for s in servers}
        assert {"fetch", "serper"}.issubset(names), (
            f"expected fetch + serper in {names}"
        )
        for server in servers:
            await server.connect()
        try:
            agent = build_evaluator_agent(mcp_servers=servers)
            result = await Runner.run(agent, input=json.dumps(payload))
            return result.final_output
        finally:
            for server in servers:
                await _cleanup_best_effort(server)

    _assert_valid_feedback(asyncio.run(_run()))

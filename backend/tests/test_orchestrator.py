"""Tests for the Orchestrator (Step 4).

The offline suite drives :func:`run_revision_loop` with hand-built
fakes — so the Writer↔Evaluator coordination is exercised without
touching OpenRouter or the Agents SDK — plus a small number of tests
for the payload serializers and the ``orchestrate_article`` entry
point with ``Runner.run`` and MCP builders monkeypatched.

One ``OPENROUTER_LIVE``-gated smoke test runs the real loop end-to-end
against OpenRouter so we catch wiring regressions that the offline
fakes can't see.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, List, Optional, Tuple

import pytest

from backend.agents.orchestrator import (
    _evaluator_payload,
    _expect_evaluator_feedback,
    _expect_writer_output,
    _writer_payload,
    orchestrate_article,
    run_revision_loop,
)
from backend.agents.schemas import (
    APPROVAL_THRESHOLD,
    ArticleBrief,
    ArticleRun,
    EvaluatorFeedback,
    RevisionAttempt,
    WriterOutput,
)
from backend.config import Settings, get_settings


# ─── Fixtures / helpers ────────────────────────────────────────────────


def _brief(topic: str = "Testing the orchestrator") -> ArticleBrief:
    return ArticleBrief(
        topic=topic,
        tone="neutral",
        length="short",
        keywords=["quality", "speed"],
    )


def _draft(suffix: str = "v1") -> WriterOutput:
    return WriterOutput(
        title=f"A Draft ({suffix})",
        summary="A one-line teaser.",
        body_markdown=(
            f"# A Draft ({suffix})\n\n"
            "Body text talking about quality and speed.\n\n"
            "[IMAGE: a stopwatch sitting on a stack of documents]\n"
        ),
        image_placeholder_count=1,
    )


def _feedback(score: int, note: str = "nit") -> EvaluatorFeedback:
    # ``approved`` is coerced by the EvaluatorFeedback validator.
    return EvaluatorFeedback(
        score=score,
        strengths=["clear voice"],
        weaknesses=["could cite more sources"] if score < 10 else [],
        suggestions=[note] if note else ["polish"],
        approved=score >= APPROVAL_THRESHOLD,
    )


class _ScriptedWriter:
    """Writer stub that returns a preset list of drafts, one per call.

    Records every call's arguments so tests can assert on what the
    orchestrator actually passes in (feedback presence, previous draft,
    etc).
    """

    def __init__(self, drafts: List[WriterOutput]) -> None:
        self._drafts = list(drafts)
        self.calls: List[
            Tuple[ArticleBrief, Optional[EvaluatorFeedback], Optional[WriterOutput]]
        ] = []

    async def __call__(
        self,
        brief: ArticleBrief,
        feedback: Optional[EvaluatorFeedback],
        previous_draft: Optional[WriterOutput],
    ) -> WriterOutput:
        self.calls.append((brief, feedback, previous_draft))
        if not self._drafts:
            raise AssertionError("ScriptedWriter ran out of scripted drafts")
        return self._drafts.pop(0)


class _ScriptedEvaluator:
    """Evaluator stub that returns a preset list of feedbacks."""

    def __init__(self, feedbacks: List[EvaluatorFeedback]) -> None:
        self._feedbacks = list(feedbacks)
        self.calls: List[Tuple[ArticleBrief, WriterOutput]] = []

    async def __call__(
        self, brief: ArticleBrief, draft: WriterOutput
    ) -> EvaluatorFeedback:
        self.calls.append((brief, draft))
        if not self._feedbacks:
            raise AssertionError("ScriptedEvaluator ran out of scripted feedbacks")
        return self._feedbacks.pop(0)


# ─── run_revision_loop: core control flow ──────────────────────────────


def test_happy_path_stops_after_first_approved_draft() -> None:
    """Approved on attempt 1 → no revision, exactly one RevisionAttempt."""

    brief = _brief()
    draft = _draft("first")
    writer = _ScriptedWriter([draft])
    evaluator = _ScriptedEvaluator([_feedback(9)])

    run = asyncio.run(run_revision_loop(brief, writer, evaluator, max_retries=3))

    assert isinstance(run, ArticleRun)
    assert run.iterations == 1
    assert run.approved is True
    assert run.final_draft is draft
    assert run.final_feedback.score == 9
    assert len(writer.calls) == 1
    assert len(evaluator.calls) == 1


def test_loop_retries_with_prior_feedback_and_draft() -> None:
    """When the first attempt is rejected, the revision call receives
    both the previous draft and the previous feedback — that's the
    whole point of shipping Step 4's prompt+payload patch."""

    brief = _brief()
    first, second = _draft("first"), _draft("second")
    reject = _feedback(5, "fix the intro")
    approve = _feedback(8, "great revision")

    writer = _ScriptedWriter([first, second])
    evaluator = _ScriptedEvaluator([reject, approve])

    run = asyncio.run(run_revision_loop(brief, writer, evaluator, max_retries=3))

    assert run.iterations == 2
    assert run.approved is True
    assert run.final_draft is second
    assert run.final_feedback.score == 8

    # Initial call: no feedback, no previous draft.
    _, init_feedback, init_prev_draft = writer.calls[0]
    assert init_feedback is None
    assert init_prev_draft is None

    # Revision call: gets the prior feedback + prior draft so it can
    # edit in place rather than starting from scratch.
    _, rev_feedback, rev_prev_draft = writer.calls[1]
    assert rev_feedback is reject
    assert rev_prev_draft is first


def test_loop_gives_up_after_max_retries() -> None:
    """Every attempt rejected → len(attempts) == 1 + max_retries and
    the returned run is not approved."""

    brief = _brief()
    writer = _ScriptedWriter([_draft(f"v{i}") for i in range(4)])
    evaluator = _ScriptedEvaluator([_feedback(3) for _ in range(4)])

    run = asyncio.run(run_revision_loop(brief, writer, evaluator, max_retries=3))

    assert run.iterations == 4  # 1 initial + 3 retries
    assert run.approved is False
    assert all(a.feedback.score == 3 for a in run.attempts)
    assert all(not a.feedback.approved for a in run.attempts)


def test_max_retries_zero_runs_only_initial_draft() -> None:
    """max_retries=0 means "take one shot, report what you got" — used
    by tight-budget callers that want fast failure feedback."""

    brief = _brief()
    writer = _ScriptedWriter([_draft("solo")])
    evaluator = _ScriptedEvaluator([_feedback(4)])

    run = asyncio.run(run_revision_loop(brief, writer, evaluator, max_retries=0))

    assert run.iterations == 1
    assert run.approved is False
    assert run.final_feedback.score == 4
    assert len(writer.calls) == 1


def test_negative_max_retries_is_rejected() -> None:
    brief = _brief()
    writer = _ScriptedWriter([])
    evaluator = _ScriptedEvaluator([])

    with pytest.raises(ValueError):
        asyncio.run(run_revision_loop(brief, writer, evaluator, max_retries=-1))


def test_revision_attempts_are_numbered_one_based_and_in_order() -> None:
    """RevisionAttempt.iteration is the Orchestrator's public iteration
    counter — consumers (UI, logs) rely on it being 1-indexed and
    strictly ascending so it maps to "attempt N of M"."""

    brief = _brief()
    writer = _ScriptedWriter([_draft(f"v{i}") for i in range(3)])
    evaluator = _ScriptedEvaluator(
        [_feedback(2), _feedback(5), _feedback(9)]  # approve on 3rd
    )

    run = asyncio.run(run_revision_loop(brief, writer, evaluator, max_retries=3))

    assert [a.iteration for a in run.attempts] == [1, 2, 3]
    assert run.approved is True


# ─── Payload serializers ───────────────────────────────────────────────


def test_writer_payload_initial_pass_has_null_feedback_and_prev_draft() -> None:
    payload = json.loads(_writer_payload(_brief(), None, None))

    assert set(payload) == {"brief", "feedback", "previous_draft"}
    assert payload["feedback"] is None
    assert payload["previous_draft"] is None
    assert payload["brief"]["topic"] == "Testing the orchestrator"


def test_writer_payload_revision_pass_embeds_full_feedback_and_draft() -> None:
    """The Writer prompt expects full JSON objects (not just IDs) for
    both feedback and previous_draft, so the agent can reason about
    concrete weaknesses and edit existing prose. Verify the payload
    round-trips through model_dump cleanly."""

    fb = _feedback(5, "tighten the intro")
    prev = _draft("v1")
    payload = json.loads(_writer_payload(_brief(), fb, prev))

    assert payload["feedback"]["score"] == 5
    assert payload["feedback"]["suggestions"] == ["tighten the intro"]
    assert payload["previous_draft"]["title"] == "A Draft (v1)"
    assert (
        payload["previous_draft"]["image_placeholder_count"]
        == prev.image_placeholder_count
    )


def test_evaluator_payload_has_brief_and_draft() -> None:
    payload = json.loads(_evaluator_payload(_brief(), _draft("v1")))

    assert set(payload) == {"brief", "draft"}
    assert payload["brief"]["topic"] == "Testing the orchestrator"
    assert payload["draft"]["title"] == "A Draft (v1)"


# ─── Runtime guards ────────────────────────────────────────────────────


def test_expect_writer_output_passes_through_valid_instance() -> None:
    draft = _draft()
    assert _expect_writer_output(draft) is draft


def test_expect_writer_output_rejects_raw_string() -> None:
    with pytest.raises(TypeError, match="WriterOutput"):
        _expect_writer_output("not a draft")


def test_expect_evaluator_feedback_rejects_raw_dict() -> None:
    with pytest.raises(TypeError, match="EvaluatorFeedback"):
        _expect_evaluator_feedback({"score": 9, "approved": True})


# ─── orchestrate_article: settings plumbing ────────────────────────────


class _FakeMCPServer:
    """Minimal stand-in for ``agents.mcp.MCPServer`` used in tests to
    verify that ``orchestrate_article`` runs the full connect/cleanup
    lifecycle. We only implement the two methods the orchestrator
    calls plus a ``.name`` attribute for log messages."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.connected = False
        self.cleaned_up = False

    async def connect(self) -> None:
        self.connected = True

    async def cleanup(self) -> None:
        self.cleaned_up = True


def _patch_orchestrator_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    writer_drafts: List[WriterOutput],
    evaluator_feedbacks: List[EvaluatorFeedback],
    writer_servers: Optional[List[_FakeMCPServer]] = None,
    evaluator_servers: Optional[List[_FakeMCPServer]] = None,
) -> dict[str, Any]:
    """Swap out the three moving parts of orchestrate_article so it can
    run without a network, a real agent, or a real subprocess.

    Returns a dict of handles the test can inspect.
    """

    from backend.agents import orchestrator as orch

    writer_servers = writer_servers or []
    evaluator_servers = evaluator_servers or []
    all_servers = [*writer_servers, *evaluator_servers]

    monkeypatch.setattr(
        orch, "build_writer_mcp_servers", lambda settings=None: writer_servers
    )
    monkeypatch.setattr(
        orch, "build_evaluator_mcp_servers", lambda settings=None: evaluator_servers
    )

    # Build agent placeholders — we don't need real Agent instances
    # because Runner.run is also patched.
    monkeypatch.setattr(
        orch, "build_writer_agent", lambda settings=None, mcp_servers=None: "writer-agent"
    )
    monkeypatch.setattr(
        orch,
        "build_evaluator_agent",
        lambda settings=None, mcp_servers=None: "evaluator-agent",
    )

    drafts = list(writer_drafts)
    feedbacks = list(evaluator_feedbacks)

    class _FakeRunnerResult:
        def __init__(self, final_output: Any) -> None:
            self.final_output = final_output

    async def _fake_run(agent: Any, input: str) -> _FakeRunnerResult:
        if agent == "writer-agent":
            assert drafts, "ran out of scripted writer drafts"
            return _FakeRunnerResult(drafts.pop(0))
        if agent == "evaluator-agent":
            assert feedbacks, "ran out of scripted evaluator feedbacks"
            return _FakeRunnerResult(feedbacks.pop(0))
        raise AssertionError(f"unexpected agent: {agent!r}")

    # ``Runner`` is imported by orchestrator at module level — patch the
    # symbol the orchestrator holds, not the one in the SDK package.
    class _FakeRunner:
        run = staticmethod(_fake_run)

    monkeypatch.setattr(orch, "Runner", _FakeRunner)

    return {"servers": all_servers, "drafts_remaining": drafts}


def test_orchestrate_article_defaults_to_settings_max_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the caller doesn't specify, the orchestrator should use the
    configured ``orchestrator_max_retries`` — so an operator can tune
    the loop length via env var without code changes."""

    monkeypatch.setenv("SWIFT_ORCHESTRATOR_MAX_RETRIES", "1")

    handles = _patch_orchestrator_runtime(
        monkeypatch,
        writer_drafts=[_draft("a"), _draft("b")],
        evaluator_feedbacks=[_feedback(3), _feedback(4)],
    )

    run = asyncio.run(orchestrate_article(_brief()))

    assert run.iterations == 2  # 1 initial + 1 retry (from env)
    assert run.approved is False
    assert handles["drafts_remaining"] == []


def test_orchestrate_article_explicit_max_retries_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SWIFT_ORCHESTRATOR_MAX_RETRIES", "3")

    _patch_orchestrator_runtime(
        monkeypatch,
        writer_drafts=[_draft("only")],
        evaluator_feedbacks=[_feedback(2)],
    )

    run = asyncio.run(orchestrate_article(_brief(), max_retries=0))

    assert run.iterations == 1  # explicit override beats settings


def test_orchestrate_article_connects_and_cleans_up_mcp_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer_servers = [_FakeMCPServer("writer-fetch")]
    evaluator_servers = [
        _FakeMCPServer("evaluator-fetch"),
        _FakeMCPServer("evaluator-serper"),
    ]

    _patch_orchestrator_runtime(
        monkeypatch,
        writer_drafts=[_draft("a")],
        evaluator_feedbacks=[_feedback(9)],
        writer_servers=writer_servers,
        evaluator_servers=evaluator_servers,
    )

    run = asyncio.run(orchestrate_article(_brief(), max_retries=0))

    assert run.approved is True
    for server in writer_servers + evaluator_servers:
        assert server.connected, f"{server.name} was never connected"
        assert server.cleaned_up, f"{server.name} was never cleaned up"


def test_orchestrate_article_cleans_up_mcp_servers_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup must run in the ``finally`` — otherwise a Writer crash
    in the middle of a run would leak ``uvx``/``npx`` subprocesses."""

    writer_servers = [_FakeMCPServer("writer-fetch")]
    evaluator_servers = [_FakeMCPServer("evaluator-fetch")]

    _patch_orchestrator_runtime(
        monkeypatch,
        writer_drafts=[],  # empty → Runner fake will raise AssertionError
        evaluator_feedbacks=[],
        writer_servers=writer_servers,
        evaluator_servers=evaluator_servers,
    )

    with pytest.raises(AssertionError):
        asyncio.run(orchestrate_article(_brief(), max_retries=0))

    for server in writer_servers + evaluator_servers:
        assert server.cleaned_up, (
            f"{server.name} leaked — cleanup did not run in finally"
        )


# ─── Settings default ──────────────────────────────────────────────────


def test_settings_default_orchestrator_max_retries_is_three(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scaffolding plan fixes this at 3; changing it needs a
    deliberate code change, not a drifted env default."""

    monkeypatch.delenv("SWIFT_ORCHESTRATOR_MAX_RETRIES", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-dummy")

    s = Settings(_env_file=None)
    assert s.orchestrator_max_retries == 3


# ─── Live smoke test (opt-in) ──────────────────────────────────────────

LIVE = os.getenv("OPENROUTER_LIVE") == "1"


@pytest.mark.skipif(not LIVE, reason="Set OPENROUTER_LIVE=1 to hit OpenRouter.")
def test_orchestrate_article_live_end_to_end() -> None:
    """Full-path smoke test: real Writer, real Evaluator, real
    OpenRouter.

    We disable MCP servers because each adds tens of seconds of cold
    start and isn't part of the orchestration contract — the MCP
    plumbing is covered by the Writer/Evaluator test suites. We also
    set ``max_retries=1`` to keep wall-clock bounded: if the first
    draft doesn't hit 7 we still get a revision pass exercising the
    previous_draft/feedback flow, but we never spend more than two
    full Writer+Evaluator calls.
    """

    from backend.agents.providers import configure_openrouter

    configure_openrouter()

    # Copy settings with all MCP servers disabled for speed + isolation.
    base_settings = get_settings()
    fast_settings = base_settings.model_copy(
        update={
            "writer_mcp_fetch_enabled": False,
            "writer_mcp_servers": [],
            "evaluator_mcp_fetch_enabled": False,
            "evaluator_mcp_serper_enabled": False,
            "evaluator_mcp_servers": [],
        }
    )

    brief = ArticleBrief(
        topic="Why small language models are having a moment",
        tone="conversational",
        length="short",
        keywords=["efficiency", "on-device"],
        audience="software engineers",
    )

    run = asyncio.run(
        orchestrate_article(brief, settings=fast_settings, max_retries=1)
    )

    assert isinstance(run, ArticleRun)
    assert 1 <= run.iterations <= 2
    assert run.attempts, "orchestrator must always produce at least one attempt"

    # Structural checks on every attempt so a bad revision pass doesn't
    # sneak through just because the final one was fine.
    for attempt in run.attempts:
        assert isinstance(attempt.draft, WriterOutput)
        assert isinstance(attempt.feedback, EvaluatorFeedback)
        assert 0 <= attempt.feedback.score <= 10
        assert attempt.feedback.approved == (
            attempt.feedback.score >= APPROVAL_THRESHOLD
        )
        # Writer has two valid visual primitives — ``[IMAGE: ...]``
        # markers and ``` ```mermaid ``` `` fenced blocks. Technical
        # topics often warrant Mermaid-only output, so we assert that
        # *some* kind of visual cue is present rather than insisting
        # on image placeholders specifically.
        body = attempt.draft.body_markdown
        assert (body.count("[IMAGE:") + body.count("```mermaid")) >= 1

    # Approval isn't guaranteed on a short budget, but if we hit the
    # threshold the final attempt must reflect it.
    if run.approved:
        assert run.final_feedback.score >= APPROVAL_THRESHOLD

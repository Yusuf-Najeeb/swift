"""Tests for pipeline events (Step 7 - backend/agents/events.py).

Covers three concerns:

1. The PipelineEvent schema - types, discriminators, payload shapes.
2. Orchestrator event emission - correct events, correct order, one
   per transition, happy path + retry path + approve-immediately.
3. Image Agent event emission - ``images.started`` fires with an
   accurate placeholder count, ``images.completed`` fires with
   correct image/diagram totals.

No live agents. Event tests are pure control-flow assertions -
they must not flake with LLM non-determinism.
"""

from __future__ import annotations

import asyncio
from typing import List, Optional, Tuple

import pytest
from pydantic import TypeAdapter, ValidationError

from backend.agents.events import (
    AttemptStartedEvent,
    EvaluatorCompletedEvent,
    ImagesCompletedEvent,
    ImagesStartedEvent,
    PipelineEvent,
    RunCompletedEvent,
    RunFailedEvent,
    RunStartedEvent,
    WriterCompletedEvent,
    estimate_word_count,
    resolve_callback,
)
from backend.agents.image_agent import illustrate_article
from backend.agents.orchestrator import run_revision_loop
from backend.agents.schemas import (
    APPROVAL_THRESHOLD,
    ArticleBrief,
    EvaluatorFeedback,
    FinalArticle,
    WriterOutput,
)


def _brief() -> ArticleBrief:
    return ArticleBrief(
        topic="Event emission tests",
        tone="neutral",
        length="short",
        keywords=["events"],
    )


def _draft(suffix: str = "v1", *, claimed_count: int = 1) -> WriterOutput:
    return WriterOutput(
        title=f"Draft {suffix}",
        summary="teaser",
        body_markdown=(
            f"# Draft {suffix}\n\n"
            "Body text.\n\n"
            "[IMAGE: a diagram of something]\n"
        ),
        image_placeholder_count=claimed_count,
    )


def _feedback(score: int) -> EvaluatorFeedback:
    return EvaluatorFeedback(
        score=score,
        strengths=["clear"],
        weaknesses=[] if score >= APPROVAL_THRESHOLD else ["thin"],
        suggestions=["polish"],
        approved=score >= APPROVAL_THRESHOLD,
    )


class _Recorder:
    """Async callback that records every event in order."""

    def __init__(self) -> None:
        self.events: List[PipelineEvent] = []

    async def __call__(self, event: PipelineEvent) -> None:
        self.events.append(event)

    @property
    def types(self) -> List[str]:
        return [e.type for e in self.events]


def _make_writer(drafts: List[WriterOutput], calls: List):
    iterator = iter(drafts)

    async def _run(
        _: ArticleBrief,
        fb: Optional[EvaluatorFeedback],
        prev: Optional[WriterOutput],
    ) -> WriterOutput:
        calls.append((fb, prev))
        return next(iterator)

    return _run


def _make_evaluator(feedbacks: List[EvaluatorFeedback]):
    iterator = iter(feedbacks)

    async def _run(
        _: ArticleBrief, __: WriterOutput
    ) -> EvaluatorFeedback:
        return next(iterator)

    return _run


# ─── Schema ────────────────────────────────────────────────────────────


def test_each_event_type_has_distinct_literal_discriminator() -> None:
    """All events carry a unique ``type`` tag so the union is truly
    discriminated; a frontend can branch on ``event.type`` without
    any ambiguity."""

    values = [
        RunStartedEvent(brief=_brief(), max_retries=3).type,
        AttemptStartedEvent(iteration=1).type,
        WriterCompletedEvent(
            iteration=1,
            title="t",
            word_count=10,
            image_placeholder_count=0,
        ).type,
        EvaluatorCompletedEvent(iteration=1, score=8, approved=True).type,
        ImagesStartedEvent(placeholder_count=0).type,
        ImagesCompletedEvent(image_count=0, diagram_count=0).type,
        RunFailedEvent(error="boom", error_type="RuntimeError").type,
    ]
    assert len(values) == len(set(values))
    assert set(values) == {
        "run.started",
        "attempt.started",
        "writer.completed",
        "evaluator.completed",
        "images.started",
        "images.completed",
        "run.failed",
    }


def test_pipeline_event_union_round_trips_via_type_adapter() -> None:
    """Serialising and re-parsing any event through the PipelineEvent
    union must preserve the concrete subclass. This is what lets the
    SSE stream survive JSON serialisation."""

    adapter = TypeAdapter(PipelineEvent)
    original = AttemptStartedEvent(iteration=2)
    blob = original.model_dump_json()
    parsed = adapter.validate_json(blob)
    assert isinstance(parsed, AttemptStartedEvent)
    assert parsed.iteration == 2
    assert parsed.type == "attempt.started"


def test_run_started_event_rejects_negative_retries() -> None:
    with pytest.raises(ValidationError):
        RunStartedEvent(brief=_brief(), max_retries=-1)


def test_evaluator_event_clamps_score_to_rubric_range() -> None:
    """Scores outside 0-10 don't make sense given the rubric; the
    schema rejects them so broken Evaluator output surfaces loudly."""

    with pytest.raises(ValidationError):
        EvaluatorCompletedEvent(iteration=1, score=11, approved=True)
    with pytest.raises(ValidationError):
        EvaluatorCompletedEvent(iteration=1, score=-1, approved=False)


def test_run_completed_event_embeds_final_article() -> None:
    """The final event carries the whole article payload; the stream
    consumer never has to make a follow-up request to get it."""

    final = FinalArticle(
        title="Done",
        summary="S",
        body_markdown="# Done\n",
        images=[],
        diagrams=[],
        image_placeholder_count=0,
    )
    event = RunCompletedEvent(article=final, iterations=2, approved=True)

    assert event.type == "run.completed"
    assert event.article.title == "Done"
    assert event.iterations == 2


def test_events_have_timezone_aware_timestamps() -> None:
    event = AttemptStartedEvent(iteration=1)
    assert event.timestamp.tzinfo is not None


def test_resolve_callback_returns_noop_for_none() -> None:
    """Callers on the emission hot path must be able to call the
    returned callback unconditionally - no ``if cb is None`` branch."""

    cb = resolve_callback(None)
    asyncio.run(cb(AttemptStartedEvent(iteration=1)))


def test_resolve_callback_returns_the_same_callable_when_provided() -> None:
    async def _cb(_: PipelineEvent) -> None:
        return None

    assert resolve_callback(_cb) is _cb


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", 0),
        ("one", 1),
        ("one two three", 3),
        ("  spaced   out\n\nwords ", 3),
    ],
)
def test_estimate_word_count_is_whitespace_split(text: str, expected: int) -> None:
    assert estimate_word_count(text) == expected


# ─── Orchestrator emission ─────────────────────────────────────────────


def test_run_revision_loop_emits_expected_events_on_approve_first_try() -> None:
    """One-attempt run: attempt.started -> writer.completed ->
    evaluator.completed. No ``run.started`` here; that's emitted by
    the outer :func:`orchestrate_article` so MCP setup can happen
    first."""

    recorder = _Recorder()
    calls: List = []

    async def _run() -> None:
        writer = _make_writer([_draft("first")], calls)
        evaluator = _make_evaluator([_feedback(9)])
        await run_revision_loop(
            _brief(),
            writer,
            evaluator,
            max_retries=3,
            on_event=recorder,
        )

    asyncio.run(_run())

    assert recorder.types == [
        "attempt.started",
        "writer.completed",
        "evaluator.completed",
    ]
    writer_event = recorder.events[1]
    assert isinstance(writer_event, WriterCompletedEvent)
    assert writer_event.title == "Draft first"
    assert writer_event.iteration == 1


def test_run_revision_loop_emits_one_bundle_per_iteration_on_retry() -> None:
    """Three attempts (reject, reject, approve) should emit a clean
    attempt/writer/evaluator triplet per iteration, in order - no
    interleaving, no duplicates, no missing events."""

    recorder = _Recorder()
    calls: List = []

    async def _run() -> None:
        writer = _make_writer(
            [_draft("a"), _draft("b"), _draft("c")], calls
        )
        evaluator = _make_evaluator(
            [_feedback(3), _feedback(5), _feedback(9)]
        )
        await run_revision_loop(
            _brief(),
            writer,
            evaluator,
            max_retries=3,
            on_event=recorder,
        )

    asyncio.run(_run())

    assert recorder.types == [
        "attempt.started",
        "writer.completed",
        "evaluator.completed",
        "attempt.started",
        "writer.completed",
        "evaluator.completed",
        "attempt.started",
        "writer.completed",
        "evaluator.completed",
    ]
    iterations = [
        e.iteration
        for e in recorder.events
        if hasattr(e, "iteration")
    ]
    assert iterations == [1, 1, 1, 2, 2, 2, 3, 3, 3]


def test_evaluator_completed_event_mirrors_feedback_details() -> None:
    """The UI should be able to show score + strengths + weaknesses
    live without waiting for the terminal event. Assert the passthrough
    is faithful, not just the score."""

    fb = EvaluatorFeedback(
        score=4,
        strengths=["strong opener"],
        weaknesses=["flat middle", "no citations"],
        suggestions=["add a source for claim 2", "trim section 3"],
        approved=False,
    )

    recorder = _Recorder()
    calls: List = []

    async def _run() -> None:
        writer = _make_writer([_draft(), _draft("v2")], calls)
        evaluator = _make_evaluator([fb, _feedback(9)])
        await run_revision_loop(
            _brief(), writer, evaluator, max_retries=3, on_event=recorder
        )

    asyncio.run(_run())

    eval_event = next(
        e for e in recorder.events if isinstance(e, EvaluatorCompletedEvent)
    )
    assert eval_event.score == 4
    assert eval_event.approved is False
    assert eval_event.strengths == ["strong opener"]
    assert eval_event.weaknesses == ["flat middle", "no citations"]
    assert eval_event.suggestions == [
        "add a source for claim 2",
        "trim section 3",
    ]


def test_run_revision_loop_is_silent_when_no_callback_provided() -> None:
    """Default behaviour must be a perfect drop-in - no callback =
    no overhead, no observable change."""

    calls: List = []

    async def _run() -> None:
        writer = _make_writer([_draft()], calls)
        evaluator = _make_evaluator([_feedback(9)])
        await run_revision_loop(
            _brief(), writer, evaluator, max_retries=3, on_event=None
        )

    asyncio.run(_run())


# ─── Image Agent emission ──────────────────────────────────────────────


def _writer_output_with(body: str, *, claimed: int = 0) -> WriterOutput:
    return WriterOutput(
        title="t",
        summary="s",
        body_markdown=body,
        image_placeholder_count=claimed,
    )


def test_illustrate_article_emits_started_with_accurate_placeholder_count() -> None:
    """``images.started`` must reflect what we'll actually resolve so
    the UI can render a progress bar with a meaningful denominator."""

    body = (
        "Intro.\n\n"
        "[IMAGE: first shot]\n\n"
        "Body.\n\n"
        "[IMAGE: second shot]\n\n"
        "[IMAGE: third shot]\n"
    )
    recorder = _Recorder()

    asyncio.run(
        illustrate_article(
            _writer_output_with(body, claimed=3), on_event=recorder
        )
    )

    started = next(
        e for e in recorder.events if isinstance(e, ImagesStartedEvent)
    )
    assert started.placeholder_count == 3


def test_illustrate_article_emits_completed_with_image_and_diagram_counts() -> None:
    """``images.completed`` carries the authoritative counts for both
    images and diagrams - the UI can verify everything rendered."""

    body = (
        "Text.\n\n"
        "[IMAGE: one]\n\n"
        "```mermaid\nflowchart TD\n  A --> B\n```\n\n"
        "[IMAGE: two]\n\n"
        "```mermaid\nsequenceDiagram\n  A->>B: hi\n```\n"
    )
    recorder = _Recorder()

    asyncio.run(
        illustrate_article(
            _writer_output_with(body, claimed=2), on_event=recorder
        )
    )

    completed = next(
        e for e in recorder.events if isinstance(e, ImagesCompletedEvent)
    )
    assert completed.image_count == 2
    assert completed.diagram_count == 2


def test_illustrate_article_emits_started_before_completed() -> None:
    """Order matters - the UI's state machine assumes started strictly
    precedes completed."""

    recorder = _Recorder()
    asyncio.run(
        illustrate_article(
            _writer_output_with("[IMAGE: solo]\n"), on_event=recorder
        )
    )

    kinds = [type(e).__name__ for e in recorder.events]
    assert kinds.index("ImagesStartedEvent") < kinds.index(
        "ImagesCompletedEvent"
    )


def test_illustrate_article_emits_zero_counts_when_nothing_to_do() -> None:
    """No markers, no diagrams: events still fire (so the UI's
    'Illustrating...' stage doesn't hang waiting) with count=0."""

    recorder = _Recorder()
    asyncio.run(
        illustrate_article(
            _writer_output_with("Just prose, no visuals.\n"),
            on_event=recorder,
        )
    )

    started = next(
        e for e in recorder.events if isinstance(e, ImagesStartedEvent)
    )
    completed = next(
        e for e in recorder.events if isinstance(e, ImagesCompletedEvent)
    )
    assert started.placeholder_count == 0
    assert completed.image_count == 0
    assert completed.diagram_count == 0


def test_illustrate_article_without_callback_still_returns_final_article() -> None:
    """Drop-in compatibility check: no callback, no surprises."""

    final = asyncio.run(
        illustrate_article(_writer_output_with("[IMAGE: a]\n"))
    )
    assert final.image_placeholder_count == 1

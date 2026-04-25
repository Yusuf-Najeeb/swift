"""Orchestrator — coordinates the Writer ↔ Evaluator revision loop.

The Orchestrator is Swift's control plane. It takes an
:class:`~backend.agents.schemas.ArticleBrief`, repeatedly runs the
Writer to produce a draft and the Evaluator to critique it, and stops
when the Evaluator approves (``score >= APPROVAL_THRESHOLD``) or the
retry budget is exhausted.

**Design: deterministic Python, not an LLM agent.**

The scaffolding plan described the Orchestrator as a Sonnet-backed
agent using the Agents SDK's "agents-as-tools" pattern. We deliberately
implement it as a plain Python coroutine instead:

* The control flow is trivial and rubric-driven — there is no
  decision for an LLM to contribute. Writer runs. Evaluator runs.
  Approved? stop. Not approved? loop, up to a fixed cap.
* Deterministic orchestration is dramatically cheaper (one fewer
  Sonnet hop per article) and easier to reason about under failure.
* It's far more testable: the revision loop is a pure async function
  that takes two callables.

The ``orchestrator_model`` setting is still in :class:`Settings` for
future use (a potential upstream planner that decides between "write
from scratch" and "research-first"), but is unused by this module.

Two entry points:

* :func:`run_revision_loop` — pure control flow. Takes ``run_writer``
  and ``run_evaluator`` callables. Used by the offline test suite to
  drive the loop with cheap synthetic outputs.
* :func:`orchestrate_article` — production entry point. Builds fresh
  Writer and Evaluator agents, connects both MCP pools in parallel,
  invokes ``run_revision_loop``, and cleans up subprocesses on the
  way out — even on error.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, List, Optional

from agents import Runner
from agents.mcp import MCPServer

from backend.agents.evaluator import build_evaluator_agent
from backend.agents.events import (
    AttemptStartedEvent,
    EvaluatorCompletedEvent,
    EventCallback,
    RunStartedEvent,
    WriterCompletedEvent,
    estimate_word_count,
    resolve_callback,
)
from backend.agents.mcp_clients import (
    build_evaluator_mcp_servers,
    build_writer_mcp_servers,
    safe_cleanup_mcp_servers,
)
from backend.agents.schemas import (
    ArticleBrief,
    ArticleRun,
    EvaluatorFeedback,
    RevisionAttempt,
    WriterOutput,
)
from backend.agents.writer import build_writer_agent
from backend.config import Settings, get_settings

log = logging.getLogger("swift.orchestrator")

#: Callable signature for "run the Writer once". Receives the brief,
#: the previous feedback (``None`` on the initial pass), and the
#: previous draft (``None`` on the initial pass). Returns the new
#: :class:`WriterOutput`.
RunWriter = Callable[
    [ArticleBrief, Optional[EvaluatorFeedback], Optional[WriterOutput]],
    Awaitable[WriterOutput],
]

#: Callable signature for "run the Evaluator once". Receives the brief
#: and the draft to grade. Returns structured feedback.
RunEvaluator = Callable[[ArticleBrief, WriterOutput], Awaitable[EvaluatorFeedback]]


async def run_revision_loop(
    brief: ArticleBrief,
    run_writer: RunWriter,
    run_evaluator: RunEvaluator,
    *,
    max_retries: int = 3,
    on_event: Optional[EventCallback] = None,
) -> ArticleRun:
    """Drive the Writer↔Evaluator loop with injected callables.

    Parameters
    ----------
    brief:
        The user's article brief.
    run_writer:
        Callable that produces a :class:`WriterOutput` given
        ``(brief, feedback, previous_draft)``.
    run_evaluator:
        Callable that produces an :class:`EvaluatorFeedback` given
        ``(brief, draft)``.
    max_retries:
        Maximum number of revision passes AFTER the initial draft.
        A value of ``3`` means up to four total attempts. ``0`` means
        "initial draft only, no revision".
    on_event:
        Optional async callback invoked after each pipeline
        transition. See :mod:`backend.agents.events` for the event
        taxonomy. Passing ``None`` (the default) disables emission
        entirely — existing callers keep working with no observable
        behaviour change. A single callback is awaited serially per
        event, so emission is deterministic; subscribers that fan out
        to many downstream sinks should manage their own concurrency.

    Returns
    -------
    ArticleRun
        Always contains at least one :class:`RevisionAttempt`. The
        ``approved`` property is derived from the final attempt.

    Raises
    ------
    ValueError
        If ``max_retries`` is negative.
    """

    if max_retries < 0:
        raise ValueError(f"max_retries must be >= 0, got {max_retries}")

    emit = resolve_callback(on_event)

    attempts: List[RevisionAttempt] = []
    feedback: Optional[EvaluatorFeedback] = None
    draft: Optional[WriterOutput] = None
    total_attempts = max_retries + 1

    # ``run.started`` is emitted by :func:`orchestrate_article` (the
    # production entry point) so MCP setup can succeed first; emitting
    # it here would fire before any I/O, duplicating info and
    # obscuring the "agents are wired" signal the outer function
    # provides. ``run_revision_loop`` owns events that are about the
    # loop itself: attempt/writer/evaluator completions.

    for iteration in range(1, total_attempts + 1):
        log.debug(
            "revision loop iteration %d / %d (feedback=%s)",
            iteration,
            total_attempts,
            "present" if feedback else "none",
        )
        await emit(AttemptStartedEvent(iteration=iteration))

        draft = await run_writer(brief, feedback, draft)
        await emit(
            WriterCompletedEvent(
                iteration=iteration,
                title=draft.title,
                word_count=estimate_word_count(draft.body_markdown),
                image_placeholder_count=draft.image_placeholder_count,
            )
        )

        feedback = await run_evaluator(brief, draft)
        await emit(
            EvaluatorCompletedEvent(
                iteration=iteration,
                score=feedback.score,
                approved=feedback.approved,
                strengths=list(feedback.strengths),
                weaknesses=list(feedback.weaknesses),
                suggestions=list(feedback.suggestions),
            )
        )

        attempts.append(
            RevisionAttempt(iteration=iteration, draft=draft, feedback=feedback)
        )

        log.info(
            "iteration=%d score=%d approved=%s",
            iteration,
            feedback.score,
            feedback.approved,
        )

        if feedback.approved:
            break
    else:
        log.warning(
            "revision loop exhausted after %d attempts; final score=%d",
            total_attempts,
            feedback.score if feedback else -1,
        )

    return ArticleRun(brief=brief, attempts=attempts)


def _writer_payload(
    brief: ArticleBrief,
    feedback: Optional[EvaluatorFeedback],
    previous_draft: Optional[WriterOutput],
) -> str:
    """Serialize the Writer input contract documented in its prompt.

    Factored out so tests can assert on the exact JSON shape we send
    without spinning up an LLM.
    """

    payload: dict[str, Any] = {
        "brief": brief.model_dump(),
        "feedback": feedback.model_dump() if feedback else None,
        "previous_draft": (
            previous_draft.model_dump() if previous_draft else None
        ),
    }
    return json.dumps(payload)


def _evaluator_payload(brief: ArticleBrief, draft: WriterOutput) -> str:
    """Serialize the Evaluator input contract documented in its prompt."""

    payload = {"brief": brief.model_dump(), "draft": draft.model_dump()}
    return json.dumps(payload)


async def orchestrate_article(
    brief: ArticleBrief,
    *,
    settings: Optional[Settings] = None,
    max_retries: Optional[int] = None,
    on_event: Optional[EventCallback] = None,
) -> ArticleRun:
    """Run a full Writer↔Evaluator loop against live agents + MCP.

    This is the function the FastAPI route and FastMCP server (later
    steps) should call.

    Parameters
    ----------
    brief:
        Article brief.
    settings:
        Optional pre-resolved :class:`Settings`. Defaults to the
        cached :func:`get_settings` instance.
    max_retries:
        Override for ``settings.orchestrator_max_retries``. Handy in
        tests and for endpoints that want to expose retry tuning to
        the caller.

    Returns
    -------
    ArticleRun
        The final run. Inspect ``article_run.approved`` to see whether
        the loop hit the threshold or ran out of retries.

    Notes
    -----
    * MCP servers are connected in parallel (``asyncio.gather``) to
      keep cold-start latency bounded by the slowest subprocess, not
      their sum.
    * All MCP servers are cleaned up in the ``finally`` via
      :func:`safe_cleanup_mcp_servers` — even if the loop raises.
    * The Writer and Evaluator agents are built fresh per call so a
      settings change mid-process (rare, but possible in tests) is
      honored.
    """

    settings = settings or get_settings()
    if max_retries is None:
        max_retries = settings.orchestrator_max_retries

    emit = resolve_callback(on_event)

    writer_servers = build_writer_mcp_servers(settings)
    evaluator_servers = build_evaluator_mcp_servers(settings)
    all_servers: List[MCPServer] = [*writer_servers, *evaluator_servers]

    log.info(
        "orchestrate_article start: topic=%r length=%s writer_mcp=%d "
        "evaluator_mcp=%d max_retries=%d",
        brief.topic,
        brief.length,
        len(writer_servers),
        len(evaluator_servers),
        max_retries,
    )
    # ``run.started`` is emitted AFTER settings resolution but BEFORE
    # any MCP connects, because what the UI wants to show at this
    # point is "we got your brief" — not "we finished booting our
    # internal plumbing". If an MCP cold-start later fails we'll emit
    # ``run.failed`` via the outer SSE layer; the ``run.started`` we
    # sent wasn't a lie.
    await emit(
        RunStartedEvent(brief=brief, max_retries=max_retries)
    )

    try:
        if all_servers:
            await asyncio.gather(*(s.connect() for s in all_servers))

        writer_agent = build_writer_agent(
            settings=settings, mcp_servers=writer_servers
        )
        evaluator_agent = build_evaluator_agent(
            settings=settings, mcp_servers=evaluator_servers
        )

        async def _run_writer(
            brief_: ArticleBrief,
            feedback_: Optional[EvaluatorFeedback],
            previous_draft_: Optional[WriterOutput],
        ) -> WriterOutput:
            result = await Runner.run(
                writer_agent,
                input=_writer_payload(brief_, feedback_, previous_draft_),
            )
            return _expect_writer_output(result.final_output)

        async def _run_evaluator(
            brief_: ArticleBrief,
            draft_: WriterOutput,
        ) -> EvaluatorFeedback:
            result = await Runner.run(
                evaluator_agent,
                input=_evaluator_payload(brief_, draft_),
            )
            return _expect_evaluator_feedback(result.final_output)

        run = await run_revision_loop(
            brief,
            _run_writer,
            _run_evaluator,
            max_retries=max_retries,
            on_event=on_event,
        )

        log.info(
            "orchestrate_article done: iterations=%d approved=%s final_score=%d",
            run.iterations,
            run.approved,
            run.final_feedback.score,
        )
        return run
    finally:
        if all_servers:
            await safe_cleanup_mcp_servers(all_servers)


def _expect_writer_output(value: Any) -> WriterOutput:
    """Guard against the Agents SDK returning a raw string.

    In normal operation ``output_type=WriterOutput`` forces structured
    output, but if the SDK's structured-output path breaks down (rare,
    happens when a model refuses the schema) we get back a ``str``.
    Failing loudly here beats silently corrupting the run.
    """

    if isinstance(value, WriterOutput):
        return value
    raise TypeError(
        f"Writer returned {type(value).__name__}, expected WriterOutput"
    )


def _expect_evaluator_feedback(value: Any) -> EvaluatorFeedback:
    """Same guard as :func:`_expect_writer_output`, for the Evaluator."""

    if isinstance(value, EvaluatorFeedback):
        return value
    raise TypeError(
        f"Evaluator returned {type(value).__name__}, expected EvaluatorFeedback"
    )


__all__ = [
    "RunEvaluator",
    "RunWriter",
    "orchestrate_article",
    "run_revision_loop",
]

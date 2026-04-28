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

RunWriter = Callable[
    [ArticleBrief, Optional[EvaluatorFeedback], Optional[WriterOutput]],
    Awaitable[WriterOutput],
]

RunEvaluator = Callable[[ArticleBrief, WriterOutput], Awaitable[EvaluatorFeedback]]


async def run_revision_loop(
    brief: ArticleBrief,
    run_writer: RunWriter,
    run_evaluator: RunEvaluator,
    *,
    max_retries: int = 3,
    on_event: Optional[EventCallback] = None,
) -> ArticleRun:
  

    if max_retries < 0:
        raise ValueError(f"max_retries must be >= 0, got {max_retries}")

    emit = resolve_callback(on_event)

    attempts: List[RevisionAttempt] = []
    feedback: Optional[EvaluatorFeedback] = None
    draft: Optional[WriterOutput] = None
    total_attempts = max_retries + 1


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

    payload: dict[str, Any] = {
        "brief": brief.model_dump(),
        "feedback": feedback.model_dump() if feedback else None,
        "previous_draft": (
            previous_draft.model_dump() if previous_draft else None
        ),
    }
    return json.dumps(payload)


def _evaluator_payload(brief: ArticleBrief, draft: WriterOutput) -> str:

    payload = {"brief": brief.model_dump(), "draft": draft.model_dump()}
    return json.dumps(payload)


async def orchestrate_article(
    brief: ArticleBrief,
    *,
    settings: Optional[Settings] = None,
    max_retries: Optional[int] = None,
    on_event: Optional[EventCallback] = None,
) -> ArticleRun:

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

    if isinstance(value, WriterOutput):
        return value
    raise TypeError(
        f"Writer returned {type(value).__name__}, expected WriterOutput"
    )


def _expect_evaluator_feedback(value: Any) -> EvaluatorFeedback:

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

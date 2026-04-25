"""Pipeline events — the contract between agents and the SSE stream.

The Orchestrator and Image Agent emit a discrete stream of events as
they work. Each event is a typed Pydantic model so:

* the FastAPI SSE endpoint serialises it via ``model_dump_json()``
  with a stable shape browsers can parse with a few lines of code;
* the frontend can branch on ``event.type`` (a ``Literal``) and get
  narrow types on ``event.payload`` fields via TypeScript codegen
  from the JSON schema;
* tests can assert on event *sequences* rather than prose log lines
  that shift under maintenance.

Event taxonomy
--------------
The event set is deliberately minimal — one event per observable
transition the UI wants to reflect, no more:

* ``run.started``          — pipeline received the brief, kicking off
* ``attempt.started``      — iteration N begins (Writer about to run)
* ``writer.completed``     — draft produced for this iteration
* ``evaluator.completed``  — feedback produced for this draft
* ``images.started``       — Image Agent begins substitution
* ``images.completed``     — Image Agent finished
* ``run.completed``        — final :class:`FinalArticle` payload
* ``run.failed``           — unexpected error terminated the pipeline

We deliberately omit a ``writer.started`` / ``evaluator.started``
pair: ``attempt.started`` already tells the UI "the Writer is about
to work", and chaining completions implicitly announces the next
phase. Emitting paired started/completed events would double the
traffic without giving the UI any signal it can't already compute.

Callback protocol
-----------------
Stages that emit events accept an optional ``on_event`` callable with
signature :data:`EventCallback`. Passing ``None`` (the default)
disables emission completely — existing call sites keep working
unchanged, and the offline test suite doesn't pay a cost.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Awaitable, Callable, List, Literal, Optional, Union

from pydantic import BaseModel, Field

from backend.agents.schemas import ArticleBrief, FinalArticle


def _utc_now() -> datetime:
    # Factory for the ``timestamp`` field. Extracted so tests can
    # monkey-patch a deterministic clock without subclassing models.
    return datetime.now(tz=timezone.utc)


class _BaseEvent(BaseModel):
    """Shared shape: every event has a ``type`` discriminator and a
    UTC timestamp captured at construction time.

    Not part of the :data:`PipelineEvent` union itself — subclasses
    override ``type`` with a ``Literal`` so the union is properly
    discriminated by Pydantic/TypeScript.
    """

    timestamp: datetime = Field(default_factory=_utc_now)


class RunStartedEvent(_BaseEvent):
    """Pipeline is starting work on a brief."""

    type: Literal["run.started"] = "run.started"
    brief: ArticleBrief
    max_retries: int = Field(
        ...,
        ge=0,
        description="Orchestrator retry budget (total attempts = 1 + max_retries).",
    )


class AttemptStartedEvent(_BaseEvent):
    """A revision iteration is beginning. The Writer will run next."""

    type: Literal["attempt.started"] = "attempt.started"
    iteration: int = Field(..., ge=1, description="1-indexed attempt number.")


class WriterCompletedEvent(_BaseEvent):
    """Writer produced a draft for the current iteration."""

    type: Literal["writer.completed"] = "writer.completed"
    iteration: int = Field(..., ge=1)
    title: str
    word_count: int = Field(
        ...,
        ge=0,
        description="Approximate word count of ``body_markdown``.",
    )
    image_placeholder_count: int = Field(
        ...,
        ge=0,
        description="Self-reported by the Writer; authoritative count lands on run.completed.",
    )


class EvaluatorCompletedEvent(_BaseEvent):
    """Evaluator produced structured feedback."""

    type: Literal["evaluator.completed"] = "evaluator.completed"
    iteration: int = Field(..., ge=1)
    score: int = Field(..., ge=0, le=10)
    approved: bool
    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)


class ImagesStartedEvent(_BaseEvent):
    """Image Agent is beginning marker substitution."""

    type: Literal["images.started"] = "images.started"
    placeholder_count: int = Field(
        ...,
        ge=0,
        description="Number of ``[IMAGE: ...]`` markers detected in the approved draft.",
    )


class ImagesCompletedEvent(_BaseEvent):
    """Image Agent finished; images list and diagrams are ready."""

    type: Literal["images.completed"] = "images.completed"
    image_count: int = Field(..., ge=0)
    diagram_count: int = Field(..., ge=0)


class RunCompletedEvent(_BaseEvent):
    """Pipeline finished successfully; payload is the illustrated article."""

    type: Literal["run.completed"] = "run.completed"
    article: FinalArticle
    iterations: int = Field(
        ...,
        ge=1,
        description="How many revision attempts were actually run.",
    )
    approved: bool = Field(
        ...,
        description=(
            "Whether the final attempt cleared the Evaluator threshold. "
            "``False`` means we ran out of retries; the article still "
            "ships but the UI may want to warn."
        ),
    )


class RunFailedEvent(_BaseEvent):
    """Unexpected error; the pipeline did not produce an article.

    Intended as a *terminal* event — the SSE stream closes after
    emitting it. Errors recoverable by revision (low Evaluator score)
    do NOT produce this event; they're visible in
    ``evaluator.completed`` with ``approved=False``.
    """

    type: Literal["run.failed"] = "run.failed"
    error: str = Field(..., description="Human-readable message.")
    error_type: str = Field(
        ...,
        description="Python exception class name, for programmatic handling.",
    )


PipelineEvent = Union[
    RunStartedEvent,
    AttemptStartedEvent,
    WriterCompletedEvent,
    EvaluatorCompletedEvent,
    ImagesStartedEvent,
    ImagesCompletedEvent,
    RunCompletedEvent,
    RunFailedEvent,
]

#: Callable an emitter awaits for each event. Async because some
#: subscribers (an SSE queue writer, an audit sink) are themselves
#: async; a sync subscriber can always ``return asyncio.sleep(0)``.
EventCallback = Callable[[PipelineEvent], Awaitable[None]]


async def _noop_callback(_: PipelineEvent) -> None:
    """Default when no subscriber is attached. Keeps emit-site code
    paths uniform so stages never need an ``if cb is None`` branch."""


def resolve_callback(cb: Optional[EventCallback]) -> EventCallback:
    """Return ``cb`` or a no-op callback.

    Exists so every emitter can use a single ``await
    _cb(event)`` call instead of guarding every single emit with an
    ``is None`` check. Keeps event emission on the ~one-line-per-event
    budget the callers deserve."""

    return cb if cb is not None else _noop_callback


def estimate_word_count(text: str) -> int:
    """Rough word count for progress events.

    Not accurate enough for analytics — it's just whitespace-split —
    but stable and fast, which is what live UIs need. Users watching
    SSE events get a ballpark; ``FinalArticle`` is the ground truth
    when precision matters.
    """

    return len(text.split())


__all__ = [
    "AttemptStartedEvent",
    "EvaluatorCompletedEvent",
    "EventCallback",
    "ImagesCompletedEvent",
    "ImagesStartedEvent",
    "PipelineEvent",
    "RunCompletedEvent",
    "RunFailedEvent",
    "RunStartedEvent",
    "WriterCompletedEvent",
    "estimate_word_count",
    "resolve_callback",
]

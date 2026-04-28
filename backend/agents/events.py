from __future__ import annotations

from datetime import datetime, timezone
from typing import Awaitable, Callable, List, Literal, Optional, Union

from pydantic import BaseModel, Field

from backend.agents.schemas import ArticleBrief, FinalArticle
from backend.storage.schemas import SavedArticle


def _utc_now() -> datetime:
     return datetime.now(tz=timezone.utc)


class _BaseEvent(BaseModel):
    timestamp: datetime = Field(default_factory=_utc_now)


class RunStartedEvent(_BaseEvent):

    type: Literal["run.started"] = "run.started"
    brief: ArticleBrief
    max_retries: int = Field(
        ...,
        ge=0,
        description="Orchestrator retry budget (total attempts = 1 + max_retries).",
    )


class AttemptStartedEvent(_BaseEvent):

    type: Literal["attempt.started"] = "attempt.started"
    iteration: int = Field(..., ge=1, description="1-indexed attempt number.")


class WriterCompletedEvent(_BaseEvent):

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

    type: Literal["evaluator.completed"] = "evaluator.completed"
    iteration: int = Field(..., ge=1)
    score: int = Field(..., ge=0, le=10)
    approved: bool
    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)


class ImagesStartedEvent(_BaseEvent):

    type: Literal["images.started"] = "images.started"
    placeholder_count: int = Field(
        ...,
        ge=0,
        description="Number of ``[IMAGE: ...]`` markers detected in the approved draft.",
    )


class ImagesCompletedEvent(_BaseEvent):

    type: Literal["images.completed"] = "images.completed"
    image_count: int = Field(..., ge=0)
    diagram_count: int = Field(..., ge=0)


class RunCompletedEvent(_BaseEvent):

    type: Literal["run.completed"] = "run.completed"
    article: FinalArticle
    saved: Optional[SavedArticle] = Field(
        None,
        description=(
            "Optional persistence metadata when the server saved the article "
            "to local storage (Step 9)."
        ),
    )
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

EventCallback = Callable[[PipelineEvent], Awaitable[None]]


async def _noop_callback(_: PipelineEvent) -> None:
    return None


def resolve_callback(cb: Optional[EventCallback]) -> EventCallback:

    return cb if cb is not None else _noop_callback


def estimate_word_count(text: str) -> int:

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

"""Shared Pydantic schemas for Swift's agents.

These models are the contract between the Orchestrator and its sub-agents.
Keeping them in one place means the Writer, Evaluator, Image and
Orchestrator agents all reason about the same shapes — and we can feed
them straight into ``Agent(output_type=...)`` for structured decoding.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

ArticleLength = Literal["short", "medium", "long"]

#: Score at or above which the Evaluator considers a draft publishable.
#: Hard-coded into :class:`EvaluatorFeedback` as well; keep them in sync.
APPROVAL_THRESHOLD: int = 7


class ArticleBrief(BaseModel):
    """User-supplied brief that kicks off an article generation run."""

    topic: str = Field(..., description="Subject of the article.")
    tone: str = Field(
        "professional",
        description="Voice / style the writer should adopt (e.g. 'casual', 'academic').",
    )
    length: ArticleLength = Field(
        "medium",
        description="Rough target size: short (~400 words), medium (~800), long (~1500+).",
    )
    keywords: List[str] = Field(
        default_factory=list,
        description="Keywords or phrases that should appear naturally in the article.",
    )
    audience: Optional[str] = Field(
        None,
        description="Intended reader (e.g. 'backend engineers', 'product managers').",
    )
    extra_notes: Optional[str] = Field(
        None,
        description="Anything else the orchestrator wants the writer to know.",
    )


class EvaluatorFeedback(BaseModel):
    """Structured critique produced by the Evaluator agent.

    The Evaluator never rewrites the article; it only returns this
    object, and the Orchestrator decides whether to loop back to the
    Writer.

    ``approved`` is a **derived** field: whatever the LLM writes for it
    gets overwritten by ``score >= APPROVAL_THRESHOLD`` via a
    model-level validator. This is deliberate. Small evaluator models
    (Llama 3.1 8B, Mistral 7B, ...) periodically set ``approved=false``
    on drafts scoring 7+ despite being told not to, and the Orchestrator
    needs the two signals to be a redundant single source of truth. We
    still ask the LLM to fill ``approved`` in the prompt because the
    reasoning step seems to improve score calibration, but we never
    trust its answer when it conflicts with the score.
    """

    score: int = Field(..., ge=0, le=10, description="Overall quality score 0-10.")
    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    approved: bool = Field(
        ...,
        description=(
            "True when the draft is good enough to move to the image "
            "stage. Always coerced to ``score >= APPROVAL_THRESHOLD`` "
            "after construction — see class docstring."
        ),
    )

    @model_validator(mode="after")
    def _sync_approved_with_score(self) -> "EvaluatorFeedback":
        expected = self.score >= APPROVAL_THRESHOLD
        if self.approved != expected:
            object.__setattr__(self, "approved", expected)
        return self


class WriterOutput(BaseModel):
    """Structured draft returned by the Writer agent.

    ``body_markdown`` is the article in Markdown, and MUST embed image
    placeholders of the form ``[IMAGE: <short description>]`` at natural
    break points. The downstream Image Agent replaces those placeholders
    with Pollinations.ai URLs.
    """

    title: str = Field(..., description="Punchy, headline-style title.")
    summary: str = Field(
        ...,
        description="One-to-two sentence teaser suitable for a preview card.",
    )
    body_markdown: str = Field(
        ...,
        description="Full article in Markdown with [IMAGE: ...] placeholders.",
    )
    image_placeholder_count: int = Field(
        ...,
        ge=0,
        description="How many [IMAGE: ...] markers the writer embedded.",
    )


class RevisionAttempt(BaseModel):
    """One trip through the Writer → Evaluator loop.

    The Orchestrator records one of these per iteration so the full
    revision history is observable — useful for debugging bad loops,
    surfacing progress to the UI via SSE (Step 7), and analytics on
    where evaluators reject drafts.
    """

    iteration: int = Field(
        ...,
        ge=1,
        description=(
            "1-indexed attempt number. ``iteration == 1`` is the "
            "initial draft (no feedback input); subsequent values are "
            "revision passes that were fed the previous attempt's "
            "feedback."
        ),
    )
    draft: WriterOutput = Field(
        ...,
        description="What the Writer produced on this iteration.",
    )
    feedback: EvaluatorFeedback = Field(
        ...,
        description="What the Evaluator returned for this draft.",
    )


class ArticleRun(BaseModel):
    """End-to-end result of a single Writer↔Evaluator loop.

    Returned by :func:`backend.agents.orchestrator.orchestrate_article`.
    Always contains at least one ``RevisionAttempt``; the last one is
    authoritative for ``final_draft`` / ``final_feedback`` / ``approved``.

    A run is considered successful when ``approved`` is ``True`` — that
    is, the last iteration's ``feedback.approved`` fired. If every
    attempt was rejected and the orchestrator exhausted its retry
    budget, ``approved`` is ``False`` and the caller can decide what to
    do (ship the best-effort draft, escalate, fail the request, …).
    """

    brief: ArticleBrief = Field(
        ...,
        description="The brief that kicked this run off; echoed back for traceability.",
    )
    attempts: List[RevisionAttempt] = Field(
        ...,
        min_length=1,
        description=(
            "Every iteration, in order. `attempts[-1]` is always the "
            "attempt whose draft/feedback became the run's final result."
        ),
    )

    @property
    def iterations(self) -> int:
        return len(self.attempts)

    @property
    def final_draft(self) -> WriterOutput:
        return self.attempts[-1].draft

    @property
    def final_feedback(self) -> EvaluatorFeedback:
        return self.attempts[-1].feedback

    @property
    def approved(self) -> bool:
        return self.final_feedback.approved


class ImageAsset(BaseModel):
    """One Pollinations-backed image resolved from a ``[IMAGE: ...]``
    marker in the Writer's Markdown.

    We keep the original description alongside the resolved URL so
    downstream consumers (UI, analytics, regeneration) can reason
    about the prompt without re-parsing the body.
    """

    description: str = Field(
        ...,
        description=(
            "The exact text inside the Writer's ``[IMAGE: ...]`` "
            "marker, trimmed. This is the prompt that was sent to "
            "Pollinations and is a good default ``alt`` text."
        ),
    )
    url: str = Field(..., description="Pollinations URL for this image.")
    alt_text: str = Field(
        ...,
        description=(
            "Accessible alt text for the rendered image. Defaults to "
            "``description`` but kept as a separate field in case we "
            "ever want to post-process (strip style hints, etc.)."
        ),
    )


class DiagramAsset(BaseModel):
    """One code-rendered diagram lifted out of the article body.

    Diagrams — flowcharts, sequence diagrams, state machines — flow
    through Swift as ordinary fenced code blocks rather than custom
    markers, because Mermaid is already a standard Markdown-adjacent
    format that every major renderer supports. We expose them as a
    separate list on :class:`FinalArticle` purely for visibility: UI
    consumers, analytics, and future syntax validators get structured
    access to the source without having to re-parse the body.

    The ``body_markdown`` on :class:`FinalArticle` still contains the
    original fenced block — this schema is an *index*, not a
    replacement. Downstream renderers should keep using the body as
    the source of truth for layout/order.
    """

    language: str = Field(
        ...,
        description=(
            "Code-fence language tag (``mermaid`` today; the field is "
            "here so we can add ``plantuml``, ``dot``, etc. without a "
            "schema migration)."
        ),
    )
    source: str = Field(
        ...,
        description="Raw diagram source exactly as it appeared in the body.",
    )


class FinalArticle(BaseModel):
    """End-to-end result: an illustrated, publishable article.

    Produced by :func:`backend.agents.image_agent.illustrate_article`
    from an approved :class:`WriterOutput`. The ``body_markdown`` has
    every ``[IMAGE: ...]`` marker replaced by a Markdown image tag
    pointing at Pollinations, so downstream rendering is a plain
    Markdown-to-HTML step with no Swift-specific logic.
    """

    title: str = Field(..., description="Headline, echoed from the Writer output.")
    summary: str = Field(..., description="Teaser, echoed from the Writer output.")
    body_markdown: str = Field(
        ...,
        description=(
            "Full article in Markdown, with ``[IMAGE: ...]`` markers "
            "replaced by ``![alt](url)`` tags. Mermaid code fences "
            "are left untouched and rendered by the frontend."
        ),
    )
    images: List[ImageAsset] = Field(
        default_factory=list,
        description="Every image generated for this article, in document order.",
    )
    diagrams: List[DiagramAsset] = Field(
        default_factory=list,
        description=(
            "Every diagram (Mermaid code fence) found in the body, "
            "in document order. Informational only — the canonical "
            "source lives inline in ``body_markdown``."
        ),
    )
    image_placeholder_count: int = Field(
        ...,
        ge=0,
        description=(
            "Authoritative count of placeholders the Image Agent "
            "actually resolved — derived from ``images``, not "
            "inherited from the (occasionally miscounted) "
            "``WriterOutput.image_placeholder_count``."
        ),
    )


__all__ = [
    "APPROVAL_THRESHOLD",
    "ArticleBrief",
    "ArticleLength",
    "ArticleRun",
    "DiagramAsset",
    "EvaluatorFeedback",
    "FinalArticle",
    "ImageAsset",
    "RevisionAttempt",
    "WriterOutput",
]

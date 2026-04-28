from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

ArticleLength = Literal["short", "medium", "long"]

APPROVAL_THRESHOLD: int = 7


class ArticleBrief(BaseModel):

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

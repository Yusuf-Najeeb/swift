"""Tests for the SSE streaming endpoint (Step 7).

Covers:

1. Request validation — malformed bodies are 422, good bodies are 200.
2. SSE framing — the response emits ``event: <type>\\ndata: <json>``
   pairs that parse back to our :data:`PipelineEvent` union.
3. End-to-end event sequence — the pipeline's events reach the wire
   in order, and the stream terminates with ``run.completed``.
4. ``include_images=False`` skips the Image Agent but still produces
   a coherent ``run.completed`` with an empty ``images`` list.
5. Error path — an Orchestrator exception surfaces as a terminal
   ``run.failed`` event instead of blowing up the HTTP response.
6. Live end-to-end test (opt-in via OPENROUTER_LIVE=1).

The offline tests monkeypatch ``orchestrate_article`` and
``illustrate_article`` on the route module so no LLM is ever hit.
"""

from __future__ import annotations

import json
import os
from typing import Iterable, List, Optional, Tuple

import pytest
from fastapi.testclient import TestClient

from backend.agents.events import PipelineEvent
from backend.agents.schemas import (
    APPROVAL_THRESHOLD,
    ArticleBrief,
    ArticleRun,
    EvaluatorFeedback,
    FinalArticle,
    RevisionAttempt,
    WriterOutput,
)
from backend.main import create_app

LIVE = os.getenv("OPENROUTER_LIVE") == "1"


# ─── SSE parsing helper ────────────────────────────────────────────────


def _parse_sse_stream(lines: Iterable[bytes]) -> List[Tuple[str, dict]]:
    """Parse an SSE byte stream into ``(event_type, data_dict)`` tuples.

    sse-starlette emits frames as ``event: <type>\\n`` + ``data: <json>\\n``
    + ``\\n``. Keep-alive pings ride on ``:`` comment lines we discard.
    """

    events: List[Tuple[str, dict]] = []
    current_event: Optional[str] = None
    current_data: Optional[str] = None

    for raw in lines:
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw

        if line == "":
            if current_event is not None and current_data is not None:
                events.append((current_event, json.loads(current_data)))
            current_event = None
            current_data = None
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            current_event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            current_data = line[len("data:"):].strip()

    if current_event is not None and current_data is not None:
        events.append((current_event, json.loads(current_data)))

    return events


# ─── Fake pipeline outputs ─────────────────────────────────────────────


def _fake_draft(title: str = "Faked Draft") -> WriterOutput:
    return WriterOutput(
        title=title,
        summary="summary",
        body_markdown=f"# {title}\n\nBody paragraph.\n",
        image_placeholder_count=0,
    )


def _fake_feedback(score: int = 9) -> EvaluatorFeedback:
    return EvaluatorFeedback(
        score=score,
        strengths=["crisp"],
        weaknesses=[],
        suggestions=["ship it"],
        approved=score >= APPROVAL_THRESHOLD,
    )


def _fake_article() -> FinalArticle:
    return FinalArticle(
        title="Faked Draft",
        summary="summary",
        body_markdown="# Faked Draft\n\nBody paragraph.\n",
        images=[],
        diagrams=[],
        image_placeholder_count=0,
    )


async def _fake_orchestrate_article(
    brief: ArticleBrief,
    *,
    settings=None,
    max_retries=None,
    on_event=None,
) -> ArticleRun:
    """Stand-in for the real orchestrator that emits realistic events.

    Runs just enough event traffic through the callback so streaming
    tests can assert on the sequence the SSE bridge produces.
    """

    from backend.agents.events import (
        AttemptStartedEvent,
        EvaluatorCompletedEvent,
        RunStartedEvent,
        WriterCompletedEvent,
        resolve_callback,
    )

    emit = resolve_callback(on_event)
    draft = _fake_draft()
    feedback = _fake_feedback()

    await emit(
        RunStartedEvent(brief=brief, max_retries=max_retries or 0)
    )
    await emit(AttemptStartedEvent(iteration=1))
    await emit(
        WriterCompletedEvent(
            iteration=1,
            title=draft.title,
            word_count=20,
            image_placeholder_count=draft.image_placeholder_count,
        )
    )
    await emit(
        EvaluatorCompletedEvent(
            iteration=1,
            score=feedback.score,
            approved=feedback.approved,
            strengths=feedback.strengths,
            weaknesses=feedback.weaknesses,
            suggestions=feedback.suggestions,
        )
    )

    return ArticleRun(
        brief=brief,
        attempts=[RevisionAttempt(iteration=1, draft=draft, feedback=feedback)],
    )


async def _fake_illustrate_article(
    writer_output: WriterOutput,
    *,
    settings=None,
    on_event=None,
) -> FinalArticle:
    from backend.agents.events import (
        ImagesCompletedEvent,
        ImagesStartedEvent,
        resolve_callback,
    )

    emit = resolve_callback(on_event)
    await emit(ImagesStartedEvent(placeholder_count=0))
    article = _fake_article()
    await emit(
        ImagesCompletedEvent(
            image_count=len(article.images),
            diagram_count=len(article.diagrams),
        )
    )
    return article


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Build a fresh app with the pipeline monkeypatched to fakes.

    We patch the symbols the route module imported (not the ones in
    ``backend.agents.orchestrator`` etc.), because Python binds
    imports at module load — patching the source module wouldn't
    affect the already-imported references the route uses.
    """

    monkeypatch.setattr(
        "backend.api.routes.orchestrate_article",
        _fake_orchestrate_article,
    )
    monkeypatch.setattr(
        "backend.api.routes.illustrate_article",
        _fake_illustrate_article,
    )
    return TestClient(create_app())


# ─── Request validation ────────────────────────────────────────────────


def test_missing_body_returns_422(client: TestClient) -> None:
    response = client.post("/api/generate/stream")
    assert response.status_code == 422


def test_missing_brief_field_returns_422(client: TestClient) -> None:
    response = client.post(
        "/api/generate/stream", json={"max_retries": 1}
    )
    assert response.status_code == 422


def test_negative_max_retries_is_rejected(client: TestClient) -> None:
    payload = {
        "brief": {"topic": "x", "length": "short"},
        "max_retries": -1,
    }
    response = client.post("/api/generate/stream", json=payload)
    assert response.status_code == 422


# ─── SSE framing + sequence ────────────────────────────────────────────


def test_successful_run_emits_ordered_event_sequence(
    client: TestClient,
) -> None:
    """The happy path must produce, in order:
    run.started, attempt.started, writer.completed, evaluator.completed,
    images.started, images.completed, run.completed.
    """

    payload = {
        "brief": {"topic": "streaming smoke", "length": "short"},
        "max_retries": 1,
    }

    with client.stream("POST", "/api/generate/stream", json=payload) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse_stream(r.iter_lines())

    types = [evt_type for evt_type, _ in events]
    assert types == [
        "run.started",
        "attempt.started",
        "writer.completed",
        "evaluator.completed",
        "images.started",
        "images.completed",
        "run.completed",
    ]


def test_events_deserialize_back_into_pipeline_event_union(
    client: TestClient,
) -> None:
    """Wire-shape contract: every emitted event must be re-parseable
    into our PipelineEvent union. This is the guarantee Next.js
    relies on to share types with the backend."""

    from pydantic import TypeAdapter

    adapter = TypeAdapter(PipelineEvent)

    payload = {"brief": {"topic": "round trip", "length": "short"}}

    with client.stream("POST", "/api/generate/stream", json=payload) as r:
        events = _parse_sse_stream(r.iter_lines())

    assert events, "stream produced no events"
    for evt_type, data in events:
        parsed = adapter.validate_python(data)
        assert parsed.type == evt_type


def test_run_completed_carries_article_payload(client: TestClient) -> None:
    """The terminal event is the full FinalArticle — consumers don't
    need a follow-up request to fetch anything."""

    payload = {"brief": {"topic": "payload check", "length": "short"}}

    with client.stream("POST", "/api/generate/stream", json=payload) as r:
        events = _parse_sse_stream(r.iter_lines())

    evt_type, data = events[-1]
    assert evt_type == "run.completed"
    assert data["article"]["title"] == "Faked Draft"
    assert data["iterations"] == 1
    assert data["approved"] is True


# ─── include_images toggle ─────────────────────────────────────────────


def test_include_images_false_skips_image_agent_events(
    client: TestClient,
) -> None:
    """When the client opts out of images, we must NOT emit
    images.started / images.completed — otherwise the UI would show
    a spurious "Illustrating..." stage that never actually ran."""

    payload = {
        "brief": {"topic": "no pics", "length": "short"},
        "include_images": False,
    }

    with client.stream("POST", "/api/generate/stream", json=payload) as r:
        events = _parse_sse_stream(r.iter_lines())

    types = [evt_type for evt_type, _ in events]
    assert "images.started" not in types
    assert "images.completed" not in types
    assert types[-1] == "run.completed"

    final = events[-1][1]
    assert final["article"]["images"] == []


# ─── Error path ────────────────────────────────────────────────────────


def test_pipeline_exception_surfaces_as_run_failed_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the Orchestrator raises, the stream must close cleanly with
    a terminal ``run.failed`` event — not a 500, not a truncated body.
    A half-closed stream would leave the UI spinning forever."""

    async def _boom(*args, **kwargs):
        raise RuntimeError("upstream timeout")

    monkeypatch.setattr("backend.api.routes.orchestrate_article", _boom)
    monkeypatch.setattr(
        "backend.api.routes.illustrate_article", _fake_illustrate_article
    )
    client = TestClient(create_app())

    payload = {"brief": {"topic": "break things", "length": "short"}}

    with client.stream("POST", "/api/generate/stream", json=payload) as r:
        assert r.status_code == 200
        events = _parse_sse_stream(r.iter_lines())

    types = [evt_type for evt_type, _ in events]
    assert types[-1] == "run.failed"

    _, data = events[-1]
    assert data["error_type"] == "RuntimeError"
    assert "upstream timeout" in data["error"]


# ─── Endpoint discovery ────────────────────────────────────────────────


def test_endpoint_is_registered_on_app() -> None:
    """The router is mounted only via ``include_router``; guard
    against someone deleting that wire during a refactor."""

    app = create_app()
    paths = {
        r.path for r in app.routes if hasattr(r, "path")
    }
    assert "/api/generate/stream" in paths


# ─── Live end-to-end ───────────────────────────────────────────────────


@pytest.mark.skipif(not LIVE, reason="set OPENROUTER_LIVE=1 to run live test")
def test_live_stream_delivers_full_pipeline_events() -> None:
    """End-to-end: POST with a real brief, watch the actual
    Orchestrator → Image Agent pipeline hit the wire via SSE.

    Assertions are structural (the right event *types* appear in the
    right order) not semantic (we don't grade the Writer's prose) so
    LLM non-determinism can't flake us.
    """

    app = create_app()
    payload = {
        "brief": {
            "topic": "Redis vs Memcached for session storage",
            "tone": "pragmatic",
            "length": "short",
            "keywords": ["cache", "persistence"],
        },
        "max_retries": 1,
    }

    with TestClient(app) as client:
        with client.stream(
            "POST", "/api/generate/stream", json=payload
        ) as r:
            assert r.status_code == 200
            events = _parse_sse_stream(r.iter_lines())

    types = [evt_type for evt_type, _ in events]

    assert types[0] == "run.started"
    assert types[-1] in {"run.completed", "run.failed"}
    assert "attempt.started" in types
    assert "writer.completed" in types
    assert "evaluator.completed" in types

    if types[-1] == "run.completed":
        _, data = events[-1]
        assert data["article"]["title"]
        assert data["iterations"] >= 1

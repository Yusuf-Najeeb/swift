"""FastAPI routers — Step 7: SSE streaming endpoint.

Exposes ``POST /api/generate/stream``: accepts an
:class:`~backend.agents.schemas.ArticleBrief` in the request body and
streams a sequence of :data:`~backend.agents.events.PipelineEvent`
events as Server-Sent Events while the Orchestrator + Image Agent
work. The stream terminates with either ``run.completed`` (success)
or ``run.failed`` (unexpected error).

Why SSE (not WebSockets, not long-polling)?
-------------------------------------------
* The flow is strictly server → client. SSE is one-way by design;
  WebSockets would give us bidirectional messaging we'd never use.
* SSE rides on plain HTTP/1.1, so it survives every reverse proxy,
  load balancer, and corporate firewall that HTTP does. Azure
  Container Apps routes it out of the box.
* Browsers have native ``EventSource`` support, but we also document
  the fetch-stream path because :class:`EventSource` only supports
  GET; our endpoint is POST (it takes a JSON body). Next.js clients
  use fetch streaming either way.

Why POST (not GET)?
-------------------
``ArticleBrief`` is a meaningful payload: multi-line notes, keyword
lists, etc. Cramming it into a querystring would force URL encoding
and hit path-length limits. POST also makes it obvious the request
*does* something (runs agents, costs tokens), which is a useful
semantic signal to proxies that cache GETs aggressively.

Client disconnection
--------------------
``sse-starlette``'s :class:`EventSourceResponse` monitors the
underlying ``Request`` and cancels the generator when the TCP
connection closes (tab closed, network blip). That cancellation
bubbles into our pipeline as an ``asyncio.CancelledError`` — the
Orchestrator's ``finally`` block runs :func:`safe_cleanup_mcp_servers`,
releasing MCP subprocesses so we don't leak ``uvx``/``npx``
processes on user disconnects.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from backend.agents import illustrate_article, orchestrate_article
from backend.agents.events import (
    PipelineEvent,
    RunCompletedEvent,
    RunFailedEvent,
)
from backend.agents.schemas import ArticleBrief
from backend.config import Settings, get_settings

log = logging.getLogger("swift.api.routes")

router = APIRouter()


class GenerateStreamRequest(BaseModel):
    """Request body for ``POST /api/generate/stream``.

    Embeds the :class:`ArticleBrief` under ``brief`` so the payload
    shape is explicit about what's the article spec vs what's a
    pipeline knob (``max_retries``), and so adding more per-request
    options later (``include_images: false``, timeouts, ...) doesn't
    collide with the brief's own fields.
    """

    brief: ArticleBrief
    max_retries: Optional[int] = Field(
        None,
        ge=0,
        description=(
            "Optional override for ``SWIFT_ORCHESTRATOR_MAX_RETRIES``. "
            "``None`` uses the server-side default."
        ),
    )
    include_images: bool = Field(
        True,
        description=(
            "When false, skip the Image Agent and return the approved "
            "Markdown unchanged (still including any Mermaid fenced "
            "blocks). Useful for clients that want just the prose."
        ),
    )


async def _pipeline_events(
    payload: GenerateStreamRequest,
    settings: Settings,
) -> AsyncIterator[PipelineEvent]:
    """Run the pipeline and yield events as they're emitted.

    The Orchestrator and Image Agent emit events via an async
    callback (:data:`~backend.agents.events.EventCallback`). To turn
    that push-style API into a pull-style iterator we bridge through
    an :class:`asyncio.Queue` and run the pipeline as a background
    task. The iterator drains the queue while the task runs and then
    emits one terminal event (``run.completed`` or ``run.failed``)
    after the task completes.

    Events the pipeline emits itself (``run.started``,
    ``attempt.started``, ``writer.completed``, ``evaluator.completed``,
    ``images.started``, ``images.completed``) go through the queue
    unchanged. The final ``run.completed`` / ``run.failed`` event is
    synthesised here because the pipeline functions return the
    :class:`~backend.agents.schemas.FinalArticle` as a return value
    rather than via the event stream — and we want the UI to see the
    article payload exactly once, as the last event.
    """

    queue: asyncio.Queue[PipelineEvent] = asyncio.Queue()

    async def _emit(event: PipelineEvent) -> None:
        await queue.put(event)

    async def _run_pipeline() -> PipelineEvent:
        run = await orchestrate_article(
            payload.brief,
            settings=settings,
            max_retries=payload.max_retries,
            on_event=_emit,
        )
        draft = run.final_draft

        if payload.include_images:
            final = await illustrate_article(
                draft, settings=settings, on_event=_emit
            )
        else:
            # Synthesise a minimal FinalArticle so the completed event
            # still has a consistent shape; images=[], diagrams= whatever
            # the body happens to contain.
            from backend.agents.image_agent import extract_diagrams
            from backend.agents.schemas import FinalArticle

            final = FinalArticle(
                title=draft.title,
                summary=draft.summary,
                body_markdown=draft.body_markdown,
                images=[],
                diagrams=extract_diagrams(draft.body_markdown),
                image_placeholder_count=0,
            )

        return RunCompletedEvent(
            article=final,
            iterations=run.iterations,
            approved=run.approved,
        )

    task = asyncio.create_task(_run_pipeline())

    try:
        while True:
            # Race the queue against the task so we both (a) flush
            # every mid-run event the pipeline pushed and (b) stop
            # promptly when the pipeline finishes. ``asyncio.wait``
            # with FIRST_COMPLETED gives us that branching without
            # needing a separate sentinel on the queue.
            queue_getter = asyncio.create_task(queue.get())
            done, pending = await asyncio.wait(
                {queue_getter, task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if queue_getter in done:
                yield queue_getter.result()
                # Continue draining — the task may still be running
                # and pushing more events.
            else:
                # The pipeline task completed before the queue got
                # anything new. Cancel the pending queue getter so we
                # don't leak a dangling task, then drain whatever the
                # pipeline pushed synchronously on its way out.
                queue_getter.cancel()
                try:
                    await queue_getter
                except (asyncio.CancelledError, BaseException):
                    pass

                while not queue.empty():
                    yield queue.get_nowait()

                # Surface the task's outcome as the terminal event.
                try:
                    yield task.result()
                except asyncio.CancelledError:
                    # Client disconnected while pipeline was running
                    # and the task got cancelled externally. No
                    # terminal event — the socket is already gone.
                    raise
                except Exception as exc:  # noqa: BLE001
                    log.exception(
                        "pipeline failed mid-stream: %s", exc
                    )
                    yield RunFailedEvent(
                        error=str(exc) or exc.__class__.__name__,
                        error_type=exc.__class__.__name__,
                    )
                return
    except asyncio.CancelledError:
        # Re-raise so sse-starlette's outer machinery can cancel the
        # pipeline task and close the HTTP response cleanly.
        if not task.done():
            task.cancel()
            # Best-effort wait: the Orchestrator's ``finally`` block
            # handles MCP cleanup, but we shouldn't block indefinitely
            # here if cleanup hangs — the ASGI server will force-close
            # the connection anyway.
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        raise


@router.post(
    "/api/generate/stream",
    tags=["streaming"],
    summary="Run the article pipeline and stream progress via SSE.",
    response_class=EventSourceResponse,
)
async def generate_stream(
    payload: GenerateStreamRequest,
    settings: Settings = Depends(get_settings),
) -> EventSourceResponse:
    """Stream one article generation run as Server-Sent Events.

    Each event in the stream has ``event: <type>`` and ``data: <json>``
    fields. Consumers can branch on ``<type>`` (see
    :data:`~backend.agents.events.PipelineEvent`) to route payloads
    to UI components without parsing the body first.
    """

    async def _event_stream() -> AsyncIterator[dict]:
        async for event in _pipeline_events(payload, settings):
            yield {
                "event": event.type,
                "data": event.model_dump_json(),
            }

    return EventSourceResponse(
        _event_stream(),
        # Browsers drop idle SSE connections after ~30s of silence; a
        # keep-alive comment every ``sse_keep_alive_seconds`` keeps
        # the connection warm during long Writer calls.
        ping=int(settings.sse_keep_alive_seconds),
    )


__all__ = ["GenerateStreamRequest", "generate_stream", "router"]

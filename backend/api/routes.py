
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Depends
from fastapi import HTTPException
from fastapi.responses import FileResponse, Response
from pathlib import Path
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
from backend.dependencies import require_api_bearer
from backend.storage.file_manager import (
    article_storage_is_azure,
    get_articles_dir,
    list_saved_articles,
    read_article_bytes,
    save_final_article,
)
from backend.storage.schemas import ArticleListItem

log = logging.getLogger("swift.api.routes")

router = APIRouter(dependencies=[Depends(require_api_bearer)])


class GenerateStreamRequest(BaseModel):

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

        saved = save_final_article(
            final,
            brief=payload.brief,
            approved=run.approved,
            iterations=run.iterations,
            settings=settings,
        )

        return RunCompletedEvent(
            article=final,
            saved=saved,
            iterations=run.iterations,
            approved=run.approved,
        )

    task = asyncio.create_task(_run_pipeline())

    try:
        while True:
            queue_getter = asyncio.create_task(queue.get())
            done, pending = await asyncio.wait(
                {queue_getter, task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if queue_getter in done:
                yield queue_getter.result()
            else:
                queue_getter.cancel()
                try:
                    await queue_getter
                except (asyncio.CancelledError, BaseException):
                    pass

                while not queue.empty():
                    yield queue.get_nowait()

                try:
                    yield task.result()
                except asyncio.CancelledError:
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
        if not task.done():
            task.cancel()
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

    async def _event_stream() -> AsyncIterator[dict]:
        async for event in _pipeline_events(payload, settings):
            yield {
                "event": event.type,
                "data": event.model_dump_json(),
            }

    return EventSourceResponse(
        _event_stream(),
        ping=int(settings.sse_keep_alive_seconds),
    )


class ArticleListResponse(BaseModel):

    articles: list[ArticleListItem] = Field(default_factory=list)


@router.get(
    "/api/articles",
    tags=["articles"],
    response_model=ArticleListResponse,
    summary="List saved article Markdown files.",
)
async def list_articles(
    settings: Settings = Depends(get_settings),
) -> ArticleListResponse:
    return ArticleListResponse(articles=list_saved_articles(settings))


def _safe_article_filename(value: str) -> str:
    if not value or value.strip() != value:
        raise HTTPException(status_code=400, detail="Invalid filename")
    p = Path(value)
    if p.name != value:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if ".." in p.parts:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return value


@router.get(
    "/api/articles/{filename}",
    tags=["articles"],
    response_model=None,
    summary="Download a saved article Markdown file.",
)
async def get_article_markdown(
    filename: str,
    settings: Settings = Depends(get_settings),
) -> FileResponse | Response:
    filename = _safe_article_filename(filename)
    if article_storage_is_azure(settings):
        try:
            body = read_article_bytes(settings, filename)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Article not found")
        return Response(
            content=body,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
            },
        )
    articles_dir = get_articles_dir(settings)
    path = (articles_dir / filename).resolve()
    try:
        base = articles_dir.resolve()
    except FileNotFoundError:
        base = articles_dir

    if base not in path.parents and path != base:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Article not found")

    return FileResponse(
        path=str(path),
        media_type="text/markdown; charset=utf-8",
        filename=filename,
    )


__all__ = [
    "ArticleListResponse",
    "GenerateStreamRequest",
    "generate_stream",
    "router",
]

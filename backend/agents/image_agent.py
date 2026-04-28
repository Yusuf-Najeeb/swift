from __future__ import annotations

import logging
import re
from typing import List, Optional
from urllib.parse import quote, urlencode

from backend.agents.events import (
    EventCallback,
    ImagesCompletedEvent,
    ImagesStartedEvent,
    resolve_callback,
)
from backend.agents.schemas import (
    DiagramAsset,
    FinalArticle,
    ImageAsset,
    WriterOutput,
)
from backend.config import Settings, get_settings

log = logging.getLogger("swift.image_agent")

_MARKER_RE = re.compile(r"\[IMAGE:\s*([^\]]+?)\s*\]")

_DIAGRAM_LANGS = ("mermaid",)
_DIAGRAM_RE = re.compile(
    r"^```(" + "|".join(_DIAGRAM_LANGS) + r")\s*\n"
    r"(.*?)"
    r"\n^```\s*$",
    re.DOTALL | re.MULTILINE,
)


def extract_diagrams(body_markdown: str) -> List[DiagramAsset]:

    assets: List[DiagramAsset] = []
    for match in _DIAGRAM_RE.finditer(body_markdown):
        language = match.group(1)
        source = match.group(2)
        if not source.strip():
            log.warning(
                "skipping empty %s fenced block at position %d",
                language,
                match.start(),
            )
            continue
        assets.append(DiagramAsset(language=language, source=source))
    return assets


def build_image_url(description: str, settings: Optional[Settings] = None) -> str:
    
    settings = settings or get_settings()

    cleaned = description.strip()
    if not cleaned:
        raise ValueError("image description must not be empty")

    base = settings.pollinations_base_url.rstrip("/")

    path_prompt = quote(cleaned, safe="")

    params: list[tuple[str, str]] = [
        ("model", settings.pollinations_model),
        ("width", str(settings.pollinations_width)),
        ("height", str(settings.pollinations_height)),
    ]
    if settings.pollinations_enhance:
        params.append(("enhance", "true"))
    if settings.pollinations_nologo:
        params.append(("nologo", "true"))
    if settings.pollinations_seed is not None:
        params.append(("seed", str(settings.pollinations_seed)))
    if settings.pollinations_referrer:
        params.append(("referrer", settings.pollinations_referrer))

    return f"{base}/{path_prompt}?{urlencode(params)}"


def _markdown_image(alt_text: str, url: str) -> str:
    safe_alt = alt_text.replace("\\", "\\\\").replace("]", "\\]")
    return f"![{safe_alt}]({url})"


async def illustrate_article(
    writer_output: WriterOutput,
    *,
    settings: Optional[Settings] = None,
    on_event: Optional[EventCallback] = None,
) -> FinalArticle:
   

    settings = settings or get_settings()
    emit = resolve_callback(on_event)

    detected = len(_MARKER_RE.findall(writer_output.body_markdown))
    await emit(ImagesStartedEvent(placeholder_count=detected))

    images: List[ImageAsset] = []

    def _substitute(match: re.Match[str]) -> str:
        raw_description = match.group(1).strip()
        if not raw_description:
            log.warning(
                "dropping empty [IMAGE: ] marker at position %d", match.start()
            )
            return ""

        try:
            url = build_image_url(raw_description, settings=settings)
        except ValueError:
            log.warning(
                "skipping invalid image marker: %r", raw_description
            )
            return ""

        asset = ImageAsset(
            description=raw_description,
            url=url,
            alt_text=raw_description,
        )
        images.append(asset)
        return _markdown_image(asset.alt_text, asset.url)

    resolved_body = _MARKER_RE.sub(_substitute, writer_output.body_markdown)
    diagrams = extract_diagrams(resolved_body)

    if writer_output.image_placeholder_count != len(images):
        log.warning(
            "writer self-reported %d image placeholders, but %d were "
            "actually resolved (title=%r)",
            writer_output.image_placeholder_count,
            len(images),
            writer_output.title,
        )
    else:
        log.info(
            "resolved %d image placeholders for %r",
            len(images),
            writer_output.title,
        )

    if diagrams:
        log.info(
            "found %d diagram block(s) for %r (languages: %s)",
            len(diagrams),
            writer_output.title,
            ", ".join(sorted({d.language for d in diagrams})),
        )

    await emit(
        ImagesCompletedEvent(
            image_count=len(images),
            diagram_count=len(diagrams),
        )
    )

    return FinalArticle(
        title=writer_output.title,
        summary=writer_output.summary,
        body_markdown=resolved_body,
        images=images,
        diagrams=diagrams,
        image_placeholder_count=len(images),
    )


__all__ = [
    "build_image_url",
    "extract_diagrams",
    "illustrate_article",
]

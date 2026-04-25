"""Image Agent — swaps ``[IMAGE: ...]`` markers for Pollinations URLs.

Pipeline position: runs *after* the Orchestrator has produced an
approved :class:`WriterOutput`. Takes that draft, finds every
``[IMAGE: short description]`` placeholder the Writer embedded,
builds a Pollinations.ai URL for each, and returns a
:class:`FinalArticle` with those markers substituted for Markdown
image tags.

**Design: deterministic Python, not an LLM agent.**

Same reasoning as the Orchestrator (see
``backend/agents/orchestrator.py``): there's no language task here.
The work is regex extraction plus URL encoding — giving that to an
LLM would be an expensive, flaky implementation of something Python
does perfectly for free. Pollinations also offers server-side prompt
enhancement via ``?enhance=true``, so "make the prompt more vivid"
is a setting, not a job.

No HTTP calls happen here. Pollinations generates images on GET, so
the URLs we build are consumable directly by any ``<img src=...>``
tag. That keeps this stage zero-latency on Swift's side and lets us
defer all the actual rendering cost to the user's browser.

Module is called ``image_agent`` to line up with the step-numbered
scaffolding plan; the implementation is just a couple of pure
functions plus one thin async entry point.
"""

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

#: Regex for ``[IMAGE: description]`` placeholders. Case-sensitive by
#: design — the Writer prompt dictates uppercase ``IMAGE`` so the
#: marker is unambiguous and doesn't collide with legitimate Markdown
#: like ``[image: figure 1]`` a human author might type. We match up
#: to the first ``]`` so a marker can't accidentally swallow a
#: following sentence if the Writer forgets to close it — bad input
#: produces no match (we just skip it) rather than corrupted output.
#:
#: Non-greedy plus ``[^\]]`` is redundant but belt-and-braces: if the
#: character class ever needs to grow (e.g. to allow escaped brackets)
#: the non-greedy quantifier keeps the match semantics stable.
_MARKER_RE = re.compile(r"\[IMAGE:\s*([^\]]+?)\s*\]")

#: Fenced code blocks for diagram languages we pass through unchanged.
#: We match the opening fence (``` plus the language tag on its own
#: line), capture every line up to the closing fence, and stash that
#: source on :class:`~backend.agents.schemas.DiagramAsset` for the
#: frontend to render.
#:
#: Design notes:
#: * The opening fence must start at column 0 — indented blocks are
#:   code-in-a-list-item, not a diagram the author expects rendered.
#: * We require the closing ``` to also start at column 0, matching
#:   CommonMark rules. A Mermaid block whose closing fence is indented
#:   is almost certainly the result of an LLM confusingly nesting a
#:   code block inside a list; we'd rather not claim ownership of it.
#: * ``re.DOTALL`` / ``re.MULTILINE`` flags are both necessary because
#:   diagrams are multi-line.
#: * Language tag alternation is explicit rather than open-ended —
#:   treating an arbitrary fence as a "diagram" would consume real
#:   code samples (``python``, ``bash``, etc.) and misrepresent them
#:   to the UI. When we add new diagram languages, extend this list.
_DIAGRAM_LANGS = ("mermaid",)
_DIAGRAM_RE = re.compile(
    r"^```(" + "|".join(_DIAGRAM_LANGS) + r")\s*\n"
    r"(.*?)"
    r"\n^```\s*$",
    re.DOTALL | re.MULTILINE,
)


def extract_diagrams(body_markdown: str) -> List[DiagramAsset]:
    """Index every diagram fenced block in document order.

    Does NOT modify the body — the diagrams stay inline so the
    frontend Markdown pipeline renders them in situ. This function's
    only job is to surface them as structured data for UI and logging.
    """

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
    """Construct a Pollinations URL for one image description.

    The prompt lands in the *path* segment (URL-encoded), while every
    other knob — model, dimensions, enhance, seed, referrer — rides
    in the query string. Empty / whitespace-only descriptions raise
    ``ValueError`` rather than silently producing a useless URL.

    Parameters
    ----------
    description:
        Free-form image prompt, typically the contents of an
        ``[IMAGE: ...]`` marker.
    settings:
        Optional pre-resolved :class:`Settings`; defaults to the
        cached :func:`get_settings`.
    """

    settings = settings or get_settings()

    cleaned = description.strip()
    if not cleaned:
        raise ValueError("image description must not be empty")

    base = settings.pollinations_base_url.rstrip("/")

    # ``quote(..., safe="")`` encodes slashes and every other reserved
    # character so the prompt can contain any Unicode without breaking
    # the path. Spaces become ``%20`` (not ``+``) — important because
    # query-string rules don't apply to the path segment.
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
    """Assemble a safe Markdown image tag.

    Alt text is escaped so a stray ``]`` in a Writer description can't
    prematurely close the tag and break downstream rendering. We leave
    the URL alone — :func:`build_image_url` already URL-encodes it.
    """

    # Only ``]`` and ``\`` need escaping inside the alt-text bracket.
    safe_alt = alt_text.replace("\\", "\\\\").replace("]", "\\]")
    return f"![{safe_alt}]({url})"


async def illustrate_article(
    writer_output: WriterOutput,
    *,
    settings: Optional[Settings] = None,
    on_event: Optional[EventCallback] = None,
) -> FinalArticle:
    """Resolve every ``[IMAGE: ...]`` marker in a draft to a live URL.

    The function is ``async`` for pipeline consistency (every other
    stage in Swift is async) but does no I/O — it returns as soon as
    the substitution completes.

    Semantics:

    * Each marker is replaced by a Markdown image tag pointing at
      Pollinations. The original description becomes the alt text.
    * The returned ``images`` list preserves document order, so
      ``images[0]`` corresponds to the first marker in the source.
    * ``image_placeholder_count`` on the returned
      :class:`FinalArticle` is derived from what we actually found
      and resolved — not copied from
      ``writer_output.image_placeholder_count``. LLMs miscount; we
      don't.
    * Empty / whitespace-only markers (``[IMAGE: ]``) are dropped
      with a warning rather than producing a broken URL — the field
      didn't carry information anyway.

    A mismatch between the Writer's self-reported count and the count
    we observe is logged at WARNING so operators can flag Writer
    regressions without failing the request. The article still ships.
    """

    settings = settings or get_settings()
    emit = resolve_callback(on_event)

    # Count markers up-front so the SSE ``images.started`` event can
    # tell the UI how many placeholders we're about to resolve. The
    # count-then-substitute two-pass approach is negligibly more work
    # (regex is cheap) and buys us the ability to show a proper
    # progress indicator on the client side.
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
            # build_image_url only raises on empty — caught above.
            # Keep this branch in case we add more validation later.
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

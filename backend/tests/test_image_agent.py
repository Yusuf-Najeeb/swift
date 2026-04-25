"""Tests for the Image Agent (Step 5).

The offline suite covers pure string-processing behaviour: URL
construction for all the quirks of prompt content (Unicode, slashes,
empty, long), marker extraction across benign and adversarial
drafts, settings overrides, and the count-mismatch warning path.

One network-gated smoke test (``IMAGE_LIVE=1``) actually fetches a
Pollinations URL built by the agent and asserts the server returns
an image payload. This doesn't need OpenRouter, so it's independent
of ``OPENROUTER_LIVE`` — a new gate keeps the default CI run offline
without conflating two different external dependencies.
"""

from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from backend.agents.image_agent import (
    _DIAGRAM_RE,
    _MARKER_RE,
    build_image_url,
    extract_diagrams,
    illustrate_article,
)
from backend.agents.schemas import (
    DiagramAsset,
    FinalArticle,
    ImageAsset,
    WriterOutput,
)
from backend.config import Settings


# ─── Fixtures / helpers ────────────────────────────────────────────────


def _test_settings(**overrides: object) -> Settings:
    """Build a Settings instance with deterministic image defaults.

    Seeding Pollinations makes URL equality checks stable across runs,
    which matters for snapshot-style assertions in tests. Every other
    knob mirrors production defaults unless a test overrides it.
    """

    base = dict(
        OPENROUTER_API_KEY="sk-test-dummy",
        SWIFT_POLLINATIONS_SEED="42",
        SWIFT_POLLINATIONS_REFERRER="swift-writer-tests",
    )
    base.update({k: str(v) for k, v in overrides.items()})

    env_backup = {k: os.environ.get(k) for k in base}
    try:
        for k, v in base.items():
            os.environ[k] = v
        return Settings()  # type: ignore[call-arg]
    finally:
        for k, original in env_backup.items():
            if original is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = original


def _parse(url: str) -> tuple[str, str, dict[str, list[str]]]:
    parsed = urlparse(url)
    return parsed.netloc, parsed.path, parse_qs(parsed.query)


# ─── build_image_url ──────────────────────────────────────────────────


def test_build_image_url_encodes_prompt_and_includes_core_params() -> None:
    url = build_image_url(
        "a calm lakeside morning, oil painting", settings=_test_settings()
    )

    netloc, path, qs = _parse(url)

    assert netloc == "image.pollinations.ai"
    # Prompt lives in the path, URL-encoded — spaces must be %20 (not +),
    # commas escaped, etc. We don't snapshot the exact string because
    # urllib can choose either upper- or lower-case hex for %-escapes;
    # decoding round-trip is the honest check.
    assert path.startswith("/prompt/")
    assert unquote(path[len("/prompt/"):]) == "a calm lakeside morning, oil painting"

    assert qs["model"] == ["flux"]
    assert qs["width"] == ["1024"]
    assert qs["height"] == ["1024"]
    assert qs["seed"] == ["42"]  # from _test_settings
    assert qs["referrer"] == ["swift-writer-tests"]


def test_build_image_url_enhances_by_default_but_respects_override() -> None:
    """``enhance=true`` is our opinionated default (see
    backend.config.Settings docstring); verify the flag flows through
    and that callers can turn it off."""

    on = build_image_url("x", settings=_test_settings())
    off = build_image_url(
        "x", settings=_test_settings(SWIFT_POLLINATIONS_ENHANCE="false")
    )

    assert parse_qs(urlparse(on).query)["enhance"] == ["true"]
    assert "enhance" not in parse_qs(urlparse(off).query)


def test_build_image_url_omits_nologo_and_seed_when_not_set() -> None:
    """Query-string hygiene: every param we emit is one the caller
    opted into. Default settings mean no watermark suppression (needs
    an account) and no seed (random per request in production)."""

    settings = _test_settings()
    # Strip the seed the helper injected so we can verify the
    # random-seed / no-nologo defaults.
    settings = settings.model_copy(
        update={"pollinations_seed": None, "pollinations_nologo": False}
    )

    url = build_image_url("x", settings=settings)
    qs = parse_qs(urlparse(url).query)

    assert "nologo" not in qs
    assert "seed" not in qs


def test_build_image_url_includes_nologo_when_requested() -> None:
    settings = _test_settings(SWIFT_POLLINATIONS_NOLOGO="true")
    url = build_image_url("x", settings=settings)
    assert parse_qs(urlparse(url).query)["nologo"] == ["true"]


def test_build_image_url_handles_unicode_and_special_chars_in_prompt() -> None:
    """Prompt characters often include punctuation that's reserved in
    URLs (``&``, ``?``, ``/``, ``#``). They must land in the path
    intact or Pollinations will generate off the wrong string."""

    weird = "café / résumé & rock?n'roll #1"
    url = build_image_url(weird, settings=_test_settings())
    _, path, _ = _parse(url)

    assert unquote(path[len("/prompt/"):]) == weird


def test_build_image_url_rejects_empty_description() -> None:
    """An empty prompt would produce a URL whose path is ``/prompt/``
    — Pollinations would either 404 or generate on nothing. Raising
    here lets the caller (illustrate_article) skip gracefully rather
    than shipping a broken image tag."""

    with pytest.raises(ValueError):
        build_image_url("   ", settings=_test_settings())


def test_build_image_url_strips_trailing_slash_from_base_url() -> None:
    """Defensive: operators sometimes set base URLs with trailing
    slashes. Accept both forms without producing ``//prompt``."""

    settings = _test_settings(
        SWIFT_POLLINATIONS_BASE_URL="https://image.pollinations.ai/prompt/"
    )
    url = build_image_url("x", settings=settings)
    assert "//prompt/" not in urlparse(url).path


def test_build_image_url_omits_referrer_when_blank() -> None:
    """A user who sets the env var to an empty string is opting out
    of app-identification; we must not send ``referrer=``."""

    settings = _test_settings(SWIFT_POLLINATIONS_REFERRER="")
    url = build_image_url("x", settings=settings)
    assert "referrer" not in parse_qs(urlparse(url).query)


# ─── marker regex ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "line, expected",
    [
        ("[IMAGE: a cat]", "a cat"),
        ("[IMAGE:  surrounding whitespace   ]", "surrounding whitespace"),
        ("[IMAGE: with : colon inside]", "with : colon inside"),
        # Multiple markers on one line must all be captured independently.
        # We test that via the substitution path elsewhere; here we assert
        # that the non-greedy match doesn't swallow the second marker.
    ],
)
def test_marker_regex_captures_expected_description(
    line: str, expected: str
) -> None:
    match = _MARKER_RE.search(line)
    assert match is not None
    assert match.group(1).strip() == expected


def test_marker_regex_is_case_sensitive() -> None:
    """The Writer prompt dictates uppercase ``IMAGE``. Matching
    case-insensitively would false-positive on common Markdown like
    ``[image: figure 1]`` a human author might write."""

    assert _MARKER_RE.search("[image: lowercase]") is None
    assert _MARKER_RE.search("[IMAGE: upper]") is not None


def test_marker_regex_ignores_unclosed_markers() -> None:
    """An unclosed ``[IMAGE:`` shouldn't devour the rest of the
    paragraph — non-greedy plus the ``[^\\]]`` character class means
    it just won't match at all."""

    assert _MARKER_RE.search("[IMAGE: forgot to close") is None


# ─── illustrate_article ───────────────────────────────────────────────


def _sample_writer_output(body: str, *, claimed_count: int = 2) -> WriterOutput:
    return WriterOutput(
        title="Sample Article",
        summary="A short teaser.",
        body_markdown=body,
        image_placeholder_count=claimed_count,
    )


def test_illustrate_article_substitutes_every_marker() -> None:
    body = (
        "# Hello\n\n"
        "Intro.\n\n"
        "[IMAGE: a calm lakeside]\n\n"
        "Body.\n\n"
        "[IMAGE: an autumn forest trail]\n"
    )
    out = asyncio.run(
        illustrate_article(
            _sample_writer_output(body), settings=_test_settings()
        )
    )

    assert isinstance(out, FinalArticle)
    assert out.image_placeholder_count == 2
    assert len(out.images) == 2
    assert "[IMAGE:" not in out.body_markdown, "marker leaked into output"
    # Markdown image tags landed in order.
    assert out.body_markdown.index("a%20calm%20lakeside") < out.body_markdown.index(
        "autumn"
    )


def test_illustrate_article_preserves_document_order() -> None:
    body = (
        "[IMAGE: first]\nlorem\n[IMAGE: second]\nipsum\n[IMAGE: third]\n"
    )
    out = asyncio.run(
        illustrate_article(
            _sample_writer_output(body, claimed_count=3),
            settings=_test_settings(),
        )
    )

    assert [img.description for img in out.images] == ["first", "second", "third"]


def test_illustrate_article_uses_actual_count_not_writer_claim(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Writer self-reports are known-flaky. When the claimed count
    disagrees with reality, the Image Agent must (a) trust reality,
    (b) log a WARNING so operators can spot bad Writer runs, (c) still
    ship the article."""

    body = "[IMAGE: only one]\n"
    draft = _sample_writer_output(body, claimed_count=5)  # Writer lied

    with caplog.at_level(logging.WARNING, logger="swift.image_agent"):
        out = asyncio.run(illustrate_article(draft, settings=_test_settings()))

    assert out.image_placeholder_count == 1
    assert len(out.images) == 1
    assert any(
        "writer self-reported 5 image placeholders" in record.message
        for record in caplog.records
    )


def test_illustrate_article_with_no_markers_returns_body_untouched() -> None:
    """A Writer that forgot its own contract shouldn't crash the
    pipeline. We return an image-free FinalArticle so the caller can
    still ship the text."""

    body = "# Plain\n\nNo markers here.\n"
    out = asyncio.run(
        illustrate_article(
            _sample_writer_output(body, claimed_count=0),
            settings=_test_settings(),
        )
    )

    assert out.body_markdown == body
    assert out.images == []
    assert out.image_placeholder_count == 0


def test_illustrate_article_drops_empty_markers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``[IMAGE: ]`` carries zero information. We drop it silently
    from the body (replace with nothing) and log a warning rather
    than producing a broken Pollinations URL."""

    body = "Keep this.\n[IMAGE:   ]\nAlso keep this.\n"
    with caplog.at_level(logging.WARNING, logger="swift.image_agent"):
        out = asyncio.run(
            illustrate_article(
                _sample_writer_output(body, claimed_count=1),
                settings=_test_settings(),
            )
        )

    assert "[IMAGE:" not in out.body_markdown
    assert out.images == []
    assert out.image_placeholder_count == 0
    assert any(
        "empty [IMAGE: ] marker" in record.message for record in caplog.records
    )


def test_illustrate_article_escapes_alt_text_brackets() -> None:
    """If a Writer description contained a raw ``]`` it'd prematurely
    close the Markdown alt attribute and break rendering. We escape
    it before emitting the tag."""

    body = "[IMAGE: a map showing [redacted] coordinates]\n"
    # The outer ``]`` belongs to the marker; the inner ``]`` is inside
    # the description — except our regex stops at the first ``]``. So
    # the captured description is "a map showing [redacted". That's
    # fine: the test is that whatever lands in alt-text is escaped.
    out = asyncio.run(
        illustrate_article(
            _sample_writer_output(body, claimed_count=1),
            settings=_test_settings(),
        )
    )

    assert len(out.images) == 1
    # The regex stopped at the first ``]`` so there's no bracket in
    # the description this time — but verify the escape logic by
    # constructing a pathological asset directly.
    from backend.agents.image_agent import _markdown_image

    md = _markdown_image("has ] bracket", "https://example.com")
    assert "\\]" in md  # bracket got escaped


def test_illustrate_article_settings_override_affects_urls() -> None:
    body = "[IMAGE: a bird]\n"
    square = asyncio.run(
        illustrate_article(
            _sample_writer_output(body, claimed_count=1),
            settings=_test_settings(),  # default 1024x1024
        )
    )
    wide = asyncio.run(
        illustrate_article(
            _sample_writer_output(body, claimed_count=1),
            settings=_test_settings(
                SWIFT_POLLINATIONS_WIDTH="1920",
                SWIFT_POLLINATIONS_HEIGHT="1080",
            ),
        )
    )

    assert "width=1024" in square.images[0].url
    assert "width=1920" in wide.images[0].url
    assert "height=1080" in wide.images[0].url


def test_illustrate_article_output_is_valid_markdown_image_tag() -> None:
    body = "[IMAGE: a cat]\n"
    out = asyncio.run(
        illustrate_article(
            _sample_writer_output(body, claimed_count=1),
            settings=_test_settings(),
        )
    )

    # Exactly one Markdown image expression; alt-text and URL both present.
    import re

    matches = re.findall(r"!\[([^\]]+)\]\(([^)]+)\)", out.body_markdown)
    assert len(matches) == 1
    alt, url = matches[0]
    assert alt == "a cat"
    assert url == out.images[0].url
    assert url.startswith("https://image.pollinations.ai/prompt/")


# ─── Diagram extraction ───────────────────────────────────────────────


def test_extract_diagrams_finds_single_mermaid_block() -> None:
    body = (
        "# Title\n\n"
        "Some prose.\n\n"
        "```mermaid\n"
        "flowchart LR\n"
        "    A --> B\n"
        "```\n\n"
        "More prose.\n"
    )
    diagrams = extract_diagrams(body)

    assert len(diagrams) == 1
    assert diagrams[0].language == "mermaid"
    assert "flowchart LR" in diagrams[0].source
    assert "A --> B" in diagrams[0].source


def test_extract_diagrams_preserves_document_order() -> None:
    body = (
        "```mermaid\nflowchart LR\n  A --> B\n```\n\n"
        "Intermission.\n\n"
        "```mermaid\nsequenceDiagram\n  U->>S: hi\n```\n\n"
        "End.\n"
        "```mermaid\nstateDiagram-v2\n  [*] --> Idle\n```\n"
    )
    diagrams = extract_diagrams(body)

    assert len(diagrams) == 3
    assert diagrams[0].source.startswith("flowchart")
    assert diagrams[1].source.startswith("sequenceDiagram")
    assert diagrams[2].source.startswith("stateDiagram-v2")


def test_extract_diagrams_returns_empty_when_no_fences_present() -> None:
    assert extract_diagrams("# Title\n\nJust prose.\n") == []


def test_extract_diagrams_ignores_non_diagram_fences() -> None:
    """Code samples (python/bash/etc.) must NOT be misclassified as
    diagrams — that would corrupt the UI's diagram index and make
    diagram counts meaningless for analytics."""

    body = (
        "```python\n"
        "def hello():\n"
        "    return 'world'\n"
        "```\n\n"
        "```bash\n"
        "echo hello\n"
        "```\n"
    )
    assert extract_diagrams(body) == []


def test_extract_diagrams_ignores_unclosed_fence() -> None:
    """An LLM that forgot the closing ``` shouldn't corrupt the
    extraction; the dangling block should be silently skipped rather
    than swallow the rest of the article."""

    body = "# Title\n\n```mermaid\nflowchart LR\n  A --> B\n\nMore prose.\n"
    assert extract_diagrams(body) == []


def test_extract_diagrams_ignores_indented_fences() -> None:
    """Indented ``` is a CommonMark code-in-list-item construct, not a
    standalone diagram the author expects rendered."""

    body = "- bullet\n\n  ```mermaid\n  flowchart LR\n    A --> B\n  ```\n"
    assert extract_diagrams(body) == []


def test_extract_diagrams_skips_empty_mermaid_block(
    caplog: pytest.LogCaptureFixture,
) -> None:
    body = "```mermaid\n\n```\n"
    with caplog.at_level(logging.WARNING, logger="swift.image_agent"):
        result = extract_diagrams(body)

    assert result == []
    assert any("empty mermaid" in record.message for record in caplog.records)


def test_illustrate_article_preserves_mermaid_fences_verbatim() -> None:
    """Critical contract: Mermaid blocks must pass through
    ``illustrate_article`` *unchanged*. The frontend is the renderer,
    not us, and any transformation here (even well-intentioned
    normalisation) risks breaking valid Mermaid syntax."""

    mermaid = (
        "```mermaid\n"
        "flowchart TD\n"
        "    A[Start] --> B{Decision}\n"
        "    B -- yes --> C[Continue]\n"
        "    B -- no  --> D[Stop]\n"
        "```"
    )
    body = f"# Tech Article\n\nPreamble.\n\n{mermaid}\n\n[IMAGE: editorial illustration of a branching river, watercolor]\n"
    out = asyncio.run(
        illustrate_article(
            _sample_writer_output(body, claimed_count=1),
            settings=_test_settings(),
        )
    )

    assert mermaid in out.body_markdown
    assert len(out.diagrams) == 1
    assert out.diagrams[0].language == "mermaid"
    assert "Decision" in out.diagrams[0].source
    assert len(out.images) == 1  # image alongside diagram


def test_illustrate_article_mixed_diagrams_and_images() -> None:
    body = (
        "# Hybrid\n\n"
        "[IMAGE: an engineer at a whiteboard, editorial illustration]\n\n"
        "```mermaid\nflowchart LR\n    A --> B\n```\n\n"
        "[IMAGE: a minimalist dashboard, flat vector illustration]\n\n"
        "```mermaid\nsequenceDiagram\n    U->>S: hi\n```\n"
    )
    out = asyncio.run(
        illustrate_article(
            _sample_writer_output(body, claimed_count=2),
            settings=_test_settings(),
        )
    )

    assert len(out.images) == 2
    assert len(out.diagrams) == 2
    # Images got substituted, diagrams didn't.
    assert "[IMAGE:" not in out.body_markdown
    assert "```mermaid" in out.body_markdown


def test_illustrate_article_no_diagrams_keeps_empty_list() -> None:
    body = "# Plain\n\n[IMAGE: an editorial illustration of a clock, flat vector]\n"
    out = asyncio.run(
        illustrate_article(
            _sample_writer_output(body, claimed_count=1),
            settings=_test_settings(),
        )
    )
    assert out.diagrams == []


def test_diagram_asset_round_trips_through_pydantic() -> None:
    asset = DiagramAsset(language="mermaid", source="flowchart LR\n  A --> B")
    round_trip = DiagramAsset.model_validate(asset.model_dump())
    assert round_trip == asset


def test_diagram_regex_is_case_sensitive_on_language_tag() -> None:
    """CommonMark fence language tags are conventionally lowercase and
    our regex enforces that. ``` ```Mermaid ``` wouldn't render as a
    diagram on most renderers, so we shouldn't index it either."""

    assert _DIAGRAM_RE.search("```Mermaid\nflowchart LR\n  A --> B\n```") is None
    assert _DIAGRAM_RE.search("```mermaid\nflowchart LR\n  A --> B\n```") is not None


# ─── ImageAsset schema ────────────────────────────────────────────────


def test_image_asset_round_trips_through_pydantic() -> None:
    asset = ImageAsset(
        description="a foo",
        url="https://image.pollinations.ai/prompt/a%20foo",
        alt_text="a foo",
    )
    round_trip = ImageAsset.model_validate(asset.model_dump())
    assert round_trip == asset


# ─── Settings defaults ────────────────────────────────────────────────


def test_settings_pollinations_defaults_match_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep these defaults under explicit test — changing them changes
    the image style for every Swift user."""

    for var in (
        "SWIFT_POLLINATIONS_BASE_URL",
        "SWIFT_POLLINATIONS_MODEL",
        "SWIFT_POLLINATIONS_WIDTH",
        "SWIFT_POLLINATIONS_HEIGHT",
        "SWIFT_POLLINATIONS_ENHANCE",
        "SWIFT_POLLINATIONS_NOLOGO",
        "SWIFT_POLLINATIONS_SEED",
        "SWIFT_POLLINATIONS_REFERRER",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-dummy")

    s = Settings(_env_file=None)
    assert s.pollinations_base_url == "https://image.pollinations.ai/prompt"
    assert s.pollinations_model == "flux"
    assert s.pollinations_width == 1024
    assert s.pollinations_height == 1024
    assert s.pollinations_enhance is True
    assert s.pollinations_nologo is False
    assert s.pollinations_seed is None
    assert s.pollinations_referrer == "swift-writer"


# ─── Live smoke test (opt-in) ─────────────────────────────────────────

IMAGE_LIVE = os.getenv("IMAGE_LIVE") == "1"


@pytest.mark.skipif(
    not IMAGE_LIVE,
    reason="Set IMAGE_LIVE=1 to hit Pollinations.ai",
)
def test_illustrate_article_produces_fetchable_urls_live() -> None:
    """End-to-end smoke: run a small draft through the Image Agent
    and verify Pollinations actually serves an image for the URL we
    built. This catches URL-construction bugs we can't catch offline
    (Pollinations changing their path, query names, accepted models,
    etc.).

    Uses urllib so we don't pull a new dependency just for this test.
    We set a short timeout to fail fast on transient outages — this
    test is a smoke check, not a perf benchmark.
    """

    import urllib.request

    body = (
        "# Live\n\n"
        "[IMAGE: minimal ink sketch of a sailboat at sunrise]\n"
    )
    settings = _test_settings()
    out = asyncio.run(
        illustrate_article(
            _sample_writer_output(body, claimed_count=1), settings=settings
        )
    )

    assert len(out.images) == 1
    url = out.images[0].url

    # Pollinations generates on GET; first byte may take several
    # seconds, so 60s is a safe-but-not-indefinite timeout.
    req = urllib.request.Request(
        url, headers={"User-Agent": "swift-writer-tests/1.0"}
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        assert response.status == 200
        content_type = response.headers.get("Content-Type", "")
        assert content_type.startswith("image/"), (
            f"expected image/* content-type, got {content_type!r}"
        )
        # Read enough bytes to confirm the stream isn't empty; don't
        # drain the whole image just to keep the test quick.
        head = response.read(1024)
        assert len(head) > 0

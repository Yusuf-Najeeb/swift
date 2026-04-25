"""Tiny end-to-end CLI for Swift.

Runs a full article through the pipeline and prints it to stdout. Zero
frontend, zero HTTP — just the agent pipeline end-to-end so you can
confirm everything wires up before the FastAPI layer lands (Step 7).

Usage
-----

From the project root, with the uv-managed venv:

    uv run python scripts/run_article.py "Why small language models matter"

Options:

    --tone       voice (default: conversational)
    --length     short | medium | long (default: short)
    --keyword    repeatable; at least one keyword recommended
    --audience   target reader (e.g. "software engineers")
    --retries    orchestrator revision budget (default: from Settings)
    --no-images  skip the Image Agent (useful when Pollinations is slow)
    --out        write the final article Markdown to a file as well as stdout

Exit codes:

* 0 — article produced (approved OR max-retries exhausted; both are
  legitimate outcomes, see FinalArticle for details)
* 1 — a runtime error occurred (missing API key, MCP crash, etc.)

This script intentionally stays thin: it doesn't wrap the pipeline
with anything Swift-specific, so if it works the pipeline works.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import List, Optional

# Make ``backend.*`` importable when this file is invoked as a plain
# script (``python scripts/run_article.py ...``). Python prepends the
# script's own directory to ``sys.path`` — not the project root — so
# without this shim the agent imports fail. Prepending is deliberate:
# it beats a pre-installed "backend" package of the same name.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.agents.image_agent import illustrate_article
from backend.agents.orchestrator import orchestrate_article
from backend.agents.providers import configure_openrouter
from backend.agents.schemas import ArticleBrief, ArticleRun, FinalArticle


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one full Swift pipeline and print the article."
    )
    parser.add_argument("topic", help="Subject of the article.")
    parser.add_argument("--tone", default="conversational")
    parser.add_argument(
        "--length",
        choices=("short", "medium", "long"),
        default="short",
    )
    parser.add_argument(
        "--keyword",
        action="append",
        dest="keywords",
        default=None,
        help="Keyword to weave into the article (repeatable).",
    )
    parser.add_argument("--audience", default=None)
    parser.add_argument(
        "--retries",
        type=int,
        default=None,
        help="Override SWIFT_ORCHESTRATOR_MAX_RETRIES for this run.",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="Skip the Image Agent; keep raw [IMAGE: ...] markers.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write final Markdown to this file in addition to stdout.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show DEBUG logs from the agents (lots of output).",
    )
    return parser.parse_args(argv)


def _configure_logging(verbose: bool) -> None:
    # Surface per-iteration scores from the orchestrator by default; go
    # deeper in verbose mode for debugging wiring.
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet a couple of chatty libraries in non-verbose mode.
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("openai").setLevel(logging.WARNING)


def _print_run_summary(run: ArticleRun) -> None:
    # Compact, human-readable recap of the revision loop — the most
    # useful thing to see in a terminal demo.
    print()
    print("─" * 70)
    print(f"Run finished: {run.iterations} attempt(s), approved={run.approved}")
    for attempt in run.attempts:
        fb = attempt.feedback
        print(
            f"  iter {attempt.iteration}: score={fb.score}  "
            f"approved={fb.approved}  "
            f"weaknesses={len(fb.weaknesses)}  "
            f"suggestions={len(fb.suggestions)}"
        )
    print("─" * 70)
    print()


async def _run(args: argparse.Namespace) -> int:
    configure_openrouter()

    brief = ArticleBrief(
        topic=args.topic,
        tone=args.tone,
        length=args.length,
        keywords=args.keywords or [],
        audience=args.audience,
    )

    print(f"Brief  : {brief.topic}")
    print(f"Tone   : {brief.tone} · Length: {brief.length}")
    if brief.keywords:
        print(f"Keywords: {', '.join(brief.keywords)}")
    if brief.audience:
        print(f"Audience: {brief.audience}")
    print("\nRunning Writer ↔ Evaluator loop...\n")

    run: ArticleRun = await orchestrate_article(brief, max_retries=args.retries)
    _print_run_summary(run)

    draft = run.final_draft
    if args.no_images:
        body = draft.body_markdown
        image_urls: List[str] = []
    else:
        print("Resolving image placeholders...\n")
        final: FinalArticle = await illustrate_article(draft)
        body = final.body_markdown
        image_urls = [img.url for img in final.images]

    # Article dump.
    print("=" * 70)
    print(f"TITLE: {draft.title}")
    print(f"SUMMARY: {draft.summary}")
    print("=" * 70)
    print()
    print(body)
    if image_urls:
        print()
        print("Images:")
        for i, url in enumerate(image_urls, 1):
            print(f"  [{i}] {url}")

    if args.out is not None:
        args.out.write_text(body, encoding="utf-8")
        print(f"\nWrote Markdown to {args.out}")

    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    _configure_logging(args.verbose)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 — top-level catch is intentional
        logging.getLogger("swift.cli").exception("run failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

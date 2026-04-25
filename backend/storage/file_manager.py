"""Local article persistence (Step 9).

Writes the final article Markdown to disk under `backend/<articles_dir>/`.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from slugify import slugify

from backend.agents.schemas import ArticleBrief, FinalArticle
from backend.config import Settings
from backend.storage.schemas import ArticleListItem, SavedArticle


def _backend_root() -> Path:
    # backend/storage/file_manager.py -> backend/
    return Path(__file__).resolve().parents[1]


def get_articles_dir(settings: Settings) -> Path:
    """Return the absolute directory for saved articles."""

    configured = Path(settings.articles_dir)
    if configured.is_absolute():
        return configured
    return _backend_root() / configured


_FRONT_MATTER = re.compile(
    r"^---\r?\n(?P<body>.*?)\r?\n---\r?\n",
    re.DOTALL,
)


def _unquote_simple_yaml_string(raw: str) -> str:
    t = raw.strip()
    if len(t) >= 2 and t[0] == t[-1] == '"':
        return t[1:-1].replace(r"\"", '"')
    return t


def _title_from_first_bytes(content: str) -> str | None:
    """Parse ``title:`` from Swift's YAML front matter, if present."""

    m = _FRONT_MATTER.match(content)
    if not m:
        return None
    for line in m.group("body").splitlines():
        line = line.rstrip()
        if line.lower().startswith("title:"):
            return _unquote_simple_yaml_string(line.split(":", 1)[1])
    return None


def list_saved_articles(settings: Settings) -> list[ArticleListItem]:
    """Return all ``*.md`` in the articles directory, newest mtime first."""

    articles_dir = get_articles_dir(settings)
    if not articles_dir.is_dir():
        return []

    out: list[ArticleListItem] = []
    for path in sorted(
        articles_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        try:
            st = path.stat()
        except OSError:
            continue
        try:
            peek = path.read_text(encoding="utf-8", errors="replace")[:16_384]
        except OSError:
            peek = ""
        title = _title_from_first_bytes(peek) or path.stem
        mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        filename = path.name
        out.append(
            ArticleListItem(
                filename=filename,
                title=title,
                url_path=f"/api/articles/{filename}",
                size_bytes=st.st_size,
                modified_utc=mtime.isoformat().replace("+00:00", "Z"),
            )
        )
    return out


def _render_markdown_document(
    article: FinalArticle,
    *,
    brief: ArticleBrief,
    approved: bool,
    iterations: int,
) -> str:
    generated_at = datetime.now(tz=timezone.utc).isoformat()
    keywords = ", ".join(brief.keywords) if brief.keywords else ""
    audience = brief.audience or ""
    notes = (brief.extra_notes or "").strip()

    front_matter = "\n".join(
        [
            "---",
            f'title: "{article.title.replace(chr(34), r"\"")}"',
            f'summary: "{article.summary.replace(chr(34), r"\"")}"',
            f"topic: {brief.topic}",
            f"tone: {brief.tone}",
            f"length: {brief.length}",
            f"keywords: {keywords}",
            f"audience: {audience}",
            f"approved: {str(bool(approved)).lower()}",
            f"iterations: {int(iterations)}",
            f"generated_at_utc: {generated_at}",
            "---",
        ]
    )

    parts = [front_matter, ""]
    if notes:
        parts.extend(["<!-- extra_notes:", notes, "-->", ""])
    parts.append(article.body_markdown.strip())
    parts.append("")  # trailing newline
    return "\n".join(parts)


def save_final_article(
    article: FinalArticle,
    *,
    brief: ArticleBrief,
    approved: bool,
    iterations: int,
    settings: Settings,
) -> SavedArticle:
    """Persist an article and return its metadata."""

    articles_dir = get_articles_dir(settings)
    articles_dir.mkdir(parents=True, exist_ok=True)

    date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    slug = slugify(article.title)[:80] or "article"
    filename = f"{date}-{slug}.md"
    path = articles_dir / filename

    # Avoid accidental overwrite if two runs share the same title+date.
    if path.exists():
        suffix = datetime.now(tz=timezone.utc).strftime("%H%M%S")
        filename = f"{date}-{slug}-{suffix}.md"
        path = articles_dir / filename

    markdown = _render_markdown_document(
        article, brief=brief, approved=approved, iterations=iterations
    )
    path.write_text(markdown, encoding="utf-8")

    # Path relative to backend/ for easy debugging.
    backend_root = _backend_root()
    try:
        rel = str(path.relative_to(backend_root))
    except ValueError:
        rel = str(path)

    return SavedArticle(
        filename=filename,
        relative_path=rel,
        url_path=f"/api/articles/{filename}",
    )


__all__ = ["get_articles_dir", "list_saved_articles", "save_final_article"]


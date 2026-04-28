
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from slugify import slugify

from backend.agents.schemas import ArticleBrief, FinalArticle
from backend.config import Settings
from backend.storage.article_titles import title_from_first_bytes
from backend.storage.schemas import ArticleListItem, SavedArticle


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[1]


def get_articles_dir(settings: Settings) -> Path:

    configured = Path(settings.articles_dir)
    if configured.is_absolute():
        return configured
    return _backend_root() / configured


def article_storage_is_azure(settings: Settings) -> bool:
    return bool((getattr(settings, "azure_storage_connection_string", None) or "").strip())


def list_saved_articles(settings: Settings) -> list[ArticleListItem]:

    if article_storage_is_azure(settings):
        from backend.storage import blob_articles

        return blob_articles.list_saved_articles_azure(settings)

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
        title = title_from_first_bytes(peek) or path.stem
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

    markdown = _render_markdown_document(
        article, brief=brief, approved=approved, iterations=iterations
    )
    date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    slug = slugify(article.title)[:80] or "article"
    filename = f"{date}-{slug}.md"

    if article_storage_is_azure(settings):
        from backend.storage import blob_articles

        initial = filename
        filename = blob_articles.ensure_unique_blob_name(
            settings, date=date, slug=slug, initial=initial
        )
        rel = blob_articles.save_final_article_azure(
            filename, markdown, settings
        )
        return SavedArticle(
            filename=filename,
            relative_path=rel,
            url_path=f"/api/articles/{filename}",
        )

    articles_dir = get_articles_dir(settings)
    articles_dir.mkdir(parents=True, exist_ok=True)
    path = articles_dir / filename
    if path.exists():
        suffix = datetime.now(tz=timezone.utc).strftime("%H%M%S")
        filename = f"{date}-{slug}-{suffix}.md"
        path = articles_dir / filename
    path.write_text(markdown, encoding="utf-8")
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


def read_article_bytes(settings: Settings, filename: str) -> bytes:
    if article_storage_is_azure(settings):
        from backend.storage import blob_articles

        return blob_articles.read_article_azure(settings, filename)
    articles_dir = get_articles_dir(settings)
    path = (articles_dir / filename).resolve()
    try:
        base = articles_dir.resolve()
    except FileNotFoundError:
        base = articles_dir
    if base not in path.parents and path != base:
        raise FileNotFoundError(filename)
    if not path.is_file():
        raise FileNotFoundError(filename)
    return path.read_bytes()


__all__ = [
    "article_storage_is_azure",
    "get_articles_dir",
    "list_saved_articles",
    "read_article_bytes",
    "save_final_article",
]


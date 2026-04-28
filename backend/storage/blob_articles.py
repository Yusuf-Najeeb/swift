
from __future__ import annotations

from datetime import datetime, timezone

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.storage.blob import BlobServiceClient

from backend.config import Settings
from backend.storage.article_titles import title_from_first_bytes
from backend.storage.schemas import ArticleListItem

_MAX_PEEK = 16_384


def _container(settings: Settings):
    assert settings.azure_storage_connection_string
    return BlobServiceClient.from_connection_string(
        settings.azure_storage_connection_string
    ).get_container_client(settings.azure_storage_container_name)


def _blob_exists(container, name: str) -> bool:
    b = container.get_blob_client(name)
    try:
        b.get_blob_properties()
        return True
    except ResourceNotFoundError:
        return False
    except HttpResponseError as e:
        if e.status_code == 404:
            return False
        raise


def list_saved_articles_azure(settings: Settings) -> list[ArticleListItem]:
    container = _container(settings)
    entries: list[tuple[ArticleListItem, datetime]] = []
    for blob in container.list_blobs():
        if not blob.name.endswith(".md"):
            continue
        if blob.size is None:
            continue
        last = blob.last_modified
        if last and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        peek = ""
        try:
            b = container.download_blob(blob.name, offset=0, length=_MAX_PEEK)
            peek = b.readall().decode("utf-8", errors="replace")
        except HttpResponseError:
            pass
        title = title_from_first_bytes(peek) or blob.name.removesuffix(".md")
        mod = last or datetime.min.replace(tzinfo=timezone.utc)
        mtime = mod.isoformat().replace("+00:00", "Z")
        entries.append(
            (
                ArticleListItem(
                    filename=blob.name,
                    title=title,
                    url_path=f"/api/articles/{blob.name}",
                    size_bytes=blob.size,
                    modified_utc=mtime,
                ),
                mod,
            )
        )
    entries.sort(key=lambda x: x[1], reverse=True)
    return [e[0] for e in entries]


def ensure_unique_blob_name(
    settings: Settings, *, date: str, slug: str, initial: str
) -> str:
    container = _container(settings)
    if not _blob_exists(container, initial):
        return initial
    suffix = datetime.now(tz=timezone.utc).strftime("%H%M%S")
    return f"{date}-{slug}-{suffix}.md"


def save_final_article_azure(
    filename: str,
    markdown: str,
    settings: Settings,
) -> str:
    container = _container(settings)
    container.upload_blob(name=filename, data=markdown.encode("utf-8"), overwrite=True)
    return f"azure://{settings.azure_storage_container_name}/{filename}"


def read_article_azure(settings: Settings, filename: str) -> bytes:
    container = _container(settings)
    client = container.get_blob_client(filename)
    try:
        return client.download_blob().readall()
    except ResourceNotFoundError as e:
        raise FileNotFoundError(filename) from e
    except HttpResponseError as e:
        if e.status_code == 404:
            raise FileNotFoundError(filename) from e
        raise


from __future__ import annotations

from pydantic import BaseModel, Field


class SavedArticle(BaseModel):

    filename: str = Field(..., description="Basename of the saved Markdown file.")
    relative_path: str = Field(
        ...,
        description="Path relative to the backend package directory (for logs/debug).",
    )
    url_path: str = Field(
        ...,
        description="API path a client can GET to download the article Markdown.",
    )


class ArticleListItem(BaseModel):

    filename: str = Field(..., description="Basename of the Markdown file.")
    title: str = Field(
        ...,
        description="Title from file front matter, or a fallback from the filename.",
    )
    url_path: str = Field(
        ...,
        description="API path a client can GET to download the Markdown file.",
    )
    size_bytes: int = Field(..., ge=0, description="File size on disk.")
    modified_utc: str = Field(
        ...,
        description="File mtime in ISO-8601 UTC (best effort from filesystem).",
    )


__all__ = ["ArticleListItem", "SavedArticle"]


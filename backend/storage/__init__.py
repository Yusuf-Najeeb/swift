"""Storage utilities for Swift Writer (Step 9).

Local disk by default; set ``AZURE_STORAGE_CONNECTION_STRING`` to use
Azure Blob (same list/save/download API).
"""

from backend.storage.schemas import ArticleListItem, SavedArticle

__all__ = ["ArticleListItem", "SavedArticle"]


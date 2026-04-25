"""Storage utilities for Swift Writer (Step 9).

Currently this package provides a simple local-disk article store.
Future steps can add Azure Blob persistence behind the same interface.
"""

from backend.storage.schemas import ArticleListItem, SavedArticle

__all__ = ["ArticleListItem", "SavedArticle"]


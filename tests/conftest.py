import os
import sys
from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@pytest.fixture(autouse=True)
def _default_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", os.getenv("OPENROUTER_API_KEY", "test-key"))

    monkeypatch.delenv("SWIFT_API_BEARER_TOKEN", raising=False)

    monkeypatch.setenv("SWIFT_MCP_SERVER_ENABLED", "0")

    monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_CONTAINER_NAME", raising=False)

    from backend.config import get_settings

    get_settings.cache_clear()


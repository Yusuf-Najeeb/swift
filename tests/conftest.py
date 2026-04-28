import os
import sys
from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@pytest.fixture(autouse=True)
def _default_env(monkeypatch: pytest.MonkeyPatch):
    # Ensure Settings() can load in tests without real secrets.
    monkeypatch.setenv("OPENROUTER_API_KEY", os.getenv("OPENROUTER_API_KEY", "test-key"))

    # Disable API auth by default in tests unless a test sets it explicitly.
    monkeypatch.delenv("SWIFT_API_BEARER_TOKEN", raising=False)

    # Avoid spinning up the FastMCP HTTP mount in route tests.
    monkeypatch.setenv("SWIFT_MCP_SERVER_ENABLED", "0")

    # Force local storage unless a test opts into Azure.
    monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_CONTAINER_NAME", raising=False)

    from backend.config import get_settings

    get_settings.cache_clear()

